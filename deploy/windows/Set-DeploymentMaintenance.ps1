#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Begin', 'Complete')][string]$Action,
    [Parameter(Mandatory)][string]$StateRoot,
    [Parameter(Mandatory)][string]$OperationId,
    [IO.FileStream]$InheritedOperationLock,
    [scriptblock]$VerifyRecovery
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# An internal coordinator owns the handle and the trusted state-directory ACL.
# This primitive neither authorizes a public request nor starts/stops services.
try {
    $root = [IO.Path]::GetFullPath($StateRoot)
    $expectedLock = Join-Path $root 'operations.lock'
    $lock = $InheritedOperationLock
    if ($null -eq $lock -or -not $lock.CanRead -or -not $lock.CanWrite -or
        $lock.SafeFileHandle.IsClosed -or $lock.SafeFileHandle.IsInvalid -or
        -not [IO.Path]::GetFullPath($lock.Name).Equals($expectedLock, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'invalid lock'
    }
    foreach ($access in @([IO.FileAccess]::Read, [IO.FileAccess]::Write)) {
        $probe = $null
        $exclusive = $false
        try {
            # Permissive probe sharing avoids mistaking our own requested
            # FileShare.None conflict for proof of the parent's exclusivity.
            $probe = [IO.File]::Open($expectedLock, [IO.FileMode]::Open, $access,
                ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
        } catch [IO.IOException] {
            if (($_.Exception.HResult -band 0xffff) -ne 32) { throw }
            $exclusive = $true
        } finally {
            if ($null -ne $probe) { $probe.Dispose() }
        }
        if (-not $exclusive) { throw 'invalid lock' }
    }
    if ([guid]::ParseExact($OperationId, 'D').ToString('D') -cne $OperationId) { throw 'invalid identity' }
} catch {
    throw 'Maintenance requires a live exclusive lock and canonical operation identity'
}

$markerPath = Join-Path $root 'maintenance.json'
if ($Action -eq 'Begin') {
    $stream = $null
    try {
        # CreateNew never replaces a previous or incomplete operation marker.
        $stream = [IO.File]::Open($markerPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $bytes = [Text.Encoding]::UTF8.GetBytes('{"version":1,"operation_id":"' + $OperationId + '"}')
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    } catch {
        # Partial marker stays fail-closed for explicit recovery.
        throw 'Maintenance marker creation failed; existing state was not cleared'
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
    return
}

if ($null -eq $VerifyRecovery) { throw 'Maintenance completion requires a recovery verifier' }
$stream = $null
try {
    $marker = Get-Item -Force -LiteralPath $markerPath -ErrorAction Stop
    if ($marker.PSIsContainer -or ($marker.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $marker.Length -gt 4096) { throw 'invalid marker' }
    $stream = [IO.File]::Open($markerPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $buffer = New-Object byte[] 4097
    $total = 0
    while ($total -lt $buffer.Length) {
        $read = $stream.Read($buffer, $total, $buffer.Length - $total)
        if ($read -eq 0) { break }
        $total += $read
    }
    if ($total -gt 4096) { throw 'invalid marker' }
    $utf8 = New-Object Text.UTF8Encoding($false, $true)
    $content = $utf8.GetString($buffer, 0, $total) | ConvertFrom-Json -ErrorAction Stop
    $keys = @($content.PSObject.Properties.Name | Sort-Object)
    if (($keys -join ',') -ne 'operation_id,version' -or
        $content.version -isnot [int] -or $content.version -ne 1 -or
        $content.operation_id -isnot [string] -or $content.operation_id -cne $OperationId) {
        throw 'invalid marker'
    }
    # Keep the marker locked against writes while the trusted caller verifies.
    $verified = & $VerifyRecovery
    if ($verified -isnot [bool] -or -not $verified) { throw 'recovery not verified' }
    if (-not $InheritedOperationLock.CanRead -or -not $InheritedOperationLock.CanWrite -or
        $InheritedOperationLock.SafeFileHandle.IsClosed) { throw 'parent lock lost' }
} catch {
    throw 'Maintenance completion failed; marker retained'
} finally {
    if ($null -ne $stream) { $stream.Dispose() }
}

# Parent operations lock remains held. No deletion occurs on any failure above.
try {
    Remove-Item -LiteralPath $markerPath -ErrorAction Stop
} catch {
    throw 'Maintenance marker could not be cleared after verification'
}
