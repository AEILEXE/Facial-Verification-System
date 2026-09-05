# ==============================================================================
# uninstall-clean.ps1  --  FANS-C Full Cleanup Script
# ==============================================================================
#
# Use this script for a FULL removal of FANS-C including all app data.
# For a normal / partial uninstall, use the Windows installer uninstaller or
# run cleanup-fansc.ps1 in the project root instead.
#
# Usage (run as Administrator):
#   powershell.exe -ExecutionPolicy Bypass -File scripts\admin\uninstall-clean.ps1
#
# What this script removes:
#   - All running FANS-C processes
#   - Windows Task Scheduler tasks
#   - Desktop and Start Menu shortcuts
#   - The fans-barangay.local hosts file entry
#   - ALL user data: .env, db.sqlite3, media/, logs/, certs (with confirmation)
#   - The entire C:\FANSC install directory (with confirmation)
#   - Optionally: mkcert root CA from Windows trust store (with extra warning)
# ==============================================================================

#Requires -RunAsAdministrator

$INSTALL_DIR = 'C:\FANSC'
$TASK_NAMES  = @('FANS-C Verification System', 'FANS-C Watchdog', 'FANS-C Daily Backup')
$PROCESSES   = @('fans_c', 'FANS-C', 'caddy', 'waitress-serve')

$pass = 0
$fail = 0

function Step-Result($label, $ok, $detail = '') {
    if ($ok) {
        Write-Host "  [PASS] $label" -ForegroundColor Green
        $script:pass++
    } else {
        Write-Host "  [FAIL] $label$(if ($detail) { " -- $detail" })" -ForegroundColor Red
        $script:fail++
    }
}

function Try-Remove($path) {
    if (Test-Path $path) {
        try {
            Remove-Item -Recurse -Force $path -ErrorAction Stop
            Step-Result "Remove: $path" $true
        } catch {
            Step-Result "Remove: $path" $false $_.Exception.Message
        }
    } else {
        Write-Host "  [SKIP] Not found: $path" -ForegroundColor Gray
    }
}

# -- Banner + confirmation -----------------------------------------------------
Write-Host ''
Write-Host '============================================================' -ForegroundColor Red
Write-Host '  FANS-C FULL CLEANUP - This removes ALL app data.' -ForegroundColor Red
Write-Host '============================================================' -ForegroundColor Red
Write-Host ''
Write-Host '  This will permanently remove:' -ForegroundColor Yellow
Write-Host "    • All FANS-C processes"
Write-Host "    • Task Scheduler tasks"
Write-Host "    • Shortcuts"
Write-Host "    • hosts entry for fans-barangay.local"
Write-Host "    • All data in $INSTALL_DIR (database, photos, logs, certs, .env)"
Write-Host ''
Write-Host '  Beneficiary data and captured photos will be PERMANENTLY DELETED.' -ForegroundColor Red
Write-Host ''
$confirm = Read-Host 'Type YES to proceed with full cleanup'
if ($confirm -ne 'YES') {
    Write-Host 'Cancelled. Nothing was changed.' -ForegroundColor Cyan
    exit 0
}
Write-Host ''

# -- 1. Stop processes ---------------------------------------------------------
Write-Host '[ Stopping processes ]' -ForegroundColor Yellow
foreach ($proc in $PROCESSES) {
    $p = Get-Process -Name $proc -ErrorAction SilentlyContinue
    if ($p) {
        try {
            Stop-Process -Name $proc -Force -ErrorAction Stop
            Step-Result "Stop $proc" $true
        } catch {
            Step-Result "Stop $proc" $false $_.Exception.Message
        }
    } else {
        Write-Host "  [SKIP] $proc not running" -ForegroundColor Gray
    }
}
# Kill python only if from install directory
if (Test-Path $INSTALL_DIR) {
    Get-WmiObject Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -like "$INSTALL_DIR*" } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "  [PASS] Stopped python.exe (PID $($_.ProcessId))" -ForegroundColor Green
        }
}
Write-Host ''

# -- 2. Task Scheduler ---------------------------------------------------------
Write-Host '[ Removing Task Scheduler tasks ]' -ForegroundColor Yellow
foreach ($task in $TASK_NAMES) {
    if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) {
        try {
            Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction Stop
            Step-Result "Remove task: $task" $true
        } catch {
            Step-Result "Remove task: $task" $false $_.Exception.Message
        }
    } else {
        Write-Host "  [SKIP] Task not found: $task" -ForegroundColor Gray
    }
}
Write-Host ''

# -- 3. Shortcuts -------------------------------------------------------------
Write-Host '[ Removing shortcuts ]' -ForegroundColor Yellow
@(
    "$env:PUBLIC\Desktop\FANS-C Verification System.lnk",
    "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\FANS-C",
    "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\FANS-C"
) | ForEach-Object { Try-Remove $_ }
Write-Host ''

# -- 4. Hosts file -------------------------------------------------------------
Write-Host '[ Removing hosts file entry ]' -ForegroundColor Yellow
$hostsFile = 'C:\Windows\System32\drivers\etc\hosts'
try {
    $lines = Get-Content $hostsFile -ErrorAction Stop
    $filtered = $lines | Where-Object {
        $_ -notmatch 'fans-barangay\.local' -and $_ -notmatch '# FANS-C'
    }
    if ($filtered.Count -lt $lines.Count) {
        Set-Content -Path $hostsFile -Value ($filtered -join "`n") -Encoding UTF8 -ErrorAction Stop
        Step-Result 'Remove hosts entry (fans-barangay.local)' $true
    } else {
        Write-Host '  [SKIP] No FANS-C entry found in hosts file.' -ForegroundColor Gray
    }
} catch {
    Step-Result 'Remove hosts entry' $false $_.Exception.Message
}
Write-Host ''

# -- 5. mkcert root CA ---------------------------------------------------------
Write-Host '[ mkcert Root CA ]' -ForegroundColor Yellow
Write-Host '  WARNING: This affects ALL certificates trusted via mkcert on this machine.' -ForegroundColor DarkYellow
$removeCA = Read-Host '  Remove mkcert root CA from Windows trust store? [Y/N]'
if ($removeCA -match '^[Yy]') {
    $mkcert = "$INSTALL_DIR\_internal\tools\mkcert\mkcert.exe"
    if (Test-Path $mkcert) {
        try {
            & $mkcert -uninstall 2>&1 | Out-Null
            Step-Result 'Uninstall mkcert root CA' $true
        } catch {
            Step-Result 'Uninstall mkcert root CA' $false $_.Exception.Message
        }
    } else {
        Write-Host '  [SKIP] mkcert.exe not found at expected path.' -ForegroundColor Gray
        Write-Host '  To remove manually: run mkcert.exe -uninstall from a copy.' -ForegroundColor DarkYellow
    }
} else {
    Write-Host '  [SKIP] Root CA kept.' -ForegroundColor Gray
}
Write-Host ''

# -- 6. Remove install directory -----------------------------------------------
Write-Host '[ Removing install directory ]' -ForegroundColor Yellow
if (Test-Path $INSTALL_DIR) {
    Write-Host "  Removing $INSTALL_DIR ..."
    try {
        Remove-Item -Recurse -Force $INSTALL_DIR -ErrorAction Stop
        if (-not (Test-Path $INSTALL_DIR)) {
            Step-Result "Remove $INSTALL_DIR" $true
        } else {
            Step-Result "Remove $INSTALL_DIR" $false 'Some files could not be deleted (locked by another process?)'
            Write-Host "  Tip: Reboot and retry, or delete $INSTALL_DIR manually." -ForegroundColor DarkYellow
        }
    } catch {
        Step-Result "Remove $INSTALL_DIR" $false $_.Exception.Message
    }
} else {
    Write-Host "  [SKIP] $INSTALL_DIR not found." -ForegroundColor Gray
}
Write-Host ''

# -- Summary -------------------------------------------------------------------
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host "  FULL CLEANUP RESULT:  $pass PASS  /  $fail FAIL" -ForegroundColor $(if ($fail -eq 0) { 'Green' } else { 'Red' })
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host ''
if ($fail -gt 0) { exit 1 } else { exit 0 }
