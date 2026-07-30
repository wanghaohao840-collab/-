$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $repositoryRoot "venv\Scripts\python.exe"
$serverVersion = "1.18.2"
$runtimeRoot = Join-Path $repositoryRoot ".runtime\qdrant\v$serverVersion"
$serverRoot = Join-Path $runtimeRoot "server"
$archivePath = Join-Path $runtimeRoot "qdrant-windows-x86_64.zip"
$testRoot = Join-Path $repositoryRoot ".runtime\qdrant-test"
$stdoutPath = Join-Path $testRoot "qdrant.stdout.log"
$stderrPath = Join-Path $testRoot "qdrant.stderr.log"
$downloadUrl = "https://github.com/qdrant/qdrant/releases/download/v$serverVersion/qdrant-x86_64-pc-windows-msvc.zip"
$healthUrl = "http://127.0.0.1:6333/"
$previousTestUrl = $env:QDRANT_TEST_URL
$serverProcess = $null

New-Item -ItemType Directory -Force $runtimeRoot, $testRoot | Out-Null
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw "Project Python was not found at $pythonExecutable."
}

$portOccupied = $false
try {
    Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1 | Out-Null
    $portOccupied = $true
}
catch {
    $portOccupied = $false
}
if ($portOccupied) {
    throw "Refusing to start Qdrant because $healthUrl already responds."
}

if (-not (Test-Path -LiteralPath $archivePath)) {
    Write-Host "Downloading official Qdrant v$serverVersion Windows binary..."
    Invoke-WebRequest -Uri $downloadUrl -OutFile $archivePath
}

if (-not (Test-Path -LiteralPath $serverRoot)) {
    New-Item -ItemType Directory -Force $serverRoot | Out-Null
    Expand-Archive -LiteralPath $archivePath -DestinationPath $serverRoot
}

$qdrantExecutable = Get-ChildItem -LiteralPath $serverRoot -Recurse -Filter "qdrant.exe" |
    Select-Object -First 1
if ($null -eq $qdrantExecutable) {
    throw "qdrant.exe was not found under $serverRoot."
}

try {
    Write-Host "Starting Qdrant v$serverVersion on 127.0.0.1:6333..."
    $serverProcess = Start-Process `
        -FilePath $qdrantExecutable.FullName `
        -WorkingDirectory $testRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru

    $health = $null
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if ($serverProcess.HasExited) {
            $stderrTail = Get-Content -LiteralPath $stderrPath -Tail 30 -ErrorAction SilentlyContinue
            throw "Qdrant exited before becoming healthy.`n$stderrTail"
        }
        try {
            $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
            break
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }

    if ($null -eq $health) {
        throw "Qdrant did not become healthy within 30 seconds."
    }
    if ([string]$health.version -ne $serverVersion) {
        throw "Expected Qdrant v$serverVersion but server reported $($health.version)."
    }

    Write-Host "Qdrant health check passed: version $($health.version)"
    $env:QDRANT_TEST_URL = $healthUrl.TrimEnd("/")
    Push-Location $repositoryRoot
    try {
        & $pythonExecutable -m pytest `
            tests/integration/test_qdrant_document_scope.py `
            -q `
            --basetemp=.runtime/pytest-qdrant-live
        if ($LASTEXITCODE -ne 0) {
            throw "Live Qdrant integration tests failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($null -ne $serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id
        Wait-Process -Id $serverProcess.Id -ErrorAction SilentlyContinue
        Write-Host "Stopped Qdrant process $($serverProcess.Id)."
    }
    $env:QDRANT_TEST_URL = $previousTestUrl
}
