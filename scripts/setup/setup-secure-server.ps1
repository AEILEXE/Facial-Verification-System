#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C Complete One-Time Secure Server Setup.
    Run this ONCE on the server machine before first use.

.DESCRIPTION
    This script performs every step needed to run FANS-C over HTTPS on the
    barangay LAN. It is safe to re-run if something needs to be fixed.

    What it does (in order):
      1.  Checks for Administrator rights
      2.  Checks project path length (warns if too long for Windows)
      3.  Verifies Python 3.11 is installed
      4.  Creates the .venv virtual environment (skippable with -SkipDeps)
      5.  Installs numpy, tensorflow-cpu, and all requirements
      6.  Creates .env from .env.example if missing
      7.  Auto-generates Django SECRET_KEY if missing or default
      8.  Auto-generates EMBEDDING_ENCRYPTION_KEY (Fernet) if missing
      9.  Finds mkcert.exe (bundled or on PATH)
     10.  Installs the mkcert local CA into the Windows trust store
     11.  Detects the server LAN IP automatically
     12.  Generates TLS certificates for:
              fans-barangay.local  <LAN-IP>  localhost  127.0.0.1
     13.  Copies certificates to stable names (fans-cert.pem / fans-cert-key.pem)
     14.  Verifies Caddyfile references the stable cert name
     15.  Runs Django database migrations
     16.  Initialises system configuration (init_config)
     17.  Collects static files (skippable with -SkipStaticFiles)
     18.  Adds a Windows Firewall rule for HTTPS (port 443) if missing
     19.  Optionally prompts to create the Django admin user
     20.  Optionally starts Waitress and Caddy

    What still requires manual steps after this script:
      - Editing .env for ALLOWED_HOSTS / CSRF_TRUSTED_ORIGINS
      - Installing the mkcert root CA on client devices (use trust-local-cert.bat)
      - Adding fans-barangay.local to each client hosts file

.PARAMETER SkipDeps
    Skip virtual environment creation and package installation.
    Use this on re-runs when .venv already exists and packages are installed.

.PARAMETER SkipAdminCreate
    Skip the Django createsuperuser step.
    Use this if an admin account already exists.

.PARAMETER SkipStaticFiles
    Skip the collectstatic step.
    Use this on re-runs when static files have not changed.

.NOTES
    Run from the project root as Administrator:
      Right-click scripts\setup\setup-secure-server.ps1 -> Run with PowerShell (as Admin)
      -- or --
      Start-Process powershell -Verb RunAs -ArgumentList "-File .\scripts\setup\setup-secure-server.ps1"

.EXAMPLE
    .\scripts\setup\setup-secure-server.ps1
    .\scripts\setup\setup-secure-server.ps1 -SkipDeps
    .\scripts\setup\setup-secure-server.ps1 -SkipDeps -SkipAdminCreate
    .\scripts\setup\setup-secure-server.ps1 -SkipDeps -SkipStaticFiles -SkipAdminCreate
#>

param(
    [switch]$SkipDeps,
    [switch]$SkipAdminCreate,
    [switch]$SkipStaticFiles,
    [switch]$ForceRegenerateCert
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $projectRoot

# -- Paths --------------------------------------------------------------------
$venvDir       = Join-Path $projectRoot '.venv'
$venvPython    = Join-Path $projectRoot '.venv\Scripts\python.exe'
$venvPip       = Join-Path $projectRoot '.venv\Scripts\pip.exe'
$venvWaitress  = Join-Path $projectRoot '.venv\Scripts\waitress-serve.exe'
$envFile       = Join-Path $projectRoot '.env'
$envExample    = Join-Path $projectRoot '.env.example'
$caddyFile     = Join-Path $projectRoot 'Caddyfile'
$mkcertBundled = Join-Path $projectRoot 'tools\mkcert\mkcert.exe'
$caddyBundled  = Join-Path $projectRoot 'tools\caddy.exe'

# -- Helper functions ---------------------------------------------------------

function Write-Step {
    param([string]$Step, [string]$Text)
    Write-Host ""
    Write-Host "  [$Step] $Text" -ForegroundColor Cyan
}

function Write-OK {
    param([string]$Msg)
    Write-Host "         [OK]   $Msg" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Msg)
    Write-Host "         [WARN] $Msg" -ForegroundColor Yellow
}

function Write-Fail {
    param([string]$Msg)
    Write-Host "         [FAIL] $Msg" -ForegroundColor Red
}

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    $joined = "$Exe $($Arguments -join ' ')"
    Write-Host "         > $joined" -ForegroundColor DarkGray
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Command failed (exit code $LASTEXITCODE): $joined"
        Read-Host "  Press Enter to exit"
        exit $LASTEXITCODE
    }
}

function Update-EnvValue {
    <#
    .SYNOPSIS
        Write Key=Value into the .env file.
        If the key already has a non-empty value, leave it unchanged.
        If it exists with an empty value, replace it.
        If it is absent entirely, append it.
    #>
    param([string]$EnvPath, [string]$Key, [string]$Value)
    $raw = Get-Content $EnvPath -Raw -Encoding UTF8

    if ($raw -match "(?m)^${Key}=.+") {
        return $false
    }
    if ($raw -match "(?m)^${Key}=\s*$") {
        $raw = $raw -replace "(?m)^${Key}=\s*$", "${Key}=${Value}"
    } else {
        if (-not $raw.EndsWith("`n")) { $raw += "`n" }
        $raw += "${Key}=${Value}`n"
    }
    Set-Content $EnvPath $raw -Encoding UTF8 -NoNewline
    return $true
}

function Set-EnvValueForce {
    <#
    .SYNOPSIS
        Like Update-EnvValue, but always overwrites — used when we have
        an authoritative value (e.g. detected LAN IP) that should beat any
        stale or wildcard value already on disk.
    #>
    param([string]$EnvPath, [string]$Key, [string]$Value)
    $raw = Get-Content $EnvPath -Raw -Encoding UTF8
    if ($raw -match "(?m)^${Key}=.*") {
        $raw = $raw -replace "(?m)^${Key}=.*", "${Key}=${Value}"
    } else {
        if (-not $raw.EndsWith("`n")) { $raw += "`n" }
        $raw += "${Key}=${Value}`n"
    }
    Set-Content $EnvPath $raw -Encoding UTF8 -NoNewline
}

# -- Banner -------------------------------------------------------------------
Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "   FANS-C  |  Complete Secure Server Setup                       " -ForegroundColor Cyan
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host ""
Write-Host "  Project : $projectRoot" -ForegroundColor DarkGray
Write-Host "  Date    : $(Get-Date -Format 'yyyy-MM-dd HH:mm')" -ForegroundColor DarkGray
Write-Host ""

# -- Step 1: Administrator check ----------------------------------------------
Write-Step "1/12" "Checking Administrator rights..."
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host ""
    Write-Fail "This script must be run as Administrator."
    Write-Host "         Right-click the script and choose 'Run with PowerShell'," -ForegroundColor Yellow
    Write-Host "         then approve the UAC prompt." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "  Press Enter to exit"
    exit 1
}
Write-OK "Administrator rights confirmed."

# -- Step 2: Path length check ------------------------------------------------
Write-Step "2/12" "Checking project path length..."
$pathLen = $projectRoot.Length
if ($pathLen -gt 80) {
    Write-Warn "Project path is $pathLen characters long."
    Write-Warn "TensorFlow wheels contain deeply nested paths that can exceed"
    Write-Warn "Windows 260-character path limit and cause silent failures."
    Write-Host ""
    Write-Host "  RECOMMENDATION: Move the project to a short path, e.g.:" -ForegroundColor Yellow
    Write-Host "    C:\FANSC   or   D:\FANS" -ForegroundColor Cyan
    Write-Host ""
    $cont = Read-Host "  Continue anyway? (y/N)"
    if ($cont -notmatch '^[yY]') {
        Write-Host "  Setup cancelled. Move the project and retry." -ForegroundColor Yellow
        exit 0
    }
    Write-Warn "Continuing with long path -- install may fail."
} else {
    Write-OK "Path length is $pathLen characters (safe)."
}

# Attempt to enable long-path support silently (non-fatal if denied)
try {
    $regPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem'
    $cur = (Get-ItemProperty $regPath -Name LongPathsEnabled -ErrorAction SilentlyContinue).LongPathsEnabled
    if ($cur -ne 1) {
        New-ItemProperty $regPath -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force | Out-Null
        Write-OK "Windows long-path support enabled in registry."
    } else {
        Write-OK "Windows long-path support already enabled."
    }
} catch {
    Write-Warn "Could not enable long-path registry key (non-fatal)."
}

# -- Step 3: Python 3.11 check ------------------------------------------------
Write-Step "3/12" "Checking Python 3.11..."
$pythonExeGlobal = $null

foreach ($candidate in @('py -3.11', 'python3.11', 'python')) {
    try {
        $verOut = (& cmd /c "$candidate --version 2>&1").Trim()
        if ($verOut -match 'Python\s+3\.11\.') {
            $pythonExeGlobal = $candidate
            Write-OK "Found $verOut (via: $candidate)"
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
    Write-Host "  Download Python 3.11 from:" -ForegroundColor Yellow
    Write-Host "    https://www.python.org/downloads/release/python-3119/" -ForegroundColor Cyan
    Write-Host "  During installation, check 'Add Python to PATH'." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "  Press Enter to exit"
    exit 1
}

# -- Step 4: Virtual environment + dependencies -------------------------------
Write-Step "4/12" "Setting up virtual environment and dependencies..."

if ($SkipDeps) {
    Write-Warn "Skipping dependency install (-SkipDeps). Assuming .venv already exists."
} else {
    # 4a: Create .venv if missing
    if (Test-Path $venvDir) {
        Write-OK ".venv already exists -- skipping creation."
    } else {
        Write-Host "         Creating .venv ..." -ForegroundColor DarkGray
        & cmd /c "$pythonExeGlobal -m venv .venv"
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Failed to create .venv. Check Python installation."
            Read-Host "  Press Enter to exit"
            exit 1
        }
        Write-OK ".venv created."
    }

    if (-not (Test-Path $venvPython)) {
        Write-Fail ".venv appears incomplete -- $venvPython not found."
        Write-Warn "Delete the .venv folder and re-run without -SkipDeps."
        Read-Host "  Press Enter to exit"
        exit 1
    }

    # 4b: Upgrade pip
    Write-Host "         Upgrading pip ..." -ForegroundColor DarkGray
    Invoke-Checked $venvPython @('-m', 'pip', 'install', '--upgrade', 'pip', '--quiet')
    Write-OK "pip upgraded."

    # 4c: Install numpy first (scipy and tensorflow inspect it at install time)
    Write-Host "         Installing numpy 1.24.3 ..." -ForegroundColor DarkGray
    Invoke-Checked $venvPip @('install', 'numpy==1.24.3', '--quiet')
    Write-OK "numpy 1.24.3 installed."

    # 4d: Install tensorflow-cpu (large download -- first run may take 5-15 min)
    Write-Host "         Installing tensorflow-cpu 2.13.1 (may take 5-15 min) ..." -ForegroundColor DarkGray
    Invoke-Checked $venvPip @('install', 'tensorflow-cpu==2.13.1', '--quiet')
    Write-OK "tensorflow-cpu 2.13.1 installed."

    # 4e: Install remaining requirements
    Write-Host "         Installing remaining requirements ..." -ForegroundColor DarkGray
    Invoke-Checked $venvPip @('install', '-r', 'requirements.txt', '--quiet')
    Write-OK "All dependencies installed."
}

# Confirm venv python exists before any Django commands
if (-not (Test-Path $venvPython)) {
    Write-Fail ".venv\Scripts\python.exe not found."
    Write-Fail "Run without -SkipDeps to create the virtual environment first."
    Read-Host "  Press Enter to exit"
    exit 1
}

# -- Step 5: Create .env from .env.example ------------------------------------
Write-Step "5/12" "Configuring .env file..."

if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-OK ".env created from .env.example."
    } else {
        Write-Fail ".env.example is missing -- cannot create .env automatically."
        Write-Fail "Create a .env file in the project root and re-run."
        Read-Host "  Press Enter to exit"
        exit 1
    }
} else {
    Write-OK ".env already exists."
}

# -- Step 6: Generate Django SECRET_KEY if missing or default -----------------
Write-Step "6/12" "Verifying Django SECRET_KEY..."

$envRaw   = Get-Content $envFile -Raw -Encoding UTF8
$needsKey = ($envRaw -match '(?m)^SECRET_KEY\s*=\s*$') -or
            ($envRaw -match '(?m)^SECRET_KEY\s*=\s*your-secret-key') -or
            ($envRaw -match '(?im)^SECRET_KEY\s*=\s*REPLACE_ME') -or
            ($envRaw -match '(?m)^SECRET_KEY\s*=\s*SECRET_KEY=')

if ($needsKey) {
    $secretKey = & $venvPython -c "import secrets; print(secrets.token_urlsafe(50))"
    $changed   = Update-EnvValue $envFile 'SECRET_KEY' $secretKey
    if ($changed) {
        Write-OK "SECRET_KEY generated and saved to .env."
    }
} else {
    Write-OK "SECRET_KEY already set."
}

# -- Step 7: Generate EMBEDDING_ENCRYPTION_KEY if missing ---------------------
Write-Step "7/12" "Verifying EMBEDDING_ENCRYPTION_KEY..."

$envRaw      = Get-Content $envFile -Raw -Encoding UTF8
$needsEmbKey = ($envRaw -match '(?m)^EMBEDDING_ENCRYPTION_KEY\s*=\s*$') -or
               ($envRaw -match '(?im)^EMBEDDING_ENCRYPTION_KEY\s*=\s*REPLACE_ME')

if ($needsEmbKey) {
    $fernetKey = & $venvPython -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    $changed   = Update-EnvValue $envFile 'EMBEDDING_ENCRYPTION_KEY' $fernetKey
    if ($changed) {
        Write-OK "EMBEDDING_ENCRYPTION_KEY generated and saved to .env."
        Write-Host ""
        Write-Warn "IMPORTANT: Back up your .env file securely."
        Write-Warn "This key encrypts all stored face embeddings."
        Write-Warn "Losing it makes registered face data permanently unreadable."
        Write-Warn "Use the same key from this .env on any additional server."
        Write-Host ""
    }
} else {
    Write-OK "EMBEDDING_ENCRYPTION_KEY already set."
}

# Final check -- key must not be empty at this point
$envRaw = Get-Content $envFile -Raw -Encoding UTF8
if (($envRaw -match '(?m)^EMBEDDING_ENCRYPTION_KEY\s*=\s*$') -or
    ($envRaw -match '(?im)^EMBEDDING_ENCRYPTION_KEY\s*=\s*REPLACE_ME')) {
    Write-Fail "EMBEDDING_ENCRYPTION_KEY is still empty or contains a placeholder in .env."
    Write-Host "         Run: .\.venv\Scripts\python.exe manage.py generate_key" -ForegroundColor Yellow
    Write-Host "         Then paste the output into .env as EMBEDDING_ENCRYPTION_KEY=<value>" -ForegroundColor Yellow
    Read-Host "  Press Enter to exit"
    exit 1
}
Write-OK ".env configuration verified."

# -- Step 8: TLS certificate (idempotent -- skips if cert already exists) -----
Write-Step "8/12" "Checking TLS certificate..."

$stableCert    = Join-Path $projectRoot 'fans-cert.pem'
$stableCertKey = Join-Path $projectRoot 'fans-cert-key.pem'
$certsExist    = (Test-Path $stableCert) -and (Test-Path $stableCertKey)
$mkcertExe     = $null
$lanIp         = $null

# Always try to locate mkcert -- needed for CAROOT path shown in the summary below
if (Test-Path $mkcertBundled) {
    $mkcertExe = $mkcertBundled
} else {
    try {
        $found     = Get-Command mkcert -ErrorAction Stop
        $mkcertExe = $found.Source
    } catch { }
}

if ($certsExist -and -not $ForceRegenerateCert) {
    Write-OK "Existing TLS certificate found -- skipping regeneration"
    Write-Host "         fans-cert.pem and fans-cert-key.pem are present" -ForegroundColor DarkGray
    Write-Host "         Re-run with -ForceRegenerateCert to replace the certificate" -ForegroundColor DarkGray
} else {
    if ($ForceRegenerateCert) {
        Write-Host "         -ForceRegenerateCert specified -- regenerating certificate" -ForegroundColor Yellow
    } else {
        Write-Host "         Certificate not found -- generating now" -ForegroundColor DarkGray
    }

    if (-not $mkcertExe) {
        Write-Fail "mkcert.exe not found."
        Write-Host "         Expected bundled location: $mkcertBundled" -ForegroundColor Yellow
        Write-Host "         Or place mkcert.exe anywhere on your system PATH." -ForegroundColor Yellow
        Write-Host "         Download from: https://github.com/FiloSottile/mkcert/releases" -ForegroundColor Cyan
        Write-Host ""
        Read-Host "  Press Enter to exit"
        exit 1
    }

    Write-OK "mkcert found: $mkcertExe"

    # Install mkcert local CA
    Write-Host "         Installing mkcert local Certificate Authority ..." -ForegroundColor DarkGray
    Write-Host "         (The rootCA.pem from this CA must be copied to each client device.)" -ForegroundColor DarkGray
    try {
        & $mkcertExe -install
        Write-OK "Local CA installed."
    } catch {
        Write-Warn "mkcert -install reported an error. Continuing..."
        Write-Host "         $_" -ForegroundColor DarkGray
    }

    # Detect LAN IP
    Write-Host "         Detecting server LAN IP address ..." -ForegroundColor DarkGray
    try {
        $udp = New-Object System.Net.Sockets.UdpClient
        $udp.Connect('8.8.8.8', 80)
        $lanIp = $udp.Client.LocalEndPoint.Address.ToString()
        $udp.Close()
    } catch { }

    if (-not $lanIp) {
        try {
            $lanIp = (Get-NetIPAddress -AddressFamily IPv4 |
                      Where-Object { $_.IPAddress -notmatch '^127\.' -and
                                     $_.IPAddress -notmatch '^169\.254\.' } |
                      Select-Object -First 1).IPAddress
        } catch { }
    }

    if ($lanIp) {
        Write-OK "LAN IP detected: $lanIp"
    } else {
        Write-Warn "Could not detect LAN IP. Certificate will cover localhost only."
        Write-Warn "Connect the server to the network and re-run to add the LAN IP."
    }

    # Generate certificate
    $certNames = @('fans-barangay.local', 'localhost', '127.0.0.1')
    if ($lanIp) { $certNames = @('fans-barangay.local', $lanIp, 'localhost', '127.0.0.1') }

    $sanCount    = $certNames.Count - 1
    $certFile    = Join-Path $projectRoot "fans-barangay.local+$sanCount.pem"
    $certKeyFile = Join-Path $projectRoot "fans-barangay.local+$sanCount-key.pem"

    Write-Host "         Generating TLS certificate for fans-barangay.local ..." -ForegroundColor DarkGray
    try {
        Push-Location $projectRoot
        & $mkcertExe @certNames
        Pop-Location
    } catch {
        Write-Fail "mkcert certificate generation failed."
        Write-Host "         $_" -ForegroundColor DarkGray
        Read-Host "  Press Enter to exit"
        exit 1
    }

    if (-not (Test-Path $certFile)) {
        Write-Fail "Expected certificate file not found: $certFile"
        Write-Host "         mkcert may have named it differently. Check the project root folder." -ForegroundColor Yellow
        Read-Host "  Press Enter to exit"
        exit 1
    }

    Write-OK "Certificate : $certFile"
    Write-OK "Private key : $certKeyFile"

    Copy-Item $certFile    $stableCert    -Force
    Copy-Item $certKeyFile $stableCertKey -Force
    Write-OK "Stable cert : $stableCert (used by Caddy)"
    Write-OK "Stable key  : $stableCertKey"
}

# -- Step 9: Check Caddyfile --------------------------------------------------
Write-Step "9/12" "Checking Caddyfile..."
if (-not (Test-Path $caddyFile)) {
    Write-Fail "Caddyfile not found in project root."
    Read-Host "  Press Enter to exit"
    exit 1
}

$caddyContent = Get-Content $caddyFile -Raw
if ($caddyContent -notmatch [regex]::Escape('fans-cert.pem')) {
    Write-Warn "Caddyfile does not reference 'fans-cert.pem'."
    Write-Warn "Check the 'tls' line in your Caddyfile."
} else {
    Write-OK "Caddyfile cert reference OK (fans-cert.pem)."
}

# -- Step 10: Django setup (migrations + init config + static files) ----------
Write-Step "10/12" "Running Django setup (migrations, config init, static files)..."

Write-Host "         Running database migrations ..." -ForegroundColor DarkGray
Invoke-Checked $venvPython @('manage.py', 'migrate', '--noinput')
Write-OK "Migrations applied."

Write-Host "         Initialising system configuration ..." -ForegroundColor DarkGray
try {
    & $venvPython manage.py init_config
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "init_config exited with code $LASTEXITCODE (non-fatal)."
    } else {
        Write-OK "System config initialised."
    }
} catch {
    Write-Warn "init_config reported an error (non-fatal):"
    Write-Host "         $_" -ForegroundColor DarkGray
}

if ($SkipStaticFiles) {
    Write-OK "Skipping static files (-SkipStaticFiles)."
} else {
    Write-Host "         Collecting static files ..." -ForegroundColor DarkGray
    try {
        & $venvPython manage.py collectstatic --noinput --clear 2>&1 | Out-Null
        Write-OK "Static files collected."
    } catch {
        Write-Warn "collectstatic reported an error (non-fatal):"
        Write-Host "         $_" -ForegroundColor DarkGray
    }
}

# -- Step 11: Network config (firewall + server hosts file) ------------------
Write-Step "11/12" "Checking network configuration (firewall + server hosts file)..."

# 11a: Windows Firewall rule
$fwRuleName = 'FANS-C Caddy HTTPS'
$existing   = netsh advfirewall firewall show rule name="$fwRuleName" 2>&1
if ($existing -match 'No rules match') {
    try {
        netsh advfirewall firewall add rule `
            name="$fwRuleName" `
            dir=in action=allow protocol=TCP localport=443 | Out-Null
        Write-OK "Firewall rule added (port 443 inbound allowed)."
    } catch {
        Write-Warn "Could not add firewall rule automatically."
        Write-Host "         Run manually as Admin:" -ForegroundColor Yellow
        Write-Host '         netsh advfirewall firewall add rule name="FANS-C Caddy HTTPS" dir=in action=allow protocol=TCP localport=443' -ForegroundColor Cyan
    }
} else {
    Write-OK "Firewall rule already exists."
}

# 11b: Server hosts file -- fans-barangay.local must resolve on the server itself
# Without this entry the browser on the server PC cannot reach https://fans-barangay.local.
$hostsFile    = 'C:\Windows\System32\drivers\etc\hosts'
$hostsContent = Get-Content $hostsFile -Raw -Encoding UTF8 -ErrorAction SilentlyContinue

function Add-HostsEntry {
    param([string]$Ip, [string]$Hostname)
    $marker  = "# FANS-C"
    $newLine = "$Ip`t$Hostname`t$marker"

    # Already has a non-commented entry for this hostname
    if ($script:hostsContent -match "(?m)^[^#\s].*\s+${Hostname}(\s|$)") {
        Write-OK "Hosts file: $Hostname already resolved (entry found)."
        return
    }

    try {
        Add-Content -Path $hostsFile -Value $newLine -Encoding UTF8
        Write-OK "Hosts file: added  $Ip  $Hostname"
        $script:hostsContent += "`n$newLine"
    } catch {
        Write-Warn "Could not write to hosts file: $_"
        Write-Warn "Add manually: $Ip  $Hostname"
    }
}

# Map loopback so the browser on the server PC resolves https://fans-barangay.local.
# Caddy binds all interfaces, so 127.0.0.1:443 reaches it correctly.
# Client devices use the LAN IP instead (handled by trust-local-cert.bat).
Add-HostsEntry -Ip '127.0.0.1' -Hostname 'fans-barangay.local'

# 11c: Auto-fill ALLOWED_HOSTS and CSRF_TRUSTED_ORIGINS in .env so the operator
# does not have to edit the file by hand after setup. We only force-write when
# the existing value is missing or a wildcard ('*'), preserving any custom list
# the operator may have configured intentionally.
$envRaw = Get-Content $envFile -Raw -Encoding UTF8

$allowedHostsList = 'fans-barangay.local,localhost,127.0.0.1'
if ($lanIp) { $allowedHostsList = "fans-barangay.local,$lanIp,localhost,127.0.0.1" }

if ($envRaw -match '(?m)^ALLOWED_HOSTS\s*=\s*\*\s*$' -or
    $envRaw -match '(?m)^ALLOWED_HOSTS\s*=\s*$' -or
    -not ($envRaw -match '(?m)^ALLOWED_HOSTS\s*=')) {
    Set-EnvValueForce $envFile 'ALLOWED_HOSTS' $allowedHostsList
    Write-OK "Wrote ALLOWED_HOSTS=$allowedHostsList"
} else {
    Write-OK "ALLOWED_HOSTS already set in .env (left unchanged)."
}

if (-not ($envRaw -match '(?m)^CSRF_TRUSTED_ORIGINS\s*=\s*\S')) {
    Set-EnvValueForce $envFile 'CSRF_TRUSTED_ORIGINS' 'https://fans-barangay.local'
    Write-OK 'Wrote CSRF_TRUSTED_ORIGINS=https://fans-barangay.local'
}
if (-not ($envRaw -match '(?m)^SECURE_PROXY_SSL_HEADER\s*=\s*\S')) {
    Set-EnvValueForce $envFile 'SECURE_PROXY_SSL_HEADER' 'HTTP_X_FORWARDED_PROTO,https'
    Write-OK 'Wrote SECURE_PROXY_SSL_HEADER for Caddy reverse proxy.'
}
if (-not ($envRaw -match '(?m)^USE_X_FORWARDED_HOST\s*=\s*\S')) {
    Set-EnvValueForce $envFile 'USE_X_FORWARDED_HOST' 'True'
    Write-OK 'Wrote USE_X_FORWARDED_HOST=True for Caddy reverse proxy.'
}

# 11d: Copy mkcert rootCA.pem into CLIENT-SETUP/ so each client install is one-shot
# (no manual file copy from the mkcert CA folder needed).
try {
    $clientSetupDir = Join-Path $projectRoot 'CLIENT-SETUP'
    $caRootDir = ((& $mkcertExe -CAROOT 2>&1) -join '').Trim()
    if ($caRootDir -and (Test-Path $caRootDir)) {
        $srcRootCa = Join-Path $caRootDir 'rootCA.pem'
        if (Test-Path $srcRootCa) {
            $dstRootCa = Join-Path $clientSetupDir 'rootCA.pem'
            if (-not (Test-Path $clientSetupDir)) {
                New-Item -ItemType Directory -Path $clientSetupDir | Out-Null
            }
            Copy-Item -Path $srcRootCa -Destination $dstRootCa -Force
            Write-OK "Copied rootCA.pem -> CLIENT-SETUP\rootCA.pem"
            Write-Host '         Distribute the entire CLIENT-SETUP folder (USB or share) to each client.' -ForegroundColor DarkGray
            Write-Host '         IMPORTANT: do NOT distribute rootCA-key.pem.' -ForegroundColor Yellow
        } else {
            Write-Warn "rootCA.pem not found in $caRootDir; clients will need it copied manually."
        }
    }
} catch {
    Write-Warn "Could not copy rootCA.pem to CLIENT-SETUP\: $_"
}

# -- Step 12: Admin user (skipped automatically if one already exists) --------
Write-Step "12/12" "Checking for admin user..."

if ($SkipAdminCreate) {
    Write-OK "Skipping admin user creation (-SkipAdminCreate)."
} else {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $suCheckRaw = & $venvPython manage.py shell --no-color -c `
        "from django.contrib.auth import get_user_model; U=get_user_model(); print(U.objects.filter(is_superuser=True).count())" `
        2>&1
    $ErrorActionPreference = $prevEAP
    $suCheckStr = (($suCheckRaw | Where-Object { $_ -match '^\d+$' }) -join '').Trim()

    if ($suCheckStr -match '^\d+$' -and [int]$suCheckStr -gt 0) {
        Write-OK "Admin user already exists ($([int]$suCheckStr) superuser(s)) -- skipping"
        Write-Host "         To add another admin: scripts\admin\create-admin-user.ps1" -ForegroundColor DarkGray
    } else {
        Write-Host ""
        Write-Host "         No admin account found. Creating Django admin user..." -ForegroundColor White
        Write-Host "         Django will now prompt for username, email, and password." -ForegroundColor White
        Write-Host "         Choose a strong password and store it securely." -ForegroundColor White
        Write-Host ""
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $venvPython manage.py createsuperuser
        $suExitCode = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        if ($suExitCode -ne 0) {
            Write-Warn "createsuperuser exited with code $suExitCode."
            Write-Warn "Create the admin account later: scripts\admin\create-admin-user.ps1"
        } else {
            Write-OK "Admin user created."
        }
    }
}

# -- Summary ------------------------------------------------------------------
Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "   Setup complete!                                                " -ForegroundColor Green
Write-Host ""
Write-Host "   Certificate files in project root:" -ForegroundColor DarkGray
Write-Host "     $stableCert" -ForegroundColor Cyan
Write-Host "     $stableCertKey" -ForegroundColor Cyan
if ($lanIp) {
    Write-Host ""
    Write-Host "   Server LAN IP detected: $lanIp" -ForegroundColor DarkGray
    Write-Host "   Staff can access the system at:" -ForegroundColor DarkGray
    Write-Host "     https://fans-barangay.local  (requires client hosts setup)" -ForegroundColor Cyan
    Write-Host "     http://${lanIp}:8000          (fallback, no camera)" -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "   Next steps:" -ForegroundColor Yellow
Write-Host ""
Write-Host "     1. Edit .env -- set ALLOWED_HOSTS and CSRF_TRUSTED_ORIGINS" -ForegroundColor Yellow
if ($lanIp) {
    Write-Host "        Example:" -ForegroundColor DarkGray
    Write-Host "          ALLOWED_HOSTS=fans-barangay.local,$lanIp,localhost,127.0.0.1" -ForegroundColor DarkGray
    Write-Host "          CSRF_TRUSTED_ORIGINS=https://fans-barangay.local" -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "     2. Distribute the server root CA to each client device:" -ForegroundColor Yellow
Write-Host "        Run CLIENT-SETUP\trust-local-cert.bat (as Admin) on each client." -ForegroundColor DarkGray

$caRootDir = $null
try {
    $caRootDir = ((& $mkcertExe -CAROOT 2>&1) -join '').Trim()
} catch { }

if ($caRootDir -and (Test-Path $caRootDir)) {
    $rootCaPem = Join-Path $caRootDir 'rootCA.pem'
    Write-Host ""
    Write-Host "        Server root CA file:" -ForegroundColor Yellow
    Write-Host "          $rootCaPem" -ForegroundColor Cyan
    Write-Host "        Copy this file + trust-local-cert.bat to each client device." -ForegroundColor Yellow
} else {
    Write-Host "        Find the server root CA:" -ForegroundColor DarkGray
    Write-Host "          Run: mkcert -CAROOT  (on this server, in any terminal)" -ForegroundColor DarkGray
    Write-Host "          Copy rootCA.pem to each client, then run trust-local-cert.bat." -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "        NOTE: Do NOT run 'mkcert -install' on client devices." -ForegroundColor Yellow
Write-Host "              That creates a different CA that does not trust this server." -ForegroundColor Yellow
Write-Host ""
Write-Host "     3. On each client device: add fans-barangay.local to hosts file" -ForegroundColor Yellow
Write-Host "        (trust-local-cert.bat handles this step too)" -ForegroundColor DarkGray
if ($lanIp) {
    Write-Host "        Entry to add:  $lanIp  fans-barangay.local" -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "     4. Run scripts\setup\setup-autostart.ps1 to enable automatic startup." -ForegroundColor Yellow
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host ""

# -- Optional: start servers --------------------------------------------------
$startNow = Read-Host "  Start the server now? (Waitress + Caddy) [Y/N]"
if ($startNow -match '^[Yy]') {
    Write-Host ""
    Write-Host "  Starting Waitress and Caddy..." -ForegroundColor Cyan

    # Waitress (minimized window)
    $waitressCmd = "Set-Location '$projectRoot'; & '$venvWaitress' --listen=127.0.0.1:8000 fans.wsgi:application; Read-Host 'Waitress stopped. Press Enter'"
    Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $waitressCmd -WindowStyle Minimized

    Start-Sleep -Seconds 4

    # Locate caddy: bundled -> PATH -> fallback
    $caddyExe = $null
    if (Test-Path $caddyBundled) {
        $caddyExe = $caddyBundled
        Write-Host "  Caddy found (bundled): $caddyExe" -ForegroundColor Green
    } else {
        try {
            $found    = Get-Command caddy -ErrorAction Stop
            $caddyExe = $found.Source
            Write-Host "  Caddy found on PATH: $caddyExe" -ForegroundColor Green
        } catch {
            $fallback = 'D:\Tools\caddy.exe'
            if (Test-Path $fallback) {
                $caddyExe = $fallback
                Write-Host "  Caddy found (fallback D:\Tools): $caddyExe" -ForegroundColor Green
            }
        }
    }

    if ($caddyExe) {
        $caddyCmd = "Set-Location '$projectRoot'; & '$caddyExe' run --config Caddyfile; Read-Host 'Caddy stopped. Press Enter'"
        Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $caddyCmd -WindowStyle Minimized
    } else {
        Write-Host "  [WARN] caddy.exe not found. Start Caddy manually." -ForegroundColor Yellow
        Write-Host "         Checked bundled: $caddyBundled" -ForegroundColor Yellow
        Write-Host "         Or place caddy.exe anywhere on your system PATH." -ForegroundColor Yellow
        Write-Host "         Download from: https://caddyserver.com/docs/install" -ForegroundColor Yellow
    }

    Start-Sleep -Seconds 2

    $openBrowser = Read-Host "  Open https://fans-barangay.local in the browser? [Y/N]"
    if ($openBrowser -match '^[Yy]') {
        Start-Process "https://fans-barangay.local"
    }
}

Write-Host ""
Read-Host "  Press Enter to close this window"
