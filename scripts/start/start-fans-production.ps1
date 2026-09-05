#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C Production Startup Script.
    Starts Waitress (Django) and Caddy (HTTPS) as separate visible processes.

.DESCRIPTION
    Use this script to bring the FANS-C system online after a server reboot,
    without opening VS Code or typing commands manually.

    This script:
      - Runs pre-flight checks (.venv, .env, certificates, encryption key)
      - Starts Waitress in a new PowerShell window (Django WSGI server)
      - Waits for Waitress to initialize
      - Starts Caddy in a new PowerShell window (HTTPS reverse proxy)

    Requirements:
      - setup-secure-server.ps1 must have been run at least once on this machine
      - caddy.exe must be on the system PATH (or in this folder)
      - mkcert TLS certificates must exist in the project root
      - .env must be configured for production (DEBUG=False, etc.)

.NOTES
    Run this from the project root folder: D:\FANS\fans-c\
    Do not run as a different working directory — Caddy resolves cert
    paths relative to its working directory.

.EXAMPLE
    .\start-fans-production.ps1

    To allow this script to run if execution policy blocks it:
    Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $projectRoot

$venvWaitress = Join-Path $projectRoot '.venv\Scripts\waitress-serve.exe'
$envFile      = Join-Path $projectRoot '.env'
$caddyFile    = Join-Path $projectRoot 'Caddyfile'
$certFile     = Join-Path $projectRoot 'fans-cert.pem'
$caddyBundled = Join-Path $projectRoot 'tools\caddy.exe'

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "   FANS-C  |  FaceNet Facial Verification System                 " -ForegroundColor Cyan
Write-Host "   Starting Production Server (Waitress + Caddy)...              " -ForegroundColor Cyan
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host ""

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
$passed = $true

# 1. .venv / waitress
if (-not (Test-Path $venvWaitress)) {
    Write-Host "  [FAIL] waitress-serve.exe not found at:" -ForegroundColor Red
    Write-Host "         $venvWaitress" -ForegroundColor Red
    Write-Host "         Run .\setup-secure-server.ps1 first to set up the virtual environment." -ForegroundColor Yellow
    $passed = $false
}

# 2. .env
if (-not (Test-Path $envFile)) {
    Write-Host "  [FAIL] .env not found." -ForegroundColor Red
    Write-Host "         Copy .env.example to .env and run .\setup-secure-server.ps1." -ForegroundColor Yellow
    $passed = $false
}

if (-not $passed) {
    Write-Host ""
    Write-Host "  Setup is incomplete. Resolve the issues above, then retry." -ForegroundColor Yellow
    Read-Host "  Press Enter to exit"
    exit 1
}

# Read .env for additional checks
$envRaw = Get-Content $envFile -Raw -Encoding UTF8

# 3. EMBEDDING_ENCRYPTION_KEY
if ($envRaw -match '(?m)^EMBEDDING_ENCRYPTION_KEY\s*=\s*$') {
    Write-Host "  [FAIL] EMBEDDING_ENCRYPTION_KEY is empty in .env" -ForegroundColor Red
    Write-Host "         Without this key, face embeddings cannot be decrypted." -ForegroundColor Yellow
    Write-Host "         Run: .\.venv\Scripts\python.exe manage.py generate_key" -ForegroundColor Cyan
    Write-Host "         Then paste the output into .env" -ForegroundColor Yellow
    Read-Host "  Press Enter to exit"
    exit 1
}

# 4. Caddyfile
if (-not (Test-Path $caddyFile)) {
    Write-Host "  [FAIL] Caddyfile not found in project root." -ForegroundColor Red
    Write-Host "         Cannot start Caddy without it." -ForegroundColor Yellow
    Read-Host "  Press Enter to exit"
    exit 1
}

# 5. TLS certificate (warning only — Caddy will produce its own error)
if (-not (Test-Path $certFile)) {
    Write-Host "  [WARN] TLS certificate not found: fans-cert.pem" -ForegroundColor Yellow
    Write-Host "         Caddy may fail to start." -ForegroundColor Yellow
    Write-Host "         Run setup-secure-server.ps1 to regenerate." -ForegroundColor Yellow
    Write-Host "         Continuing anyway..." -ForegroundColor DarkGray
    Write-Host ""
}

# 6. Locate caddy.exe: bundled → PATH → fallback D:\Tools\caddy.exe
$caddyExe = $null
if (Test-Path $caddyBundled) {
    $caddyExe = $caddyBundled
    Write-Host "  [OK]  Caddy found (bundled): $caddyExe" -ForegroundColor Green
} else {
    try {
        $found = Get-Command caddy -ErrorAction Stop
        $caddyExe = $found.Source
        Write-Host "  [OK]  Caddy found on PATH: $caddyExe" -ForegroundColor Green
    } catch {
        $fallback = 'D:\Tools\caddy.exe'
        if (Test-Path $fallback) {
            $caddyExe = $fallback
            Write-Host "  [OK]  Caddy found (fallback D:\Tools): $caddyExe" -ForegroundColor Green
        }
    }
}

if (-not $caddyExe) {
    Write-Host "  [FAIL] caddy.exe not found." -ForegroundColor Red
    Write-Host "         Checked:" -ForegroundColor Yellow
    Write-Host "           - $caddyBundled  (bundled)" -ForegroundColor Yellow
    Write-Host "           - D:\Tools\caddy.exe  (custom path)" -ForegroundColor Yellow
    Write-Host "           - System PATH" -ForegroundColor Yellow
    Write-Host "         Place caddy.exe in the project's tools\ folder for automatic detection." -ForegroundColor Yellow
    Write-Host "         Download from: https://caddyserver.com/docs/install" -ForegroundColor Yellow
    Read-Host "  Press Enter to exit"
    exit 1
}

Write-Host "  [OK]  All pre-flight checks passed." -ForegroundColor Green
Write-Host ""

# ---------------------------------------------------------------------------
# Start Waitress in a new PowerShell window
# ---------------------------------------------------------------------------
Write-Host "  [1/2] Starting Waitress (Django WSGI server)..." -ForegroundColor Cyan

$waitressCmd = @"
`$host.UI.RawUI.WindowTitle = 'FANS-C Waitress'
Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   FANS-C Waitress -- Django App Server                          ' -ForegroundColor Cyan
Write-Host '   DO NOT CLOSE THIS WINDOW while the system is in use.          ' -ForegroundColor Yellow
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''
Set-Location '$projectRoot'
& '$venvWaitress' --listen=127.0.0.1:8000 fans.wsgi:application
Write-Host ''
Write-Host '  Waitress has stopped. Press Enter to close.' -ForegroundColor Red
Read-Host
"@

Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $waitressCmd

# Give Waitress time to initialize before starting Caddy
Write-Host "  [..] Waiting 5 seconds for Waitress to initialize..." -ForegroundColor DarkGray
Start-Sleep -Seconds 5

# ---------------------------------------------------------------------------
# Start Caddy in a new PowerShell window
# ---------------------------------------------------------------------------
Write-Host "  [2/2] Starting Caddy (HTTPS reverse proxy)..." -ForegroundColor Cyan

$caddyCmd = @"
`$host.UI.RawUI.WindowTitle = 'FANS-C Caddy'
Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   FANS-C Caddy -- HTTPS Reverse Proxy                           ' -ForegroundColor Cyan
Write-Host '   DO NOT CLOSE THIS WINDOW while the system is in use.          ' -ForegroundColor Yellow
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''
Set-Location '$projectRoot'
& '$caddyExe' run --config Caddyfile
Write-Host ''
Write-Host '  Caddy has stopped. Press Enter to close.' -ForegroundColor Red
Read-Host
"@

Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $caddyCmd

# ---------------------------------------------------------------------------
# Detect LAN IP (for staff connection guidance)
# ---------------------------------------------------------------------------
$lanIp = $null
try {
    $udp = New-Object System.Net.Sockets.UdpClient
    $udp.Connect('8.8.8.8', 80)
    $lanIp = $udp.Client.LocalEndPoint.Address.ToString()
    $udp.Close()
} catch {
    try {
        $lanIp = (Get-NetIPAddress -AddressFamily IPv4 |
                  Where-Object { $_.IPAddress -notmatch '^127\.' -and $_.IPAddress -notmatch '^169\.254\.' } |
                  Select-Object -First 1).IPAddress
    } catch { }
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "   Both servers are starting in their own windows.               " -ForegroundColor Green
Write-Host ""
Write-Host "   SECURE ACCESS (Recommended):" -ForegroundColor Green
Write-Host "     https://fans-barangay.local" -ForegroundColor Cyan
Write-Host "     Camera enabled · Full features · Requires hosts file setup  " -ForegroundColor DarkGray
Write-Host ""
Write-Host "   FALLBACK ACCESS (No setup needed):" -ForegroundColor Yellow
if ($lanIp) {
    Write-Host "     http://$lanIp`:8000" -ForegroundColor Cyan
    Write-Host "     Works immediately · No hosts file · Camera disabled        " -ForegroundColor DarkGray
} else {
    Write-Host "     (LAN IP not detected -- connect server to the network)     " -ForegroundColor DarkYellow
}
Write-Host ""
Write-Host "   Server only (this device): http://127.0.0.1:8000             " -ForegroundColor DarkGray
Write-Host ""
if ($lanIp) {
    Write-Host "   TIP: Give staff the FALLBACK URL to connect right away.      " -ForegroundColor Yellow
    Write-Host "        Switch to SECURE ACCESS once hosts file is set up.      " -ForegroundColor Yellow
} else {
    Write-Host "   TIP: Connect this server to the network, then restart to     " -ForegroundColor Yellow
    Write-Host "        get the Fallback URL for staff devices.                 " -ForegroundColor Yellow
}
Write-Host ""
Write-Host "   Internet is NOT required for normal operation.                 " -ForegroundColor DarkGray
Write-Host "   Connection help: https://fans-barangay.local/help/connect/    " -ForegroundColor DarkGray
Write-Host ""
Write-Host "   To stop: Close both the Waitress and Caddy windows.            " -ForegroundColor DarkGray
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host ""
Read-Host "  Press Enter to close this launcher window"
