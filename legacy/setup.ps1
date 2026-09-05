#Requires -Version 5.1
<#
.SYNOPSIS
    DEPRECATED -- DO NOT USE.

.DESCRIPTION
    This script has been superseded by setup-secure-server.ps1.

    setup-secure-server.ps1 is now the ONLY required setup script.
    It handles everything this script used to do, plus TLS certificate
    generation and all secure-server configuration in a single run.

    HOW TO SET UP FANS-C (current workflow):
      1. Run: .\setup-secure-server.ps1   (one-time server setup)
      2. Run: .\setup-autostart.ps1       (enable auto-start at boot)
      3. Daily use: no scripts needed -- the system starts automatically.

    This file is kept only for reference. Do not run it.

.NOTES
    Deprecated: replaced by setup-secure-server.ps1
#>

Write-Host ""
Write-Host "  ================================================================" -ForegroundColor Red
Write-Host "   DEPRECATED SCRIPT -- DO NOT RUN THIS FILE                     " -ForegroundColor Red
Write-Host "  ================================================================" -ForegroundColor Red
Write-Host ""
Write-Host "  setup.ps1 has been replaced by setup-secure-server.ps1." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Run the correct setup script instead:" -ForegroundColor White
Write-Host "    .\setup-secure-server.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "  setup-secure-server.ps1 handles everything:" -ForegroundColor White
Write-Host "    - Virtual environment and Python dependencies" -ForegroundColor DarkGray
Write-Host "    - .env creation and key generation" -ForegroundColor DarkGray
Write-Host "    - TLS certificate generation (mkcert)" -ForegroundColor DarkGray
Write-Host "    - Django migrations and static files" -ForegroundColor DarkGray
Write-Host "    - Firewall rule and admin user creation" -ForegroundColor DarkGray
Write-Host ""
Read-Host "  Press Enter to close"
exit 0

# ---------------------------------------------------------------------------
# LEGACY CODE BELOW -- retained for reference only, not executed
# ---------------------------------------------------------------------------
return

param(
    [switch]$SkipStaticFiles,
    [switch]$SkipAdminCreate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

function Write-Step {
    param([int]$Step, [string]$Text)
    Write-Host ""
    Write-Host "  [$Step] $Text" -ForegroundColor Cyan
}

function Write-OK   { param([string]$msg) Write-Host "      [OK]   $msg" -ForegroundColor Green }
function Write-Warn { param([string]$msg) Write-Host "      [WARN] $msg" -ForegroundColor Yellow }
function Write-Fail {
    param([string]$msg)
    Write-Host "      [FAIL] $msg" -ForegroundColor Red
}

function Invoke-Checked {
    <#
    .SYNOPSIS Run a command in the project root; exit if it fails.
    #>
    param([string]$Exe, [string[]]$Arguments)
    $joined = "$Exe $($Arguments -join ' ')"
    Write-Host "      > $joined" -ForegroundColor DarkGray
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Command failed (exit code $LASTEXITCODE): $joined"
        exit $LASTEXITCODE
    }
}

function Update-EnvValue {
    <#
    .SYNOPSIS
        Write $Key=$Value into the .env file.
        If the key already has a non-empty value, leave it unchanged.
        If it exists with an empty value, replace it.
        If it is absent, append it.
    #>
    param([string]$EnvPath, [string]$Key, [string]$Value)
    $raw = Get-Content $EnvPath -Raw -Encoding UTF8

    if ($raw -match "(?m)^${Key}=.+") {
        # Key exists and already has a value — do not overwrite
        return $false
    }
    if ($raw -match "(?m)^${Key}=\s*$") {
        # Key present but empty — replace the whole line
        $escaped = [regex]::Escape($Value)
        $raw = $raw -replace "(?m)^${Key}=\s*$", "${Key}=${Value}"
    } else {
        # Key missing entirely — append
        if (-not $raw.EndsWith("`n")) { $raw += "`n" }
        $raw += "${Key}=${Value}`n"
    }
    Set-Content $EnvPath $raw -Encoding UTF8 -NoNewline
    return $true
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
Clear-Host
Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "   FANS-C  |  FaceNet Facial Verification System  |  Setup v2    " -ForegroundColor Cyan
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host ""
Write-Host "  Project : $PSScriptRoot" -ForegroundColor DarkGray
Write-Host "  Date    : $(Get-Date -Format 'yyyy-MM-dd HH:mm')" -ForegroundColor DarkGray
Write-Host ""

$projectRoot = $PSScriptRoot
Set-Location $projectRoot

# ---------------------------------------------------------------------------
# Step 1: Path length check
# ---------------------------------------------------------------------------
Write-Step 1 "Checking project path length..."

$pathLen = $projectRoot.Length
if ($pathLen -gt 80) {
    Write-Warn "Project path is $pathLen characters long."
    Write-Warn "TensorFlow's wheel contains deeply nested paths that can exceed"
    Write-Warn "Windows's default 260-character limit, causing silent failures."
    Write-Host ""
    Write-Host "  RECOMMENDATION: Move the project to a short path, for example:" -ForegroundColor Yellow
    Write-Host "    C:\FANSC   or   D:\FANS" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Alternatively, enable long-path support (requires Admin PowerShell):" -ForegroundColor Yellow
    Write-Host '    New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `' -ForegroundColor DarkGray
    Write-Host '        -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force' -ForegroundColor DarkGray
    Write-Host ""
    $cont = Read-Host "  Continue anyway? (y/N)"
    if ($cont -notmatch '^[yY]') {
        Write-Host "  Setup cancelled. Move the project and retry." -ForegroundColor Yellow
        exit 0
    }
    Write-Warn "Continuing with long path — install may fail. Retry from a shorter path if so."
} else {
    Write-OK "Path length is $pathLen characters (safe)"
}

# Attempt to enable long paths silently (requires admin; non-fatal if it fails)
try {
    $regPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem'
    $cur = (Get-ItemProperty $regPath -Name LongPathsEnabled -ErrorAction SilentlyContinue).LongPathsEnabled
    if ($cur -ne 1) {
        New-ItemProperty $regPath -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force | Out-Null
        Write-OK "Windows long-path support enabled in registry"
    } else {
        Write-OK "Windows long-path support already enabled"
    }
} catch {
    Write-Warn "Could not enable long-path registry key (not running as Administrator)."
    Write-Warn "If TensorFlow install fails with path errors, run PowerShell as Admin and retry."
}

# ---------------------------------------------------------------------------
# Step 2: Python 3.11 check
# ---------------------------------------------------------------------------
Write-Step 2 "Checking Python 3.11..."

$pythonExeGlobal = $null

# Prefer the py launcher so we can explicitly request 3.11
foreach ($candidate in @('py -3.11', 'python3.11', 'python')) {
    try {
        $verOut = (& cmd /c "$candidate --version 2>&1").Trim()
        if ($verOut -match 'Python\s+3\.11\.') {
            $pythonExeGlobal = $candidate
            Write-OK "Found $verOut  (via: $candidate)"
            break
        }
    } catch { }
}

if (-not $pythonExeGlobal) {
    Write-Fail "Python 3.11.x not found on this system."
    Write-Host ""
    Write-Host "  tensorflow-cpu 2.13.x requires Python 3.11 exactly." -ForegroundColor Yellow
    Write-Host "  Python 3.12 / 3.13 produce a DLL load error at runtime." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Download Python 3.11.9 from:" -ForegroundColor Yellow
    Write-Host "    https://www.python.org/downloads/release/python-3119/" -ForegroundColor Cyan
    Write-Host "  During installation, check 'Add Python to PATH'." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  After installation, re-run this script." -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------------------
# Step 3: Create virtual environment
# ---------------------------------------------------------------------------
Write-Step 3 "Setting up virtual environment..."

$venvDir      = Join-Path $projectRoot '.venv'
$venvPython   = Join-Path $venvDir 'Scripts\python.exe'
$venvPip      = Join-Path $venvDir 'Scripts\pip.exe'
$venvActivate = Join-Path $venvDir 'Scripts\Activate.ps1'

if (Test-Path $venvDir) {
    Write-OK ".venv already exists — skipping creation"
} else {
    Write-Host "      Creating .venv ..." -ForegroundColor DarkGray
    & cmd /c "$pythonExeGlobal -m venv .venv"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Failed to create .venv. Check Python installation."
        exit 1
    }
    Write-OK ".venv created"
}

if (-not (Test-Path $venvPython)) {
    Write-Fail ".venv appears incomplete — $venvPython not found."
    Write-Warn "Delete the .venv folder and re-run this script."
    exit 1
}

# ---------------------------------------------------------------------------
# Step 4: Upgrade pip
# ---------------------------------------------------------------------------
Write-Step 4 "Upgrading pip..."
Invoke-Checked $venvPython @('-m', 'pip', 'install', '--upgrade', 'pip', '--quiet')
Write-OK "pip upgraded"

# ---------------------------------------------------------------------------
# Step 5: Install dependencies in correct order
# ---------------------------------------------------------------------------
Write-Step 5 "Installing Python dependencies..."
Write-Host "      tensorflow-cpu is large (~200-350 MB). First install may take 5-15 min." -ForegroundColor DarkGray
Write-Host ""

# 5a — numpy FIRST (scipy and tensorflow inspect it at install time)
Write-Host "      [5a] numpy 1.24.3 ..." -ForegroundColor DarkGray
Invoke-Checked $venvPip @('install', 'numpy==1.24.3', '--quiet')
Write-OK "numpy 1.24.3 installed"

# 5b — tensorflow-cpu next (installs Keras, protobuf, etc.)
Write-Host "      [5b] tensorflow-cpu 2.13.1 ..." -ForegroundColor DarkGray
Invoke-Checked $venvPip @('install', 'tensorflow-cpu==2.13.1', '--quiet')
Write-OK "tensorflow-cpu 2.13.1 installed"

# 5c — remainder from requirements.txt
# Use --no-deps for numpy/tensorflow lines already installed to avoid re-resolution
Write-Host "      [5c] Remaining requirements ..." -ForegroundColor DarkGray
Invoke-Checked $venvPip @('install', '-r', 'requirements.txt', '--quiet')
Write-OK "All dependencies installed"

# ---------------------------------------------------------------------------
# Step 6: Create .env from .env.example
# ---------------------------------------------------------------------------
Write-Step 6 "Configuring .env file..."

$envFile    = Join-Path $projectRoot '.env'
$envExample = Join-Path $projectRoot '.env.example'

if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-OK ".env created from .env.example"
    } else {
        Write-Fail ".env.example is missing — cannot create .env"
        exit 1
    }
} else {
    Write-OK ".env already exists"
}

# ---------------------------------------------------------------------------
# Step 7: Auto-generate Django SECRET_KEY if missing/default
# ---------------------------------------------------------------------------
Write-Step 7 "Verifying SECRET_KEY..."

$envRaw = Get-Content $envFile -Raw -Encoding UTF8
$needsKey = ($envRaw -match '(?m)^SECRET_KEY\s*=\s*$') -or
            ($envRaw -match '(?m)^SECRET_KEY\s*=\s*your-secret-key')

if ($needsKey) {
    $secretKey = & $venvPython -c @"
import secrets, string
chars = string.ascii_letters + string.digits + '!@#$%^&*(-_=+)'
print(''.join(secrets.choice(chars) for _ in range(50)))
"@
    $changed = Update-EnvValue $envFile 'SECRET_KEY' $secretKey
    if ($changed) {
        Write-OK "SECRET_KEY generated and saved to .env"
    }
} else {
    Write-OK "SECRET_KEY already set"
}

# ---------------------------------------------------------------------------
# Step 8: Auto-generate EMBEDDING_ENCRYPTION_KEY if missing
# ---------------------------------------------------------------------------
Write-Step 8 "Verifying EMBEDDING_ENCRYPTION_KEY..."

$envRaw = Get-Content $envFile -Raw -Encoding UTF8
if ($envRaw -match '(?m)^EMBEDDING_ENCRYPTION_KEY\s*=\s*$') {
    $fernetKey = & $venvPython -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    $changed = Update-EnvValue $envFile 'EMBEDDING_ENCRYPTION_KEY' $fernetKey
    if ($changed) {
        Write-OK "EMBEDDING_ENCRYPTION_KEY generated and saved to .env"
        Write-Host ""
        Write-Warn "IMPORTANT: Back up your .env file securely."
        Write-Warn "This key encrypts all stored face embeddings."
        Write-Warn "Losing it will make registered face data permanently unreadable."
        Write-Warn "On another device, use the SAME key from this .env file."
        Write-Host ""
    }
} else {
    Write-OK "EMBEDDING_ENCRYPTION_KEY already set"
}

# ---------------------------------------------------------------------------
# Step 9: Database migrations
# ---------------------------------------------------------------------------
Write-Step 9 "Running database migrations..."
Invoke-Checked $venvPython @('manage.py', 'migrate', '--noinput')
Write-OK "Migrations applied"

# ---------------------------------------------------------------------------
# Step 10: Initialise system configuration
# ---------------------------------------------------------------------------
Write-Step 10 "Initialising system configuration..."
Invoke-Checked $venvPython @('manage.py', 'init_config')
Write-OK "System config initialised"

# ---------------------------------------------------------------------------
# Step 11: Create admin user (interactive)
# ---------------------------------------------------------------------------
# No credentials are hard-coded.  Django's createsuperuser prompts for
# username, email, and password interactively so the person running setup
# chooses the credentials at setup time.
# Use -SkipAdminCreate if an admin account already exists.
# ---------------------------------------------------------------------------
if ($SkipAdminCreate) {
    Write-Step 11 "Skipping admin user creation (-SkipAdminCreate)"
} else {
    Write-Step 11 "Creating admin user (you will be prompted for credentials)..."
    Write-Host ""
    Write-Host "      Django will now prompt for a username, email, and password." -ForegroundColor White
    Write-Host "      Choose a strong password and store it securely." -ForegroundColor White
    Write-Host ""
    # Run createsuperuser interactively -- must not be called via Invoke-Checked
    # because it reads from stdin.  Let it run with its normal exit code handling.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $venvPython manage.py createsuperuser
    $suExitCode = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    if ($suExitCode -ne 0) {
        Write-Warn "createsuperuser exited with code $suExitCode."
        Write-Warn "You can create the admin account later with:"
        Write-Warn "  .\.venv\Scripts\Activate.ps1"
        Write-Warn "  python manage.py createsuperuser"
    } else {
        Write-OK "Admin user created"
    }
}

# ---------------------------------------------------------------------------
# Step 12: Collect static files
# ---------------------------------------------------------------------------
if ($SkipStaticFiles) {
    Write-Step 12 "Skipping static file collection (-SkipStaticFiles)"
} else {
    Write-Step 12 "Collecting static files..."
    Invoke-Checked $venvPython @('manage.py', 'collectstatic', '--noinput', '--clear', '--quiet')
    Write-OK "Static files collected"
}

# ---------------------------------------------------------------------------
# Step 13: System health check
# ---------------------------------------------------------------------------
Write-Step 13 "Running system health check..."
Write-Host ""
& $venvPython manage.py check_system
Write-Host ""

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "   Setup Complete!" -ForegroundColor Green
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host ""
Write-Host "  To start the server:" -ForegroundColor White
Write-Host "    .\run.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Or manually:" -ForegroundColor White
Write-Host "    .\.venv\Scripts\Activate.ps1" -ForegroundColor Cyan
Write-Host "    python manage.py runserver" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Open the app:" -ForegroundColor White
Write-Host "    URL : http://127.0.0.1:8000/" -ForegroundColor Cyan
Write-Host "    Log in with the admin account you created in Step 11." -ForegroundColor White
Write-Host ""
Write-Host "  If you skipped Step 11, create the admin account now with:" -ForegroundColor White
Write-Host "    .\.venv\Scripts\Activate.ps1" -ForegroundColor Cyan
Write-Host "    python manage.py createsuperuser" -ForegroundColor Cyan
Write-Host ""
