#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$EnvFile = 'deploy\.env',
    [string]$StateRoot = 'deploy-state',
    [string]$BackupRoot = 'D:\python_self_agent_backups',
    [ValidateRange(1, 600)][int]$HealthTimeoutSeconds = 180,
    [scriptblock]$ExternalInvoker,
    [scriptblock]$HealthProbe
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'Operations.Common.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'Backup.Common.psm1') -Force

function Test-PathOverlap {
    param([Parameter(Mandatory)][string]$First, [Parameter(Mandatory)][string]$Second)
    $firstPath = [IO.Path]::GetFullPath($First).TrimEnd('\', '/')
    $secondPath = [IO.Path]::GetFullPath($Second).TrimEnd('\', '/')
    if ($firstPath.Equals($secondPath, [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    return $firstPath.StartsWith($secondPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        $secondPath.StartsWith($firstPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}

function Invoke-BackupExternal {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @()
    )
    if ($null -ne $ExternalInvoker) {
        return @(& $ExternalInvoker $FilePath $ArgumentList)
    }
    return @(Invoke-External -FilePath $FilePath -ArgumentList $ArgumentList)
}

function Get-ComposeArguments {
    param([Parameter(Mandatory)]$Config, [Parameter(Mandatory)][string[]]$Command)
    return @(
        'compose',
        '--project-directory', $Config.RepositoryRoot,
        '--file', $Config.ComposeFile,
        '--env-file', $Config.EnvFile
    ) + $Command
}

function Get-RunningDeploymentServices {
    param([Parameter(Mandatory)]$Config)

    $output = Invoke-BackupExternal -FilePath 'docker' -ArgumentList (
        Get-ComposeArguments -Config $Config -Command @('ps', '--services', '--filter', 'status=running')
    )
    $services = @()
    foreach ($line in $output) {
        $name = ([string]$line).Trim()
        if ([string]::IsNullOrWhiteSpace($name)) {
            continue
        }
        if ($name -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]*$') {
            throw "Compose returned an unsafe service name"
        }
        if ($services -notcontains $name) {
            $services += $name
        }
    }
    return @($services)
}

function Invoke-ComposeForServices {
    param(
        [Parameter(Mandatory)]$Config,
        [Parameter(Mandatory)][ValidateSet('stop', 'start')][string]$Action,
        [Parameter(Mandatory)][string[]]$Services
    )
    if ($Services.Count -eq 0) {
        return
    }
    Invoke-BackupExternal -FilePath 'docker' -ArgumentList (
        Get-ComposeArguments -Config $Config -Command (@($Action) + $Services)
    ) | Out-Null
}

function Test-BackupHealth {
    param([Parameter(Mandatory)]$Config)

    if ($null -ne $HealthProbe) {
        $result = & $HealthProbe $Config
        if ($result -is [bool]) {
            return $result
        }
        return $null -ne $result -and [bool]$result.Healthy
    }
    return Wait-Until -TimeoutSeconds $HealthTimeoutSeconds -IntervalSeconds 5 -Condition {
        (Test-ComposeHealth -Config $Config).Healthy
    }
}

function Write-BackupMetadata {
    param(
        [Parameter(Mandatory)][string]$Archive,
        [Parameter(Mandatory)][string]$Hash,
        [Parameter(Mandatory)][DateTime]$CreatedUtc,
        [Parameter(Mandatory)][ValidateSet('daily', 'weekly')][string]$Kind
    )
    $payload = [ordered]@{
        archive = [IO.Path]::GetFileName($Archive)
        sha256 = $Hash
        created_at = $CreatedUtc.ToString('o')
        kind = $Kind
        format = 1
    } | ConvertTo-Json
    [IO.File]::WriteAllText(
        "$Archive.meta",
        $payload,
        (New-Object System.Text.UTF8Encoding($false))
    )
}

function Write-BackupChecksum {
    param([Parameter(Mandatory)][string]$archive)

    $hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$archive.sha256" -Encoding ASCII -Value "$hash  $([IO.Path]::GetFileName($archive))"
    return $hash
}

$config = Get-OperationsConfig -RepositoryRoot $RepositoryRoot -EnvFile $EnvFile -StateRoot $StateRoot -BackupRoot $BackupRoot
$dataRoot = $config.DataRoot
$backupPath = $config.BackupRoot
if ((Test-PathOverlap $dataRoot $backupPath) -or (Test-PathOverlap $dataRoot $config.StateRoot)) {
    throw 'Deployment data must not overlap backup or operations state roots'
}

$dataParent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($dataRoot))
Assert-BackupTreeSafe -Path $dataRoot -AllowedRoot $dataParent | Out-Null
$backupParent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($backupPath))
Assert-BackupPathAncestorsSafe -Path $backupPath | Out-Null
New-Item -ItemType Directory -Force -Path $backupPath | Out-Null
Assert-BackupTreeSafe -Path $backupPath -AllowedRoot $backupParent | Out-Null

$dailyDirectory = Join-Path $backupPath 'daily'
$weeklyDirectory = Join-Path $backupPath 'weekly'
New-Item -ItemType Directory -Force -Path $dailyDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $weeklyDirectory | Out-Null
Assert-BackupTreeSafe -Path $dailyDirectory -AllowedRoot $backupPath | Out-Null
Assert-BackupTreeSafe -Path $weeklyDirectory -AllowedRoot $backupPath | Out-Null

$now = (Get-Date).ToUniversalTime()
$stamp = $now.ToString('yyyyMMddTHHmmssZ')
$archive = Join-Path $dailyDirectory "assistant-$stamp.tar.gz"
foreach ($candidate in @($archive, "$archive.sha256", "$archive.meta")) {
    if (Test-Path -LiteralPath $candidate) {
        throw "Refusing to overwrite an existing backup file: $candidate"
    }
}

$runningServices = @(Get-RunningDeploymentServices -Config $config)
$operationError = $null
$restartError = $null
try {
    Invoke-ComposeForServices -Config $config -Action stop -Services $runningServices
    Invoke-BackupExternal -FilePath 'tar.exe' -ArgumentList @('-C', $dataRoot, '-czf', $archive, '.') | Out-Null
    $hash = Write-BackupChecksum -Archive $archive
    Write-BackupMetadata -Archive $archive -Hash $hash -CreatedUtc $now -Kind daily

    $createdSet = @(Get-CompleteBackupSets -Directory $dailyDirectory -Prefix 'assistant-' | Where-Object {
        $_.Archive.Equals($archive, [StringComparison]::OrdinalIgnoreCase)
    })
    if ($createdSet.Count -ne 1) {
        throw 'Daily backup archive or one of its sidecars is incomplete'
    }
} catch {
    $operationError = $_
} finally {
    try {
        Invoke-ComposeForServices -Config $config -Action start -Services $runningServices
    } catch {
        $restartError = $_
    }
}

if ($null -ne $operationError) {
    throw $operationError
}
if ($null -ne $restartError) {
    throw $restartError
}
if (-not (Test-BackupHealth -Config $config)) {
    throw 'Deployment did not become healthy after the cold backup'
}

$iso = Get-IsoWeekInfo -UtcDate $now
$weeklyName = 'assistant-week-{0:D4}-W{1:D2}.tar.gz' -f $iso.Year, $iso.Week
$weeklyArchive = Join-Path $weeklyDirectory $weeklyName
$weeklySet = @(Get-CompleteBackupSets -Directory $weeklyDirectory -Prefix 'assistant-week-' | Where-Object {
    $_.Archive.Equals($weeklyArchive, [StringComparison]::OrdinalIgnoreCase)
})
if ($weeklySet.Count -eq 0) {
    foreach ($candidate in @($weeklyArchive, "$weeklyArchive.sha256", "$weeklyArchive.meta")) {
        if (Test-Path -LiteralPath $candidate) {
            throw "Incomplete weekly set already exists; refusing to overwrite it: $candidate"
        }
    }
    Copy-Item -LiteralPath $archive -Destination $weeklyArchive
    $weeklyHash = Write-BackupChecksum -Archive $weeklyArchive
    Write-BackupMetadata -Archive $weeklyArchive -Hash $weeklyHash -CreatedUtc $now -Kind weekly
    $weeklySet = @(Get-CompleteBackupSets -Directory $weeklyDirectory -Prefix 'assistant-week-' | Where-Object {
        $_.Archive.Equals($weeklyArchive, [StringComparison]::OrdinalIgnoreCase)
    })
    if ($weeklySet.Count -ne 1) {
        throw 'Weekly backup archive or one of its sidecars is incomplete'
    }
}

$dailyPlan = Get-RetentionPlan -Directory $dailyDirectory -Prefix 'assistant-' -Keep 7
$weeklyPlan = Get-RetentionPlan -Directory $weeklyDirectory -Prefix 'assistant-week-' -Keep 4
foreach ($set in @($dailyPlan.Remove)) {
    Remove-BackupSet -BackupSet $set -AllowedRoot $dailyDirectory
}
foreach ($set in @($weeklyPlan.Remove)) {
    Remove-BackupSet -BackupSet $set -AllowedRoot $weeklyDirectory
}

[PSCustomObject]@{
    Archive = $archive
    Checksum = "$archive.sha256"
    Metadata = "$archive.meta"
    WeeklyArchive = $weeklyArchive
}
