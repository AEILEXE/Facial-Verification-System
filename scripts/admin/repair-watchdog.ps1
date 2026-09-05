#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C Repair -- Re-register the watchdog Task Scheduler task.
    Run this if the self-healing watchdog is not running.

.DESCRIPTION
    This script ONLY re-registers the watchdog scheduled task.
    It does NOT re-run full setup, restart services, or change any config.

    Use this when:
      - check-system-health.ps1 shows watchdog task missing or broken
      - The watchdog task was accidentally deleted or disabled
      - Watchdog is not appearing in Task Scheduler
      - The watchdog log is no longer being written after reboot

    What it does:
      - Re-registers "FANS-C Watchdog" in Task Scheduler
      - Trigger: 150 seconds after system startup (after main task finishes)
      - Runs as SYSTEM with no visible window

.NOTES
    Must be run as Administrator.

.EXAMPLE
    .\scripts\admin\repair-watchdog.ps1
#>

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $projectRoot

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
Write-Host '   FANS-C  |  Repair Watchdog Task' -ForegroundColor Cyan
Write-Host '   Re-registers watchdog self-healing task only.' -ForegroundColor DarkGray
Write-Host '   Does NOT re-run full setup or change any configuration.' -ForegroundColor DarkGray
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''

$watchdogTaskName = 'FANS-C Watchdog'
$watchdogScript   = Join-Path $projectRoot 'scripts\admin\watchdog.ps1'

if (-not (Test-Path $watchdogScript)) {
    Write-Host "  [FAIL] watchdog.ps1 not found: $watchdogScript" -ForegroundColor Red
    Write-Host '         The project folder may have been moved or files deleted.' -ForegroundColor Yellow
    Write-Host '         Verify the project is intact and re-run.' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

Write-Host "  Task name         : $watchdogTaskName" -ForegroundColor DarkGray
Write-Host "  Watchdog script   : $watchdogScript" -ForegroundColor DarkGray
Write-Host "  Project root      : $projectRoot" -ForegroundColor DarkGray
Write-Host "  Log file          : $projectRoot\logs\fans-watchdog.log" -ForegroundColor DarkGray
Write-Host ''

$wdArgs   = "-WindowStyle Hidden -ExecutionPolicy Bypass -NonInteractive -File `"$watchdogScript`""
$wdAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $wdArgs -WorkingDirectory $projectRoot

$wdTrigger       = New-ScheduledTaskTrigger -AtStartup
$wdTrigger.Delay = 'PT150S'

$wdSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit      (New-TimeSpan -Hours 0) `
    -MultipleInstances       IgnoreNew `
    -StartWhenAvailable      `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount            3 `
    -RestartInterval         (New-TimeSpan -Minutes 2)

$wdPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

$existing = Get-ScheduledTask -TaskName $watchdogTaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host '  [INFO] Existing watchdog task found -- replacing...' -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $watchdogTaskName -Confirm:$false
}

try {
    Register-ScheduledTask `
        -TaskName    $watchdogTaskName `
        -Action      $wdAction `
        -Trigger     $wdTrigger `
        -Settings    $wdSettings `
        -Principal   $wdPrincipal `
        -Description 'Self-healing watchdog for FANS-C. Monitors Waitress and Caddy every 45 seconds. Restarts failed services automatically. Managed by IT/Admin. Do not delete or disable.' `
        -Force | Out-Null

    Write-Host "  [OK]  Watchdog task registered: '$watchdogTaskName'" -ForegroundColor Green
    Write-Host '        Starts 150 seconds after every reboot (after main startup task).' -ForegroundColor DarkGray
    Write-Host '        Checks Waitress and Caddy every 45 seconds.' -ForegroundColor DarkGray
} catch {
    Write-Host '  [FAIL] Could not register watchdog task.' -ForegroundColor Red
    Write-Host "         Error: $_" -ForegroundColor DarkGray
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''
Read-Host '  Press Enter to close'
