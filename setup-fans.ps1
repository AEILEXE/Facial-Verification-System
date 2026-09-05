# ============================================================
# setup-fans.ps1  --  FANS-C First-Time and Re-Setup Script
#
# Usage:
#   powershell.exe -ExecutionPolicy Bypass -File setup-fans.ps1
#
# VERIFICATION CHECKLIST:
# 1. python manage.py check -> 0 issues
# 2. python manage.py runserver -> login at http://127.0.0.1:8000
# 3. scripts\start\start-fans.bat -> login at http://127.0.0.1:8000
# 4. https://fans-barangay.local -> login (requires Caddy + mkcert cert)
# ============================================================

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# ============================================================
# Step 1 -- Verify or recreate .venv with Python 3.11
# ============================================================
$venvPy = ".\.venv\Scripts\python.exe"

if (Test-Path $venvPy) {
    $ver = & $venvPy --version 2>&1
    if ($ver -notmatch "3\.11") {
        Write-Host "[INFO] .venv has $ver -- need 3.11. Removing..."
        Remove-Item -Recurse -Force .venv
    }
}

if (-not (Test-Path $venvPy)) {
    Write-Host "[INFO] Creating .venv with Python 3.11..."
    py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] py -3.11 not found. Install Python 3.11 first."
        exit 1
    }
    & .\.venv\Scripts\pip.exe install -r requirements.txt --no-warn-script-location
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] pip install failed."
        exit 1
    }
}

Write-Host "[PASS] .venv is Python 3.11"

# ============================================================
# Step 2 -- django check
# ============================================================
$ErrorActionPreference = 'Continue'
$r = & .\.venv\Scripts\python.exe manage.py check 2>&1
$ErrorActionPreference = 'Stop'
$checkFailed = ($LASTEXITCODE -ne 0) -or ("$r" -match "SystemCheckError")

if ($checkFailed) {
    Write-Host "[FAIL] manage.py check`n$r"
    exit 1
}
Write-Host "[PASS] manage.py check"

# ============================================================
# Step 3 -- collectstatic
# ============================================================
$ErrorActionPreference = 'Continue'
& .\.venv\Scripts\python.exe manage.py collectstatic --noinput --clear 2>&1
$ErrorActionPreference = 'Stop'
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] collectstatic failed."
    exit 1
}
if (-not (Test-Path "staticfiles\staticfiles.json")) {
    Write-Host "[FAIL] staticfiles.json missing after collectstatic."
    exit 1
}
Write-Host "[PASS] collectstatic"

# ============================================================
# Step 4 -- migrate
# ============================================================
$ErrorActionPreference = 'Continue'
& .\.venv\Scripts\python.exe manage.py migrate --run-syncdb 2>&1
$ErrorActionPreference = 'Stop'
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] migrate failed."
    exit 1
}
Write-Host "[PASS] migrate"

# ============================================================
# Step 5 -- mkcert (skip if already done)
# ============================================================
$certDir = "certs"
if (-not (Test-Path "$certDir\fans-barangay.local.pem")) {
    Write-Host "[INFO] Running mkcert..."
    if (-not (Get-Command mkcert -ErrorAction SilentlyContinue)) {
        Write-Host "[WARN] mkcert not found, skipping HTTPS cert. Install manually."
    } else {
        if (-not (Test-Path $certDir)) { New-Item -ItemType Directory $certDir | Out-Null }
        mkcert -cert-file "$certDir\fans-barangay.local.pem" -key-file "$certDir\fans-barangay.local-key.pem" fans-barangay.local localhost 127.0.0.1
        Write-Host "[PASS] mkcert cert created"
    }
} else {
    Write-Host "[PASS] mkcert cert already exists"
}

# ============================================================
# Step 6 -- verify start-fans.bat has /D flag
# ============================================================
$bat = Get-Content "scripts\start\start-fans.bat" -Raw
if ($bat -notmatch '/D\s+"%PROJECT_ROOT%"') {
    Write-Host "[WARN] start-fans.bat may be missing /D working directory fix. Check manually."
} else {
    Write-Host "[PASS] start-fans.bat working directory OK"
}

Write-Host ""
Write-Host "FANS-C setup complete. Launch: scripts\start\start-fans.bat"
