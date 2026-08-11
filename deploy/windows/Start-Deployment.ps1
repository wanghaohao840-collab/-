#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$EnvFile = 'deploy\.env',
    [string]$StateRoot = 'deploy-state',
    [ValidateRange(30, 600)][int]$TimeoutSeconds = 180
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

$modulePath = Join-Path $PSScriptRoot 'Operations.Common.psm1'
$moduleAvailable = $false
$config = $null
$startupJob = $null

try {
    Import-Module $modulePath -Force
    $moduleAvailable = $true
    $config = Get-OperationsConfig -RepositoryRoot $RepositoryRoot -EnvFile $EnvFile -StateRoot $StateRoot
    $deadlineUtc = (Get-Date).ToUniversalTime().AddSeconds($TimeoutSeconds)
    $startupJob = Start-Job -ScriptBlock {
        param($Config, $Deadline, $ModulePath)

        Set-StrictMode -Version Latest
        $ErrorActionPreference = 'Stop'

        function Get-RemainingSeconds {
            $remaining = [Math]::Floor(($Deadline - (Get-Date).ToUniversalTime()).TotalSeconds)
            return [Math]::Max(0, [int]$remaining)
        }

        try {
            Import-Module $ModulePath -Force

            $remainingSeconds = Get-RemainingSeconds
            if ($remainingSeconds -lt 1 -or -not (Wait-Until -TimeoutSeconds $remainingSeconds -IntervalSeconds 5 -Condition { Test-DockerReady })) {
                throw 'Docker Engine did not become ready before the startup deadline'
            }

            Push-Location $Config.RepositoryRoot
            try {
                Invoke-External docker @('compose', '--env-file', $Config.EnvFile, 'up', '-d') | Out-Null
            } finally {
                Pop-Location
            }

            $remainingSeconds = Get-RemainingSeconds
            if ($remainingSeconds -lt 1 -or -not (Wait-Until -TimeoutSeconds $remainingSeconds -IntervalSeconds 5 -Condition {
                (Test-ComposeHealth -Config $Config).Healthy
            })) {
                throw 'Compose services did not become healthy before the startup deadline'
            }

            if ((Get-RemainingSeconds) -lt 1) {
                throw 'Startup deadline elapsed before the smoke test'
            }
            Invoke-External $Config.Python @((Join-Path $Config.RepositoryRoot 'deploy\smoke_test.py'), '--env-file', $Config.EnvFile) | Out-Null
            if ((Get-RemainingSeconds) -lt 1) {
                throw 'Startup deadline elapsed during the smoke test'
            }
            return [PSCustomObject]@{ Succeeded = $true; Detail = $null }
        } catch {
            return [PSCustomObject]@{ Succeeded = $false; Detail = $_.Exception.Message }
        }
    } -ArgumentList $config, $deadlineUtc, $modulePath

    $completedJob = Wait-Job -Job $startupJob -Timeout $TimeoutSeconds
    if ($null -eq $completedJob) {
        Stop-Job -Job $startupJob | Out-Null
        throw "Startup did not finish within $TimeoutSeconds seconds"
    }

    $result = Receive-Job -Job $startupJob -ErrorAction Stop
    if ($null -eq $result -or -not $result.Succeeded) {
        $detail = if ($null -eq $result) { 'Startup job returned no result' } else { $result.Detail }
        throw $detail
    }

    Write-OperationsStatus $config.StateRoot @{
        status = 'healthy'
        category = 'startup'
        checked_at = (Get-Date).ToUniversalTime().ToString('o')
    }
} catch {
    if ($moduleAvailable -and $null -ne $config) {
        Write-OperationsLog $config.StateRoot 'startup' $_.Exception.Message 'ERROR'
        Write-OperationsStatus $config.StateRoot @{
            status = 'failed'
            category = 'startup'
            detail = $_.Exception.Message
            checked_at = (Get-Date).ToUniversalTime().ToString('o')
        }
        Send-OperationsNotification $config.StateRoot 'startup' 'Deployment startup failed' $_.Exception.Message | Out-Null
        Write-TrustedFallbackTelemetry -Category 'startup'
    } else {
        Write-TrustedFallbackTelemetry -Category 'startup'
    }
    throw
} finally {
    if ($null -ne $startupJob) {
        Remove-Job -Job $startupJob -Force -ErrorAction SilentlyContinue
    }
}
