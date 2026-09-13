#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RepositoryRoot,
    [Parameter(Mandatory)][string]$EnvFile,
    [Parameter(Mandatory)][string]$StateRoot,
    [Parameter(Mandatory)][string]$BackupRoot,
    [Parameter(Mandatory)][string]$DockerContext,
    [Parameter(Mandatory)][IO.FileStream]$InheritedOperationLock
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($DockerContext -cnotmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$') { throw 'Invalid backup context' }
Import-Module (Join-Path $PSScriptRoot 'Operations.Common.psm1') -Force -WarningAction SilentlyContinue
$contextName = $DockerContext
$runner = {
    param($FilePath, $ArgumentList)
    if ($FilePath -ceq 'docker') {
        return Invoke-External -FilePath 'docker' -ArgumentList (@('--context', $contextName) + $ArgumentList)
    }
    if ($FilePath -ceq 'tar.exe') { return Invoke-External -FilePath 'tar.exe' -ArgumentList $ArgumentList }
    throw 'Unexpected backup executable'
}.GetNewClosure()
$result = @(& (Join-Path $PSScriptRoot 'Backup-Deployment.ps1') `
    -RepositoryRoot $RepositoryRoot -EnvFile $EnvFile -StateRoot $StateRoot -BackupRoot $BackupRoot `
    -InheritedOperationLock $InheritedOperationLock -KeepStopped -ExternalInvoker $runner)
if ($result.Count -ne 1 -or $result[0].KeptStopped -isnot [bool] -or -not $result[0].KeptStopped) {
    throw 'Incomplete cold backup'
}
$receipt = $result[0]
foreach ($field in @('Archive','Checksum','Metadata','QdrantArchive','QdrantChecksum')) {
    $path = $receipt.$field
    if ($path -isnot [string] -or [string]::IsNullOrEmpty($path) -or -not [IO.File]::Exists($path)) {
        throw 'Incomplete cold backup'
    }
}
return $receipt
