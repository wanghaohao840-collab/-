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
$config = $null
$configurationWarning = $null
try {
    $config = Get-OperationsConfig -RepositoryRoot $RepositoryRoot -EnvFile $EnvFile -StateRoot $StateRoot -BackupRoot $BackupRoot
} catch {
    $configurationWarning = Protect-LogText ([string]$_.Exception.Message)
}

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

if ($null -ne $config) {
    Write-Output "Preserved environment file: $($config.EnvFile)"
    Write-Output "Preserved deployment data: $($config.DataRoot)"
    Write-Output "Preserved operations state: $($config.StateRoot)"
    Write-Output "Preserved backup location: $($config.BackupRoot)"
} else {
    $envLabel = Protect-LogText ([string]$EnvFile)
    $stateLabel = if ([string]::IsNullOrWhiteSpace($StateRoot)) {
        'deploy-state (safe default; not resolved)'
    } else {
        Protect-LogText ([string]$StateRoot)
    }
    $backupLabel = if ([string]::IsNullOrWhiteSpace($BackupRoot)) {
        'D:\python_self_agent_backups (safe default; not resolved)'
    } else {
        Protect-LogText ([string]$BackupRoot)
    }
    Write-Output "Configuration warning: $configurationWarning"
    Write-Output "Preserved environment file label: $envLabel"
    Write-Output 'Preserved deployment data label: deploy-data (safe default; not resolved)'
    Write-Output "Preserved operations state label: $stateLabel"
    Write-Output "Preserved backup location label: $backupLabel"
}
Write-Output 'Preserved containers and images.'
