# FANSC Backup & Restore Guide

**Full name:** FANSC: Secure FaceNet-Based Facial Verification System for Senior Citizen Stipend Distribution

This document explains what to back up, how to back it up, and how to restore the system after a disk failure, reinstall, or hardware migration.

---

## 1. What MUST Be Backed Up Together

These four items are a single unit. **Backing up one without the others will leave the system in an unrecoverable state.**

| Item | Path (default install) | Why it matters |
|---|---|---|
| Database file | `db.sqlite3` (next to `fans_c.exe`) | Holds every beneficiary, user, audit log, claim record, and verification attempt. |
| Environment file | `.env` (next to `fans_c.exe`) | Holds `SECRET_KEY`, `EMBEDDING_ENCRYPTION_KEY`, ALLOWED_HOSTS, threshold values, and other deployment-specific settings. |
| Media folder | `media/` (next to `fans_c.exe`) | Registration photos, ID scans, profile pictures referenced by the database. |
| Encryption key | `EMBEDDING_ENCRYPTION_KEY` value (printed and stored offline) | Required to decrypt encrypted FaceNet embeddings. **Embeddings are unrecoverable without it.** |

> **Why all four together?** The database stores a path like `beneficiaries/photos/abc123.jpg` and an encrypted blob like `gAAAA...` — but the photo lives in `media/` and the blob can only be decrypted by the key in `.env`. Missing any one of the four breaks the chain.

---

## 2. Recommended Backup Folder Layout

Inside the FANSC install directory (default: `C:\FANSC\`):

```
C:\FANSC\
  fans_c.exe
  db.sqlite3              <-- source
  .env                    <-- source
  media/                  <-- source
  backups/                <-- destination
    2026-05-27_0830\
      db.sqlite3
      .env
      media\
      _backup_manifest.json   <-- completion/restore-readiness record
    2026-05-26_0830\
      ...
```

`_backup_manifest.json` (added in v2.1.16) records `status`, `integrity_check`,
`env_included`, and `db_backup_bytes` (the exact byte size of `db.sqlite3` at
backup time). A backup directory is only ever treated as valid/restore-ready
when this manifest is present **and** its recorded state still matches the
files actually on disk (exact `db_backup_bytes` match, `.env` present,
`integrity_check` recorded as `ok`). A directory without a manifest, or one
where the manifest disagrees with reality, is an incomplete/failed attempt
and must not be restored from — use `verify-backup.ps1` (Section 7a) to
check any specific backup before restoring from it.

The Database & Backup Status page (under Administration) automatically reads from this `backups/` folder, using this same restore-readiness check.

A same-minute retry of the automated backup is named `YYYY-MM-DD_HHmm_2`,
`_3`, etc. (never `_0` or `_1`) rather than overwriting the original attempt.

---

## 3. Automated Daily Backup (Recommended — Phase 3A)

As of Phase 3A, a **Windows Task Scheduler task** named `FANS-C Daily Backup` is registered automatically by the application launcher during first-run setup. It runs every night at **21:00** under the SYSTEM account without any operator action.

**What it backs up:** `db.sqlite3`, `.env`, and `media\` — the same three items as the manual procedure below.

**Where it writes:** `C:\FANSC\backups\YYYY-MM-DD_HHMM\` — one folder per run.

**Rotation:** Keeps the **14 most recent** backup folders. Older folders are deleted automatically.

**Security:** Each backup folder is created with restricted NTFS permissions (Administrators + SYSTEM only). The backup contains `EMBEDDING_ENCRYPTION_KEY` and `SECRET_KEY` inside `.env` — treat backup folders with the same physical security as the server itself.

**Backup log:** `C:\FANSC\logs\fans-backup.log` — one line per run with timestamp, status ([OK] / [ERROR]), and destination path.

**Exit codes** (useful if scripting around the task's result):

| Code | Meaning |
|---|---|
| `0` | Backup completed successfully, rotation was clean |
| `1` | Required backup step failed — do not treat the destination as valid |
| `2` | Backup itself succeeded, but rotation of old backups reported an error |
| `3` | Skipped — another backup run was already in progress (not a failure) |
| `4` | Backup/rotation succeeded, but the required operational log could not be written |

To check recent backup status:
```powershell
Get-Content "C:\FANSC\logs\fans-backup.log" | Select-Object -Last 20
```

To trigger a backup immediately without waiting for 21:00:
```powershell
Start-ScheduledTask -TaskName "FANS-C Daily Backup"
```

To run the backup script directly as an Administrator:
```powershell
& "C:\FANSC\scripts\admin\daily-backup.ps1"
```

**After each automated backup:** Copy the latest `backups\` subfolder to an external USB drive or a secure shared folder on a different machine. The Task Scheduler task writes the backup locally — off-site copy is still a manual responsibility.

---

## 4. Manual Backup (Fallback — if Automated Backup is Unavailable)

**A manual backup folder holds the same sensitive contents as the automated
one — beneficiary PII, claim/payout records, encrypted FaceNet embeddings,
and `.env` secrets (`SECRET_KEY`, `EMBEDDING_ENCRYPTION_KEY`) — so it must
end up with the same restricted permissions.** A folder created with plain
`New-Item` inherits its parent directory's (often broad) permissions. Apply
the ACL restriction immediately after creating the folder and **before**
copying anything into it.

Run as an Administrator from PowerShell:

```powershell
$ts   = Get-Date -Format "yyyy-MM-dd_HHmm"
$src  = "C:\FANSC"
$dst  = "C:\FANSC\backups\$ts"
New-Item -ItemType Directory -Force -Path $dst | Out-Null

# Restrict to Administrators + SYSTEM only, BEFORE copying any sensitive file
# in. Reuses the exact ACL policy the automated backup applies — dot-sourcing
# the script only defines its functions (New-FansBackupAcl, etc.); it does not
# run a backup, so this is safe to call standalone.
. "C:\FANSC\scripts\admin\daily-backup.ps1"
Set-Acl -LiteralPath $dst -AclObject (New-FansBackupAcl)

Copy-Item "$src\db.sqlite3" -Destination $dst
Copy-Item "$src\.env"       -Destination $dst
Copy-Item "$src\media"      -Destination $dst -Recurse
Write-Host "Backup saved to $dst"
```

If `daily-backup.ps1` is not available to dot-source, apply the equivalent
ACL directly instead of the two lines above:

```powershell
$acl    = New-Object System.Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)   # disable inheritance, drop inherited ACEs
$admins = New-Object System.Security.Principal.SecurityIdentifier([System.Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid, $null)
$system = New-Object System.Security.Principal.SecurityIdentifier([System.Security.Principal.WellKnownSidType]::LocalSystemSid, $null)
$acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($admins, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
$acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($system, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
Set-Acl -LiteralPath $dst -AclObject $acl
```

Tips:
- Stop FANSC before copying `db.sqlite3` to guarantee a consistent snapshot, or use the hot-backup method in Section 5 below.
- Copy the resulting folder to an **external USB drive** or **secure shared folder** on a different machine — the ACL restriction above only protects the folder while it stays on an NTFS volume; a broad-permissions destination (e.g. a shared folder open to "Everyone") re-exposes the same data regardless of the source folder's ACL.
- Rotate: keep at least the last 7 daily backups, plus one per month for 12 months.

---

## 5. Live (Hot) Backup with SQLite `.backup`

If you cannot stop the service:

```powershell
$ts  = Get-Date -Format "yyyy-MM-dd_HHmm"
$dst = "C:\FANSC\backups\$ts"
New-Item -ItemType Directory -Force -Path $dst | Out-Null

# Same restricted ACL as Section 4 — apply BEFORE the hot-backup and BEFORE
# copying .env/media in, since the destination folder will hold the same
# sensitive contents (PII, biometric embeddings, secrets) as any other backup.
. "C:\FANSC\scripts\admin\daily-backup.ps1"
Set-Acl -LiteralPath $dst -AclObject (New-FansBackupAcl)

# NOTE: if your install path contains an apostrophe (e.g. "C:\Citizen's Apps\FANSC"),
# do NOT wrap the destination in quotes as shown in older versions of this guide --
# the sqlite3.exe shell's .backup command cannot safely quote an embedded apostrophe.
# Instead, cd into the destination folder and reference the file by bare name:
Push-Location $dst
& "C:\FANSC\tools\sqlite3.exe" "C:\FANSC\db.sqlite3" ".backup db.sqlite3"
Pop-Location
Copy-Item "C:\FANSC\.env" -Destination $dst
Copy-Item "C:\FANSC\media" -Destination $dst -Recurse
```

If `daily-backup.ps1` is not available to dot-source, apply the equivalent ACL
commands shown in Section 4 instead of the two lines above.

The SQLite `.backup` command creates a consistent copy even while the database is being written.

---

## 6. Securing the Encryption Key

The `EMBEDDING_ENCRYPTION_KEY` lives inside `.env`. If the office machine is stolen, that file goes with it.

Recommended:
- **Print the key on paper** and lock it in a drawer or safe at the barangay office.
- Also save it to a **dedicated USB stick** that is stored separately from the FANSC computer.
- Rotate only with deliberate planning &mdash; old embeddings cannot be decrypted with a new key. Generating a new key invalidates every stored face.

To extract the current key:

```powershell
Get-Content "C:\FANSC\.env" | Select-String "EMBEDDING_ENCRYPTION_KEY"
```

---

## 7. Restore on a Fresh Machine

### 7a. Recommended: `restore-backup.ps1` (added in v2.1.16)

Run as Administrator:

```powershell
& "C:\FANSC\scripts\admin\restore-backup.ps1" -BackupPath "C:\FANSC\backups\2026-05-27_0830"
```

This automates the manual steps below, plus two safety measures the manual
procedure doesn't have:
- It **verifies the chosen backup is restore-ready first** (same check the
  Database & Backup Status page and nightly rotation use) and refuses to
  touch anything if it isn't.
- It **snapshots the current live `db.sqlite3`/`.env`/`media\`** into
  `backups\pre-restore-<timestamp>\` before overwriting anything, so a bad
  restore is itself recoverable.

It stops Waitress/Caddy, performs the snapshot and copy, and re-runs
`PRAGMA integrity_check` against the restored database — then prints the
remaining manual steps (start the service, open Database & Backup Status,
run one test verification). It asks for a typed `YES` confirmation before
making any change.

To check a backup's restore-readiness on its own, without restoring:
```powershell
& "C:\FANSC\scripts\admin\verify-backup.ps1" -BackupPath "C:\FANSC\backups\2026-05-27_0830"
# Add -Deep to also re-run PRAGMA integrity_check live (slower, most thorough):
& "C:\FANSC\scripts\admin\verify-backup.ps1" -BackupPath "C:\FANSC\backups\2026-05-27_0830" -Deep
```

### 7b. Manual fallback (if the scripts above are unavailable)

1. Install FANSC normally (run the installer; let it create `C:\FANSC\`).
2. **Stop the service** before replacing data:
   ```powershell
   & "C:\FANSC\scripts\admin\stop-fans.ps1"
   ```
3. Replace the three live files with the backed-up copies:
   ```powershell
   $src = "D:\backup-2026-05-27_0830"
   Copy-Item "$src\db.sqlite3" "C:\FANSC\db.sqlite3" -Force
   Copy-Item "$src\.env"       "C:\FANSC\.env"       -Force
   Remove-Item "C:\FANSC\media" -Recurse -Force
   Copy-Item "$src\media"      "C:\FANSC\media" -Recurse -Force
   ```
4. Verify the `EMBEDDING_ENCRYPTION_KEY` line in `.env` is intact and matches what was used to create the embeddings.
5. Start the service:
   ```powershell
   & "C:\FANSC\scripts\admin\start-now.ps1"
   ```
6. Open the Database &amp; Backup Status page (Administration &rarr; Database &amp; Backup Status) and confirm:
   - Database: **Connected**
   - Encryption Key: **Configured**
   - Beneficiary record count matches the source machine
7. Run one test verification with a known beneficiary to confirm embeddings still decrypt and match.

---

## 8. Restore Verification Checklist

| Check | Where to verify |
|---|---|
| Database connects | Database &amp; Backup Status page |
| EMBEDDING_ENCRYPTION_KEY present | Database &amp; Backup Status page |
| Beneficiary count matches | Database &amp; Backup Status &mdash; *Registered beneficiary records* |
| Audit log carried over | Audit Logs page &mdash; sort by oldest |
| Photos visible | Open any beneficiary detail page |
| One live verification succeeds | Verify &mdash; pick a known beneficiary |

If any of these fail, **do not put the restore into production**. Investigate first.

---

## 9. Disaster Recovery Without the Key

If the encryption key is lost:
- Beneficiary names, dates of birth, addresses, claim records, and audit logs are **still readable** (these are not encrypted).
- All face embeddings become **permanently undecryptable**. Every beneficiary must be re-enrolled (recaptured) before they can verify again.

This is why Section 5 above takes the key seriously. Treat it like the keys to a vault.

---

## 10. Operational Reminders

- A backup that has never been restored is not a real backup. Practice the restore once on a spare machine before you need it.
- Test backups quarterly.
- Update this document if the install path, file names, or service control scripts change.
