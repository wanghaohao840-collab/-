#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$EnvFile = 'deploy\.env',
    [string]$LegacyQdrantRoot = 'deploy-data\qdrant',
    [string]$StateRoot = $null,
    [string]$BackupRoot = $null,
    [ValidateRange(1, 600)][int]$HealthTimeoutSeconds = 180,
    [scriptblock]$ExternalInvoker,
    [scriptblock]$HealthProbe
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'Operations.Common.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'Backup.Common.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'QdrantVolume.Common.psm1') -Force

function Invoke-MigrationExternal {
    param([Parameter(Mandatory)][string]$FilePath, [string[]]$ArgumentList = @())
    if ($null -ne $ExternalInvoker) {
        return @(& $ExternalInvoker $FilePath $ArgumentList)
    }
    return @(Invoke-External -FilePath $FilePath -ArgumentList $ArgumentList)
}

function Get-MigrationComposeArguments {
    param([Parameter(Mandatory)]$Config, [Parameter(Mandatory)][string[]]$Command)
    return @(
        'compose', '--project-directory', $Config.RepositoryRoot,
        '--file', $Config.ComposeFile, '--env-file', $Config.EnvFile
    ) + $Command
}

function Get-MigrationRunningServices {
    param([Parameter(Mandatory)]$Config)
    $lines = Invoke-MigrationExternal -FilePath 'docker' -ArgumentList (
        Get-MigrationComposeArguments -Config $Config -Command @('ps', '--services', '--filter', 'status=running')
    )
    $services = @()
    foreach ($line in $lines) {
        $name = ([string]$line).Trim()
        if ([string]::IsNullOrWhiteSpace($name)) { continue }
        if ($name -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$') {
            throw 'Compose returned an unsafe service name'
        }
        if ($services -notcontains $name) { $services += $name }
    }
    return @($services)
}

function Invoke-MigrationCompose {
    param([Parameter(Mandatory)]$Config, [Parameter(Mandatory)][string[]]$Command)
    Invoke-MigrationExternal -FilePath 'docker' -ArgumentList (
        Get-MigrationComposeArguments -Config $Config -Command $Command
    ) | Out-Null
}

function Export-LegacyQdrantDirectory {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Archive
    )
    Invoke-MigrationExternal -FilePath 'tar.exe' -ArgumentList @('-C', $Source, '-czf', $Archive, '.') | Out-Null
    if (-not (Test-Path -LiteralPath $Archive -PathType Leaf)) {
        throw 'Legacy Qdrant backup archive was not created'
    }
    $hash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$Archive.sha256" -Encoding ASCII -Value "$hash  $([IO.Path]::GetFileName($Archive))"
    return $hash
}

function Get-ComposeQdrantInventory {
    param([Parameter(Mandatory)]$Config)
    $collectionJson = @(Invoke-MigrationExternal -FilePath 'docker' -ArgumentList (
        Get-MigrationComposeArguments -Config $Config -Command @('exec', '-T', 'qdrant', 'wget', '-q', '-O', '-', 'http://127.0.0.1:6333/collections')
    )) -join "`n"
    $collections = @((ConvertFrom-Json $collectionJson).result.collections)
    $inventory = @()
    foreach ($collection in @($collections | Sort-Object name)) {
        $name = [string]$collection.name
        if ($name -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$') {
            throw 'Qdrant returned an unsafe collection name'
        }
        $detailJson = @(Invoke-MigrationExternal -FilePath 'docker' -ArgumentList (
            Get-MigrationComposeArguments -Config $Config -Command @('exec', '-T', 'qdrant', 'wget', '-q', '-O', '-', "http://127.0.0.1:6333/collections/$name")
        )) -join "`n"
        $detail = ConvertFrom-Json $detailJson
        $inventory += [PSCustomObject]@{
            name = $name
            points_count = [long]$detail.result.points_count
            status = [string]$detail.result.status
        }
    }
    return @($inventory)
}

function Test-MigrationHealth {
    param([Parameter(Mandatory)]$Config)
    if ($null -ne $HealthProbe) {
        return [bool](& $HealthProbe $Config)
    }
    return Wait-Until -TimeoutSeconds $HealthTimeoutSeconds -IntervalSeconds 3 -Condition {
        (Test-ComposeHealth -Config $Config).Healthy
    }
}

$config = Get-OperationsConfig -RepositoryRoot $RepositoryRoot -EnvFile $EnvFile -StateRoot $StateRoot -BackupRoot $BackupRoot
$legacy = if ([IO.Path]::IsPathRooted($LegacyQdrantRoot)) {
    [IO.Path]::GetFullPath($LegacyQdrantRoot)
} else {
    [IO.Path]::GetFullPath((Join-Path $config.RepositoryRoot $LegacyQdrantRoot))
}
$dataParent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($config.DataRoot))
Assert-SafePath -Path $legacy -AllowedRoot $dataParent | Out-Null
if (-not (Test-Path -LiteralPath $legacy -PathType Container)) {
    throw "Legacy Qdrant directory was not found: $legacy"
}
Assert-BackupTreeSafe -Path $legacy -AllowedRoot $dataParent | Out-Null
$volumeName = Get-QdrantVolumeName -Config $config

if (-not $PSCmdlet.ShouldProcess($volumeName, "Migrate legacy Qdrant data from $legacy")) {
    [PSCustomObject]@{
        LegacyQdrantRoot = $legacy
        QdrantVolumeName = $volumeName
        Changed = $false
    }
    return
}

$operationLock = Enter-OperationsLock -StateRoot $config.StateRoot
$runningServices = @()
$servicesStopped = $false
$archive = $null
try {
    if (-not (Test-DockerReady)) {
        throw 'Docker Linux daemon is not ready'
    }
    Invoke-MigrationCompose -Config $config -Command @('config', '--quiet')
    $helperImage = Get-QdrantHelperImage -Config $config -ExternalInvoker $ExternalInvoker
    $runningServices = @(Get-MigrationRunningServices -Config $config)

    $migrationRoot = Join-Path $config.BackupRoot 'migrations'
    New-Item -ItemType Directory -Force -Path $migrationRoot | Out-Null
    Assert-SafePath -Path $migrationRoot -AllowedRoot $config.BackupRoot | Out-Null
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $archive = Join-Path $migrationRoot "qdrant-ntfs-before-volume-$stamp.tar.gz"
    foreach ($candidate in @($archive, "$archive.sha256", "$archive.meta")) {
        if (Test-Path -LiteralPath $candidate) {
            throw "Refusing to overwrite migration evidence: $candidate"
        }
    }

    if ($runningServices.Count -gt 0) {
        Invoke-MigrationCompose -Config $config -Command (@('stop') + $runningServices)
        $servicesStopped = $true
    }
    $hash = Export-LegacyQdrantDirectory -Source $legacy -Archive $archive
    $created = New-QdrantVolume -Name $volumeName -ExternalInvoker $ExternalInvoker
    if (-not $created -and -not (Test-QdrantVolumeEmpty -Name $volumeName -HelperImage $helperImage -ExternalInvoker $ExternalInvoker)) {
        throw "Destination Qdrant volume already contains data: $volumeName"
    }
    Import-QdrantDirectory -Name $volumeName -SourceDirectory $legacy -HelperImage $helperImage -RequireEmpty -ExternalInvoker $ExternalInvoker

    Invoke-MigrationCompose -Config $config -Command @('up', '-d', '--no-build', 'app', 'qdrant')
    $servicesStopped = $false
    if (-not (Test-MigrationHealth -Config $config)) {
        throw 'Migrated deployment did not become healthy'
    }
    $inventory = @(Get-ComposeQdrantInventory -Config $config)
    Invoke-MigrationExternal -FilePath $config.Python -ArgumentList @(
        (Join-Path $config.RepositoryRoot 'deploy\smoke_test.py'),
        '--env-file', $config.EnvFile
    ) | Out-Null
    $metadata = [ordered]@{
        format = 1
        created_at = (Get-Date).ToUniversalTime().ToString('o')
        legacy_qdrant_root = $legacy
        qdrant_volume_name = $volumeName
        archive = [IO.Path]::GetFileName($archive)
        sha256 = $hash
        git_revision = (@(Invoke-MigrationExternal -FilePath 'git' -ArgumentList @('-C', $config.RepositoryRoot, 'rev-parse', 'HEAD')) | Select-Object -First 1)
        target_inventory = $inventory
        legacy_retained = $true
    }
    [IO.File]::WriteAllText(
        "$archive.meta",
        ($metadata | ConvertTo-Json -Depth 8),
        (New-Object Text.UTF8Encoding($false))
    )
    Write-OperationsLog -StateRoot $config.StateRoot -Category 'qdrant-migration' -Message "Migrated Qdrant to volume $volumeName; legacy directory retained at $legacy"
    Write-OperationsStatus -StateRoot $config.StateRoot -Status ([ordered]@{
        category = 'qdrant-migration'
        healthy = $true
        volume = $volumeName
        legacy = $legacy
        backup = $archive
        inventory = $inventory
    })
    [PSCustomObject]@{
        LegacyQdrantRoot = $legacy
        QdrantVolumeName = $volumeName
        BackupArchive = $archive
        Inventory = $inventory
        Changed = $true
    }
} catch {
    $detail = Protect-LogText $_.Exception.Message
    if ($servicesStopped -and $runningServices.Count -gt 0) {
        try {
            Invoke-MigrationCompose -Config $config -Command (@('start') + $runningServices)
        } catch {
            $detail += "; prior service restart failed: $(Protect-LogText $_.Exception.Message)"
        }
    }
    Write-OperationsLog -StateRoot $config.StateRoot -Category 'qdrant-migration' -Message $detail -Level ERROR
    Write-OperationsStatus -StateRoot $config.StateRoot -Status ([ordered]@{
        category = 'qdrant-migration'
        healthy = $false
        volume = $volumeName
        legacy = $legacy
        backup = $archive
        error = $detail
        legacy_retained = $true
    })
    throw
} finally {
    Exit-OperationsLock -Lock $operationLock
}
