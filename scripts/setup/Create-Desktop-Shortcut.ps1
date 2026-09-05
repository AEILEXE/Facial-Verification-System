#Requires -Version 5.1
<#
.SYNOPSIS
    Creates a branded FANS-C desktop shortcut for the daily launcher.

.DESCRIPTION
    Run this ONCE after setup to place a clean, named shortcut on the
    Desktop that points to start-fans-quiet.bat with the correct working
    directory (required for the launcher to find its files).

    If  assets\logo.ico  exists in the project folder, the shortcut
    will use that icon.  Otherwise it uses the default Windows icon.

    To update the icon later: place logo.ico in assets\ and re-run
    this script.

.NOTES
    Run from the project root folder, or use:
        powershell -ExecutionPolicy Bypass -File Create-Desktop-Shortcut.ps1
#>

$ErrorActionPreference = 'Stop'

# ── Paths ─────────────────────────────────────────────────────────────────────

$projectRoot  = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$launcherPath = Join-Path $projectRoot 'scripts\start\start-fans-quiet.bat'
$iconPath     = Join-Path $projectRoot 'assets\logo.ico'
$shortcutName = 'FANS-C Verification System.lnk'
$shortcutPath = Join-Path ([System.Environment]::GetFolderPath('Desktop')) $shortcutName

# ── Banner ────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "   FANS-C  |  Create Desktop Shortcut" -ForegroundColor Cyan
Write-Host "   Places a clean launcher icon on the Desktop." -ForegroundColor DarkGray
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host ""

# ── Validate ──────────────────────────────────────────────────────────────────

if (-not (Test-Path $launcherPath)) {
    Write-Host "  [FAIL] Launcher not found: $launcherPath" -ForegroundColor Red
    Write-Host ""
    Write-Host "         Make sure this script is run from the FANS-C project folder." -ForegroundColor Yellow
    Write-Host "         The file start-fans-quiet.bat must be in the same folder." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "  Press Enter to exit"
    exit 1
}

# ── Create shortcut ───────────────────────────────────────────────────────────

Write-Host "  Creating shortcut..." -ForegroundColor Cyan
Write-Host "    Name:    $shortcutName" -ForegroundColor DarkGray
Write-Host "    Target:  $launcherPath" -ForegroundColor DarkGray
Write-Host "    Folder:  $projectRoot" -ForegroundColor DarkGray

$shell    = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)

$shortcut.TargetPath       = $launcherPath
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description      = 'Start FANS-C Barangay Senior Citizen Verification System'
$shortcut.WindowStyle      = 1  # Normal window

$usingCustomIcon = Test-Path $iconPath

if ($usingCustomIcon) {
    $shortcut.IconLocation = "$iconPath,0"
    Write-Host "    Icon:    $iconPath" -ForegroundColor Green
} else {
    Write-Host "    Icon:    (default)" -ForegroundColor Yellow
}

$shortcut.Save()

# ── Done ──────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "   Desktop shortcut created successfully." -ForegroundColor Green

if ($usingCustomIcon) {
    Write-Host "   Using custom system logo." -ForegroundColor Green
} else {
    Write-Host "   No logo found, using default icon." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "   TIP: Place your logo as  assets\logo.ico  and re-run" -ForegroundColor DarkGray
    Write-Host "        this script to apply the branded icon." -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "   Double-click  'FANS-C Verification System'  each morning" -ForegroundColor DarkGray
Write-Host "   to start the system.  Staff can then open the browser and" -ForegroundColor DarkGray
Write-Host "   go to:  https://fans-barangay.local" -ForegroundColor Cyan
Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host ""
Read-Host "  Press Enter to close"
