#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RepositoryRoot,
    [Parameter(Mandatory)][string]$EnvFile,
    [Parameter(Mandatory)][string]$StateRoot,
    [Parameter(Mandatory)][string]$BackupRoot,
    [Parameter(Mandatory)][string]$DockerContext,
    [Parameter(Mandatory)][string]$ExpectedEndpoint,
    [Parameter(Mandatory)][string]$ExpectedDaemonId,
    [Parameter(Mandatory)][hashtable]$ApprovedInputs,
    [Parameter(Mandatory)][IO.FileStream]$InheritedOperationLock,
    [Parameter(Mandatory)][string]$OperationId,
    [Parameter(Mandatory)][string]$ExpectedJournalSha256
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$held = New-Object 'Collections.Generic.List[IO.FileStream]'

function Open-HeldFile([string]$Path) {
    if (-not [IO.Path]::IsPathRooted($Path) -or $Path -match '(^|[\\/])\.\.([\\/]|$)') { throw 'path' }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) { throw 'file' }
    $ancestor = $item
    while ($null -ne $ancestor) {
        if (($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'reparse' }
        if ($ancestor -is [IO.FileInfo]) { $ancestor = $ancestor.Directory }
        else { $ancestor = $ancestor.Parent }
    }
    $stream = [IO.File]::Open($item.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    [void]$held.Add($stream)
    return $stream
}

function Hash-Stream($Stream) {
    $Stream.Position = 0
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash($Stream)).Replace('-','').ToLowerInvariant() }
    finally { $hasher.Dispose(); $Stream.Position = 0 }
}

function Assert-Context {
    $answer = @(& (Join-Path $PSScriptRoot 'Test-LearningDeploymentContext.ps1') `
        -StateRoot $StateRoot -OperationId $OperationId -InheritedOperationLock $InheritedOperationLock `
        -DockerContext $DockerContext -ExpectedEndpoint $ExpectedEndpoint `
        -ExpectedDaemonId $ExpectedDaemonId -ApprovedInputs $ApprovedInputs)
    if ($answer.Count -ne 1 -or $answer[0] -isnot [bool] -or -not $answer[0]) { throw 'context' }
}

function Get-LatestRecord {
    $entries = @(Get-ChildItem -LiteralPath $journalDirectory -Force | Sort-Object Name)
    if ($entries.Count -lt 1 -or $entries.Count -gt 16) { throw 'journal' }
    for ($i=0; $i -lt $entries.Count; $i++) {
        if ($entries[$i].Name -cne ('{0:D4}.json' -f $i) -or $entries[$i].PSIsContainer) { throw 'journal' }
    }
    return $entries[-1].FullName
}

try {
    if ([guid]::ParseExact($OperationId,'D').ToString('D') -cne $OperationId -or
        $ExpectedJournalSha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $DockerContext -cnotmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$') { throw 'identity' }
    if (-not $ApprovedInputs.ContainsKey($EnvFile) -or
        -not $ApprovedInputs.ContainsKey((Join-Path $RepositoryRoot 'compose.yaml'))) { throw 'inputs' }
    Assert-Context
    foreach ($path in $ApprovedInputs.Keys) {
        $inputStream = Open-HeldFile $path
        if ($inputStream.Length -gt 16777216 -or
            (Hash-Stream $inputStream) -cne $ApprovedInputs[$path]) { throw 'input changed' }
    }
    $journalDirectory = Join-Path (Join-Path $StateRoot 'learning-maintenance') $OperationId
    $recordPath = Get-LatestRecord
    $recordStream = Open-HeldFile $recordPath
    if ($recordStream.Length -gt 16384 -or (Hash-Stream $recordStream) -cne $ExpectedJournalSha256) { throw 'journal' }
    $reader = New-Object IO.StreamReader($recordStream,(New-Object Text.UTF8Encoding($false,$true)),$false,16384,$true)
    try { $record = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
    if ($record.operation_id -cne $OperationId -or $record.phase -cne 'recovering' -or
        $null -eq $record.backup) { throw 'phase' }
    $archive = $record.backup.app_archive
    $qdrant = $record.backup.qdrant_archive
    if ($qdrant -cne ($archive + '.qdrant-volume.tar.gz')) { throw 'archive pair' }
    $prefix = [IO.Path]::GetFullPath($BackupRoot).TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
    foreach ($path in @($archive,$qdrant)) {
        if (-not [IO.Path]::GetFullPath($path).StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) { throw 'scope' }
    }
    $appStream = Open-HeldFile $archive
    $qdrantStream = Open-HeldFile $qdrant
    if ((Hash-Stream $appStream) -cne $record.backup.app_sha256 -or
        (Hash-Stream $qdrantStream) -cne $record.backup.qdrant_sha256) { throw 'archive hash' }
    foreach ($path in @("$archive.sha256","$archive.meta","$qdrant.sha256")) {
        $sidecar = Open-HeldFile $path
        if ($sidecar.Length -gt 65536) { throw 'sidecar size' }
    }
    Assert-Context
    if ((Get-LatestRecord) -cne $recordPath) { throw 'changed phase' }
    Import-Module (Join-Path $PSScriptRoot 'Operations.Common.psm1') -Force -WarningAction SilentlyContinue
    $contextName = $DockerContext
    $runner = {
        param($FilePath,$ArgumentList)
        if ($FilePath -ceq 'docker') {
            return Invoke-External -FilePath 'docker' -ArgumentList (@('--context',$contextName)+$ArgumentList)
        }
        if ($FilePath -ceq 'tar.exe') { return Invoke-External -FilePath 'tar.exe' -ArgumentList $ArgumentList }
        throw 'unexpected executable'
    }.GetNewClosure()
    $result = @(& (Join-Path $PSScriptRoot 'Restore-Deployment.ps1') -Archive $archive `
        -RepositoryRoot $RepositoryRoot -EnvFile $EnvFile -StateRoot $StateRoot -BackupRoot $BackupRoot `
        -InheritedOperationLock $InheritedOperationLock -KeepStopped -ExternalInvoker $runner)
    if ($result.Count -ne 1 -or $result[0].KeptStopped -isnot [bool] -or
        -not $result[0].KeptStopped -or $result[0].RestoredArchive -cne $archive) { throw 'receipt' }
    Assert-Context
    if ((Get-LatestRecord) -cne $recordPath) { throw 'changed phase' }
    return $result[0]
} catch {
    throw 'Bound cold restore failed; maintenance retained'
} finally {
    foreach ($stream in $held) { $stream.Dispose() }
}
