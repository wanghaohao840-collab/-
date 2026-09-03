Set-StrictMode -Version Latest
$script:OperationsNotifyCooldownMinutes = 30

function Protect-LogText {
    [CmdletBinding()]
    param([AllowEmptyString()][string]$Text)
    $clean = $Text -replace '(?i)(KEY|TOKEN|PASSWORD|SECRET)=([^\s;]+)', '$1=[REDACTED]'
    $clean = $clean -replace '(?i)(https?://)[^/@\s]+:[^/@\s]+@', '$1[REDACTED]@'
    return $clean
}

function Assert-SafePath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$AllowedRoot,
        [switch]$AllowRoot
    )
    $candidate = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $root = [IO.Path]::GetFullPath($AllowedRoot).TrimEnd('\', '/')
    if ($candidate -eq $root) {
        if ($AllowRoot) { return $candidate }
        throw "Path must be below allowed root: $candidate"
    }
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    if (-not $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path is outside allowed root: $candidate"
    }
    return $candidate
}

function Invoke-External {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [int[]]$AllowExitCodes = @(0)
    )
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 wraps native stderr as ErrorRecord objects.
        # Docker writes ordinary progress to stderr even when it exits zero, so
        # success must be decided exclusively from the native exit code.
        $ErrorActionPreference = 'Continue'
        $output = @(& $FilePath @ArgumentList 2>&1 | ForEach-Object { Protect-LogText ([string]$_) })
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($AllowExitCodes -notcontains $exitCode) {
        throw "$FilePath failed with exit code $exitCode`: $($output -join [Environment]::NewLine)"
    }
    return $output
}

function Resolve-OperationsPath {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$BasePath
    )
    if ([IO.Path]::IsPathRooted($Path)) {
        return [IO.Path]::GetFullPath($Path)
    }
    return [IO.Path]::GetFullPath((Join-Path $BasePath $Path))
}

function Test-PathOverlap {
    param(
        [Parameter(Mandatory)][string]$First,
        [Parameter(Mandatory)][string]$Second
    )
    $firstPath = [IO.Path]::GetFullPath($First).TrimEnd('\', '/')
    $secondPath = [IO.Path]::GetFullPath($Second).TrimEnd('\', '/')
    if ($firstPath.Equals($secondPath, [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    $firstPrefix = $firstPath + [IO.Path]::DirectorySeparatorChar
    $secondPrefix = $secondPath + [IO.Path]::DirectorySeparatorChar
    return $firstPath.StartsWith($secondPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        $secondPath.StartsWith($firstPrefix, [StringComparison]::OrdinalIgnoreCase)
}

function Read-DeployEnvValue {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$EnvFile,
        [Parameter(Mandatory)][string]$Name
    )
    if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
        throw "Deployment environment file was not found: $EnvFile"
    }
    $pattern = '^\s*' + [regex]::Escape($Name) + '\s*=\s*(.*)\s*$'
    foreach ($line in [IO.File]::ReadAllLines($EnvFile)) {
        $match = [regex]::Match($line, $pattern)
        if ($match.Success) {
            return $match.Groups[1].Value.Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function Get-OperationsConfig {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)][string]$EnvFile,
        [string]$StateRoot,
        [string]$BackupRoot
    )
    if (-not (Test-Path -LiteralPath $RepositoryRoot -PathType Container)) {
        throw "Repository root was not found: $RepositoryRoot"
    }
    $repositoryPath = (Resolve-Path -LiteralPath $RepositoryRoot).Path
    $composeFile = Join-Path $repositoryPath 'compose.yaml'
    if (-not (Test-Path -LiteralPath $composeFile -PathType Leaf)) {
        throw "Repository compose.yaml was not found: $repositoryPath"
    }

    $envPath = Resolve-OperationsPath -Path $EnvFile -BasePath $repositoryPath
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        throw "Deployment environment file was not found: $envPath"
    }
    $dataSetting = Read-DeployEnvValue -EnvFile $envPath -Name 'DEPLOY_DATA_ROOT'
    if ([string]::IsNullOrWhiteSpace($dataSetting)) {
        throw 'DEPLOY_DATA_ROOT must be set in the deployment environment file'
    }
    $dataRoot = Resolve-OperationsPath -Path $dataSetting -BasePath $repositoryPath

    $defaultStateRoot = 'deploy-state'
    $defaultBackupRoot = 'D:\python_self_agent_backups'
    $stateSetting = Read-DeployEnvValue -EnvFile $envPath -Name 'DEPLOY_STATE_ROOT'
    $backupSetting = Read-DeployEnvValue -EnvFile $envPath -Name 'DEPLOY_BACKUP_ROOT'
    $cooldownSetting = Read-DeployEnvValue -EnvFile $envPath -Name 'OPERATIONS_NOTIFY_COOLDOWN_MINUTES'

    if ([string]::IsNullOrWhiteSpace($StateRoot)) {
        $StateRoot = if ([string]::IsNullOrWhiteSpace($stateSetting)) {
            $defaultStateRoot
        } else {
            $stateSetting
        }
    }
    if ([string]::IsNullOrWhiteSpace($BackupRoot)) {
        $BackupRoot = if ([string]::IsNullOrWhiteSpace($backupSetting)) {
            $defaultBackupRoot
        } else {
            $backupSetting
        }
    }

    $notificationCooldownMinutes = 30
    if (-not [string]::IsNullOrWhiteSpace($cooldownSetting)) {
        $parsedCooldown = 0
        if (-not [int]::TryParse($cooldownSetting, [ref]$parsedCooldown) -or
            $parsedCooldown -lt 1 -or $parsedCooldown -gt 1440) {
            throw 'OPERATIONS_NOTIFY_COOLDOWN_MINUTES must be an integer from 1 through 1440'
        }
        $notificationCooldownMinutes = $parsedCooldown
    }
    $script:OperationsNotifyCooldownMinutes = $notificationCooldownMinutes

    $qdrantVolumeName = Read-DeployEnvValue -EnvFile $envPath -Name 'QDRANT_VOLUME_NAME'
    if ([string]::IsNullOrWhiteSpace($qdrantVolumeName)) {
        $qdrantVolumeName = 'zhiyan_qdrant_data'
    }
    if ($qdrantVolumeName -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$') {
        throw 'QDRANT_VOLUME_NAME must be a safe Docker volume name'
    }
    $pythonSetting = Read-DeployEnvValue -EnvFile $envPath -Name 'OPERATIONS_PYTHON'
    if ([string]::IsNullOrWhiteSpace($pythonSetting)) {
        $pythonSetting = 'venv\Scripts\python.exe'
    }
    $operationsPython = Resolve-OperationsPath -Path $pythonSetting -BasePath $repositoryPath

    $statePath = Resolve-OperationsPath -Path $StateRoot -BasePath $repositoryPath
    $backupPath = Resolve-OperationsPath -Path $BackupRoot -BasePath $repositoryPath

    if ((Test-PathOverlap $statePath $dataRoot) -or
        (Test-PathOverlap $statePath $backupPath) -or
        (Test-PathOverlap $dataRoot $backupPath)) {
        throw 'Operations state, deployment data, and backup roots must not overlap'
    }

    return [PSCustomObject]@{
        RepositoryRoot = $repositoryPath
        ComposeFile = $composeFile
        EnvFile = $envPath
        StateRoot = $statePath
        BackupRoot = $backupPath
        DataRoot = $dataRoot
        QdrantVolumeName = $qdrantVolumeName
        NotificationCooldownMinutes = $notificationCooldownMinutes
        Python = $operationsPython
    }
}

function Write-OperationsLog {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$StateRoot,
        [Parameter(Mandatory)][string]$Category,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Message,
        [ValidateSet('DEBUG', 'INFO', 'WARN', 'ERROR')][string]$Level = 'INFO'
    )
    New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
    $logFile = Join-Path $StateRoot 'operations.log'
    if ((Test-Path -LiteralPath $logFile) -and (Get-Item -LiteralPath $logFile).Length -ge 10MB) {
        $oldest = "$logFile.7"
        if (Test-Path -LiteralPath $oldest) {
            Remove-Item -LiteralPath $oldest -Force
        }
        for ($index = 6; $index -ge 1; $index--) {
            $source = "$logFile.$index"
            if (Test-Path -LiteralPath $source) {
                Move-Item -LiteralPath $source -Destination "$logFile.$($index + 1)" -Force
            }
        }
        Move-Item -LiteralPath $logFile -Destination "$logFile.1" -Force
    }
    $timestamp = (Get-Date).ToUniversalTime().ToString('o')
    $line = '{0} [{1}] ({2}) {3}' -f $timestamp, $Level, $Category, (Protect-LogText $Message)
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
}

function Protect-OperationsStatusValue {
    param($Value)
    if ($null -eq $Value) {
        return $null
    }
    if ($Value -is [string]) {
        return Protect-LogText $Value
    }
    if ($Value -is [System.Collections.IDictionary]) {
        $clean = [ordered]@{}
        foreach ($key in $Value.Keys) {
            $clean[[string]$key] = Protect-OperationsStatusValue $Value[$key]
        }
        return $clean
    }
    if (($Value -is [System.Collections.IEnumerable]) -and -not ($Value -is [string])) {
        return @($Value | ForEach-Object { Protect-OperationsStatusValue $_ })
    }
    if ($Value -is [PSCustomObject]) {
        $clean = [ordered]@{}
        foreach ($property in $Value.PSObject.Properties) {
            $clean[$property.Name] = Protect-OperationsStatusValue $property.Value
        }
        return [PSCustomObject]$clean
    }
    return $Value
}

function Write-OperationsStatus {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$StateRoot,
        [Parameter(Mandatory)]$Status
    )
    New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
    $payload = Protect-OperationsStatusValue $Status | ConvertTo-Json -Depth 16
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText((Join-Path $StateRoot 'status.json'), $payload, $utf8)
}

function Show-OperationsToast {
    param(
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Message
    )
    [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
    $template = [Windows.UI.Notifications.ToastTemplateType, Windows.UI.Notifications, ContentType=WindowsRuntime]::ToastText02
    $xml = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]::GetTemplateContent($template)
    $textNodes = $xml.GetElementsByTagName('text')
    $textNodes.Item(0).AppendChild($xml.CreateTextNode($Title)) | Out-Null
    $textNodes.Item(1).AppendChild($xml.CreateTextNode($Message)) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime]::new($xml)
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]::CreateToastNotifier('Python Self Agent').Show($toast)
}

function Send-OperationsNotification {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$StateRoot,
        [Parameter(Mandatory)][string]$Category,
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Message,
        [scriptblock]$NotificationAction
    )
    try {
        $notificationRoot = Join-Path $StateRoot 'notifications'
        New-Item -ItemType Directory -Force -Path $notificationRoot | Out-Null
        $safeCategory = $Category -replace '[^a-zA-Z0-9_.-]', '_'
        $stampFile = Join-Path $notificationRoot "$safeCategory.json"
        $now = (Get-Date).ToUniversalTime()
        if (Test-Path -LiteralPath $stampFile) {
            $previous = Get-Content -LiteralPath $stampFile -Raw | ConvertFrom-Json
            if ($previous.last_sent_at) {
                $lastSent = [DateTime]::Parse($previous.last_sent_at).ToUniversalTime()
                if (($now - $lastSent).TotalMinutes -lt $script:OperationsNotifyCooldownMinutes) {
                    return $false
                }
            }
        }
        if ($null -eq $NotificationAction) {
            $NotificationAction = {
                param($NotificationTitle, $NotificationMessage)
                Show-OperationsToast -Title $NotificationTitle -Message $NotificationMessage
            }
        }
        & $NotificationAction (Protect-LogText $Title) (Protect-LogText $Message) | Out-Null

        $stamp = [PSCustomObject]@{ last_sent_at = $now.ToString('o') } | ConvertTo-Json
        [IO.File]::WriteAllText($stampFile, $stamp, (New-Object System.Text.UTF8Encoding($false)))
        return $true
    } catch {
        return $false
    }
}

function Test-DockerReady {
    [CmdletBinding()]
    param()
    try {
        Invoke-External -FilePath 'docker' -ArgumentList @('info') | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Test-ComposeHealth {
    [CmdletBinding()]
    param([Parameter(Mandatory)]$Config)
    $requiredServices = @('app', 'qdrant')
    $services = @()
    try {
        $lines = Invoke-External -FilePath 'docker' -ArgumentList @(
            'compose',
            '--project-directory', $Config.RepositoryRoot,
            '--file', $Config.ComposeFile,
            '--env-file', $Config.EnvFile,
            'ps', '--format', '{{.Service}}|{{.State}}|{{.Health}}',
            'app', 'qdrant'
        )
        foreach ($line in $lines) {
            $parts = $line -split '\|', 3
            if ($parts.Count -lt 3 -or $requiredServices -notcontains $parts[0]) {
                continue
            }
            $services += [PSCustomObject]@{
                Name = $parts[0]
                State = $parts[1]
                Health = $parts[2]
            }
        }
    } catch {
        return [PSCustomObject]@{
            Healthy = $false
            Services = @($services)
            Reason = Protect-LogText $_.Exception.Message
        }
    }

    $unhealthy = @()
    foreach ($name in $requiredServices) {
        $service = @($services | Where-Object { $_.Name -eq $name })
        if ($service.Count -ne 1 -or $service[0].State -ne 'running' -or $service[0].Health -ne 'healthy') {
            $unhealthy += $name
        }
    }
    return [PSCustomObject]@{
        Healthy = ($unhealthy.Count -eq 0)
        Services = @($services)
        Reason = if ($unhealthy.Count -eq 0) { 'app and qdrant are healthy' } else { 'Unhealthy services: ' + ($unhealthy -join ', ') }
    }
}

function Get-FreeTcpPort {
    [CmdletBinding()]
    param()
    $listener = New-Object System.Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
    try {
        $listener.Start()
        return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    } finally {
        $listener.Stop()
    }
}

function Enter-OperationsLock {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$StateRoot)

    $root = [IO.Path]::GetFullPath($StateRoot)
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $lockPath = Join-Path $root 'operations.lock'
    try {
        return [IO.File]::Open(
            $lockPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
    } catch [IO.IOException] {
        throw 'Another deployment operation is already in progress'
    }
}

function Exit-OperationsLock {
    [CmdletBinding()]
    param([Parameter(Mandatory)][IDisposable]$Lock)

    $Lock.Dispose()
}

function Wait-Until {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][scriptblock]$Condition,
        [Parameter(Mandatory)][ValidateRange(1, 3600)][int]$TimeoutSeconds,
        [ValidateRange(1, 300)][int]$IntervalSeconds = 1
    )
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    do {
        if (& $Condition) {
            return $true
        }
        if ($stopwatch.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
            break
        }
        $remainingMilliseconds = [Math]::Ceiling(
            ($TimeoutSeconds - $stopwatch.Elapsed.TotalSeconds) * 1000
        )
        $sleepMilliseconds = [Math]::Min($IntervalSeconds * 1000, $remainingMilliseconds)
        Start-Sleep -Milliseconds ([Math]::Max(1, [int]$sleepMilliseconds))
    } while ($true)
    return $false
}

Export-ModuleMember -Function @(
    'Get-OperationsConfig',
    'Assert-SafePath',
    'Invoke-External',
    'Protect-LogText',
    'Write-OperationsLog',
    'Write-OperationsStatus',
    'Send-OperationsNotification',
    'Test-DockerReady',
    'Test-ComposeHealth',
    'Read-DeployEnvValue',
    'Get-FreeTcpPort',
    'Enter-OperationsLock',
    'Exit-OperationsLock',
    'Wait-Until'
)
