#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$StateRoot,
    [Parameter(Mandatory)][string]$OperationId,
    [IO.FileStream]$InheritedOperationLock,
    [Parameter(Mandatory)][string]$DockerContext,
    [Parameter(Mandatory)][string]$ExpectedEndpoint,
    [Parameter(Mandatory)][string]$ExpectedDaemonId,
    [Parameter(Mandatory)][hashtable]$ApprovedInputs,
    [scriptblock]$ExternalInvoker
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$streams = New-Object 'Collections.Generic.List[IO.FileStream]'

function Assert-ParentLock {
    $parent = $InheritedOperationLock
    if ($null -eq $parent -or -not $parent.CanRead -or -not $parent.CanWrite -or
        $parent.SafeFileHandle.IsClosed -or $parent.SafeFileHandle.IsInvalid -or
        -not [IO.Path]::GetFullPath($parent.Name).Equals(
            (Join-Path ([IO.Path]::GetFullPath($StateRoot)) 'operations.lock'),
            [StringComparison]::OrdinalIgnoreCase)) { throw 'lock' }
    foreach ($access in @([IO.FileAccess]::Read, [IO.FileAccess]::Write)) {
        $probe = $null
        $denied = $false
        try {
            $probe = [IO.File]::Open($parent.Name, [IO.FileMode]::Open, $access,
                ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
        } catch [IO.IOException] {
            if (($_.Exception.HResult -band 0xffff) -eq 32) { $denied = $true }
        } finally {
            if ($null -ne $probe) { $probe.Dispose() }
        }
        if (-not $denied) { throw 'lock' }
    }
}

function Open-ApprovedFile {
    param([string]$Path, [long]$Limit)
    if (-not [IO.Path]::IsPathRooted($Path)) { throw 'path' }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or $item.Length -gt $Limit) { throw 'file' }
    $ancestor = $item
    while ($null -ne $ancestor) {
        if (($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'reparse' }
        if ($ancestor -is [IO.FileInfo]) { $ancestor = $ancestor.Directory }
        else { $ancestor = $ancestor.Parent }
    }
    $stream = [IO.File]::Open($item.FullName, [IO.FileMode]::Open,
        [IO.FileAccess]::Read, [IO.FileShare]::Read)
    [void]$streams.Add($stream)
    if ($stream.Length -gt $Limit) { throw 'size' }
    return $stream
}

function Invoke-DockerObservation {
    param([string[]]$Arguments)
    if ($null -ne $ExternalInvoker) {
        $output = @(& $ExternalInvoker 'docker' $Arguments)
        if ($output.Count -ne 1 -or $output[0] -isnot [string]) { throw 'output' }
        $text = $output[0]
    } else {
        # All arguments are internally built or validated single tokens; quote
        # the fixed Go templates. Never use a shell or surface raw diagnostics.
        $start = New-Object Diagnostics.ProcessStartInfo
        $start.FileName = 'docker'
        $start.Arguments = (($Arguments | ForEach-Object { '"' + $_ + '"' }) -join ' ')
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $process = New-Object Diagnostics.Process
        $process.StartInfo = $start
        try {
            [void]$process.Start()
            $stdout = $process.StandardOutput.ReadToEndAsync()
            $stderr = $process.StandardError.ReadToEndAsync()
            if (-not $process.WaitForExit(20000)) {
                $process.Kill()
                throw 'timeout'
            }
            if ($process.ExitCode -ne 0) { throw 'docker' }
            $text = $stdout.GetAwaiter().GetResult()
            [void]$stderr.GetAwaiter().GetResult()
        } finally { $process.Dispose() }
    }
    if ($text.Length -gt 65536) { throw 'output' }
    return $text.TrimEnd("`r", "`n")
}

try {
    if ([guid]::ParseExact($OperationId, 'D').ToString('D') -cne $OperationId) { throw 'id' }
    if ($DockerContext -cnotmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$') { throw 'context' }
    if ($ExpectedEndpoint -cnotin @('npipe:////./pipe/dockerDesktopLinuxEngine',
                                  'npipe:////./pipe/docker_engine')) { throw 'endpoint' }
    if ($ExpectedDaemonId -cnotmatch '^[a-zA-Z0-9][a-zA-Z0-9:_.-]{0,127}$') { throw 'daemon' }
    if ($ApprovedInputs.Count -lt 1 -or $ApprovedInputs.Count -gt 16) { throw 'inputs' }
    Assert-ParentLock
    $marker = Open-ApprovedFile -Path (Join-Path $StateRoot 'maintenance.json') -Limit 4096
    $reader = New-Object IO.StreamReader($marker, (New-Object Text.UTF8Encoding($false,$true)), $false, 4096, $true)
    try { $content = $reader.ReadToEnd() } finally { $reader.Dispose() }
    if ($content -cne ('{"version":1,"operation_id":"' + $OperationId + '"}')) { throw 'marker' }
    foreach ($path in $ApprovedInputs.Keys) {
        $expected = $ApprovedInputs[$path]
        if ($path -isnot [string] -or $expected -isnot [string] -or
            $expected -cnotmatch '^[0-9a-f]{64}$') { throw 'input' }
        $stream = Open-ApprovedFile -Path $path -Limit 16777216
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { $digest = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-','').ToLowerInvariant() }
        finally { $hasher.Dispose() }
        if ($digest -cne $expected) { throw 'hash' }
    }
    $endpointArgs = @('context','inspect',$DockerContext,'--format','{{.Endpoints.docker.Host}}')
    if ((Invoke-DockerObservation $endpointArgs) -cne $ExpectedEndpoint) { throw 'endpoint' }
    if ((Invoke-DockerObservation @('--context',$DockerContext,'info','--format','{{.ID}}|{{.OSType}}')) -cne
        ($ExpectedDaemonId + '|linux')) { throw 'daemon' }
    if ((Invoke-DockerObservation $endpointArgs) -cne $ExpectedEndpoint) { throw 'endpoint' }
    Assert-ParentLock
    return $true
} catch {
    return $false
} finally {
    foreach ($stream in $streams) { $stream.Dispose() }
}
