[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$RepositoryRoot,
    [Parameter(Mandatory=$true)][string]$ReleaseDirectory,
    [Parameter(Mandatory=$true)][ValidatePattern('^sha256:[a-f0-9]{64}$')][string]$BaselineImage,
    [Parameter(Mandatory=$true)][ValidatePattern('^sha256:[a-f0-9]{64}$')][string]$CandidateImage,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{7,40}$')][string]$Revision,
    [Parameter(Mandatory=$true)][string]$BackupRoot
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$releaseDir = (Resolve-Path -LiteralPath $ReleaseDirectory).Path
Import-Module (Join-Path $repo 'deploy\windows\Operations.Common.psm1') -Force
function Invoke-Docker([string[]]$Arguments) {
    $output = & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed: $($Arguments[0])" }
    return $output
}
$baseline = $BaselineImage
$candidate = (Invoke-Docker @('image', 'inspect', $CandidateImage, '--format', '{{.Id}}')).Trim()
$compose = @('compose', '--project-directory', $repo, '--file', (Join-Path $repo 'compose.release.yaml'), '--env-file', (Join-Path $repo 'deploy\.env'))
function Start-And-Verify {
    Invoke-Docker ($compose + @('start', 'qdrant')) | Out-Null
    Invoke-Docker ($compose + @('up', '-d', '--no-deps', '--no-build', '--pull', 'never', 'app')) | Out-Null
    $deadline = (Get-Date).AddSeconds(180)
    do {
        $appHealth = Invoke-Docker @('inspect', 'python_self_agent-app-1', '--format', '{{.State.Health.Status}}')
        $qdrantHealth = Invoke-Docker @('inspect', 'python_self_agent-qdrant-1', '--format', '{{.State.Health.Status}}')
        if ($appHealth -eq 'healthy' -and $qdrantHealth -eq 'healthy') { return }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    throw 'Deployment health timeout'
}
$lock = Enter-OperationsLock -StateRoot (Join-Path $repo 'deploy-state')
$started = $false
try {
    if ((Invoke-Docker @('context', 'show')).Trim() -ne 'desktop-linux') { throw 'Unexpected Docker context' }
    if ((Invoke-Docker @('inspect', 'python_self_agent-app-1', '--format', '{{.Image}}')).Trim() -ne $baseline) { throw 'Production image changed' }
    if ((Invoke-Docker @('inspect', 'python_self_agent-app-1', '--format', '{{.State.Health.Status}}')).Trim() -ne 'healthy') { throw 'Baseline unhealthy' }
    $releasePath = Join-Path $repo 'compose.release.yaml'
    $rollbackPath = Join-Path $releaseDir 'rollback.yaml'
    if ((Get-FileHash $releasePath).Hash -ne (Get-FileHash $rollbackPath).Hash) { throw 'Release configuration changed' }
    $candidateConfig = Get-Content -LiteralPath $releasePath -Raw | ConvertFrom-Json
    $candidateConfig.services.app.image = $candidate
    $candidatePath = Join-Path $releaseDir 'candidate.yaml'
    [IO.File]::WriteAllText($candidatePath, ($candidateConfig | ConvertTo-Json -Depth 30))
    Invoke-Docker @('compose', '--project-directory', $repo, '--file', $candidatePath, '--env-file', (Join-Path $repo 'deploy\.env'), 'config', '--quiet') | Out-Null
    $started = $true
    Write-Output 'STAGE cold-backup'
    $backup = & (Join-Path $repo 'deploy\windows\Backup-Deployment.ps1') -RepositoryRoot $repo -BackupRoot $BackupRoot -InheritedOperationLock $lock -KeepStopped
    if (-not $backup.KeptStopped -or -not $backup.QdrantArchive) { throw 'Paired cold backup incomplete' }
    $backup | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $releaseDir 'backup.json') -Encoding utf8
    Write-Output 'STAGE switch-image'
    Copy-Item -LiteralPath $candidatePath -Destination $releasePath -Force
    Start-And-Verify
    if ((Invoke-Docker @('inspect', 'python_self_agent-app-1', '--format', '{{.Image}}')).Trim() -ne $candidate) { throw 'Wrong image running' }
    $expected = (Get-FileHash (Join-Path $releaseDir 'dist\index.html') -Algorithm SHA256).Hash.ToLowerInvariant()
    $actual = (Invoke-Docker @('exec', 'python_self_agent-app-1', 'sha256sum', '/app/web/dist/index.html')).Split(' ')[0]
    if ($actual -ne $expected) { throw 'Frontend index hash mismatch' }
    [ordered]@{ status='deployed'; candidate=$candidate; baseline=$baseline; revision=$Revision; index_sha256=$actual; backup=$backup.Archive; qdrant_backup=$backup.QdrantArchive; completed_at=(Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseDir 'result.json') -Encoding utf8
    Write-Output 'DEPLOYED: app and qdrant healthy, frontend hash verified'
} catch {
    $failure = $_
    if ($started) {
        Write-Output 'STAGE rollback-image'
        Copy-Item -LiteralPath (Join-Path $releaseDir 'rollback.yaml') -Destination (Join-Path $repo 'compose.release.yaml') -Force
        Start-And-Verify
    }
    throw $failure
} finally {
    Exit-OperationsLock -Lock $lock
}
