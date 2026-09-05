#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C Repair -- Re-register the auto-start Task Scheduler task.
    Run this if the system does not start automatically after a reboot.

.DESCRIPTION
    This script ONLY re-registers the auto-start scheduled task.
    It does NOT re-run full setup, reinstall dependencies, or change any config.

    Use this when:
      - The system stopped auto-starting after a reboot
      - The auto-start task was accidentally deleted or disabled
      - check-system-health.ps1 shows auto-start task missing or broken
      - You moved the project folder and the task path is stale

    What it does:
      - Re-registers "FANS-C Verification System" in Task Scheduler
      - Trigger: system startup (as SYSTEM, before any user logs in)
      - Optionally starts the system right now without rebooting

.NOTES
    Must be run as Administrator.

.EXAMPLE
    .\scripts\admin\repair-autostart.ps1
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
Write-Host '   FANS-C  |  Repair Auto-Start Task' -ForegroundColor Cyan
Write-Host '   Re-registers Task Scheduler auto-start entry only.' -ForegroundColor DarkGray
Write-Host '   Does NOT re-run full setup or change any configuration.' -ForegroundColor DarkGray
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''

$taskName     = 'FANS-C Verification System'
$hiddenScript = Join-Path $projectRoot 'scripts\start\start-fans-hidden.ps1'

if (-not (Test-Path $hiddenScript)) {
    Write-Host "  [FAIL] start-fans-hidden.ps1 not found: $hiddenScript" -ForegroundColor Red
    Write-Host '         The project folder may have been moved or files deleted.' -ForegroundColor Yellow
    Write-Host '         Verify the project is intact and re-run.' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

Write-Host "  Task name         : $taskName" -ForegroundColor DarkGray
Write-Host "  Hidden launcher   : $hiddenScript" -ForegroundColor DarkGray
Write-Host "  Project root      : $projectRoot" -ForegroundColor DarkGray
Write-Host ''

$psArgs   = "-WindowStyle Hidden -ExecutionPolicy Bypass -NonInteractive -File `"$hiddenScript`""
$action   = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $psArgs -WorkingDirectory $projectRoot
$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit      (New-TimeSpan -Hours 0) `
    -MultipleInstances       IgnoreNew `
    -StartWhenAvailable      `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host '  [INFO] Existing task found -- replacing with updated definition...' -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

try {
    Register-ScheduledTask `
        -TaskName    $taskName `
        -Action      $action `
        -Trigger     $trigger `
        -Settings    $settings `
        -Principal   $principal `
        -Description 'Starts FANS-C (Waitress + Caddy) automatically at system startup. Managed by IT/Admin. Do not delete or disable.' `
        -Force | Out-Null

    Write-Host "  [OK]  Auto-start task registered: '$taskName'" -ForegroundColor Green
    Write-Host '        System will start automatically on next reboot.' -ForegroundColor DarkGray
} catch {
    Write-Host '  [FAIL] Could not register scheduled task.' -ForegroundColor Red
    Write-Host "         Error: $_" -ForegroundColor DarkGray
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

Write-Host ''

$runNow = Read-Host '  Start system now without rebooting? [Y/N]'
if ($runNow -match '^[Yy]') {
    try {
        Start-ScheduledTask -TaskName $taskName
        Write-Host '  [OK]  Task started. Waitress and Caddy are launching in the background.' -ForegroundColor Green
        Write-Host '        Wait ~30 seconds, then open https://fans-barangay.local' -ForegroundColor DarkGray
    } catch {
        Write-Host '  [WARN] Could not trigger task via Task Scheduler.' -ForegroundColor Yellow
        Write-Host "         Error: $_" -ForegroundColor DarkGray
        Write-Host '         Alternative: run scripts\admin\start-now.ps1' -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''
Read-Host '  Press Enter to close'
