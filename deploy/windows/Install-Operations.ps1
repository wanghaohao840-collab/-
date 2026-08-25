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

    $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -RepositoryRoot "{1}" -EnvFile "{2}" -StateRoot "{3}"' -f $ScriptPath, $Config.RepositoryRoot, $Config.EnvFile, $Config.StateRoot
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

function Resolve-OperationsMonthlyTriggerSchema {
    param([Parameter(Mandatory)]$CimClass)

    $expectedNamespace = 'Root/Microsoft/Windows/TaskScheduler'
    $expectedClass = 'MSFT_TaskMonthlyDOWTrigger'
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals(
            [string]$CimClass.CimSystemProperties.Namespace,
            $expectedNamespace
        ) -or [string]$CimClass.CimClassName -cne $expectedClass) {
        throw 'Monthly trigger CIM class or namespace did not match the exact contract.'
    }

    $properties = @($CimClass.CimClassProperties)
    $expectedTypes = [ordered]@{
        Enabled = 'Boolean'
        StartBoundary = 'String'
        DaysOfWeek = 'UInt16'
        WeeksOfMonth = 'UInt16'
    }
    foreach ($entry in $expectedTypes.GetEnumerator()) {
        $matches = @($properties | Where-Object { [string]$_.Name -ceq $entry.Key })
        if ($matches.Count -ne 1) {
            throw "Monthly trigger schema is missing required property: $($entry.Key)."
        }
        if ([string]$matches[0].CimType -cne $entry.Value) {
            throw "Monthly trigger schema has invalid CIM type for $($entry.Key)."
        }
    }

    $monthCandidates = @($properties | Where-Object {
        [string]$_.Name -ieq 'MonthsOfYear' -or [string]$_.Name -ieq 'MonthOfYear'
    })
    $monthMatches = @($monthCandidates | Where-Object {
        [string]$_.Name -ceq 'MonthsOfYear' -or [string]$_.Name -ceq 'MonthOfYear'
    })
    if ($monthCandidates.Count -ne 1 -or $monthMatches.Count -ne 1) {
        throw 'Monthly trigger schema must expose exactly one MonthOfYear/MonthsOfYear property.'
    }
    if ([string]$monthMatches[0].CimType -cne 'UInt16') {
        throw "Monthly trigger schema has invalid CIM type for $($monthMatches[0].Name)."
    }
    return [string]$monthMatches[0].Name
}

function Assert-OperationsMonthlyRestoreDrillTrigger {
    param(
        [Parameter(Mandatory)]$Trigger,
        [Parameter(Mandatory)][ValidateSet('MonthsOfYear', 'MonthOfYear')]
        [string]$MonthPropertyName,
        [Parameter(Mandatory)][string]$ExpectedStartBoundary
    )

    if ($Trigger.GetType().FullName -cne 'Microsoft.Management.Infrastructure.CimInstance' -or
        [string]$Trigger.CimClass.CimClassName -cne 'MSFT_TaskMonthlyDOWTrigger') {
        throw 'Monthly trigger did not materialize the exact CIM instance type.'
    }
    $requiredTriggerType = 'Microsoft.Management.Infrastructure.CimInstance#MSFT_TaskTrigger'
    if ($Trigger.PSTypeNames -notcontains $requiredTriggerType) {
        throw 'Monthly trigger is missing required MSFT_TaskTrigger type.'
    }
    foreach ($name in @('Enabled', 'StartBoundary', 'DaysOfWeek', 'WeeksOfMonth', $MonthPropertyName)) {
        if ($null -eq $Trigger.PSObject.Properties[$name]) {
            throw "Monthly trigger is missing materialized property: $name."
        }
    }

    $expected = [ordered]@{
        Enabled = $true
        StartBoundary = $ExpectedStartBoundary
        DaysOfWeek = [uint16]1
        WeeksOfMonth = [uint16]1
    }
    $expected[$MonthPropertyName] = [uint16]4095
    foreach ($entry in $expected.GetEnumerator()) {
        $actual = $Trigger.PSObject.Properties[$entry.Key].Value
        if ($actual -ne $entry.Value) {
            throw "Invalid monthly trigger value: $($entry.Key)."
        }
    }
}

function New-OperationsMonthlyRestoreDrillTrigger {
    param([Parameter(Mandatory)][datetime]$StartBoundary)

    if ($StartBoundary.Hour -ne 4 -or $StartBoundary.Minute -ne 0 -or
        $StartBoundary.Second -ne 0) {
        throw 'Monthly restore drill StartBoundary must be 04:00:00.'
    }
    $namespace = 'Root/Microsoft/Windows/TaskScheduler'
    $className = 'MSFT_TaskMonthlyDOWTrigger'
    $classes = @(Get-CimClass -Namespace $namespace -ClassName $className -ErrorAction Stop)
    if ($classes.Count -ne 1) {
        throw 'Monthly trigger CIM class resolution did not return exactly one class.'
    }
    $monthPropertyName = Resolve-OperationsMonthlyTriggerSchema -CimClass $classes[0]
    $boundary = $StartBoundary.ToString('s', [Globalization.CultureInfo]::InvariantCulture)
    $properties = @{
        Enabled = $true
        StartBoundary = $boundary
        DaysOfWeek = [uint16]1
        WeeksOfMonth = [uint16]1
    }
    $properties[$monthPropertyName] = [uint16]4095
    $trigger = New-CimInstance -CimClass $classes[0] -ClientOnly `
        -Property $properties -ErrorAction Stop
    Assert-OperationsMonthlyRestoreDrillTrigger -Trigger $trigger `
        -MonthPropertyName $monthPropertyName -ExpectedStartBoundary $boundary
    return $trigger
}

function Add-OperationsTaskXmlElement {
    param(
        [Parameter(Mandatory)][xml]$Document,
        [Parameter(Mandatory)][Xml.XmlNode]$Parent,
        [Parameter(Mandatory)][string]$Namespace,
        [Parameter(Mandatory)][string]$Name,
        [AllowNull()][string]$Value
    )

    $element = $Document.CreateElement($Name, $Namespace)
    if ($PSBoundParameters.ContainsKey('Value')) {
        $element.InnerText = $Value
    }
    [void]$Parent.AppendChild($element)
    return $element
}

function Assert-OperationsMonthlyRestoreDrillXml {
    param(
        [Parameter(Mandatory)][string]$XmlText,
        [Parameter(Mandatory)]$ExpectedTask,
        [Parameter(Mandatory)][string]$ExpectedSid
    )

    [xml]$document = $XmlText
    $namespace = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
    $manager = New-Object Xml.XmlNamespaceManager($document.NameTable)
    $manager.AddNamespace('t', $namespace)
    $one = {
        param([string]$Path)
        $nodes = @($document.SelectNodes($Path, $manager))
        if ($nodes.Count -ne 1) {
            throw "Monthly XML node count is invalid: $Path"
        }
        return $nodes[0]
    }

    $root = & $one '/t:Task'
    if ($root.GetAttribute('version') -cne '1.4') {
        throw 'Monthly XML version is invalid.'
    }
    $triggers = & $one '/t:Task/t:Triggers'
    $calendar = & $one '/t:Task/t:Triggers/t:CalendarTrigger'
    if (@($triggers.SelectNodes('*')).Count -ne 1 -or
        $calendar.LocalName -cne 'CalendarTrigger') {
        throw 'Monthly XML trigger collection is invalid.'
    }
    $schedule = & $one '/t:Task/t:Triggers/t:CalendarTrigger/t:ScheduleByMonthDayOfWeek'
    $start = & $one '/t:Task/t:Triggers/t:CalendarTrigger/t:StartBoundary'
    $triggerEnabled = & $one '/t:Task/t:Triggers/t:CalendarTrigger/t:Enabled'
    $days = @($schedule.SelectNodes('t:DaysOfWeek/*', $manager) | ForEach-Object LocalName)
    $weeks = @($schedule.SelectNodes('t:Weeks/t:Week', $manager) | ForEach-Object InnerText)
    $months = @($schedule.SelectNodes('t:Months/*', $manager) | ForEach-Object LocalName)
    $expectedMonths = @(
        'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December'
    )
    $expectedBoundary = ([datetime]$ExpectedTask.Trigger.StartBoundary).ToString(
        'yyyy-MM-ddTHH:mm:ss',
        [Globalization.CultureInfo]::InvariantCulture
    )
    if ($start.InnerText -cne $expectedBoundary -or
        ([datetime]$start.InnerText).TimeOfDay -ne [timespan]'04:00:00' -or
        $triggerEnabled.InnerText -cne 'true' -or
        $days.Count -ne 1 -or $days[0] -cne 'Sunday' -or
        $weeks.Count -ne 1 -or $weeks[0] -cne '1' -or
        $months.Count -ne 12 -or
        @(Compare-Object ($expectedMonths | Sort-Object) ($months | Sort-Object)).Count -ne 0) {
        throw 'Monthly XML schedule is invalid.'
    }

    $principals = & $one '/t:Task/t:Principals'
    $principal = & $one '/t:Task/t:Principals/t:Principal'
    if (@($principals.SelectNodes('*')).Count -ne 1 -or
        $principal.GetAttribute('id') -cne 'Author') {
        throw 'Monthly XML principal collection is invalid.'
    }
    $userId = & $one '/t:Task/t:Principals/t:Principal/t:UserId'
    $logonType = & $one '/t:Task/t:Principals/t:Principal/t:LogonType'
    $runLevel = & $one '/t:Task/t:Principals/t:Principal/t:RunLevel'
    if ($userId.InnerText -cne $ExpectedSid -or
        $logonType.InnerText -cne 'InteractiveToken' -or
        $runLevel.InnerText -cne 'HighestAvailable') {
        throw 'Monthly XML principal is invalid.'
    }

    $actions = & $one '/t:Task/t:Actions'
    $exec = & $one '/t:Task/t:Actions/t:Exec'
    if ($actions.GetAttribute('Context') -cne 'Author' -or
        @($actions.SelectNodes('*')).Count -ne 1 -or
        $exec.LocalName -cne 'Exec') {
        throw 'Monthly XML action collection is invalid.'
    }
    $command = & $one '/t:Task/t:Actions/t:Exec/t:Command'
    $arguments = & $one '/t:Task/t:Actions/t:Exec/t:Arguments'
    if (@($ExpectedTask.Action).Count -ne 1 -or
        $command.InnerText -cne [string]$ExpectedTask.Action.Execute -or
        $arguments.InnerText -cne [string]$ExpectedTask.Action.Arguments -or
        [regex]::Matches(
            $arguments.InnerText,
            '(?i)(?<!\S)-WindowStyle\s+Hidden(?!\S)'
        ).Count -ne 1) {
        throw 'Monthly XML action is invalid.'
    }

    $settings = & $one '/t:Task/t:Settings'
    $expectedSettings = [ordered]@{
        MultipleInstancesPolicy = 'IgnoreNew'
        DisallowStartIfOnBatteries = 'true'
        StopIfGoingOnBatteries = 'true'
        AllowHardTerminate = 'true'
        StartWhenAvailable = 'true'
        RunOnlyIfNetworkAvailable = 'false'
        AllowStartOnDemand = 'true'
        Enabled = 'true'
        Hidden = 'false'
        RunOnlyIfIdle = 'false'
        WakeToRun = 'true'
        ExecutionTimeLimit = 'PT72H'
        Priority = '7'
        DisallowStartOnRemoteAppSession = 'false'
        UseUnifiedSchedulingEngine = 'true'
    }
    $settingNames = @($settings.SelectNodes('*') | ForEach-Object LocalName)
    $expectedSettingNames = @($expectedSettings.Keys) + @('IdleSettings')
    if ($settingNames.Count -ne $expectedSettingNames.Count -or
        @(Compare-Object ($expectedSettingNames | Sort-Object) ($settingNames | Sort-Object)).Count -ne 0) {
        throw 'Monthly XML settings collection is invalid.'
    }
    foreach ($entry in $expectedSettings.GetEnumerator()) {
        $node = & $one ("/t:Task/t:Settings/t:{0}" -f $entry.Key)
        if ($node.InnerText -cne $entry.Value) {
            throw "Monthly XML setting is invalid: $($entry.Key)"
        }
    }
    $idle = & $one '/t:Task/t:Settings/t:IdleSettings'
    $expectedIdle = [ordered]@{
        Duration = 'PT10M'
        WaitTimeout = 'PT1H'
        StopOnIdleEnd = 'true'
        RestartOnIdle = 'false'
    }
    $idleNames = @($idle.SelectNodes('*') | ForEach-Object LocalName)
    if ($idleNames.Count -ne $expectedIdle.Count -or
        @(Compare-Object ($expectedIdle.Keys | Sort-Object) ($idleNames | Sort-Object)).Count -ne 0) {
        throw 'Monthly XML idle settings collection is invalid.'
    }
    foreach ($entry in $expectedIdle.GetEnumerator()) {
        $nodes = @($idle.SelectNodes(("t:{0}" -f $entry.Key), $manager))
        if ($nodes.Count -ne 1 -or $nodes[0].InnerText -cne $entry.Value) {
            throw "Monthly XML idle setting is invalid: $($entry.Key)"
        }
    }
}

function New-OperationsMonthlyRestoreDrillXml {
    param(
        [Parameter(Mandatory)]$Task,
        [Parameter(Mandatory)]$Principal
    )

    if ($Task.Name -cne 'PythonSelfAgent-MonthlyRestoreDrill' -or
        @($Task.Action).Count -ne 1 -or @($Task.Trigger).Count -ne 1) {
        throw 'Monthly XML input task is invalid.'
    }
    $sid = ([Security.Principal.NTAccount]([string]$Principal.UserId)).Translate(
        [Security.Principal.SecurityIdentifier]
    ).Value
    $namespace = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
    $document = New-Object Xml.XmlDocument
    [void]$document.AppendChild($document.CreateXmlDeclaration('1.0', 'UTF-16', $null))
    $root = $document.CreateElement('Task', $namespace)
    $root.SetAttribute('version', '1.4')
    [void]$document.AppendChild($root)

    $triggers = Add-OperationsTaskXmlElement $document $root $namespace 'Triggers'
    $calendar = Add-OperationsTaskXmlElement $document $triggers $namespace 'CalendarTrigger'
    Add-OperationsTaskXmlElement $document $calendar $namespace 'StartBoundary' `
        ([datetime]$Task.Trigger.StartBoundary).ToString(
            'yyyy-MM-ddTHH:mm:ss',
            [Globalization.CultureInfo]::InvariantCulture
        ) | Out-Null
    Add-OperationsTaskXmlElement $document $calendar $namespace 'Enabled' 'true' | Out-Null
    $schedule = Add-OperationsTaskXmlElement $document $calendar $namespace 'ScheduleByMonthDayOfWeek'
    $weeks = Add-OperationsTaskXmlElement $document $schedule $namespace 'Weeks'
    Add-OperationsTaskXmlElement $document $weeks $namespace 'Week' '1' | Out-Null
    $days = Add-OperationsTaskXmlElement $document $schedule $namespace 'DaysOfWeek'
    Add-OperationsTaskXmlElement $document $days $namespace 'Sunday' | Out-Null
    $months = Add-OperationsTaskXmlElement $document $schedule $namespace 'Months'
    foreach ($month in @(
        'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December'
    )) {
        Add-OperationsTaskXmlElement $document $months $namespace $month | Out-Null
    }

    $principals = Add-OperationsTaskXmlElement $document $root $namespace 'Principals'
    $principalNode = Add-OperationsTaskXmlElement $document $principals $namespace 'Principal'
    $principalNode.SetAttribute('id', 'Author')
    Add-OperationsTaskXmlElement $document $principalNode $namespace 'UserId' $sid | Out-Null
    Add-OperationsTaskXmlElement $document $principalNode $namespace 'LogonType' 'InteractiveToken' | Out-Null
    Add-OperationsTaskXmlElement $document $principalNode $namespace 'RunLevel' 'HighestAvailable' | Out-Null

    $settings = Add-OperationsTaskXmlElement $document $root $namespace 'Settings'
    $settingValues = [ordered]@{
        MultipleInstancesPolicy = 'IgnoreNew'
        DisallowStartIfOnBatteries = 'true'
        StopIfGoingOnBatteries = 'true'
        AllowHardTerminate = 'true'
        StartWhenAvailable = 'true'
        RunOnlyIfNetworkAvailable = 'false'
        AllowStartOnDemand = 'true'
        Enabled = 'true'
        Hidden = 'false'
        RunOnlyIfIdle = 'false'
        WakeToRun = 'true'
        ExecutionTimeLimit = 'PT72H'
        Priority = '7'
        DisallowStartOnRemoteAppSession = 'false'
        UseUnifiedSchedulingEngine = 'true'
    }
    foreach ($entry in $settingValues.GetEnumerator()) {
        Add-OperationsTaskXmlElement $document $settings $namespace $entry.Key $entry.Value |
            Out-Null
    }
    $idle = Add-OperationsTaskXmlElement $document $settings $namespace 'IdleSettings'
    foreach ($entry in ([ordered]@{
        Duration = 'PT10M'
        WaitTimeout = 'PT1H'
        StopOnIdleEnd = 'true'
        RestartOnIdle = 'false'
    }).GetEnumerator()) {
        Add-OperationsTaskXmlElement $document $idle $namespace $entry.Key $entry.Value |
            Out-Null
    }

    $actions = Add-OperationsTaskXmlElement $document $root $namespace 'Actions'
    $actions.SetAttribute('Context', 'Author')
    $exec = Add-OperationsTaskXmlElement $document $actions $namespace 'Exec'
    Add-OperationsTaskXmlElement $document $exec $namespace 'Command' `
        ([string]$Task.Action.Execute) | Out-Null
    Add-OperationsTaskXmlElement $document $exec $namespace 'Arguments' `
        ([string]$Task.Action.Arguments) | Out-Null

    $xmlText = $document.OuterXml
    Assert-OperationsMonthlyRestoreDrillXml -XmlText $xmlText `
        -ExpectedTask $Task -ExpectedSid $sid
    return $xmlText
}

function Get-OperationsSchtasksXml {
    param([Parameter(Mandatory)][string]$TaskName)

    if ($TaskName -cne 'PythonSelfAgent-MonthlyRestoreDrill') {
        throw 'schtasks XML query is outside the exact monthly task allowlist.'
    }
    $oldPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $rawOutput = @(& schtasks.exe /Query /TN ("\" + $TaskName) /XML 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldPreference
    }
    $nativeErrors = @($rawOutput | Where-Object {
        $_ -is [Management.Automation.ErrorRecord]
    })
    $output = @($rawOutput | Where-Object {
        $_ -isnot [Management.Automation.ErrorRecord]
    })
    if ($exitCode -ne 0 -or $nativeErrors.Count -ne 0 -or $output.Count -eq 0) {
        throw "schtasks XML query failed: $exitCode"
    }
    return ($output -join [Environment]::NewLine)
}

function Register-OperationsTask {
    param(
        [Parameter(Mandatory)]$Task,
        [AllowNull()][string]$MonthlyXml
    )

    if ($Task.Name -ceq 'PythonSelfAgent-MonthlyRestoreDrill') {
        if ([string]::IsNullOrWhiteSpace($MonthlyXml)) {
            throw 'Monthly task XML is required.'
        }
        $result = @(Register-ScheduledTask -TaskName $Task.Name `
            -TaskPath '\' -Xml $MonthlyXml -Force -ErrorAction Stop)
    } else {
        if (-not [string]::IsNullOrEmpty($MonthlyXml)) {
            throw "Monthly XML is not allowed for task: $($Task.Name)"
        }
        $result = @(Register-ScheduledTask -TaskName $Task.Name `
            -TaskPath '\' -InputObject $Task.Definition -Force -ErrorAction Stop)
    }
    if ($result.Count -ne 1 -or $null -eq $result[0] -or
        [string]$result[0].TaskName -cne [string]$Task.Name -or
        [string]$result[0].TaskPath -cne '\') {
        throw "Scheduled task registration returned an invalid result: $($Task.Name)"
    }
    return $result[0]
}

function ConvertTo-OperationsPrincipalSid {
    param([Parameter(Mandatory)][string]$UserId)

    if ($UserId -match '^S-1-') {
        return ([Security.Principal.SecurityIdentifier]$UserId).Value
    }
    return ([Security.Principal.NTAccount]$UserId).Translate(
        [Security.Principal.SecurityIdentifier]
    ).Value
}

function Assert-OperationsPersistedTrigger {
    param(
        [Parameter(Mandatory)]$ExpectedTask,
        [Parameter(Mandatory)]$ActualTrigger
    )

    $expected = $ExpectedTask.Trigger
    if ([bool]$ActualTrigger.Enabled -ne $true) {
        throw "Persisted trigger is invalid: $($ExpectedTask.Name)"
    }
    switch ($ExpectedTask.Name) {
        'PythonSelfAgent-LoginRecovery' {
            if ([string]$ActualTrigger.CimClass.CimClassName -cne
                    'MSFT_TaskLogonTrigger' -or
                [string]$ActualTrigger.UserId -cne [string]$expected.UserId -or
                [string]$ActualTrigger.Delay -cne [string]$expected.Delay) {
                throw "Persisted trigger is invalid: $($ExpectedTask.Name)"
            }
        }
        { $_ -in @('PythonSelfAgent-Health', 'PythonSelfAgent-DailyBackup') } {
            if ([string]$ActualTrigger.CimClass.CimClassName -cne
                    'MSFT_TaskDailyTrigger' -or
                [int]$ActualTrigger.DaysInterval -ne 1 -or
                ([datetime]$ActualTrigger.StartBoundary) -ne
                    ([datetime]$expected.StartBoundary)) {
                throw "Persisted trigger is invalid: $($ExpectedTask.Name)"
            }
            if ($ExpectedTask.Name -ceq 'PythonSelfAgent-Health') {
                if ($null -eq $ActualTrigger.Repetition -or
                    [string]$ActualTrigger.Repetition.Interval -cne 'PT5M' -or
                    [string]$ActualTrigger.Repetition.Duration -cne 'P1D' -or
                    [bool]$ActualTrigger.Repetition.StopAtDurationEnd -ne $true) {
                    throw "Persisted trigger is invalid: $($ExpectedTask.Name)"
                }
            } elseif ($null -ne $ActualTrigger.Repetition -and
                (-not [string]::IsNullOrEmpty(
                    [string]$ActualTrigger.Repetition.Interval
                ) -or -not [string]::IsNullOrEmpty(
                    [string]$ActualTrigger.Repetition.Duration
                ))) {
                throw "Persisted trigger is invalid: $($ExpectedTask.Name)"
            }
        }
        default {
            throw "Persisted trigger validation is unsupported: $($ExpectedTask.Name)"
        }
    }
}

function Assert-OperationsPersistedTask {
    param(
        [Parameter(Mandatory)]$ExpectedTask,
        [Parameter(Mandatory)]$ExpectedPrincipal,
        [switch]$RequireMonthlyXml
    )

    $matches = @(Get-ScheduledTask -TaskName $ExpectedTask.Name -ErrorAction Stop |
        Where-Object { $_.TaskPath -ceq '\' })
    if ($matches.Count -ne 1) {
        throw "Persisted task count is invalid: $($ExpectedTask.Name)"
    }
    $actual = $matches[0]
    $expectedSid = ConvertTo-OperationsPrincipalSid `
        -UserId ([string]$ExpectedPrincipal.UserId)
    $actualSid = ConvertTo-OperationsPrincipalSid `
        -UserId ([string]$actual.Principal.UserId)
    if ([string]$actual.TaskName -cne [string]$ExpectedTask.Name -or
        [string]$actual.TaskPath -cne '\' -or
        $actualSid -cne $expectedSid -or
        [string]$actual.Principal.LogonType -cne 'Interactive' -or
        [string]$actual.Principal.RunLevel -cne 'Highest') {
        throw "Persisted principal is invalid: $($ExpectedTask.Name)"
    }
    if (@($actual.Actions).Count -ne 1 -or
        [string]$actual.Actions[0].Execute -cne [string]$ExpectedTask.Action.Execute -or
        [string]$actual.Actions[0].Arguments -cne [string]$ExpectedTask.Action.Arguments -or
        [regex]::Matches(
            [string]$actual.Actions[0].Arguments,
            '(?i)(?<!\S)-WindowStyle\s+Hidden(?!\S)'
        ).Count -ne 1) {
        throw "Persisted action is invalid: $($ExpectedTask.Name)"
    }
    if ([bool]$actual.Settings.Enabled -ne $true -or
        [bool]$actual.Settings.Hidden -ne $false -or
        [bool]$actual.Settings.StartWhenAvailable -ne $true -or
        [string]$actual.Settings.MultipleInstances -cne 'IgnoreNew' -or
        [bool]$actual.Settings.WakeToRun -ne [bool]$ExpectedTask.Settings.WakeToRun) {
        throw "Persisted settings are invalid: $($ExpectedTask.Name)"
    }
    if (@($actual.Triggers).Count -ne 1) {
        throw "Persisted trigger count is invalid: $($ExpectedTask.Name)"
    }
    if (-not $RequireMonthlyXml) {
        Assert-OperationsPersistedTrigger -ExpectedTask $ExpectedTask `
            -ActualTrigger $actual.Triggers[0]
    }

    $exports = @(Export-ScheduledTask -TaskName $ExpectedTask.Name `
        -TaskPath '\' -ErrorAction Stop)
    if ($exports.Count -ne 1 -or [string]::IsNullOrWhiteSpace([string]$exports[0])) {
        throw "Persisted task XML export is invalid: $($ExpectedTask.Name)"
    }
    [xml]$persistedXml = [string]$exports[0]
    $persistedNs = New-Object Xml.XmlNamespaceManager($persistedXml.NameTable)
    $persistedNs.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
    $enabledNodes = @($persistedXml.SelectNodes(
        '/t:Task/t:Settings/t:Enabled',
        $persistedNs
    ))
    if ($enabledNodes.Count -ne 1 -or $enabledNodes[0].InnerText -cne 'true') {
        throw "Persisted task is disabled: $($ExpectedTask.Name)"
    }
    if ($RequireMonthlyXml) {
        Assert-OperationsMonthlyRestoreDrillXml `
            -XmlText $persistedXml.OuterXml -ExpectedTask $ExpectedTask `
            -ExpectedSid $expectedSid
        $schtasksXml = Get-OperationsSchtasksXml -TaskName $ExpectedTask.Name
        Assert-OperationsMonthlyRestoreDrillXml `
            -XmlText $schtasksXml -ExpectedTask $ExpectedTask `
            -ExpectedSid $expectedSid
    }
    return $actual
}

function Assert-OperationsInstalledState {
    param(
        [Parameter(Mandatory)]$ExpectedTasks,
        [Parameter(Mandatory)]$ExpectedPrincipal,
        [Parameter(Mandatory)][string]$FirewallName,
        [Parameter(Mandatory)][string]$EnvFile
    )

    $expectedNames = @($ExpectedTasks | ForEach-Object Name | Sort-Object)
    $actualTasks = @(Get-ScheduledTask -ErrorAction Stop | Where-Object {
        $_.TaskName -like 'PythonSelfAgent-*'
    })
    if ($actualTasks.Count -ne 4 -or
        @(Compare-Object $expectedNames ($actualTasks.TaskName | Sort-Object)).Count -ne 0) {
        throw 'Persisted operations task set is invalid.'
    }
    foreach ($task in $ExpectedTasks) {
        Assert-OperationsPersistedTask -ExpectedTask $task `
            -ExpectedPrincipal $ExpectedPrincipal `
            -RequireMonthlyXml:($task.Name -ceq 'PythonSelfAgent-MonthlyRestoreDrill') |
            Out-Null
    }

    $rules = @(Get-NetFirewallRule -DisplayName $FirewallName -ErrorAction Stop)
    if ($rules.Count -ne 1) {
        throw 'Persisted firewall rule count is invalid.'
    }
    $ports = @($rules[0] | Get-NetFirewallPortFilter -ErrorAction Stop)
    $addresses = @($rules[0] | Get-NetFirewallAddressFilter -ErrorAction Stop)
    if ($ports.Count -ne 1 -or $addresses.Count -ne 1 -or
        [string]$rules[0].Enabled -cne 'True' -or
        [string]$rules[0].Direction -cne 'Inbound' -or
        [string]$rules[0].Action -cne 'Allow' -or
        [string]$rules[0].Profile -cne 'Private' -or
        [string]$ports[0].Protocol -cne 'TCP' -or
        [string]$ports[0].LocalPort -cne '7860' -or
        [string]$addresses[0].RemoteAddress -cne 'LocalSubnet') {
        throw 'Persisted firewall rule is invalid.'
    }

    $acl = Get-Acl -LiteralPath $EnvFile -ErrorAction Stop
    $expectedSids = @(
        (ConvertTo-OperationsPrincipalSid -UserId ([string]$ExpectedPrincipal.UserId)),
        'S-1-5-18',
        'S-1-5-32-544'
    ) | Sort-Object
    $actualSids = @($acl.Access | ForEach-Object {
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    } | Sort-Object)
    if (-not $acl.AreAccessRulesProtected -or
        @($acl.Access | Where-Object IsInherited).Count -ne 0 -or
        $actualSids.Count -ne 3 -or
        @(Compare-Object $expectedSids $actualSids).Count -ne 0) {
        throw 'Persisted environment ACL is invalid.'
    }
    $currentUserSid = $expectedSids | Where-Object {
        $_ -notin @('S-1-5-18', 'S-1-5-32-544')
    }
    $userRules = @($acl.Access | Where-Object {
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq
            $currentUserSid
    })
    $fullRules = @($acl.Access | Where-Object {
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -in
            @('S-1-5-18', 'S-1-5-32-544')
    })
    $expectedUserRights = [Security.AccessControl.FileSystemRights]::Modify -bor
        [Security.AccessControl.FileSystemRights]::Synchronize
    if ($userRules.Count -ne 1 -or
        [string]$userRules[0].AccessControlType -cne 'Allow' -or
        $userRules[0].FileSystemRights -ne $expectedUserRights -or
        $fullRules.Count -ne 2 -or
        @($fullRules | Where-Object {
            [string]$_.AccessControlType -cne 'Allow' -or
                $_.FileSystemRights -ne
                    [Security.AccessControl.FileSystemRights]::FullControl
        }).Count -ne 0) {
        throw 'Persisted environment ACL rights are invalid.'
    }
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
$firewallName = 'Python Self Agent - Private Intranet 7860'
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Highest
$loginTrigger = New-ScheduledTaskTrigger -AtLogOn
$healthTrigger = New-ScheduledTaskTrigger -Daily -At '00:00'
$healthRepetition = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 1)
$healthTrigger.Repetition = $healthRepetition.Repetition
$backupTrigger = New-ScheduledTaskTrigger -Daily -At '03:00'
$monthlyTrigger = New-OperationsMonthlyRestoreDrillTrigger `
    -StartBoundary (Get-Date -Hour 4 -Minute 0 -Second 0)

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
    $definition = New-ScheduledTask -Action $task.Action -Trigger $task.Trigger `
        -Settings $task.Settings -Principal $principal -ErrorAction Stop
    $task | Add-Member -NotePropertyName Definition -NotePropertyValue $definition
}

$monthlyTask = @($taskDefinitions | Where-Object {
    $_.Name -ceq 'PythonSelfAgent-MonthlyRestoreDrill'
})
if ($monthlyTask.Count -ne 1) {
    throw 'Monthly task definition count is invalid.'
}
$monthlyXml = New-OperationsMonthlyRestoreDrillXml `
    -Task $monthlyTask[0] -Principal $principal

# All preflight checks and in-memory task validation are above this line.
# System changes below are intentional.

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
        -Protocol TCP -LocalPort 7860 -Profile Private -RemoteAddress LocalSubnet `
        -ErrorAction Stop | Out-Null
}

foreach ($task in $taskDefinitions) {
    if ($PSCmdlet.ShouldProcess($task.Name, 'Register scheduled task')) {
        $xmlArgument = if ($task.Name -ceq 'PythonSelfAgent-MonthlyRestoreDrill') {
            $monthlyXml
        } else {
            $null
        }
        Register-OperationsTask -Task $task -MonthlyXml $xmlArgument | Out-Null
        Assert-OperationsPersistedTask -ExpectedTask $task `
            -ExpectedPrincipal $principal `
            -RequireMonthlyXml:($task.Name -ceq 'PythonSelfAgent-MonthlyRestoreDrill') |
            Out-Null
    }
}

Assert-OperationsInstalledState -ExpectedTasks $taskDefinitions `
    -ExpectedPrincipal $principal -FirewallName $firewallName -EnvFile $config.EnvFile
Write-Output 'Windows deployment operations installation completed.'
