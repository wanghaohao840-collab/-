#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$EnvFile = 'deploy\.env',
    [string]$StateRoot = $null,
    [string]$BackupRoot = $null,
    [ValidateRange(1, 600)][int]$HealthTimeoutSeconds = 180,
    [scriptblock]$CommandRunner,
    [switch]$SkipNotification
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'Operations.Common.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'Backup.Common.psm1') -Force

$stableImages = [ordered]@{
    app = 'python_self_agent-app:local'
    qdrant = 'python_self_agent-qdrant:local'
}

function Invoke-UpdateCommand {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @()
    )
    if ($null -ne $CommandRunner) {
        return @(& $CommandRunner $FilePath $ArgumentList)
    }
    return @(Invoke-External -FilePath $FilePath -ArgumentList $ArgumentList)
}

function Invoke-UpdateScript {
    param(
        [Parameter(Mandatory)][string]$ScriptPath,
        [Parameter(Mandatory)][System.Collections.IDictionary]$Parameters
    )
    if ($null -eq $CommandRunner) {
        return @(& $ScriptPath @Parameters)
    }
    $arguments = @('-NoProfile', '-NonInteractive', '-File', $ScriptPath)
    foreach ($name in $Parameters.Keys) {
        $arguments += "-$name"
        $arguments += [string]$Parameters[$name]
    }
    return @(Invoke-UpdateCommand -FilePath 'powershell.exe' -ArgumentList $arguments)
}

function Get-ComposeArguments {
    param(
        [Parameter(Mandatory)]$Config,
        [Parameter(Mandatory)][string[]]$Command
    )
    return @(
        'compose',
        '--project-directory', $Config.RepositoryRoot,
        '--file', $Config.ComposeFile,
        '--env-file', $Config.EnvFile
    ) + $Command
}

function Test-UpdateHealth {
    param([Parameter(Mandatory)]$Config)
    $output = @(Invoke-UpdateCommand -FilePath 'docker' -ArgumentList (
        Get-ComposeArguments -Config $Config -Command @(
            'ps', '--format', '{{.Service}}|{{.State}}|{{.Health}}', 'app', 'qdrant'
        )
    ))
    $healthy = @{}
    foreach ($line in $output) {
        $parts = ([string]$line) -split '\|', 3
        if ($parts.Count -eq 3 -and @('app', 'qdrant') -contains $parts[0]) {
            $healthy[$parts[0]] = ($parts[1] -eq 'running' -and $parts[2] -eq 'healthy')
        }
    }
    return $healthy.Count -eq 2 -and $healthy['app'] -and $healthy['qdrant']
}

function Get-RunningUpdateServices {
    param([Parameter(Mandatory)]$Config)
    $output = @(Invoke-UpdateCommand -FilePath 'docker' -ArgumentList (
        Get-ComposeArguments -Config $Config -Command @(
            'ps', '--format', '{{.Service}}|{{.State}}|{{.Health}}', 'app', 'qdrant'
        )
    ))
    $services = @()
    foreach ($line in $output) {
        $parts = ([string]$line) -split '\|', 3
        if ($parts.Count -eq 3 -and
            @('app', 'qdrant') -contains $parts[0] -and
            $parts[1] -eq 'running' -and
            $services -notcontains $parts[0]) {
            $services += $parts[0]
        }
    }
    return @($services)
}

function Wait-ForUpdateHealth {
    param([Parameter(Mandatory)]$Config)
    return Wait-Until -TimeoutSeconds $HealthTimeoutSeconds -IntervalSeconds 1 -Condition {
        Test-UpdateHealth -Config $Config
    }
}

function Assert-BackupDriveCapacity {
    param([Parameter(Mandatory)][string]$Path)
    $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path))
    if ([string]::IsNullOrWhiteSpace($root)) {
        throw 'The backup root does not resolve to a filesystem drive'
    }
    $available = ([IO.DriveInfo]::new($root)).AvailableFreeSpace
    if ($available -lt 10GB) {
        throw 'The backup drive must have at least 10 GiB free before an upgrade'
    }
}

function Get-CurrentImageId {
    param([Parameter(Mandatory)][string]$Image)
    $output = @(Invoke-UpdateCommand -FilePath 'docker' -ArgumentList @(
        'image', 'inspect', '--format', '{{.Id}}', $Image
    ))
    $id = @($output | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ }) | Select-Object -Last 1
    if ($null -eq $id -or $id -notmatch '^sha256:[0-9a-fA-F]{64}$') {
        throw "Could not resolve a valid current image ID for $Image"
    }
    return [string]$id
}

function Get-VerifiedBackupArchive {
    param(
        [Parameter(Mandatory)]$BackupOutput,
        [Parameter(Mandatory)]$Config
    )
    $record = @($BackupOutput | Where-Object {
        $null -ne $_ -and $null -ne $_.PSObject.Properties['Archive']
    }) | Select-Object -Last 1
    if ($null -eq $record) {
        throw 'The cold backup did not return an archive path'
    }
    $archive = Assert-SafePath -Path ([string]$record.Archive) -AllowedRoot $Config.BackupRoot
    foreach ($required in @($archive, "$archive.sha256", "$archive.meta")) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "The cold backup is incomplete: $required"
        }
    }
    $line = (Get-Content -LiteralPath "$archive.sha256" -Raw).Trim()
    $match = [regex]::Match($line, '^(?<hash>[0-9a-fA-F]{64})  (?<name>[^\\/]+)$')
    if (-not $match.Success -or
        -not $match.Groups['name'].Value.Equals([IO.Path]::GetFileName($archive), [StringComparison]::Ordinal)) {
        throw 'The cold backup checksum sidecar is invalid'
    }
    $actual = Get-BackupSha256 -LiteralPath $archive
    if (-not $actual.Equals($match.Groups['hash'].Value, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The cold backup checksum verification failed'
    }
    return $archive
}

function Protect-UpdateValues {
    param([object[]]$Values)
    return @($Values | ForEach-Object { Protect-LogText ([string]$_) })
}

function Write-UpdateReport {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][System.Collections.IDictionary]$Payload
    )
    $json = $Payload | ConvertTo-Json -Depth 12
    [IO.File]::WriteAllText($Path, $json, (New-Object System.Text.UTF8Encoding($false)))
}

function Invoke-DefaultSmoke {
    param([Parameter(Mandatory)]$Config)
    Invoke-UpdateCommand -FilePath $Config.Python -ArgumentList @(
        (Join-Path $Config.RepositoryRoot 'deploy\smoke_test.py'),
        '--env-file', $Config.EnvFile
    ) | Out-Null
}

$config = $null
$stage = 'initialization'
$startedAt = (Get-Date).ToUniversalTime()
$stamp = $startedAt.ToString('yyyyMMddTHHmmssZ')
$gitStatus = @()
$backupArchive = $null
$oldImageIds = [ordered]@{}
$rollbackImages = [ordered]@{
    app = "python_self_agent-app:rollback-$stamp"
    qdrant = "python_self_agent-qdrant:rollback-$stamp"
}
$rollbackTagsReady = $false
$candidateRollbackRequired = $false
$rollbackSucceeded = $null
$reportPath = $null

try {
    $config = Get-OperationsConfig -RepositoryRoot $RepositoryRoot -EnvFile $EnvFile -StateRoot $StateRoot -BackupRoot $BackupRoot
    Assert-BackupDriveCapacity -Path $config.BackupRoot
    if (-not (Test-UpdateHealth -Config $config)) {
        throw 'The current app and qdrant services must be healthy before an upgrade'
    }

    $reportRoot = Join-Path $config.StateRoot 'reports'
    New-Item -ItemType Directory -Force -Path $reportRoot | Out-Null
    $reportPath = Join-Path $reportRoot "update-$stamp.json"

    $stage = 'git-status'
    $gitStatus = Protect-UpdateValues (Invoke-UpdateCommand -FilePath 'git' -ArgumentList @(
        '-C', $config.RepositoryRoot, 'status', '--short'
    ))

    $stage = 'deployment-tests'
    $testTemp = Join-Path $config.StateRoot "pytest-update-$stamp"
    Invoke-UpdateCommand -FilePath $config.Python -ArgumentList @(
        '-m', 'pytest', (Join-Path $config.RepositoryRoot 'tests\deploy'), '-q', "--basetemp=$testTemp"
    ) | Out-Null

    $stage = 'compose-config'
    Invoke-UpdateCommand -FilePath 'docker' -ArgumentList (
        Get-ComposeArguments -Config $config -Command @('config', '--quiet')
    ) | Out-Null

    $stage = 'cold-backup'
    $backupOutput = Invoke-UpdateScript -ScriptPath (Join-Path $PSScriptRoot 'Backup-Deployment.ps1') -Parameters ([ordered]@{
        RepositoryRoot = $config.RepositoryRoot
        EnvFile = $config.EnvFile
        StateRoot = $config.StateRoot
        BackupRoot = $config.BackupRoot
        HealthTimeoutSeconds = $HealthTimeoutSeconds
    })
    $backupArchive = Get-VerifiedBackupArchive -BackupOutput $backupOutput -Config $config

    $stage = 'resolve-images'
    $oldImageIds['app'] = Get-CurrentImageId -Image $stableImages['app']
    $oldImageIds['qdrant'] = Get-CurrentImageId -Image $stableImages['qdrant']

    $stage = 'rollback-tag-app'
    Invoke-UpdateCommand -FilePath 'docker' -ArgumentList @(
        'image', 'tag', $oldImageIds['app'], $rollbackImages['app']
    ) | Out-Null
    $stage = 'rollback-tag-qdrant'
    Invoke-UpdateCommand -FilePath 'docker' -ArgumentList @(
        'image', 'tag', $oldImageIds['qdrant'], $rollbackImages['qdrant']
    ) | Out-Null
    $rollbackTagsReady = $true

    $stage = 'build'
    Invoke-UpdateCommand -FilePath 'docker' -ArgumentList (
        Get-ComposeArguments -Config $config -Command @('build', 'app', 'qdrant')
    ) | Out-Null

    $stage = 'scan-app'
    Invoke-UpdateCommand -FilePath 'docker' -ArgumentList @(
        'scout', 'cves', '--only-severity', 'critical', '--exit-code', '--only-fixed', $stableImages['app']
    ) | Out-Null
    $stage = 'scan-qdrant'
    Invoke-UpdateCommand -FilePath 'docker' -ArgumentList @(
        'scout', 'cves', '--only-severity', 'critical', '--exit-code', '--only-fixed', $stableImages['qdrant']
    ) | Out-Null

    $stage = 'candidate-up'
    $candidateRollbackRequired = $true
    Invoke-UpdateCommand -FilePath 'docker' -ArgumentList (
        Get-ComposeArguments -Config $config -Command @('up', '-d', '--no-build', 'app', 'qdrant')
    ) | Out-Null

    $stage = 'candidate-health'
    if (-not (Wait-ForUpdateHealth -Config $config)) {
        throw 'The candidate deployment did not become healthy'
    }

    $stage = 'default-smoke'
    Invoke-DefaultSmoke -Config $config
    $stage = 'deep-smoke'
    Invoke-UpdateCommand -FilePath $config.Python -ArgumentList @(
        (Join-Path $config.RepositoryRoot 'deploy\smoke_test.py'),
        '--env-file', $config.EnvFile,
        '--deep'
    ) | Out-Null

    $stage = 'complete'
    Write-UpdateReport -Path $reportPath -Payload ([ordered]@{
        status = 'succeeded'
        started_at = $startedAt.ToString('o')
        completed_at = (Get-Date).ToUniversalTime().ToString('o')
        final_stage = $stage
        git_status = @($gitStatus)
        backup_archive = $backupArchive
        stable_images = $stableImages
        previous_image_ids = $oldImageIds
        rollback_images = $rollbackImages
        rollback_succeeded = $null
    })
    Write-OperationsLog -StateRoot $config.StateRoot -Category 'update' -Message "Upgrade succeeded; report: $reportPath"
    if (-not $SkipNotification) {
        Send-OperationsNotification -StateRoot $config.StateRoot -Category 'update' -Title 'Deployment upgrade succeeded' -Message 'Tests, scans, and smoke checks passed.' | Out-Null
    }
} catch {
    $failureStage = $stage
    $failureDetail = Protect-LogText $_.Exception.Message
    $compensationErrors = @()
    $rollbackFailurePriority = $null
    $imagesRestored = $false
    $runningServicesReady = $false
    $dataRestored = $false

    if ($rollbackTagsReady) {
        try {
            foreach ($service in @('app', 'qdrant')) {
                $stage = "rollback-image-$service"
                Invoke-UpdateCommand -FilePath 'docker' -ArgumentList @(
                    'image', 'tag', $oldImageIds[$service], $stableImages[$service]
                ) | Out-Null
            }
            $imagesRestored = $true
        } catch {
            $rollbackFailurePriority = 'high'
            $compensationErrors += Protect-LogText "restore stable image tags: $($_.Exception.Message)"
        }
    }

    if ($candidateRollbackRequired -and $imagesRestored) {
        $stage = 'rollback-running-services'
        try {
            $runningServices = @(Get-RunningUpdateServices -Config $config)
            if ($runningServices -notcontains 'app' -or $runningServices -notcontains 'qdrant') {
                $stage = 'rollback-prepare-services'
                Invoke-UpdateCommand -FilePath 'docker' -ArgumentList (
                    Get-ComposeArguments -Config $config -Command @(
                        'up', '-d', '--no-build', '--no-recreate', 'app', 'qdrant'
                    )
                ) | Out-Null
                $runningServices = @(Get-RunningUpdateServices -Config $config)
            }
            if ($runningServices -notcontains 'app' -or $runningServices -notcontains 'qdrant') {
                throw 'Both app and qdrant must be running before data restore'
            }
            $runningServicesReady = $true
        } catch {
            $rollbackFailurePriority = 'high'
            $compensationErrors += Protect-LogText "establish running services: $($_.Exception.Message)"
        }
    }

    if ($candidateRollbackRequired -and $imagesRestored -and $runningServicesReady -and $null -ne $backupArchive) {
        $stage = 'rollback-data'
        try {
            Invoke-UpdateScript -ScriptPath (Join-Path $PSScriptRoot 'Restore-Deployment.ps1') -Parameters ([ordered]@{
                Archive = $backupArchive
                RepositoryRoot = $config.RepositoryRoot
                EnvFile = $config.EnvFile
                StateRoot = $config.StateRoot
                BackupRoot = $config.BackupRoot
                HealthTimeoutSeconds = $HealthTimeoutSeconds
            }) | Out-Null
            $dataRestored = $true
        } catch {
            $rollbackFailurePriority = 'high'
            $compensationErrors += Protect-LogText "restore backup: $($_.Exception.Message)"
        }

    }

    if ($candidateRollbackRequired -and $imagesRestored -and $runningServicesReady -and $dataRestored) {
        $stage = 'rollback-recreate'
        try {
            Invoke-UpdateCommand -FilePath 'docker' -ArgumentList (
                Get-ComposeArguments -Config $config -Command @(
                    'up', '-d', '--no-build', '--force-recreate', 'app', 'qdrant'
                )
            ) | Out-Null
            if (-not (Wait-ForUpdateHealth -Config $config)) {
                throw 'The restored deployment did not become healthy'
            }
            $stage = 'rollback-smoke'
            Invoke-DefaultSmoke -Config $config
        } catch {
            $rollbackFailurePriority = 'high'
            $compensationErrors += Protect-LogText "verify restored deployment: $($_.Exception.Message)"
        }
        $rollbackSucceeded = ($compensationErrors.Count -eq 0)
    } elseif (-not $candidateRollbackRequired -and $rollbackTagsReady) {
        $rollbackSucceeded = ($compensationErrors.Count -eq 0)
    } elseif ($candidateRollbackRequired) {
        $rollbackSucceeded = $false
    }

    if ($null -ne $reportPath) {
        try {
            Write-UpdateReport -Path $reportPath -Payload ([ordered]@{
                status = 'failed'
                started_at = $startedAt.ToString('o')
                completed_at = (Get-Date).ToUniversalTime().ToString('o')
                failure_stage = $failureStage
                failure_detail = $failureDetail
                git_status = @($gitStatus)
                backup_archive = $backupArchive
                stable_images = $stableImages
                previous_image_ids = $oldImageIds
                rollback_images = $rollbackImages
                rollback_succeeded = $rollbackSucceeded
                rollback_failure_priority = $rollbackFailurePriority
                compensation_errors = @($compensationErrors)
            })
        } catch {}
        Write-OperationsLog -StateRoot $config.StateRoot -Category 'update' -Message "Upgrade failed at $failureStage`: $failureDetail" -Level ERROR
        if (-not $SkipNotification) {
            Send-OperationsNotification -StateRoot $config.StateRoot -Category 'update' -Title 'Deployment upgrade failed' -Message "Stage: $failureStage. $failureDetail" | Out-Null
        }
    }
    if ($compensationErrors.Count -gt 0) {
        throw "Upgrade failed at $failureStage`: $failureDetail. HIGH PRIORITY rollback failure: $($compensationErrors -join '; ')"
    }
    throw "Upgrade failed at $failureStage`: $failureDetail"
}
