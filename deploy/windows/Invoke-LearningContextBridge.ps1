#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$lock = $null

function Read-BoundedLine {
    param([int]$Limit)
    $line = New-Object Text.StringBuilder
    while ($true) {
        $character = [Console]::In.Read()
        if ($character -eq -1) {
            if ($line.Length -eq 0) { return $null }
            throw 'partial line'
        }
        if ($character -eq 10) { return $line.ToString() }
        if ($character -eq 13 -or $line.Length -ge $Limit) { throw 'invalid line' }
        [void]$line.Append([char]$character)
    }
}

function Reply([string]$Value) {
    [Console]::Out.WriteLine($Value)
    [Console]::Out.Flush()
}

function Test-BridgeContext {
    $result = @(& (Join-Path $PSScriptRoot 'Test-LearningDeploymentContext.ps1') `
        -StateRoot $config.state_root -OperationId $config.operation_id `
        -InheritedOperationLock $lock -DockerContext $config.docker_context `
        -ExpectedEndpoint $config.expected_endpoint -ExpectedDaemonId $config.expected_daemon_id `
        -ApprovedInputs $inputs)
    return ($result.Count -eq 1 -and $result[0] -is [bool] -and $result[0])
}

try {
    $line = Read-BoundedLine 16384
    if ($null -eq $line) { throw 'missing init' }
    $config = $line | ConvertFrom-Json
    $begin = $false
    if (@($config.PSObject.Properties.Name) -ccontains 'begin') {
        if ($config.begin -isnot [bool]) { throw 'begin' }
        $begin = $config.begin
        $config.PSObject.Properties.Remove('begin')
    }
    $keys = @($config.PSObject.Properties.Name | Sort-Object)
    if (($keys -join ',') -cnotin @('approved_inputs,docker_context,expected_daemon_id,expected_endpoint,operation_id,state_root',
        'approved_inputs,backup,docker_context,expected_daemon_id,expected_endpoint,operation_id,state_root',
        'approved_inputs,docker_context,expected_daemon_id,expected_endpoint,operation_id,restore,state_root',
        'approved_inputs,backup,docker_context,expected_daemon_id,expected_endpoint,operation_id,restore,state_root')) { throw 'init' }
    if ([guid]::ParseExact($config.operation_id, 'D').ToString('D') -cne $config.operation_id) { throw 'identity' }
    if (-not [IO.Path]::IsPathRooted($config.state_root) -or
        -not [IO.Directory]::Exists($config.state_root) -or
        (-not $begin -and -not [IO.File]::Exists((Join-Path $config.state_root 'maintenance.json')))) { throw 'state' }
    $inputs = @{}
    foreach ($property in $config.approved_inputs.PSObject.Properties) {
        $inputs[$property.Name] = $property.Value
    }
    Import-Module (Join-Path $PSScriptRoot 'Operations.Common.psm1') -Force -WarningAction SilentlyContinue
    if ($begin) {
        $lock = Enter-OperationsLock -StateRoot $config.state_root
        & (Join-Path $PSScriptRoot 'Set-DeploymentMaintenance.ps1') -Action Begin `
            -StateRoot $config.state_root -OperationId $config.operation_id -InheritedOperationLock $lock
    } else {
        $lock = Enter-OperationsLock -StateRoot $config.state_root -RecoveryId $config.operation_id
    }
    $backupAttempted = $false
    $restoreAttempted = $false
    Reply 'READY'
    while ($true) {
        $command = Read-BoundedLine 72
        if ($null -eq $command -or $command -ceq 'CLOSE') { break }
        if ($command.StartsWith('RESTORE:', [StringComparison]::Ordinal)) {
            try {
                if ($restoreAttempted) { throw 'attempted' }
                $restoreAttempted = $true
                if ($command -cnotmatch '^RESTORE:[0-9a-f]{64}$' -or $keys -cnotcontains 'restore') { throw 'config' }
                $restore = $config.restore
                if ((@($restore.PSObject.Properties.Name | Sort-Object) -join ',') -cne 'backup_root,env_file,repository_root') { throw 'config' }
                foreach ($path in @($restore.repository_root,$restore.env_file,$restore.backup_root)) {
                    if (-not [IO.Path]::IsPathRooted($path)) { throw 'path' }
                }
                if (-not $inputs.ContainsKey($restore.env_file) -or
                    -not $inputs.ContainsKey((Join-Path $restore.repository_root 'compose.yaml'))) { throw 'inputs' }
                if (-not (Test-BridgeContext)) { throw 'context' }
                $receipt = @(& (Join-Path $PSScriptRoot 'Invoke-LearningColdRestore.ps1') `
                    -RepositoryRoot $restore.repository_root -EnvFile $restore.env_file `
                    -BackupRoot $restore.backup_root -StateRoot $config.state_root `
                    -DockerContext $config.docker_context -InheritedOperationLock $lock `
                    -ExpectedEndpoint $config.expected_endpoint -ExpectedDaemonId $config.expected_daemon_id `
                    -ApprovedInputs $inputs -OperationId $config.operation_id `
                    -ExpectedJournalSha256 $command.Substring(8))
                if ($receipt.Count -ne 1 -or -not (Test-BridgeContext)) { throw 'receipt' }
                $json = ConvertTo-Json -InputObject $receipt[0] -Compress -Depth 4
                if ($json.Length -gt 16000) { throw 'receipt' }
                Reply ('RESTORE:' + $json)
            } catch { Reply 'RESTORE_REJECTED' }
            continue
        }
        if ($command -ceq 'BACKUP') {
            try {
                if ($backupAttempted) { throw 'attempted' }
                $backupAttempted = $true
                if ($keys -cnotcontains 'backup') { throw 'config' }
                $backup = $config.backup
                if ((@($backup.PSObject.Properties.Name | Sort-Object) -join ',') -cne 'backup_root,env_file,repository_root') { throw 'config' }
                foreach ($path in @($backup.repository_root,$backup.env_file,$backup.backup_root)) {
                    if (-not [IO.Path]::IsPathRooted($path)) { throw 'path' }
                }
                if (-not $inputs.ContainsKey($backup.env_file) -or
                    -not $inputs.ContainsKey((Join-Path $backup.repository_root 'compose.yaml'))) { throw 'inputs' }
                if (-not (Test-BridgeContext)) { throw 'context' }
                $receipt = @(& (Join-Path $PSScriptRoot 'Invoke-LearningColdBackup.ps1') `
                    -RepositoryRoot $backup.repository_root -EnvFile $backup.env_file `
                    -BackupRoot $backup.backup_root -StateRoot $config.state_root `
                    -DockerContext $config.docker_context -InheritedOperationLock $lock)
                if ($receipt.Count -ne 1 -or -not (Test-BridgeContext)) { throw 'receipt' }
                $json = ConvertTo-Json -InputObject $receipt[0] -Compress -Depth 4
                if ($json.Length -gt 16000) { throw 'receipt' }
                Reply ('BACKUP:' + $json)
            } catch { Reply 'BACKUP_REJECTED' }
            continue
        }
        if ($command -cne 'VERIFY') { throw 'command' }
        $result = @()
        try {
            $result = @(Test-BridgeContext)
        } catch { $result = @($false) }
        if ($result.Count -eq 1 -and $result[0] -is [bool] -and $result[0]) { Reply 'VERIFIED' }
        else { Reply 'REJECTED' }
    }
} catch {
    Reply 'ERROR'
} finally {
    if ($null -ne $lock) { $lock.Dispose() }
}
