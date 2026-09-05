#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C Start Script. Run this every time you want to start the server.

.DESCRIPTION
    - Activates the .venv virtual environment
    - Validates that .env exists and EMBEDDING_ENCRYPTION_KEY is set
    - Applies any pending database migrations
    - Triggers a background sync of unsynced records (if SYNC_API_URL is configured)
    - Starts the Django development server

.PARAMETER Port
    Port number for the Django server (default: 8000).

.PARAMETER Host
    Host/IP for the server (default: 127.0.0.1).

.EXAMPLE
    .\run.ps1
    .\run.ps1 -Port 8080
    .\run.ps1 -Host 0.0.0.0 -Port 8000
#>

param(
    [int]$Port = 8000,
    [string]$ServerHost = '127.0.0.1'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot
Set-Location $projectRoot

$venvActivate = Join-Path $projectRoot '.venv\Scripts\Activate.ps1'
$venvPython   = Join-Path $projectRoot '.venv\Scripts\python.exe'
$envFile      = Join-Path $projectRoot '.env'

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "   FANS-C  |  FaceNet Facial Verification System  |  Starting... " -ForegroundColor Cyan
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host ""

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

# 1. .venv present?
if (-not (Test-Path $venvActivate)) {
    Write-Host "  [FAIL] .venv not found at $venvActivate" -ForegroundColor Red
    Write-Host "         Run .\setup-secure-server.ps1 first to set up the project." -ForegroundColor Yellow
    exit 1
}

# 2. .env present?
if (-not (Test-Path $envFile)) {
    Write-Host "  [FAIL] .env not found." -ForegroundColor Red
    Write-Host "         Copy .env.example to .env and fill in your values." -ForegroundColor Yellow
    Write-Host "         Then run .\setup-secure-server.ps1 to complete setup." -ForegroundColor Yellow
    exit 1
}

# 3. EMBEDDING_ENCRYPTION_KEY set?
$envRaw = Get-Content $envFile -Raw -Encoding UTF8
if ($envRaw -match '(?m)^EMBEDDING_ENCRYPTION_KEY\s*=\s*$') {
    Write-Host "  [FAIL] EMBEDDING_ENCRYPTION_KEY is not set in .env" -ForegroundColor Red
    Write-Host "         Run .\setup-secure-server.ps1 to auto-generate the key, or run:" -ForegroundColor Yellow
    Write-Host "           .\.venv\Scripts\python.exe manage.py generate_key" -ForegroundColor Cyan
    Write-Host "         Then paste the key into .env as EMBEDDING_ENCRYPTION_KEY=<key>" -ForegroundColor Yellow
    exit 1
}

Write-Host "  [OK]  Environment checks passed" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Activate virtual environment
# ---------------------------------------------------------------------------
Write-Host "  [..] Activating .venv ..." -ForegroundColor DarkGray
& $venvActivate
Write-Host "  [OK]  .venv activated" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Apply pending migrations
# ---------------------------------------------------------------------------
Write-Host "  [..] Checking for pending migrations ..." -ForegroundColor DarkGray
& $venvPython manage.py migrate --noinput 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [WARN] migrate exited with code $LASTEXITCODE — check your database." -ForegroundColor Yellow
} else {
    Write-Host "  [OK]  Migrations up to date" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Background sync (non-blocking; fires and forgets)
# ---------------------------------------------------------------------------
if ($envRaw -match '(?m)^SYNC_API_URL\s*=\s*https?://') {
    Write-Host "  [..] Triggering offline sync in background ..." -ForegroundColor DarkGray
    Start-Process $venvPython `
        -ArgumentList "manage.py sync_beneficiaries --quiet" `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden
    Write-Host "  [OK]  Sync started (runs in background)" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Start server
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  Starting Django server on http://${ServerHost}:${Port}/" -ForegroundColor Green
Write-Host "  Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

& $venvPython manage.py runserver "${ServerHost}:${Port}"
