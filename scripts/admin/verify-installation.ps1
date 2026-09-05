#Requires -Version 5.1
<#
.SYNOPSIS
    Verify a FANSC manual installation is complete and correctly configured.

.DESCRIPTION
    Runs a comprehensive series of read-only checks against the current installation
    and prints a clear PASS / FAIL / WARN summary for each item.

    This script does NOT make any changes to the system.

    Checks performed:
      1.  Python 3.11 available (py -3.11)
      2.  .venv exists with correct Python version
      3.  Required Python packages import successfully
      4.  Django settings load without errors
      5.  Pending migrations status
      6.  staticfiles/ directory exists (or collectstatic can be suggested)
      7.  Caddy executable exists
      8.  mkcert executable exists
      9.  TLS certificate files present
      10. .env file exists and required keys are set (not placeholder values)
      11. DEBUG mode (should be False for production)
      12. ALLOWED_HOSTS is not the bare minimum (should include LAN hostname/IP)
      13. CSRF_TRUSTED_ORIGINS is set (required for HTTPS form POSTs via Caddy)
      14. Database file / path is accessible
      15. media/ folder exists and is writable
      16. logs/ folder exists and is writable
      -- Summary: overall PASS / FAIL --

.NOTES
    Run from the project root. Does not require Administrator.
    For post-setup verification use the output of scripts\setup\setup-complete.ps1.

.EXAMPLE
    .\scripts\admin\verify-installation.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $projectRoot

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$envFile    = Join-Path $projectRoot '.env'
$caddyExe   = Join-Path $projectRoot 'tools\caddy.exe'
$mkcertExe  = Join-Path $projectRoot 'tools\mkcert\mkcert.exe'
$certFile   = Join-Path $projectRoot 'fans-cert.pem'
$certKey    = Join-Path $projectRoot 'fans-cert-key.pem'
$mediaDir   = Join-Path $projectRoot 'media'
$logsDir    = Join-Path $projectRoot 'logs'
$staticDir  = Join-Path $projectRoot 'staticfiles'

$pass  = 0
$fail  = 0
$warn  = 0
$items = [System.Collections.Generic.List[hashtable]]::new()

function Add-Result {
    param([string]$Label, [string]$Status, [string]$Message)
    $items.Add(@{ Label = $Label; Status = $Status; Message = $Message })
    switch ($Status) {
        'PASS' { $script:pass++ }
        'FAIL' { $script:fail++ }
        'WARN' { $script:warn++ }
    }
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   FANS-C  |  Installation Verification Script' -ForegroundColor Cyan
Write-Host "   Project: $projectRoot" -ForegroundColor DarkGray
Write-Host "   Date   : $(Get-Date -Format 'yyyy-MM-dd HH:mm')" -ForegroundColor DarkGray
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''

# ---------------------------------------------------------------------------
# 1. Python 3.11 available
# ---------------------------------------------------------------------------
Write-Host '  Checking Python 3.11 ...' -ForegroundColor DarkGray
try {
    $pyVer = & py -3.11 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>&1
    if ($LASTEXITCODE -eq 0 -and $pyVer -match '^3\.11\.') {
        Add-Result '01. Python 3.11 (system)' 'PASS' "Found: Python $pyVer"
    } else {
        Add-Result '01. Python 3.11 (system)' 'FAIL' "py -3.11 returned: $pyVer -- Install Python 3.11 from python.org"
    }
} catch {
    Add-Result '01. Python 3.11 (system)' 'FAIL' "py -3.11 not found -- Install Python 3.11 from python.org"
}

# ---------------------------------------------------------------------------
# 2. .venv exists and is Python 3.11
# ---------------------------------------------------------------------------
Write-Host '  Checking .venv ...' -ForegroundColor DarkGray
if (Test-Path $venvPython) {
    try {
        $venvVer = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>&1
        if ($venvVer -match '^3\.11\.') {
            Add-Result '02. .venv Python version' 'PASS' "Python $venvVer"
        } else {
            Add-Result '02. .venv Python version' 'FAIL' ".venv uses Python $venvVer -- Re-create with py -3.11 -m venv .venv"
        }
    } catch {
        Add-Result '02. .venv Python version' 'FAIL' "Could not query .venv Python version"
    }
} else {
    Add-Result '02. .venv Python version' 'FAIL' ".venv\Scripts\python.exe not found -- Run setup-complete.ps1 or: py -3.11 -m venv .venv"
}

# ---------------------------------------------------------------------------
# 3. Required packages import check
# ---------------------------------------------------------------------------
Write-Host '  Checking required package imports ...' -ForegroundColor DarkGray

$packages = @(
    @{ Import = 'django';         Label = 'django' },
    @{ Import = 'cv2';            Label = 'cv2 (opencv)' },
    @{ Import = 'numpy';          Label = 'numpy' },
    @{ Import = 'tensorflow';     Label = 'tensorflow' },
    @{ Import = 'keras_facenet';  Label = 'keras_facenet' },
    @{ Import = 'waitress';       Label = 'waitress' },
    @{ Import = 'cryptography';   Label = 'cryptography' },
    @{ Import = 'PIL';            Label = 'PIL (Pillow)' },
    @{ Import = 'openpyxl';       Label = 'openpyxl' },
    @{ Import = 'requests';       Label = 'requests' },
    @{ Import = 'dotenv';         Label = 'python-dotenv' },
    @{ Import = 'whitenoise';     Label = 'whitenoise' }
)

if (Test-Path $venvPython) {
    foreach ($pkg in $packages) {
        $result = & $venvPython -c "import $($pkg.Import); print('ok')" 2>&1
        if ($result -match 'ok') {
            Add-Result "03. Package: $($pkg.Label)" 'PASS' ''
        } else {
            $errShort = ($result | Select-Object -First 1) -replace '^.*Error: ', ''
            Add-Result "03. Package: $($pkg.Label)" 'FAIL' "Import failed: $errShort -- Run: pip install -r requirements.txt"
        }
    }
} else {
    Add-Result '03. Package imports' 'FAIL' '.venv not found -- cannot check imports'
}

# ---------------------------------------------------------------------------
# 4. Django settings load
# ---------------------------------------------------------------------------
Write-Host '  Checking Django settings ...' -ForegroundColor DarkGray
if (Test-Path $venvPython) {
    $env:DJANGO_SETTINGS_MODULE = 'fans.settings'
    $checkOut = & $venvPython manage.py check 2>&1
    if ($LASTEXITCODE -eq 0) {
        Add-Result '04. Django settings load' 'PASS' 'manage.py check passed'
    } else {
        $firstErr = ($checkOut | Where-Object { $_ -match 'Error|error' } | Select-Object -First 1)
        Add-Result '04. Django settings load' 'FAIL' "manage.py check failed: $firstErr"
    }
} else {
    Add-Result '04. Django settings load' 'FAIL' '.venv not found'
}

# ---------------------------------------------------------------------------
# 5. Pending migrations
# ---------------------------------------------------------------------------
Write-Host '  Checking migrations ...' -ForegroundColor DarkGray
if (Test-Path $venvPython) {
    $migOut = & $venvPython manage.py migrate --check 2>&1
    if ($LASTEXITCODE -eq 0) {
        Add-Result '05. Migrations applied' 'PASS' 'No pending migrations'
    } else {
        Add-Result '05. Migrations applied' 'WARN' 'Unapplied migrations found -- Run: python manage.py migrate'
    }
} else {
    Add-Result '05. Migrations applied' 'FAIL' '.venv not found'
}

# ---------------------------------------------------------------------------
# 6. staticfiles/
# ---------------------------------------------------------------------------
Write-Host '  Checking staticfiles ...' -ForegroundColor DarkGray
$staticManifest = Join-Path $staticDir 'staticfiles.json'
if (Test-Path $staticManifest) {
    Add-Result '06. staticfiles/ manifest' 'PASS' 'staticfiles.json present'
} elseif (Test-Path $staticDir) {
    Add-Result '06. staticfiles/ manifest' 'WARN' 'staticfiles/ exists but staticfiles.json missing -- Run: python manage.py collectstatic --noinput'
} else {
    Add-Result '06. staticfiles/ manifest' 'WARN' 'staticfiles/ missing -- Run: python manage.py collectstatic --noinput'
}

# ---------------------------------------------------------------------------
# 7. Caddy executable
# ---------------------------------------------------------------------------
Write-Host '  Checking Caddy ...' -ForegroundColor DarkGray
if (Test-Path $caddyExe) {
    Add-Result '07. Caddy executable' 'PASS' $caddyExe
} else {
    # Also try PATH
    $caddyPath = $null
    try { $caddyPath = (Get-Command caddy -ErrorAction Stop).Source } catch {}
    if ($caddyPath) {
        Add-Result '07. Caddy executable' 'PASS' "Found on PATH: $caddyPath"
    } else {
        Add-Result '07. Caddy executable' 'FAIL' "caddy.exe not found at $caddyExe or on PATH -- Download from caddyserver.com"
    }
}

# ---------------------------------------------------------------------------
# 8. mkcert executable
# ---------------------------------------------------------------------------
Write-Host '  Checking mkcert ...' -ForegroundColor DarkGray
if (Test-Path $mkcertExe) {
    Add-Result '08. mkcert executable' 'PASS' $mkcertExe
} else {
    Add-Result '08. mkcert executable' 'WARN' "mkcert.exe not found at $mkcertExe -- Required only for cert generation/renewal"
}

# ---------------------------------------------------------------------------
# 9. TLS certificate files
# ---------------------------------------------------------------------------
Write-Host '  Checking TLS certificates ...' -ForegroundColor DarkGray
$certOK = $true
if (Test-Path $certFile) {
    Add-Result '09. fans-cert.pem' 'PASS' 'Present'
} else {
    Add-Result '09. fans-cert.pem' 'FAIL' 'Missing -- Run setup-secure-server.ps1 to generate'
    $certOK = $false
}
if (Test-Path $certKey) {
    Add-Result '09. fans-cert-key.pem' 'PASS' 'Present'
} else {
    Add-Result '09. fans-cert-key.pem' 'FAIL' 'Missing -- Run setup-secure-server.ps1 to generate'
    $certOK = $false
}

# ---------------------------------------------------------------------------
# 10-13. .env configuration
# ---------------------------------------------------------------------------
Write-Host '  Checking .env ...' -ForegroundColor DarkGray
if (-not (Test-Path $envFile)) {
    Add-Result '10. .env exists' 'FAIL' '.env file not found -- Copy .env.example to .env and configure'
    Add-Result '11. SECRET_KEY' 'FAIL' '.env missing'
    Add-Result '12. EMBEDDING_ENCRYPTION_KEY' 'FAIL' '.env missing'
    Add-Result '13. DEBUG mode' 'FAIL' '.env missing'
    Add-Result '14. ALLOWED_HOSTS' 'WARN' '.env missing'
    Add-Result '15. CSRF_TRUSTED_ORIGINS' 'WARN' '.env missing'
} else {
    Add-Result '10. .env exists' 'PASS' $envFile
    $envRaw = Get-Content $envFile -Raw -Encoding UTF8 -ErrorAction SilentlyContinue

    # SECRET_KEY
    if ($envRaw -match '(?m)^SECRET_KEY\s*=\s*(.+)$') {
        $skVal = $Matches[1].Trim()
        if ($skVal -match 'change|placeholder|insecure|your-secret') {
            Add-Result '11. SECRET_KEY' 'FAIL' 'SECRET_KEY is still the placeholder value -- Generate a real key'
        } elseif ($skVal.Length -lt 30) {
            Add-Result '11. SECRET_KEY' 'WARN' 'SECRET_KEY looks very short -- Should be at least 50 characters'
        } else {
            Add-Result '11. SECRET_KEY' 'PASS' "Set (length: $($skVal.Length))"
        }
    } else {
        Add-Result '11. SECRET_KEY' 'FAIL' 'SECRET_KEY not found in .env -- Add SECRET_KEY=<value>'
    }

    # EMBEDDING_ENCRYPTION_KEY
    if ($envRaw -match '(?m)^EMBEDDING_ENCRYPTION_KEY\s*=\s*(.+)$') {
        $ekVal = $Matches[1].Trim()
        if ($ekVal.Length -gt 20) {
            Add-Result '12. EMBEDDING_ENCRYPTION_KEY' 'PASS' "Set (length: $($ekVal.Length))"
        } else {
            Add-Result '12. EMBEDDING_ENCRYPTION_KEY' 'FAIL' 'EMBEDDING_ENCRYPTION_KEY is set but too short -- Generate with: python manage.py generate_key'
        }
    } else {
        Add-Result '12. EMBEDDING_ENCRYPTION_KEY' 'FAIL' 'Not found in .env -- Generate with: python manage.py generate_key'
    }

    # DEBUG
    if ($envRaw -match '(?m)^DEBUG\s*=\s*True') {
        Add-Result '13. DEBUG=False' 'WARN' 'DEBUG=True -- Set DEBUG=False for production deployment'
    } else {
        Add-Result '13. DEBUG=False' 'PASS' 'DEBUG is not True'
    }

    # ALLOWED_HOSTS
    if ($envRaw -match '(?m)^ALLOWED_HOSTS\s*=\s*(.+)$') {
        $ahVal = $Matches[1].Trim()
        if ($ahVal -match 'fans-barangay\.local') {
            Add-Result '14. ALLOWED_HOSTS (LAN hostname)' 'PASS' $ahVal
        } else {
            Add-Result '14. ALLOWED_HOSTS (LAN hostname)' 'WARN' "fans-barangay.local missing from ALLOWED_HOSTS: $ahVal"
        }
    } else {
        Add-Result '14. ALLOWED_HOSTS' 'WARN' 'ALLOWED_HOSTS not explicitly set in .env (defaults to localhost,127.0.0.1 only)'
    }

    # CSRF_TRUSTED_ORIGINS
    if ($envRaw -match '(?m)^CSRF_TRUSTED_ORIGINS\s*=\s*(.+)$') {
        $ctVal = $Matches[1].Trim()
        if ($ctVal.Length -gt 5) {
            Add-Result '15. CSRF_TRUSTED_ORIGINS' 'PASS' $ctVal
        } else {
            Add-Result '15. CSRF_TRUSTED_ORIGINS' 'WARN' 'CSRF_TRUSTED_ORIGINS is empty -- Set to https://fans-barangay.local for HTTPS deployments'
        }
    } else {
        Add-Result '15. CSRF_TRUSTED_ORIGINS' 'WARN' 'Not set in .env -- Required for HTTPS: CSRF_TRUSTED_ORIGINS=https://fans-barangay.local'
    }
}

# ---------------------------------------------------------------------------
# 16. Database path
# ---------------------------------------------------------------------------
Write-Host '  Checking database ...' -ForegroundColor DarkGray
$dbPath = Join-Path $projectRoot 'db.sqlite3'
if (Test-Path $dbPath) {
    $dbSize = [math]::Round((Get-Item $dbPath).Length / 1KB, 1)
    Add-Result '16. db.sqlite3' 'PASS' "Present ($dbSize KB)"
} else {
    Add-Result '16. db.sqlite3' 'WARN' 'db.sqlite3 not found -- Run: python manage.py migrate (creates empty database)'
}

# ---------------------------------------------------------------------------
# 17. media/ writable
# ---------------------------------------------------------------------------
Write-Host '  Checking media/ ...' -ForegroundColor DarkGray
if (Test-Path $mediaDir) {
    try {
        $testFile = Join-Path $mediaDir '.write-test'
        [System.IO.File]::WriteAllText($testFile, 'test')
        Remove-Item $testFile -Force -ErrorAction SilentlyContinue
        Add-Result '17. media/ writable' 'PASS' $mediaDir
    } catch {
        Add-Result '17. media/ writable' 'FAIL' "media/ exists but is not writable: $($_.Exception.Message)"
    }
} else {
    Add-Result '17. media/ writable' 'WARN' "media/ does not exist -- Django creates it automatically on first upload"
}

# ---------------------------------------------------------------------------
# 18. logs/ writable
# ---------------------------------------------------------------------------
Write-Host '  Checking logs/ ...' -ForegroundColor DarkGray
$runtimeLogsDir = Join-Path $projectRoot 'logs'
if (Test-Path $runtimeLogsDir) {
    try {
        $testFile = Join-Path $runtimeLogsDir '.write-test'
        [System.IO.File]::WriteAllText($testFile, 'test')
        Remove-Item $testFile -Force -ErrorAction SilentlyContinue
        Add-Result '18. logs/ writable' 'PASS' $runtimeLogsDir
    } catch {
        Add-Result '18. logs/ writable' 'FAIL' "logs/ exists but is not writable: $($_.Exception.Message)"
    }
} else {
    Add-Result '18. logs/ writable' 'WARN' "logs/ does not exist -- Create with: New-Item -ItemType Directory logs"
}

# ---------------------------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   FANS-C  |  Verification Summary' -ForegroundColor Cyan
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''

foreach ($item in $items) {
    $label = $item.Label
    $msg   = if ($item.Message) { "  -- $($item.Message)" } else { '' }
    switch ($item.Status) {
        'PASS' { Write-Host "   [PASS]  $label$msg" -ForegroundColor Green }
        'FAIL' { Write-Host "   [FAIL]  $label$msg" -ForegroundColor Red }
        'WARN' { Write-Host "   [WARN]  $label$msg" -ForegroundColor Yellow }
    }
}

Write-Host ''
Write-Host '  ----------------------------------------------------------------' -ForegroundColor DarkCyan
Write-Host ("   PASS: $pass   FAIL: $fail   WARN: $warn") -ForegroundColor White
Write-Host '  ----------------------------------------------------------------' -ForegroundColor DarkCyan
Write-Host ''

if ($fail -gt 0) {
    Write-Host '  RESULT: INSTALLATION INCOMPLETE' -ForegroundColor Red
    Write-Host '  Fix all [FAIL] items before putting the system into production.' -ForegroundColor Yellow
} elseif ($warn -gt 0) {
    Write-Host '  RESULT: INSTALLATION OK WITH WARNINGS' -ForegroundColor Yellow
    Write-Host '  Review [WARN] items -- some may be required for production.' -ForegroundColor Yellow
} else {
    Write-Host '  RESULT: INSTALLATION VERIFIED' -ForegroundColor Green
    Write-Host '  All checks passed. System is ready for production.' -ForegroundColor Green
}

Write-Host ''
