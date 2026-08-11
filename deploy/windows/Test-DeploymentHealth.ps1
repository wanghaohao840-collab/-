#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$EnvFile = 'deploy\.env',
    [string]$StateRoot = 'deploy-state',
    [bool]$AttemptRecovery = $true
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'Operations.Common.psm1') -Force

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

    $bindAddress = Read-DeployEnvValue -EnvFile $Config.EnvFile -Name 'APP_BIND_ADDRESS'
    $port = Read-DeployEnvValue -EnvFile $Config.EnvFile -Name 'APP_PORT'
    if ([string]::IsNullOrWhiteSpace($bindAddress) -or $bindAddress -eq '0.0.0.0') {
        $bindAddress = '127.0.0.1'
    } elseif ($bindAddress -eq '::' -or $bindAddress -eq '[::]') {
        $bindAddress = '[::1]'
    }
    if ([string]::IsNullOrWhiteSpace($port)) {
        $port = '7860'
    }

    try {
        $response = Invoke-WebRequest -Uri "http://${bindAddress}:$port/" -UseBasicParsing -TimeoutSec 10
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

$config = Get-OperationsConfig -RepositoryRoot $RepositoryRoot -EnvFile $EnvFile -StateRoot $StateRoot
$previousStatus = Get-PreviousHealthStatus -OperationsStateRoot $config.StateRoot
$recoveryAttempted = $false

try {
    $health = Invoke-DeploymentHealthChecks -Config $config
    if (-not $health.Healthy -and $AttemptRecovery) {
        $recoveryAttempted = $true
        Write-OperationsLog $config.StateRoot 'health' "Health check failed; attempting Compose recovery: $($health.Reason)" 'WARN'

        Push-Location $config.RepositoryRoot
        try {
            Invoke-External docker @('compose', '--env-file', $config.EnvFile, 'up', '-d') | Out-Null
        } finally {
            Pop-Location
        }

        Wait-Until -TimeoutSeconds 90 -IntervalSeconds 5 -Condition {
            (Test-DockerReady) -and (Test-ComposeHealth -Config $config).Healthy
        } | Out-Null
        $health = Invoke-DeploymentHealthChecks -Config $config
    }

    if (-not $health.Healthy) {
        throw $health.Reason
    }

    Write-OperationsStatus $config.StateRoot @{
        status = 'healthy'
        category = 'health'
        checked_at = (Get-Date).ToUniversalTime().ToString('o')
        recovery_attempted = $recoveryAttempted
    }
    if ($recoveryAttempted -or ($previousStatus.status -eq 'failed' -and $previousStatus.category -eq 'health')) {
        Send-OperationsNotification $config.StateRoot 'health-recovered' 'Deployment health recovered' 'Docker, Compose, and HTTP health checks are passing.'
    }
} catch {
    Write-OperationsLog $config.StateRoot 'health' $_.Exception.Message 'ERROR'
    Write-OperationsStatus $config.StateRoot @{
        status = 'failed'
        category = 'health'
        detail = $_.Exception.Message
        checked_at = (Get-Date).ToUniversalTime().ToString('o')
        recovery_attempted = $recoveryAttempted
    }
    Send-OperationsNotification $config.StateRoot 'health' 'Deployment health check failed' $_.Exception.Message
    throw
}
