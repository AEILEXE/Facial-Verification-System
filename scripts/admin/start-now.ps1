#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C -- Start services now without re-running setup.
    Starts Waitress and Caddy in the background immediately.

.DESCRIPTION
    Use this when you need to start the system manually without rebooting
    and without re-running any setup steps.

    Pre-flight checks (read-only, no changes made):
      - waitress-serve.exe exists (.venv must be set up)
      - .env file exists
      - fans-cert.pem exists
      - caddy.exe found (bundled or on PATH)

    If the system is already running, reports current status and exits.

    This does NOT:
      - Re-run setup or install anything
      - Regenerate certificates
      - Prompt for admin user creation
      - Modify Task Scheduler tasks

.NOTES
    Must be run as Administrator (Caddy needs to bind port 443).

.EXAMPLE
    .\scripts\admin\start-now.ps1
#>

$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $projectRoot

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host ''
    Write-Host '  [FAIL] Must be run as Administrator (Caddy needs port 443).' -ForegroundColor Red
    Write-Host '         Right-click -> Run with PowerShell, approve UAC.' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   FANS-C  |  Start System Now' -ForegroundColor Cyan
Write-Host '   Starts Waitress and Caddy without re-running any setup.' -ForegroundColor DarkGray
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''

$venvWaitress  = Join-Path $projectRoot '.venv\Scripts\waitress-serve.exe'
$caddyBundled  = Join-Path $projectRoot 'tools\caddy.exe'
$stableCert    = Join-Path $projectRoot 'fans-cert.pem'
$envFile       = Join-Path $projectRoot '.env'

function Find-CaddyExe {
    if (Test-Path $caddyBundled) { return $caddyBundled }
    try { $f = Get-Command caddy -ErrorAction Stop; return $f.Source } catch {}
    if (Test-Path 'D:\Tools\caddy.exe') { return 'D:\Tools\caddy.exe' }
    return $null
}

function Test-PortOpen {
    param([int]$Port)
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect('127.0.0.1', $Port)
        $tcp.Close()
        return $true
    } catch { return $false }
}

function Test-HttpsProbe {
    param([string]$Uri = 'https://fans-barangay.local/', [int]$TimeoutMs = 6000)
    try {
        [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
        $req         = [System.Net.HttpWebRequest]::Create($Uri)
        $req.Timeout = $TimeoutMs
        $req.Method  = 'GET'
        $resp        = $req.GetResponse()
        $resp.Close()
        return $true
    } catch [System.Net.WebException] {
        if ($null -ne $_.Exception.Response) { return $true }
        return $false
    } catch {
        return $false
    } finally {
        [Net.ServicePointManager]::ServerCertificateValidationCallback = $null
    }
}

function Start-Hidden {
    param([string]$Exe, [string]$Arguments, [string]$WorkDir)
    $psi                  = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName         = $Exe
    $psi.Arguments        = $Arguments
    $psi.WorkingDirectory = $WorkDir
    $psi.CreateNoWindow   = $true
    $psi.UseShellExecute  = $false
    return [System.Diagnostics.Process]::Start($psi)
}

# -- Check if already running --------------------------------------------------
$wRunning = $null -ne (Get-Process 'waitress-serve' -ErrorAction SilentlyContinue)
$cRunning = $null -ne (Get-Process 'caddy'          -ErrorAction SilentlyContinue)

if ($wRunning -and $cRunning) {
    Write-Host '  [OK] System is already running.' -ForegroundColor Green
    $p8000 = Test-PortOpen 8000
    $p443  = Test-PortOpen 443
    Write-Host "       Port 8000 (Waitress) : $(if ($p8000) { 'LISTENING' } else { 'NOT responding' })" -ForegroundColor $(if ($p8000) { 'Green' } else { 'Yellow' })
    Write-Host "       Port 443  (Caddy)    : $(if ($p443)  { 'LISTENING' } else { 'NOT responding' })"  -ForegroundColor $(if ($p443)  { 'Green' } else { 'Yellow' })
    Write-Host ''
    Read-Host '  Press Enter to close'
    exit 0
}

# -- Pre-flight checks ---------------------------------------------------------
Write-Host '  Pre-flight checks...' -ForegroundColor DarkGray
$canStart = $true

if (Test-Path $venvWaitress) {
    Write-Host '  [OK] waitress-serve.exe found.' -ForegroundColor Green
} else {
    Write-Host '  [FAIL] waitress-serve.exe not found.' -ForegroundColor Red
    Write-Host "         Expected: $venvWaitress" -ForegroundColor Yellow
    Write-Host '         Run setup-complete.ps1 first to install Python dependencies.' -ForegroundColor Yellow
    $canStart = $false
}

$caddyExe = Find-CaddyExe
if ($caddyExe) {
    Write-Host "  [OK] Caddy found: $caddyExe" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] caddy.exe not found (checked: $caddyBundled)" -ForegroundColor Red
    $canStart = $false
}

if (Test-Path $stableCert) {
    Write-Host '  [OK] fans-cert.pem found.' -ForegroundColor Green
} else {
    Write-Host '  [FAIL] fans-cert.pem missing -- Caddy cannot start HTTPS.' -ForegroundColor Red
    Write-Host '         Run: setup-complete.ps1 -ForceRegenerateCert' -ForegroundColor Yellow
    $canStart = $false
}

if (Test-Path $envFile) {
    Write-Host '  [OK] .env found.' -ForegroundColor Green
} else {
    Write-Host '  [FAIL] .env missing -- Django cannot start.' -ForegroundColor Red
    Write-Host '         Run setup-complete.ps1 to configure.' -ForegroundColor Yellow
    $canStart = $false
}

if (-not $canStart) {
    Write-Host ''
    Write-Host '  Pre-flight failed. Fix the issues above before starting.' -ForegroundColor Red
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

# -- Stop stale processes ------------------------------------------------------
Write-Host ''
Write-Host '  Stopping any stale processes...' -ForegroundColor DarkGray
Stop-Process -Name 'waitress-serve' -Force -ErrorAction SilentlyContinue
Stop-Process -Name 'caddy'          -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# -- Start Waitress ------------------------------------------------------------
Write-Host '  Starting Waitress (Django) on 127.0.0.1:8000...' -ForegroundColor DarkGray
try {
    $null = Start-Hidden -Exe $venvWaitress `
        -Arguments '--listen=127.0.0.1:8000 fans.wsgi:application' `
        -WorkDir $projectRoot
} catch {
    Write-Host "  [FAIL] Could not start Waitress: $_" -ForegroundColor Red
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

Write-Host '  Waiting for Django to load...' -ForegroundColor DarkGray
Write-Host '  Note: First start after reboot may take up to 90 seconds (FaceNet model).' -ForegroundColor DarkGray
Start-Sleep -Seconds 15

$port8000 = $false
for ($i = 0; $i -lt 4; $i++) {
    if (Test-PortOpen 8000) { $port8000 = $true; break }
    Start-Sleep -Seconds 5
}

if ($port8000) {
    Write-Host '  [OK] Waitress is listening on port 8000.' -ForegroundColor Green
} else {
    Write-Host '  [WARN] Port 8000 not yet responding -- Django may still be loading.' -ForegroundColor Yellow
    Write-Host '         If this persists, run scripts\start\start-fans.bat to see errors.' -ForegroundColor DarkGray
}

# -- Start Caddy ---------------------------------------------------------------
Write-Host '  Starting Caddy (HTTPS) on port 443...' -ForegroundColor DarkGray
try {
    $null = Start-Hidden -Exe $caddyExe -Arguments 'run --config Caddyfile' -WorkDir $projectRoot
} catch {
    Write-Host "  [FAIL] Could not start Caddy: $_" -ForegroundColor Red
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

Start-Sleep -Seconds 8
$port443 = Test-PortOpen 443

if ($port443) {
    Write-Host '  [OK] Caddy is listening on port 443 (HTTPS).' -ForegroundColor Green
} else {
    Write-Host '  [WARN] Port 443 not yet responding.' -ForegroundColor Yellow
    Write-Host '         Run scripts\admin\check-system-health.ps1 if this persists.' -ForegroundColor DarkGray
}

# -- HTTPS end-to-end probe ----------------------------------------------------
$httpsOK = $false
if ($port8000 -and $port443) {
    Write-Host '  Verifying HTTPS end-to-end (fans-barangay.local -> Caddy -> Django)...' -ForegroundColor DarkGray
    Start-Sleep -Seconds 2
    $httpsOK = Test-HttpsProbe
    if ($httpsOK) {
        Write-Host '  [OK] HTTPS verified end-to-end -- https://fans-barangay.local responds.' -ForegroundColor Green
    } else {
        Write-Host '  [WARN] HTTPS probe failed -- Caddy running but not routing to Django.' -ForegroundColor Yellow
        Write-Host '         Likely cause: fans-barangay.local not in server hosts file.' -ForegroundColor DarkGray
        Write-Host '         Fix: run scripts\admin\repair-hosts.ps1 (as Admin)' -ForegroundColor Yellow
    }
}

# -- Summary -------------------------------------------------------------------
Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
if ($port8000 -and $port443 -and $httpsOK) {
    Write-Host '   System started successfully.' -ForegroundColor Green
    Write-Host '   Site: https://fans-barangay.local' -ForegroundColor Cyan
} elseif ($port8000 -and $port443 -and -not $httpsOK) {
    Write-Host '   Waitress and Caddy are up, but HTTPS end-to-end probe failed.' -ForegroundColor Yellow
    Write-Host '   Run scripts\admin\repair-hosts.ps1 (as Admin) then retry.' -ForegroundColor Yellow
} elseif ($port8000) {
    Write-Host '   Waitress OK but HTTPS is not yet available.' -ForegroundColor Yellow
    Write-Host '   Run check-system-health.ps1 for diagnosis.' -ForegroundColor Yellow
} else {
    Write-Host '   Services launched but ports are not yet responding.' -ForegroundColor Yellow
    Write-Host '   Wait 60 seconds and run check-system-health.ps1.' -ForegroundColor Yellow
}
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''
Read-Host '  Press Enter to close'
