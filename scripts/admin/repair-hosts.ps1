#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C Repair -- Add fans-barangay.local to the server hosts file.
    Run this when https://fans-barangay.local does not load from this PC.

.DESCRIPTION
    Adds a hosts file entry so the browser on this server PC can resolve
    fans-barangay.local to 127.0.0.1 (where Caddy is listening on port 443).

    Without this entry:
      - https://fans-barangay.local times out or shows "site not found"
      - Caddy may be running and port 443 may be open, but the browser
        cannot connect because the hostname does not resolve.

    This script ONLY modifies C:\Windows\System32\drivers\etc\hosts.
    It does NOT restart services, re-run setup, or change any config.

    After running this script:
      - Open https://fans-barangay.local in the browser.
      - If it still does not load, run check-system-health.ps1 for details.

    Client devices need their own hosts entry (different IP -- the server LAN IP).
    Use CLIENT-SETUP\trust-local-cert.bat on each client device.

.NOTES
    Must be run as Administrator (hosts file requires admin to write).

.EXAMPLE
    .\scripts\admin\repair-hosts.ps1
#>

$ErrorActionPreference = 'Continue'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host ''
    Write-Host '  [FAIL] Must be run as Administrator.' -ForegroundColor Red
    Write-Host '         Right-click -> Run with PowerShell, approve UAC.' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   FANS-C  |  Repair Server Hosts File' -ForegroundColor Cyan
Write-Host '   Adds fans-barangay.local -> 127.0.0.1 to this PC only.' -ForegroundColor DarkGray
Write-Host '   Does NOT restart services or change any other configuration.' -ForegroundColor DarkGray
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''

$hostsFile = 'C:\Windows\System32\drivers\etc\hosts'
$hostname  = 'fans-barangay.local'
$marker    = '# FANS-C'

# -- Read current hosts file --------------------------------------------------
if (-not (Test-Path $hostsFile)) {
    Write-Host '  [FAIL] Hosts file not found: ' -ForegroundColor Red -NoNewline
    Write-Host $hostsFile -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

$hostsContent = Get-Content $hostsFile -Raw -Encoding UTF8

# -- Check if entry already exists --------------------------------------------
$alreadyMapped = $hostsContent -match "(?m)^[^#\s].*\s+${hostname}(\s|$)"

if ($alreadyMapped) {
    Write-Host "  [OK] $hostname is already mapped in the hosts file." -ForegroundColor Green
    Write-Host ''

    # Show the matching line(s)
    $lines = Get-Content $hostsFile -Encoding UTF8
    $matches = $lines | Where-Object { $_ -match "(?i)^\s*[^#].*\s+${hostname}(\s|$)" }
    foreach ($line in $matches) {
        Write-Host "       Entry: $line" -ForegroundColor DarkGray
    }

    Write-Host ''
    Write-Host '  If https://fans-barangay.local still does not load:' -ForegroundColor Yellow
    Write-Host '    - Confirm Caddy is running:  scripts\admin\fans-control-center.ps1 -> [4]' -ForegroundColor DarkGray
    Write-Host '    - Run full health check:     scripts\admin\check-system-health.ps1' -ForegroundColor DarkGray
    Write-Host ''
    Read-Host '  Press Enter to close'
    exit 0
}

# -- Add the entry ------------------------------------------------------------
Write-Host "  Adding: 127.0.0.1   $hostname" -ForegroundColor DarkGray
Write-Host ''

$newLine = "127.0.0.1`t$hostname`t$marker"

try {
    # Ensure the file ends with a newline before appending
    $raw = Get-Content $hostsFile -Raw -Encoding UTF8
    if (-not $raw.EndsWith("`n")) {
        Add-Content -Path $hostsFile -Value '' -Encoding UTF8
    }
    Add-Content -Path $hostsFile -Value $newLine -Encoding UTF8
    Write-Host "  [OK] Added:  127.0.0.1   $hostname" -ForegroundColor Green
} catch {
    Write-Host "  [FAIL] Could not write to hosts file: $_" -ForegroundColor Red
    Write-Host '         Try closing any program that may have the file open.' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   Hosts file updated.' -ForegroundColor Green
Write-Host ''
Write-Host '   Next step: open this URL in the browser on this PC:' -ForegroundColor White
Write-Host '     https://fans-barangay.local' -ForegroundColor Cyan
Write-Host ''
Write-Host '   If the site does not load, run check-system-health.ps1' -ForegroundColor DarkGray
Write-Host '   to confirm Caddy and Django are running correctly.' -ForegroundColor DarkGray
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''
Read-Host '  Press Enter to close'
