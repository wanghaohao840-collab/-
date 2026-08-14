# Windows deployment operations

This guide operates the existing single-node Compose deployment from
`D:\python_self_agent`. Run the installation command from a signed-in
administrator session after preparing `deploy\.env` and starting the deployment.
It creates scheduled tasks and one inbound firewall rule; it does not create an
Internet-facing deployment.

Qdrant Dashboard is intentionally unavailable in this deployment. The Qdrant
HTTP API is the supported interface and remains private to the Compose network;
Qdrant ports are not published on the host. The derived image removes the
upstream static Web UI as a targeted remediation. This does not make the
unmodified upstream image vulnerability-free.

## Network preflight and installation

The installer refuses to continue when any active Internet-connected profile is
not `Private`. It does not change a `Public` profile for you. Inspect the current
profiles first:

```powershell
Get-NetConnectionProfile | Format-Table InterfaceAlias, InterfaceIndex, NetworkCategory, IPv4Connectivity, IPv6Connectivity
```

If an active Internet-connected profile is `Public`, resolve that classification
through the approved Windows network policy or, after confirming the interface
index and authorization, run the following explicit administrator command. Then
repeat the inspection and install only after the profile reports `Private`:

```powershell
Set-NetConnectionProfile -InterfaceIndex <interface-index> -NetworkCategory Private
```

Install the operations tasks and Private-intranet firewall rule:

```powershell
Start-Process powershell.exe -Verb RunAs -ArgumentList @(
  '-NoProfile', '-ExecutionPolicy', 'Bypass',
  '-File', 'D:\python_self_agent\deploy\windows\Install-Operations.ps1'
)
```

The firewall rule is restricted to TCP 7860 with `Private` and `LocalSubnet`:

```powershell
Get-NetFirewallRule -DisplayName 'Python Self Agent - Private Intranet 7860' |
  Format-List DisplayName, Enabled, Direction, Action, Profile
Get-NetFirewallRule -DisplayName 'Python Self Agent - Private Intranet 7860' |
  Get-NetFirewallPortFilter
Get-NetFirewallRule -DisplayName 'Python Self Agent - Private Intranet 7860' |
  Get-NetFirewallAddressFilter
```

Do not expose this HTTP service on a Public network. Internet access requires a
separately operated HTTPS gateway or reverse proxy with its own access controls.

## Scheduled operations

The installer registers these four tasks for the interactive installing user:

| Task | Schedule | Purpose |
| --- | --- | --- |
| `PythonSelfAgent-LoginRecovery` | at logon | start/recover the Compose deployment |
| `PythonSelfAgent-Health` | daily from 00:00, every 5 minutes | health check and recovery |
| `PythonSelfAgent-DailyBackup` | daily at 03:00 | cold backup; keeps 7 daily sets and 4 weekly sets |
| `PythonSelfAgent-MonthlyRestoreDrill` | first Sunday at 04:00 | isolated restore verification |

Inspect all task definitions and recent status:

```powershell
Get-ScheduledTask -TaskName 'PythonSelfAgent-*' |
  ForEach-Object { $_; Get-ScheduledTaskInfo -TaskName $_.TaskName }
```

Start a task manually when needed:

```powershell
Start-ScheduledTask -TaskName 'PythonSelfAgent-LoginRecovery'
Start-ScheduledTask -TaskName 'PythonSelfAgent-Health'
Start-ScheduledTask -TaskName 'PythonSelfAgent-DailyBackup'
Start-ScheduledTask -TaskName 'PythonSelfAgent-MonthlyRestoreDrill'
```

## Manual backup, restore, drill, and upgrade

Use explicit roots in manual commands. They override the documented defaults:
state is `D:\python_self_agent\deploy-state` and backups are stored outside the
repository at `D:\python_self_agent_backups`.

`DEPLOY_STATE_ROOT`, `DEPLOY_BACKUP_ROOT`, and
`OPERATIONS_NOTIFY_COOLDOWN_MINUTES` are loaded from `deploy\.env` when explicit
state or backup parameters are not supplied. The notification cooldown must be
an integer from 1 through 1440 minutes; its safe default is 30 minutes.

```powershell
Set-Location -LiteralPath 'D:\python_self_agent'
$state = 'D:\python_self_agent\deploy-state'
$backups = 'D:\python_self_agent_backups'
& .\deploy\windows\Backup-Deployment.ps1 -RepositoryRoot 'D:\python_self_agent' -EnvFile 'deploy\.env' -StateRoot $state -BackupRoot $backups
& .\deploy\windows\Restore-Deployment.ps1 -Archive "$backups\daily\assistant-<timestamp>.tar.gz" -RepositoryRoot 'D:\python_self_agent' -EnvFile 'deploy\.env' -StateRoot $state -BackupRoot $backups
& .\deploy\windows\Invoke-RestoreDrill.ps1 -RepositoryRoot 'D:\python_self_agent' -EnvFile 'deploy\.env' -StateRoot $state -BackupRoot $backups
& .\deploy\windows\Update-Deployment.ps1 -RepositoryRoot 'D:\python_self_agent' -EnvFile 'deploy\.env' -StateRoot $state -BackupRoot $backups
```

Backups are complete archive sets beneath `D:\python_self_agent_backups\daily`
and `D:\python_self_agent_backups\weekly`, each with checksum and metadata
sidecars. Do not delete rollback artifacts while investigating a failed restore
or upgrade. Upgrade reports record rollback image tags and backup archives in
`D:\python_self_agent\deploy-state\reports\update-*.json`; restore-drill
reports are in `D:\python_self_agent\deploy-state\reports\restore-drill-*.json`.
Operational logs and the latest status are
`D:\python_self_agent\deploy-state\operations.log` and
`D:\python_self_agent\deploy-state\status.json`.

View live application logs separately:

```powershell
Set-Location -LiteralPath 'D:\python_self_agent'
docker compose --env-file deploy\.env logs --tail 200 app qdrant
```

## Uninstall

Run the following from an elevated PowerShell session to remove the four tasks
and the Private-intranet firewall rule:

```powershell
Set-Location -LiteralPath 'D:\python_self_agent'
& .\deploy\windows\Uninstall-Operations.ps1 -RepositoryRoot 'D:\python_self_agent' -EnvFile 'deploy\.env' -StateRoot 'D:\python_self_agent\deploy-state' -BackupRoot 'D:\python_self_agent_backups'
```

Uninstall does not delete `deploy\.env`, deployment data, operations state,
backups, containers, or images. Remove those only through an explicit,
separately reviewed retention procedure.
