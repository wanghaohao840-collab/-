#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess, ConfirmImpact='High')]
param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$EnvFile = 'deploy\.env',
    [string]$StateRoot = $null,
    [string]$BackupRoot = $null,
    [Parameter(Mandatory)][string]$QualityReport,
    [string]$MigrationId = ('bge-m3-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')),
    [ValidateRange(1, 65536)][int]$SourceDimension = 384,
    [ValidateRange(30, 900)][int]$HealthTimeoutSeconds = 180
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Operations.Common.psm1') -Force

function Get-ComposeArguments {
    param([Parameter(Mandatory)]$Config, [Parameter(Mandatory)][string[]]$Command)
    return @(
        'compose', '--project-directory', $Config.RepositoryRoot,
        '--file', $Config.ComposeFile, '--env-file', $Config.EnvFile
    ) + $Command
}

function Set-EmbeddingProvider {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][ValidateSet('simple', 'siliconflow')][string]$Value,
        [Parameter(Mandatory)][string]$BackupPath
    )
    if (Test-Path -LiteralPath $BackupPath) {
        throw 'Embedding environment backup already exists; recovery is required'
    }
    $lines = [IO.File]::ReadAllLines($Path)
    $providerLineIndexes = @()
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match '^\s*RAG_EMBEDDING_PROVIDER\s*=') {
            $providerLineIndexes += $index
        }
    }
    if ($providerLineIndexes.Count -ne 1) {
        throw 'RAG_EMBEDDING_PROVIDER must occur exactly once'
    }
    $lines[$providerLineIndexes[0]] = "RAG_EMBEDDING_PROVIDER=$Value"
    $temporary = "$Path.embedding-$([guid]::NewGuid().ToString('N')).tmp"
    try {
        [IO.File]::WriteAllLines($temporary, $lines, (New-Object Text.UTF8Encoding($false)))
        $stream = [IO.File]::Open($temporary, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        try { $stream.Flush($true) } finally { $stream.Dispose() }
        [IO.File]::Replace($temporary, $Path, $BackupPath, $true)
    } finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function Restore-EmbeddingEnvironment {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$BackupPath)
    if (-not (Test-Path -LiteralPath $BackupPath -PathType Leaf)) {
        throw 'Embedding environment backup is missing'
    }
    [IO.File]::Replace($BackupPath, $Path, $null, $true)
}

if ($MigrationId -notmatch '^[A-Za-z0-9._-]{1,128}$') {
    throw 'MigrationId contains unsupported characters'
}
$config = Get-OperationsConfig -RepositoryRoot $RepositoryRoot -EnvFile $EnvFile `
    -StateRoot $StateRoot -BackupRoot $BackupRoot
$qualityPath = [IO.Path]::GetFullPath($QualityReport)
if (-not (Test-Path -LiteralPath $qualityPath -PathType Leaf)) {
    throw 'Quality report was not found'
}
$appDataRoot = Join-Path $config.DataRoot 'app'
$evidencePath = Join-Path $appDataRoot "vector_indexes\rag\migrations\$MigrationId\evidence.json"
$environmentBackup = "$($config.EnvFile).embedding-cutover-backup"

if (-not $PSCmdlet.ShouldProcess($config.RepositoryRoot, "Migrate RAG embedding using $MigrationId")) {
    [PSCustomObject]@{ Changed = $false; MigrationId = $MigrationId; Status = 'what-if' }
    return
}

$operationLock = Enter-OperationsLock -StateRoot $config.StateRoot
$journalBegan = $false
$journalComplete = $false
$environmentChanged = $false
$applicationStopped = $false
$repositoryLocationPushed = $false
try {
    if ([IO.Path]::GetFileName($config.ComposeFile) -eq 'compose.release.yaml') {
        throw 'Fixed-image deployments require a separately validated embedding migration release; refusing implicit build or activation'
    }
    if (Test-Path -LiteralPath $environmentBackup) {
        throw 'Embedding environment backup already exists; recovery is required'
    }
    Push-Location $config.RepositoryRoot
    $repositoryLocationPushed = $true
    Write-OperationsLog $config.StateRoot 'embedding-migration' "Starting $MigrationId"
    Invoke-External -FilePath 'docker.exe' -ArgumentList (
        Get-ComposeArguments $config @('build', 'app')
    ) | Out-Null

    & (Join-Path $PSScriptRoot 'Backup-Deployment.ps1') `
        -RepositoryRoot $config.RepositoryRoot -EnvFile $config.EnvFile `
        -StateRoot $config.StateRoot -BackupRoot $config.BackupRoot `
        -InheritedOperationLock $operationLock

    Invoke-External -FilePath 'docker.exe' -ArgumentList (
        Get-ComposeArguments $config @('stop', 'app')
    ) | Out-Null
    $applicationStopped = $true

    $qualityMount = "${qualityPath}:/tmp/bge-m3-quality.json:ro"
    Invoke-External -FilePath 'docker.exe' -ArgumentList (
        Get-ComposeArguments $config @(
            'run', '--rm', '--no-deps', '--entrypoint', 'python', '--volume', $qualityMount,
            'app', '-m', 'deploy.embedding_migrate',
            '--data-root', '/app/data', '--migration-id', $MigrationId,
            '--quality-report', '/tmp/bge-m3-quality.json',
            '--source-dimension', [string]$SourceDimension
        )
    ) | Out-Null

    Invoke-External -FilePath $config.Python -ArgumentList @(
        '-m', 'deploy.embedding_cutover', 'begin', '--data-root', $appDataRoot,
        '--migration-id', $MigrationId, '--evidence', $evidencePath
    ) | Out-Null
    $journalBegan = $true
    Invoke-External -FilePath $config.Python -ArgumentList @(
        '-m', 'deploy.embedding_cutover', 'apply', '--data-root', $appDataRoot,
        '--migration-id', $MigrationId
    ) | Out-Null

    Set-EmbeddingProvider -Path $config.EnvFile -Value siliconflow -BackupPath $environmentBackup
    $environmentChanged = $true
    Invoke-External -FilePath $config.Python -ArgumentList @(
        '-m', 'deploy.embedding_cutover', 'complete', '--data-root', $appDataRoot,
        '--migration-id', $MigrationId, '--env-file', $config.EnvFile
    ) | Out-Null
    $journalComplete = $true

    Invoke-External -FilePath 'docker.exe' -ArgumentList (
        Get-ComposeArguments $config @('up', '-d', '--no-deps', 'app')
    ) | Out-Null
    $healthy = Wait-Until -TimeoutSeconds $HealthTimeoutSeconds -IntervalSeconds 5 -Condition {
        (Test-ComposeHealth -Config $config).Healthy
    }
    if (-not $healthy) {
        Invoke-External -FilePath 'docker.exe' -ArgumentList (
            Get-ComposeArguments $config @('stop', 'app')
        ) | Out-Null
        throw 'Deployment failed to become healthy after embedding activation'
    }
    Invoke-External -FilePath $config.Python -ArgumentList @(
        (Join-Path $config.RepositoryRoot 'deploy\smoke_test.py'),
        '--env-file', $config.EnvFile, '--deep'
    ) | Out-Null
    Remove-Item -LiteralPath $environmentBackup -Force
    Write-OperationsLog $config.StateRoot 'embedding-migration' "Completed $MigrationId"
    Write-OperationsStatus $config.StateRoot @{
        status = 'healthy'; category = 'embedding-migration'; migration_id = $MigrationId
        checked_at = (Get-Date).ToUniversalTime().ToString('o')
    }
    [PSCustomObject]@{ Changed = $true; MigrationId = $MigrationId; Status = 'active' }
} catch {
    $failure = $_
    if (-not $journalComplete) {
        $recoverySucceeded = $false
        try {
            if ($environmentChanged -and (Test-Path -LiteralPath $environmentBackup)) {
                Restore-EmbeddingEnvironment -Path $config.EnvFile -BackupPath $environmentBackup
            }
            if ($journalBegan) {
                Invoke-External -FilePath $config.Python -ArgumentList @(
                    '-m', 'deploy.embedding_cutover', 'rollback', '--data-root', $appDataRoot,
                    '--migration-id', $MigrationId
                ) | Out-Null
            }
            $recoverySucceeded = $true
        } catch {
            Write-OperationsLog $config.StateRoot 'embedding-migration' 'Automatic pre-activation recovery failed' 'ERROR'
        }
        if ($applicationStopped) {
            if ($recoverySucceeded) {
                Invoke-External -FilePath 'docker.exe' -ArgumentList (
                    Get-ComposeArguments $config @('up', '-d', '--no-deps', 'app')
                ) | Out-Null
            } else {
                Invoke-External -FilePath 'docker.exe' -ArgumentList (
                    Get-ComposeArguments $config @('stop', 'app')
                ) | Out-Null
            }
        }
    } elseif ($applicationStopped) {
        Invoke-External -FilePath 'docker.exe' -ArgumentList (
            Get-ComposeArguments $config @('stop', 'app')
        ) | Out-Null
        Write-OperationsLog $config.StateRoot 'embedding-migration' `
            'Post-activation verification failed; application remains stopped for manual recovery' 'ERROR'
    }
    Write-OperationsLog $config.StateRoot 'embedding-migration' $failure.Exception.Message 'ERROR'
    Write-OperationsStatus $config.StateRoot @{
        status = 'failed'; category = 'embedding-migration'; migration_id = $MigrationId
        activation_complete = $journalComplete
        checked_at = (Get-Date).ToUniversalTime().ToString('o')
    }
    throw $failure
} finally {
    if ($repositoryLocationPushed) {
        Pop-Location
    }
    Exit-OperationsLock -Lock $operationLock
}
