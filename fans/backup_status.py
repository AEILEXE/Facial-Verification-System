"""
Shared backup-manifest scanning helpers.

Extracted from fans/views.py (system_health) so the same restore-readiness
logic can be reused by accounts/management/commands/sync_backup_audit.py
without duplicating it a third time (the PowerShell script's
Test-FansBackupRestoreReady is the other copy — keep all in sync).
"""
import re

# Canonical backup-directory naming (finding N-02, kept in sync with
# daily-backup.ps1's Test-FansBackupDirName): base yyyy-MM-dd_HHmm, or
# yyyy-MM-dd_HHmm_N for a same-minute retry where N is EXACTLY 2..999 with no
# leading zeros. Get-NextFansBackupDestination only ever generates that exact
# domain -- "_0", "_1" (the base name is already conceptually sequence 1;
# "_1" is never a valid alias for it), "_01", "_1000"+, and any other shape
# are rejected.
_FANS_BACKUP_NAME_RE = re.compile(r'^(\d{4}-\d{2}-\d{2}_\d{4})(?:_([2-9]|[1-9][0-9]|[1-9][0-9]{2}))?$')


def _is_valid_db_backup_bytes(value):
    """
    Finding N-01: validates a manifest's db_backup_bytes field as a genuine,
    non-Boolean JSON integer -- mirrors daily-backup.ps1's
    Test-FansBackupDbBackupBytes (kept in sync).

    `type(value) is int` (not `isinstance(value, (int, float))`) is required
    specifically because `bool` is a subclass of `int` in Python, so
    `isinstance(True, int)` is True and a JSON `true`/`false` would otherwise
    silently pass as 1/0. The explicit upper bound matches what PowerShell's
    ConvertFrom-Json enforces implicitly: a JSON integer literal too large
    for a signed 64-bit value deserializes there as System.Decimal, not
    Int32/Int64, and is rejected by CLR type on that side.
    """
    if type(value) is not int:
        return False
    return 1 <= value <= 2**63 - 1


def _is_fans_backup_restore_ready(entry_path, manifest):
    """
    Mirrors scripts/admin/daily-backup.ps1's Test-FansBackupRestoreReady --
    keep both in sync (v2.1.16 correction, findings F-03/F-04).

    manifest status=complete alone is not trusted: this also requires the
    recorded integrity/env state to say "ok" / true, and requires
    db.sqlite3/.env to still physically exist with a non-zero size that
    matches what was recorded at backup-creation time (cheaply catches
    truncation/tampering/partial-overwrite after the manifest was written).
    This intentionally does NOT re-run PRAGMA integrity_check -- that is only
    ever run once, at backup creation time, by the PowerShell script.
    """
    if manifest.get('status') != 'complete':
        return False
    if manifest.get('integrity_check') != 'ok':
        return False
    if manifest.get('env_included') is not True:
        return False
    db_path = entry_path / 'db.sqlite3'
    if not db_path.is_file():
        return False
    try:
        db_size = db_path.stat().st_size
    except OSError:
        return False
    if db_size <= 0:
        return False
    if not (entry_path / '.env').is_file():
        return False
    recorded_bytes = manifest.get('db_backup_bytes')
    if not _is_valid_db_backup_bytes(recorded_bytes):
        return False
    if recorded_bytes != db_size:
        return False
    return True


def _scan_fans_backup_directories(backups_root):
    """
    Yields (entry, base_timestamp_str, sequence, manifest_or_None) for every
    directory under backups_root that matches the automated FANS-C naming
    scheme (YYYY-MM-DD_HHmm, optionally suffixed _N for a same-minute retry --
    see Get-NextFansBackupDestination). manifest is None when missing/unparseable.
    Unrelated directories/files are skipped entirely.
    """
    import json

    if not backups_root.exists() or not backups_root.is_dir():
        return
    for entry in backups_root.iterdir():
        if not entry.is_dir():
            continue
        match = _FANS_BACKUP_NAME_RE.match(entry.name)
        if not match:
            continue
        base, seq = match.group(1), int(match.group(2) or 1)
        manifest_path = entry / '_backup_manifest.json'
        manifest = None
        if manifest_path.is_file():
            try:
                # utf-8-sig tolerates a UTF-8 BOM (Windows PowerShell 5.1's
                # `Set-Content -Encoding UTF8` writes one) as well as plain UTF-8.
                manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                manifest = None
        yield entry, base, seq, manifest


def _list_completed_fans_backups(backups_root):
    """
    Return restore-ready FANS-C backup directories under backups_root, newest
    first (v2.1.16 correction, findings F-03/F-04). A newer directory that
    fails the restore-ready predicate is excluded entirely rather than
    hiding an older still-valid backup -- the older one simply becomes the
    newest entry in the returned list.
    """
    import datetime as _dt

    results = []
    for entry, base, seq, manifest in _scan_fans_backup_directories(backups_root):
        if manifest is None or not _is_fans_backup_restore_ready(entry, manifest):
            continue
        try:
            timestamp = _dt.datetime.strptime(base, '%Y-%m-%d_%H%M')
        except ValueError:
            continue
        results.append({
            'name': entry.name, 'path': entry, 'timestamp': timestamp,
            'sequence': seq, 'manifest': manifest,
        })

    results.sort(key=lambda r: (r['timestamp'], r['sequence']), reverse=True)
    return results


def _count_incomplete_fans_backups(backups_root):
    """
    Codex observation: count strictly-named backup directories that are NOT
    restore-ready (failed/incomplete/tampered attempts), for operational
    visibility only. Never deletes anything, never counts them as valid.
    """
    count = 0
    for entry, base, seq, manifest in _scan_fans_backup_directories(backups_root):
        import datetime as _dt
        try:
            _dt.datetime.strptime(base, '%Y-%m-%d_%H%M')
        except ValueError:
            continue
        if manifest is None or not _is_fans_backup_restore_ready(entry, manifest):
            count += 1
    return count
