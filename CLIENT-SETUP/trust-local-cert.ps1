#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C Client Trust Setup.
    Run this ONCE on each client device (tablet, laptop, PC) that will access
    the FANS-C system over the secure HTTPS address.

.DESCRIPTION
    HOW CERTIFICATE TRUST WORKS IN FANS-C
    ───────────────────────────────────────
    The HTTPS certificate used by FANS-C was generated on the SERVER using
    mkcert. When mkcert generates a certificate, it signs it with a local
    Certificate Authority (CA) that was created on the server machine.

    Client devices need to TRUST that server CA — not generate their own.
    Running "mkcert -install" on a client would create a DIFFERENT CA local
    to that device, which does NOT sign the server's certificate and therefore
    does NOT fix the browser warning.

    The correct trust model:
      Server:  runs mkcert -install → creates the CA that signed the cert
               copies rootCA.pem from its CAROOT folder to client devices
      Clients: import the SERVER's rootCA.pem into Trusted Root Cert Authorities
               (this script does exactly that)

    WHAT THE IT ADMIN MUST DO FIRST (on the server):
      1. Run: mkcert -CAROOT           → this prints the CA folder path
      2. Copy rootCA.pem from that folder to a USB drive
      3. Put rootCA.pem in this CLIENT-SETUP folder (same folder as this script)

    WHAT THIS SCRIPT DOES (on each client device):
      1. Checks for Administrator rights
      2. Locates rootCA.pem — looks in the same folder as this script, or asks
      3. Imports it into Windows Trusted Root Certification Authorities
         (certutil -addstore Root) — this is what makes the browser trust FANS-C
      4. Optionally adds fans-barangay.local to the hosts file
      5. Optionally opens https://fans-barangay.local in the browser

    mkcert.exe does NOT need to be installed on client devices.

    REQUIREMENTS:
      - rootCA.pem (copied from the server) — place next to this script
      - Server LAN IP address (ask your IT admin)
      - Administrator rights

.NOTES
    Easiest way to run:
      Double-click trust-local-cert.bat  (it handles elevation automatically)

    Manual run as Administrator:
      Right-click trust-local-cert.ps1 → "Run with PowerShell"
      then click Yes on the UAC prompt.

.EXAMPLE
    .\trust-local-cert.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── Banner ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "   FANS-C  |  Client Certificate Trust Setup                      " -ForegroundColor Cyan
Write-Host "   Run this ONCE on each computer that needs to access FANS-C.    " -ForegroundColor DarkGray
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host ""
Write-Host "  This installs the SERVER's Certificate Authority so your browser" -ForegroundColor DarkGray
Write-Host "  trusts the FANS-C HTTPS certificate without showing a warning.  " -ForegroundColor DarkGray
Write-Host ""

# ── Step 1: Administrator check ──────────────────────────────────────────────
Write-Host "  [1/3] Checking Administrator rights..." -ForegroundColor Cyan
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host ""
    Write-Host " [FAIL] This script must be run as Administrator." -ForegroundColor Red
    Write-Host "        Use trust-local-cert.bat - it handles this automatically." -ForegroundColor Yellow
    Write-Host "        Or right-click trust-local-cert.ps1 and choose" -ForegroundColor Yellow
    Write-Host "        'Run with PowerShell', then click Yes on the UAC prompt." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

    Write-Host "        Administrator rights confirmed." -ForegroundColor Green
# ── Step 2: Locate and import server root CA ─────────────────────────────────
Write-Host ""
Write-Host "  [2/3] Locating server root CA certificate..." -ForegroundColor Cyan
Write-Host "         (This is the rootCA.pem file copied from the FANS-C server.)" -ForegroundColor DarkGray
Write-Host ""

$scriptDir = $PSScriptRoot
$caCertPath = $null

# Auto-discover: look next to this script for common names the admin may have used.
$candidates = @(
    (Join-Path $scriptDir 'rootCA.pem'),
    (Join-Path $scriptDir 'rootCA.cer'),
    (Join-Path $scriptDir 'rootCA.crt'),
    (Join-Path $scriptDir 'fans-c-rootCA.pem'),
    (Join-Path $scriptDir 'fans-c-rootCA.cer')
)

foreach ($c in $candidates) {
    if (Test-Path $c) {
        $caCertPath = $c
        Write-Host "         Found: $caCertPath" -ForegroundColor Green
        break
    }
}

if (-not $caCertPath) {
    Write-Host "         No root CA file found automatically next to this script." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "         The IT admin must copy rootCA.pem from the server first." -ForegroundColor Yellow
    Write-Host "         On the server, run: mkcert -CAROOT" -ForegroundColor DarkGray
    Write-Host "         Copy 'rootCA.pem' from that folder to this CLIENT-SETUP" -ForegroundColor DarkGray
    Write-Host "         folder, then re-run this script." -ForegroundColor DarkGray
    Write-Host ""
    $manualPath = Read-Host "         Or type the full path to rootCA.pem now (or press Enter to exit)"
    $manualPath = $manualPath.Trim().Trim('"')

    if (-not $manualPath) {
        Write-Host ""
        Write-Host "  [EXIT] No certificate file provided. Exiting." -ForegroundColor Red
        Read-Host "  Press Enter to close"
        exit 1
    }

    if (-not (Test-Path $manualPath)) {
        Write-Host ""
        Write-Host "  [FAIL] File not found: $manualPath" -ForegroundColor Red
        Read-Host "  Press Enter to exit"
        exit 1
    }

    $caCertPath = $manualPath
    Write-Host "         Using: $caCertPath" -ForegroundColor Green
}

# Import into Windows Trusted Root Certification Authorities using certutil.
# certutil -addstore handles both PEM (.pem, .crt) and DER (.cer) format files.
Write-Host ""
Write-Host "         Importing into Trusted Root Certification Authorities..." -ForegroundColor DarkGray

$certutilOutput = certutil -addstore -f Root "$caCertPath" 2>&1
$certutilExit   = $LASTEXITCODE

if ($certutilExit -eq 0) {
    Write-Host "         Certificate imported successfully." -ForegroundColor Green
    Write-Host "         Your browser will now trust the FANS-C HTTPS certificate." -ForegroundColor DarkGray
} else {
    Write-Host ""
    Write-Host "  [FAIL] certutil could not import the certificate." -ForegroundColor Red
    Write-Host "         Exit code: $certutilExit" -ForegroundColor DarkGray
    Write-Host "         Output: $certutilOutput" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "         Possible reasons:" -ForegroundColor Yellow
    Write-Host "           - The file is not a valid CA certificate" -ForegroundColor Yellow
    Write-Host "           - The file was not copied correctly from the server" -ForegroundColor Yellow
    Write-Host ""
    Read-Host "  Press Enter to exit"
    exit 1
}

# ── Step 3: Hosts file ───────────────────────────────────────────────────────
Write-Host ""
Write-Host "  [3/3] Hosts file setup for fans-barangay.local..." -ForegroundColor Cyan
Write-Host ""

$hostsFile = 'C:\Windows\System32\drivers\etc\hosts'
$hostname  = 'fans-barangay.local'

$hostsContent  = Get-Content $hostsFile -Raw -ErrorAction SilentlyContinue
$alreadyMapped = $hostsContent -match "\s$([regex]::Escape($hostname))(\s|$)"

if ($alreadyMapped) {
    Write-Host "         '$hostname' is already in your hosts file." -ForegroundColor Green
    Write-Host "         No change needed." -ForegroundColor DarkGray
} else {
    Write-Host "         '$hostname' is NOT in your hosts file." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "         Adding this entry lets your browser find the FANS-C server" -ForegroundColor DarkGray
    Write-Host "         by name instead of IP address." -ForegroundColor DarkGray
    Write-Host ""
    $serverIp = Read-Host "         Enter the server LAN IP address (e.g. 192.168.1.77)"
    $serverIp = $serverIp.Trim()

    if ($serverIp -match '^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$') {
        $hostsEntry = "`n$serverIp  $hostname`n"
        try {
            Add-Content -Path $hostsFile -Value $hostsEntry -Encoding ASCII
            Write-Host ""
            Write-Host "         Hosts entry added successfully:" -ForegroundColor Green
            Write-Host "           $serverIp  $hostname" -ForegroundColor Cyan
        } catch {
            Write-Host ""
            Write-Host "  [WARN] Could not write to hosts file automatically." -ForegroundColor Yellow
            Write-Host "         Add this line manually to: $hostsFile" -ForegroundColor Yellow
            Write-Host "           $serverIp  $hostname" -ForegroundColor Cyan
        }
    } else {
        Write-Host ""
        Write-Host "  [SKIP] '$serverIp' does not look like a valid IP address." -ForegroundColor Yellow
        Write-Host "         Skipping hosts file update." -ForegroundColor Yellow
        Write-Host "         Add this line manually to: $hostsFile" -ForegroundColor Yellow
        Write-Host "           <server-ip>  $hostname" -ForegroundColor Cyan
    }
}

# ── Done ─────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "   Setup complete!                                                " -ForegroundColor Green
Write-Host ""
Write-Host "   Open this address in your browser:" -ForegroundColor DarkGray
Write-Host "     https://fans-barangay.local" -ForegroundColor Cyan
Write-Host ""
Write-Host "   The browser should show a padlock (no security warning)." -ForegroundColor DarkGray
Write-Host "   The camera will work for face verification." -ForegroundColor DarkGray
Write-Host ""
Write-Host "   If a warning still appears, restart the browser and try again." -ForegroundColor DarkGray
Write-Host "   If problems persist, contact your IT administrator." -ForegroundColor DarkGray
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host ""

$openBrowser = Read-Host "  Open https://fans-barangay.local in the browser now? [Y/N]"
if ($openBrowser -match '^[Yy]') {
    Start-Process "https://fans-barangay.local"
}

Write-Host ""
Read-Host "  Press Enter to close this window"
