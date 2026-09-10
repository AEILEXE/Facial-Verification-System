# FANS-C System Structure

**FANS-C: A Secure FaceNet-Based Facial Verification System for Senior Citizen Stipend Distribution**

This document is the master technical reference for the FANS-C system. It explains how the entire system is organized, how all parts connect, and how the system behaves during setup, startup, daily use, and failure recovery. It is intended for IT and Admin staff, developers, and thesis/capstone defense preparation.

---

## Table of Contents

1. [Repository Overview](#1-repository-overview)
2. [Full System Flow](#2-full-system-flow)
3. [Folder Map](#3-folder-map)
4. [How All Parts Connect](#4-how-all-parts-connect)
5. [Component Roles](#5-component-roles)
6. [Defense Guide — Q&A](#6-defense-guide--qa)
7. [Critical Folders](#7-critical-folders)

---

## 1. Repository Overview

FANS-C is a **LAN-based biometric verification system** deployed on a single Windows server PC inside a barangay office. Barangay staff access it using a web browser — no software needs to be installed on staff devices.

The system uses **FaceNet** (a deep learning facial recognition model) to verify the identity of senior citizens before their stipend is released. A live camera feed is captured in the browser and processed server-side by the FaceNet model.

**Core technology stack:**

| Layer | Technology |
|---|---|
| Web framework | Django 4.2 (Python) |
| Application server | Waitress (WSGI) |
| HTTPS proxy | Caddy |
| Face recognition | FaceNet via keras-facenet |
| Anti-spoofing | Texture analysis (liveness.py) |
| Database | SQLite (default) or PostgreSQL |
| Auto-start | Windows Task Scheduler |
| Self-healing | watchdog.ps1 (background monitor) |
| Certificate authority | mkcert (local CA, LAN-trusted) |

**What makes this system unusual for a capstone project:**

- It runs entirely without internet after initial setup
- It uses production-grade components (Waitress + Caddy) rather than Django's development server
- It includes a self-healing watchdog with rate limiting, cooldown, and recovery logging
- All face embeddings are stored encrypted using a Fernet key
- HTTPS is enforced (required for browser camera access)

---

## 2. Full System Flow

### 2.1 Setup Flow (IT, one time)

```
IT runs:
scripts\setup\setup-complete.ps1 (as Administrator)
        |
        |-- Step 1: setup-secure-server.ps1
        |       |-- Creates Python virtual environment (.venv)
        |       |-- Installs all dependencies (pip install -r requirements.txt)
        |       |-- Runs mkcert to generate TLS certificate (fans-cert.pem, fans-cert-key.pem)
        |       |-- Generates SECRET_KEY and EMBEDDING_ENCRYPTION_KEY into .env
        |       |-- Runs Django migrations (creates database tables)
        |       |-- Runs collectstatic (copies static files to staticfiles/)
        |       |-- Prompts to create admin account (createsuperuser)
        |
        |-- Step 2: Verify certificate files exist
        |
        |-- Step 3: Verify caddy.exe is present
        |
        |-- Step 4: Verify .env has required keys
        |
        |-- Step 5: setup-autostart.ps1
        |       |-- Registers "FANS-C Verification System" Task Scheduler task
        |       |-- This task runs start-fans-hidden.ps1 at every system boot
        |
        |-- Step 6: Optionally create desktop shortcut
        |
        |-- Step 7: Live validation
        |       |-- Starts Waitress (port 8000)
        |       |-- Waits 8 seconds
        |       |-- Starts Caddy (port 443)
        |       |-- Waits 8 seconds
        |       |-- Tests TCP connections to both ports
        |       |-- Reports PASS or FAIL
        |
        |-- Step 8: Register watchdog
                |-- Registers "FANS-C Watchdog" Task Scheduler task
                |-- This task runs watchdog.ps1 150 seconds after every boot
```

After setup completes, IT:
- Edits `.env` to set `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DEBUG=False`
- Copies `CLIENT-SETUP\` folder to a USB drive
- Runs `trust-local-cert.bat` on every staff device (as Administrator)

---

### 2.2 Startup Flow (every boot, automatic)

```
Server PC turns on
        |
        |-- Windows Task Scheduler fires: "FANS-C Verification System"
        |       |-- Runs: scripts\start\start-fans-hidden.ps1
        |       |-- Activates Python virtual environment
        |       |-- Starts Waitress: waitress-serve --listen=127.0.0.1:8000 fans.wsgi:application
        |       |-- Waits for port 8000 to respond
        |       |-- Starts Caddy: caddy run --config Caddyfile
        |       |-- Waits for port 443 to respond
        |       |-- Writes result to logs\fans-startup.log
        |
        |-- 150 seconds later, Task Scheduler fires: "FANS-C Watchdog"
                |-- Runs: scripts\admin\watchdog.ps1
                |-- Begins continuous monitoring loop (every 45 seconds)
```

Daily workflow after boot:
1. Turn on PC
2. Wait 30 seconds
3. Open browser → `https://fans-barangay.local`
4. Log in and begin processing

---

### 2.3 Browser Access Flow

```
Staff device (browser)
        |
        | HTTPS request to fans-barangay.local:443
        | (TLS certificate trusted because trust-local-cert.bat was run)
        |
        v
   [ Caddy — port 443 ]
        |
        | Plain HTTP forwarded to 127.0.0.1:8000
        | (loopback only — not accessible from network)
        |
        v
   [ Waitress — port 8000 ]
        |
        | Calls Django WSGI application
        |
        v
   [ Django — FANS-C application ]
        |
        |-- Reads .env for configuration
        |-- Queries SQLite (or PostgreSQL) for records
        |-- Renders HTML templates (templates/)
        |-- Serves static files via WhiteNoise (staticfiles/)
        |-- Processes face images via verification/ app
```

---

### 2.4 Face Verification Flow

**Important distinction:**
- *Liveness* — confirms the camera sees a real live person (not a phone/photo/screen)
- *Face verification* — confirms that live person matches the registered beneficiary

These are two separate, sequential checks. Face verification does not run if liveness fails in strict mode.

Verification uses a **two-phase LivenessTransaction flow** (introduced in v2.1.x):

```
Staff submits beneficiary for verification
        |
        v
Browser opens camera (HTTPS required)
        |
        | Captures neutral/frontal frame (before challenge)
        | Captures sequence frames during challenge
        | Head-movement challenge ALWAYS required ('side' direction)
        |   Beneficiary must visibly turn head to either side (abs yaw ≥ 5°)
        | Sends neutral_image + frames + challenge_completed to server
        |
        v
=== PHASE 1: verify_check_liveness — issues tx_token ===
        |
        |-- Replay-evidence check (atomic scan + reservation — see below)
        |   If matched: no token issued, request denied before any other gate runs
        |-- Anti-spoof neutral frame (score must be ≥ ANTI_SPOOF_THRESHOLD 0.25)
        |-- Sequence frame gate: requires ≥ 3 frames
        |   If < 3 frames: returns debug_stage='insufficient_sequence_frames', no token
        |-- PAD sequence analysis (PresentationAttackDetector.analyze_sequence)
        |-- FaceNet embedding from neutral/frontal frame (NOT the challenge frame)
        |   If embedding fails: returns debug_stage='embedding_failed', no token
        |-- If all gates pass: creates LivenessTransaction
        |   Stores embedding + session_id + challenge_direction + evidence
        |   hashes in LivenessTransaction; returns tx_token to browser
        |-- If any gate fails: no token issued; browser shows denial reason
        |
        |   --- Replay-evidence check (three layers, checked in order) ---
        |   1. evidence_hash       — exact raw-byte SHA-256 (byte-identical replay)
        |   2. evidence_pixel_hash — SHA-256 of decoded pixels (catches a
        |      byte-identical replay re-saved with different metadata/compression)
        |   3. evidence_phash      — perceptual dHash, Hamming-distance match
        |      (catches a recompressed/resized replay)
        |   check_and_reserve_liveness_evidence() (verification/views.py) runs
        |   the scan AND reserves new evidence (LivenessEvidenceReservation row)
        |   as one atomic step under an in-process lock (_liveness_replay_lock)
        |   — closes a race where two concurrent submissions of the same
        |   transformed evidence could otherwise both pass the scan before
        |   either had written anything (v2.1.16 Final Hardening Patch).
        |
        v
=== PHASE 2: verify_submit — consumes tx_token ===
        |
        |-- Validates tx_token: not expired, not used, matches beneficiary/
        |   claimant type/verification attempt (session_id)/stipend event/
        |   representative/operator, AND matches the session's CURRENT
        |   challenge direction (v2.1.16 Final Hardening Patch — a face-match
        |   retry rotates the challenge for the next attempt while keeping the
        |   same session_id; a token proven against the superseded direction
        |   is now rejected)
        |-- Consumes tx_token (one-time use, atomic claim())
        |-- Retrieves embedding from LivenessTransaction (neutral frame embedding)
        |-- Looks up beneficiary's stored embedding from database
        |-- Decrypts stored embedding (EMBEDDING_ENCRYPTION_KEY)
        |-- Computes cosine similarity
        |-- Applies threshold → VERIFIED / DENIED / MANUAL_REVIEW
        |
        v
VerificationAttempt record written to database
ClaimRecord created if VERIFIED and payout event is active (auto-verify / fallback / manual-review paths)
Staff sees result screen (simple outcome messages — no raw scores shown)
Server log records neutral_tx_embedding=True for audit
```

**Admin Override and Override Release Payout (Phase 1C)**

When verification is denied or flagged for manual review, an admin may correct
the decision. The decision correction and payout release are **two distinct steps**
with separate audit events and separate authorization checks:

```
Failed / Manual-Review VerificationAttempt
        |
        v
=== admin_override — verification/override/<id>/ ===
        |
        |-- Admin role required
        |-- Segregation of duties: performed_by cannot override (unless President)
        |-- Attempt not yet overridden (idempotency guard)
        |-- Sets VerificationAttempt.decision = VERIFIED (or DENIED)
        |-- Sets overridden=True, override_by, override_reason, override_at
        |-- Logs ACTION_OVERRIDE (decision, reason, beneficiary_id, original_score)
        |-- Does NOT create a ClaimRecord
        |-- Does NOT release a payout
        |-- Redirects to verify_result
        |
        v
Result page shows VERIFIED banner + "Admin Override Applied" card
"Release Payout" button visible if:
  - user.is_admin
  - attempt.overridden == True
  - attempt.decision == 'verified'
  - no existing ClaimRecord
  - attempt.stipend_event is not None
        |
        v
=== override_release_payout — verification/override-release/<id>/ ===
        |
        |-- GET: pre-flight guards → render confirmation page
        |         (beneficiary, stipend event, override details, PHP amount)
        |-- POST: confirm payout release
        |
        |-- Guards (re-run inside transaction.atomic() on locked objects):
        |       decision == VERIFIED
        |       overridden == True
        |       stipend_event exists
        |       beneficiary.is_eligible_to_claim (STATUS_ACTIVE + consent_given)
        |       no existing ClaimRecord for this attempt
        |       no existing STATUS_CLAIMED ClaimRecord for same beneficiary + event
        |       segregation of duties: performed_by cannot release (unless President)
        |
        |-- select_for_update() on VerificationAttempt + Beneficiary
        |-- _create_claim_record(verification_method=VERIFY_OVERRIDE)
        |-- Logs ACTION_CLAIM (via='override_release_payout', reference_number, amount)
        |-- IntegrityError from DB UniqueConstraint caught → user-facing error
        |
        v
ClaimRecord created (STATUS_CLAIMED, VERIFY_OVERRIDE)
Result page shows "Claim Recorded" section with reference number
```

**Why two steps?**
- `admin_override()` corrects a factual record (the verification decision). This is an auditable judgment call — who changed it, why, and what the original outcome was.
- `override_release_payout()` authorizes a financial disbursement. This requires a second deliberate confirmation with its own audit trail (`ACTION_CLAIM`).
- Keeping them separate allows different admins to perform each step, and allows the override to be recorded even when no stipend event is attached (in which case the release button never appears).

### 2.4b Registration Liveness Flow

Note: Registration uses a **risk-based** challenge (only when anti-spoof score < 0.30 or quality poor).
Final stipend verification uses a **strict mandatory** challenge (always). These are different flows.

```
Staff captures face during beneficiary registration
        |
        v
Browser client-side anti-spoof pre-check
        | If score ≥ 0.30 and quality good:
        |   No challenge (registration risk-based — reduces burden on elderly beneficiaries)
        | If score < 0.30 or quality poor:
        |   Head-movement 'side' challenge required before face can be submitted
        | Anti-spoof hard threshold (0.25) still blocks phone screens regardless
        | Sends image + liveness signals to server
        |
        v
beneficiaries/views.py — Server-side registration liveness gate
        |-- Server computes anti-spoof score independently
        |-- If REGISTRATION_LIVENESS_REQUIRED=True (default):
        |       If score < ANTI_SPOOF_THRESHOLD (0.25): REJECTED
        |       If server_challenge_required and challenge_completed=false: REJECTED
        |-- If liveness passes:
        |       FaceNet generates embedding
        |       Embedding encrypted + stored
        |       Beneficiary saved as pending
        |
        v
Duplicate face detection
        |-- New embedding compared to all existing embeddings
        |-- If similarity ≥ threshold: duplicate detected
        |       Record saved as pending with duplicate_review_required=True
        |       Admin duplicate review queue notified
        |       (NOT blocked, NOT auto-approved)
        |-- If no duplicate:
        |       Normal pending / auto-approval workflow
```

---

### 2.5 Watchdog Recovery Flow

```
watchdog.ps1 runs every 45 seconds
        |
        |-- Check Waitress:
        |       Is waitress-serve.exe process running?
        |       Is port 8000 accepting TCP connections?
        |       Does Django respond to HTTP probe on port 8000?
        |
        |-- If Waitress is healthy: continue
        |
        |-- If Waitress is unhealthy:
        |       Check restart history (prune entries older than 10 minutes)
        |       If restart count >= 3: log [ALERT], stop attempting, wait for IT
        |       If cooldown active (< 60s since last restart): log [WAIT], skip
        |       If port recovered on its own: log [SKIP], no action needed
        |       Otherwise:
        |               Kill any stale Waitress process
        |               Start new Waitress instance
        |               Wait for initialization
        |               Re-check port
        |               If recovered: log [OK]
        |               If still down: log [FAIL]
        |
        |-- Same logic applied to Caddy (port 443)
        |
        |-- Log result to logs\fans-watchdog.log
        |-- If all healthy: log [HEALTHY] (once every ~7.5 minutes to reduce noise)
```

---

### 2.6 Diagnostic Flow

```
IT suspects a problem
        |
        v
Run: scripts\admin\check-system-health.ps1
        |
        |-- Checks (read-only, no changes made):
        |       1. Waitress process and port 8000
        |       2. Caddy process and port 443
        |       3. TLS certificate files present and current
        |       4. .env configuration (required keys set, DEBUG mode)
        |       5. Task Scheduler auto-start task (state, last run)
        |       6. Task Scheduler watchdog task (state, last run)
        |       7. Recent entries from logs\fans-startup.log
        |       8. Recent entries from logs\fans-watchdog.log
        |
        v
Prints [OK] / [FAIL] / [WARN] for each check
Prints specific fix instructions for any failures
```

---

### 2.7 Payout Approval & Distribution-Window Flow

```
Admin creates a StipendEvent
        |
        v
approval_status = pending_approval  (NOT usable for claiming yet)
        |
        v
President approves
        |
        v
approval_status = approved  ("published")
        |
        v
Is today within [payout_start_date, payout_end_date]?  -- get_active_event_for_date()
   |no                              |yes
   v                                v
Not the active event      Is now within [payout_start_time, payout_end_time]
                           (if a daily window is set)?   -- is_within_time_window()
                                |no                |yes
                                v                  v
                  Dashboard shows SCHEDULED   get_open_events_now() includes it
                  (opens at <start time>)     Dashboard shows OPEN
                  Verify: "No active payout   Verify: claiming allowed
                  event is scheduled"
```

President-created schedules publish (`approved`) immediately — no separate
approval step. `get_active_event_for_date()` (date-window only) and
`get_open_events_now()`/`is_within_time_window()` (date **and** time
window) are deliberately different queries: the former is used where a
broader "is there an event around today" answer is appropriate, the
latter is the actual claim gate. Both the dashboard and the Verify page
now consult the time-window check (v2.1.16) so the dashboard's status
badge (`OPEN` / `SCHEDULED` / `UPCOMING` / `PENDING APPROVAL` / `INACTIVE`)
never claims an event is claimable when Verify would actually refuse it.

---

### 2.8 Notification Flow

```
Something notification-worthy happens
(duplicate face detected, security alert, approval pending, ...)
        |
        v
logs.notifications.notify_admins(category, title, message, url, dedupe_key)
   (or notify_user() for a specific user)
        |
        v
logs.Notification row created — category, priority (LOW/MED/HIGH),
dedupe_key prevents re-notifying for the same underlying event
        |
        v
Recipient is role-gated (President/Admin/IT for notify_admins)
        |
        v
Navbar bell badge shows unread count; dropdown lists recent items
        |
        v
Click -> notification_open (marks read, redirects to `url`)
        |
        v
Underlying case decided (e.g. duplicate-face reviewed) ->
resolve_notification(dedupe_key) marks matching notifications resolved
```

---

## 3. Folder Map

```
project root/
|
|-- fans/                       Django project configuration (settings, URLs, WSGI)
|-- accounts/                   User authentication and role management
|-- beneficiaries/              Beneficiary records and management
|-- verification/               FaceNet facial verification engine
|-- logs/                       Django app: AuditLog model + audit UI (PERMANENT — Django app)
|
|-- templates/                  HTML templates (all pages)
|-- static/                     Source static files (CSS, JS, images)
|-- staticfiles/                Collected static files served in production
|-- media/                      User-uploaded files (NOT included in installer payload)
|-- assets/                     Additional frontend assets
|
|-- scripts/
|   |-- setup/                  One-time setup scripts (IT)
|   |-- start/                  Service launcher scripts
|   |-- admin/                  Diagnostic and maintenance scripts (IT)
|
|-- CLIENT-SETUP/               Scripts to run on each staff device (once)
|-- tools/                      External binaries (caddy.exe, mkcert.exe)
|-- docs/                       Technical documentation (this folder)
|-- dev/                        Developer utilities
|   |-- build_exe.ps1           PyInstaller orchestration
|   |-- installer/fans_c.iss    Inno Setup script
|   |-- build-logs/archive/     Archived top-level build transcripts (gitignored)
|-- legacy/                     Deprecated or archived files
|
|-- .venv/                      Python virtual environment (auto-created by setup)
|-- db.sqlite3                  SQLite database (default storage; NOT in installer payload)
|-- .env                        Environment configuration (secrets, settings; NOT in installer payload)
|-- .env.example                Template for .env
|-- Caddyfile                   Caddy HTTPS reverse proxy configuration
|-- manage.py                   Django management entry point
|-- requirements.txt            Python package dependencies
|-- fans-cert.pem               TLS certificate (generated by mkcert during setup)
|-- fans-cert-key.pem           TLS private key (generated by mkcert during setup; NOT in installer payload)
```

### Files and folders that must NOT move

These paths are referenced by code, settings, scripts, or the installer and
moving them would break the system:

- Django apps: `accounts/`, `beneficiaries/`, `verification/`, `fans/`, `logs/`
- `templates/`, `static/`, `staticfiles/`, `media/`, `assets/`
- `manage.py`, `requirements.txt`, `Caddyfile`
- `CLIENT-SETUP/`, `tools/`, `scripts/`
- `dev/installer/fans_c.iss`, `dev/build_exe.ps1`, `dev/launcher.py`,
  `dev/fans_c.spec`
- Top-level docs: `README.md`, `SETUP.md`, `CHANGELOG.md`,
  `CLIENT_ACCESS.md` (if present)

### Installer payload — must NOT include

`dev/build_exe.ps1` and `dev/installer/fans_c.iss` already exclude these. The
v2.1.12 payload safety scan re-confirms:

- `db.sqlite3` (runtime database)
- `.env` (secrets / encryption key)
- `logs/*.log` (runtime log files)
- `media/` (user-uploaded images)
- raw face/liveness images (never stored on disk in any case)
- private keys / TLS certificates (`*-key.pem`)
- local test data / dev fixtures
- `dev/build-logs/archive/` (build transcripts)

---

## 4. How All Parts Connect

### Django ↔ Waitress

Waitress is the production WSGI server for Django. It imports the WSGI callable from `fans/wsgi.py` and listens on `127.0.0.1:8000`. Django never handles TCP directly — Waitress does that and passes requests to Django as WSGI calls.

### Waitress ↔ Caddy

Caddy receives all incoming HTTPS traffic on port 443, terminates TLS (decrypts the connection), and forwards the request as plain HTTP to Waitress on `127.0.0.1:8000`. Caddy adds `X-Forwarded-Proto: https` and `X-Real-IP` headers so Django knows the original protocol and client IP. Django uses these headers because `SECURE_PROXY_SSL_HEADER` and `USE_X_FORWARDED_HOST` are set in `.env`.

### Django ↔ Database

Django uses its ORM (Object-Relational Mapper) to read and write records. By default the database is `db.sqlite3` (SQLite) in the project root. The `USE_SQLITE` setting in `.env` controls whether SQLite or PostgreSQL is used. All beneficiary records, user accounts, and verification logs are stored in the database.

### Django ↔ FaceNet

The `verification` Django app contains `face_utils.py`, which loads the keras-facenet model and runs inference. When a verification request arrives, Django calls functions in `face_utils.py` to:
1. Detect and crop the face (MTCNN)
2. Generate a face embedding (FaceNet, 512-dimensional vector)
3. Decrypt the stored embedding from the database
4. Compute cosine similarity and return a match decision

### Django ↔ Media Folder

When a beneficiary's face image is uploaded during registration, Django stores it in the `media/` folder. Face embeddings (the numerical representation produced by FaceNet) are stored encrypted in the database using the `EMBEDDING_ENCRYPTION_KEY` from `.env`.

### Django ↔ Templates and Static Files

Django renders HTML responses using templates from the `templates/` folder. Static files (CSS, JavaScript, images) are collected into `staticfiles/` by `collectstatic` and served in production by WhiteNoise (a middleware that serves static files directly from Django/Waitress without a separate file server).

### Watchdog ↔ Task Scheduler ↔ Services

Windows Task Scheduler manages two background tasks:
- **FANS-C Verification System** — starts Waitress and Caddy at boot
- **FANS-C Watchdog** — starts the watchdog 150 seconds after boot

The watchdog does not use Task Scheduler to restart services. It directly calls PowerShell commands to kill and relaunch Waitress and Caddy processes when it detects a failure.

### mkcert ↔ Caddy ↔ Client Browsers

mkcert generates two files: `fans-cert.pem` (the TLS certificate) and `fans-cert-key.pem` (the private key). These are referenced in the `Caddyfile` and loaded by Caddy to enable HTTPS. mkcert also creates a local Certificate Authority (CA). The `trust-local-cert.bat` script installs this CA on each staff device, which causes the browser to trust the FANS-C certificate without a warning.

---

## 5. Component Roles

| Component | Role | What breaks without it |
|---|---|---|
| Django | Application logic, web pages, database access, verification | Nothing works |
| Waitress | Serves Django over HTTP on port 8000 | Django is unreachable |
| Caddy | HTTPS termination on port 443 | Browser cannot connect (no HTTPS = no camera access) |
| FaceNet (keras-facenet) | Generates face embeddings for matching | Verification returns random or mock results |
| MTCNN | Detects and crops faces from images | FaceNet cannot process raw images |
| SQLite / PostgreSQL | Stores all records | No data persists between requests |
| Task Scheduler | Auto-starts services at boot | System requires manual start after every reboot |
| watchdog.ps1 | Detects and recovers from service crashes during the day | Failures require IT intervention to fix |
| mkcert CA + trust-local-cert.bat | Browser trusts the local HTTPS certificate | Certificate warnings block camera access |
| .env | All configuration and encryption keys | System fails to start or loads defaults that break security |
| EMBEDDING_ENCRYPTION_KEY | Encrypts/decrypts stored face embeddings | All stored face data becomes unreadable |

---

## 6. Defense Guide — Q&A

### Why Waitress + Caddy instead of Django's built-in server?

Django's built-in development server (`manage.py runserver`) is explicitly not suitable for production. It is single-threaded, does not handle concurrent requests well, and has no security hardening. Waitress is a production-grade WSGI server that handles concurrent requests safely. Caddy is added on top to handle HTTPS, which is required for the browser camera API (`getUserMedia`) to work — browsers refuse camera access on plain HTTP.

### Why HTTPS? Why not just HTTP?

Modern browsers enforce the Web Security Model: the `navigator.mediaDevices.getUserMedia()` API (used to access the camera) is only available in secure contexts — meaning the page must be served over HTTPS or from localhost. Since staff devices access the system over a LAN (not localhost), HTTPS is mandatory. Without HTTPS, the camera button will silently fail or not appear at all.

### Why use mkcert instead of a real SSL certificate?

Real SSL certificates (from Let's Encrypt or a commercial CA) require a publicly accessible domain name and internet access to verify domain ownership. FANS-C runs on a private LAN with no public domain — `fans-barangay.local` is not resolvable from the internet. mkcert creates a locally-trusted CA that signs the certificate for the local domain. Once that CA is installed on staff devices, the certificate is trusted exactly like a real one — no browser warnings, full camera access.

### Why is the watchdog a separate script and not part of the main startup?

The watchdog needs to continue running after the startup script has finished. If it were part of the startup script, it would block the startup script from completing. As a separate Task Scheduler task, it starts 150 seconds after boot (giving the main startup time to complete) and then runs indefinitely in the background, independent of any other process.

### Why the 150-second delay before the watchdog starts?

The main startup task (FANS-C Verification System) needs time to:
1. Activate the Python virtual environment
2. Start Waitress (Django takes 4-8 seconds to initialize; FaceNet model download takes up to 90 seconds on first run)
3. Start Caddy

If the watchdog started immediately at boot, it would see both services as down and immediately attempt a restart, which would conflict with the normal startup already in progress and could result in duplicate processes.

### How does the system recover from a crash?

1. Watchdog detects the failed service within 45 seconds (the monitoring interval)
2. Checks if a restart should be attempted (cooldown, max attempt limits)
3. Kills any stale process to ensure a clean state
4. Starts a fresh process
5. Waits for the port to respond, then confirms recovery
6. If recovery fails after 3 attempts in 10 minutes, logs an [ALERT] and stops retrying

Staff do not see this recovery — the system comes back on its own within about 45-60 seconds of a crash. If the system cannot recover automatically, IT is alerted via the watchdog log.

### What happens if the EMBEDDING_ENCRYPTION_KEY is lost?

Face embeddings (the numerical fingerprints of enrolled faces) are stored in the database encrypted with this key. If the key is lost or changed, the encrypted embeddings cannot be decrypted — all enrolled beneficiaries would need to be re-registered. This key must be backed up securely outside the server. It is stored in the `.env` file in the project root.

### Why store face embeddings instead of raw photos?

Face embeddings (512-dimensional floating-point vectors) are a compact numerical representation of a face. They are:
- Smaller than photos (much less storage)
- Encrypted at rest (secure)
- Not directly reversible to the original image (unlike photos, embeddings cannot trivially reconstruct someone's appearance)
- Faster to compare (cosine similarity of two vectors vs. running a full model on two images)

### What is the difference between liveness and face verification?

These are two separate, sequential security checks:

| | Liveness | Face Verification |
|---|---|---|
| **What it checks** | Is the camera seeing a real live person? | Is that live person the correct registered beneficiary? |
| **Blocks what** | Phone screens, printed photos, replay attacks | People whose face does not match the enrolled record |
| **Runs when** | Always, first | Only after liveness passes (strict mode) |
| **Server authoritative?** | Yes — server result always wins | Yes |
| **Failure action** | Denies verification before face matching runs | Denies verification after face matching |

Liveness alone does not identify the person. Face verification alone does not confirm the person is physically present. Both are required for a secure outcome.

### Why are liveness score details hidden from operators?

When the client-side (browser) score and the server-side score diverge slightly, this is a normal result of measurement variation — not necessarily a tampering attempt. Showing raw score numbers, labels like "client spoof" and "server measured," or explanations of proxy trust to barangay staff creates alarm without providing actionable information. The operator's job is to confirm the outcome (verified/denied), not to interpret biometric scores. Technical details are stored in the audit log for IT review when needed.

### Why is the head-movement challenge always required for stipend verification?

The head-movement challenge is a **strict mandatory gate** for final stipend verification. Every verification attempt — regardless of anti-spoof score or face quality — must complete the challenge before FaceNet face matching runs. This eliminates the risk that a high-texture printed photo or a phone screen captures a score above the soft threshold and bypasses active liveness confirmation.

The challenge direction is `'side'`: the beneficiary must visibly turn their head to either side. Because the challenge accepts movement in either direction, it accommodates the mirrored camera preview without requiring the operator to instruct "your left" vs. "screen left."

For **face registration** only, the challenge is risk-based: it is required when the anti-spoof score is borderline (< 0.30) or when capture quality is poor. A clearly live face scored at ≥ 0.30 with good quality does not need the challenge during enrollment, reducing burden on elderly senior citizens during a one-time setup step. The anti-spoof hard threshold (0.25) still blocks phone screens at registration regardless of challenge outcome.

### What happens when a duplicate face is detected at registration?

The registration is not blocked outright (which would leave a legitimate twin with no path forward) and is not auto-approved (which would allow fraud to proceed). Instead:
- The record is saved as `pending` with `duplicate_review_required=True`
- It appears in the Admin Duplicate Review Queue
- President, Admin, or IT must manually review and decide: approve (legitimate twin) or reject (fraud)
- The record cannot claim stipends until resolved
- All decisions are recorded in the audit log

### What is the difference between DEMO_MODE and the model being unavailable?

`DEMO_MODE=True` lowers the face similarity threshold and makes liveness non-blocking. Real face matching still runs if the FaceNet model loaded correctly. If `keras-facenet` fails to load (or, on a clean machine, the one-time weights download hasn't succeeded), FANS-C fails closed: registration and verification are blocked with a clear "model unavailable" message rather than falling back to a mock model with random similarity scores. You can check `/verification/config/` in the web interface to see which state the model is in.

### Why must client devices run trust-local-cert.bat?

Each client device has its own browser trust store — a list of Certificate Authorities it trusts. The FANS-C server's HTTPS certificate was signed by the mkcert CA created on the server. That CA is not in any browser's default trust store. `trust-local-cert.bat` installs the server's CA into the Windows trust store on the client device, which causes Chrome, Edge, and Firefox to automatically trust any certificate signed by that CA — including the FANS-C certificate.

### What is the role of the Caddyfile?

The Caddyfile is Caddy's configuration file. It tells Caddy:
- Which domain to serve (`fans-barangay.local`)
- Which port to listen on (443, HTTPS)
- Which TLS certificate files to use (`fans-cert.pem`, `fans-cert-key.pem`)
- Where to forward requests (127.0.0.1:8000, Waitress)
- Which headers to add (X-Forwarded-Proto, X-Real-IP)

Without the Caddyfile, Caddy does not know any of this and will not start correctly.

### Why does the project need to be at a short path like D:\FANS?

Windows limits file paths to 260 characters by default. TensorFlow's Python wheel (the package that keras-facenet depends on) contains deeply nested file paths that exceed this limit when extracted in a deeply nested project folder. The symptom is `pip install` failing with a "No such file or directory" error even though the directory exists. Placing the project at `D:\FANS` keeps all paths short enough to avoid this.

### What is the role of WhiteNoise?

WhiteNoise is a Python middleware for Django that enables serving static files (CSS, JavaScript, images) directly from the Django/Waitress process without a separate web server. In a typical production Django setup, a separate server (like Nginx) serves static files. Because FANS-C uses Caddy only as an HTTPS proxy (not a file server), WhiteNoise fills that role within the Waitress process.

---

## 7. Critical Folders

These folders are required for the system to function. Missing or corrupted content in any of them will prevent the system from working.

### `.venv/` — Python Virtual Environment

**What it contains:** The Python interpreter, all installed packages (Django, Waitress, TensorFlow, keras-facenet, etc.)

**What breaks if missing:** The system cannot start at all. Waitress is a Python command and requires the virtual environment.

**How to recreate:** Run `scripts\setup\setup-complete.ps1` or `scripts\setup\setup-secure-server.ps1` as Administrator. Do not move the project folder after creating the venv — the venv stores absolute paths and breaks if moved.

---

### `.env` — Configuration and Secrets

**What it contains:** `SECRET_KEY`, `EMBEDDING_ENCRYPTION_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, database settings, face recognition thresholds.

**What breaks if missing:**
- Django refuses to start (SECRET_KEY required)
- Stored face embeddings become permanently unreadable (EMBEDDING_ENCRYPTION_KEY required)
- Staff browsers are blocked by Django's CSRF/ALLOWED_HOSTS checks

**How to recreate:** Run `setup-complete.ps1` to generate a new `.env`. WARNING: This generates a new `EMBEDDING_ENCRYPTION_KEY`, which makes all previously stored face embeddings unreadable. Back up the original `.env` file and restore the original key.

---

### `fans-cert.pem` and `fans-cert-key.pem` — TLS Certificate

**What they contain:** The HTTPS certificate and private key for `fans-barangay.local`, generated by mkcert.

**What breaks if missing:** Caddy cannot start (it requires the certificate files). The system cannot serve HTTPS. Browser camera access stops working.

**How to recreate:** Run `setup-secure-server.ps1`. The new certificate has a different fingerprint — you may need to re-run `trust-local-cert.bat` on client devices if the browser starts warning again.

---

### `db.sqlite3` — Database

**What it contains:** All beneficiary records, user accounts, verification logs, stipend event records.

**What breaks if missing:** The application starts but has no data. All registrations and verification history is lost.

**How to recover:** Restore from backup. If no backup exists, run `python manage.py migrate` to create an empty database, but all data is permanently lost.

---

### `staticfiles/` — Production Static Files

**What it contains:** Collected copies of all CSS, JavaScript, and images, ready for WhiteNoise to serve.

**What breaks if missing:** Web pages load without styles and scripts. The application may be partially usable but the interface is broken.

**How to recreate:** Run `python manage.py collectstatic --noinput` (with the virtual environment activated).

---

### `tools/caddy.exe` — HTTPS Proxy

**What it contains:** The Caddy binary, a single self-contained executable.

**What breaks if missing:** HTTPS cannot start. Staff browsers cannot connect. Camera access is blocked.

**How to fix:** Download `caddy.exe` from the official Caddy website and place it at `tools\caddy.exe`.

---

*For folder-level documentation, see the individual docs in this folder:*
- [folder-scripts.md](folder-scripts.md) — scripts/ folder
- [folder-django-app.md](folder-django-app.md) — Django application folders
- [folder-static-templates.md](folder-static-templates.md) — templates, static, staticfiles, media
- [folder-logs.md](folder-logs.md) — logs/ folder
- [folder-client-setup.md](folder-client-setup.md) — CLIENT-SETUP/ folder
