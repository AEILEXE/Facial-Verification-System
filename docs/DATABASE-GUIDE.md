# FANS-C Database Guide

---

## 1. Database Modes

FANS-C supports two database backends. The active mode is controlled by `USE_SQLITE` in `.env`.

### 1.1 SQLite Mode (Default)

```env
USE_SQLITE=True
```

**Best for:** Single-server deployments, demo installations, barangay offices with one server PC.

- Database file: `db.sqlite3` in the project root (or `C:\FANSC\db.sqlite3` for the installer version)
- No separate database server required
- All data is in one portable file — easy to back up and move
- One writer at a time — adequate for typical barangay usage (one verification session at a time)
- Not suitable for simultaneous multi-PC write-heavy workloads

### 1.2 PostgreSQL Mode

```env
USE_SQLITE=False
DB_NAME=fans_db
DB_USER=fans_user
DB_PASSWORD=your_secure_password
DB_HOST=192.168.1.100
DB_PORT=5432
CONN_MAX_AGE=60
```

**Best for:** Multi-building deployments where multiple server PCs share one central database.

- Requires a PostgreSQL server (install separately)
- All Django workstations must point `DB_HOST` to the same server
- Handles concurrent writes safely
- All workstations **must** share the same `EMBEDDING_ENCRYPTION_KEY` — face data encrypted on one machine must be decryptable on all others
- Use `psycopg2-binary` (already in `requirements.txt`) — no system PostgreSQL client needed on the app server

---

## 2. What Is Stored

### `accounts` app

| Model | Contents |
|---|---|
| `CustomUser` | Staff accounts — username, hashed password, full name, email, system role, employee ID, officer assignment, active flag |

Active roles: `president`, `admin`, `it`, `staff`

Legacy roles (migrated, no longer assignable): `admin_it` → `admin` (accounts/0006), `head_brgy` → `president` (accounts/0009)

### `beneficiaries` app

| Model | Contents |
|---|---|
| `Beneficiary` | Senior citizen records — name, senior citizen ID, date of birth, address, photo path, sync status, lifecycle state |
| `Representative` | Authorized representative per beneficiary — name, relationship, face embedding (encrypted) |

### `verification` app

| Model | Contents |
|---|---|
| `VerificationAttempt` | Every face scan attempt — timestamp, beneficiary, similarity score, decision (VERIFIED / DENIED / NOT_VERIFIED / MANUAL_REVIEW), method (face/fallback/manual/override), operator, demo flag |
| `StipendEvent` | Distribution events — name, date, type |
| `ClaimRecord` | Claim per beneficiary per event — timestamp, amount, verification method, override flag |
| `FaceEmbedding` | Encrypted face embedding per beneficiary — Fernet-encrypted binary blob, created_at |
| `LivenessTransaction` | One-time liveness proof tokens — `tx_token` (UUID), encrypted neutral-frame embedding, anti-spoof score, PAD score/flags, `challenge_direction`, `session_id`, beneficiary/claimant/event/representative/operator bindings, `evidence_hash`/`evidence_pixel_hash`/`evidence_phash` (replay-evidence fingerprints — migrations `0030`/`0031`), created_at, expires_at, used_at; consumed by `verify_submit` to bind identity to a verified liveness proof |
| `LivenessEvidenceReservation` | (migration `0032`) Short-lived "claim ticket" for a piece of replay evidence — `evidence_hash`/`evidence_pixel_hash`/`evidence_phash`, `reserved_at`. Written atomically (under an in-process lock) at the moment `verify_check_liveness`'s replay scan finds no match, well before the corresponding `LivenessTransaction` itself is created — closes a race where two concurrent submissions of the same (transformed) evidence could otherwise both pass the perceptual-hash scan. Rows are never deleted. |
| `FaceUpdateRequest` | Pending face re-enrollment requests — requested by, reviewed by, status |
| `ApprovalRequest` | Manual review requests for borderline verifications |
| `SpecialClaimRequest` | Special claim requests (e.g., for representatives) |

### `logs` app

| Model | Contents |
|---|---|
| `AuditLog` | Structured, append-only audit trail — action type, actor, target, timestamp, IP address, details. Read-only in Django admin (UI-layer restriction); no cryptographic tamper-evidence (hash chaining, signing) is implemented — see SECURITY-CHECKLIST.md item 7.5. |

---

## 3. Runtime Data Files

These files must **never** be committed to git, deleted carelessly, or included in the installer:

| File / Folder | Description |
|---|---|
| `db.sqlite3` | SQLite database — all application data |
| `media/` | Uploaded files — beneficiary and staff photos |
| `logs/*.log` | Runtime logs — startup, watchdog, Django errors |
| `.env` | Configuration and encryption keys |
| `fans-cert.pem` / `fans-cert-key.pem` | TLS certificate and private key |

---

## 4. Backup Procedures

> **This section describes the manual/basic backup approach only.** The current, authoritative backup procedure — including the automated Task Scheduler job, backup manifest, integrity lock, and `restore-backup.ps1` — is documented in **[BACKUP-RESTORE.md](BACKUP-RESTORE.md)**. Use that document for production backup/restore; the steps below are kept for understanding the underlying SQLite mechanics only.

### 4.1 SQLite Backup

SQLite is a single file. Back it up while the server is idle (or use SQLite's online backup if you need hot backup):

```powershell
# Simple file copy (safe when Waitress is stopped):
Stop-Process -Name waitress-serve -Force -ErrorAction SilentlyContinue
$ts = Get-Date -Format 'yyyyMMdd-HHmm'
Copy-Item db.sqlite3 "D:\Backups\db.sqlite3.backup-$ts"
# Also back up .env and certs
Copy-Item .env "D:\Backups\.env.backup-$ts"
Copy-Item fans-cert.pem "D:\Backups\fans-cert.pem.backup-$ts"
Copy-Item fans-cert-key.pem "D:\Backups\fans-cert-key.pem.backup-$ts"
```

For a hot backup while the server is running (Django management command):
```powershell
.\.venv\Scripts\python.exe manage.py dbbackup
# Note: requires django-dbbackup package, not installed by default
```

Recommended backup frequency: **daily**, stored on a USB drive kept off-site or in a secure cabinet.

### 4.2 PostgreSQL Backup

```powershell
# Run on the database server:
pg_dump -U fans_user fans_db > "fans_db_backup_$(Get-Date -Format 'yyyyMMdd-HHmm').sql"
```

Or using the Django management command if `django-dbbackup` is installed.

### 4.3 What to Back Up (Priority Order)

1. **`.env`** — contains `EMBEDDING_ENCRYPTION_KEY`. Losing this makes all face data permanently unreadable. Back up separately from the database.
2. **`db.sqlite3`** (or PostgreSQL dump) — all records
3. **`media/`** — beneficiary photos
4. **`fans-cert.pem` + `fans-cert-key.pem`** — regeneratable but requires re-trusting on all client devices
5. **`staticfiles/`** — regeneratable with `collectstatic`

---

## 5. Restore Procedures

### 5.1 Restore SQLite Database

```powershell
# Stop services
.\scripts\admin\stop-fans.ps1
# Restore
Copy-Item D:\Backups\db.sqlite3.backup-YYYYMMDD-HHMM db.sqlite3
# Restore .env (IMPORTANT: must use matching EMBEDDING_ENCRYPTION_KEY)
Copy-Item D:\Backups\.env.backup-YYYYMMDD-HHMM .env
# Start services
.\scripts\start\start-fans-hidden.ps1
```

**WARNING:** The `EMBEDDING_ENCRYPTION_KEY` in `.env` **must match** the key used when the database was backed up. If you restore a database with a different encryption key, all face embeddings will be unreadable. Always restore `.env` alongside `db.sqlite3`.

### 5.2 Fresh Install with Existing Data

If reinstalling on a new machine:

1. Install the system via the installer or `setup-complete.ps1`
2. **Before running the first launch/migration**, restore `.env` with the original keys
3. Copy `db.sqlite3` from backup
4. Run `manage.py migrate` (applies any new migrations; existing data is preserved)
5. Copy `media/` folder from backup

---

## 6. Migration Management

### Viewing Applied Migrations

```powershell
.\.venv\Scripts\python.exe manage.py showmigrations
```

### Checking for Unapplied Migrations

```powershell
.\.venv\Scripts\python.exe manage.py migrate --check
```

A non-zero exit code means there are unapplied migrations. Run:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
```

### Creating New Migrations (developers only)

After changing a model:

```powershell
.\.venv\Scripts\python.exe manage.py makemigrations
.\.venv\Scripts\python.exe manage.py migrate
```

### Recent Migrations of Note (`verification` app)

| Migration | Purpose |
|---|---|
| `0030_liveness_evidence_hash` | Adds `LivenessTransaction.evidence_hash` — SHA-256 of the raw submitted frame bytes, the first replay-detection layer (exact byte-for-byte replay). |
| `0031_liveness_tx_context_binding` | Adds `evidence_pixel_hash` (SHA-256 of the decoded pixel content — catches a byte-identical replay re-saved with different metadata/compression), `evidence_phash` (perceptual dHash, compared by Hamming distance — catches a recompressed/resized replay), and `session_id` (binds a token to one verification attempt). Includes a `dedupe_evidence_hashes` data-migration step (v2.1.16 Final Hardening Patch) that resolves any pre-existing duplicate `evidence_hash`/`evidence_pixel_hash` values — keeping the oldest, blanking later duplicates, never deleting a row — before the `UniqueConstraint`s on those two fields are applied, so the migration cannot fail outright against an already-populated database. |
| `0032_liveness_evidence_reservation` | Adds the `LivenessEvidenceReservation` model (v2.1.16 Final Hardening Patch) — an atomic "claim ticket" for replay evidence, closing a race where two concurrent submissions of the same (transformed) evidence could both pass the perceptual-hash scan before either had written anything. See the model table above and `docs/SECURITY-CHECKLIST.md` item 8.43. |

### Rules for Migrations in This Project

- Do **not** squash or delete existing migrations — the production database may be at any earlier migration state
- Do **not** run `migrate --fake` unless you are certain what you are doing
- Always test migrations on a copy of the database before applying to production
- If a migration changes an encrypted field, coordinate with the backup/restore plan

---

## 7. Development vs. Production Databases

**Never use the production database for development or testing.**

| Scenario | Recommended Setup |
|---|---|
| Local development | Copy `.env.example` to `.env`, set `DEBUG=True`, use a fresh local `db.sqlite3` |
| Testing | Use Django's test runner which creates an isolated `test_` database automatically |
| Staging | Use a separate `.env` with different keys and a separate `db.sqlite3` |
| Production | `DEBUG=False`, all keys set, database backed up regularly |

To reset a development database (development only — data loss):
```powershell
Remove-Item db.sqlite3 -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py create_admin
```

---

## 8. Schema Reference (Key Tables)

### Face Embedding Storage

Face embeddings are stored in the `FaceEmbedding` model (app: `verification`):

```python
class FaceEmbedding(models.Model):
    beneficiary = models.OneToOneField(Beneficiary, ...)
    embedding   = models.BinaryField()   # Fernet-encrypted numpy array
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)
```

The embedding is a 512-dimensional float32 numpy array (keras-facenet's default checkpoint, `20180402-114759` — runtime-verified, not the 128-d checkpoints the package also ships), serialized and encrypted with Fernet before storage. Decryption requires `EMBEDDING_ENCRYPTION_KEY` from `.env`.

### Audit Log

```python
class AuditLog(models.Model):
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user        = models.ForeignKey(CustomUser, null=True, ...)  # None for anonymous actions
    action      = models.CharField(max_length=30, choices=ACTION_CHOICES)
    target_type = models.CharField(max_length=50, blank=True)    # e.g. 'user', 'beneficiary'
    target_id   = models.CharField(max_length=100, blank=True)   # PK of the affected object
    details     = models.JSONField(default=dict, blank=True)
    ip_address  = models.GenericIPAddressField(null=True, blank=True)
    user_agent  = models.CharField(max_length=500, blank=True)
    timestamp   = models.DateTimeField(auto_now_add=True)
```

Use `AuditLog.log(action, user, target_type, target_id, details, request)` — do not call `AuditLog.objects.create()` directly. Audit logs are append-only; no UI allows deleting them.

---

## 9. Database Health Checks

Run periodically to confirm database integrity:

```powershell
# Check that Django can reach the database:
.\.venv\Scripts\python.exe manage.py check

# Check for unapplied migrations:
.\.venv\Scripts\python.exe manage.py migrate --check

# Quick record count check:
.\.venv\Scripts\python.exe manage.py shell -c "
from beneficiaries.models import Beneficiary
from accounts.models import CustomUser
from logs.models import AuditLog
print('Beneficiaries:', Beneficiary.objects.count())
print('Users:', CustomUser.objects.count())
print('Audit entries:', AuditLog.objects.count())
"
```

SQLite integrity check:
```powershell
.\.venv\Scripts\python.exe manage.py dbshell
-- In the sqlite3 prompt:
PRAGMA integrity_check;
.quit
```
