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
$config = Get-OperationsConfig -RepositoryRoot $RepositoryRoot -EnvFile $EnvFile -StateRoot $StateRoot

try {
    if (-not (Wait-Until -TimeoutSeconds $TimeoutSeconds -IntervalSeconds 5 -Condition { Test-DockerReady })) {
        throw "Docker Engine was not ready within $TimeoutSeconds seconds"
    }

    Push-Location $config.RepositoryRoot
    try {
        Invoke-External docker @('compose', '--env-file', $config.EnvFile, 'up', '-d') | Out-Null
    } finally {
        Pop-Location
    }

    if (-not (Wait-Until -TimeoutSeconds $TimeoutSeconds -IntervalSeconds 5 -Condition {
        (Test-ComposeHealth -Config $config).Healthy
    })) {
        throw 'Compose services did not become healthy'
    }

    Invoke-External $config.Python @((Join-Path $config.RepositoryRoot 'deploy\smoke_test.py'), '--env-file', $config.EnvFile) | Out-Null
    Write-OperationsStatus $config.StateRoot @{
        status = 'healthy'
        category = 'startup'
        checked_at = (Get-Date).ToUniversalTime().ToString('o')
    }
} catch {
    Write-OperationsLog $config.StateRoot 'startup' $_.Exception.Message 'ERROR'
    Write-OperationsStatus $config.StateRoot @{
        status = 'failed'
        category = 'startup'
        detail = $_.Exception.Message
    }
    Send-OperationsNotification $config.StateRoot 'startup' 'Deployment startup failed' $_.Exception.Message
    throw
}
