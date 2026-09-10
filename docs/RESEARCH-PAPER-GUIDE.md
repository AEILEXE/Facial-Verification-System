# FANS-C Research Paper Guide

**FANS-C: A Secure FaceNet-Based Facial Verification System for Senior Citizen Stipend Distribution**

**Branch:** `4.0-Final-v2.1.16-hardening` | **Current Version:** v2.1.16 | **Date:** 2026-09-05
**Audience:** Capstone/thesis evaluators, researchers, research paper authors

This guide is the authoritative reference for documenting the FANS-C system in a research paper or capstone thesis. It describes what the system actually does — not what was planned or aspirational. This guide was last synchronized with the codebase on the date above (updated from a v2.1.13 snapshot); for the exact current feature list, security-control list, and test count, cross-check against [docs/SYSTEM-OVERVIEW.md](SYSTEM-OVERVIEW.md), [docs/SECURITY-CHECKLIST.md](SECURITY-CHECKLIST.md), and [CHANGELOG.md](../CHANGELOG.md), which are updated more frequently.

---

## Table of Contents

1. [Project Title](#1-project-title)
2. [System Overview](#2-system-overview)
3. [Problem Statement](#3-problem-statement)
4. [Objectives](#4-objectives)
5. [Scope and Limitations](#5-scope-and-limitations)
6. [Target Users](#6-target-users)
7. [System Modules](#7-system-modules)
8. [User Roles and Permissions](#8-user-roles-and-permissions)
9. [Officer Positions vs System Roles](#9-officer-positions-vs-system-roles)
10. [Beneficiary Registration Process](#10-beneficiary-registration-process)
11. [Face Enrollment Process](#11-face-enrollment-process)
12. [Verification Process](#12-verification-process)
13. [Stipend Claiming Process](#13-stipend-claiming-process)
14. [Reports and Logs](#14-reports-and-logs)
15. [Security and Privacy Measures](#15-security-and-privacy-measures)
16. [Technology Stack](#16-technology-stack)
17. [Deployment Process](#17-deployment-process)
18. [Client Setup Process](#18-client-setup-process)
19. [Database and Data Flow Summary](#19-database-and-data-flow-summary)
20. [Testing and Validation Summary](#20-testing-and-validation-summary)
21. [Known Limitations](#21-known-limitations)
22. [Future Improvements](#22-future-improvements)

---

## 1. Project Title

**FANS-C: A Secure FaceNet-Based Facial Verification System for Senior Citizen Stipend Distribution**

Subtitle options for the paper:
- *Design and Implementation of a Biometric Identity Verification System for Senior Citizen Stipend Distribution in a Barangay Setting*
- *FANS-C: A LAN-Deployed FaceNet and Liveness-Gated Stipend Distribution System for Senior Citizens*

---

## What Changed in v2.1.13 (Quick Reference)

| Area | Change |
|---|---|
| FaceNet decision policy | **Three-zone band.** `score >= AUTO_VERIFY_THRESHOLD` (0.88) → VERIFIED. `[VERIFICATION_THRESHOLD, AUTO_VERIFY_THRESHOLD)` → MANUAL_REVIEW (release blocked). `[review_band, VERIFICATION_THRESHOLD)` → MANUAL_REVIEW (low band), where `review_band = VERIFICATION_THRESHOLD * 0.85`. Below `review_band` → NOT_VERIFIED (not DENIED — DENIED is a separate decision reserved for liveness/anti-spoof/processing failures, not a low similarity score). Closes the 0.82–0.83 wrong-person / baby-photo / low-quality false-accept window observed in v2.1.12 manual QA. |
| Low-quality forced review | `LOW_QUALITY_FORCES_MANUAL_REVIEW=True` (default). Where it applies, a low-quality face capture scoring ≥ 0.88 is downgraded to MANUAL_REVIEW so a human confirms — low-quality embeddings can produce coincidental high similarities. **Scope note (added 2026-09-08):** `apply_quality_override()` only ever receives a quality dict on `verify_submit`'s fallback identity path (no stored liveness-transaction embedding). On the transaction-bound path — effectively all production traffic under the default `LIVENESS_PROOF_REQUIRED=True` — this override does not currently run; see `docs/SECURITY-CHECKLIST.md`'s 2026-09-08 entry. |
| PAD over-rejection fixed | `PresentationAttackDetector.analyze_sequence()` now takes `landmark_motion_ok`. When the client's FaceMesh confirms head motion above `PAD_LANDMARK_MOTION_MIN_DEG` (2.0°), pixel-level near-duplicate and static-sequence signals are suppressed. Texture-based PAD (glare, flatness, sharpness) unaffected — phone screens and printed photos still blocked. |
| Result page UI | MANUAL_REVIEW shows "MANUAL REVIEW — RELEASE BLOCKED" banner + explicit decision breakdown (Liveness / Identity match / Release status). Similarity meter shows both threshold markers (lower + auto-verify) and is tinted by decision, not by raw score. |

## What Changed in v2.1.12 (Quick Reference)

| Area | Change |
|---|---|
| Liveness UX | Real-person liveness no longer gets stuck at "Processing liveness proof…". Root cause was a JS `ReferenceError` (`elapsed` declared inside the challenge `setInterval`, used outside it when building the proof payload). Fix hoists `challengeElapsedMs` to the outer scope, wraps the proof POST in `try/catch/finally`, tops up sequence frames before sending if the challenge finishes fast, and shows the real backend reason on failure. FaceNet remains unchanged as the final identity engine. |
| User management | Only one active System Role = President is allowed. Form-level validation across all five user forms; error: "Only one active President account is allowed. Deactivate or change the current President first." |
| Officer Position integration | User create/edit form now exposes Officer Position + Start Date + Is Current. Saving creates/updates the linked `OfficerAssignment` so Organization Chart and Officer Assignments page update in the same flow. Unique positions block another active holder. |
| Label clarity | Role field is labelled "System Role / Access Role" with help text "This controls software permissions, not the organization chart position." User list column header renamed "Role" → "System Role". |

## What Changed in v2.1.11 (Quick Reference)

| Area | Change |
|---|---|
| Representative verification | Hard-blocks any path where a senior's face could approve a representative claim; secondary cross-probe to detect when live face matches beneficiary instead of representative. |
| Duplicate name+DOB workflow | Added Different-Person override request, PENDING gate, President/Admin review queue. |
| Repeated cancellations | Warns when prior cancellations exist; after threshold, only President/Admin may cancel and a longer reason is required. |
| Phone/photo spoof gate | PAD weights raised; combined flatness+sharpness boost; verify_submit denies even with valid TX when PAD score crossed threshold. |
| IT role display | UI label is always `IT`; internal DB value remains `it`. |
| Schedule approval | Admin-created schedules start in `pending_approval`; only President may approve/reject. |
| Admin Override Actions | UI rewritten with explicit purpose, warnings, labelled fields. |
| Liveness retry | Failed-liveness button is now a clickable Retry, not a disabled-state dead end. |
| Audit logs | View Details modal with structured sections; date/decision/beneficiary/IP/keyword filters; CSV export. |
| Liveness sensitivity | Threshold 5°→4°; peak-yaw/peak-pitch tracking; 10 s duration; classified failure messages. |
| PAD static/duplicate detection | Static-sequence band widened (< 0.5 px ⇒ 0.6–0.95); new dHash-based near-duplicate-frame detector (≥ 50 % near-dup ⇒ 0.95, weight 0.70). |
| Structured PAD/liveness audit | Each verification persists `anti_spoof_score`, `liveness_result`, `tx_*` flags, `pa_score`, `pa_flags`, `facenet_score`, `final_decision`, `final_block_reason`, `reference_embedding_source`. |
| Token reuse defence | `verify.js` clears `livenessToken` immediately on submit dispatch; server still enforces single-use. |
| Project housekeeping | Top-level build transcripts archived under `dev/build-logs/archive/`; `.gitignore` extended; `SYSTEM-STRUCTURE.md` documents must-not-move paths and installer payload exclusions. |

Honest limitations (unchanged): webcam-only PAD remains heuristic and cannot guarantee phone-screen detection under all conditions. The strict liveness gate + sequence/duplicate detection + same-face check + FaceNet identity match together provide layered defence, but a sophisticated replay with a high-quality screen and a person physically following the challenge could still pass. A trained offline CNN PAD model was evaluated and **deferred** (model size/licensing/packaging risk). See Section 21.

---

## 2. System Overview

FANS-C is a biometric identity verification system deployed at the barangay level to manage the distribution of senior citizen stipends. It replaces paper-based attendance lists and manual identity checks with a secure, multi-layer facial recognition pipeline.

**Core concept:**

Before a senior citizen can receive their stipend, the system must confirm two things:
1. The person in front of the camera is a real, live human being — not a photo, phone screen, or replay video (liveness / anti-spoofing).
2. That live person is the same individual whose face was enrolled in the system (FaceNet identity verification).

Both checks must pass before a claim is recorded.

**Deployment model:**

- One dedicated server PC inside the barangay office
- All barangay staff access it via a web browser — no software installation required on staff devices
- Runs on a local area network (LAN); no public internet required for daily operation
- HTTPS is enforced for all connections (required for browser camera access)
- Starts automatically at boot; a self-healing watchdog recovers from crashes without IT intervention

**How staff use the system:**

1. Turn on the server PC and wait ~30 seconds
2. Open a browser on any staff device connected to the barangay network
3. Go to `https://fans-barangay.local`
4. Log in and begin processing beneficiaries

No scripts, no command line, no technical knowledge required for daily operation.

---

## 3. Problem Statement

Senior citizen stipend distribution in Philippine barangays has historically relied on manual processes:
- Paper sign-in lists that can be falsified or manipulated
- Manual identity checks that depend on staff recognition
- No permanent electronic record of who claimed and when
- No mechanism to detect when a person claims multiple times or a non-beneficiary claims on behalf of someone else

These gaps create opportunities for fraud and errors in distribution. They also create difficulty for administrators who need to audit claims or resolve disputes.

FANS-C addresses these problems by:
- Replacing paper identity checks with biometric verification using FaceNet facial recognition
- Requiring liveness proof before any identity match runs (blocking phone screens, photos, and replay attacks)
- Creating a permanent, structured, append-only audit log of every claim, verification attempt, and administrative action (UI-restricted, not cryptographically tamper-evident)
- Providing a structured approval workflow for edge cases (manual reviews, representative claims, duplicate detections)

---

## 4. Objectives

**General Objective:**
To design, implement, and deploy a secure, biometric-based stipend distribution system for senior citizens at the barangay level that is reliable, fraud-resistant, and operable without technical expertise.

**Specific Objectives:**

1. Implement FaceNet-based facial identity verification as the primary identity check for stipend claims.
2. Implement a multi-layer liveness and anti-spoofing gate that blocks presentation attacks before the identity check runs.
3. Enforce a server-issued LivenessTransaction (tx_token) that cryptographically binds a liveness proof to a specific identity comparison request, preventing face-switching attacks between the liveness and identity phases.
4. Provide a risk-based registration liveness check that protects enrollment without placing unnecessary burden on elderly beneficiaries.
5. Implement a duplicate face review workflow that handles potential twin or lookalike scenarios without blocking legitimate beneficiaries or auto-approving fraud.
6. Encrypt all stored face embeddings at rest using a Fernet key, ensuring biometric data cannot be read without the encryption key.
7. Deploy the system on a Windows LAN server with one-click installer, automated HTTPS certificate management, and a self-healing watchdog.
8. Provide role-based access control (President, Admin, IT, Staff) so that system functions are restricted to authorized personnel.
9. Maintain a permanent, append-only audit log of all significant system actions.
10. Achieve a production-ready system validated by a clean-PC installer test, Django system check, and automated test suite.

---

## 5. Scope and Limitations

### Scope

The system covers the following within a single barangay deployment:

- **Beneficiary management:** Registration, record management, representative enrollment, face update requests
- **Liveness / anti-spoofing:** Texture-based anti-spoof scoring + mandatory head-movement challenge for verification; risk-based challenge for registration
- **Face identity verification:** FaceNet embedding comparison using cosine similarity
- **Stipend event management:** Multi-day payout events with scheduled distribution windows
- **Claims recording:** Verified claim records tied to payout events, including override and representative claims
- **Duplicate face review:** Admin queue for resolving potential twin/lookalike detections at registration
- **Reports and export:** Claims reports, event summary, staff performance, override/fallback reports, suspicious attempt reports, beneficiary history
- **Audit and verification logs:** Permanent, structured, append-only records (UI-restricted, not cryptographically tamper-evident)
- **Role-based access control:** Four roles (President, Admin, IT, Staff)
- **Officer positions:** 14 org-chart titles tracked separately from system roles
- **User management:** Create, edit, suspend, deactivate staff accounts; password management
- **HTTPS local network deployment:** Caddy + mkcert for LAN HTTPS without a public domain
- **Windows installer:** One-click installer for barangay deployment; clean install and upgrade install paths

### Out of Scope

- Internet-facing or multi-barangay deployment (LAN-only design)
- Mobile app (browser-only)
- Facial recognition for staff login (staff log in via username/password only)
- Forgot-password email flow (not implemented; admin resets passwords manually)
- Calendar view for stipend schedules (list view only)
- Trained neural-network PAD (Presentation Attack Detection) model (current anti-spoof is heuristic-based)
- Biometric step-up authentication for staff login (removed in v2.0)

---

## 6. Target Users

| User Type | Description |
|---|---|
| **Senior Citizens (Beneficiaries)** | Primary recipients of the stipend. They interact with the system through the camera during verification — they do not log in. |
| **Authorized Representatives** | Persons authorized to claim on behalf of a beneficiary (e.g., a family member when the beneficiary is ill). They have their own enrolled face and claim type. |
| **Staff (Frontline Operators)** | Barangay staff who register beneficiaries, run face verification sessions, and submit special-claim or manual-review requests. They log in via username/password. |
| **Admin** | Administrative staff who manage user accounts, approve pending records, manage officer assignments, and run administrative reports. |
| **IT** | Technical staff who manage system configuration, certificates, backups, and diagnostics. Has all Admin permissions plus technical diagnostic pages. |
| **President** | Highest operational authority. Has all system permissions. Approves claims, overrides decisions, manages users, runs reports. |

---

## 7. System Modules

The system is built on five Django applications:

| Module (App) | Purpose |
|---|---|
| `fans/` | Project configuration — settings, URL routing, WSGI, error pages, middleware |
| `accounts/` | User authentication, login throttling, role-based access control, password management, officer positions and assignments |
| `beneficiaries/` | Beneficiary registration and records, representative management, face enrollment, duplicate face review, offline sync support |
| `verification/` | FaceNet face verification engine, liveness detection (anti-spoof + challenge), LivenessTransaction (tx_token), stipend events, claims, approval workflow, reports, system diagnostics |
| `logs/` | AuditLog model (permanent, structured, append-only audit trail — UI-restricted, not cryptographically tamper-evident), verification log, template tags for log display |

**Supporting infrastructure (outside Django apps):**

| Component | Purpose |
|---|---|
| Waitress | Python WSGI server; runs Django on port 8000 (loopback only) |
| Caddy | HTTPS reverse proxy on port 443; TLS termination; forwards to Waitress |
| mkcert | Local certificate authority for LAN HTTPS |
| Task Scheduler | Auto-starts Waitress + Caddy at boot; runs watchdog |
| watchdog.ps1 | Self-healing monitor; restarts failed services automatically |
| WhiteNoise | Django middleware for serving static files without a separate file server |

---

## 8. User Roles and Permissions

System roles control what a logged-in user can see and do. Role is set by an administrator and enforced server-side — direct URL access to admin-only pages is blocked regardless of what the user types in the browser.

| Role | DB Value | What they can do |
|---|---|---|
| **President** | `president` | All operational tasks: verify, register, approve claims and manual reviews, manage users, run reports, reset passwords for all roles, view audit and verification logs |
| **Admin** | `admin` | Administrative tasks: register, approve claims and manual reviews, manage users and officer assignments, run reports, reset Staff passwords, view audit and verification logs |
| **IT** | `it` | All President and Admin permissions + system diagnostics, connection info, HTTPS proxy diagnostics, technical setup pages |
| **Staff** | `staff` | Register beneficiaries, run face verification, submit manual-review or special-claim requests; no user management, no reports, no claim approvals |

**Notes:**
- Staff log in directly to the dashboard after password authentication. No biometric step-up is required for system access.
- Staff cannot approve pending claims. Claims submitted outside an active payout event are routed to the Admin Review Queue for President or Admin to approve.
- All four roles can change their own password.
- Only President can reset any user's password; Admin and IT can reset Staff passwords.

**Legacy roles (historical only, no longer assignable):**

| Legacy Role | DB Value | Status |
|---|---|---|
| Head Barangay | `head_brgy` | Migrated → President (migration `accounts/0009`) |
| IT/Admin | `admin_it` | Migrated → Admin (migration `accounts/0006`) |

These legacy values are no longer in `ROLE_CHOICES` and cannot be assigned to new users. Existing accounts were automatically migrated.

---

## 9. Officer Positions vs System Roles

**Important distinction — do not confuse these:**

| Concept | What it is | What it controls |
|---|---|---|
| **System Role** | Software permission level | What the user can see and do in the application |
| **Officer Position** | Organizational chart title | Nothing — it is a label only |
| **Assigned Office / Unit** | Where the user works | Nothing — it is a label only |

An officer position does **not** grant or restrict system access. A user with the officer position "President" can only do what their System Role allows. The organization's President would typically hold the `president` system role, but these are configured separately.

**Available Officer Positions:**

| Position | Unique? |
|---|---|
| President | Yes — only one active holder |
| Vice President Internal | Yes |
| Vice President External | Yes |
| Secretary | Yes |
| Treasurer | Yes |
| Auditor | Yes |
| PRO 1 | No — multiple holders allowed |
| PRO 2 | No |
| Chairman of the Board | Yes |
| Vice Chairman of the Board | Yes |
| Board Secretary | Yes |
| Board Undersecretary | Yes |
| Board of Director | No — multiple holders allowed |
| Adviser / Punong Barangay | No |

**Unique** positions: only one active user may hold the position at a time. Assigning a user to a unique position automatically ends any previous active assignment for that position.

**Non-unique** positions (PRO 1, PRO 2, Board of Director, Adviser / Punong Barangay): multiple active holders are allowed simultaneously.

**Assigned Office / Unit** is a free-text field that records where a user is physically assigned (e.g., "Main Office", "Finance Unit"). It does not affect permissions.

---

## 10. Beneficiary Registration Process

Beneficiary registration is the process by which a senior citizen's identity is entered into the system and their face is enrolled.

**Step-by-step process:**

1. A Staff (or Admin/President/IT) user logs in and navigates to **Register → New Beneficiary**.
2. The operator enters the beneficiary's personal information: full name, Senior Citizen ID number, date of birth, address (barangay, house number, street), contact number, and valid ID.
3. If the beneficiary will be represented by an authorized representative, the operator enters the representative's information (name, relationship, contact) and optionally enrolls the representative's face in a separate step.
4. The operator proceeds to the face capture page. The camera opens in the browser.
5. The system runs a **registration liveness check** (described in Section 11).
6. If liveness passes, the server runs **FaceNet** to extract a 512-dimensional face embedding from the captured image.
7. The embedding is encrypted using Fernet (AES-128-CBC + HMAC-SHA256) and stored in the `FaceEmbedding` table. The raw image is **not** stored permanently.
8. The server runs **duplicate face detection**: the new embedding is compared against all existing enrolled embeddings.
   - If no similar embedding exists: the record is saved as **pending** (subject to the auto-approval workflow).
   - If a similar embedding is found: the record is saved as **pending** with `duplicate_review_required=True`, and it appears in the Admin Duplicate Review Queue. It cannot proceed until an administrator explicitly approves or rejects it.
9. An administrator (President, Admin, or IT) reviews and approves or rejects pending records. Auto-approval may also be configured for certain scenarios.
10. Once **active**, the beneficiary's record is available for verification and stipend claiming.

**Approval workflow:**
- Pending records (without duplicate flag) can be auto-approved if system configuration allows.
- Duplicate-flagged records always require manual review by an administrator.
- All approve/reject actions are recorded in the audit log.

**Duplicate Different-Person override (v2.1.11):**
When a registration matches an existing record on **name + date of birth**, the operator sees a modal with the existing record details and three options:
1. **Cancel registration** — use the existing record.
2. **View existing beneficiary** — open the existing profile.
3. **Submit Different-Person Override Request** — proceed with a written reason (≥ 10 chars) and optional distinguishing info.

If the operator submits an override, the new record is created in `PENDING` status with an attached `DuplicateNameDobRequest`. President/Admin reviews the request in the Override Review queue and approves (activates the beneficiary) or rejects (deactivates the beneficiary). Auto-approval is suppressed when an override is in flight.

---

## 11. Face Enrollment Process

Face enrollment is the technical process of capturing and storing a biometric representation of a person's face. It occurs during beneficiary registration and representative registration.

**Registration liveness check (risk-based):**

Before enrolling any face, the server performs a liveness check to prevent enrollment of phone screens, printed photos, or replay videos.

| Capture Quality | Anti-Spoof Score | Outcome |
|---|---|---|
| Good — clear live face | ≥ 0.30 | Fast path — no head-movement challenge required |
| Borderline or poor quality | < 0.30 | Head-movement challenge required before enrollment can proceed |
| Obvious spoof (phone/photo) | < 0.25 (hard threshold) | Always rejected — challenge cannot override |

The risk-based approach is intentional: the head-movement challenge requires the beneficiary to follow on-screen instructions, which may be difficult for elderly users. High-quality live captures skip the challenge. The hard threshold (0.25) always rejects spoofing attacks regardless of challenge outcome.

**Face enrollment flow:**

1. Camera captures the face image in the browser.
2. Client-side anti-spoof pre-check runs (MediaPipe-based; advisory only).
3. If anti-spoof score is borderline, a head-movement ('side') challenge is presented. The beneficiary turns their head visibly to one side.
4. The image (and optional challenge proof) are sent to the server.
5. The server independently verifies the anti-spoof score.
6. If liveness passes: **FaceNet (via keras-facenet)** detects the face using **MTCNN**, crops it, and generates a 512-dimensional embedding vector.
7. The embedding is encrypted with the `EMBEDDING_ENCRYPTION_KEY` (Fernet) and stored in the `FaceEmbedding` database table.
8. The raw face image is discarded from memory. No face images are written to disk or stored in the database.

**What is stored:** Only the encrypted embedding vector (128 float32 values serialized and encrypted). No raw images.

**For representatives:** The same process applies. The representative's embedding is stored separately and linked to the beneficiary's record.

**Representative verification rule (v2.1.11):**
- When the operator selects representative claim type, the verification compares the captured face against the **representative's** stored embedding only.
- The system never falls back to the beneficiary's embedding for a representative claim.
- If the representative has no enrolled face, verification is denied with an explicit "register their face before verifying" message.
- A secondary cross-probe denies the claim when the live face matches the **beneficiary** with score ≥ threshold on a representative claim — preventing the senior from approving a representative claim with their own face.

---

## 12. Verification Process

The verification process confirms a person's identity before releasing a stipend. It is a two-phase pipeline:
- **Phase 1 (Liveness proof):** Confirm the camera sees a real live person and capture a frontal embedding. Issue a server-bound tx_token.
- **Phase 2 (Identity comparison):** Consume the tx_token and compare the captured embedding against the enrolled embedding using FaceNet.

**Why two phases?**

A single-phase design where liveness and identity checks run in one request creates a vulnerability: an attacker could potentially manipulate the request between liveness confirmation and identity matching (a "face-switching attack"). The two-phase LivenessTransaction design binds the liveness proof to a specific embedding and beneficiary at the server. The tx_token is one-use, time-limited (2 minutes), and tied to a specific beneficiary and claimant type.

### 12.1 Detailed Verification Flow

**Setup:**
1. Staff logs in and searches for the beneficiary by name or Senior Citizen ID.
2. Staff selects the beneficiary (and optionally a representative claim type).
3. The verification page opens. The browser requests camera access (HTTPS required).

**Phase 1 — Liveness proof (`verify_check_liveness`):**

4. The camera stabilizes. The system captures a **neutral/frontal frame** before any challenge begins. This is the frame used for identity embedding — the beneficiary must face the camera directly.
5. The **head-movement challenge** begins. The beneficiary must visibly turn their head to either side (absolute yaw ≥ 5°). The challenge is **always required** — there is no fast path for high anti-spoof scores.
6. The browser sends to the server:
   - The neutral/frontal frame (pre-challenge)
   - The proof frame (turned face)
   - A sequence of motion frames (minimum 3)
   - `challenge_completed=True`

7. The server runs the liveness gate (authoritative — client scores are advisory only):
   - Anti-spoof check on the neutral frame. Score < 0.25 → **rejected, no tx_token issued**.
   - Sequence frame count. Fewer than 3 frames → **rejected** (`insufficient_sequence_frames`).
   - PAD (Presentation Attack Detection) analysis on the sequence frames. Score ≥ PAD threshold → **rejected**.
   - FaceNet embedding from the neutral/frontal frame. Embedding failure → **rejected** (`embedding_failed`).

8. If all gates pass: the server creates a `LivenessTransaction` record and returns a `tx_token` UUID to the browser.
   - The tx_token is one-use, expires in 2 minutes, and is bound to the specific beneficiary and claimant type.
   - The embedding computed from the neutral frame is stored encrypted in the LivenessTransaction.

**Phase 2 — Identity comparison (`verify_submit`):**

9. The browser sends the `tx_token` to the server.

10. The server validates the tx_token:
    - Missing → **DENIED**
    - Expired (> 2 minutes) → **DENIED**
    - Already used → **DENIED** (single-use enforcement)
    - Wrong beneficiary → **DENIED**
    - Wrong claimant type → **DENIED**

11. Liveness confirmed via TX stored scores:
    - TX anti-spoof score < 0.25 → **DENIED**
    - TX PAD score ≥ threshold → **DENIED**

12. **FaceNet identity comparison:**
    - The server retrieves the beneficiary's enrolled embedding from the `FaceEmbedding` table and decrypts it.
    - The embedding stored in the LivenessTransaction (from the neutral frame) is compared to the enrolled embedding using **cosine similarity**.
    - Score ≥ threshold → **VERIFIED**
    - Score < threshold → **NOT VERIFIED**
    - Score in manual review band (≥ 85% of threshold) → **MANUAL REVIEW**

13. **Lookalike safety gate** (if VERIFIED):
    - The claimed similarity score is compared against all other enrolled beneficiaries within a 0.05 similarity band.
    - If another beneficiary matches within the band → escalated to **MANUAL REVIEW**.

14. The tx_token is consumed (marked as used).

15. A `VerificationAttempt` record is written to the database with the full decision details.

**Result outcomes:**

| Result | Meaning |
|---|---|
| VERIFIED | Face match passed; if a payout event is active, a claim record is created |
| NOT VERIFIED | Face match failed — score below threshold |
| MANUAL REVIEW | Score in borderline band, or lookalike detected; staff must verify identity using a valid ID before releasing stipend |
| DENIED | Liveness or TX gate failed — face matching did not run |

**Why the neutral frame for identity?**

The head-movement challenge requires turning the face. Angled face captures have lower cosine similarity than frontal captures against the enrollment embedding (typically a 5–10 point drop). Using the turned frame for identity matching caused false rejections for real registered users. The neutral frame (captured before the challenge, frontal) gives the most accurate similarity score. The challenge/proof frames are still used for PAD and motion analysis.

**In strict mode (`LIVENESS_REQUIRED=True`, the default):**
- Liveness failure denies verification before any face matching runs.
- A perfect face match score cannot override a failed anti-spoof or PAD check.

**In assisted rollout mode (`LIVENESS_REQUIRED=False`):**
- Liveness failure is logged but non-blocking; face matching still runs.
- For initial pilot only — switch to strict mode for production.

---

## 13. Stipend Claiming Process

Stipend claiming is the business outcome of a successful verification. Claims are tied to **Stipend Events** (distribution windows).

**Stipend Events:**
- An administrator (President, Admin, or IT) creates a **Stipend Event** with a name, event type, and a date range (start date and end date, supporting multi-day payout windows).
- Only one active event can be running at a time.
- Events can have custom types (e.g., Regular Stipend, Special Event).
- Office hours validation: claims are only accepted within a configured time window (default 07:00–20:00, Asia/Manila timezone).
- **Approval workflow (v2.1.11):** Schedules created by Admin start in `pending_approval` and are NOT usable for claiming until the **President** approves. President-created schedules publish immediately. `get_active_event_*` only considers events whose `approval_status='approved'`. Rejection requires a written reason and deactivates the schedule.

**Claim creation:**
- When a beneficiary is VERIFIED and an active payout event exists, a `ClaimRecord` is automatically created.
- The claim records the beneficiary, event, timestamp, operator, claim type (direct or representative), and verification method.
- A beneficiary can only claim once per event (duplicate claims within the same event are blocked).

**Claims outside an active event:**
- If no active event exists, a verified beneficiary cannot automatically claim.
- The operator can submit a **special claim request** for administrator review.
- President or Admin reviews and approves or denies special claim requests.

**Override claims:**
- In cases where face verification fails but the operator is confident of the person's identity (e.g., the enrolled face is outdated due to aging or illness), an **override** can be submitted.
- Override claims go to the admin review queue and are tracked separately in reports.

---

## 14. Reports and Logs

### Reports

Available to **President, Admin, and IT** roles:

| Report | Description | Export Formats |
|---|---|---|
| Claims Report | All claims within a date/event range; filterable by type, status, beneficiary | Print/PDF, Excel (.xlsx) |
| Event Summary | Summary of each stipend event — total claims, verified count, override count | Print/PDF, Excel (.xlsx) |
| Staff Distribution Activity | Per-staff verification activity counts (attempts, verified, denied, overrides) | Print/PDF, Excel (.xlsx) |
| Override & Fallback Report | All manual overrides and fallback ID-check verifications | Print/PDF, Excel (.xlsx) |
| Suspicious Attempts Report | Flagged verification attempts for fraud review | Print/PDF, Excel (.xlsx) |
| Beneficiary History | Full verification and claim history for a specific beneficiary | Print/PDF |

All report exports are recorded in the audit log.

### Logs

| Log | Description | Accessible By |
|---|---|---|
| **Audit Log** | Permanent, append-only log of every significant system action: login, logout, registration, verification decision, override, password reset, user creation, role change, duplicate review decision | President, Admin, IT |
| **Verification Log** | Full detail of every face verification attempt: beneficiary, operator, similarity score, liveness result, decision, timestamp | President, Admin, IT |

The audit log is the authoritative record for any dispute. It cannot be edited or deleted through the system interface. All entries include the actor's identity, target, timestamp, and IP address.

### Log files (server-side)

| File | Contents |
|---|---|
| `logs/fans-startup.log` | Service startup events at each boot |
| `logs/fans-watchdog.log` | Watchdog health checks, restart attempts, and alerts |
| `logs/django-errors.log` | Django application errors, tracebacks, 500 errors |

---

## 15. Security and Privacy Measures

### Authentication and Access Control

- **Username/password login** for all staff roles
- **Login throttling:** configurable maximum failed attempts and lockout window (default: 8 attempts in 10 minutes, 15-minute lockout)
- **Password policy:** minimum 10 characters, at least one letter AND one digit or symbol, at least one uppercase letter
- **must_change_password flag:** when an admin resets a password, the account is flagged; the user must change the password on next login before accessing any other page
- **Session security:** 8-hour session expiry, session ends on browser close (`SESSION_EXPIRE_AT_BROWSER_CLOSE=True`), HTTPOnly and Secure cookie flags in production

### Transport Security

- **HTTPS enforced** by Caddy (TLS termination on port 443)
- **HSTS header** set by Caddy (`Strict-Transport-Security: max-age=31536000; includeSubDomains`)
- **Local certificate authority** via mkcert; staff devices trust the CA via `trust-local-cert.bat`
- **Waitress** listens on `127.0.0.1:8000` (loopback only) — external clients cannot reach it directly; only Caddy can inject the `X-Forwarded-Proto: https` header

### Biometric Data Security

- **No raw face images stored**: browser-submitted face images are decoded in memory, processed, and discarded. No images are written to disk or database.
- **Face embeddings encrypted at rest**: Fernet AES-128-CBC + HMAC-SHA256 using `EMBEDDING_ENCRYPTION_KEY` from `.env`
- **LivenessTransaction embeddings encrypted**: temporary embeddings in the tx_token record are also encrypted; the record expires in 2 minutes and is single-use
- **Anti-spoof and PAD scores**: stored as numeric floats; no biometric content
- **Similarity scores**: stored as numeric floats; no biometric content

### Liveness and Presentation Attack Defense

- **Texture anti-spoof check**: heuristic-based analysis of the captured frame (sharpness, texture, depth cues). Default threshold: 0.25.
- **Sequence frame gate**: at least 3 motion frames required; static images that cannot produce motion are rejected.
- **Static-sequence detection (v2.1.11)**: inter-frame mean motion < 0.5 px scores 0.6–0.95; < 0.15 px scores 0.97. Phone-screen flicker and held photos now consistently cross the suspicious threshold.
- **Near-duplicate frame detection (v2.1.11)**: 64-bit perceptual dHash per frame; pairwise Hamming distance ≤ 4 ⇒ near-duplicate. ≥ 50 % of frame pairs near-duplicate ⇒ score 0.95 (weight 0.70). Catches replayed video loops and held-still photos.
- **Head-movement challenge**: mandatory for all verification attempts. Beneficiary must visibly turn head to either side (absolute yaw ≥ 4°, **peak-tracked** so a brief natural turn counts). Challenge window is 10 s; timeout = denial.
- **Server authority**: the server makes the final liveness decision. Browser MediaPipe scores are advisory only. If client and server scores diverge, the server result is used.
- **tx_token binding**: after liveness passes, the server issues a cryptographically random, one-use, time-limited tx_token that binds the liveness proof to the specific identity embedding and beneficiary. Identity matching only runs after consuming a valid token.
- **Token reuse defence (v2.1.11)**: `verify.js` clears the in-page `livenessToken` immediately on submit dispatch — combined with server-side single-use, a stale/old token cannot be reused after any failure path. Failed liveness presents a clickable Retry button that resets token, score, captured frames, and challenge state without page reload.
- **PAD denial at submit (v2.1.11)**: even when a valid TX exists, `verify_submit` denies if the TX's PAD score crossed `PHONE_SCREEN_SPOOF_THRESHOLD` (defence-in-depth).
- **Decision rule**: anti-spoof alone, liveness alone, and a valid `tx_token` alone are each **not sufficient** to verify. A `VERIFIED` decision requires all three: anti-spoof passed AND liveness (challenge + PAD + sequence) passed AND FaceNet similarity ≥ threshold.

**Structured PAD/liveness logging fields (audit log).** Each verification decision now persists: `anti_spoof_score`, `liveness_result`, `tx_token_present`, `tx_valid`, `tx_used`, `tx_expired`, `tx_claimant_match`, `challenge_motion_detected`, `static_sequence_detected`, `pa_score`, `pa_flags` (including `near_duplicate`), `facenet_score`, `final_decision`, `final_block_reason`, `reference_embedding_source`. Client-side movement metrics (`peak_yaw_delta`, `peak_pitch_delta`, `face_lost_count`, `max_lost_burst`, `challenge_duration_ms`) are also captured at challenge time.

### Known Security Limitation

The current anti-spoof is a **heuristic** (texture analysis), not a trained PAD model. A sharp, high-resolution phone screen displaying a face photo may score above the anti-spoof threshold, and a phone held at the right angle by a person who physically follows the challenge could potentially still pass. The v2.1.11 static-sequence + near-duplicate detectors close additional gaps (video loops, held photos) without an external model, but a trained CNN PAD model (e.g., MiniFASNet/Silent-Face-Anti-Spoofing) would close the residual gap.

**Offline PAD model decision (v2.1.11).** We evaluated integrating a trained local PAD model and **deferred** the integration:
- Model files (~50–100 MB typical) materially grow the installer payload (current ~210 MB).
- Licensing for the senior-citizen LAN deployment context needs review.
- Adding a TensorFlow/PyTorch model risks breaking the existing PyInstaller bundle that already ships a TensorFlow runtime for FaceNet.

The current local heuristic PAD + sequence/duplicate detectors were strengthened instead. Recommended future improvement once a model is locally licensed and packaging-tested.

### Privacy Measures

- Raw verification images are never stored — only the encrypted embedding vector and numeric scores
- Liveness mismatch technical details (raw scores, client vs. server comparison) are stored in the audit log but **not displayed** to operators — only simple outcome messages appear
- All sensitive files (`.env`, `db.sqlite3`, certificates) are excluded from version control and the installer payload via `.gitignore`, staging exclusions, and a mandatory payload safety scan
- Face data encrypted with a key that is never stored in the database

---

## 16. Technology Stack

All technologies listed here are confirmed present in the codebase (`requirements.txt`, Django settings, and deployment configuration).

| Layer | Technology | Version / Notes |
|---|---|---|
| **Web framework** | Django | 4.2.x |
| **Application server** | Waitress | 3.x (production WSGI; listens on 127.0.0.1:8000) |
| **HTTPS reverse proxy** | Caddy | 2.x (TLS termination on port 443) |
| **Face recognition** | FaceNet via keras-facenet | 0.3.2, default checkpoint `20180402-114759` (512-dim embedding, cosine similarity — runtime-verified; do not assume 128-dim) |
| **Face detection** | MTCNN | 1.0.0 (detects and crops face region for FaceNet) |
| **Deep learning runtime** | TensorFlow CPU | 2.13.1 (required by keras-facenet; Python 3.11 only) |
| **Anti-spoof / liveness** | Heuristic texture analysis (`liveness.py`) | Custom implementation; no external model |
| **Head-movement challenge** | MediaPipe FaceMesh | Bundled locally (no CDN dependency); runs in browser |
| **Image processing** | OpenCV | 4.10.x |
| **Embedding encryption** | Fernet (AES-128-CBC + HMAC-SHA256) via `cryptography` | 43.x |
| **Database (default)** | SQLite | Built-in; single-file; adequate for one-server deployments |
| **Database (optional)** | PostgreSQL | 14+ (via psycopg2-binary); for multi-server deployments |
| **Static file serving** | WhiteNoise | 6.x (serves static files within Waitress/Django process) |
| **Excel export** | openpyxl | 3.1+ |
| **Certificate authority** | mkcert | Local CA for LAN HTTPS (no public domain required) |
| **Installer** | PyInstaller + Inno Setup 6 | PyInstaller bundles Python + all dependencies; Inno Setup compiles the Windows .exe installer |
| **Python version** | CPython 3.11 | **Only supported version** — TensorFlow 2.13.1 requires 3.11; 3.12+ not supported |
| **Operating system** | Windows 10/11 | 64-bit; tested on Windows 11 Home |
| **Auto-start / watchdog** | Windows Task Scheduler + PowerShell | Task Scheduler runs startup and watchdog scripts at boot |
| **Frontend frameworks** | Bootstrap 5.3.2, Bootstrap Icons 1.11.3 | Vendored locally (no CDN; works offline) |
| **Testing framework** | Django TestCase | 423 tests, 0 failures (current codebase) |

### How FaceNet works in FANS-C

1. The browser sends a face image to the Django server via a POST request.
2. **MTCNN** (Multi-task Cascaded Convolutional Network) detects the face in the image and crops it to a standard size.
3. **FaceNet** (keras-facenet, based on the InceptionResNetV1 architecture) processes the cropped face and outputs a 512-dimensional floating-point vector — the "face embedding."
4. For enrollment: the embedding is encrypted with Fernet and stored in the database.
5. For verification: the enrollment embedding is retrieved and decrypted; the cosine similarity between the enrollment embedding and the verification embedding is computed. A score ≥ threshold (default 0.75 in strict mode) is a match.

FaceNet does **not** run the liveness or anti-spoof checks. Those are separate heuristic and challenge-based functions. FaceNet is solely responsible for identity comparison.

### How liveness/anti-spoofing works

The liveness system is a separate, independent layer that runs **before** FaceNet:

1. **Texture anti-spoof (server-side):** The captured frame is analyzed for signs of a live face — sharpness (Laplacian variance), texture patterns (LBP proxy), edge analysis (Sobel). The resulting score (0.0–1.0, higher = more likely live) is compared against `ANTI_SPOOF_THRESHOLD` (default 0.25).
2. **Sequence frame analysis / PAD (server-side):** A sequence of motion frames is analyzed for signs of a presentation attack — specular glare, static repetition, screen-like artifacts. A PAD score above the threshold blocks token issuance.
3. **Head-movement challenge (client-side, validated server-side):** MediaPipe FaceMesh tracks the nose tip landmark across frames. The yaw (horizontal rotation) is computed relative to a baseline taken before the challenge. A visible turn to either side (|yaw delta| ≥ 5°) completes the challenge. The server validates that challenge_completed=True before issuing the tx_token.

---

## 17. Deployment Process

### Installer path (recommended for barangay deployment)

1. Copy `FANS-C-Setup-v2.1.x.exe` to the server PC.
2. Double-click → Run as Administrator.
3. Follow the installer wizard (Next → Install).
4. On first launch, the 8-step setup wizard runs automatically:
   - Generates `SECRET_KEY` and `EMBEDDING_ENCRYPTION_KEY` into `.env`
   - Detects LAN IP and sets `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`
   - Generates TLS certificate via mkcert (`fans-cert.pem`, `fans-cert-key.pem`)
   - Runs `manage.py migrate` (creates the database)
   - Runs `manage.py collectstatic`
   - Adds `fans-barangay.local` to the hosts file
   - Registers the auto-start Task Scheduler task
   - Registers the watchdog Task Scheduler task
   - Prompts to create the initial admin account (**Create Admin** screen)
5. Browser opens to `https://fans-barangay.local` when setup completes.

**Clean install vs. upgrade install:**

| Scenario | What happens |
|---|---|
| **Clean install** — no existing `C:\FANSC` data | Setup wizard runs on first launch; Create Admin appears; empty database |
| **Upgrade install** — existing `C:\FANSC\.env` and `db.sqlite3` present | Setup wizard does NOT run; existing accounts and data preserved; Create Admin does NOT appear |

To force a clean install after a previous installation: uninstall via Windows Apps, manually delete `C:\FANSC`, then run the installer again.

### Manual / source path (for IT/developer)

1. Ensure Python 3.11, `caddy.exe`, and `mkcert.exe` are available.
2. Run `scripts\setup\setup-complete.ps1` as Administrator.
3. All setup steps are automated: venv, dependencies, certificates, `.env`, migrations, auto-start, watchdog.
4. Copy `CLIENT-SETUP\` folder to a USB drive.

### Runtime data location (installer path)

| Data | Location |
|---|---|
| Application code (read-only bundle) | `C:\FANSC\_internal\` |
| Environment config and encryption keys | `C:\FANSC\.env` |
| Database | `C:\FANSC\db.sqlite3` |
| Media uploads | `C:\FANSC\media\` |
| Log files | `C:\FANSC\logs\` |
| TLS certificate | `C:\FANSC\fans-cert.pem`, `fans-cert-key.pem` |

The `_internal\` folder is overwritten on every reinstall. All user data (`C:\FANSC\.env`, `db.sqlite3`, `media\`, `logs\`) is preserved across reinstalls.

**What the installer must never contain** (enforced by payload safety scan):

- `.env` — contains machine-specific secrets
- `db.sqlite3` — contains beneficiary data and user accounts
- `fans-cert.pem` / `fans-cert-key.pem` — machine-specific TLS certificate and private key
- `rootCA.pem` — developer's local CA
- `logs\` — runtime log files
- `media\` — user-uploaded files

---

## 18. Client Setup Process

Client setup makes a staff device able to access `https://fans-barangay.local` from a web browser.

**What needs to happen on each client device (done once per device):**

1. The browser must trust the server's HTTPS certificate.
2. The browser must be able to resolve `fans-barangay.local` to the server's IP address.

**Step 1 — Copy the CLIENT-SETUP folder:**

After the server is set up, copy the entire `CLIENT-SETUP\` folder from the server (`C:\FANSC\_internal\CLIENT-SETUP\`) to a USB drive. This folder contains:
- `rootCA.pem` — the server's local Certificate Authority certificate (safe to share)
- `trust-local-cert.bat` — the trust setup script

**Step 2 — Run trust-local-cert.bat on the client device:**

Double-click `trust-local-cert.bat` and approve the UAC (administrator) prompt. The script:
- Imports `rootCA.pem` into the Windows Trusted Root Certification Authorities store
- Prompts for the server's IP address
- Adds the `fans-barangay.local → server IP` entry to the client's hosts file

After this runs, the browser will trust the FANSC HTTPS certificate and can resolve `fans-barangay.local`.

**Security note — rootCA.pem vs rootCA-key.pem:**

- `rootCA.pem` is the **public** certificate authority file. It is safe to copy to client devices. Clients need it to trust the server's HTTPS certificate.
- `rootCA-key.pem` is the **private key** of the certificate authority. It must **never** be copied to any client device. Keep it on the server only.

**What certificate trust does and does not fix:**

| Certificate trust (`trust-local-cert.bat`) | What it does |
|---|---|
| ✅ Removes the "Your connection is not private" browser warning | Yes |
| ✅ Enables the browser's camera/microphone API over this HTTPS connection | Yes |
| ❌ Fixes `ERR_CONNECTION_REFUSED` | **No** — that means the server is not running, not a trust issue |
| ❌ Fixes "Limited HTTP mode" banner | **No** — that is a Django/Caddy proxy header issue |

**ERR_CONNECTION_REFUSED** means the server's Caddy/Waitress processes are not running (or port 443 is not listening). It is not a certificate problem. The fix is to start the FANS-C service on the server.

**Network hostname resolution:**

If the office router supports local DNS/hostname mapping, configuring `fans-barangay.local → server IP` on the router means every device on the network automatically resolves the domain — no per-device hosts file edits needed. This is the recommended approach for a real barangay deployment.

---

## 19. Database and Data Flow Summary

### Database modes

- **SQLite (default):** Single-file database at `C:\FANSC\db.sqlite3`. Suitable for single-server deployments. One writer at a time (adequate for typical barangay usage).
- **PostgreSQL (optional):** For multi-server or high-concurrency deployments. Requires a separate PostgreSQL server. All workstations must share the same `EMBEDDING_ENCRYPTION_KEY`.

### Key data models

| Model | App | Contents |
|---|---|---|
| `CustomUser` | `accounts` | Staff accounts — username, hashed password, name, email, system role, officer assignment, employee ID, active flag |
| `OfficerPosition` | `accounts` | Officer position definitions (title, unique flag, order) |
| `OfficerAssignment` | `accounts` | Links users to officer positions with start/end date tracking |
| `Beneficiary` | `beneficiaries` | Senior citizen records — name, SC ID, DOB, address, lifecycle state, sync status |
| `Representative` | `beneficiaries` | Authorized representative per beneficiary — name, relationship, face embedding |
| `FaceEmbedding` | `verification` | Encrypted 512-dim face embedding per beneficiary |
| `LivenessTransaction` | `verification` | One-time liveness proof tokens — tx_token UUID, encrypted neutral-frame embedding, anti-spoof score, PAD score, expiry, consumed flag |
| `VerificationAttempt` | `verification` | Every face scan attempt — beneficiary, operator, similarity score, decision, liveness result, timestamp |
| `StipendEvent` | `verification` | Distribution events — name, type, start/end date, active flag |
| `ClaimRecord` | `verification` | Claim per beneficiary per event — timestamp, amount, verification method, operator, claimant type |
| `FaceUpdateRequest` | `verification` | Pending face re-enrollment requests |
| `ApprovalRequest` | `verification` | Manual review requests for borderline verifications |
| `SpecialClaimRequest` | `verification` | Special claim requests (e.g., for representatives outside events) |
| `AuditLog` | `logs` | Permanent audit trail — action, actor, target, timestamp, IP address, details (JSON) |

### Data flow summary

```
[Browser camera] → base64 image → [Django server]
    → MTCNN face detection → face crop
    → FaceNet embedding (512-dim vector)
    → Liveness/anti-spoof checks (independent of FaceNet)
    → If liveness passes: encrypt embedding → store in LivenessTransaction
    → Issue tx_token → send to browser
    → Browser sends tx_token back
    → Server validates tx_token → retrieve stored embedding
    → Compare with enrolled embedding (decrypt from FaceEmbedding)
    → Cosine similarity → VERIFIED / NOT VERIFIED / MANUAL REVIEW / DENIED
    → Write VerificationAttempt + AuditLog
    → If VERIFIED + active event: create ClaimRecord
```

**What is never stored:**
- Raw face images from verification or registration
- Challenge/proof frames
- Private keys (never in database; stored in `.env` only)

**What is always stored:**
- Encrypted embedding vectors (Fernet, AES-128-CBC + HMAC-SHA256)
- Numeric scores (anti-spoof, PAD, similarity) as plain floats
- Decision results and audit log entries

---

## 20. Testing and Validation Summary

### Test suite

The project uses Django's built-in `TestCase` framework. Tests cover URL routing, access control, model creation, role helpers, password validation, liveness gates, TX token behavior, and lookalike safety.

| Category | What is tested |
|---|---|
| URL access control | That URLs return the correct HTTP status for each role |
| Role helpers | `is_president()`, `is_admin()`, `is_admin_it()`, `is_staff_member()` |
| Password validators | Minimum length, character class, uppercase requirement |
| Audit log | Model creation, view access by role |
| Liveness TX gates | Anti-spoof gate, sequence frame gate, PAD gate, embedding failure |
| TX token validation | Missing token, expired token, used token, wrong beneficiary, wrong claimant |
| Lookalike safety gate | Manual review triggered on close lookalike, not triggered when no match |
| Officer management | Position creation, assignment, unique enforcement |
| User management | Create, edit, password reset, must_change_password flow |

**Current test results:**
- Tests: **400 passed, 0 failed** on `verification + accounts + beneficiaries + logs` (branch `2.1`, as of v2.1.11; includes 3 new PAD/liveness regression tests added in the deep-hardening follow-up)
- Django system check: **0 issues**
- Django deploy check: **1 warning** (W018 — `DEBUG=True` in dev environment, expected; 4 checks silenced for Caddy-handled features)

**Note:** FaceNet (TensorFlow/keras-facenet) is **not** imported during any test. All face-related tests use mock embeddings and mock model responses. The test suite is safe to run offline without model weights present.

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
| v2.2.1 *(historical internal label, folded into v2.1.x — never a separate release; see CHANGELOG.md)* | 323 |
| v2.2.2 *(same as above)* | 323 |
| v2.3.0 *(same as above)* | 328 |
| v2.3.1 *(same as above)* | 339 |
| v2.1.8 | 365 |
| v2.1.9 | 382 |
| v2.1.10 | 423 |
| v2.1.11 | 400 (verification + accounts + beneficiaries + logs; includes PAD/liveness deep-hardening tests) |

### Pre-release validation

Before every installer build:
1. `python manage.py check` — must show 0 issues
2. `python manage.py test` — all tests must pass
3. `python -m compileall` on all app folders — must produce no errors
4. Payload safety scan (`build_exe.ps1 -Clean`) — must print `SAFE`
5. Clean-PC test — Create Admin must appear; developer credentials must not work

---

## 21. Known Limitations

| Limitation | Detail |
|---|---|
| **Python 3.11 only** | TensorFlow 2.13.1 does not support Python 3.12 or 3.13. The system cannot be upgraded to a newer Python version without upgrading TensorFlow first. |
| **Heuristic anti-spoof, not a trained PAD model** | Current texture analysis can be defeated by a sharp, high-resolution phone screen displaying a face photo. The head-movement challenge mitigates this (a static screen cannot complete the challenge on its own), but a phone held at the correct angle by a person could potentially complete the challenge. A trained CNN PAD model would close this gap. |
| **FaceNet model download on first start** | keras-facenet downloads ~90 MB from GitHub on the first import. Internet access is required once. The weights are cached in a machine-local directory next to `fans_c.exe` (`<install folder>\models\keras-facenet\`), shared by interactive setup and the SYSTEM-account autostart process, so subsequent starts load from cache regardless of which Windows account starts FANS-C. |
| **SQLite — one writer at a time** | SQLite allows only one concurrent write. Adequate for typical barangay usage (one verification at a time per server). High-throughput or multi-workstation scenarios should use PostgreSQL. |
| **Camera requires HTTPS** | The browser camera API (`getUserMedia`) is unavailable on plain HTTP. HTTPS is mandatory. |
| **Windows only** | The installer, startup scripts, and watchdog are Windows-specific (PowerShell, Task Scheduler, Inno Setup). |
| **LAN-only design** | The system is not designed for public internet exposure. Do not expose port 443 to the internet without a full security review, VPN, and WAF. |
| **No forgot-password email flow** | Password resets are done by an administrator via the User Management interface. Email-based self-service reset is not implemented. |
| **No stipend calendar view** | Distribution events are listed (no calendar view). |
| **Registration fast path** | A high-quality live capture may skip the head-movement challenge during registration (risk-based). If a very high-quality phone screen photo can defeat the anti-spoof threshold (< 0.25), it could be enrolled without the challenge. The 0.25 hard threshold is the defense in this case. |

---

## 22. Future Improvements

These are **not implemented** in the current system. They are acknowledged improvements for future development.

| Improvement | Priority | Rationale |
|---|---|---|
| **Trained CNN PAD model** (e.g., MiniFASNet/Silent-Face-Anti-Spoofing) | High | Would replace the heuristic anti-spoof with a model trained specifically to distinguish live faces from printed photos, phone screens, and replays at various angles and lighting conditions. |
| **Forgot Password via email** (Gmail/Google Workspace SMTP) | Medium | Would allow staff to reset their own passwords without administrator intervention. Requires: Django password reset flow, Gmail App Password in `.env`, rate limiting, expiry, generic response (no enumeration). |
| **Stipend schedule calendar view** | Low | Visual calendar showing active and upcoming payout events. |
| **Multi-building PostgreSQL sync testing** | Low | Multi-server mode is supported via `USE_SQLITE=False` + `SYNC_API_URL`, but is not tested in all configurations. |
| **Long-term support Python/TF upgrade** | Medium | Django 4.2 extended support ended April 2026; target is Django 5.2 LTS. TF 2.13/Python 3.11 ceiling needs a resolution path. |

---

## Related Documents

| Document | Location | Purpose |
|---|---|---|
| System Overview | [docs/SYSTEM-OVERVIEW.md](SYSTEM-OVERVIEW.md) | Detailed system description for administrators and evaluators |
| System Structure | [docs/SYSTEM-STRUCTURE.md](SYSTEM-STRUCTURE.md) | Technical flows, folder map, component roles, defense Q&A |
| Database Guide | [docs/DATABASE-GUIDE.md](DATABASE-GUIDE.md) | Database models, backup/restore, migration management |
| Security Checklist | [docs/SECURITY-CHECKLIST.md](SECURITY-CHECKLIST.md) | Security review findings and status |
| Deployment Checklist | [docs/DEPLOYMENT-CHECKLIST.md](DEPLOYMENT-CHECKLIST.md) | Pre-deployment verification checklist |
| README | [README.md](../README.md) | Deployment and usage guide (end users, IT) |
| SETUP.md | [SETUP.md](../SETUP.md) | Developer/IT manual setup guide |
| BUILD.md | [dev/BUILD.md](../dev/BUILD.md) | Installer build guide |
| CHANGELOG | [CHANGELOG.md](../CHANGELOG.md) | Version history |
