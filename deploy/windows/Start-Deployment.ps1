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

Import-Module (Join-Path $PSScriptRoot 'Operations.Common.psm1') -Force

function Get-SafeFallbackStateRoot {
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$RequestedStateRoot
    )

    $basePath = [IO.Path]::GetFullPath($Root)
    if ([IO.Path]::IsPathRooted($RequestedStateRoot)) {
        return [IO.Path]::GetFullPath($RequestedStateRoot)
    }
    return [IO.Path]::GetFullPath((Join-Path $basePath $RequestedStateRoot))
}

$fallbackStateRoot = Get-SafeFallbackStateRoot -Root $RepositoryRoot -RequestedStateRoot $StateRoot
$modulePath = Join-Path $PSScriptRoot 'Operations.Common.psm1'
$deadlineUtc = (Get-Date).ToUniversalTime().AddSeconds($TimeoutSeconds)
$startupJob = Start-Job -ScriptBlock {
    param($Root, $EnvironmentFile, $RequestedStateRoot, $FallbackStateRoot, $Deadline, $ModulePath)

    Set-StrictMode -Version Latest
    $ErrorActionPreference = 'Stop'
    Import-Module $ModulePath -Force

    function Get-RemainingSeconds {
        $remaining = [Math]::Floor(($Deadline - (Get-Date).ToUniversalTime()).TotalSeconds)
        return [Math]::Max(0, [int]$remaining)
    }

    $operationsStateRoot = $FallbackStateRoot
    try {
        $config = Get-OperationsConfig -RepositoryRoot $Root -EnvFile $EnvironmentFile -StateRoot $RequestedStateRoot
        $operationsStateRoot = $config.StateRoot

        $remainingSeconds = Get-RemainingSeconds
        if ($remainingSeconds -lt 1 -or -not (Wait-Until -TimeoutSeconds $remainingSeconds -IntervalSeconds 5 -Condition { Test-DockerReady })) {
            throw 'Docker Engine did not become ready before the startup deadline'
        }

        Push-Location $config.RepositoryRoot
        try {
            Invoke-External docker @('compose', '--env-file', $config.EnvFile, 'up', '-d') | Out-Null
        } finally {
            Pop-Location
        }

        $remainingSeconds = Get-RemainingSeconds
        if ($remainingSeconds -lt 1 -or -not (Wait-Until -TimeoutSeconds $remainingSeconds -IntervalSeconds 5 -Condition {
            (Test-ComposeHealth -Config $config).Healthy
        })) {
            throw 'Compose services did not become healthy before the startup deadline'
        }

        if ((Get-RemainingSeconds) -lt 1) {
            throw 'Startup deadline elapsed before the smoke test'
        }
        Invoke-External $config.Python @((Join-Path $config.RepositoryRoot 'deploy\smoke_test.py'), '--env-file', $config.EnvFile) | Out-Null
        if ((Get-RemainingSeconds) -lt 1) {
            throw 'Startup deadline elapsed during the smoke test'
        }
        Write-OperationsStatus $operationsStateRoot @{
            status = 'healthy'
            category = 'startup'
            checked_at = (Get-Date).ToUniversalTime().ToString('o')
        }
        return [PSCustomObject]@{ Succeeded = $true; StateRoot = $operationsStateRoot; Detail = $null }
    } catch {
        return [PSCustomObject]@{
            Succeeded = $false
            StateRoot = $operationsStateRoot
            Detail = $_.Exception.Message
        }
    }
} -ArgumentList $RepositoryRoot, $EnvFile, $StateRoot, $fallbackStateRoot, $deadlineUtc, $modulePath

try {
    $completedJob = Wait-Job -Job $startupJob -Timeout $TimeoutSeconds
    if ($null -eq $completedJob) {
        Stop-Job -Job $startupJob | Out-Null
        throw "Startup did not finish within $TimeoutSeconds seconds"
    }

    $result = Receive-Job -Job $startupJob -ErrorAction Stop
    if ($null -eq $result -or -not $result.Succeeded) {
        if ($null -ne $result -and -not [string]::IsNullOrWhiteSpace($result.StateRoot)) {
            $fallbackStateRoot = $result.StateRoot
        }
        $detail = if ($null -eq $result) { 'Startup job returned no result' } else { $result.Detail }
        throw $detail
    }
} catch {
    Write-OperationsLog $fallbackStateRoot 'startup' $_.Exception.Message 'ERROR'
    Write-OperationsStatus $fallbackStateRoot @{
        status = 'failed'
        category = 'startup'
        detail = $_.Exception.Message
        checked_at = (Get-Date).ToUniversalTime().ToString('o')
    }
    Send-OperationsNotification $fallbackStateRoot 'startup' 'Deployment startup failed' $_.Exception.Message | Out-Null
    throw
} finally {
    Remove-Job -Job $startupJob -Force -ErrorAction SilentlyContinue
}
