#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$EnvFile = 'deploy\.env',
    [string]$StateRoot = $null,
    [bool]$AttemptRecovery = $true
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$trustedRepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$trustedFallbackStateRoot = Join-Path $trustedRepositoryRoot 'deploy-state'

function Write-TrustedFallbackTelemetry {
    param([Parameter(Mandatory)][string]$Category)

    $message = 'Deployment operation failed during initialization.'
    try {
        New-Item -ItemType Directory -Force -Path $trustedFallbackStateRoot | Out-Null
        $timestamp = (Get-Date).ToUniversalTime().ToString('o')
        Add-Content -LiteralPath (Join-Path $trustedFallbackStateRoot 'operations.log') -Value "$timestamp [ERROR] ($Category) $message" -Encoding UTF8
        $payload = [ordered]@{
            status = 'failed'
            category = $Category
            detail = $message
            checked_at = $timestamp
        } | ConvertTo-Json
        [IO.File]::WriteAllText(
            (Join-Path $trustedFallbackStateRoot 'status.json'),
            $payload,
            (New-Object System.Text.UTF8Encoding($false))
        )
    } catch {}
    try {
        Write-EventLog -LogName Application -Source 'Python Self Agent' -EntryType Error -EventId 1001 -Message $message
    } catch {}
}

function Get-PreviousHealthStatus {
    param([Parameter(Mandatory)][string]$OperationsStateRoot)

    $statusFile = Join-Path $OperationsStateRoot 'status.json'
    if (-not (Test-Path -LiteralPath $statusFile -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $statusFile -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Test-DeploymentHttp {
    param([Parameter(Mandatory)]$Config)

    $port = Read-DeployEnvValue -EnvFile $Config.EnvFile -Name 'APP_PORT'
    if ([string]::IsNullOrWhiteSpace($port)) {
        $port = '7860'
    }

    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$port/" -UseBasicParsing -TimeoutSec 10
        if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 400) {
            throw "Application returned HTTP $($response.StatusCode)"
        }
        return [PSCustomObject]@{ Healthy = $true; Reason = 'Application HTTP endpoint is healthy' }
    } catch {
        return [PSCustomObject]@{ Healthy = $false; Reason = $_.Exception.Message }
    }
}

function Invoke-DeploymentHealthChecks {
    param([Parameter(Mandatory)]$Config)

    if (-not (Test-DockerReady)) {
        return [PSCustomObject]@{ Healthy = $false; Reason = 'Docker Engine is not ready' }
    }

    $composeHealth = Test-ComposeHealth -Config $Config
    if (-not $composeHealth.Healthy) {
        return [PSCustomObject]@{ Healthy = $false; Reason = $composeHealth.Reason }
    }

    return Test-DeploymentHttp -Config $Config
}

$modulePath = Join-Path $PSScriptRoot 'Operations.Common.psm1'
$moduleAvailable = $false
$operationsStateRoot = $trustedFallbackStateRoot
$config = $null
$previousStatus = $null
$recoveryAttempted = $false

try {
    Import-Module $modulePath -Force
    $moduleAvailable = $true
    $config = Get-OperationsConfig -RepositoryRoot $RepositoryRoot -EnvFile $EnvFile -StateRoot $StateRoot
    $operationsStateRoot = $config.StateRoot
    $previousStatus = Get-PreviousHealthStatus -OperationsStateRoot $operationsStateRoot
    $health = Invoke-DeploymentHealthChecks -Config $config
    if (-not $health.Healthy -and $AttemptRecovery) {
        $recoveryAttempted = $true
        Write-OperationsLog $operationsStateRoot 'health' "Health check failed; attempting Compose recovery: $($health.Reason)" 'WARN'

        try {
            Push-Location $config.RepositoryRoot
            try {
                Invoke-External docker @('compose', '--env-file', $config.EnvFile, 'up', '-d') | Out-Null
            } finally {
                Pop-Location
            }
        } catch {
            Write-OperationsLog $operationsStateRoot 'health' "Compose recovery command failed: $($_.Exception.Message)" 'WARN'
        }

        Wait-Until -TimeoutSeconds 90 -IntervalSeconds 5 -Condition {
            (Test-DockerReady) -and (Test-ComposeHealth -Config $config).Healthy
        } | Out-Null
        $health = Invoke-DeploymentHealthChecks -Config $config
    }

    if (-not $health.Healthy) {
        throw $health.Reason
    }

    Write-OperationsStatus $operationsStateRoot @{
        status = 'healthy'
        category = 'health'
        checked_at = (Get-Date).ToUniversalTime().ToString('o')
        recovery_attempted = $recoveryAttempted
    }
    $previousHealthFailed = $null -ne $previousStatus -and $previousStatus.status -eq 'failed' -and $previousStatus.category -eq 'health'
    if ($recoveryAttempted -or $previousHealthFailed) {
        Send-OperationsNotification $operationsStateRoot 'health-recovered' 'Deployment health recovered' 'Docker, Compose, and HTTP health checks are passing.' | Out-Null
    }
} catch {
    if ($moduleAvailable -and $null -ne $config) {
        Write-OperationsLog $operationsStateRoot 'health' $_.Exception.Message 'ERROR'
        Write-OperationsStatus $operationsStateRoot @{
            status = 'failed'
            category = 'health'
            detail = $_.Exception.Message
            checked_at = (Get-Date).ToUniversalTime().ToString('o')
            recovery_attempted = $recoveryAttempted
        }
        Send-OperationsNotification $operationsStateRoot 'health' 'Deployment health check failed' $_.Exception.Message | Out-Null
    } else {
        Write-TrustedFallbackTelemetry -Category 'health'
    }
    throw
}
