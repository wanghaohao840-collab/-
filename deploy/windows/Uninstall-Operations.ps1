#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$EnvFile = 'deploy\.env',
    [string]$StateRoot = $null,
    [string]$BackupRoot = $null
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'Operations.Common.psm1') -Force

$firewallName = 'Python Self Agent - Private Intranet 7860'
$taskNames = @(
    'PythonSelfAgent-LoginRecovery',
    'PythonSelfAgent-Health',
    'PythonSelfAgent-DailyBackup',
    'PythonSelfAgent-MonthlyRestoreDrill'
)
$config = Get-OperationsConfig -RepositoryRoot $RepositoryRoot -EnvFile $EnvFile -StateRoot $StateRoot -BackupRoot $BackupRoot

foreach ($taskName in $taskNames) {
    $matchingTasks = @(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue |
        Where-Object { $_.TaskName -eq $taskName })
    if ($matchingTasks.Count -gt 0 -and $PSCmdlet.ShouldProcess($taskName, 'Unregister scheduled task')) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
}

$matchingRules = @(Get-NetFirewallRule -DisplayName $firewallName -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -eq $firewallName })
if ($matchingRules.Count -gt 0 -and $PSCmdlet.ShouldProcess($firewallName, 'Remove firewall rule')) {
    $matchingRules | Remove-NetFirewallRule -ErrorAction Stop
}

Write-Output "Preserved environment file: $($config.EnvFile)"
Write-Output "Preserved deployment data: $($config.DataRoot)"
Write-Output "Preserved operations state: $($config.StateRoot)"
Write-Output "Preserved backup location: $($config.BackupRoot)"
Write-Output 'Preserved containers and images.'
