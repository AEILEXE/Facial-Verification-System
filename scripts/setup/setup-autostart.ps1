#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C Auto-Start Setup — IT/Admin, run ONCE after setup-secure-server.ps1.
    Registers a Windows Task Scheduler task that starts FANS-C automatically
    every time this PC is turned on, before anyone logs in.

.DESCRIPTION
    After this script runs successfully, the Head Barangay's daily workflow
    becomes:
        1. Turn on the PC.
        2. Open any browser.
        3. Go to https://fans-barangay.local
        4. Log in normally.

    No scripts to run. No windows to manage. No technical steps.

    What this script does:
      - Registers a scheduled task named "FANS-C Verification System"
      - Trigger:  At system startup (fires before any user logs in)
      - Account:  SYSTEM (always elevated — Caddy can bind port 443 silently)
      - Action:   Runs start-fans-hidden.ps1 with no visible window
      - Startup log is written to: logs\fans-startup.log

    REQUIREMENTS:
      - Must be run as Administrator (right-click → Run with PowerShell)
      - setup-secure-server.ps1 must have been completed first
      - start-fans-hidden.ps1 must be in the same project folder

    TO REMOVE the auto-start task later:
      Unregister-ScheduledTask -TaskName "FANS-C Verification System" -Confirm:$false

    TO START MANUALLY without rebooting:
      Right-click the task in Task Scheduler → Run
      — or — double-click start-fans-quiet.bat (shows a status window)

    TO STOP the system:
      Run stop-fans.ps1 (IT/Admin only).

.EXAMPLE
    Right-click scripts\setup\setup-autostart.ps1 → Run with PowerShell
#>

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

# ── Banner ────────────────────────────────────────────────────────────────────
Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   FANS-C  |  Auto-Start Task Scheduler Setup' -ForegroundColor Cyan
Write-Host '   IT/Admin: run once after setup-secure-server.ps1' -ForegroundColor DarkGray
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''

# ── Step 1: Administrator check ───────────────────────────────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host '  [FAIL] This script must be run as Administrator.' -ForegroundColor Red
    Write-Host '         Right-click the file and choose "Run with PowerShell",' -ForegroundColor Yellow
    Write-Host '         then approve the UAC prompt.' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}
Write-Host '  [OK]  Administrator rights confirmed.' -ForegroundColor Green

# ── Step 2: Verify the hidden launcher exists ─────────────────────────────────
$hiddenScript = Join-Path $projectRoot 'scripts\start\start-fans-hidden.ps1'
if (-not (Test-Path $hiddenScript)) {
    Write-Host ''
    Write-Host '  [FAIL] start-fans-hidden.ps1 not found:' -ForegroundColor Red
    Write-Host "         $hiddenScript" -ForegroundColor Yellow
    Write-Host '         Make sure this script is in the FANS-C project folder.' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}
Write-Host "  [OK]  Hidden launcher found: $hiddenScript" -ForegroundColor Green

# ── Step 3: Build the scheduled task ─────────────────────────────────────────
$taskName = 'FANS-C Verification System'

# Action: run PowerShell hidden, executing the hidden launcher
$psArgs = "-WindowStyle Hidden -ExecutionPolicy Bypass -NonInteractive -File `"$hiddenScript`""
$action = New-ScheduledTaskAction `
    -Execute         'powershell.exe' `
    -Argument        $psArgs `
    -WorkingDirectory $projectRoot

# Trigger: at system startup (before any user logs in)
$trigger = New-ScheduledTaskTrigger -AtStartup

# Settings: no time limit, ignore duplicate runs, start even if missed
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit    (New-TimeSpan -Hours 0) `
    -MultipleInstances     IgnoreNew `
    -StartWhenAvailable    `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

# Principal: SYSTEM account — always has privileges to bind port 443,
# runs silently without any UAC prompt or user interaction
$principal = New-ScheduledTaskPrincipal `
    -UserId    'SYSTEM' `
    -LogonType ServiceAccount `
    -RunLevel  Highest

# ── Step 4: Register (replace if already exists) ──────────────────────────────
Write-Host ''
Write-Host "  Registering task: '$taskName'..." -ForegroundColor Cyan

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host '  [INFO] Existing task found — replacing...' -ForegroundColor Yellow
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

    Write-Host "  [OK]  Task registered: '$taskName'" -ForegroundColor Green
} catch {
    Write-Host '  [FAIL] Could not register scheduled task.' -ForegroundColor Red
    Write-Host "         $_" -ForegroundColor DarkGray
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

# ── Step 5: Register daily backup task ────────────────────────────────────────
$backupTaskName = 'FANS-C Daily Backup'
$backupScript   = Join-Path $projectRoot 'scripts\admin\daily-backup.ps1'

Write-Host ''
Write-Host "  Registering task: '$backupTaskName'..." -ForegroundColor Cyan

if (-not (Test-Path $backupScript)) {
    Write-Host '  [WARN] daily-backup.ps1 not found — skipping backup task registration.' -ForegroundColor Yellow
    Write-Host "         Expected: $backupScript" -ForegroundColor DarkGray
} else {
    $backupArgs     = "-WindowStyle Hidden -ExecutionPolicy Bypass -NonInteractive -File `"$backupScript`""
    $backupAction   = New-ScheduledTaskAction `
        -Execute          'powershell.exe' `
        -Argument         $backupArgs `
        -WorkingDirectory $projectRoot
    $backupTrigger  = New-ScheduledTaskTrigger -Daily -At '21:00'
    $backupSettings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit  (New-TimeSpan -Minutes 30) `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries

    # v2.1.16 correction (finding N-03): no pre-unregister step. An existing
    # task is replaced directly by Register-ScheduledTask -Force below -- if
    # that registration call then failed for any reason, unregistering first
    # would have destroyed a known-good existing backup task and left
    # nothing in its place. This matches dev/launcher.py's Daily Backup
    # registration, which was already corrected the same way.
    try {
        Register-ScheduledTask `
            -TaskName    $backupTaskName `
            -Action      $backupAction `
            -Trigger     $backupTrigger `
            -Settings    $backupSettings `
            -Principal   $principal `
            -Description 'Daily hot-backup of db.sqlite3, .env, and media\ after office hours. Keeps last 14 backups. Log: logs\fans-backup.log' `
            -Force | Out-Null
        Write-Host "  [OK]  Task registered: '$backupTaskName' (daily at 21:00, SYSTEM)" -ForegroundColor Green
    } catch {
        Write-Host '  [WARN] Could not register backup task.' -ForegroundColor Yellow
        Write-Host "         $_" -ForegroundColor DarkGray
        Write-Host '         Run setup-autostart.ps1 again after verifying daily-backup.ps1 exists.' -ForegroundColor Yellow
    }
}

# ── Step 6: Optionally run it right now (no reboot needed to test) ────────────
Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   Auto-start configured successfully.' -ForegroundColor Green
Write-Host ''
Write-Host '   Head Barangay daily workflow (from now on):' -ForegroundColor DarkGray
Write-Host '     1. Turn on the PC' -ForegroundColor White
Write-Host '     2. Open any browser' -ForegroundColor White
Write-Host '     3. Go to:  https://fans-barangay.local' -ForegroundColor Cyan
Write-Host '     4. Log in normally' -ForegroundColor White
Write-Host ''
Write-Host '   No startup scripts. No console windows. No technical steps.' -ForegroundColor Green
Write-Host ''
Write-Host '   Task details:' -ForegroundColor DarkGray
Write-Host "     Name:     $taskName" -ForegroundColor DarkGray
Write-Host '     Trigger:  System startup (before login)' -ForegroundColor DarkGray
Write-Host '     Account:  SYSTEM (no UAC prompt)' -ForegroundColor DarkGray
Write-Host "     Script:   $hiddenScript" -ForegroundColor DarkGray
Write-Host '     Log file: logs\fans-startup.log (in project folder)' -ForegroundColor DarkGray
Write-Host ''
Write-Host '   IT/Admin tools:' -ForegroundColor DarkGray
Write-Host '     scripts\admin\stop-fans.ps1        — stop Waitress + Caddy now' -ForegroundColor DarkGray
Write-Host '     scripts\start\start-fans-quiet.bat — manual start with status window' -ForegroundColor DarkGray
Write-Host '     scripts\start\start-fans.bat       — debug start (all windows visible)' -ForegroundColor DarkGray
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''

$runNow = Read-Host '  Start the system now (without rebooting)? [Y/N]'
if ($runNow -match '^[Yy]') {
    Write-Host ''
    Write-Host '  Starting via Task Scheduler...' -ForegroundColor Cyan
    try {
        Start-ScheduledTask -TaskName $taskName
        Write-Host '  [OK]  Task started. Waitress and Caddy are launching in the background.' -ForegroundColor Green
        Write-Host '        Wait about 20 seconds, then open https://fans-barangay.local' -ForegroundColor DarkGray
    } catch {
        Write-Host '  [WARN] Could not start task automatically.' -ForegroundColor Yellow
        Write-Host "         $_" -ForegroundColor DarkGray
        Write-Host '         You can start it manually in Task Scheduler, or reboot.' -ForegroundColor Yellow
    }
}

Write-Host ''
Read-Host '  Press Enter to close'
