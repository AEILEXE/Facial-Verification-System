#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C Stop Script - IT/Admin use only.
    Stops the Waitress and Caddy background processes.

.DESCRIPTION
    Use this script when you need to stop the FANS-C system - for example,
    before a maintenance reboot, or to restart the services after a config change.

    The Head Barangay does not need to run this. The system starts automatically
    at boot via Task Scheduler and stops automatically when the PC shuts down.

    What this script does:
      - Stops any running "waitress-serve" process (Django WSGI server)
      - Stops any running "caddy" process (HTTPS reverse proxy)
      - Cleans up PID tracking files

    The auto-start Task Scheduler task is NOT removed. The services will
    start again automatically the next time the PC boots.

    TO RESTART without rebooting:
      Run this script, then run start-fans-quiet.bat (shows a status window)
      - or - go to Task Scheduler and right-click the task -> Run.

    TO REMOVE auto-start permanently:
      Unregister-ScheduledTask -TaskName "FANS-C Verification System" -Confirm:$false

.EXAMPLE
    Right-click stop-fans.ps1 -> Run with PowerShell
#>

$ErrorActionPreference = 'SilentlyContinue'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   FANS-C  |  Stop Services' -ForegroundColor Cyan
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''

$stopped = 0

# -- Stop Waitress (Django WSGI server) ----------------------------------------
$procs = Get-Process -Name 'waitress-serve' -ErrorAction SilentlyContinue
if ($procs) {
    $procs | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host '  [OK]  Waitress stopped.' -ForegroundColor Green
    $stopped++
} else {
    Write-Host '  [--]  Waitress was not running.' -ForegroundColor DarkGray
}

# -- Stop Caddy (HTTPS reverse proxy) ------------------------------------------
$procs = Get-Process -Name 'caddy' -ErrorAction SilentlyContinue
if ($procs) {
    $procs | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host '  [OK]  Caddy stopped.' -ForegroundColor Green
    $stopped++
} else {
    Write-Host '  [--]  Caddy was not running.' -ForegroundColor DarkGray
}

# -- Clean up PID files --------------------------------------------------------
Remove-Item (Join-Path $projectRoot '.fans-waitress.pid') -ErrorAction SilentlyContinue
Remove-Item (Join-Path $projectRoot '.fans-caddy.pid')    -ErrorAction SilentlyContinue

# -- Summary -------------------------------------------------------------------
Write-Host ''
if ($stopped -gt 0) {
    Write-Host "  $stopped service(s) stopped. FANS-C is offline." -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  The system will start again automatically at the next reboot.' -ForegroundColor DarkGray
    Write-Host '  To restart now without rebooting:' -ForegroundColor DarkGray
    Write-Host '    - Double-click scripts\start\start-fans-quiet.bat' -ForegroundColor Cyan
    Write-Host '    - Or: Task Scheduler -> FANS-C Verification System -> Run' -ForegroundColor Cyan
} else {
    Write-Host '  No FANS-C services were found running.' -ForegroundColor DarkGray
}
Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''
Read-Host '  Press Enter to close'
