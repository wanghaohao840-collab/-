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

function Assert-RequiredCommand {
    param([Parameter(Mandatory)][string]$Name)

    if ($null -eq (Get-Command -Name $Name -CommandType Application -ErrorAction SilentlyContinue)) {
        throw "Required command was not found: $Name"
    }
}

function Assert-PrivateInternetProfiles {
    $activeProfiles = @(
        Get-NetConnectionProfile | Where-Object {
            $_.IPv4Connectivity -eq 'Internet' -or $_.IPv6Connectivity -eq 'Internet'
        }
    )
    $nonPrivateProfiles = @($activeProfiles | Where-Object { $_.NetworkCategory -ne 'Private' })
    if ($nonPrivateProfiles.Count -gt 0) {
        throw 'Every active Internet-connected network profile must be Private before installation.'
    }
}

function Get-OperationsWtsNativeMethods {
    if ($null -eq ('PythonSelfAgent.Operations.WtsNativeMethods' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace PythonSelfAgent.Operations
{
    public enum WtsInfoClass
    {
        WTSUserName = 5,
        WTSDomainName = 7
    }

    public static class WtsNativeMethods
    {
        [DllImport("Wtsapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern bool WTSQuerySessionInformation(
            IntPtr hServer,
            int sessionId,
            WtsInfoClass wtsInfoClass,
            out IntPtr ppBuffer,
            out int pBytesReturned);

        [DllImport("Wtsapi32.dll")]
        public static extern void WTSFreeMemory(IntPtr pMemory);
    }
}
'@
    }
    return ('PythonSelfAgent.Operations.WtsNativeMethods' -as [type])
}

function Get-WtsSessionValue {
    param(
        [Parameter(Mandatory)][int]$SessionId,
        [Parameter(Mandatory)][int]$InfoClass
    )

    $nativeMethods = Get-OperationsWtsNativeMethods
    $buffer = [IntPtr]::Zero
    $bytesReturned = 0
    try {
        if (-not $nativeMethods::WTSQuerySessionInformation(
            [IntPtr]::Zero,
            $SessionId,
            $InfoClass,
            [ref]$buffer,
            [ref]$bytesReturned
        )) {
            throw 'Unable to resolve the user attached to the installer process session.'
        }
        if ($buffer -eq [IntPtr]::Zero -or $bytesReturned -lt 2) {
            return $null
        }
        return [Runtime.InteropServices.Marshal]::PtrToStringUni($buffer)
    } finally {
        if ($buffer -ne [IntPtr]::Zero) {
            $nativeMethods::WTSFreeMemory($buffer)
        }
    }
}

function Get-OperationsInteractiveUserName {
    $sessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId
    $userName = Get-WtsSessionValue -SessionId $sessionId -InfoClass 5
    $domainName = Get-WtsSessionValue -SessionId $sessionId -InfoClass 7
    if ([string]::IsNullOrWhiteSpace($domainName) -or [string]::IsNullOrWhiteSpace($userName)) {
        throw 'The installer process session must have one nonempty domain and user name.'
    }
    return ('{0}\{1}' -f $domainName.Trim(), $userName.Trim())
}

function Test-ListenerAddressCompatibleWithBinding {
    param(
        [Parameter(Mandatory)][string]$ListenerAddress,
        [Parameter(Mandatory)][AllowEmptyString()][string]$HostIp
    )

    $normalizedListener = $ListenerAddress.Trim().ToLowerInvariant()
    $normalizedHost = $HostIp.Trim().ToLowerInvariant()
    if ([string]::IsNullOrWhiteSpace($normalizedHost) -or $normalizedHost -eq '0.0.0.0') {
        return $normalizedListener -in @('0.0.0.0', '::', '127.0.0.1', '::1')
    }
    return $normalizedListener -eq $normalizedHost
}

function Test-ComposePortListenerOwnership {
    param(
        [Parameter(Mandatory)][object[]]$Listeners,
        [Parameter(Mandatory)][object[]]$PortBindings,
        [Parameter(Mandatory)][hashtable]$ProcessesById
    )

    if ($Listeners.Count -eq 0) {
        return $false
    }

    $wildcardBindings = @(
        $PortBindings | Where-Object {
            $_.HostPort -eq '7860' -and
                ([string]::IsNullOrWhiteSpace([string]$_.HostIp) -or [string]$_.HostIp -eq '0.0.0.0')
        }
    )
    if ($wildcardBindings.Count -gt 0) {
        $wildcardListeners = @(
            $Listeners | Where-Object { [string]$_.LocalAddress -in @('0.0.0.0', '::') }
        )
        if ($wildcardListeners.Count -eq 0) {
            return $false
        }
    }

    $acceptedProcessNames = @('com.docker.backend', 'docker-proxy', 'wslrelay')
    foreach ($listener in $Listeners) {
        $listenerAddress = [string]$listener.LocalAddress
        $compatibleBindings = @(
            $PortBindings | Where-Object {
                $_.HostPort -eq '7860' -and
                    (Test-ListenerAddressCompatibleWithBinding -ListenerAddress $listenerAddress -HostIp ([string]$_.HostIp))
            }
        )
        if ($compatibleBindings.Count -eq 0) {
            return $false
        }

        $processId = [int]$listener.OwningProcess
        if ($processId -eq 4 -or -not $ProcessesById.ContainsKey($processId)) {
            return $false
        }
        $processName = ([string]$ProcessesById[$processId].ProcessName).ToLowerInvariant()
        if ($acceptedProcessNames -notcontains $processName) {
            return $false
        }
    }
    return $true
}

function Assert-Tcp7860IsFreeOrOwnedByComposeApp {
    param([Parameter(Mandatory)]$Config)

    $listeners = @(Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -eq 0) {
        return
    }

    $appContainerIds = @(
        Invoke-External -FilePath 'docker' -ArgumentList @(
            'compose',
            '--project-directory', $Config.RepositoryRoot,
            '--file', $Config.ComposeFile,
            '--env-file', $Config.EnvFile,
            'ps', '-q', 'app'
        )
    )
    $appContainerIds = @($appContainerIds | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($appContainerIds.Count -ne 1) {
        throw 'TCP 7860 is occupied and the current Compose app must resolve to exactly one container.'
    }
    $appContainerId = $appContainerIds[0].Trim()

    $portJson = @(
        Invoke-External -FilePath 'docker' -ArgumentList @(
            'inspect', '--format', '{{json .HostConfig.PortBindings}}', $appContainerId
        )
    ) | Select-Object -First 1
    $portBindings = $portJson | ConvertFrom-Json
    $appPortBindings = @($portBindings.'7860/tcp' | Where-Object { $_.HostPort -eq '7860' })
    if ($appPortBindings.Count -eq 0) {
        throw 'TCP 7860 is occupied by a process outside the current Compose app mapping.'
    }

    $processesById = @{}
    foreach ($listener in $listeners) {
        $listenerProcessId = [int]$listener.OwningProcess
        if ($listenerProcessId -eq 4) {
            throw 'TCP 7860 is owned by System and cannot be correlated to an accepted Docker Desktop forwarder.'
        }
        try {
            $processesById[$listenerProcessId] = Get-Process -Id $listenerProcessId -ErrorAction Stop
        } catch {
            throw 'TCP 7860 listener process could not be resolved for Compose ownership validation.'
        }
    }
    if (-not (Test-ComposePortListenerOwnership -Listeners $listeners -PortBindings $appPortBindings -ProcessesById $processesById)) {
        throw 'TCP 7860 listeners do not unambiguously match the current Compose app Docker Desktop forwarding process.'
    }
}

function New-OperationsTaskAction {
    param(
        [Parameter(Mandatory)][string]$ScriptPath,
        [Parameter(Mandatory)]$Config,
        [switch]$IncludeBackupRoot
    )

    $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -RepositoryRoot "{1}" -EnvFile "{2}" -StateRoot "{3}"' -f $ScriptPath, $Config.RepositoryRoot, $Config.EnvFile, $Config.StateRoot
    if ($IncludeBackupRoot) {
        $arguments += ' -BackupRoot "{0}"' -f $Config.BackupRoot
    }
    return New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
}

function New-OperationsTaskSettings {
    param([bool]$WakeToRun)

    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
    $settings.StartWhenAvailable = $true
    $settings.MultipleInstances = 'IgnoreNew'
    $settings.WakeToRun = $WakeToRun
    return $settings
}

$modulePath = Join-Path $PSScriptRoot 'Operations.Common.psm1'
Import-Module $modulePath -Force

$config = Get-OperationsConfig -RepositoryRoot $RepositoryRoot -EnvFile $EnvFile -StateRoot $StateRoot -BackupRoot $BackupRoot
$taskScripts = [ordered]@{
    'PythonSelfAgent-LoginRecovery' = Join-Path $PSScriptRoot 'Start-Deployment.ps1'
    'PythonSelfAgent-Health' = Join-Path $PSScriptRoot 'Test-DeploymentHealth.ps1'
    'PythonSelfAgent-DailyBackup' = Join-Path $PSScriptRoot 'Backup-Deployment.ps1'
    'PythonSelfAgent-MonthlyRestoreDrill' = Join-Path $PSScriptRoot 'Invoke-RestoreDrill.ps1'
}

foreach ($scriptPath in $taskScripts.Values) {
    if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
        throw "Required task script was not found: $scriptPath"
    }
}

Assert-RequiredCommand -Name 'docker.exe'
Assert-RequiredCommand -Name 'tar.exe'
Invoke-External -FilePath 'docker.exe' -ArgumentList @('version') | Out-Null
Invoke-External -FilePath 'docker.exe' -ArgumentList @('compose', 'version') | Out-Null
Invoke-External -FilePath 'docker.exe' -ArgumentList @('scout', 'version') | Out-Null
Invoke-External -FilePath 'tar.exe' -ArgumentList @('--version') | Out-Null
Assert-PrivateInternetProfiles
Assert-Tcp7860IsFreeOrOwnedByComposeApp -Config $config
$currentUser = Get-OperationsInteractiveUserName

# All preflight checks above this line. System changes below are intentional.
$firewallName = 'Python Self Agent - Private Intranet 7860'
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Highest
$loginTrigger = New-ScheduledTaskTrigger -AtLogOn
$healthTrigger = New-ScheduledTaskTrigger -Daily -At '00:00'
$healthRepetition = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 1)
$healthTrigger.Repetition = $healthRepetition.Repetition
$backupTrigger = New-ScheduledTaskTrigger -Daily -At '03:00'
$monthlyTrigger = New-CimInstance -Namespace 'Root/Microsoft/Windows/TaskScheduler' -ClassName MSFT_TaskMonthlyDOWTrigger -ClientOnly
$monthlyTrigger.Enabled = $true
$monthlyTrigger.StartBoundary = (Get-Date -Hour 4 -Minute 0 -Second 0).ToString('s')
$monthlyTrigger.DaysOfWeek = 1 # Sunday
$monthlyTrigger.WeeksOfMonth = 1 # First week only
$monthlyTrigger.MonthsOfYear = 4095 # Every month

if ($PSCmdlet.ShouldProcess($config.StateRoot, 'Create operations state directory')) {
    New-Item -ItemType Directory -Force -Path $config.StateRoot | Out-Null
}
if ($PSCmdlet.ShouldProcess($config.EnvFile, 'Restrict deployment environment file ACL')) {
    Invoke-External -FilePath 'icacls.exe' -ArgumentList @($config.EnvFile, '/inheritance:r') | Out-Null
    Invoke-External -FilePath 'icacls.exe' -ArgumentList @($config.EnvFile, '/grant:r', "${currentUser}:(M)") | Out-Null
    Invoke-External -FilePath 'icacls.exe' -ArgumentList @($config.EnvFile, '/grant', 'SYSTEM:(F)') | Out-Null
    Invoke-External -FilePath 'icacls.exe' -ArgumentList @($config.EnvFile, '/grant', 'Administrators:(F)') | Out-Null
}
if ($PSCmdlet.ShouldProcess($firewallName, 'Replace private intranet firewall rule')) {
    Get-NetFirewallRule -DisplayName $firewallName -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -eq $firewallName } |
        Remove-NetFirewallRule -ErrorAction Stop
    New-NetFirewallRule -DisplayName $firewallName -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort 7860 -Profile Private -RemoteAddress LocalSubnet | Out-Null
}

$taskDefinitions = @(
    [PSCustomObject]@{
        Name = 'PythonSelfAgent-LoginRecovery'
        Action = New-OperationsTaskAction -ScriptPath $taskScripts['PythonSelfAgent-LoginRecovery'] -Config $config
        Trigger = $loginTrigger
        Settings = New-OperationsTaskSettings -WakeToRun $false
    },
    [PSCustomObject]@{
        Name = 'PythonSelfAgent-Health'
        Action = New-OperationsTaskAction -ScriptPath $taskScripts['PythonSelfAgent-Health'] -Config $config
        Trigger = $healthTrigger
        Settings = New-OperationsTaskSettings -WakeToRun $false
    },
    [PSCustomObject]@{
        Name = 'PythonSelfAgent-DailyBackup'
        Action = New-OperationsTaskAction -ScriptPath $taskScripts['PythonSelfAgent-DailyBackup'] -Config $config -IncludeBackupRoot
        Trigger = $backupTrigger
        Settings = New-OperationsTaskSettings -WakeToRun $true
    },
    [PSCustomObject]@{
        Name = 'PythonSelfAgent-MonthlyRestoreDrill'
        Action = New-OperationsTaskAction -ScriptPath $taskScripts['PythonSelfAgent-MonthlyRestoreDrill'] -Config $config -IncludeBackupRoot
        Trigger = $monthlyTrigger
        Settings = New-OperationsTaskSettings -WakeToRun $true
    }
)

foreach ($task in $taskDefinitions) {
    if ($PSCmdlet.ShouldProcess($task.Name, 'Register scheduled task')) {
        Register-ScheduledTask -TaskName $task.Name -Action $task.Action -Trigger $task.Trigger `
            -Settings $task.Settings -Principal $principal -Force | Out-Null
    }
}

Write-Output 'Windows deployment operations installation completed.'
