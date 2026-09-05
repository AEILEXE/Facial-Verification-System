# FANSC: A Secure FaceNet-Based Facial Verification System for Senior Citizen Stipend Distribution

> **A functional biometric identity verification system built for real-world barangay deployment.**
> Designed to streamline senior citizen stipend distribution through secure facial recognition — no paper lists, no manual identity checks.

Developed as a capstone project for deployment in controlled government environments (Quezon City).

---

## ⚡ One-Click Installer (Recommended for Deployment)

> **For barangay deployment — no technical knowledge required.**

Download **FANS-C-Setup.exe** from the
[Releases](../../releases/latest) page.

1. Copy `FANS-C-Setup.exe` to the server PC
2. Double-click → **Run as Administrator**
3. Follow the installer wizard (Next → Install)
4. The setup wizard runs automatically on first launch:
   - Generates security keys
   - Sets up the database
   - Creates an HTTPS certificate
   - Prompts to create your admin account
   - Registers autostart on every boot
5. Browser opens to `https://fans-barangay.local`

**Nothing else required.** No Python, no GitHub, no PowerShell,
no manual downloads — the one automatic download (the ~90 MB FaceNet
model, on first use) needs internet access once, into a shared
machine-local cache next to `fans_c.exe`; see
[ML Model Notes](SETUP.md#ml-model-notes) for details.

For the manual/developer setup path, see the
[Quick Deployment Guide](#quick-deployment-guide) below.

---

## Quick Deployment Guide

> Use this if you are setting up FANSC for the first time. Follow each step in order.

---

### Part 1 — Server PC Setup (do this once)

> This is the PC that will run the system. All other staff connect to it over the network.

**Before you start, make sure you have:**
- The FANSC project folder on a short path (e.g., `D:\FANS` or `C:\FANSC`)
- Python 3.11 installed — check "Add Python to PATH" during install
- `caddy.exe` placed in the `tools\` folder inside the project
- `mkcert.exe` placed in `tools\mkcert\` inside the project
- You are logged in as **Administrator**
- (Optional, recommended) Internet access on the server during the very first
  setup so vendor JS/CSS can be downloaded for offline use.

**Steps:**

1. Open the project folder.
2. (Optional, while online) Right-click `scripts\setup\fetch-vendor-assets.ps1`
   → **Run with PowerShell**. This caches Bootstrap and Bootstrap-Icons under
   `static\vendor\` so the UI works after the LAN goes offline.
3. Right-click `scripts\setup\setup-complete.ps1` → **Run with PowerShell**.
   The master script automatically handles, in order:
   - Python venv + all dependencies (including `waitress`)
   - HTTPS certificate generation via mkcert
   - `.env` creation, `SECRET_KEY` and `EMBEDDING_ENCRYPTION_KEY` generation
   - `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, and proxy headers — auto-filled from detected LAN IP
   - Copies the mkcert root CA into `CLIENT-SETUP\rootCA.pem` for client deploys
   - Database migrations and `collectstatic`
   - Auto-start Task Scheduler entry + watchdog
   - Live verification that ports 8000 and 443 are responding
4. Wait for it to finish — every step should show **PASS**.
5. Write down the **Server IP** shown at the end (example: `192.168.1.50`).

That's it. The server will now **start automatically every time the PC is turned on.**

> **Note on first boot:** Django + FaceNet warmup can take ~30 seconds the first
> time a service starts after reboot. If the page does not load immediately,
> wait 60 seconds and try again before assuming a failure.

---

### Part 2 — Staff PC Setup (do this once per device)

> Do this on every computer or tablet that staff will use to access the system.

**You need:**
- The `CLIENT-SETUP` folder from the server PC (the server-setup script
  populated this folder with `rootCA.pem` automatically — copy the whole
  folder to a USB drive)
- The server's IP address (from Part 1, Step 5)

**Steps:**

1. Copy the `CLIENT-SETUP` folder to the staff PC.
2. Double-click `CLIENT-SETUP\trust-local-cert.bat` and approve the UAC prompt.
   The script:
   - imports the server's root CA into Windows trust,
   - prompts for the server IP and adds the `fans-barangay.local` entry to
     the hosts file automatically (no manual editing required).

> **Tip:** If your router supports "Local DNS" or "Hostname Mapping", you can configure it there once instead of running the trust script per device.

---

### Part 3 — Daily Use (no setup needed)

Once setup is complete, staff do not need to run any scripts or commands.

**Every day:**

1. Turn on the **server PC** and wait about **30 seconds**
2. On any staff device, open a browser (Chrome, Edge, or Firefox)
3. Go to: **`https://fans-barangay.local`**
4. Log in and start processing beneficiaries

Nothing else is required. If the system does not load after 30 seconds, wait another 30 seconds and try again.

---

## Table of Contents

1. [Key Features](#key-features)
2. [System Overview](#system-overview)
3. [Quick Setup — IT (One Time Only)](#-quick-setup--it--one-time-only)
4. [Daily Use — President / Admin](#-daily-use--president--admin)
5. [Troubleshooting — IT](#-troubleshooting--it)
6. [Script Reference](#-script-reference)
7. [Important Notes](#-important-notes)
8. [System Architecture](#-system-architecture)
9. [Technology Stack](#-technology-stack)
10. [Audit & Verification Logs](#-audit--verification-logs)
11. [Role Summary](#-role-summary)
12. [Reports & Export](#-reports--export)
13. [Password Management](#-password-management)
14. [Local Development (Source / runserver)](#local-development-source--runserver)

---

## Local Development (Source / `runserver`)

End users who installed FANS-C via the installer `.exe` should ignore this section — the installer handles HTTPS automatically. (This section is only for developers running the app directly with `manage.py runserver`.)

For developers running the project from source: Django's `runserver` is **HTTP-only**. Browsers only allow the camera / liveness flow in a *secure context*, which means either:

- a "localhost" origin (`http://127.0.0.1:8000/` or `http://localhost:8000/`), **or**
- a real HTTPS URL with a trusted certificate.

Raw LAN HTTP (e.g. `http://192.168.x.x:8000`) is **not supported** for the camera / liveness flow — the browser will refuse `getUserMedia` outside a secure context.

| Dev mode | Command | Open in browser |
|---|---|---|
| Same-laptop dev | `python manage.py runserver 127.0.0.1:8000` | `http://127.0.0.1:8000/` |
| LAN / multi-device dev (HTTPS) | `python manage.py runserver 127.0.0.1:8000` (terminal 1) + `caddy run --config Caddyfile` (terminal 2) | `https://fans-barangay.local` |

Full step-by-step (mkcert, hosts file, `.env`, troubleshooting) is in [docs/DEV-HTTPS.md](docs/DEV-HTTPS.md). The installer HTTPS flow on the LAN is unaffected by this — it continues to use Waitress + Caddy as before.

---

## Latest Release

**Version:** v2.1.16 — 2026-09-05 (Final Hardening Patch; see version-numbering note below)

> **Note on version numbering:** development between 2026-08-27 and 2026-09-02
> was tracked internally under the milestone labels "v2.2.0" and "Post-UAT
> Hardening Pass" (both still referenced in older doc sections and in
> `CHANGELOG.md`'s `[Unreleased]` header). That work was never packaged as a
> separate v2.2.0 installer — the project's actual release-version line
> stayed on v2.1.x (see `dev/installer/fans_c.iss`, `AppVersion=2.1.16`, and
> the current branch `4.0-Final-v2.1.16-hardening`). Everything built under
> the "v2.2.0" label is real, shipped functionality — only the version
> *number* attached to it was superseded, not the content. Treat "v2.2.0" in
> older doc text as historical shorthand for that development window, not a
> separate released version.

**Installer:** not yet built for this state — see [dev/BUILD.md](dev/BUILD.md). The last installer actually built was for v2.1.18; the "v2.2.0"-labeled work, the Post-UAT hardening pass, the v2.1.16 Post-UAT Stabilization round, and the Final Hardening Patch below have not yet been packaged into an `.exe`.

| Item | Value |
|---|---|
| Version | v2.1.16 |
| Branch | `4.0-Final-v2.1.16-hardening` |
| Tests | see [CHANGELOG.md](CHANGELOG.md) for the exact count as of the most recent test run — this number changes frequently; do not hardcode it in more than one place |
| Django check | 0 issues (`manage.py check`), 0 pending migrations (`manage.py makemigrations --check --dry-run`) |
| Payload safety | **SAFE** — `.env`, `db.sqlite3`, certs, media, private keys confirmed absent from the last built installer; re-verify at each new build (see [dev/BUILD.md](dev/BUILD.md)) |

**Full version history:** this section used to duplicate per-release notes that are already maintained in **[CHANGELOG.md](CHANGELOG.md)** — that file is now the single source of truth for release history. A summary of the four most recent, not-yet-packaged work passes:

- **v2.1.16 Final Hardening Patch (2026-09-05):** closes the remaining findings from an external review of the liveness/PAD pipeline — production startup now hard-fails if presentation-attack detection is left in any "soft" (non-denying) configuration (`PRESENTATION_ATTACK_REVIEW_OR_DENY`, `STRICT_PRESENTATION_ATTACK_CHECK`, `PHONE_SCREEN_SPOOF_THRESHOLD` floor); `verify_submit` now rejects a liveness token whose challenge direction no longer matches the session's current challenge (closes a retry-rotation gap); a new `LivenessEvidenceReservation` table + in-process lock makes the perceptual-hash replay scan atomic, closing a race where two concurrent transformed-replay submissions could both pass it; migration `0031` gained an upgrade-safety data-migration step that deduplicates any pre-existing evidence-hash collisions before its uniqueness constraints are applied. Source-only — no EXE/installer built, nothing committed yet. 1407 tests pass, 0 failures. Full detail in [CHANGELOG.md](CHANGELOG.md).
- **v2.1.16 Post-UAT Stabilization (2026-09-04):** professionally branded HTML+plaintext OTP/password-changed emails (`templates/accounts/emails/`); larger, clearer Forgot Password / admin-assistance buttons on the login page; responsive-layout overflow fixes and a reworked notification-dropdown that no longer clips long text; the deactivate/suspend reason prompt replaced with a proper Bootstrap modal (same validation and audit logging); a dashboard/Verify-page inconsistency fixed so the dashboard no longer shows a payout event as "OPEN" when it's actually outside its daily claiming time window (new `SCHEDULED` status); recalibrated presentation-attack detection so isolated bright-lighting/glasses glare on a real face can no longer, by itself, deny liveness the way an actual phone-screen replay does, plus differentiated "possible attack" vs. "lighting/camera conditions" denial messages; the first-run installer wizard re-themed to the FANS-C navy/gold identity. Duplicate-face notification behavior was separately audited and found correct (no change needed). Full detail in [CHANGELOG.md](CHANGELOG.md).
- **Post-UAT hardening (2026-08-30 → 2026-09-02, commits `abd3922`, `95c55bb`, `d989f6a`):** self-service email-OTP password reset; a `DISAPPROVED` beneficiary status distinct from `INACTIVE`; organization-chart tiering (`level` field) so equal-rank officers render as siblings; a per-verification `session_id` guard that closes a cross-tab session-collision path where a stale browser tab could submit against the wrong beneficiary (see `docs/POST-UAT-FOLLOWUP-FIX-REPORT.md`, Issue 32 — the most significant fix in this pass); representative-face duplicate detection extended to `RepresentativeFaceEmbedding`; automatic 48-hour approval reminders; navbar/org-chart/analytics UI refinements. Full root-cause writeups: [docs/POST-UAT-FIX-REPORT.md](docs/POST-UAT-FIX-REPORT.md) and [docs/POST-UAT-FOLLOWUP-FIX-REPORT.md](docs/POST-UAT-FOLLOWUP-FIX-REPORT.md).
- **"v2.2.0" development milestone (2026-08-29):** notification categories expanded 4→7 with priority levels, fraud/security-review scoring moved to an explicit 0–100 risk score, two new executive analytics charts, a refresh-triggered logout bug fixed, a mislabeled pending-payout dashboard bug fixed. Full detail in [CHANGELOG.md](CHANGELOG.md).

**Historical per-version release notes (v1.0 → v2.1.18):** full detail for every release, including v2.1.14–v2.1.18 (not reproduced below), is in [CHANGELOG.md](CHANGELOG.md). The v2.1.13 highlights are kept here as they remain the clearest existing explanation of the FaceNet decision-band design:

**v2.1.13 (2026-05-27):**

- **PAD over-rejection of real users (Issue 1).** Live users could be
  falsely blocked as "near-duplicate / phone screen / replay" because
  the v2.1.11 pixel heuristics flag visually-similar sampled frames as
  suspicious even when the user is genuinely moving. **Fix:** PAD now
  suppresses its pixel-level `near_duplicate` and `static_sequence`
  signals when the client's FaceMesh confirms head movement above
  `PAD_LANDMARK_MOTION_MIN_DEG` (default 2.0°). Texture-based PAD
  (glare, flatness, sharpness) is unaffected — a phone screen, printed
  photo, or replay video still triggers those gates regardless of
  landmark motion.
- **CRITICAL: FaceNet false-accept risk (Issue 2).** A wrong-person /
  baby-photo / low-quality capture could auto-verify at FaceNet score
  ~0.82–0.83 because the old policy was a single-zone "score >= 0.75
  → VERIFIED". **Fix:** new three-zone decision band:
  - `score >= AUTO_VERIFY_THRESHOLD` (default **0.88**) → VERIFIED (auto-release)
  - `VERIFICATION_THRESHOLD <= score < AUTO_VERIFY_THRESHOLD` → MANUAL_REVIEW
  - `review_band <= score < VERIFICATION_THRESHOLD` → MANUAL_REVIEW
  - below → NOT_VERIFIED

  Also: when face quality is low, any auto-verify score is downgraded
  to MANUAL_REVIEW (`LOW_QUALITY_FORCES_MANUAL_REVIEW=True`). Result:
  a 0.82 baby-photo is now routed to MANUAL_REVIEW with release
  BLOCKED instead of auto-VERIFIED.
- **Manual Review UI now clearly says BLOCKED (Issue 3).** Result page
  shows "MANUAL REVIEW — RELEASE BLOCKED" banner and an explicit
  decision-breakdown card:
  - *Liveness:* Passed (live-face gate only — not an identity match)
  - *Identity match:* Manual review required
  - *Release status:* **BLOCKED pending administrator review**
  The similarity-score meter now shows BOTH threshold markers (lower
  threshold and the auto-verify line) and is tinted by decision, not
  by raw score, to prevent the misleading "green bar above 0.75 but
  release blocked" appearance.
- **FaceNet remains the final identity engine.** Liveness/PAD remain
  gates only. A valid `tx_token` alone cannot mark a person VERIFIED;
  liveness + same-face binding + FaceNet score >= AUTO_VERIFY_THRESHOLD
  are all required for auto-release.

**v2.1.12 (2026-05-27):**

- **Real-person liveness stuck at "Processing liveness proof…" fixed (Issue 1).**
  Root cause: a JavaScript `ReferenceError` in `static/js/verify.js` —
  `elapsed` was declared inside the challenge interval's scope but used
  outside it when building the proof POST payload, so the request never
  fired and no `tx_token` was ever issued. Fix hoists `challengeElapsedMs`
  to the outer scope, wraps the Mode-B proof POST in `try/catch/finally`,
  tops up sequence frames if the challenge completed before the backend
  3-frame minimum was reached, and shows the actual no-token reason from
  the server instead of the generic "head movement not detected" message.
  FaceNet remains the final identity verification engine — liveness/PAD
  are still only gates before FaceNet.
- **President System Role uniqueness (Issue 2).** Only one active user may
  hold System Role = President. Server-side validation in every user
  form. Error message: "Only one active President account is allowed.
  Deactivate or change the current President first." Inactive/suspended
  former Presidents remain for audit/history. Role label renamed to
  "System Role / Access Role" with help text explaining it controls
  software permissions only (not org chart).
- **President Officer-Position integrated with user create/edit (Issue 3).**
  The user form now exposes Officer Position, Start Date, and Is Current
  inline. Selecting Officer Position = President while creating/editing a
  user creates the linked `OfficerAssignment` so the Organization Chart
  and Officer Assignments pages update in the same flow. Unique positions
  block another active holder with a clear message that names the
  position.

**v2.1.11 (2026-05-27):**

- **Representative verification hardened (Issue 9):** Representative claims must compare against the representative's stored face. If `representative_id` is missing or the representative has no enrolled face, the system denies BEFORE any FaceNet comparison runs — closing a path where a senior's face could approve a representative claim. A secondary cross-probe denies when the live face matches the beneficiary on a representative claim. UI relabels the page to "Verify Representative Face" with a clear warning.
- **Duplicate Different-Person override workflow (Issue 1):** Name+DOB collisions now offer a clear UI: cancel, view existing, or submit a written "Different Person" override request. Override-flagged records remain `PENDING` and require President/Admin approval via the new Override Review queue. All decisions audit-logged.
- **Repeated cancellation safeguards (Issue 2):** Cancelling a payout when prior cancellations exist for the same beneficiary+event shows a warning. After `CANCEL_REPEAT_PRESIDENT_THRESHOLD` (default 3) only President/Admin/IT may cancel, and a longer written reason is required. Cancelled records never count toward Released totals.
- **Phone/photo spoof gate hardened (Issue 3):** PAD weights raised (screen flatness 0.30→0.55, sharpness-texture 0.25→0.50), plus a combined boost when both flatness and sharpness signals are elevated. `verify_submit` denies even when a valid TX exists if the TX PAD score crossed the suspicious threshold (defence-in-depth).
- **IT role display (Issue 4):** `get_role_display()` continues to return `IT` uppercase; the internal DB value stays `it`. New regression assertions cover both forms.
- **President approval for schedules (Issue 5):** Admin-created stipend schedules now start in `pending_approval` and are NOT usable for claiming until the President approves. President-created schedules publish immediately. `get_active_event_*` and `is_active_on_date` only consider approved/published events.
- **Admin Override Actions clarified (Issue 6):** Override panel rewritten with an explicit "Use only for correction after review" warning, repeated-cancellation notice, and per-field labels. Required override reason is labelled and required.
- **Liveness retry button (Issue 7):** When liveness fails, the verify button becomes a clickable RETRY that resets the capture flow — no full page reload needed.
- **Audit Logs UI overhauled (Issue 8):** Table now shows summary fields with badges; a "View Details" modal exposes structured sections (Basic Info, Verification Result, Reason, Related IDs, Raw Technical Details). Filters added for date range, decision, beneficiary ID, IP, and keyword. CSV export with structured fields.
- **Liveness sensitivity tuned (Issue 10):** Challenge threshold lowered 5°→4°, peak-yaw/peak-pitch tracking so a brief natural turn counts, challenge duration widened 7 s→10 s, classified failure messages ("no movement", "movement too fast / face lost", "movement too small").
- **PAD/liveness deep hardening (post-issue follow-up):** Static-sequence detection widened (< 0.15 → 0.97, < 0.5 → 0.6–0.95) so phone-screen flicker scores suspicious. New **near-duplicate frame detector** (dHash-based) catches replayed video loops and held-still photos: ≥ 50% near-identical frames → score 0.95 (weight 0.70 applied). Per-decision audit log now persists `anti_spoof_score`, `liveness_result`, `tx_token_present`, `tx_valid`, `tx_used`, `tx_expired`, `tx_claimant_match`, `challenge_motion_detected`, `static_sequence_detected`, `pa_score`, `pa_flags`, `reference_embedding_source`, `facenet_score`, `final_decision`, `final_block_reason`. `verify.js` now clears `livenessToken` the moment a submit POST is dispatched — a network error or aborted request can no longer leave a token reusable on a subsequent click. Client now also POSTs structured movement metrics (`peak_yaw_delta`, `peak_pitch_delta`, `face_lost_count`, `max_lost_burst`, `challenge_duration_ms`) so the audit log carries the same numbers the user actually produced.
- Migrations: `beneficiaries/0012_duplicate_namedob_request`, `verification/0019_stipend_approval`.
- Project housekeeping: top-level build transcripts (`build_*.txt`, `inno_log.txt`, `iscc_out.txt`, `pyinstaller_log.txt`) moved to `dev/build-logs/archive/` and added to `.gitignore`.

**v2.1.10 (2026-05-27):**
- **rootCA.pem auto-sync on upgrade:** Launcher now copies `rootCA.pem` to `CLIENT-SETUP\` on every start if the file is missing; hosts entry re-added automatically if absent.
- **Upgrade install dialog:** Installer detects existing runtime data and shows UPGRADE INSTALL vs FRESH INSTALL dialog.
- **ERR_CONNECTION_REFUSED guide added to SETUP.md.**
- 423 tests pass, 0 failures.

**v2.1.9 (2026-05-27):**
- Removed legacy `admin_it` and `head_brgy` from all creation paths.
- Migration `accounts/0009` normalizes any remaining legacy role values.
- `get_role_display()` fallback for unrecognized legacy values.

**v2.1.8 (2026-05-27):**
- **Liveness score zero on token failure:** When the server issues no `tx_token` (challenge failed, anti-spoof blocked, or insufficient frames), the UI now shows 0% / N/A for the liveness score — not 100%. The verify button is disabled in strict mode.
- **TX gate hardened (UI):** `livenessToken` is cleared immediately on any failure path; verify button blocks submission if `!livenessData.passed || !livenessToken`; retry correctly resets all state including old tokens.
- **TX gate hardened (server):** `verify_submit` validates the server-issued `LivenessTransaction` before running FaceNet. Missing, expired, used, or claimant-mismatched tokens are rejected with structured diagnostic logging.
- **Role split — President / Admin / IT / Staff:** "Head Barangay" renamed to "President"; "IT/Admin" split into separate "Admin" and "IT" roles. Four system roles total.
- **Frontal-frame identity:** FaceNet embedding now uses the neutral/frontal frame captured before the challenge, not the angled challenge frame.
- **Sequence frame minimum:** At least 3 sequence frames required for Mode B PAD analysis.
- **`UnboundLocalError` crash fix:** `face_result` always initialized to `None` in `verify_submit`.
- **User Management table:** Name | Username | System Role | Assigned Office / Unit | Officer Position | Status | Actions
- **Officer positions separated from system roles:** President/VP/Secretary/Treasurer/etc. are org-chart titles, not login roles.

---

> **Historical note:** Entries below used a deprecated version numbering scheme (v2.2.x, v2.3.x) before the project standardized on v2.1.x branch naming. They are preserved for reference only.

**v2.3.1 (2026-05-25, historical):**
- `baseYaw=n/a` fix — liveness challenge baseline variable corrected.
- `face_mesh.binarypb` added to installer — fixes MediaPipe 404 on some systems.
- Lookalike manual review message improved.
- `LookalikeSafetyGateTest` (4 tests) added.
- Audit log race fix for duplicate face events.

**v2.3.0 (2026-05-25, historical):**
- **Strict liveness gate:** Head-movement challenge is ALWAYS required before FaceNet runs. No fast path.
- **'side' challenge direction:** Accepts movement to either side (abs yaw ≥ 5°), resolving mirrored-preview confusion.
- **Rep face registration — `CHECK_LIVENESS_URL` fixed.**
- **Profile picture / avatar removed from User Management.**
- **Password uppercase requirement added.**
- **Show/hide password toggle added to login page.**

---

## Key Features

- **Facial Verification (FaceNet)** — FaceNet is the **final** identity verification engine. Liveness/PAD do not replace it; they are gates that must pass before FaceNet runs. A valid `tx_token` alone cannot verify — a beneficiary is only `VERIFIED` when liveness gates pass AND FaceNet similarity ≥ threshold.
- **Local PAD (Presentation Attack Detection)** — Heuristic detector running entirely offline (no cloud, no internet). Signals: texture-based anti-spoof, screen-flatness, sharpness/texture ratio, **inter-frame motion (static-sequence)**, and **near-duplicate frame detection** (v2.1.11) for replayed video / held photo. Composite score is denied at `PHONE_SCREEN_SPOOF_THRESHOLD` (default 0.40).
- **Strict Mandatory Liveness Challenge (Verification)** — Every final stipend verification requires a completed head-movement challenge before FaceNet runs; no fast path; challenge timeout is a denial. Failed liveness presents a clickable Retry button (v2.1.11) that resets the token, score, captured frames, and challenge state without page reload.
- **Risk-Based Liveness Challenge (Registration Only)** — The enrollment challenge is only required for suspicious or borderline captures; clean, high-quality live frames pass without unnecessary movement
- **Liveness movement tuning** — The challenge accepts normal natural head turns (≥ 4° normalized, peak-tracked) within a 10 s window. Movements that are too small, too fast, or that lose the face are returned as retry with a specific reason ("no movement", "movement too fast / face lost", "movement too small"). Static photos and replay videos cannot complete the movement check.
- **Duplicate Face / Twin Review** — Duplicate detections during registration go to an admin review queue instead of blocking or auto-approving; admins approve legitimate twins or reject fraud; all decisions are audit-logged
- **Centralized LAN Server** — One server PC serves the entire barangay office; staff access via any browser on the same network
- **Secure HTTPS Access** — All connections are encrypted; camera access (required for face scanning) only works over HTTPS
- **Fully Automated Setup** — One script (`setup-complete.ps1`) handles venv, dependencies, certificates, environment, database, auto-start, and watchdog registration
- **Auto-Start on Boot** — The system starts automatically when the server PC turns on; no manual steps required
- **Self-Healing Watchdog** — A background monitor checks the system every 45 seconds and automatically restarts services if they fail
- **Health Check Tool** — IT can run a single script at any time to see the live status of every component
- **Offline-Safe After Initial FaceNet Setup** — The system runs entirely within the barangay's local network for normal daily operation. The one exception: the first time FaceNet loads for a given Windows account (setup wizard or first server start), it downloads its ~90 MB model weights automatically — this needs internet once for that account. FANS-C never silently substitutes a fake/random model if that download hasn't happened or fails — biometric registration/verification is blocked with a clear message instead of producing unreliable results. See [SETUP.md](SETUP.md#ml-model-notes) for the current known caveat with the packaged installer's autostart account.
- **Role-Based Access** — Four system roles: President (operational head), Admin (administrative), IT (technical admin), Staff (frontline)
- **Multi-Day Payout Windows** — Distribution events can span multiple days with a start and end date, and can optionally restrict claiming to a daily time window (`payout_start_time`/`payout_end_time`). The dashboard's "Current Distribution Event" card only shows **OPEN** when the event is genuinely claimable right now — an event that's active for today but outside its daily window shows **SCHEDULED** with the opening time, matching what the Verify page will actually allow.
- **Claims Reporting & Export** — Admins can export claims and event summaries as Excel (.xlsx) or print-ready PDF
- **Approval Workflow** — Admin-created payout schedules require President approval before they're usable for claiming; claims submitted outside an active payout event go to the President / Admin for approval
- **Notifications** — An in-app notification center (bell icon in the navbar) alerts President/Admin/IT to duplicate-face reviews, security alerts, and pending approvals; each category is role-gated so only authorized staff see it
- **Analytics Dashboard** — Executive, operational, and security analytics tabs (claim progress, payout completion, verification results, review cases, fraud-risk indicators) for President/Admin/IT
- **Forgot Password OTP Recovery** — Self-service password reset via a 6-digit code emailed to the user's registered address (professionally branded HTML email with a plain-text fallback), with hashed codes, rate limiting, resend cooldown, expiry, single-use, and anti-enumeration behavior; an admin-assisted "Request Administrator Assistance" fallback remains available for sites without email configured. See [docs/PASSWORD-RECOVERY-ARCHITECTURE.md](docs/PASSWORD-RECOVERY-ARCHITECTURE.md).
- **Password Management** — Users can change their own password; admins can reset other users' passwords (logged)
- **Person-Icon User Badge** — Navbar user badge shows a person icon for all accounts; profile picture upload is not available (removed in v2.3.0)
- **FaceNet Startup Warmup** — Model is loaded in the background at boot so the first verification is fast
- **Audit Log** — Every significant action (login, registration, verification, override, password reset, user creation) is permanently recorded in the audit log. Accessible to President, Admin, and IT via Logs → Audit Log.
- **Verification Log** — Full history of all face verification attempts, decisions, similarity scores, and liveness results. Accessible to President, Admin, and IT via Logs → Verification Log.
- **Suspicious Attempt Reports** — Admins can view and export a report of flagged verification attempts for fraud review.
- **Staff Performance Reports** — Per-staff verification statistics for operational oversight.
- **Override & Fallback Reports** — Reports showing all manual overrides and fallback ID checks performed.
- **Beneficiary History Reports** — Per-beneficiary full verification and claim history.
- **Face Update / Re-enrollment** — Admins can initiate a face re-enrollment for a beneficiary (e.g. aging, illness). The new face capture goes through admin approval before replacing the stored embedding.
- **Representative Face Registration** — Authorized representatives can have their own face enrolled so they can claim on behalf of a beneficiary.
- **Sync Conflict Resolution** — Admin interface for reviewing and resolving beneficiary records that failed to sync from offline workstations.
- **Deploy-Ready Security Configuration** — Production security checks pass cleanly (4 known checks silenced with documented justification — all handled by Caddy at the proxy layer).

---

## System Overview

The FANSC system runs on **one dedicated server PC** inside the barangay office.

- All barangay staff connect to it using a regular web browser on their own devices — no software installation needed on their end.
- The system starts automatically every time the server PC is turned on. Staff do not need to run any scripts or commands.
- If a service ever fails or crashes, the built-in watchdog detects this within 45 seconds and automatically restarts it.
- All face data and stipend records are stored securely on the server, not on individual devices.
- The system does **not** require an internet connection to operate. Everything stays inside the barangay's local network.

**What staff do every day:**
1. Open a browser
2. Go to `https://fans-barangay.local`
3. Log in and start processing beneficiaries

That is the entire daily workflow. No scripts. No commands. No technical knowledge required.

---

## 🚀 Quick Setup — IT (One Time Only)

> Run this once when setting up the server for the first time. You do not need to repeat it unless reinstalling.

**Requirements before you begin:**
- You are logged in to the server PC as an Administrator
- All devices are connected to the same local network (LAN/Wi-Fi)
- `caddy.exe` is placed in the `tools\` folder inside the project
- `mkcert.exe` is placed in `tools\mkcert\`

---

### Step 1 — Run the master setup script (on the server PC)

Right-click the file below and select **Run with PowerShell**:

```
scripts\setup\setup-complete.ps1
```

Or open PowerShell as Administrator and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup\setup-complete.ps1
```

This single script handles everything in order — no manual configuration needed:

| What it does | How |
|---|---|
| Python venv + all dependencies | `pip install -r requirements.txt` (includes `waitress`) |
| Local HTTPS certificate | mkcert generates `fans-cert.pem` |
| `.env` file + security keys | `SECRET_KEY` and `EMBEDDING_ENCRYPTION_KEY` auto-generated |
| `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` | Auto-filled from detected LAN IP |
| Client certificate package | `rootCA.pem` copied into `CLIENT-SETUP\` |
| Database migrations | `manage.py migrate` |
| Static files | `manage.py collectstatic` |
| Auto-start on boot | Task Scheduler entry registered |
| Watchdog (self-healing) | Watchdog task registered |
| Live validation | Ports 8000 and 443 tested end-to-end |

Wait for it to finish and confirm all steps show **PASS**.

---

### Step 2 — Trust the certificate on each client device

On every device that staff will use to access the system, run the certificate trust script once:

```
CLIENT-SETUP\trust-local-cert.bat
```

> This allows the browser on that device to accept the local HTTPS certificate without a security warning.
> Without this step, the browser will block access or disable the camera.

The `CLIENT-SETUP\rootCA.pem` file is placed there automatically by the server setup script — no manual copying required.

---

### Step 3 — Open the system in a browser

On any device on the same network:

```
https://fans-barangay.local
```

Log in with the admin credentials created during setup.

---

**Setup notes:**
- Must be run as Administrator — the script will not work otherwise
- All devices must be on the same LAN or Wi-Fi network as the server
- Do not close the PowerShell window while setup is running
- If setup fails partway, fix the reported issue and re-run `setup-complete.ps1` — it is safe to run again

---

## 👤 Daily Use — President / Admin

Once setup is complete, the daily workflow is:

1. **Turn on the server PC**
2. **Wait about 30 seconds** for the system to start automatically
3. **Open any browser** (Chrome, Edge, Firefox)
4. **Go to:** `https://fans-barangay.local`
5. **Log in** and begin processing beneficiaries

**Nothing else is required.** No scripts. No terminal. No technical steps.

> If the browser cannot connect after 30 seconds, wait another 30 seconds and try again. If it still does not work, contact IT.

---

## 🛠 Troubleshooting — IT

### Pre-deployment validation

Before going live, run both admin check scripts to confirm the installation is complete and all checks pass:

```powershell
# Full installation check (packages, certs, .env, migrations, directories)
.\scripts\admin\verify-installation.ps1

# Non-destructive smoke tests (pip, system check, deploy check, test suite)
.\scripts\admin\run-smoke-tests.ps1
```

Both scripts print a `[PASS]` / `[FAIL]` / `[WARN]` summary and exit non-zero if anything fails. Address any failures before serving real users.

---

### Run the health check first

When something is not working, the first step is always:

```
scripts\admin\check-system-health.ps1
```

Right-click and select **Run with PowerShell**, or run it from any PowerShell window.

This script checks every component and tells you exactly what is and is not working:
- Is Waitress (the app server) running?
- Is port 8000 responding?
- Is Caddy (HTTPS) running?
- Is port 443 responding?
- Are the certificate files present?
- Are `.env` keys configured?
- Is the auto-start task registered?
- Is the watchdog active?

It prints **[OK]** or **[FAIL]** for each item and gives specific fix instructions.

---

### Check the logs

| Log file | What it contains |
|---|---|
| `logs\fans-startup.log` | What happened during the last automatic startup |
| `logs\fans-watchdog.log` | Every health check, restart attempt, and alert from the watchdog |
| `logs\django-errors.log` | Django application errors and tracebacks (Python exceptions, view crashes, 500 errors) |

If the watchdog log shows repeated `[ALERT]` entries for the same service, automatic recovery has given up and IT inspection is required.

---

### Manual startup (if auto-start fails)

To start the system manually without rebooting:

```
scripts\admin\start-now.ps1
```

Or double-click:

```
scripts\start\start-fans-quiet.bat
```

It starts both services in the background and shows port verification results.

---

### Debug mode (to see full error output)

If a service is failing to start and you need to see the exact error message:

```
scripts\start\start-fans.bat
```

This opens both server windows visibly so you can read all output. Use this when diagnosing a startup crash.

---

### Stop the system

```
scripts\admin\stop-fans.ps1
```

Run with PowerShell to cleanly stop Waitress and Caddy.

---

### Re-run setup to fix configuration problems

If the health check reports issues with `.env`, certificates, auto-start tasks, or dependencies, re-running setup is the safest fix:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup\setup-complete.ps1
```

The script is safe to re-run. It will restore everything to a correct state.

---

### Common issues and fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| Browser cannot reach `https://fans-barangay.local` | Services not running | Run health check; check logs; run `start-now.ps1` |
| "Limited HTTP mode" banner / camera blocked on HTTPS | Django not receiving X-Forwarded-Proto from Caddy, or Caddy not running | Check `/system/connection/` diagnostics; confirm Caddy is running; see HTTPS troubleshooting in SETUP.md |
| Camera not working in browser | Certificate not trusted on this device | Run `CLIENT-SETUP\trust-local-cert.bat` on that device |
| System did not start after reboot | Auto-start timing or task not registered | Run health check; check startup log; re-run `setup-complete.ps1` if task is missing |
| Watchdog shows repeated `[ALERT]` | Service crashing on startup | Run `start-fans.bat` to see full error output |
| `NET::ERR_CERT_AUTHORITY_INVALID` / privacy warning | Certificate not trusted on this client | Run `CLIENT-SETUP\trust-local-cert.bat` on that device |
| `HTTP ERROR 502` | Caddy running but Waitress/Django is not | Run health check, then `start-fans.bat` to diagnose |
| `ERR_CONNECTION_REFUSED` | Both services are down | Run `start-now.ps1` and verify health |
| `Bad Request (400)` or `CSRF verification failed` | `.env` not configured | Re-run `setup-complete.ps1` to regenerate `.env` with correct values |
| `Internal Server Error (500)` / staticfiles manifest error | Static files not collected | Re-run `setup-complete.ps1` |
| Any `.env` key error | Missing or malformed key | Re-run `setup-complete.ps1` |
| `NoReverseMatch` error on dashboard | Leftover reference to a removed feature | Re-run `python manage.py check` and report the view name to IT developer |
| `System check identified N issues` on `check --deploy` | Security warnings from Django | Run `python manage.py check --deploy` — 4 silenced checks are expected and documented; any others need review |

---

### How to verify startup is really working

After a reboot:

1. Do **not** manually run any start script
2. Wait about **30–60 seconds**
3. Open:
   ```
   https://fans-barangay.local
   ```
4. Run:
   ```powershell
   scripts\admin\check-system-health.ps1
   ```

You want to see:
- `Waitress process` → Running
- `Port 8000 (app)` → LISTENING
- `Caddy process` → Running
- `Port 443 (HTTPS)` → LISTENING
- `HTTPS end-to-end` → responds correctly
- Final line:
  ```
  HEALTH: ALL CHECKS PASSED
  ```

---

## 📂 Script Reference

| Script | Purpose | Who Uses It |
|---|---|---|
| `scripts\setup\setup-complete.ps1` | **Master one-time setup** — runs the entire setup flow from start to finish | IT (once) |
| `scripts\setup\setup-secure-server.ps1` | Lower-level setup: venv, dependencies, certificates, Django configuration | IT (advanced) |
| `scripts\setup\setup-autostart.ps1` | Registers the auto-start task in Windows Task Scheduler only | IT (advanced) |
| `scripts\setup\Create-Desktop-Shortcut.ps1` | Creates a desktop shortcut for easy manual startup | IT (optional) |
| `scripts\start\start-fans-hidden.ps1` | Silent startup launcher — called by Task Scheduler at boot only, never manually | Task Scheduler only |
| `scripts\start\start-fans-quiet.bat` | Manual daily launcher — starts both services minimized in the background | IT (manual start) |
| `scripts\start\start-fans.bat` | Debug launcher — starts both services in visible windows for troubleshooting | IT (debug only) |
| `scripts\admin\fans-control-center.ps1` | All-in-one admin menu: start, stop, restart, health check, logs, repair tools | IT (recommended) |
| `scripts\admin\check-system-health.ps1` | Live health diagnostic — read-only, checks every component and reports status | IT (any time) |
| `scripts\admin\watchdog.ps1` | Self-healing monitor — runs automatically via Task Scheduler 150s after boot, never manually | Task Scheduler only |
| `scripts\admin\start-now.ps1` | Start services immediately without rebooting or re-running any setup | IT |
| `scripts\admin\stop-fans.ps1` | Stops Waitress and Caddy cleanly | IT |
| `scripts\admin\repair-autostart.ps1` | Re-register auto-start Task Scheduler task only (targeted fix) | IT |
| `scripts\admin\repair-watchdog.ps1` | Re-register watchdog Task Scheduler task only (targeted fix) | IT |
| `scripts\admin\repair-hosts.ps1` | Add fans-barangay.local to server hosts file (targeted fix) | IT |
| `scripts\admin\create-admin-user.ps1` | Create or add user accounts with selectable role | IT |
| `scripts\admin\verify-installation.ps1` | 18-check installation health script: Python version, all required packages, pending migrations, TLS certs, `.env` keys, writable directories | IT (pre-deployment) |
| `scripts\admin\run-smoke-tests.ps1` | 6 non-destructive pre-deployment checks: pip, Django system check, deploy check, migration drift, collectstatic dry-run, test suite | IT (pre-deployment) |
| `CLIENT-SETUP\trust-local-cert.bat` | Installs the local HTTPS certificate on a client device | IT (per device, once) |

---

## ⚠️ Important Notes

- **Run setup as Administrator.** `setup-complete.ps1` requires Administrator rights to register Task Scheduler tasks and bind to port 443. Right-click → Run with PowerShell, or open PowerShell as Admin first.

- **Setup is safe to re-run.** If something failed or a component needs to be restored, re-run `setup-complete.ps1`. It will regenerate what is missing. Avoid running it repeatedly when everything is working — it will regenerate security keys.

- **All devices must be on the same network.** Staff devices and the server PC must be connected to the same LAN or Wi-Fi. The system does not work over the internet or from outside the barangay network.

- **Install the certificate on every client device.** The `CLIENT-SETUP\trust-local-cert.bat` script must be run once on each device that staff will use. Without it, the browser will show a security warning and the camera will not work.

- **Do not move script files.** Scripts calculate the project root from their own location. Moving them to different folders will break path resolution.

- **The watchdog runs automatically.** Do not run `watchdog.ps1` manually. It is managed by Task Scheduler and starts automatically 150 seconds after each boot (after the main startup task and FaceNet model load finish). To stop or reset it, use Windows Task Scheduler and look for the task named **FANS-C Watchdog**.

- **Backup your `.env` file.** This file contains the `SECRET_KEY` and `EMBEDDING_ENCRYPTION_KEY`. If it is lost, encrypted face data cannot be read. Store a secure copy of this file off the server.

- **DEBUG must be False in production.** The `.env` file must have `DEBUG=False` for production deployment. The system will show full error tracebacks to users if `DEBUG=True` is left on in a live environment. The setup script sets this correctly — only change it temporarily for local debugging.

- **Face embedding key is irreplaceable.** The `EMBEDDING_ENCRYPTION_KEY` in `.env` encrypts every stored face embedding. If this key is lost or changed, all enrolled faces become permanently unreadable and every beneficiary must re-register from scratch. Back up this key securely and separately from the server.

- **FaceNet warmup makes first startup slower.** A 25–30 second startup after boot is normal. Give the system time to finish loading before deciding it failed.

---

## 🏗 System Architecture

```
Browser (staff device)
        |
        | HTTPS — port 443
        v
   [ Caddy ]  — HTTPS reverse proxy
        |
        | HTTP — port 8000 (local only)
        v
   [ Waitress ] — Python WSGI server
        |
        v
   [ Django ] — FANSC application
        |
        v
   [ SQLite / Database ]  +  [ FaceNet model ]
```

**In plain terms:**

- **Django** is the application itself — it handles logins, records, face verification logic, and the web pages staff see.
- **Waitress** serves the Django application on port 8000. It is a production-grade Python server.
- **Caddy** sits in front of Waitress and handles HTTPS (the secure, encrypted connection). It terminates SSL and forwards requests to Waitress. HTTPS is required for the browser camera to work.
- **FaceNet** is the facial recognition model. It converts a face image into a numerical fingerprint and compares it against enrolled data to verify identity.
- **Task Scheduler** starts Waitress and Caddy automatically at boot, and runs the watchdog in the background.
- **The watchdog** monitors Waitress and Caddy every 45 seconds. If either stops responding, it restarts the failed service automatically — up to 3 attempts per 10-minute window.

Everything runs on one server PC, inside the barangay's local network. No cloud. No internet dependency.

**Django Application Modules:**

| App | Purpose |
|---|---|
| `fans/` | Project configuration — settings, URL routing, WSGI, error pages |
| `accounts/` | User authentication, login throttling, role-based access, password management |
| `beneficiaries/` | Beneficiary registration, records, representative management, offline sync |
| `verification/` | FaceNet face verification engine, liveness detection, stipend events, claims, approvals, reports |
| `logs/` | Audit log and verification log — permanent tamper-evident record of all system actions |

---

## 🧰 Technology Stack

| Layer | Technology | Version |
|---|---|---|
| Web framework | Django | 4.2.x |
| Application server | Waitress | 3.x |
| HTTPS reverse proxy | Caddy | 2.x |
| Face recognition | FaceNet via keras-facenet | 0.3.2 |
| Face detection | MTCNN | 1.0.0 |
| Deep learning runtime | TensorFlow CPU | 2.13.1 |
| Anti-spoofing | Texture analysis + head-movement challenge (liveness.py) | custom |
| Image processing | OpenCV | 4.10.x |
| Encryption | Fernet (AES-128-CBC + HMAC-SHA256) via cryptography | 43.x |
| Database (default) | SQLite | built-in |
| Database (centralized) | PostgreSQL | 14+ |
| Static file serving | WhiteNoise | 6.x |
| Excel export | openpyxl | 3.1+ |
| Python version | CPython | 3.11 ONLY |
| OS target | Windows 10/11 | 64-bit |
| Certificate authority | mkcert | local CA |

> Python 3.12 and above are NOT supported. TensorFlow CPU 2.13.1 requires Python 3.11 specifically. The setup script enforces this automatically.

### Liveness vs. Face Verification — Two Separate Checks

These are two distinct and independent security steps. They must not be confused:

| Check | What it does |
|---|---|
| **Liveness** | Confirms the camera sees a **real live person** — not a phone screen, printed photo, or pre-recorded video |
| **Face Verification** | Confirms that live person **matches the registered beneficiary** (or their authorized representative) |

Liveness does **not** identify the person — it only validates that someone real is physically present.  
Face verification does **not** replace liveness — even a perfect face match cannot override a liveness failure in strict mode.

The server always makes the final liveness decision. Client-side signals (browser MediaPipe score) are collected for usability only. If the client and server scores diverge, the server result is used. Technical score details are stored in the audit log for IT review but are **not shown in the normal operator UI**.

---

### Liveness and Anti-Spoofing Behavior (Verification)

Verification uses two independent checks before a face match is attempted:

1. **Texture anti-spoof** — scores the captured frame for signs of a live face (sharpness, depth cues, texture). Higher score = more likely real. Threshold: `ANTI_SPOOF_THRESHOLD` (default 0.25).
2. **Head-movement challenge** — **always required** (v2.3.0+). The beneficiary must visibly turn their head to either side ('side' direction). There is no fast path. Challenge timeout is treated as failed, not passed. FaceNet does not run until challenge is confirmed.

In **strict mode** (`LIVENESS_REQUIRED=True`, the default):

- A failed liveness check **denies verification before any face matching runs**. A perfect face match score cannot override a liveness failure.
- `challenge_completed=false` is a liveness denial regardless of anti-spoof score.
- If head tracking (MediaPipe) is unavailable in the browser, the challenge is treated as **failed**, not auto-passed.
- The denial reason in the verification log identifies the specific failure: anti-spoof below threshold, challenge not completed, or both.

In **non-strict / assisted rollout mode** (`LIVENESS_REQUIRED=False`): liveness failure is logged but non-blocking. Face matching still runs. Use during initial rollout only.

**Liveness mismatch UI:** When the client-side pre-check score differs from the server measurement, technical details (raw scores, labels like "client spoof" or "server measured") are stored in the audit log but **not displayed** to normal operators. Operators see only simple outcome messages — no alarming score data is shown for routine checks.

**LivenessTransaction (TX token):** After the head-movement challenge completes, the server issues a one-time `tx_token` that cryptographically binds the liveness proof to the identity embedding computed from the neutral frame. `verify_submit` only proceeds if it receives and consumes a valid token — a client cannot skip liveness and directly submit a face match request.

---

### Registration Liveness

Liveness is also enforced during beneficiary face registration (`REGISTRATION_LIVENESS_REQUIRED=True` by default):

- The server checks anti-spoof score and optionally requires a head-movement challenge before accepting any face embedding.
- **Fast path (no challenge):** Anti-spoof score ≥ 0.30 and face quality good → accepted directly. Senior citizens with a clear live camera view are not forced through unnecessary movement.
- **Challenge path:** Anti-spoof score < 0.30 (borderline/suspicious) or poor face quality → head-movement challenge required before enrollment.
- **Rejected:** Anti-spoof score below the hard threshold (< 0.25) → always rejected regardless of anything else. Phone screens, printed photos, and replay attacks cannot be enrolled as face embeddings.
- Server-side validation is independent of the browser. A tampered client cannot bypass the server liveness gate.

---

### Duplicate Face / Twin Review

When a new beneficiary's face is similar to an already-enrolled face:

- The record is saved as **pending** with `duplicate_review_required=True`.
- The registration does **not** hard-block and does **not** auto-approve.
- Duplicate records appear in the **Admin Duplicate Review Queue** (`/duplicate-review/`).
- **President, Admin, or IT** can:
  - **Approve** — the faces are legitimate twins or natural lookalikes. The record is activated.
  - **Reject** — the submission is fraudulent. The record stays inactive.
- All approve/reject decisions are recorded in the audit log with the reviewing admin's identity.
- Duplicate beneficiaries cannot claim stipends until their record is resolved.

---

### User Management — Profile Picture / Avatar

Profile picture upload is **not available** (removed in v2.3.0). The navbar user badge shows a person icon for all accounts. User accounts are identified by name, role, and employee ID only.

---

### Browser Autofill / Saved Password Note

FANS-C registration and login forms apply HTML-level autofill suppression (`autocomplete="off"` / `autocomplete="new-password"`) to reduce browser credential suggestions on shared barangay PCs.

> **Important for shared PCs:** HTML autofill attributes reduce suggestions but browser password managers may override them. For shared kiosk PCs, browser password saving should be fully disabled in Chrome/Edge settings (Settings → Autofill → Passwords → toggle off) or via Windows/organization group policy.

---

## 📋 Audit & Verification Logs

The system maintains two permanent logs, accessible from **Navbar → Audit & Logs**:

**Audit Log** records every significant action in the system — logins (successful and failed), beneficiary registration, face verification decisions, manual overrides, password resets, user creation, and report exports. The audit log cannot be edited or deleted from within the system. Accessible to **President**, **Admin**, and **IT**.

**Verification Log** records every face verification attempt in detail: the beneficiary, the staff member who ran it, the similarity score, the liveness result, the final decision (pass/fail/override), and the timestamp. Accessible to **President**, **Admin**, and **IT**.

Both logs support date filtering and are viewable directly in the browser. The audit log is the authoritative record for any dispute or review.

---

## 🔐 Role Summary

### System Roles (software permissions)

These are the four login roles in the system. System Role controls what a user can see and do in the software.

| Role | DB Value | What they can do |
|---|---|---|
| **President** | `president` | All operational tasks: verify, register, approve claims/manual-reviews, manage users, run reports, reset passwords for all roles |
| **Admin** | `admin` | Administrative tasks: register, approve claims/manual-reviews, manage users, run reports, reset Staff passwords |
| **IT** | `it` | All President/Admin permissions + system diagnostics, connection info, and technical setup pages |
| **Staff** | `staff` | Register beneficiaries, run verification, submit manual-review or special-claim requests; no user management or reports |

> Staff log in directly to the dashboard after password authentication. No additional biometric step-up is required for system access.

> Staff cannot approve pending claims or access reports. Pending claims (submitted without an active event) appear in the Admin Review Queue for President/Admin to approve.

### Officer Positions (org-chart titles)

Officer Position is a separate field that records a user's role in the barangay org chart. It does **not** affect software permissions — permissions are determined by System Role only.

Available officer positions:
- President, Vice President Internal, Vice President External
- Secretary, Treasurer, Auditor
- PRO 1, PRO 2
- Chairman of the Board, Vice Chairman of the Board
- Board Secretary, Board Undersecretary
- Board of Director *(non-unique — multiple holders allowed)*
- Adviser / Punong Barangay

All positions except **Board of Director** are unique: only one active user may hold each position at a time.

### Assigned Office / Unit

The **Assigned Office / Unit** field records where a user is physically assigned (e.g., "Main Office", "Finance Unit"). This is an organizational label and does not affect system permissions.

### Creating users

Use **Admin → User Management → Add User** in the web UI, or:

```powershell
scripts\admin\create-admin-user.ps1
```

The script supports selectable role creation:

1. `President`
2. `Admin`
3. `IT`
4. `Staff`

---

## 📊 Reports & Export

President, Admin, and IT have access to report views. They are accessible from:
- **Navbar → Admin → Claims Report / Event Summary**
- **Dashboard → Quick Actions → Claims Report / Event Summary**

| Report | URL | Export options |
|---|---|---|
| Claims Report | `/verification/reports/claims/` | Print / Save as PDF, Excel (.xlsx) |
| Event Summary | `/verification/reports/event-summary/` | Print / Save as PDF, Excel (.xlsx) |
| Staff Performance | `/verification/reports/staff-performance/` | Print / Save as PDF, Excel (.xlsx) |
| Override & Fallback | `/verification/reports/override-fallback/` | Print / Save as PDF, Excel (.xlsx) |
| Suspicious Attempts | `/verification/reports/suspicious-attempts/` | Print / Save as PDF, Excel (.xlsx) |
| Beneficiary History | `/verification/reports/beneficiary/<id>/` | Print / Save as PDF |

All report access is restricted to **President**, **Admin**, and **IT** roles. All exports are recorded in the audit log.

Reports can be filtered by date range, event, status, and claimant type. All exports are logged in the audit trail.

**To export as PDF:** Click **Print / Save as PDF** — this opens a clean print-ready page in a new browser tab. Use the browser's built-in print dialog (Ctrl+P) and select "Save as PDF" as the printer destination.

**To export as Excel:** Click **Export Excel** — the browser downloads a `.xlsx` file immediately.

---

## 🔑 Password Management

- **Change own password** — Available to all users via the user menu (top right) → *Change Password*
- **Reset another user's password** — President can reset any user's password. Admin and IT can reset Staff and lower-level accounts. Go to **Admin → User Management** and click the key icon next to the user
- All password resets are recorded in the audit log with the resetting admin's identity

---

---

## 🗑 Uninstall / Removal

### Normal uninstall (recommended)

Use the Windows installer uninstaller — it stops all processes, removes shortcuts, removes Task Scheduler tasks, and removes program files:

- **Settings → Apps → Installed Apps → FANS-C Verification System → Uninstall**
- or **Control Panel → Programs and Features → FANS-C Verification System → Uninstall**
- or double-click the Inno Setup uninstaller in `C:\FANSC`

By default the uninstaller **preserves user data** (`.env`, `db.sqlite3`, `media/`, `logs/`, certificate files) so data survives a reinstall.

### Cleanup helper script (for IT staff)

A helper script provides interactive cleanup with PASS/FAIL reporting:

```powershell
# Run as Administrator
powershell.exe -ExecutionPolicy Bypass -File C:\FANSC\cleanup-fansc.ps1
```

This script stops processes, removes Task Scheduler tasks, removes shortcuts, offers hosts file cleanup, and asks whether to remove user data and the install folder.

### Full cleanup (removes all data)

To wipe everything including the database, captured photos, and the install folder:

```powershell
# Run as Administrator
powershell.exe -ExecutionPolicy Bypass -File "C:\FANSC\scripts\admin\uninstall-clean.ps1"
```

Type `YES` at the confirmation prompt. This removes all app data permanently.

### What each option removes

| Item | Normal uninstall | Cleanup script | Full cleanup |
|---|---|---|---|
| Program files (`C:\FANSC\_internal\`) | Yes | Optional | Yes |
| Task Scheduler tasks | Yes | Yes | Yes |
| Desktop / Start Menu shortcuts | Yes | Yes | Yes |
| `fans-barangay.local` hosts entry | Yes (installer) | Optional | Yes |
| `.env` (encryption key, settings) | **No** | Optional | Yes |
| `db.sqlite3` (beneficiary data) | **No** | Optional | Yes |
| `media/` (captured photos) | **No** | Optional | Yes |
| `logs/` | **No** | Optional | Yes |
| TLS certificate files | **No** | Optional | Yes |
| mkcert root CA (trust store) | **No** | Optional | Optional |

> **Important:** The mkcert root CA is shared. Removing it may break certificates trusted by mkcert on this machine. Only remove it if you are certain no other mkcert-signed certificates are in use.

### Kill stuck processes before uninstalling

If the old setup is stuck, kill it first (run as Administrator in PowerShell):

```powershell
Stop-Process -Name "fans_c"         -Force -ErrorAction SilentlyContinue
Stop-Process -Name "caddy"          -Force -ErrorAction SilentlyContinue
Stop-Process -Name "waitress-serve" -Force -ErrorAction SilentlyContinue
```

---

---

## ✅ Clean-PC QA Checklist

Run this checklist on a freshly installed machine before releasing a build to a barangay:

| # | Check | Expected result |
|---|---|---|
| 1 | Install FANS-C-Setup.exe on clean PC | Setup completes; Create Admin page appears on first open |
| 2 | Developer credentials from build machine | **Must not work** — dev database not included |
| 3 | Real person registration (live face) | Registration succeeds; embedding saved |
| 4 | Phone/screen registration attempt | Registration **rejected** by anti-spoof or challenge |
| 5 | Real person verification | Verified — face match passes; liveness passes; server log shows `neutral_tx_embedding=True` |
| 6 | Phone/screen verification attempt | **Denied** — liveness gate blocks before face match runs |
| 7 | TX token issued | Browser receives `tx_token` after challenge; `verify_submit` consumes it |
| 8 | Sequence frame gate | Attempt with 0 or 1 frames returns denial (`insufficient_sequence_frames`); no token issued |
| 9 | Technical liveness mismatch banner | **Not shown** to operators — no raw score text visible |
| 10 | User Management — no profile picture upload | Profile picture field is absent from Edit User form; person icon shown in navbar |
| 11 | Password — uppercase required | Password without uppercase letter is rejected on create/reset |
| 12 | Duplicate face registration | Record goes to duplicate review queue; cannot auto-approve or claim |
| 13 | Admin duplicate review — approve | Record activated; audit logged |
| 14 | Admin duplicate review — reject | Record stays inactive; audit logged |
| 15 | Browser autofill on registration | Suggestions suppressed or minimal |
| 16 | Payload safety | Installer does **not** contain `.env`, `db.sqlite3`, certs, logs, or `media/` uploads |
| 17 | HTTPS and camera | Camera works on `https://fans-barangay.local`; no "Limited HTTP mode" banner |
| 18 | Auto-start after reboot | System comes up automatically within 60 seconds |
| 19 | Watchdog recovery | Services restart automatically within 45 seconds of a simulated crash |

---

## 🔮 Future / Optional Features

These are **not yet implemented**. Do not assume they exist.

- **Stronger Presentation Attack Detection (PAD) model** — A trained CNN PAD model would better distinguish live skin from phone screens under arbitrary tilt angles. The current texture heuristic has a known limitation in that case.
- **Stipend schedule calendar view** — Visual calendar showing all active and upcoming payout events.

**Already implemented (previously listed here as future work, now shipped):** self-service password recovery via email OTP (an admin-approval workflow was evaluated and deliberately not built instead — see [docs/PASSWORD-RECOVERY-ARCHITECTURE.md](docs/PASSWORD-RECOVERY-ARCHITECTURE.md) for the design decision, and `accounts/otp.py` for the implementation: hashed codes, rate limiting, resend cooldown, expiry, single-use, anti-enumeration, audit logging).

---

*For additional reference files, see [SETUP.md](SETUP.md) (developer/IT setup), [CLIENT_ACCESS.md](CLIENT_ACCESS.md) (staff browser access guide), [docs/SYSTEM-OVERVIEW.md](docs/SYSTEM-OVERVIEW.md) (comprehensive system reference for admins and capstone evaluators), and [docs/RESEARCH-PAPER-GUIDE.md](docs/RESEARCH-PAPER-GUIDE.md) (research paper and thesis reference).*
