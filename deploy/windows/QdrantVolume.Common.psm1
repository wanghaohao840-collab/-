#Requires -Version 5.1
Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'Operations.Common.psm1') -Force

function Assert-SafeDockerResourceName {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Name)

    if ($Name -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$') {
        throw "Unsafe Docker resource name: $Name"
    }
    return $Name
}

function Get-QdrantVolumeName {
    [CmdletBinding()]
    param([Parameter(Mandatory)]$Config)

    $name = if ($null -ne $Config.PSObject.Properties['QdrantVolumeName']) {
        [string]$Config.QdrantVolumeName
    } else {
        Read-DeployEnvValue -EnvFile $Config.EnvFile -Name 'QDRANT_VOLUME_NAME'
    }
    if ([string]::IsNullOrWhiteSpace($name)) {
        $name = 'zhiyan_qdrant_data'
    }
    return Assert-SafeDockerResourceName -Name $name
}

function Invoke-QdrantVolumeExternal {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [scriptblock]$ExternalInvoker
    )
    if ($null -ne $ExternalInvoker) {
        return @(& $ExternalInvoker $FilePath $ArgumentList)
    }
    return @(Invoke-External -FilePath $FilePath -ArgumentList $ArgumentList)
}

function Get-QdrantHelperImage {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]$Config,
        [scriptblock]$ExternalInvoker
    )

    $arguments = @(
        'compose', '--project-directory', $Config.RepositoryRoot,
        '--file', $Config.ComposeFile, '--env-file', $Config.EnvFile,
        'images', '-q', 'qdrant'
    )
    $images = @(Invoke-QdrantVolumeExternal -FilePath 'docker' -ArgumentList $arguments -ExternalInvoker $ExternalInvoker |
        ForEach-Object { ([string]$_).Trim() } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($images.Count -ne 1 -or $images[0] -notmatch '^(sha256:)?[0-9a-fA-F]{12,64}$') {
        throw 'The current Qdrant helper image could not be resolved uniquely'
    }
    return $images[0]
}

function New-QdrantVolume {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Name,
        [scriptblock]$ExternalInvoker
    )

    $safeName = Assert-SafeDockerResourceName -Name $Name
    $existing = @(Invoke-QdrantVolumeExternal -FilePath 'docker' -ArgumentList @('volume', 'ls', '--quiet', '--filter', "name=^$safeName$") -ExternalInvoker $ExternalInvoker |
        ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ -eq $safeName })
    if ($existing.Count -eq 0) {
        Invoke-QdrantVolumeExternal -FilePath 'docker' -ArgumentList @('volume', 'create', '--label', 'com.zhiyan.role=qdrant-data', $safeName) -ExternalInvoker $ExternalInvoker | Out-Null
        return $true
    }
    if ($existing.Count -ne 1) {
        throw "Docker returned an ambiguous volume match for $safeName"
    }
    return $false
}

function Test-QdrantVolumeEmpty {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$HelperImage,
        [scriptblock]$ExternalInvoker
    )

    $safeName = Assert-SafeDockerResourceName -Name $Name
    $output = @(Invoke-QdrantVolumeExternal -FilePath 'docker' -ArgumentList @(
        'run', '--rm', '--user', '0:0',
        '--mount', "type=volume,source=$safeName,target=/volume,readonly",
        $HelperImage, 'sh', '-ec', 'test -z "$(find /volume -mindepth 1 -maxdepth 1 -print -quit)" && printf empty'
    ) -ExternalInvoker $ExternalInvoker)
    return (@($output | Where-Object { ([string]$_).Trim() -eq 'empty' }).Count -eq 1)
}

function Export-QdrantVolume {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Archive,
        [Parameter(Mandatory)][string]$HelperImage,
        [scriptblock]$ExternalInvoker
    )

    $safeName = Assert-SafeDockerResourceName -Name $Name
    $archivePath = [IO.Path]::GetFullPath($Archive)
    $archiveParent = [IO.Path]::GetDirectoryName($archivePath)
    if (-not (Test-Path -LiteralPath $archiveParent -PathType Container)) {
        throw "Qdrant export directory does not exist: $archiveParent"
    }
    Assert-SafePath -Path $archivePath -AllowedRoot $archiveParent | Out-Null
    if (Test-Path -LiteralPath $archivePath) {
        throw "Refusing to overwrite Qdrant archive: $archivePath"
    }
    $archiveName = [IO.Path]::GetFileName($archivePath)
    Invoke-QdrantVolumeExternal -FilePath 'docker' -ArgumentList @(
        'run', '--rm', '--user', '0:0',
        '--mount', "type=volume,source=$safeName,target=/source,readonly",
        '--mount', "type=bind,source=$archiveParent,target=/backup",
        $HelperImage, 'tar', '-C', '/source', '-czf', "/backup/$archiveName", '.'
    ) -ExternalInvoker $ExternalInvoker | Out-Null
    if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
        throw 'Qdrant export did not create the requested archive'
    }
    return $archivePath
}

function Import-QdrantVolume {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Archive,
        [Parameter(Mandatory)][string]$HelperImage,
        [switch]$RequireEmpty,
        [scriptblock]$ExternalInvoker
    )

    $safeName = Assert-SafeDockerResourceName -Name $Name
    $archivePath = [IO.Path]::GetFullPath($Archive)
    if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
        throw "Qdrant archive was not found: $archivePath"
    }
    $archiveParent = [IO.Path]::GetDirectoryName($archivePath)
    Assert-SafePath -Path $archivePath -AllowedRoot $archiveParent | Out-Null
    if ($RequireEmpty -and -not (Test-QdrantVolumeEmpty -Name $safeName -HelperImage $HelperImage -ExternalInvoker $ExternalInvoker)) {
        throw "Qdrant volume must be empty before import: $safeName"
    }
    $archiveName = [IO.Path]::GetFileName($archivePath)
    Invoke-QdrantVolumeExternal -FilePath 'docker' -ArgumentList @(
        'run', '--rm', '--user', '0:0',
        '--mount', "type=volume,source=$safeName,target=/target",
        '--mount', "type=bind,source=$archiveParent,target=/backup,readonly",
        $HelperImage, 'tar', '-C', '/target', '-xzf', "/backup/$archiveName"
    ) -ExternalInvoker $ExternalInvoker | Out-Null
}

function Import-QdrantDirectory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$SourceDirectory,
        [Parameter(Mandatory)][string]$HelperImage,
        [switch]$RequireEmpty,
        [scriptblock]$ExternalInvoker
    )

    $safeName = Assert-SafeDockerResourceName -Name $Name
    $sourcePath = [IO.Path]::GetFullPath($SourceDirectory)
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) {
        throw "Legacy Qdrant directory was not found: $sourcePath"
    }
    if ($RequireEmpty -and -not (Test-QdrantVolumeEmpty -Name $safeName -HelperImage $HelperImage -ExternalInvoker $ExternalInvoker)) {
        throw "Qdrant volume must be empty before import: $safeName"
    }
    Invoke-QdrantVolumeExternal -FilePath 'docker' -ArgumentList @(
        'run', '--rm', '--user', '0:0',
        '--mount', "type=bind,source=$sourcePath,target=/source,readonly",
        '--mount', "type=volume,source=$safeName,target=/target",
        $HelperImage, 'sh', '-ec', 'tar -C /source -cf - . | tar -C /target -xf -'
    ) -ExternalInvoker $ExternalInvoker | Out-Null
}

function Remove-QdrantVolume {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$RequiredPrefix,
        [scriptblock]$ExternalInvoker
    )

    $safeName = Assert-SafeDockerResourceName -Name $Name
    if ([string]::IsNullOrWhiteSpace($RequiredPrefix) -or -not $safeName.StartsWith($RequiredPrefix, [StringComparison]::Ordinal)) {
        throw 'Qdrant cleanup volume does not match the required operation prefix'
    }
    Invoke-QdrantVolumeExternal -FilePath 'docker' -ArgumentList @('volume', 'rm', $safeName) -ExternalInvoker $ExternalInvoker | Out-Null
}

function Get-QdrantInventory {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$BaseUrl)

    $uri = ([Uri]$BaseUrl).AbsoluteUri.TrimEnd('/')
    $collections = @((Invoke-RestMethod -Method Get -Uri "$uri/collections" -TimeoutSec 10).result.collections)
    $items = @()
    foreach ($collection in @($collections | Sort-Object name)) {
        $name = [string]$collection.name
        $encoded = [Uri]::EscapeDataString($name)
        $detail = Invoke-RestMethod -Method Get -Uri "$uri/collections/$encoded" -TimeoutSec 10
        $items += [PSCustomObject]@{
            Name = $name
            PointsCount = [long]$detail.result.points_count
            Status = [string]$detail.result.status
        }
    }
    return @($items)
}

Export-ModuleMember -Function Assert-SafeDockerResourceName, Get-QdrantVolumeName, Get-QdrantHelperImage, New-QdrantVolume, Test-QdrantVolumeEmpty, Export-QdrantVolume, Import-QdrantVolume, Import-QdrantDirectory, Remove-QdrantVolume, Get-QdrantInventory
