#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C -- Restore db.sqlite3, .env, and media\ from a verified backup.

.DESCRIPTION
    Wraps docs\BACKUP-RESTORE.md Section 7's manual procedure into one
    reviewable script, adding two safety measures the manual procedure
    did not have:

      1. The chosen backup is verified restore-ready FIRST (reusing
         Test-FansBackupRestoreReady from daily-backup.ps1, exactly like
         verify-backup.ps1) -- a backup that fails this check is never
         restored from, and nothing is touched.
      2. The CURRENT live state (db.sqlite3, .env, media\) is snapshotted
         into backups\pre-restore-<timestamp>\ before anything is
         overwritten, so a bad restore is itself recoverable. That
         directory name intentionally does NOT match the automated backup
         naming scheme (YYYY-MM-DD_HHmm[_N]) and carries no completion
         manifest, so it is never touched by daily-backup.ps1's own
         rotation logic -- it is a permanent, manually-managed safety copy,
         not part of the 14-backup rotation set.

    This script does not modify scripts\admin\daily-backup.ps1 or its
    backup/rotation/lock behavior in any way -- it only reads the
    restore-readiness functions it defines, via dot-sourcing (safe because
    daily-backup.ps1 gates its main routine on
    $MyInvocation.InvocationName).

    Waitress/Caddy are stopped directly (Stop-Process) rather than by
    calling stop-fans.ps1, because that script ends with an interactive
    "Press Enter to close" prompt not suitable for use from another script.
    Starting the system back up is intentionally left to the operator
    (instructions are printed at the end) rather than automated here, to
    keep this script's scope to the data restore itself.

.PARAMETER BackupPath
    Full path to the backup directory to restore from,
    e.g. C:\FANSC\backups\2026-08-28_2100

.EXAMPLE
    .\restore-backup.ps1 -BackupPath "C:\FANSC\backups\2026-08-28_2100"
#>

param(
    [Parameter(Mandatory)][string]$BackupPath
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$dailyBackupScript = Join-Path $PSScriptRoot 'daily-backup.ps1'

Write-Host ''
Write-Host '  ================================================================' -ForegroundColor Red
Write-Host '   FANS-C  |  RESTORE FROM BACKUP' -ForegroundColor Red
Write-Host '   This overwrites the LIVE database, .env, and media folder.' -ForegroundColor Yellow
Write-Host '  ================================================================' -ForegroundColor Red
Write-Host ''

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host '  [FAIL] Must be run as Administrator.' -ForegroundColor Red
    exit 1
}

if (-not (Test-Path -LiteralPath $dailyBackupScript -PathType Leaf)) {
    Write-Host "  [FAIL] Could not find daily-backup.ps1 at $dailyBackupScript -- cannot verify backup before restoring." -ForegroundColor Red
    exit 1
}
. $dailyBackupScript

# -- Step 1: verify the chosen backup is restore-ready BEFORE touching anything ----
if (-not (Test-Path -LiteralPath $BackupPath -PathType Container)) {
    Write-Host "  [FAIL] Backup path not found: $BackupPath" -ForegroundColor Red
    exit 1
}
Write-Host "  [1/5] Verifying backup: $BackupPath" -ForegroundColor DarkGray
$verify = Test-FansBackupRestoreReady -DirectoryPath $BackupPath
if (-not $verify.Ok) {
    Write-Host "  [FAIL] Backup is NOT restore-ready: $($verify.Reason)" -ForegroundColor Red
    Write-Host '         Restore ABORTED. No files were touched.' -ForegroundColor Yellow
    exit 1
}
Write-Host '  [OK]  Backup passed restore-readiness checks.' -ForegroundColor Green

# -- Confirm ------------------------------------------------------------------------
Write-Host ''
Write-Host "  About to restore from: $BackupPath" -ForegroundColor Yellow
Write-Host "  Onto live install:     $projectRoot" -ForegroundColor Yellow
Write-Host '  The current live db.sqlite3, .env, and media\ will be snapshotted first,' -ForegroundColor DarkGray
Write-Host '  then overwritten.' -ForegroundColor DarkGray
Write-Host ''
$confirm = Read-Host '  Type YES to proceed with restore'
if ($confirm -ne 'YES') {
    Write-Host '  Cancelled. Nothing was changed.' -ForegroundColor Cyan
    exit 0
}

# -- Step 2: stop services ------------------------------------------------------------
Write-Host ''
Write-Host '  [2/5] Stopping FANS-C services...' -ForegroundColor DarkGray
Stop-Process -Name 'waitress-serve' -Force -ErrorAction SilentlyContinue
Stop-Process -Name 'caddy'          -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Write-Host '  [OK]  Services stopped (if they were running).' -ForegroundColor Green

# -- Step 3: snapshot current live state -----------------------------------------------
Write-Host ''
Write-Host '  [3/5] Snapshotting current live state before overwrite...' -ForegroundColor DarkGray
$snapshotName = "pre-restore-{0}" -f (Get-Date -Format 'yyyy-MM-dd_HHmmss')
$snapshotDir  = Join-Path (Join-Path $projectRoot 'backups') $snapshotName
New-Item -ItemType Directory -Force -Path $snapshotDir | Out-Null

$liveDb    = Join-Path $projectRoot 'db.sqlite3'
$liveEnv   = Join-Path $projectRoot '.env'
$liveMedia = Join-Path $projectRoot 'media'

if (Test-Path -LiteralPath $liveDb -PathType Leaf) {
    Copy-Item -LiteralPath $liveDb -Destination (Join-Path $snapshotDir 'db.sqlite3') -Force
}
if (Test-Path -LiteralPath $liveEnv -PathType Leaf) {
    Copy-Item -LiteralPath $liveEnv -Destination (Join-Path $snapshotDir '.env') -Force
}
if (Test-Path -LiteralPath $liveMedia -PathType Container) {
    Copy-Item -LiteralPath $liveMedia -Destination (Join-Path $snapshotDir 'media') -Recurse -Force
}
Write-Host "  [OK]  Current state snapshotted to: $snapshotDir" -ForegroundColor Green
Write-Host '        (not part of the automated 14-backup rotation set)' -ForegroundColor DarkGray

# -- Step 4: restore from the chosen backup --------------------------------------------
Write-Host ''
Write-Host '  [4/5] Restoring from backup...' -ForegroundColor DarkGray
try {
    $backupDb    = Join-Path $BackupPath 'db.sqlite3'
    $backupEnv   = Join-Path $BackupPath '.env'
    $backupMedia = Join-Path $BackupPath 'media'

    Copy-Item -LiteralPath $backupDb -Destination $liveDb -Force
    Write-Host '  [OK]  db.sqlite3 restored.' -ForegroundColor Green

    if (Test-Path -LiteralPath $backupEnv -PathType Leaf) {
        Copy-Item -LiteralPath $backupEnv -Destination $liveEnv -Force
        Write-Host '  [OK]  .env restored.' -ForegroundColor Green
    }

    if (Test-Path -LiteralPath $backupMedia -PathType Container) {
        if (Test-Path -LiteralPath $liveMedia -PathType Container) {
            Remove-Item -LiteralPath $liveMedia -Recurse -Force
        }
        Copy-Item -LiteralPath $backupMedia -Destination $liveMedia -Recurse -Force
        Write-Host '  [OK]  media\ restored.' -ForegroundColor Green
    } else {
        Write-Host '  [--]  Backup has no media\ -- live media\ left untouched.' -ForegroundColor DarkGray
    }
} catch {
    Write-Host "  [FAIL] Restore failed partway through: $_" -ForegroundColor Red
    Write-Host "         The pre-restore snapshot is at: $snapshotDir" -ForegroundColor Yellow
    Write-Host '         Restore from it manually if needed.' -ForegroundColor Yellow
    exit 1
}

# -- Step 5: verify the restored database ----------------------------------------------
Write-Host ''
Write-Host '  [5/5] Verifying restored database...' -ForegroundColor DarkGray
$sqlite3Exe = Join-Path $projectRoot 'tools\sqlite3.exe'
if (Test-Path -LiteralPath $sqlite3Exe -PathType Leaf) {
    $postRestore = Test-SqliteIntegrity -Sqlite3Exe $sqlite3Exe -DbPath $liveDb
    if ($postRestore.Ok) {
        Write-Host '  [OK]  PRAGMA integrity_check: ok on the restored database.' -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] Restored database failed integrity check: $($postRestore.Detail)" -ForegroundColor Red
        Write-Host "         The pre-restore snapshot is at: $snapshotDir" -ForegroundColor Yellow
        exit 1
    }
} else {
    Write-Host "  [WARN] sqlite3.exe not found at $sqlite3Exe -- could not re-verify." -ForegroundColor Yellow
}

Write-Host ''
Write-Host '  ================================================================' -ForegroundColor Green
Write-Host '   RESTORE COMPLETE' -ForegroundColor Green
Write-Host '  ================================================================' -ForegroundColor Green
Write-Host ''
Write-Host '  Next steps (manual):' -ForegroundColor White
Write-Host '    1. Start the system: scripts\admin\start-now.ps1 (or reboot)' -ForegroundColor Cyan
Write-Host '    2. Open System Administration -> Database & Backup Status and confirm:' -ForegroundColor Cyan
Write-Host '         - Database: Connected' -ForegroundColor DarkGray
Write-Host '         - Encryption Key: Configured' -ForegroundColor DarkGray
Write-Host '         - Beneficiary record count matches the source' -ForegroundColor DarkGray
Write-Host '    3. Run one test verification with a known beneficiary.' -ForegroundColor Cyan
Write-Host "    4. Pre-restore snapshot kept at: $snapshotDir" -ForegroundColor DarkGray
Write-Host ''
exit 0
