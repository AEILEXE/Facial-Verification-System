#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C Daily Backup -- hot-backup database, .env, and media after office hours.

.DESCRIPTION
    Designed to run unattended as a Windows Task Scheduler task (daily at 21:00,
    SYSTEM account). Safe to run while FANS-C is serving requests -- SQLite's
    online .backup command produces a consistent snapshot without stopping Waitress.

    What is backed up:
      db.sqlite3   -- all records (beneficiaries, claims, audit log, attempts)
      .env         -- SECRET_KEY, EMBEDDING_ENCRYPTION_KEY, all thresholds
      media\       -- beneficiary registration photos and ID scans

    WARNING: The backup folder contains sensitive biometric data and secrets.
    It is created with restricted NTFS permissions (Administrators + SYSTEM only,
    identified by well-known SID rather than localized account name).
    Never store backups on a world-readable share or unencrypted external drive.

    A backup is only considered SUCCESSFUL when every required step below
    passes: sqlite3.exe present, source DB present, an exclusive run lock
    acquired, a collision-free destination selected, backup folder created and
    ACL-restricted, hot-backup taken, backup copy integrity-verified via
    PRAGMA integrity_check, .env copied, media\ copied (when present), and a
    completion manifest (_backup_manifest.json, status=complete) written.
    A backup directory without that manifest is an incomplete/failed attempt.

    Restore-readiness is re-checked (cheaply, from recorded metadata -- see
    Test-FansBackupRestoreReady) by both rotation and system-health reporting
    every time they look at a backup directory: status=complete alone is not
    trusted blindly, because a directory can be tampered with, truncated, or
    partially overwritten after its manifest was written. Only a directory
    that is still fully restore-ready by that predicate counts as valid.

    Backup location: <install_dir>\backups\YYYY-MM-DD_HHmm[_N]\
                      (the optional _N suffix disambiguates a same-minute
                      retry so it can never collide with / overwrite a
                      previous attempt at that same destination -- see
                      Get-NextFansBackupDestination)
    Log file:        <install_dir>\logs\fans-backup.log
    Rotation:        Keeps the 14 most recent RESTORE-READY backups; older
                      ones are deleted. Incomplete/failed attempts and
                      unrelated files or folders under backups\ are never
                      touched by rotation.
    Concurrency:      An exclusive OS-level lock (backups\.daily-backup.lock)
                      ensures at most one backup run is ever active at a time.
                      A second overlapping invocation (scheduled + manual,
                      double-click, etc.) is rejected immediately rather than
                      queued or raced.

    Exit codes:
      0 = backup completed successfully, rotation was clean, logging was fine
      1 = backup FAILED -- do not treat the destination as valid/recoverable
      2 = backup valid, but rotation of old backups reported an error
      3 = SKIPPED -- another backup run was already in progress (not a failure
          of this run, but no backup was produced by this invocation)
      4 = backup and rotation were fine, but the required operational log
          (logs\fans-backup.log) could not be written during this run

.NOTES
    Registered by scripts\setup\setup-autostart.ps1 / dev\launcher.py as
    "FANS-C Daily Backup".
    To run manually (as Administrator):
        & "C:\FANSC\scripts\admin\daily-backup.ps1"
    To check backup history:
        Get-Content "C:\FANSC\logs\fans-backup.log" | Select-Object -Last 30

    This file also defines reusable functions (Test-SqliteIntegrity,
    Test-FansBackupRestoreReady, Get-EligibleFansBackups,
    Invoke-FansBackupRotation, Get-NextFansBackupDestination, etc.) that can
    be dot-sourced for automated testing without running the full backup:
        . "C:\FANSC\scripts\admin\daily-backup.ps1"
    Dot-sourcing only loads the functions below -- it does NOT perform a backup.
#>

# ---------------------------------------------------------------------------
# Reusable functions (safe to dot-source for testing; no side effects here)
# ---------------------------------------------------------------------------

function Write-FansBackupLog {
    <# Writes to the console unconditionally, then best-effort to $LogFile.
       Returns $true only if the file write itself succeeded, so the caller
       can track whether the REQUIRED operational log is actually being
       produced (finding F-06) without the log-write failure ever throwing
       or aborting the backup by itself. #>
    param(
        [Parameter(Mandatory)][string]$LogFile,
        [Parameter(Mandatory)][string]$Level,
        [Parameter(Mandatory)][string]$Message
    )
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [$Level] $Message"
    Write-Host $line
    try {
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8 -ErrorAction Stop
        return $true
    } catch {
        Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [ERROR] Could not write to required log file '$LogFile': $_"
        return $false
    }
}

function Test-SqliteIntegrity {
    <# Runs PRAGMA integrity_check against a SQLite file and requires the
       result to be exactly "ok". Explicitly rejects missing/zero-byte files
       first -- SQLite treats a zero-length file as a valid empty database,
       which would otherwise make integrity_check falsely report "ok" for a
       truncated/failed backup copy. #>
    param(
        [Parameter(Mandatory)][string]$Sqlite3Exe,
        [Parameter(Mandatory)][string]$DbPath
    )
    if (-not (Test-Path -LiteralPath $DbPath -PathType Leaf)) {
        return [PSCustomObject]@{ Ok = $false; Detail = "backup database file not found: $DbPath" }
    }
    $size = (Get-Item -LiteralPath $DbPath).Length
    if ($size -le 0) {
        return [PSCustomObject]@{ Ok = $false; Detail = 'backup database file is zero bytes' }
    }
    try {
        $output = & $Sqlite3Exe $DbPath 'PRAGMA integrity_check;' 2>&1
        $exitCode = $LASTEXITCODE
        $text = ($output | Out-String).Trim()
        if ($exitCode -ne 0) {
            return [PSCustomObject]@{ Ok = $false; Detail = "sqlite3.exe exited $exitCode -- $text" }
        }
        if ($text -eq 'ok') {
            return [PSCustomObject]@{ Ok = $true; Detail = 'ok' }
        }
        return [PSCustomObject]@{ Ok = $false; Detail = $text }
    } catch {
        return [PSCustomObject]@{ Ok = $false; Detail = "$_" }
    }
}

function Invoke-FansSqliteBackup {
    <# Hot-backs-up $SourceDb into $DestDir\$DestFileName via SQLite's
       .backup dot-command. Returns the full destination path on success;
       throws on failure.

       Finding N-02: the bundled tools\sqlite3.exe's dot-command tokenizer
       only recognizes single quotes for a `.backup 'FILE'` argument, has no
       escape sequence for an embedded apostrophe (doubling it, backslash-
       escaping it, and double-quoting were all tried experimentally against
       the real binary and every one of them broke), and a Windows install
       directory MAY legitimately contain both a space and an apostrophe
       (e.g. "C:\Citizen's Apps\FANSC", freely choosable via the Inno Setup
       directory picker) -- so the destination path can never be safely
       embedded as quoted/escaped TEXT in the dot-command at all.

       Instead, the sqlite3.exe CHILD PROCESS's working directory is set to
       $DestDir (via Push-Location, scoped to this function only) and the
       destination is referenced by a bare, path-free filename. Since the
       filename itself is always one of our own fixed, safe names (never
       user-chosen), this sidesteps the tokenizer entirely regardless of
       what the destination folder's own path contains. Verified
       experimentally against the real bundled binary with plain, spaced,
       apostrophe-only, and combined space+apostrophe destination folders. #>
    param(
        [Parameter(Mandatory)][string]$Sqlite3Exe,
        [Parameter(Mandatory)][string]$SourceDb,
        [Parameter(Mandatory)][string]$DestDir,
        [string]$DestFileName = 'db.sqlite3'
    )
    Push-Location -LiteralPath $DestDir -ErrorAction Stop
    try {
        & $Sqlite3Exe $SourceDb ".backup $DestFileName"
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($exitCode -ne 0) {
        throw "sqlite3.exe exited with code $exitCode during .backup"
    }
    return Join-Path $DestDir $DestFileName
}

function Test-FansBackupDirName {
    <# Strict eligibility gate: only the exact automated naming scheme is
       ever considered a FANS-C backup directory -- yyyy-MM-dd_HHmm, or
       yyyy-MM-dd_HHmm_N for a same-minute retry where N is EXACTLY the
       canonical domain 2..999 with no leading zeros (finding N-02).
       Get-NextFansBackupDestination only ever generates that exact domain;
       "_0", "_1" (the base name is already conceptually sequence 1 -- "_1"
       is never a valid alias for it), "_01", "_1000"+, and any non-canonical
       shape are rejected. Bounding the suffix to 1-3 digits here also means
       a match can never overflow a 32-bit int when cast further down. #>
    param([Parameter(Mandatory)][string]$Name)
    return [bool]($Name -match '^\d{4}-\d{2}-\d{2}_\d{4}(_([2-9]|[1-9][0-9]|[1-9][0-9]{2}))?$')
}

function Get-FansBackupTimestamp {
    <# Parses the yyyy-MM-dd_HHmm base portion of a backup directory name.
       Returns $null for anything that isn't both a canonical name (see
       Test-FansBackupDirName) AND a real calendar date/time -- e.g.
       "2026-13-99_9999" has the right digit shape but is not a valid date. #>
    param([Parameter(Mandatory)][string]$Name)
    if ($Name -notmatch '^(?<base>\d{4}-\d{2}-\d{2}_\d{4})(?:_([2-9]|[1-9][0-9]|[1-9][0-9]{2}))?$') { return $null }
    $base = $Matches['base']
    $parsed = [datetime]::MinValue
    $ok = [datetime]::TryParseExact(
        $base, 'yyyy-MM-dd_HHmm', [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::None, [ref]$parsed)
    if (-not $ok) { return $null }
    return $parsed
}

function Get-FansBackupSequence {
    <# The canonical _N same-minute-retry suffix as an integer (1 when
       absent or non-canonical), used only as a sort tie-breaker so a retry
       is correctly treated as newer than the attempt it replaced. The
       regex's bounded 1-3 digit shape guarantees [int] below never
       overflows. #>
    param([Parameter(Mandatory)][string]$Name)
    if ($Name -match '^\d{4}-\d{2}-\d{2}_\d{4}_([2-9]|[1-9][0-9]|[1-9][0-9]{2})$') { return [int]$Matches[1] }
    return 1
}

function Test-FansBackupDbBackupBytes {
    <# Finding N-01: validates the manifest's db_backup_bytes field as a
       genuine, non-Boolean JSON integer -- never coerced from a string,
       float, or out-of-range value. Windows PowerShell 5.1's ConvertFrom-Json
       represents a JSON integer as System.Int32 (small) or System.Int64
       (large); it uses System.Decimal for anything fractional or too large
       to fit Int64, and System.Boolean/System.String for JSON true/false/
       "13" -- so checking the CLR TYPE (not attempting a numeric cast first)
       rejects all of those by construction, with no coercion logic at all.
       A value this function accepts is, by definition, already within
       1..[int64]::MaxValue -- no separate range check is needed since
       anything larger deserializes as Decimal, not Int32/Int64. #>
    param($Value)
    if ($null -eq $Value) {
        return [PSCustomObject]@{ Ok = $false; Value = $null; Reason = 'db_backup_bytes is missing or null' }
    }
    $type = $Value.GetType()
    if ($type -ne [int32] -and $type -ne [int64]) {
        return [PSCustomObject]@{ Ok = $false; Value = $null; Reason = "db_backup_bytes has type $($type.FullName), expected a JSON integer" }
    }
    $longValue = [int64]$Value
    if ($longValue -lt 1) {
        return [PSCustomObject]@{ Ok = $false; Value = $null; Reason = "db_backup_bytes ($longValue) must be a positive integer" }
    }
    return [PSCustomObject]@{ Ok = $true; Value = $longValue; Reason = 'ok' }
}

function Get-NextFansBackupDestination {
    <# Finding F-01: picks a destination directory that does NOT currently
       exist, so a backup run can NEVER reuse (and thereby partially
       overwrite, or inherit a stale manifest from) a previous attempt at the
       same base timestamp -- whether that previous attempt completed, is
       mid-flight, or failed and was left behind. A same-minute retry gets an
       incrementing _N suffix instead. #>
    param(
        [Parameter(Mandatory)][string]$BackupsRoot,
        [Parameter(Mandatory)][string]$Timestamp   # yyyy-MM-dd_HHmm
    )
    $candidate = Join-Path $BackupsRoot $Timestamp
    if (-not (Test-Path -LiteralPath $candidate)) {
        return [PSCustomObject]@{ Path = $candidate; Name = $Timestamp }
    }
    for ($i = 2; $i -le 999; $i++) {
        $name = "{0}_{1}" -f $Timestamp, $i
        $candidate = Join-Path $BackupsRoot $name
        if (-not (Test-Path -LiteralPath $candidate)) {
            return [PSCustomObject]@{ Path = $candidate; Name = $name }
        }
    }
    throw "Could not find an available backup destination under $BackupsRoot for $Timestamp after 999 attempts"
}

function Enter-FansBackupLock {
    <# Finding F-01: acquires an exclusive OS-level lock so at most one
       daily-backup.ps1 process is ever actively running (guards against
       scheduled+manual overlap, double invocation, and true concurrent
       invocation). Returns the open FileStream on success -- keep it
       referenced for the run's duration; disposing it releases the lock --
       or $null if another process already holds it. #>
    param([Parameter(Mandatory)][string]$LockPath)
    try {
        return [System.IO.File]::Open(
            $LockPath, [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    } catch {
        return $null
    }
}

function Get-FansBackupManifestObject {
    <# Parses a backup directory's manifest file. Returns $null (never
       throws) if it is missing or not valid JSON. #>
    param([Parameter(Mandatory)][string]$ManifestPath)
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { return $null }
    try {
        return Get-Content -LiteralPath $ManifestPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    } catch {
        return $null
    }
}

function Test-FansBackupRestoreReady {
    <# Findings F-03/F-04: the restore-readiness predicate. status=complete
       in the manifest is NOT sufficient by itself -- this additionally
       requires the recorded integrity/env state to say "ok", and requires
       db.sqlite3/.env to still physically exist with a non-zero/matching
       size. This intentionally does NOT re-run PRAGMA integrity_check here
       (expensive); it trusts the value recorded at backup-creation time, but
       only after confirming the file that was checked has not since been
       truncated or replaced (recorded byte count vs. current file size).
       Used identically by rotation eligibility and (via fans/views.py's
       Python port, kept in sync) system-health reporting. #>
    param(
        [Parameter(Mandatory)][string]$DirectoryPath,
        [string]$ManifestFileName = '_backup_manifest.json'
    )
    $manifestPath = Join-Path $DirectoryPath $ManifestFileName
    $manifest = Get-FansBackupManifestObject -ManifestPath $manifestPath
    if ($null -eq $manifest) {
        return [PSCustomObject]@{ Ok = $false; Reason = 'manifest missing or unparseable' }
    }
    if ($manifest.status -ne 'complete') {
        return [PSCustomObject]@{ Ok = $false; Reason = "manifest status is '$($manifest.status)', not 'complete'" }
    }
    if ($manifest.integrity_check -ne 'ok') {
        return [PSCustomObject]@{ Ok = $false; Reason = "manifest integrity_check is '$($manifest.integrity_check)', not 'ok'" }
    }
    if ($manifest.env_included -ne $true) {
        return [PSCustomObject]@{ Ok = $false; Reason = 'manifest env_included is not true' }
    }
    $dbPath = Join-Path $DirectoryPath 'db.sqlite3'
    if (-not (Test-Path -LiteralPath $dbPath -PathType Leaf)) {
        return [PSCustomObject]@{ Ok = $false; Reason = 'db.sqlite3 is missing from the backup directory' }
    }
    $dbSize = (Get-Item -LiteralPath $dbPath).Length
    if ($dbSize -le 0) {
        return [PSCustomObject]@{ Ok = $false; Reason = 'db.sqlite3 in the backup directory is zero bytes' }
    }
    $envPath = Join-Path $DirectoryPath '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        return [PSCustomObject]@{ Ok = $false; Reason = '.env is missing from the backup directory' }
    }
    $bytesCheck = Test-FansBackupDbBackupBytes -Value $manifest.db_backup_bytes
    if (-not $bytesCheck.Ok) {
        return [PSCustomObject]@{ Ok = $false; Reason = "manifest db_backup_bytes invalid: $($bytesCheck.Reason)" }
    }
    if ($bytesCheck.Value -ne [int64]$dbSize) {
        return [PSCustomObject]@{
            Ok     = $false
            Reason = "recorded db_backup_bytes ($($bytesCheck.Value)) does not match current db.sqlite3 size ($dbSize) -- possible truncation/tampering"
        }
    }
    return [PSCustomObject]@{ Ok = $true; Reason = 'ok' }
}

function Get-EligibleFansBackups {
    <# Restore-ready FANS-C backup directories under $BackupsRoot, newest
       first. Eligibility requires the strict naming scheme AND
       Test-FansBackupRestoreReady -- unrelated directories/files and
       incomplete/failed/tampered backup attempts are never returned, so
       callers (rotation, health) can never mistake them for valid backups,
       and an invalid newer attempt can never hide or displace an older
       valid one. #>
    param(
        [Parameter(Mandatory)][string]$BackupsRoot,
        [string]$ManifestFileName = '_backup_manifest.json'
    )
    $results = @()
    if (-not (Test-Path -LiteralPath $BackupsRoot -PathType Container)) {
        return $results
    }
    Get-ChildItem -LiteralPath $BackupsRoot -Directory -ErrorAction Stop | ForEach-Object {
        if (-not (Test-FansBackupDirName -Name $_.Name)) { return }
        $ts = Get-FansBackupTimestamp -Name $_.Name
        if ($null -eq $ts) { return }
        $ready = Test-FansBackupRestoreReady -DirectoryPath $_.FullName -ManifestFileName $ManifestFileName
        if (-not $ready.Ok) { return }
        $seq = Get-FansBackupSequence -Name $_.Name
        $results += [PSCustomObject]@{ Directory = $_; Timestamp = $ts; Sequence = $seq }
    }
    return @($results | Sort-Object -Property Timestamp, Sequence -Descending)
}

function Get-IncompleteFansBackupCount {
    <# Codex observation: counts strictly-named backup directories that are
       NOT restore-ready (failed/incomplete/tampered attempts), purely for
       operational visibility. Never deletes, never counts them as valid. #>
    param(
        [Parameter(Mandatory)][string]$BackupsRoot,
        [string]$ManifestFileName = '_backup_manifest.json'
    )
    if (-not (Test-Path -LiteralPath $BackupsRoot -PathType Container)) { return 0 }
    $count = 0
    Get-ChildItem -LiteralPath $BackupsRoot -Directory -ErrorAction Stop | ForEach-Object {
        if (-not (Test-FansBackupDirName -Name $_.Name)) { return }
        if ($null -eq (Get-FansBackupTimestamp -Name $_.Name)) { return }
        $ready = Test-FansBackupRestoreReady -DirectoryPath $_.FullName -ManifestFileName $ManifestFileName
        if (-not $ready.Ok) { $count++ }
    }
    return $count
}

function Invoke-FansBackupRotation {
    <# Deletes only eligible (strictly-named, restore-ready) backup
       directories older than the newest $KeepCount. Unrelated files/folders
       and incomplete/invalid attempts are left alone -- they simply never
       appear in the eligible set, so they can never consume a retention slot
       or be mistaken for a deletion candidate. Returns $false (without
       throwing) if any eligible old backup could not be removed, so the
       caller can report a distinct "rotation had a problem" outcome separate
       from the new backup itself. #>
    param(
        [Parameter(Mandatory)][string]$BackupsRoot,
        [int]$KeepCount = 14,
        [string]$ManifestFileName = '_backup_manifest.json',
        [scriptblock]$Logger = { param($Level, $Message) }
    )
    try {
        $eligible = Get-EligibleFansBackups -BackupsRoot $BackupsRoot -ManifestFileName $ManifestFileName
    } catch {
        & $Logger 'ERROR' "Rotation aborted -- could not enumerate backups root: $_"
        return $false
    }
    $rotationOk = $true
    if ($eligible.Count -gt $KeepCount) {
        $toDelete = $eligible | Select-Object -Skip $KeepCount
        foreach ($old in $toDelete) {
            try {
                Remove-Item -LiteralPath $old.Directory.FullName -Recurse -Force -ErrorAction Stop
                & $Logger 'INFO' "Rotated old backup: $($old.Directory.Name)"
            } catch {
                & $Logger 'ERROR' "Could not remove old backup $($old.Directory.Name): $_"
                $rotationOk = $false
            }
        }
    }
    & $Logger 'INFO' "Rotation complete. Eligible restore-ready backups: $($eligible.Count) (retention cap $KeepCount)."
    return $rotationOk
}

function Write-FansBackupManifest {
    <# Written via .NET directly (not Set-Content -Encoding UTF8) so the file
       is plain UTF-8 with no BOM regardless of PowerShell version -- Windows
       PowerShell 5.1's UTF8 encoding always emits a BOM, which breaks a
       plain json.loads() on the reading (Python) side. #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][hashtable]$Data
    )
    $json = $Data | ConvertTo-Json -Depth 4
    [System.IO.File]::WriteAllText($Path, $json, (New-Object System.Text.UTF8Encoding($false)))
}

function New-FansBackupAcl {
    <# Finding F-08: identifies Administrators/SYSTEM by well-known SID
       (S-1-5-32-544 / S-1-5-18) rather than localized account name, so the
       restriction does not depend on the OS display language. The ACL
       POLICY is unchanged: both principals get FullControl, inheritance is
       disabled, no one else is granted access. #>
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)   # disable inheritance, remove inherited ACEs
    $adminsSid = New-Object System.Security.Principal.SecurityIdentifier(
        [System.Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid, $null)
    $systemSid = New-Object System.Security.Principal.SecurityIdentifier(
        [System.Security.Principal.WellKnownSidType]::LocalSystemSid, $null)
    $adminRule  = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $adminsSid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $systemRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $systemSid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($adminRule)
    $acl.AddAccessRule($systemRule)
    return $acl
}

# ---------------------------------------------------------------------------
# Main backup routine -- skipped when this file is dot-sourced (testing)
# ---------------------------------------------------------------------------

if ($MyInvocation.InvocationName -ne '.') {

$ErrorActionPreference = 'Stop'

# $PSScriptRoot = <install>\scripts\admin\  ->  parent twice = install root
$projectRoot      = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$dbSource         = Join-Path $projectRoot 'db.sqlite3'
$envSource        = Join-Path $projectRoot '.env'
$mediaSource      = Join-Path $projectRoot 'media'
$sqlite3Exe       = Join-Path $projectRoot 'tools\sqlite3.exe'
$backupsRoot      = Join-Path $projectRoot 'backups'
$logFile          = Join-Path $projectRoot 'logs\fans-backup.log'
$manifestFileName = '_backup_manifest.json'
$keepCount        = 14   # number of restore-ready daily backups to retain
$lockPath         = Join-Path $backupsRoot '.daily-backup.lock'

$logsDir = Join-Path $projectRoot 'logs'
if (-not (Test-Path -LiteralPath $logsDir -PathType Container)) {
    try { New-Item -ItemType Directory -Force -Path $logsDir -ErrorAction Stop | Out-Null } catch { }
}

# Finding F-06: $script:logWriteFailed tracks whether the REQUIRED operational
# log could actually be written at any point in this run. The very first Log
# call below doubles as the "verify the log can be written before doing
# sensitive work" pre-flight check -- no separate probe needed.
$script:logWriteFailed = $false
function Log {
    param([string]$Level, [string]$Message)
    $ok = Write-FansBackupLog -LogFile $logFile -Level $Level -Message $Message
    if (-not $ok) { $script:logWriteFailed = $true }
}

function Fail {
    <# A required step failed: log it, make unmistakably clear this backup is
       not valid, and exit non-zero. No "completed successfully" line is ever
       written on this path. Never calls itself recursively -- Log/
       Write-FansBackupLog already swallow their own write failures and
       return a status flag instead of throwing, so there is no
       Fail -> Log -> Fail loop even when the log file itself is unwritable. #>
    param([string]$Message)
    Log 'ERROR' $Message
    Log 'ERROR' 'Backup FAILED and must NOT be treated as valid or recoverable.'
    exit 1
}

Log 'INFO' 'FANS-C Daily Backup started.'

# -- ensure the backups root exists (needed for both the lock file and the ------
# eventual destination directory) --------------------------------------------
try {
    New-Item -ItemType Directory -Force -Path $backupsRoot -ErrorAction Stop | Out-Null
} catch {
    Fail "Could not create/access the backups root: $backupsRoot -- $_"
}

# -- finding F-01: reject a concurrent/overlapping run immediately -------------
# (scheduled + manual overlap, double invocation, true concurrent invocation).
# This is a SKIP, not a failure of this invocation -- nothing was corrupted,
# this run simply yields to whichever process already holds the lock.
$lockStream = Enter-FansBackupLock -LockPath $lockPath
if ($null -eq $lockStream) {
    Log 'INFO' 'Another FANS-C Daily Backup run appears to be in progress (lock held). Skipping this run rather than risking a destination collision. This is not a failure.'
    exit 3
}

try {

    # -- 1) sqlite3.exe must exist ------------------------------------------------
    if (-not (Test-Path -LiteralPath $sqlite3Exe -PathType Leaf)) {
        Fail "Required sqlite3.exe not found at: $sqlite3Exe"
    }

    # -- 2) source database must exist --------------------------------------------
    if (-not (Test-Path -LiteralPath $dbSource -PathType Leaf)) {
        Fail "Source database not found: $dbSource"
    }

    # -- finding F-01: pick a destination that cannot collide with / overwrite ----
    # any previous attempt (completed, in-progress, or failed) at this same
    # nominal timestamp. A same-minute retry gets an incrementing _N suffix.
    $timestamp = Get-Date -Format 'yyyy-MM-dd_HHmm'
    try {
        $destination = Get-NextFansBackupDestination -BackupsRoot $backupsRoot -Timestamp $timestamp
    } catch {
        Fail "Could not select a backup destination: $_"
    }
    $backupDest   = $destination.Path
    $backupName   = $destination.Name
    $manifestPath = Join-Path $backupDest $manifestFileName
    Log 'INFO' "Backup destination: $backupDest"

    # -- 3) create backup destination securely ------------------------------------
    try {
        New-Item -ItemType Directory -Path $backupDest -ErrorAction Stop | Out-Null
    } catch {
        Fail "Could not create backup folder: $backupDest -- $_"
    }

    # -- 4) restrict NTFS permissions to Administrators + SYSTEM (by well-known --
    # SID -- finding F-08) --------------------------------------------------------
    # Failure here is FATAL: an unprotected folder containing .env, biometric
    # embeddings, and beneficiary PII is not an acceptable "successful" backup,
    # even if the file contents themselves were copied correctly.
    try {
        Set-Acl -LiteralPath $backupDest -AclObject (New-FansBackupAcl) -ErrorAction Stop
        Log 'INFO' 'Backup folder permissions restricted to Administrators + SYSTEM.'
    } catch {
        Fail "Could not restrict backup folder permissions (Administrators + SYSTEM ACL): $_"
    }

    # -- 5) hot-backup the SQLite database -----------------------------------------
    # SQLite .backup creates a consistent copy even while Waitress is writing.
    # See Invoke-FansSqliteBackup for why the destination is never embedded
    # as quoted text in the dot-command (finding N-02).
    $dbDest = Join-Path $backupDest 'db.sqlite3'
    try {
        Invoke-FansSqliteBackup -Sqlite3Exe $sqlite3Exe -SourceDb $dbSource -DestDir $backupDest | Out-Null
    } catch {
        Fail "Database backup FAILED: $_"
    }

    # -- 6/7) backup copy must exist, be non-zero, and pass integrity_check -------
    $integrity = Test-SqliteIntegrity -Sqlite3Exe $sqlite3Exe -DbPath $dbDest
    if (-not $integrity.Ok) {
        Fail "Backup database failed integrity verification: $($integrity.Detail)"
    }
    $dbBackupBytes = (Get-Item -LiteralPath $dbDest).Length
    $dbSizeKB = [math]::Round($dbBackupBytes / 1KB, 1)
    Log 'INFO' "Database backed up and integrity-verified: $dbDest ($dbSizeKB KB, PRAGMA integrity_check=ok)"

    # -- 8/9) .env must exist and be copied (contains recovery-critical secrets) --
    if (-not (Test-Path -LiteralPath $envSource -PathType Leaf)) {
        Fail ".env not found at $envSource -- key recovery would be impossible from this backup. Backup ABORTED."
    }
    try {
        Copy-Item -LiteralPath $envSource -Destination (Join-Path $backupDest '.env') -Force -ErrorAction Stop
        Log 'INFO' '.env copied to backup.'
    } catch {
        Fail ".env copy FAILED: $_"
    }

    # -- media\ -- copied when present. Absence is non-fatal (fresh install, no ---
    # uploads yet), but a copy FAILURE when media exists IS fatal: a backup that
    # silently drops beneficiary photos/ID scans must not be reported as complete.
    $mediaIncluded = $false
    $mediaFileCount = 0
    if (Test-Path -LiteralPath $mediaSource -PathType Container) {
        try {
            $mediaDest = Join-Path $backupDest 'media'
            Copy-Item -LiteralPath $mediaSource -Destination $mediaDest -Recurse -Force -ErrorAction Stop
            $mediaFileCount = (Get-ChildItem -LiteralPath $mediaDest -Recurse -File -ErrorAction Stop).Count
            $mediaIncluded = $true
            Log 'INFO' "media\ copied: $mediaFileCount file(s)."
        } catch {
            Fail "media\ copy FAILED: $_"
        }
    } else {
        Log 'INFO' 'media\ directory not found -- skipped (no uploads yet).'
    }

    # -- 10) record required completion state --------------------------------------
    # Written LAST, only once every required step above has actually succeeded.
    # db_backup_bytes is later re-checked against the live file size by
    # Test-FansBackupRestoreReady (findings F-03/F-04) to cheaply catch any
    # truncation/tampering that happens after this manifest is written.
    try {
        Write-FansBackupManifest -Path $manifestPath -Data @{
            version          = 2
            status           = 'complete'
            directory_name   = $backupName
            timestamp        = $timestamp
            created_at_utc   = (Get-Date).ToUniversalTime().ToString('o')
            db_backup_bytes  = $dbBackupBytes
            integrity_check  = $integrity.Detail
            env_included     = $true
            media_included   = $mediaIncluded
            media_file_count = $mediaFileCount
        }
    } catch {
        Fail "Could not write backup completion manifest: $_"
    }

    Log 'OK' "Backup completed successfully: $backupDest"

    # -- 11) rotate old backups -- only after this backup is confirmed complete ----
    # A rotation problem does not retroactively invalidate today's backup, so it
    # gets its own exit code (2) rather than being folded into the 0/1 result.
    $rotationOk = Invoke-FansBackupRotation -BackupsRoot $backupsRoot -KeepCount $keepCount `
        -ManifestFileName $manifestFileName -Logger { param($Level, $Message) Log $Level $Message }

    $incompleteCount = Get-IncompleteFansBackupCount -BackupsRoot $backupsRoot -ManifestFileName $manifestFileName
    if ($incompleteCount -gt 0) {
        Log 'INFO' "Operational note: $incompleteCount incomplete/failed backup attempt(s) remain under $backupsRoot (not deleted, not counted as valid -- inspect manually)."
    }

    # -- finding F-06: a broken required log takes priority over a clean rotation --
    # result -- the data backup itself is fine, but this run's own operational
    # evidence trail is not, so it must not be reported as a full (exit 0) success.
    if ($script:logWriteFailed) {
        Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [ERROR] Backup data completed successfully, but the required operational log ($logFile) could not be written during this run."
        exit 4
    }
    if ($rotationOk) {
        exit 0
    } else {
        Log 'ERROR' "Backup succeeded but rotation reported errors -- see above. Today's backup is still valid."
        exit 2
    }

} finally {
    if ($null -ne $lockStream) {
        try { $lockStream.Dispose() } catch { }
    }
}

}
