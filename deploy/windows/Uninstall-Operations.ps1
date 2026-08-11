#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$EnvFile = 'deploy\.env',
    [string]$StateRoot = 'deploy-state',
    [string]$BackupRoot = 'D:\python_self_agent_backups'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-UninstallPath {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$BasePath
    )

    if ([IO.Path]::IsPathRooted($Path)) {
        return [IO.Path]::GetFullPath($Path)
    }
    return [IO.Path]::GetFullPath((Join-Path $BasePath $Path))
}

$firewallName = 'Python Self Agent - Private Intranet 7860'
$taskNames = @(
    'PythonSelfAgent-LoginRecovery',
    'PythonSelfAgent-Health',
    'PythonSelfAgent-DailyBackup',
    'PythonSelfAgent-MonthlyRestoreDrill'
)
$repositoryPath = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$envPath = Resolve-UninstallPath -Path $EnvFile -BasePath $repositoryPath
$statePath = Resolve-UninstallPath -Path $StateRoot -BasePath $repositoryPath
$backupPath = Resolve-UninstallPath -Path $BackupRoot -BasePath $repositoryPath
$dataPath = Join-Path $repositoryPath 'deploy-data'

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

Write-Output "Preserved environment file: $envPath"
Write-Output "Preserved deployment data: $dataPath"
Write-Output "Preserved operations state: $statePath"
Write-Output "Preserved backup location: $backupPath"
Write-Output 'Preserved containers and images.'
