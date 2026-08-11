#Requires -Version 5.1
Set-StrictMode -Version Latest

$operationsModule = Join-Path $PSScriptRoot 'Operations.Common.psm1'
Import-Module $operationsModule

$script:DailyPattern = '^assistant-(?<stamp>\d{8}T\d{6}Z)\.tar\.gz$'
$script:WeeklyPattern = '^assistant-week-(?<year>\d{4})-W(?<week>\d{2})\.tar\.gz$'

function Assert-NoReparseAncestors {
    param([Parameter(Mandatory)][string]$Path)

    $current = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    if (-not (Test-Path -LiteralPath $current)) {
        $current = [IO.Path]::GetDirectoryName($current)
    }
    while (-not [string]::IsNullOrWhiteSpace($current)) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Reparse-point paths are not allowed: $current"
            }
        }
        $parent = [IO.Path]::GetDirectoryName($current.TrimEnd('\', '/'))
        if ([string]::IsNullOrWhiteSpace($parent) -or
            $parent.Equals($current, [StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        $current = $parent
    }
}

function Test-RegularNonReparseFile {
    param([Parameter(Mandatory)][string]$LiteralPath)

    if (-not (Test-Path -LiteralPath $LiteralPath -PathType Leaf)) {
        return $false
    }
    $item = Get-Item -LiteralPath $LiteralPath -Force
    return -not [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function Assert-BackupTreeSafe {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$AllowedRoot
    )

    $safePath = Assert-SafePath -Path $Path -AllowedRoot $AllowedRoot -AllowRoot
    Assert-NoReparseAncestors -Path $safePath
    if (-not (Test-Path -LiteralPath $safePath -PathType Container)) {
        throw "Directory was not found: $safePath"
    }
    $rootItem = Get-Item -LiteralPath $safePath -Force
    if ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Reparse points are not allowed: $safePath"
    }
    foreach ($item in @(Get-ChildItem -LiteralPath $safePath -Force -Recurse)) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Reparse points are not allowed: $($item.FullName)"
        }
    }
    return $safePath
}

function Get-BackupNamePattern {
    param([Parameter(Mandatory)][string]$Prefix)

    if ($Prefix -eq 'assistant-') {
        return $script:DailyPattern
    }
    if ($Prefix -eq 'assistant-week-') {
        return $script:WeeklyPattern
    }
    throw "Unsupported backup prefix: $Prefix"
}

function Get-CompleteBackupSets {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Directory,
        [Parameter(Mandatory)][string]$Prefix
    )

    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
        return @()
    }
    $directoryItem = Get-Item -LiteralPath $Directory -Force
    Assert-NoReparseAncestors -Path $directoryItem.FullName
    if ($directoryItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Backup directory must not be a reparse point: $Directory"
    }
    $pattern = Get-BackupNamePattern -Prefix $Prefix
    $sets = @()
    foreach ($archiveItem in @(Get-ChildItem -LiteralPath $directoryItem.FullName -File -Force)) {
        $match = [regex]::Match($archiveItem.Name, $pattern)
        if (-not $match.Success) {
            continue
        }
        $archive = $archiveItem.FullName
        $checksum = "$archive.sha256"
        $metadata = "$archive.meta"
        if (-not (Test-RegularNonReparseFile -LiteralPath $archive) -or
            -not (Test-RegularNonReparseFile -LiteralPath $checksum) -or
            -not (Test-RegularNonReparseFile -LiteralPath $metadata)) {
            continue
        }
        $key = if ($Prefix -eq 'assistant-') {
            $match.Groups['stamp'].Value
        } else {
            '{0}-W{1}' -f $match.Groups['year'].Value, $match.Groups['week'].Value
        }
        $sets += [PSCustomObject]@{
            Key = $key
            Archive = $archive
            Checksum = $checksum
            Metadata = $metadata
        }
    }
    return @($sets | Sort-Object -Property Key -Descending)
}

function Get-RetentionPlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Directory,
        [Parameter(Mandatory)][string]$Prefix,
        [Parameter(Mandatory)][ValidateRange(0, 10000)][int]$Keep
    )

    $sets = @(Get-CompleteBackupSets -Directory $Directory -Prefix $Prefix)
    return [PSCustomObject]@{
        Keep = @($sets | Select-Object -First $Keep)
        Remove = if ($Keep -eq 0) { @($sets) } else { @($sets | Select-Object -Skip $Keep) }
    }
}

function Remove-BackupSet {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]$BackupSet,
        [Parameter(Mandatory)][string]$AllowedRoot
    )

    $root = [IO.Path]::GetFullPath($AllowedRoot).TrimEnd('\', '/')
    foreach ($property in @('Archive', 'Checksum', 'Metadata')) {
        $literalPath = [string]$BackupSet.$property
        $safePath = Assert-SafePath -Path $literalPath -AllowedRoot $root
        $parent = [IO.Path]::GetDirectoryName($safePath).TrimEnd('\', '/')
        if (-not $parent.Equals($root, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Backup set file must be directly below its allowed root: $safePath"
        }
        if (-not (Test-RegularNonReparseFile -LiteralPath $safePath)) {
            throw "Backup set file is missing, non-regular, or a reparse point: $safePath"
        }
        Assert-NoReparseAncestors -Path $safePath
        Assert-SafePath -Path $safePath -AllowedRoot $root | Out-Null
        Remove-Item -LiteralPath $safePath -Force
    }
}

Export-ModuleMember -Function @(
    'Assert-BackupTreeSafe',
    'Get-CompleteBackupSets',
    'Get-RetentionPlan',
    'Remove-BackupSet'
)
