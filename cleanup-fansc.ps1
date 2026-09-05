# ==============================================================================
# cleanup-fansc.ps1  --  FANS-C Normal Uninstall / Full Cleanup Helper
# ==============================================================================
#
# Usage (run as Administrator):
#   powershell.exe -ExecutionPolicy Bypass -File cleanup-fansc.ps1
#
# What this script does:
#   1. Stops all FANS-C processes (fans_c.exe, caddy.exe, waitress-serve.exe)
#   2. Removes Windows Task Scheduler tasks created by FANS-C setup
#   3. Removes Desktop and Start Menu shortcuts
#   4. Optionally removes the hosts file entry for fans-barangay.local
#   5. Asks whether to remove USER DATA (db, media, .env, certs, logs)
#   6. Optionally removes the entire C:\FANSC install folder
#   7. Reports PASS / FAIL per section
#
# For the full cleanup script see: scripts\admin\uninstall-clean.ps1
# ==============================================================================

#Requires -RunAsAdministrator

$INSTALL_DIR = 'C:\FANSC'
$TASK_NAMES  = @('FANS-C Verification System', 'FANS-C Watchdog')
$PROCESSES   = @('fans_c', 'FANS-C', 'caddy', 'waitress-serve')

$pass  = 0
$fail  = 0
$warns = @()

function Step-Result($label, $ok, $detail='') {
    if ($ok) {
        Write-Host "  [PASS] $label" -ForegroundColor Green
        $script:pass++
    } else {
        Write-Host "  [FAIL] $label$(if($detail){" -- $detail"})" -ForegroundColor Red
        $script:fail++
    }
}

Write-Host ''
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host '  FANS-C Cleanup / Uninstall Helper' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host ''

# ── 1. Stop processes ─────────────────────────────────────────────────────────
Write-Host '[ Stopping FANS-C processes ]' -ForegroundColor Yellow
foreach ($proc in $PROCESSES) {
    $running = Get-Process -Name $proc -ErrorAction SilentlyContinue
    if ($running) {
        try {
            Stop-Process -Name $proc -Force -ErrorAction Stop
            Step-Result "Stop $proc" $true
        } catch {
            Step-Result "Stop $proc" $false $_.Exception.Message
        }
    } else {
        Write-Host "  [SKIP] $proc is not running" -ForegroundColor Gray
    }
}

# Also kill python/pythonw ONLY if launched from the install directory
if (Test-Path $INSTALL_DIR) {
    Get-WmiObject Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -like "$INSTALL_DIR*" } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "  [PASS] Stopped python.exe (PID $($_.ProcessId)) from install dir" -ForegroundColor Green
        }
}
Write-Host ''

# ── 2. Remove Task Scheduler tasks ────────────────────────────────────────────
Write-Host '[ Removing Task Scheduler tasks ]' -ForegroundColor Yellow
foreach ($task in $TASK_NAMES) {
    $exists = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    if ($exists) {
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

# ── 3. Remove shortcuts ───────────────────────────────────────────────────────
Write-Host '[ Removing shortcuts ]' -ForegroundColor Yellow
$shortcutPaths = @(
    "$env:PUBLIC\Desktop\FANS-C Verification System.lnk",
    "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\FANS-C",
    "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\FANS-C"
)
$anyShortcut = $false
foreach ($path in $shortcutPaths) {
    if (Test-Path $path) {
        $anyShortcut = $true
        try {
            Remove-Item -Recurse -Force $path -ErrorAction Stop
            Step-Result "Remove shortcut: $path" $true
        } catch {
            Step-Result "Remove shortcut: $path" $false $_.Exception.Message
        }
    }
}
if (-not $anyShortcut) {
    Write-Host "  [SKIP] No FANS-C shortcuts found" -ForegroundColor Gray
}
Write-Host ''

# ── 4. Hosts file entry ───────────────────────────────────────────────────────
Write-Host '[ Hosts file ]' -ForegroundColor Yellow
$hostsFile = 'C:\Windows\System32\drivers\etc\hosts'
$hostsContent = Get-Content $hostsFile -Raw -ErrorAction SilentlyContinue
if ($hostsContent -match 'fans-barangay\.local') {
    $removeHosts = Read-Host '  Remove fans-barangay.local from hosts file? [Y/N]'
    if ($removeHosts -match '^[Yy]') {
        try {
            $newContent = ($hostsContent -split "`n" |
                Where-Object { $_ -notmatch 'fans-barangay\.local' -and $_ -notmatch '# FANS-C' }) -join "`n"
            Set-Content -Path $hostsFile -Value $newContent -Encoding UTF8 -ErrorAction Stop
            Step-Result 'Remove hosts entry' $true
        } catch {
            Step-Result 'Remove hosts entry' $false $_.Exception.Message
        }
    } else {
        Write-Host '  [SKIP] Hosts entry kept.' -ForegroundColor Gray
    }
} else {
    Write-Host '  [SKIP] No fans-barangay.local entry found in hosts file.' -ForegroundColor Gray
}
Write-Host ''

# ── 5. User data (db, media, .env, certs, logs) ───────────────────────────────
Write-Host '[ User data (database, media, logs, .env, certificates) ]' -ForegroundColor Yellow
Write-Host '  Normal uninstall PRESERVES user data by default.' -ForegroundColor Cyan
$deleteData = Read-Host '  Delete user data (.env, db.sqlite3, media, logs, certs)? [Y/N]'
if ($deleteData -match '^[Yy]') {
    $dataItems = @('.env', 'db.sqlite3', 'media', 'logs', 'fans-cert.pem', 'fans-cert-key.pem')
    foreach ($item in $dataItems) {
        $itemPath = Join-Path $INSTALL_DIR $item
        if (Test-Path $itemPath) {
            try {
                Remove-Item -Recurse -Force $itemPath -ErrorAction Stop
                Step-Result "Remove $item" $true
            } catch {
                Step-Result "Remove $item" $false $_.Exception.Message
            }
        } else {
            Write-Host "  [SKIP] $item not found" -ForegroundColor Gray
        }
    }
} else {
    Write-Host '  [SKIP] User data preserved.' -ForegroundColor Gray
}
Write-Host ''

# ── 6. mkcert root CA ─────────────────────────────────────────────────────────
Write-Host '[ mkcert Root CA ]' -ForegroundColor Yellow
Write-Host '  WARNING: Removing the mkcert root CA affects ALL certificates trusted by mkcert.' -ForegroundColor DarkYellow
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
        Write-Host '  [SKIP] mkcert.exe not found — CA not removed.' -ForegroundColor Gray
        $warns += 'mkcert not found; remove CA manually if needed.'
    }
} else {
    Write-Host '  [SKIP] mkcert root CA kept in trust store.' -ForegroundColor Gray
}
Write-Host ''

# ── 7. Install folder ─────────────────────────────────────────────────────────
Write-Host '[ Install folder ]' -ForegroundColor Yellow
if (Test-Path $INSTALL_DIR) {
    Write-Host "  Install folder: $INSTALL_DIR"
    $removeDir = Read-Host "  Remove $INSTALL_DIR and all remaining contents? [Y/N]"
    if ($removeDir -match '^[Yy]') {
        try {
            Remove-Item -Recurse -Force $INSTALL_DIR -ErrorAction Stop
            if (-not (Test-Path $INSTALL_DIR)) {
                Step-Result "Remove $INSTALL_DIR" $true
            } else {
                Step-Result "Remove $INSTALL_DIR" $false 'Folder still exists (locked files?)'
            }
        } catch {
            Step-Result "Remove $INSTALL_DIR" $false $_.Exception.Message
        }
    } else {
        Write-Host "  [SKIP] $INSTALL_DIR kept." -ForegroundColor Gray
    }
} else {
    Write-Host "  [SKIP] $INSTALL_DIR does not exist." -ForegroundColor Gray
}
Write-Host ''

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host "  CLEANUP RESULT:  $pass PASS  /  $fail FAIL" -ForegroundColor $(if ($fail -eq 0) { 'Green' } else { 'Red' })
if ($warns.Count -gt 0) {
    Write-Host '  Warnings:' -ForegroundColor DarkYellow
    $warns | ForEach-Object { Write-Host "    - $_" -ForegroundColor DarkYellow }
}
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host ''
if ($fail -gt 0) { exit 1 } else { exit 0 }
