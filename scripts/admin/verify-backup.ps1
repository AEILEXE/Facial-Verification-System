#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C -- Verify that a specific backup directory is restore-ready.

.DESCRIPTION
    Read-only check. Reuses the exact restore-readiness predicate that
    scripts\admin\daily-backup.ps1's own rotation logic and the System
    Health page use (Test-FansBackupRestoreReady) -- this script does not
    reimplement that logic, it dot-sources daily-backup.ps1 for it.
    daily-backup.ps1 is designed to be safely dot-sourced: when
    $MyInvocation.InvocationName is '.', it only defines functions and
    performs no backup/rotation/lock side effects at all.

    Optionally re-runs PRAGMA integrity_check live against the backup's
    db.sqlite3 (-Deep) for maximum assurance before a restore -- the
    nightly automated check only ever runs once, at backup-creation time;
    this lets an operator re-confirm immediately before trusting a backup
    with a real restore.

.PARAMETER BackupPath
    Full path to a backup directory, e.g. C:\FANSC\backups\2026-08-28_2100

.PARAMETER Deep
    Also re-run PRAGMA integrity_check live against the backup's db.sqlite3.
    Slower (must open/scan the whole database) but authoritative.

.EXAMPLE
    .\verify-backup.ps1 -BackupPath "C:\FANSC\backups\2026-08-28_2100"
    .\verify-backup.ps1 -BackupPath "C:\FANSC\backups\2026-08-28_2100" -Deep
#>

param(
    [Parameter(Mandatory)][string]$BackupPath,
    [switch]$Deep
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$dailyBackupScript = Join-Path $PSScriptRoot 'daily-backup.ps1'

Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   FANS-C  |  Backup Verification' -ForegroundColor Cyan
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''

if (-not (Test-Path -LiteralPath $dailyBackupScript -PathType Leaf)) {
    Write-Host "  [FAIL] Could not find daily-backup.ps1 at $dailyBackupScript -- cannot verify." -ForegroundColor Red
    exit 1
}

# Dot-source for the reusable functions only. daily-backup.ps1 gates its main
# backup routine on $MyInvocation.InvocationName -- dot-sourcing it here never
# runs a backup, never touches the lock, never writes anything.
. $dailyBackupScript

if (-not (Test-Path -LiteralPath $BackupPath -PathType Container)) {
    Write-Host "  [FAIL] Backup path not found: $BackupPath" -ForegroundColor Red
    exit 1
}

$result = Test-FansBackupRestoreReady -DirectoryPath $BackupPath
if (-not $result.Ok) {
    Write-Host "  [FAIL] NOT restore-ready: $($result.Reason)" -ForegroundColor Red
    Write-Host ''
    Write-Host '  Do not restore from this backup.' -ForegroundColor Yellow
    exit 1
}

Write-Host "  [OK]  $BackupPath passes the restore-readiness checks." -ForegroundColor Green
Write-Host '        (manifest status/integrity/env-included/size all consistent)' -ForegroundColor DarkGray

if ($Deep) {
    Write-Host ''
    Write-Host '  Running deep check (PRAGMA integrity_check, live)...' -ForegroundColor DarkGray
    $sqlite3Exe = Join-Path $projectRoot 'tools\sqlite3.exe'
    if (-not (Test-Path -LiteralPath $sqlite3Exe -PathType Leaf)) {
        Write-Host "  [FAIL] sqlite3.exe not found at $sqlite3Exe -- cannot run deep check." -ForegroundColor Red
        exit 1
    }
    $dbPath = Join-Path $BackupPath 'db.sqlite3'
    $integrity = Test-SqliteIntegrity -Sqlite3Exe $sqlite3Exe -DbPath $dbPath
    if ($integrity.Ok) {
        Write-Host '  [OK]  Deep check passed -- PRAGMA integrity_check: ok' -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] Deep check FAILED: $($integrity.Detail)" -ForegroundColor Red
        exit 1
    }
}

Write-Host ''
Write-Host '  This backup is safe to restore from.' -ForegroundColor Green
Write-Host ''
exit 0
