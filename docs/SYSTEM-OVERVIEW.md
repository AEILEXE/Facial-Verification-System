# FANS-C System Overview

**FANS-C: A Secure FaceNet-Based Facial Verification System for Senior Citizen Stipend Distribution**

**Version:** v2.1.16 — 2026-09-05 (branch `4.0-Final-v2.1.16-hardening`; includes the v2.1.16 Post-UAT Stabilization pass and the v2.1.16 Final Hardening Patch). Development between 2026-08-27 and 2026-09-02 was tracked internally under the "v2.2.0" / "Post-UAT hardening" milestone labels — that work shipped under this v2.1.x version line, not as a separate v2.2.0 release; see [README.md](../README.md#latest-release) for the full note. Supersedes the "Phase 1 Complete" snapshot below; see [CHANGELOG.md](../CHANGELOG.md) for the authoritative version/date history.
**Audience:** Capstone evaluators, barangay administrators, IT staff, developers

---

## 1. System Purpose

FANS-C is a biometric identity verification system deployed at the barangay level to manage senior citizen stipend distribution. It replaces paper-based sign-in lists and manual identity checks with secure facial recognition, ensuring that:

- Only registered senior citizens (or their authorized representatives) can claim a stipend
- Every claim is tied to a verified biometric identity event, recorded permanently
- Spoof attacks using phones, photos, and screens are blocked before any match runs
- All decisions — approval, denial, override, duplicate review — are audit-logged

The system runs on **one dedicated server PC** inside the barangay office. Staff use any web browser on the same local network; no software needs to be installed on their devices.

---

## 2. User Roles

| Role | DB value | What they can do |
|---|---|---|
| **President** | `president` | All operational tasks: register beneficiaries, run verification, approve/deny claims and manual reviews, manage users, run reports, reset passwords for all roles |
| **Admin** | `admin` | Administrative tasks: register, approve claims/manual-reviews, manage users and officer assignments, run reports, reset Staff passwords |
| **IT** | `it` | All President and Admin permissions + system diagnostics, connection info, network setup pages |
| **Staff** | `staff` | Register beneficiaries, run verification, submit manual-review or special-claim requests; no user management, no reports, no approvals |

> **System Role vs Officer Position (v2.1.12).**
> *System Role* (table column "System Role / Access Role") controls software permissions — what menus and actions a user can access.
> *Officer Position* (org-chart title — President, VP, Secretary, etc.) controls the Organization Chart and is recorded as an `OfficerAssignment`.
> The two are intentionally separate: a "President" System Role gives software permissions; a "President" Officer Position puts the user on the org chart. Creating a President user via the User form now creates the linked Officer Assignment in the same flow. Only one active user may hold `System Role = President`; inactive/suspended former Presidents remain visible for audit.
| **Beneficiary** | — | Senior citizen being served; not a system user account |
| **Authorized Representative** | — | Person authorized to claim on behalf of a beneficiary; has their own enrolled face |

**Legacy roles (historical only — no longer assignable):**

| Legacy Role | DB value | Migration |
|---|---|---|
| Head Barangay | `head_brgy` | Migrated → `president` (accounts/0009) |
| IT/Admin | `admin_it` | Migrated → `admin` (accounts/0006) |

Roles are enforced in the Django views, not just in the UI. A Staff user cannot access admin pages even by typing the URL directly.

---

## 3. Main Modules

| Module | Django app | Purpose |
|---|---|---|
| Beneficiary management | `beneficiaries` | Register, search, view, and manage senior citizen records |
| Representative management | `beneficiaries` | Enroll and manage authorized representatives per beneficiary |
| Face registration | `beneficiaries` | Capture face, run registration liveness, create encrypted face embedding |
| Registration approval | `beneficiaries` | Auto-approval or admin approval of new beneficiary records |
| Duplicate face review | `beneficiaries` | Admin queue for resolving potential duplicate/twin detections at registration |
| Face verification | `verification` | Confirm identity at payout: liveness → face match → claim creation |
| Liveness detection | `verification` | Anti-spoofing texture check + mandatory head-movement 'side' challenge for final verification |
| Stipend events / payouts | `verification` | Schedule and manage multi-day distribution events, each requiring President approval (admin-created schedules) before claims can be processed against them; an optional daily claiming time window can further restrict when a published event is actually open |
| Claims | `verification` | Record verified claims tied to payout events |
| Reports | `verification` | Claims, event summary, staff performance, override/fallback, suspicious attempts |
| Audit logs | `logs` | Permanent, tamper-evident record of every significant system action |
| Verification logs | `logs` | Full detail of every face verification attempt |
| Notifications | `logs` | In-app notification center (navbar bell) for duplicate-face reviews, security alerts, and pending approvals — role-gated to President/Admin/IT |
| User management | `accounts` | Create, edit, deactivate/suspend (reason required, entered via a modal) staff/admin accounts |
| System diagnostics | `verification` | HTTPS proxy diagnostics, connection info (IT only) |
| Installer/deployment | — | Windows installer, Caddy HTTPS proxy, Waitress WSGI server, watchdog |

---

## 4. Liveness vs. Face Verification

These are two independent, sequential security checks. They must not be confused.

```
Liveness  =  real person check
Face verification  =  correct person check
```

| | Liveness | Face Verification |
|---|---|---|
| **Question answered** | Is the camera seeing a living human being? | Is that person the correct registered beneficiary? |
| **Blocks** | Phone screens, printed photos, replay attacks | Identity fraud by the wrong person |
| **Runs** | First, always | Only after liveness passes (strict mode) |
| **Who decides** | Server (client score is advisory only) | Server |
| **Failure action** | Deny — face match never runs | Deny — face match ran but score below threshold |

**Liveness alone does not identify the person.**
**Face verification alone does not confirm the person is physically present.**
Both checks are required for a secure verification outcome.

**Server authority:** The server always makes the final liveness decision. The browser runs a pre-check (MediaPipe texture score) as a usability aid. If the client and server scores diverge, the server result is used. Technical mismatch details are stored in the audit log — they are not shown in the normal operator UI.

---

## 5. Registration Workflow

```
Staff logs in
  → Registers beneficiary info (name, SC ID, DOB, address, contact, valid ID)
  → Optionally registers authorized representative info
  → Face capture page opens camera
  → Client-side anti-spoof pre-check runs
      → Strong live capture (score ≥ 0.30, quality OK) → fast path, no challenge
      → Borderline / poor quality → head-movement challenge required
  → Server validates liveness independently
      → Anti-spoof < 0.25 → REJECTED (phone/screen/photo)
      → Challenge required but not completed → REJECTED
      → Liveness passes → FaceNet generates encrypted embedding
  → Duplicate face detection
      → Duplicate found → record pending, admin review queue
      → No duplicate → normal pending / auto-approval workflow
  → Admin approves or rejects pending records
```

Key security properties:
- Anti-spoof and challenge are both enforced server-side — a tampered browser cannot bypass them
- Phone screens, printed photos, and replay videos cannot be enrolled as face embeddings
- Duplicate detections do not hard-block or auto-approve — they require admin review

---

## 6. Verification Workflow

```
Staff searches for beneficiary
  → Selects beneficiary (or beneficiary + representative for rep claim)
  → Camera opens (HTTPS required)

  ── Phase 1: Liveness proof (server-issued tx_token) ──────────────────────

  → Camera stabilises; neutral/frontal frame captured automatically
      (This is the frame used for identity embedding — face must be looking
       directly at the camera, not turned.)
  → Head-movement 'side' challenge required
      → Beneficiary must visibly turn head to one side and return
  → Browser sends to server:
      • proof/challenge frame (turned face)
      • neutral/frontal frame (pre-challenge, forward-facing)
      • sequence of motion frames (minimum 3)
  → SERVER liveness gate (authoritative):
      → Replay-evidence check (atomic scan + reservation, see below)
          → Matches prior evidence → REJECTED — no tx_token issued
      → Anti-spoof check on neutral frame
          → Score < 0.25 → REJECTED (phone/screen/photo — no tx_token issued)
      → Sequence frame count < 3 → REJECTED (no movement — no tx_token issued)
      → PAD (presentation attack detection) on sequence frames
          → Score ≥ PAD threshold (deny mode) → REJECTED — no tx_token issued
      → FaceNet embedding computed from neutral/frontal frame
          → Embedding fails → REJECTED — no tx_token issued
      → All gates pass → tx_token issued (LivenessTransaction record created)
          • tx_token is a one-time, time-limited, beneficiary-bound token
          • Embedding from neutral frame stored encrypted in LivenessTransaction
          • Bound to this attempt's session_id, stipend event, representative,
            operator, and the SERVER-assigned challenge direction (see below)

  ── Replay-evidence check (three layers, atomic) ──────────────────────────

  → evidence_hash: exact raw-byte SHA-256 of the submitted frames
      → Matches a prior LivenessTransaction/reservation → REJECTED
  → evidence_pixel_hash: SHA-256 of the DECODED pixel content (catches a
    byte-identical replay re-saved with different metadata/compression)
      → Matches → REJECTED
  → evidence_phash: perceptual dHash, compared by Hamming distance (catches
    a recompressed/resized replay that changes both raw bytes and exact
    pixel values but not the visual content)
      → Within the similarity threshold of any prior evidence → REJECTED
      → No match → this evidence is immediately RESERVED (a
        LivenessEvidenceReservation row is written) under an in-process
        lock, so a second, concurrent submission of the same evidence
        cannot also pass this scan before the first request's actual
        LivenessTransaction is created further down the pipeline

  ── Phase 2: Identity verification (tx_token consumed) ───────────────────

  → Browser sends tx_token + submitted frame to server
  → tx_token validated:
      → Missing → DENIED
      → Expired (> 2 min) → DENIED
      → Already used → DENIED (single-use)
      → Wrong beneficiary → DENIED
      → Wrong claimant type → DENIED
      → Wrong verification attempt (session_id changed since issuance) → DENIED
      → Wrong stipend event / representative / operator → DENIED
      → Challenge direction no longer matches the session's CURRENT
        challenge (a retry rotates it) → DENIED
  → Liveness confirmed via TX stored scores:
      → TX anti_spoof_score < 0.25 → DENIED
      → TX pa_score ≥ PAD threshold → DENIED
  → FaceNet identity comparison (uses embedding from LivenessTransaction):
      → Score < threshold → NOT VERIFIED
      → Score ≥ threshold → VERIFIED
      → Score in review band (≥85% of threshold) → MANUAL REVIEW
  → Lookalike safety gate (if VERIFIED):
      → Another beneficiary matches within 0.05 band → escalate to MANUAL REVIEW
  → Result displayed (Verified / Manual Review / Not Verified / Denied)
  → Claim created if Verified and the selected event is still claimable right
      now (re-checked at this instant — see Finalization-Within-Window
      Policy below; the identity result stays Verified even when the claim
      cannot be created)
  → tx_token consumed (marked used)
  → All details written to VerificationAttempt record + AuditLog
```

**Admin Override and Payout Release (two separate steps)**

When a verification is denied or flagged for manual review, an admin may correct
the decision using the Admin Override action. The override and the payout release
are **intentionally separate steps**:

```
Failed / Manual-Review verification attempt
        ↓
Admin Override (verification/override/<id>/)
        ↓  Changes VerificationAttempt.decision to VERIFIED
        ↓  Sets overridden=True, records reason
        ↓  Logs ACTION_OVERRIDE (who, why, previous decision)
        ↓  Does NOT create a ClaimRecord
        ↓
VERIFIED decision on result page
        ↓
Override Release Payout (verification/override-release/<id>/)
        ↓  Admin reviews: beneficiary, stipend event, override details
        ↓  Confirms payout release (POST with CSRF)
        ↓  Eligibility re-checked (beneficiary must still be Active + consent given)
        ↓  No existing ClaimRecord for this attempt (duplicate guard)
        ↓  No existing STATUS_CLAIMED record for same beneficiary + event
        ↓  select_for_update() on VerificationAttempt + Beneficiary
        ↓  ClaimRecord created (verification_method = admin_override)
        ↓  Logs ACTION_CLAIM (who, reference number, via='override_release_payout')
        ↓
Payout released — normal claim workflow continues
```

**Important:** `admin_override()` changes the verification decision only. It does
**not** create a ClaimRecord and does **not** directly release a payout. The
Release Payout step is a separate, second admin action with its own audit trail.
This design preserves two independent audit events — one for the decision correction
and one for the financial release — and allows different admins to perform each step.

**Finalization-Within-Window Policy (Phase B.5)**

A stipend event is selected and bound once, at the start of verification
(`verify_start` — or explicitly, via the event chooser, when more than one
event is simultaneously open). That binding does not change for the rest of
the workflow: it survives through Manual Review, Admin Override, and
Special Claim approval, and it is never silently swapped for a different
event, even if another event is open at finalization time.

However, a bound event is only a candidate — it must still satisfy the
*actual* eligibility rules at the moment a ClaimRecord is about to be
created, not merely when verification began. Approval can be revoked, an
event can be deactivated, or the claiming window can simply run out while a
Manual Review or Override sits pending. All financial finalization paths —
the direct automatic VERIFIED path, Manual Review approval, Admin Override
release, and Special Claim approval — call the same shared check
(`StipendEvent.check_claim_eligible_now()`, wrapped for locking by
`views._lock_event_for_finalization()`) immediately before creating the
ClaimRecord, inside the same database transaction. That check verifies, in
order: the event is still active, still approved/published, still within
its date range, still within its own payout time window (if one is set),
and still within the system's global 07:00–20:00 Asia/Manila same-day
claiming hours (this last check is the only one with any effect on a
blank-time "all day" event — an event with explicit times is already
constrained to fall within 07:00–20:00 when it is created or edited).

**Biometric result and payout eligibility are independent.** A closed
window, a deactivated event, or a revoked approval is a financial/workflow
restriction — it never rewrites the identity outcome. A VERIFIED face match
stays VERIFIED; only the ClaimRecord creation is blocked. The result page,
the reviewer's approval message, and the AuditLog entry all say so
explicitly (e.g. *"Identity verification completed, but the stipend payout
was not released because this event's claiming window has closed."*) — the
UI never shows a false "Face not verified" for a face that was, in fact,
verified.

*Canonical example — 7:59 PM → 8:01 PM:* a beneficiary's verification
begins at 7:59 PM while the event's claiming window is still open. By the
time the face match completes and the claim would be finalized, it is
8:01 PM and the window (or the 8:00 PM global claiming hours) has closed.
The identity result is still VERIFIED; the payout is not released; the
attempt is fully auditable and reviewable.

*Manual Review after close:* the reviewer may still record their identity
determination (approve/reject the request) — that resolution is preserved.
If the bound event is no longer claimable at approval time, the ClaimRecord
is simply not created, and the reviewer sees a clear message that the
identity review was recorded but payout was withheld, with the reason.

*Admin Override after close:* the override decision and its audit record
are unaffected. `override_release_payout` still refuses to create a
ClaimRecord if the bound event is no longer claimable, and tells the admin
exactly why.

*Concurrent events remain separate entitlements:* if Event A and Event B
are both open when verification starts and the operator explicitly selects
Event B, a later state change to Event B (or to Event A) never causes an
automatic switch to the other event. Event B either pays out, or the
payout is blocked — the system never substitutes Event A to "make it work."

**Why neutral/frontal frame for identity?**

The head-movement challenge requires the subject to turn their head.  Angled face
captures have lower cosine similarity than frontal captures against the enrollment
embedding (a 5–10 point drop is normal).  Using the turned proof frame for identity
matching caused false rejects for real registered users.  The neutral frame taken
before the challenge is frontal and gives the most accurate similarity score.

**Challenge and proof frames are still used for liveness** (PAD, motion analysis,
sequence counting).  Only the source of the identity embedding changes.

**Security:** The client cannot forge the neutral frame — it is processed and
verified server-side before the tx_token is issued.  The tx_token is
cryptographically random, single-use, and bound to a specific beneficiary,
claimant type, stipend event, representative, operator, verification
attempt (`session_id`), and the server-assigned challenge direction — a
token cannot be replayed against a different context along any of those
dimensions, including a later retry of the *same* attempt that rotated the
challenge direction. See [docs/SECURITY-CHECKLIST.md](SECURITY-CHECKLIST.md)
section 8 for the full list of context-binding checks.

In **strict mode** (`LIVENESS_REQUIRED=True`, the default):
- Face matching never runs when the TX liveness gate fails.
- Even a perfect face match score cannot override a failed anti-spoof or PAD check.

In **assisted rollout mode** (`LIVENESS_REQUIRED=False`):
- TX liveness failure is logged but non-blocking; face matching still runs.
- Use only during initial pilot to collect real-world liveness data without blocking users.

---

## 7. Registration Liveness

Registration liveness is **risk-based** (v2.2.1):

| Capture quality | Anti-spoof score | Action |
|---|---|---|
| Good | ≥ 0.30 | **Fast path** — no head-movement challenge required |
| Borderline or poor quality | < 0.30 | **Challenge required** — beneficiary must turn head |
| Failed | < 0.25 (hard threshold) | **Rejected** regardless of challenge |

The goal is to avoid blocking elderly senior citizens who have difficulty following on-screen instructions, while still blocking phone screens and printed photos. The hard anti-spoof threshold at 0.25 always blocks obvious spoofing regardless of the challenge.

Settings (`.env`):
- `REGISTRATION_LIVENESS_REQUIRED=True` — enforce liveness at registration (default: True)
- `REGISTRATION_CHALLENGE_REQUIRED=False` — force challenge on every registration (default: False = risk-based)
- `LIVENESS_CHALLENGE_TRIGGER_THRESHOLD=0.30` — score below which challenge is required

**Liveness challenge baseline (v2.3.1):** The baseline yaw value is now captured explicitly in a dedicated `_baselineYaw` variable and is visible in debug logs immediately after the 10-frame stable wait. The `baseYaw=n/a` issue that prevented challenge completion in certain launch sequences is resolved.

---

## 8. Duplicate Face / Twin Review

When a new face embedding is too similar to an existing enrolled face:

1. Registration is **not** hard-blocked (legitimate twins would be permanently locked out).
2. Registration is **not** auto-approved (fraud would silently succeed).
3. The record is saved as **pending** with `duplicate_review_required=True`.
4. The record appears in **Admin → Duplicate Review Queue** (`/duplicate-review/`).
5. An admin (**President**, **Admin**, or **IT**) reviews the case and chooses:
   - **Approve** — faces are legitimate twins or natural lookalikes → record is activated.
   - **Reject** — the submission appears fraudulent → record stays inactive.
6. All decisions are **audit-logged** with the reviewing admin's identity and timestamp.
7. Duplicate records **cannot claim stipends** until resolved.

---

## 9. Liveness Mismatch UI

When the client-side (browser) liveness score differs from the server measurement:

- The server result is **always** used for the final decision.
- Technical details (client score, server score, "client reported", "server measured") are stored in `attempt.notes` in the database.
- These technical details are **not displayed** to normal operators on the verification result page.
- Only simple, user-friendly messages appear in the UI:
  - *"Live face check completed."* (if liveness passed)
  - *"Live face check failed. Please retake with the real person facing the camera."* (if failed)
- Technical mismatch details are available to IT, Admin, and President in the verification log and audit log.

**No security is weakened.** The mismatch note is a UI-only cleanup — the server continues to use its own liveness result, and all data is preserved in the database for audit.

---

## 10. Privacy and Biometric Data Storage

FANS-C is designed to minimise the data footprint of biometric processing.

### What is stored

| Data | Where | How |
|---|---|---|
| Face embedding (vector) | `FaceEmbedding` table | Encrypted at rest (Fernet AES-128-CBC + HMAC-SHA256) |
| Liveness TX embedding (vector) | `LivenessTransaction` table | Encrypted at rest; single-use; expires in 2 minutes |
| Anti-spoof score (numeric) | `VerificationAttempt`, `LivenessTransaction` | Plain float; no biometric content |
| PAD score and flags (numeric) | `LivenessTransaction` | Plain floats and flag names; no images |
| Similarity score (numeric) | `VerificationAttempt` | Plain float; no biometric content |
| Decision reason (text) | `VerificationAttempt` | Human-readable decision string |
| Audit log (text) | `AuditLog` | Action codes, user IDs, timestamps — no biometric data |

### What is NOT stored

- **Raw face images from verification requests** are never written to disk or
  the database.  The image bytes sent from the browser are decoded in memory,
  processed through the face pipeline, and discarded when the request ends.
- **Challenge/proof frames and sequence frames** are processed in memory only.
  No frame images are written.
- **The neutral/frontal frame** used for identity embedding is processed
  in-memory; only the resulting embedding vector (encrypted) is stored.
- **Profile pictures / user avatars** — upload is not available.

### Encryption

Embeddings are encrypted using the `EMBEDDING_ENCRYPTION_KEY` in `.env`
(a Fernet key — AES-128-CBC + HMAC-SHA256).  The key is never stored in the
database.  If the key is lost, existing embeddings cannot be decrypted and
re-enrollment is required.

---

## 11. User Management

Admins (President, Admin, and IT) can manage staff and admin user accounts at
**Admin → User Management** (route: `accounts:user_list`).

### Roles and permissions

See [Section 2](#2-user-roles) above.

### Profile picture / avatar

Profile picture upload is **not available** (removed in v2.3.0).  The navbar user badge shows a person-icon for all users.  User accounts are managed by name, role, and employee ID only.

### Creating and editing users

- Go to **Admin → User Management → Create User**.
- All of these fields are required: First name, Last name, Email, Role, Employee ID.
- Passwords must meet the policy: ≥ 10 characters, at least one letter and one digit or symbol, and at least one **uppercase** letter.

### Password management

- **Staff self-service:** Top-right user menu → Change Password.
- **Admin reset:** Admin → User Management → select user → Reset Password.
  When an admin resets a password, the account is flagged `must_change_password=True`.
  On the user's next login they are immediately redirected to Change Password and
  **cannot use the system until the password is changed**.
- **After changing password:** The `must_change_password` flag is cleared automatically.
- All password changes and resets are recorded in the audit log.

### Password policy

| Rule | Minimum |
|---|---|
| Length | 10 characters |
| Character class | At least one letter AND one digit or symbol |
| Uppercase | At least one uppercase letter (A–Z) |

### User account notes

- Creating users: **Admin → User Management → Create User** or `scripts\admin\create-admin-user.ps1`.
- Deactivating users: **Admin → User Management** → set account status to *Suspended* or *Inactive*.  Deactivated users cannot log in.

---

## 12. Security Model

| Layer | Mechanism |
|---|---|
| Transport (installer / production) | HTTPS via Caddy (TLS termination); `Strict-Transport-Security` header. The installer ships this stack and runs it under the watchdog. |
| Transport (local dev) | `python manage.py runserver` is HTTP-only. Camera/liveness only works in a secure context — either same-laptop `http://127.0.0.1:8000/` (browser localhost exemption) or `runserver` fronted by Caddy on `https://fans-barangay.local`. Raw LAN HTTP is not supported for the camera flow. See [DEV-HTTPS.md](DEV-HTTPS.md). |
| Application | Django with CSRF, HTTPOnly/Secure cookies, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` |
| Proxy trust | Waitress listens on `127.0.0.1:8000` only; external clients cannot reach it; Caddy is the only source of `X-Forwarded-Proto` |
| Secrets | `SECRET_KEY` and `EMBEDDING_ENCRYPTION_KEY` in `.env` only — never committed, never bundled |
| Face data | Embeddings encrypted at rest (Fernet AES-128-CBC + HMAC-SHA256) |
| Liveness | Server issues a `tx_token` (LivenessTransaction) only after anti-spoof, PAD, sequence-frame, and embedding gates pass; client is advisory only |
| Identity embedding | FaceNet embedding is always computed from the neutral/frontal frame; challenge/proof frames are used for PAD and motion checks only |
| Audit | Every significant action written to `AuditLog` (permanent, not editable via UI) |
| Deployment | LAN-only; no internet exposure recommended; Windows Firewall should block port 8000 externally |

### Shared PC autofill note

FANS-C registration forms use `autocomplete="new-password"` to suppress saved-credential fill on data-entry fields.  The **login form** uses `autocomplete="username"` and `autocomplete="current-password"` so that staff's saved login credentials fill normally — this is the correct behaviour for a login form.  For shared kiosk PCs, browser password saving should be disabled in Chrome/Edge Settings → Autofill → Passwords, or via group policy.

### Forgot Password

Implemented as of the Post-UAT Hardening Pass (2026-08-30 → 2026-09-02) as a
self-service email OTP flow (`accounts/otp.py`, `PasswordResetOTP` model): a
6-digit code is emailed to the user's registered address via Gmail/Google
Workspace SMTP, with hashed codes (never stored plaintext), rate limiting,
an atomic resend cooldown, expiry, single-use enforcement, and
anti-enumeration behavior (the response does not reveal whether an email
address has an account). Professionally branded HTML + plain-text emails
were added in the v2.1.16 Post-UAT Stabilization pass (2026-09-04). The
admin-assisted path described below (**User Management → key icon**)
remains available as a fallback for sites without email configured
(`EMAIL_CONFIGURED` gating) or for a user who cannot access their
registered email. Full design rationale: [docs/PASSWORD-RECOVERY-ARCHITECTURE.md](PASSWORD-RECOVERY-ARCHITECTURE.md).

---

## 13. Installer and Runtime Data

### What the installer bundles

- Application code (Django, templates, static files)
- Python runtime and all dependencies (TensorFlow, FaceNet, etc.)
- `caddy.exe`, `mkcert.exe`, setup scripts
- WhiteNoise static files
- MediaPipe FaceMesh assets (local, no CDN dependency): `face_mesh.js`, `face_mesh.binarypb`, `face_mesh_solution_packed_assets.data`, `face_mesh_solution_simd_wasm_bin.js`, `face_mesh_solution_simd_wasm_bin.wasm`

### What the installer does NOT bundle

| File | Reason |
|---|---|
| `.env` | Contains `SECRET_KEY`, `EMBEDDING_ENCRYPTION_KEY` — must be generated fresh per machine |
| `db.sqlite3` | Contains beneficiary data and user accounts — must not be pre-populated |
| `fans-cert.pem` / `fans-cert-key.pem` | TLS certificate/key — machine-specific, must not be shared |
| `logs/` | Runtime logs — must not carry developer output into production |
| `media/` | Uploaded user photos — must not carry developer data to deployed machines |
| `rootCA.pem` | Developer's local CA — must not be shipped to end machines |

A **payload safety scan** runs automatically during the build process. The Inno Setup compiler does not run if any sensitive file is detected in the staging folder. Builds that fail this scan must never be released.

### Runtime data location (installed EXE)

The installer places `fans_c.exe` in `C:\FANSC\` (default). Runtime data is written **next to `fans_c.exe`**, not inside the read-only `_internal\` bundle:

| Data | Location |
|---|---|
| `.env` | `C:\FANSC\.env` |
| `db.sqlite3` | `C:\FANSC\db.sqlite3` |
| `media/` | `C:\FANSC\media\` |
| `logs/` | `C:\FANSC\logs\` |
| TLS certs | `C:\FANSC\fans-cert.pem`, `fans-cert-key.pem` |

This separation is critical: `_internal\` is overwritten on every reinstall; `C:\FANSC\` is preserved.

---

## 14. Build and QA

### Build steps

1. Stop `fans_c.exe` and `caddy.exe`
2. Delete `dist\` and `build\`
3. Run `.\dev\build_exe.ps1 -Clean` (PyInstaller + staging + payload safety scan)
4. Scan must print `SAFE` — if not, the build is aborted
5. Compile Inno Setup installer (`.\dev\installer\fans_c.iss`)
6. Distribute `FANS-C-Setup.exe`

### Pre-release QA

1. `python manage.py check` — 0 issues
2. `python manage.py test --verbosity=1` — all tests pass (v2.1.12 adds ~20 new tests covering the liveness `elapsed`-scope fix, President System-Role uniqueness, and Officer Assignment integration)
3. Clean-PC install test — see [Deployment Checklist](DEPLOYMENT-CHECKLIST.md)
4. Payload safety scan — `SAFE`

### Test count history

| Version | Tests |
|---|---|
| v2.0.3 | 56 |
| v2.0.4 | 121 |
| v2.0.5 | 168 |
| v2.0.7 | 213 |
| v2.0.8 | 226 |
| v2.0.9 | 237 |
| v2.1.0 | 286 |
| v2.1.3 | 308 |
| v2.1.4 | 316 |
| v2.2.1 | 323 |
| v2.2.2 | 323 |
| v2.3.0 | 328 |
| v2.3.1 | 339 |
| v2.1.8 | 365 |
| v2.1.9 | 382 |
| v2.1.10 | 423 |
| v2.1.11 | 400 (verification/accounts/beneficiaries/logs; PAD/liveness deep-hardening tests included) |
| v2.1.12 | ~420 (adds liveness `elapsed`-scope regression + President role uniqueness + Officer Assignment integration tests) |
| v2.1.13 | **446** (adds PAD landmark-motion gate + auto-verify threshold band + low-quality forced manual review + UI wording tests) |
| v2.1.14 | 460 (post-liveness integrity gate, shared-rep-review workflow) |
| Phase 1 (4.0-Final) | **518** (Phase 1A login guidance, Phase 1B DOB validation, Phase 1C override release payout; 2 pre-existing accounts test fixes) |
| Phase 3A (4.0-Final) | **524** (Django admin hardening — VerificationAttempt/FaceEmbedding/ClaimRecord/AuditLog fully read-only; automated daily backup via Task Scheduler; beneficiary list pagination — 6 new tests) |
| "v2.2.0" development milestone (2026-08-29) | ~729 (analytics/fraud-scoring/notification-category expansion; see CHANGELOG.md — this milestone label was never packaged as a separate release, see the version-numbering note at the top of this document) |
| Post-UAT Fix Report (2026-09-01) | 840 (16 issues; see [docs/POST-UAT-FIX-REPORT.md](POST-UAT-FIX-REPORT.md)) |
| Post-UAT Followup Fix Report (2026-09-01) | 880 (6 issues, incl. the session-collision `session_id` guard; see [docs/POST-UAT-FOLLOWUP-FIX-REPORT.md](POST-UAT-FOLLOWUP-FIX-REPORT.md)) |
| v2.1.16 Post-UAT Stabilization (2026-09-04) | 904 (see [UAT.md](../UAT.md) and CHANGELOG.md) |
| v2.1.16 Final Hardening Patch (2026-09-05) | **1407** (PAD production-configuration enforcement, liveness challenge/session binding, atomic perceptual-hash replay reservation, migration `0031` upgrade-safety dedupe — see CHANGELOG.md and `docs/SECURITY-CHECKLIST.md` items 8.41–8.44) |

Per README.md and CHANGELOG.md's version-numbering note: treat any specific
count above other than the most recent row as a historical snapshot, not a
current claim — re-run `python manage.py test` for the current number.

---

## 15. Current Known Notes / Future Work

### Phase 1 Changes (branch 4.0-Final, 2026-08-27)

| Item | Change |
|---|---|
| **Phase 1A — Login recovery guidance** | The login page now shows a locked-out/forgot-password help paragraph below the Sign In button, instructing users to contact the system administrator and bring a valid ID. Template-only change — no SMTP, no email reset, no new endpoints. **Superseded:** the Post-UAT Hardening Pass (2026-08-30 → 2026-09-02) added a self-service email OTP password reset (see Section 12, "Forgot Password"); the admin-assisted path added here remains as a fallback for sites without email configured. |
| **Phase 1B — Beneficiary age validation** | `beneficiaries/validators.py` — new shared `validate_senior_citizen_dob()` validator using a precise birthday comparison (replaces the inaccurate `(today - dob).days // 365` floor-division in `BeneficiaryInfoForm`). The validator is now wired into **both** `BeneficiaryInfoForm` (registration) and `BeneficiaryEditForm` (edit). Before this fix, editing a beneficiary's date of birth to an age under 60 was accepted silently. |
| **Phase 1C — Override release payout** | `verification/views.py` — new `override_release_payout` view. Closes the confirmed gap where `admin_override()` set `decision=VERIFIED` but provided no path to create a ClaimRecord. See Section 6 above for the full workflow. |

### Phase B.5 — Finalization-Within-Window Policy

| Item | Change |
|---|---|
| **Shared eligibility gate** | `StipendEvent.check_claim_eligible_now()` (models.py) + `views._lock_event_for_finalization()` — re-validates active/approved/date-range/time-window/global-hours state immediately before every ClaimRecord creation, inside the same DB transaction. See Section 6 above ("Finalization-Within-Window Policy") for the full policy. |
| **Call sites updated** | The direct automatic VERIFIED path (`verify_submit`), the representative fallback path (`verify_fallback`), `override_release_payout`, `manual_verify_review`, and `special_claim_review` all revalidate before creating a ClaimRecord. |
| **Biometric/payout separation** | A blocked payout never rewrites `VerificationAttempt.decision` — a VERIFIED face stays VERIFIED even when the event has since closed. |
| **No automatic fallback** | If the bound event becomes ineligible, the system never substitutes a different concurrently-open event — the payout is blocked, full stop. |

### Open Items / Future Work

| Item | Status |
|---|---|
| Forgot Password via Gmail/Google Workspace SMTP | **Implemented** (Post-UAT Hardening Pass, 2026-08-30 → 2026-09-02) — see Section 12 and [docs/PASSWORD-RECOVERY-ARCHITECTURE.md](PASSWORD-RECOVERY-ARCHITECTURE.md). Real end-to-end SMTP delivery is unit-tested against Django's in-memory email backend only — see [UAT.md](../UAT.md)'s "currently unverified" list. |
| Trained PAD (Presentation Attack Detection) model | **Not implemented.** Current texture heuristic cannot block a phone screen that is physically tilted during the challenge window. A trained CNN PAD model would close this gap. |
| Stipend schedule calendar view | Not implemented — distribution events use list view only |
| Multi-building PostgreSQL sync | Supported via `USE_SQLITE=False` + `SYNC_API_URL`; not tested in all configurations |
| Django 4.2 LTS end-of-support | Django 4.2 extended support ended April 2026. Migration target is Django 5.2 LTS. Separate planning required — not a blocking issue for current deployment. |

---

*Related documents:*
- [README.md](../README.md) — deployment and usage guide (end users, IT)
- [SETUP.md](../SETUP.md) — developer setup guide
- [CHANGELOG.md](../CHANGELOG.md) — version history
- [DEPLOYMENT-CHECKLIST.md](DEPLOYMENT-CHECKLIST.md) — pre-deployment checklist (IT)
- [SECURITY-CHECKLIST.md](SECURITY-CHECKLIST.md) — security review (IT, developers)
- [RESEARCH-PAPER-GUIDE.md](RESEARCH-PAPER-GUIDE.md) — comprehensive research paper reference
- [SYSTEM-STRUCTURE.md](SYSTEM-STRUCTURE.md) — detailed technical flows and folder map (developers)
- [DATABASE-GUIDE.md](DATABASE-GUIDE.md) — database configuration and models (developers)
- [dev/BUILD.md](../dev/BUILD.md) — installer build guide (developers)
