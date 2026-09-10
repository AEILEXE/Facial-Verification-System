# FANSC v2.1.17 — Complete System Reference

**Status:** Final Official Release · **Source commit:** `103fd5d9a95ec90b177bc3e7f6c015bde6099046` · **Date compiled:** 2026-09-10

---

## 1. Document Purpose

This document is the single comprehensive technical reference for FANSC (Facial-verification-based Automated system for seNior citizen Stipend distribution, more formally "Secure FaceNet-Based Facial Verification System for Senior Citizen Stipend Distribution"). It is written so that a future developer, researcher, evaluator, technical administrator, panel member, or auditor can understand how the whole system works — architecture, data model, biometric pipeline, security posture, deployment, and known limitations — without first reading the entire source tree.

Every technical claim in this document is backed by the current v2.1.17 source code, configuration, or test results as they exist in this repository at the commit above. Where something could not be verified from the repository, this document says so explicitly rather than inferring it from generic Django, FaceNet, or biometric-security conventions. For narrower, task-specific documentation (setup steps, a single checklist, a single subsystem's design rationale), see the cross-references throughout and the documentation index in [README.md](../README.md).

FANSC v2.1.17 is the final official release. No further feature development is planned; ongoing work on the project is documentation maintenance only.

---

## 2. System Identity

| Field | Value |
|---|---|
| Full name | Secure FaceNet-Based Facial Verification System for Senior Citizen Stipend Distribution |
| Short name | FANSC / FANS-C |
| Current version | v2.1.17 |
| Release status | Final Official Release |
| Repository (local) | `C:\FANSC\Facial-Verification-System` |
| Repository (GitHub) | `AEILEXE/Facial-Verification-System`, branch `main` |
| Platform | Windows (server and client) |
| Intended deployment | A single dedicated server PC inside a barangay (local government) office, serving staff browsers over the local area network — no cloud dependency |

---

## 3. What FANSC Is

FANSC is a biometric identity-verification system deployed at the barangay level to manage senior-citizen stipend distribution. It replaces paper-based sign-in lists and manual identity checks with server-authoritative facial recognition, liveness detection, and presentation-attack detection, so that:

- Only a registered senior citizen (or their authorized representative) can claim a stipend.
- Every claim is tied to a recorded biometric verification event (`VerificationAttempt`).
- Common spoofing methods (printed photos, phone/screen replay, static images, recorded video) are checked for before any identity match is attempted.
- Every consequential action — registration, verification decision, administrative override, duplicate-face review, payout — is written to an audit trail (`AuditLog`).

**Who uses it:** barangay staff (registration and verification), Admin/President (oversight, approvals, user management), Technical Administrator (diagnostics, technical configuration, biometric evaluation tooling), and, indirectly, senior-citizen beneficiaries and their representatives as the subjects of verification.

**What it does not claim to do:** FANSC does not claim a certified or independently measured biometric accuracy rate (no FAR/FRR/accuracy percentage is asserted anywhere in current documentation without a caveat — see §30 and §39). It does not implement a trained neural presentation-attack classifier (its PAD is heuristic — see §20). It is not a general-purpose payments platform, does not integrate with a national ID system, and is not represented as legally certified for any regulatory compliance regime (see `SECURITY.md`, `PRIVACY.md`).

---

## 4. System Objectives

- **Verification integrity** — a stipend can only be claimed after a server-authoritative liveness check and a face match against an encrypted, previously enrolled template.
- **Accountability and traceability** — every registration, verification, override, and payout action is attributable to a specific staff account and timestamped in `AuditLog`.
- **Operational efficiency** — replaces manual list-checking with a verification flow staff can run from any browser on the local network, with retry and fallback paths so legitimate claimants are not permanently blocked by a single failed capture.
- **Controlled-environment security** — the system is designed for a single-server, local-network deployment with server-side enforcement of all security-relevant decisions (liveness, anti-spoofing, thresholds); client-side signals are advisory only.
- **Segregation of duties** — financial-mutation actions (overrides, payout release, stipend event management) are gated separately from general administrative read access (see §12).

---

## 5. High-Level Architecture

```
 Client Devices (barangay staff browsers, LAN)
        |
        |  HTTPS (mkcert-issued cert, fans-barangay.local)
        v
 Caddy (reverse proxy, TLS termination, security headers)
        |
        |  plain HTTP, 127.0.0.1:8000 only
        v
 Waitress (WSGI server, 4 threads, trusts only the local Caddy proxy)
        |
        v
 Django (fans project: accounts / beneficiaries / verification / logs apps)
        |
        +--> Database (SQLite by default, or PostgreSQL for a centralized
        |     deployment)
        |
        +--> Biometric components, in-process:
              - Face detection: RetinaFace -> MTCNN -> OpenCV Haar cascade
                (fallback chain)
              - Face recognition: FaceNet (keras-facenet, 512-d embeddings)
              - Liveness / anti-spoof: heuristic, server-side
              - Presentation Attack Detection: heuristic, server-side
```

Only the components actually present in the repository are shown. There is no message queue, no separate microservice, and no cloud API call in the verification path — everything from HTTPS termination to the biometric decision runs on the one server PC. See §32 for the full deployment picture and §33 for the HTTPS/certificate mechanics.

---

## 6. Technology Stack

| Component | Technology | Used for |
|---|---|---|
| Language | Python 3.11 (pinned — 3.12/3.13 not supported by `tensorflow-cpu` 2.13.x) | Entire backend and build tooling |
| Web framework | Django 4.2.21 | Application framework — ORM, views, templates, auth, admin |
| WSGI server | Waitress 3.0.2 | Serves Django in production; bound to `127.0.0.1:8000`, trusts only the local Caddy proxy for forwarded headers |
| Reverse proxy / TLS | Caddy (standalone binary, bundled under `tools/`) | Terminates HTTPS for the LAN, proxies to Waitress, sets security headers (HSTS, X-Frame-Options, etc.) |
| Face detection | RetinaFace (primary) → MTCNN (fallback) → OpenCV Haar cascade (last resort) | Locating and aligning a face in a captured frame |
| Face recognition | `keras-facenet` 0.3.2 (checkpoint `20180402-114759`, Inception-ResNet-v1 on VGGFace2) | Generating 512-dimensional face embeddings |
| Numerical / CV | `tensorflow-cpu` 2.13.1, `opencv-python` 4.10.0.84, `numpy` 1.24.3, `scipy` 1.11.4 | Model inference, image preprocessing, similarity math |
| Encryption | `cryptography` 43.0.3 (Fernet) | Encrypting stored face embeddings at rest |
| Frontend | Django templates, Bootstrap 5.3.2, Bootstrap Icons 1.11.3, vanilla JavaScript | Server-rendered UI; no SPA framework |
| Charts | Chart.js 4.4.4 | Analytics dashboards |
| Client-side face mesh | MediaPipe (WASM, browser-only) | UX-only liveness preview — never the security decision |
| Static files | WhiteNoise 6.9.0 | Compressed, manifest-hashed static file serving |
| Database | SQLite (default) or PostgreSQL (`psycopg2-binary`, for centralized deployment) | Persistent storage |
| Reporting | `openpyxl` 3.1.5 (XLSX), `reportlab` 4.5.1 (PDF) | Report export |
| Packaging | PyInstaller (onedir build) | Bundles the Python/Django/ML runtime into a Windows executable |
| Installer | Inno Setup 6 | Produces the Windows installer (`FANS-C-Setup-v2.1.17.exe`) |

---

## 7. Application Architecture

FANSC is a single Django project (`fans/`) with four local apps, listed in `INSTALLED_APPS` in this order: `accounts`, `beneficiaries`, `verification`, `logs`.

### `accounts/` — Users, authentication, org chart, password recovery
Owns the custom user model and all staff-facing authentication and role logic.
- **Key models:** `CustomUser` (role, account status, employee ID), `OfficerPosition` / `OfficerAssignment` (organizational org-chart, separate from system role — see §12), `PasswordResetRequest` (admin-mediated recovery queue), `PasswordResetOTP` (self-service email-OTP recovery).
- **Key views:** login/logout with IP-based lockout, the two password-recovery flows, first-run bootstrap (`create_admin`, `bootstrap_president`), full user CRUD, officer position/assignment CRUD, org chart.
- **Relationship to other apps:** every other app's models reference `CustomUser` for "who did this" (`created_by`, `performed_by`, `reviewed_by`, `approved_by`, etc.).

### `beneficiaries/` — Beneficiary and representative records
Owns the senior-citizen registry and the representative (proxy-claimant) records.
- **Key models:** `Beneficiary` (personal/address/ID fields, status, offline-sync fields, duplicate-review fields), `DuplicateNameDobRequest` (name+DOB collision review), `Representative` (FK to `Beneficiary`, one beneficiary may have several), `SharedRepresentativeReview` (cross-beneficiary shared-representative review case).
- **Key views:** dashboard, beneficiary list/detail/edit, 4-step registration flow, representative add/deactivate, duplicate-face and name/DOB review queues, offline-sync conflict review.
- **Services:** `sync.py` (offline → central-server push), `qc_barangays.py` (address reference data).

### `verification/` — Biometric verification engine, stipends, claims, analytics
The largest app — face matching, liveness, PAD, stipend scheduling, claim/payout lifecycle, fraud signals, analytics, and the controlled biometric-evaluation subsystem.
- **Key models (16):** `StipendEvent`, `FaceEmbedding`, `RepresentativeFaceEmbedding`, `UserFaceEmbedding` (defined but unused — its enforcing middleware was removed), `VerificationAttempt`, `AdditionalFaceEmbedding`, `FaceUpdateLog`, `FaceUpdateRequest`, `ManualVerificationRequest`, `ClaimRecord`, `SpecialClaimRequest`, `LivenessTransaction`, `LivenessEvidenceReservation`, `SystemConfig`, `EvaluationDataset`, `EvaluationTrial`.
- **Key views (~85 functions across `views.py`, 8,624 lines):** the core verification flow (`verify_select` → `verify_start` → `verify_check_liveness` → `verify_submit` → `verify_result` / `verify_fallback`), manual-review and special-claim admin queues, stipend event CRUD/approval, face re-enrollment, representative face registration, payout actions, shared-representative review, reports, analytics dashboards, fraud signals, and the Biometric Evaluation (research) workflow.
- **Services:** `face_utils.py` (detection, alignment, embedding, comparison), `liveness.py`, `pad.py`, `fraud_signals.py`, `analytics.py` / `biometric_analytics.py` / `template_analytics.py`.
- On Django startup, `VerificationConfig.ready()` warms up FaceNet and MTCNN in a background thread (skipped for management commands, tests, or `FANS_SKIP_FACENET_WARMUP=1`).

### `logs/` — Audit trail and notifications
A passive/consumer app written to by the other three.
- **Key models:** `AuditLog` (structured, append-only action log — see §27 for exactly what "append-only" does and does not guarantee), `Notification` (per-user inbox with category/priority/dedupe).
- **Key views:** audit log list, verification log list, notification center.

### `fans/` — Project-level configuration
Not a Django "app" with models, but the project glue: `settings.py`, `urls.py`, `wsgi.py`, `views.py` (error handlers, health checks, the authenticated media-serving view), `middleware.py` (two custom middlewares — see §11), `context_processors.py`, `production_guard.py` (fail-closed production startup checks), `backup_status.py`, `report_export.py` (export sanitization), `stream_safety.py`.

---

## 8. Backend Architecture

- **Request handling:** standard Django MVT. `fans/urls.py` routes `accounts/`, `dashboard/` (→ `beneficiaries.urls`), `verification/`, and `logs/` via `include()`, plus root-level health/system views served directly from `fans.views`. `/` redirects to `/dashboard/`.
- **Media serving:** Django's default insecure static `MEDIA_URL` serving was deliberately replaced with `fans_views.serve_protected_media` — a login-gated view that only serves a path matching an actual `FileField`/`ImageField` value on a real `Beneficiary`, `SharedRepresentativeReview`, or `CustomUser` row, with per-object authorization on top. A path that does not match a tracked record returns a generic 404, not a filesystem read.
- **Middleware stack (in order):** `fans.middleware.DynamicCookieSecurityMiddleware` → `SecurityMiddleware` → `WhiteNoiseMiddleware` → `SessionMiddleware` → `CommonMiddleware` → `CsrfViewMiddleware` → `AuthenticationMiddleware` → `MessageMiddleware` → `fans.middleware.AccountStatusMiddleware` → `XFrameOptionsMiddleware`. The two custom middlewares: `DynamicCookieSecurityMiddleware` strips the `Secure` cookie flag only on requests that actually arrived over plain HTTP (supporting a documented LAN-IP HTTP fallback without weakening the HTTPS path), and `AccountStatusMiddleware` force-logs-out a session the moment the account becomes inactive/suspended mid-session.
- **Forms and validators:** each app defines its own `forms.py`; `accounts/validators.py` adds two password-strength validators wired into `AUTH_PASSWORD_VALIDATORS`; `beneficiaries/validators.py` enforces senior-citizen DOB rules (age ≥ 60, no future DOB) and ID-number normalization.
- **Context processors:** Django's `debug`, `request`, `auth`, `messages`, plus `fans.context_processors.server_access_info` (injects LAN/local/domain URLs into every template) and `logs.context_processors.notifications`.
- **Authentication backend:** Django's default `ModelBackend`; no custom backend override was found.
- **Database access:** exclusively via the Django ORM — no raw SQL was found in the reviewed views/models.
- **Transactions:** not separately audited in depth beyond model-level constraints (e.g., the one-`claimed`-`ClaimRecord`-per-`(beneficiary, stipend_event)` database constraint). *Not established by the current repository:* an exhaustive audit of every `atomic()` block was outside the scope of this documentation pass.

---

## 9. Frontend Architecture

- **Templates:** server-rendered Django templates rooted at `templates/base.html`, using Bootstrap 5.3.2 (with a CDN fallback if the bundled copy fails to load) and Bootstrap Icons 1.11.3.
- **JavaScript:** plain custom scripts in `static/js/` — `webcam.js`, `verify.js`, `liveness.js`, `register.js`, `analytics.js`, `analytics_biometric.js`, `address_cascades.js`, `qc_address.js`. No SPA framework (React/Vue) is present.
- **Camera/capture interface:** the browser captures webcam frames and posts them to the server as base64-encoded JSON payloads (most face-capture paths) or, in one case, as a size-capped (10 MB) multipart upload.
- **Client-side face mesh:** MediaPipe FaceMesh runs in the browser to drive the on-screen liveness UX (e.g., "turn now" prompts) — explicitly documented in `verification/liveness.py` as never trusted for the pass/fail decision.
- **AJAX/fetch:** used for the liveness-check step (`verify_check_liveness`) and other interactive flows; CSRF tokens are present in 53 templates (68 occurrences) covering the POST forms found.
- **Responsive/UX polish:** the v2.1.17 release specifically modernized the auth screens, manual review, org chart, user list, and analytics pages, and added a month/year Payout Calendar grid (event fields HTML-escaped before DOM insertion).
- **Browser-side validation vs. server authority:** consistently, client-side checks (form validation, MediaPipe preview scores) are UX conveniences only. Every security-relevant decision — liveness, anti-spoofing, face match, thresholds — is recomputed and decided server-side; this pattern is explicit and repeated throughout `verification/liveness.py` and `verification/views.py`.

---

## 10. Database Architecture

**Engine:** SQLite by default (`db.sqlite3`, file-based, `USE_SQLITE=True`), or PostgreSQL for a centralized-server deployment (`USE_SQLITE=False`, via `psycopg2-binary`, with connection pooling).

**Entity-relationship summary** (model names and FK fields as they exist in code — not inferred):

- **Users** (`accounts.CustomUser`) is the root actor referenced throughout the system as `created_by`, `performed_by`, `reviewed_by`, `approved_by`, `claimed_by`, `released_by`, `override_by`, `registered_by`, etc. (all `SET_NULL` on delete, preserving audit history after account removal). `OfficerAssignment.user` links a user to an `OfficerPosition` (the org-chart title — a concept intentionally separate from the software `role` field; see §12).
- **Beneficiary and Representative** (`beneficiaries` app): `Representative.beneficiary` (FK, `CASCADE`, `related_name='representatives'`) — one beneficiary may have several representatives. `Beneficiary.duplicate_match_beneficiary` is a self-referencing FK used when a new registration's face matches an existing one. `SharedRepresentativeReview.representative` (FK, `CASCADE`) captures the case where one person's face matches a representative already enrolled under a *different* beneficiary.
- **Biometric templates** (`verification` app): `FaceEmbedding.beneficiary` (OneToOne, `CASCADE`) is the primary encrypted template; `AdditionalFaceEmbedding.beneficiary` (FK, `CASCADE`) holds extra templates for multi-template matching (§17); `RepresentativeFaceEmbedding.representative` (OneToOne) is a representative's single template; `UserFaceEmbedding.user` (OneToOne) exists in the schema but its enforcing middleware was deliberately removed — currently unused.
- **Verification and claims** (`verification` app): `VerificationAttempt.beneficiary` (FK, `CASCADE`) is the immutable per-attempt record; `.representative` and `.stipend_event` are `SET_NULL`. `ClaimRecord` is the completed/queued payout — `.beneficiary` (`CASCADE`), `.stipend_event` / `.representative` / `.verification_attempt` (`SET_NULL`); a database constraint enforces one `claimed` record per `(beneficiary, stipend_event)` unless `is_special_additional=True`, with `SpecialClaimRequest.original_claim` as the escape hatch. `LivenessTransaction.used_by_attempt` (FK → `VerificationAttempt`) marks which attempt consumed the single-use liveness proof — the mechanism preventing face-switching between the liveness step and the final match (§19).
- **Stipend scheduling:** `StipendEvent` stands alone as the payout-period definition; `VerificationAttempt`, `ClaimRecord`, `ManualVerificationRequest`, `SpecialClaimRequest`, and `LivenessTransaction` all optionally reference it (`SET_NULL`).
- **Audit:** `AuditLog.user` (FK, `SET_NULL`) plus free-text `target_type`/`target_id` fields (not a true FK — a generic string reference) form the system-wide audit trail. `Notification.recipient` (FK, `CASCADE`) is the per-user alert inbox.

There is no separate `Account`/`StaffProfile` model beyond `CustomUser`, and no separate `Payout` model beyond `ClaimRecord` — `ClaimRecord`'s own fields (`amount`, `reference_number`, `released_by`, `released_at`) directly represent the payout event.

See [DATABASE-GUIDE.md](DATABASE-GUIDE.md) for the full table-by-table schema reference.

---

## 11. Authentication and Account Security

- **Login/logout:** `UserLoginView` extends Django's `LoginView`; a cache-based per-IP lockout blocks an IP after 8 failed attempts within a 600-second window, locking it out for 900 seconds. Every login, login-failure, and logout is written to `AuditLog`. `logout_view` calls `request.session.flush()` (full session invalidation, not just de-authentication).
- **Password handling:** standard Django `set_password()`/PBKDF2 hashing on `CustomUser` (`AbstractUser` subclass). `AUTH_PASSWORD_VALIDATORS`: similarity, minimum length 10 (raised from Django's default 8), common-password, numeric-password, plus two custom validators (`CharacterClassValidator`, `UppercasePasswordValidator`).
- **OTP:** 6-digit numeric code generated with `secrets.randbelow` (cryptographically secure), stored only as a `make_password()` hash (never plaintext), delivered by email only (no SMS) when SMTP is configured. Expiry 5 minutes, max 5 verification attempts, per-IP and per-user rate limiting (the per-user resend cooldown uses `cache.add()` for atomicity).
- **Password recovery:** a hybrid model — self-service email-OTP (opt-in, requires SMTP) plus an always-available admin-mediated approval queue (`PasswordResetRequest`). Anti-enumeration: the same generic "if this account exists, a code was sent" message is returned regardless of whether the identifier resolves to a real user. See [PASSWORD-RECOVERY-ARCHITECTURE.md](PASSWORD-RECOVERY-ARCHITECTURE.md) for the full design rationale — verified consistent with the current code.
- **Sessions:** `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE='Lax'`, 8-hour session age, `SESSION_EXPIRE_AT_BROWSER_CLOSE=True`.
- **CSRF:** `CsrfViewMiddleware` active; `CSRF_COOKIE_HTTPONLY=True`, `SAMESITE='Lax'`; `{% csrf_token %}` present in every POST form found by search; no `@csrf_exempt` views found.
- **Cookies:** `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE` are dynamic — `True` by default (tied to `not DEBUG`), overridable via `SECURE_COOKIES`. `DynamicCookieSecurityMiddleware` strips the `Secure` flag per-response only for a request that actually arrived over plain HTTP, supporting a documented LAN-IP HTTP fallback mode without weakening the HTTPS path.
- **Account states:** `CustomUser.account_status` (active/inactive/suspended) plus `is_active`; `AccountStatusMiddleware` force-logs-out a session the instant the account goes inactive, closing a session-revocation gap Django does not handle by default.
- **Authorization:** see §12.

---

## 12. Roles and Permissions

FANSC has four **System Roles** (`CustomUser.role`): **President**, **Admin**, **IT** (user-facing label: **Technical Administrator**), **Staff**. Two legacy values (`head_brgy`, `admin_it`) exist only for migration compatibility and are no longer assignable.

**System Role is explicitly distinct from Officer Position.** System Role controls software permissions. Officer Position (`OfficerPosition` / `OfficerAssignment`) represents an actual barangay/OSCA organizational office (President, Treasurer, Board of Director, …) with start/end dates, driven entirely by explicit assignment records — a Technical Administrator account with no assignment simply has no org-chart entry. See [TECHNICAL-ADMINISTRATOR-ROLE-MODEL.md](TECHNICAL-ADMINISTRATOR-ROLE-MODEL.md) for the full rationale.

| Role | `is_admin` | `has_financial_authority` | What it means in practice |
|---|---|---|---|
| **President** | Yes | Yes | Full operational authority, including President-exclusive actions (creating/promoting other President or Admin accounts, resetting other admin-tier passwords) |
| **Admin** | Yes | Yes | Administrative tasks, financial-mutation authority, cannot touch President-exclusive actions |
| **Technical Administrator** (`it`) | Yes | **No** | Broad **read** access to admin-tier screens (diagnostics, connection info, analytics) — but denied financial-mutation actions |
| **Staff** | No | No | Registration and verification operations only; no user management, no reports, no approvals |

**Financial authority is a real, narrower gate than "is admin."** `has_financial_authority` is `True` only for President and Admin, and specifically excludes Technical Administrator, for:

| Action | View(s) |
|---|---|
| Create/edit/delete a stipend event | `stipend_create/edit/delete` |
| Override a verification decision | `admin_override` |
| Release an overridden payout | `override_release_payout` |
| Cancel/fail/correct a released payout | `payout_action` |
| Approve/reject Manual Review (POST) | `manual_verify_review` |
| Approve/reject a Special Claim (POST) | `special_claim_review` |
| Assign/close an officer assignment | `officer_assignment_create/close` |
| Approve/reject a pending claim | `pending_claim_review` |

These are enforced server-side on the raw view, not merely by hiding UI buttons — a direct POST from a non-authorized role is rejected the same as from the UI (including privilege-escalation attempts like a non-President trying to create another President/Admin/IT account).

**Biometric Evaluation access matrix** (research subsystem, §30): Technical Administrator has full read+write (create/start/finalize/archive sessions, run/abort/withdraw trials); President has read-only oversight; Admin and Staff have no access.

**Beneficiary / Representative** are not system users — they are the subjects of verification and have no login.

---

## 13. Beneficiary Lifecycle

**Registration** — a multi-step flow (`register_step1/2/3`, `register_face`, `register_submit_face`): personal/address information → consent → face capture and enrollment (§15). `validate_senior_citizen_dob()` enforces age ≥ 60 and rejects a future date of birth.

**Validation and duplicate checks** — `check_duplicate_face()` searches a combined pool of existing beneficiary and representative embeddings (default threshold 0.80) — deliberately, since "a biometric identity is a single pool regardless of the role a record is registered under." A match does not hard-block or auto-approve registration; it sets `duplicate_review_required=True` and routes the record to an admin review queue (President/Admin/Technical Administrator), who approves it as a legitimate twin/lookalike or rejects it as likely fraud. The record cannot claim a stipend while pending.

**Approval/status** — `Beneficiary.status`: active / inactive / deceased / pending / disapproved, with deactivation audit fields.

**Verification and claim** — see §16–§21 for the biometric pipeline and §22 for the claim/payout flow.

**History/audit** — every registration, edit, verification attempt, and claim is tied to `AuditLog` entries and, for verification specifically, to `VerificationAttempt`/`ClaimRecord` rows.

**Offline sync** — `Beneficiary` carries `sync_status`/`sync_error`/`last_synced_at`/`offline_device` fields; `beneficiaries/sync.py` pushes records to a central server for a multi-device/offline deployment scenario, with a `sync_conflict_review` admin queue for collisions.

---

## 14. Representative Lifecycle

**Creation** — `add_representative`; `Representative.beneficiary` (FK, `CASCADE`) ties a representative to exactly one beneficiary, but one beneficiary may have multiple representatives.

**Identity document validation** — the one place a genuine file-extension allowlist is enforced (`{'.pdf', '.jpg', '.jpeg', '.png', '.docx'}`), specifically for the shared-representative-review authorization document, so that a malicious upload cannot later be served back as active content.

**Same-beneficiary identity protection** — at registration, `register_rep_face_submit` runs a dedicated gate comparing the new representative's face against the very beneficiary they are registering to represent (`compare_with_all_embeddings`, threshold 0.80 / `FACE_DEDUP_THRESHOLD`). A match is a **hard reject** — nothing is saved, unlike the cross-beneficiary case below. At claim time, `check_representative_beneficiary_fallback()` runs the same check again as defense-in-depth, blocking a claim where the "representative" is actually the beneficiary's own face.

**Same-face protection (cross-identity)** — if a representative's face matches an *already-enrolled* representative or beneficiary under a **different** identity, the registration is **not** rejected outright (one person may legitimately represent multiple seniors); instead the embedding is saved, `Representative.shared_review_status` is set to a pending state, and a `SharedRepresentativeReview` case is created for admin approval before that representative can be used to claim.

**Review workflow** — `shared_rep_review_list/detail`: an admin reviews the case (optionally with an uploaded authorization document) and approves, rejects, or requests further documentation.

**Activation/deactivation** — `deactivate_representative`.

**Authorization** — a representative's own biometric template is required to claim on a beneficiary's behalf; matching uses a single stored template (no multi-template support for representatives — see §17).

---

## 15. Facial Enrollment

**Capture** — the browser captures a frame, base64-encodes it, and posts it to the server; all processing happens server-side.

**Face detection (fallback chain):**
1. **RetinaFace** (primary) — confidence gate, rejects below 0.5.
2. **MTCNN** (fallback if RetinaFace is unavailable) — confidence gate, rejects below 0.85.
3. **OpenCV Haar cascade** (last resort) — bounding-box only, no landmarks/alignment.

A face touching the frame edge, or with an inter-eye distance under 15 pixels, is rejected before further processing.

**Alignment** — a 4-degree-of-freedom similarity transform (rotation + uniform scale + translation), computed from the two eye landmarks, maps them to canonical positions in a 160×160 output (the standard MTCNN/FaceNet convention).

**Preprocessing** — CLAHE (adaptive contrast) on the L channel in LAB color space, BGR→RGB conversion, resize to 160×160, `keras-facenet`'s built-in `fixed_image_standardization`, then L2-normalization (applied twice as a deliberate safeguard, and defensively re-applied inside the cosine-similarity function itself).

**Embedding model** — `keras-facenet`'s `FaceNet()` class, default checkpoint `20180402-114759` (Inception-ResNet-v1, VGGFace2). **Embedding dimension is 512** — this was runtime-verified against the package's own checkpoint metadata during this documentation pass, and the code explicitly warns not to assume the 128-dimensional checkpoints the same package also ships. (Several older documentation files previously stated "128" in error — corrected as part of this consolidation; see the version-history note in [CHANGELOG.md](../CHANGELOG.md).)

**No mock/fallback embeddings in production** — if TensorFlow or `keras-facenet` fails to load, the system raises `FaceNetUnavailableError` rather than silently comparing against a placeholder vector; a mock embedding generator exists but is explicitly test-only.

**Storage** — the embedding is stored as an encrypted binary field:
- `FaceEmbedding` (one primary template per beneficiary, `OneToOne`)
- `AdditionalFaceEmbedding` (any number of extra templates per beneficiary, added via the "Update Face Data" re-enrollment flow)
- `RepresentativeFaceEmbedding` (one per representative, `OneToOne`)
- `UserFaceEmbedding` (schema exists, currently unused — the staff face-login middleware that would have enforced it was removed)

**Encryption** — Fernet (AES-128-CBC + HMAC-SHA256) via `EMBEDDING_ENCRYPTION_KEY` from `.env`. If the key is unset, the system fails loudly rather than generating an ephemeral key — deliberately fail-closed, so a restart never silently invalidates every stored template. If the key is ever lost or changed, all previously stored embeddings become permanently undecryptable and affected beneficiaries must re-enroll.

**Raw photo storage** — `Beneficiary.profile_picture` is a separate, plain (unencrypted) `ImageField`, distinct from the encrypted biometric template — an optional registration photo, not the biometric identity data itself. Raw frames submitted during **verification** (as opposed to registration) are processed in memory and never written to disk (see §29).

**Duplicate checking** — see §13.

---

## 16. FaceNet Verification Pipeline

Step by step, from camera to decision:

1. **Camera capture** — browser captures a frame, base64-encodes it.
2. **`verify_start`** — initializes the verification session and assigns a liveness challenge.
3. **`verify_check_liveness`** (AJAX) — runs the server-side anti-spoof texture score and PAD heuristics, validates the head-movement challenge against server-side re-detected pose (not client-supplied landmarks), and — if all checks pass — issues a single-use `LivenessTransaction` binding the session to the face embedding computed from the **neutral/frontal frame** (not the angled challenge frame).
4. **`verify_submit`** — decrypts the embedding stored on the `LivenessTransaction` and uses **that** as the "live" embedding for matching (deliberate face-binding: this prevents swapping in a different image between the liveness step and the final match).
5. **Face detection, alignment, preprocessing** — same pipeline as enrollment (§15).
6. **Embedding comparison** — cosine similarity between the live embedding and the beneficiary's (or representative's) stored template(s).
7. **Multi-template matching** — for a beneficiary claim, the maximum score across the primary template and any additional templates is used (§17); for a representative claim, a single stored template is used.
8. **Threshold evaluation** — the base decision (§18).
9. **Liveness/PAD gates** — already enforced in step 3; face matching never runs if liveness or PAD fails.
10. **Quality and lookalike overrides** — may escalate a would-be "verified" result to "manual review" (§18).
11. **Final decision recorded** — a `VerificationAttempt` row is written with the decision, similarity score, and all relevant flags.
12. **Downstream claim eligibility** — a `ClaimRecord` is auto-created only for a `verified` decision; `manual_review` and `not_verified` require further action (§21, §22).

**The similarity metric, in plain language:** cosine similarity measures the angle between two embedding vectors, ignoring their magnitude. Two embeddings pointing in nearly the same direction in the 512-dimensional embedding space (regardless of length) score close to 1.0 — meaning the two faces are judged highly similar by the model. A score near 0 or negative means the two faces are dissimilar. FANSC re-normalizes both vectors to unit length before computing the dot product, which is mathematically equivalent to cosine similarity.

---

## 17. Multi-Template Matching

For a **beneficiary** claim, matching is confirmed to be `s_max = max_j cosine(e_live, e_stored_j)` — the live embedding is compared against the beneficiary's primary `FaceEmbedding` *and* every `AdditionalFaceEmbedding` (added over time via re-enrollment), and the **highest** valid similarity score wins. The winning template is recorded on the attempt (`matched_template`) along with the full score breakdown, for audit and for `template_analytics.py`'s per-template win-rate reporting.

The reason multiple templates exist: a person's appearance can drift over time (aging, weight change, hairstyle, glasses), and a single enrollment photo may stop matching well. Rather than forcing a hard re-enrollment cutover, additional templates accumulate and the best match is used, while `template_analytics.py` can flag a beneficiary whose match confidence is trending down as a candidate for voluntary re-enrollment (advisory only — never automatic).

For a **representative** claim, there is no multi-template support — matching uses the representative's single stored template (`RepresentativeFaceEmbedding`, `OneToOne`).

---

## 18. Verification Decision Policy

Two genuinely separate thresholds exist, forming a four-zone decision band, computed as:

```
review_band = VERIFICATION_THRESHOLD * 0.85

score >= AUTO_VERIFY_THRESHOLD        -> verified
threshold <= score < AUTO_VERIFY      -> manual_review  (high band)
review_band <= score < threshold      -> manual_review  (low band)
score < review_band                   -> not_verified
```

With the current production defaults (`VERIFICATION_THRESHOLD = 0.75`, `AUTO_VERIFY_THRESHOLD = 0.88`), `review_band = 0.6375`:

- **score ≥ 0.88** → `verified` — a `ClaimRecord` is auto-created (subject to the overrides below).
- **0.6375 ≤ score < 0.88** → `manual_review` — the attempt is saved, no `ClaimRecord` is created, and an administrator must explicitly approve release.
- **score < 0.6375** → base decision is `not_verified`, which then falls into the retry flow (§21).

**The Manual Review floor is not the Automatic Verification threshold, and the two must not be confused.** Lowering the manual-review floor (`VERIFICATION_THRESHOLD`) does **not** lower the automatic-verification bar (`AUTO_VERIFY_THRESHOLD`) — they are independently configured (and independently overridable at runtime via a `SystemConfig` database row, which production code consults in preference to the raw settings constant).

**Two overrides can only ever raise a decision, never lower it:**
- **Quality override** (`LOW_QUALITY_FORCES_MANUAL_REVIEW=True`) — would force a would-be `verified` down to `manual_review` if the captured frame failed a quality check. **Important caveat, self-corrected in current documentation:** on the common transaction-bound path (where a `LivenessTransaction` embedding is available, which is the normal case), this quality value is not populated and the rule is a documented no-op. It only actively fires on the rarer fallback path where no TX embedding is available.
- **Lookalike escalation** — if another beneficiary's stored embedding also scores within a small band (0.05, a code-level default not present in `.env`/`settings.py`) of the claimed score, a `verified` decision is escalated to `manual_review`.

---

## 19. Liveness Detection

FANSC's liveness check has two server-side components, both required when `LIVENESS_REQUIRED=True` (the default and the only allowed production setting — see §35):

1. **Anti-spoof texture score** — a heuristic (not machine-learned) weighted combination of Laplacian variance (focus/blur), local-block variance, and Sobel edge density, checked against `ANTI_SPOOF_THRESHOLD` (0.25).
2. **Server-authoritative head-movement challenge** — the beneficiary is asked to turn their head to one side (`'side'` is currently the only challenge direction issued, though the code supports others). Critically, the server **independently re-runs its own face detector** on the raw frame bytes it received and computes head pose itself — it does **not** trust client-supplied MediaPipe landmark data for the pass/fail decision. This closed a real, previously-spoofable trust boundary (documented in the code as a v2.1.16 "Round #4" hardening fix, after an earlier version did trust client-supplied landmarks). The pass threshold is 4.0 degrees of measured yaw change.

**Session binding** — a `LivenessTransaction` token is single-use, expires in 120 seconds, and is bound to the specific verification session (`session_id`), so a token minted for one attempt cannot be replayed against another. It also stores three independent hash fingerprints of the captured evidence (raw-byte hash, decoded-pixel hash, and a perceptual hash) with database-level uniqueness constraints — defending, respectively, against byte-identical, pixel-identical, and near-identical (recompressed/resized) frame replay.

**What is client-side vs. server-side:** the on-screen "turn now" UX cue and the MediaPipe FaceMesh preview are client-side only, used to decide when to enable the submit button — the code is explicit that this is never the security decision. The anti-spoof score, PAD heuristics, head-pose re-detection, replay-hash checks, and session/token binding are all server-side.

**Nothing in the liveness pipeline is a trained ML classifier** — it is rule-based (fixed-weight formulas, degree thresholds, hash comparisons). The only machine-learning models anywhere in the pipeline are the face detector (for landmark localization) and FaceNet (for identity embedding) — neither is a liveness/anti-spoof classifier.

---

## 20. Presentation Attack Detection

FANSC's PAD module's own docstring is explicit: **"Does NOT require a trained CNN model... these are heuristics, not a trained CNN."** It combines four to five weighted signals:

- **Specular glare** — HSV high-value/low-saturation pixel fraction (screen reflections).
- **Screen flatness** — block-variance uniformity (prints/LCDs are unnaturally smooth compared to real skin texture).
- **Sharpness/texture ratio** — LCD screens are sharp but texture-poor compared to real skin.
- **Sequence staticness** — inter-frame motion; a still photo or looped video shows near-zero motion between frames.
- **Near-duplicate frame detection** — perceptual-hash comparison across a frame sequence, catching a held-still photo or looped video.

The composite score is compared against `PHONE_SCREEN_SPOOF_THRESHOLD` (0.40); with `PAD_REQUIRED=True` (the required production setting), a suspicious score **denies** the attempt rather than merely flagging it for review. Recent hardening requires glare to be corroborated by flatness before triggering the highest-confidence weight, specifically so that ambient light or glasses reflections on a *real* face don't cause false rejections.

**There is no trained neural spoof-detection classifier anywhere in this codebase.** Both the liveness and PAD modules explicitly document this as a known limitation and name (without implementing) potential future integrations such as Silent-Face-Anti-Spoofing or MiniFASNet for deployments needing a higher security bar.

---

## 21. Verification State Machine

```
 Capture
   |
   v
 Liveness (anti-spoof texture + server-authoritative head-movement challenge)
   |  (fail -> denied / retry, depending on the specific check)
   v
 LivenessTransaction issued (single-use, session-bound, expires 120s)
   |
   v
 Identity Match (cosine similarity, multi-template for beneficiaries)
   |
   v
 Quality / Lookalike gates (can only escalate toward Manual Review)
   |
   v
 +----------+------------------+----------------+
 | Verified | Manual Review    | Not Verified   |
 +----------+------------------+----------------+
   |             |                    |
   |             v                    v
   |      Admin approval       Retry (up to MAX_RETRY_ATTEMPTS=2,
   |      required for         new challenge each time)
   |      payout release            |
   |                                 v
   |                          Fallback (ID-document-based
   |                          manual verification path,
   |                          after retries exhausted)
   v
 Claim/Payout eligibility (ClaimRecord auto-created;
 still subject to normal payout-release/cancel controls)
```

Retry only applies to the base `not_verified` outcome — `verified` and `manual_review` results return immediately without consulting the retry counter. After `MAX_RETRY_ATTEMPTS` (2) exhausted retries, the system marks the attempt's `fallback_triggered` flag, logs an `AuditLog` fallback action, and routes to `verify_fallback` — an ID-document-based manual verification path for staff/admin.

---

## 22. Stipend Distribution Workflow

1. **Event creation** — a `StipendEvent` is created (`event_type`: regular / birthday_bonus / custom), with an approval workflow (`approval_status`) and an amount that gets snapshotted into each resulting claim.
2. **Eligibility and claim** — a beneficiary (or their representative) is verified against the event; a successful verification creates a `ClaimRecord` linked to the `VerificationAttempt` that authorized it.
3. **Approval / manual review** — a `manual_review` decision requires admin approval (`has_financial_authority`) before any payout proceeds.
4. **Payout** — `payout_action` (cancel/fail/correct a released payout) and `override_release_payout` are gated to `has_financial_authority`.
5. **Finalization** — `ClaimRecord.status` moves through claimed / pending_approval / rejected / cancelled / failed; a database constraint enforces one claimed record per `(beneficiary, stipend_event)` unless explicitly marked `is_special_additional`.
6. **Special/additional claims** — `SpecialClaimRequest` is the escape hatch for a legitimate second claim on the same event (e.g., a correction), referencing the `original_claim` and requiring admin approval.
7. **Audit** — every step above writes to `AuditLog`.

*Event-window/payout-time-window rules and "blocked payout" edge cases exist in the model (`StipendEvent` carries optional payout date/time window fields) but were not exhaustively enumerated in this documentation pass — for the complete current rule set, consult `verification/views.py`'s stipend and claim views directly.*

---

## 23. Birthday Bonus / DOB Logic

`StipendEvent.event_type` includes a `birthday_bonus` option, and `Beneficiary` date-of-birth is protected: `validate_senior_citizen_dob()` enforces age ≥ 60 and rejects a future DOB, and DOB correction is a privileged, audited action (`beneficiary_correct_dob`) rather than a normal field edit — consistent with DOB being both an eligibility gate (age ≥ 60) and, for birthday-bonus events, a scheduling input.

*Not established in further detail by this documentation pass:* the exact mechanics of how a `birthday_bonus`-type `StipendEvent` is matched to a beneficiary's specific birth date (e.g., automatic month/day matching versus manual event-to-beneficiary association) were not independently traced through `verification/views.py` in this research pass. A future documentation update should verify this directly against the `stipend_*` views before making a more specific claim.

---

## 24. Calendar and Event Management

Stipend events are managed through `stipend_list/create/edit/delete/approve/reject`. The v2.1.17 release added a **Payout Calendar** — a month/year calendar grid rendered client-side (`static/js/analytics.js`, `stipend_list.html`), with all event fields HTML-escaped before insertion into the DOM (an explicit XSS precaution called out in the CHANGELOG).

*Not established in further detail by this documentation pass:* specific UI affordances such as a "Past Events" filter or "Jump to Date" control were referenced in the original task brief but not independently verified against the current templates/views in this research pass.

---

## 25. Analytics

`verification/analytics.py`, `biometric_analytics.py`, and `template_analytics.py` back four dashboards: `analytics_executive`, `analytics_operational`, `analytics_security`, `analytics_biometric_performance`.

[ANALYTICS-METHODOLOGY.md](ANALYTICS-METHODOLOGY.md) explicitly scopes the system to **descriptive, rule-based** analytics and lists claims FANSC must not make (no predictive analytics, no ML-based fraud prediction). This distinction matters and is enforced in current documentation:

**Operational metrics are not biometric accuracy metrics.** The "Verified Rate" shown on operational dashboards is the proportion of verification *attempts* that resulted in a `verified` decision — a real, useful operational statistic — but it is **not** the same thing as FAR (False Accept Rate), FRR (False Reject Rate), or overall biometric accuracy, which require known ground truth (a controlled trial where the true genuine/impostor status of each attempt is known in advance). FANSC does not have completed ground-truth evaluation data as of this release (§30) and does not claim otherwise.

---

## 26. Reporting

Reports include claims, event summaries, staff performance, override/fallback activity, suspicious-attempt summaries, and beneficiary history, exportable as CSV, XLSX (`openpyxl`), and PDF (`reportlab`).

**Formula-injection protection is real and implemented:** `sanitize_export_cell()` (`fans/report_export.py`) prefixes any string cell beginning with `=`, `+`, `-`, `@`, tab, or carriage return with a literal `'`, forcing spreadsheet software to render it as text rather than executing it as a formula. This runs on every XLSX cell built through the shared report-workbook helper and is also called directly at several raw CSV/XLSX export sites. Numbers, decimals, and dates pass through unmodified.

*Not established as exported by this documentation pass:* no evidence was found of embeddings or raw face images being included in any report export — reports operate on operational/administrative fields, not biometric template data.

---

## 27. Audit and Logging

`AuditLog` is a **structured, append-only-by-convention** table — not a cryptographically tamper-evident ledger. It has no hash chaining, no digital signature, and no database-level immutability constraint. The only enforced restriction is at the Django-admin UI layer, where `VerificationAttempt`, `FaceEmbedding`, `ClaimRecord`, and `AuditLog` are registered read-only (no add/change/delete) — a UI-layer restriction that a superuser with direct database or shell access could still bypass. `docs/SECURITY-CHECKLIST.md` itself self-flags "audit logs are read-only to staff" as `[REVIEW]` (not fully confirmed) rather than overstating this control, and this reference document follows the same honesty rather than repeating the "tamper-evident" phrasing found in some older documentation (corrected during this consolidation).

What **is** logged is extensive: authentication events, password lifecycle, user/officer administration, every verification decision (including no-face, multiple-faces, subject-changed, and stale-transaction cases), claims and payouts (including duplicate-payout attempts), duplicate-face and shared-representative review outcomes, backup completion status, report exports, and biometric-evaluation research actions. Each row carries the actor (nullable, `SET_NULL` on user deletion), action code, a free-text target type/ID, a structured JSON `details` blob, IP address, user agent, and timestamp.

`Notification` is the companion per-user inbox for actionable alerts (approval-required, fraud/security alerts, password-reset requests), with a dedupe key to avoid duplicate notifications for the same underlying event.

---

## 28. Security Architecture

| Area | What is actually implemented |
|---|---|
| RBAC | Four system roles; `has_financial_authority` narrows Technical Administrator below full admin authority; raw-POST-level enforcement, not just UI hiding (§12) |
| CSRF | `CsrfViewMiddleware`; token present in all POST forms found; no `@csrf_exempt` found |
| XSS | Django template auto-escaping (default); calendar event fields explicitly HTML-escaped before DOM insertion |
| SQL injection | Django ORM exclusively; no raw SQL found in reviewed code |
| Authentication | Custom per-IP lockout, cryptographically-random OTP, hashed OTP storage, strong password validators |
| Session security | HttpOnly, SameSite=Lax, 8-hour age, browser-close expiry |
| Secure cookies | Dynamic `Secure` flag (on by default, stripped only for verified plain-HTTP LAN requests) |
| HTTPS | Caddy terminates TLS with an mkcert-issued local certificate (§33) |
| Upload validation | Content-based validation for face captures (a face must actually be detected); explicit 10 MB cap on the one multipart face-upload path; extension allowlist for the one document-upload path |
| Export sanitization | Real, implemented formula-injection protection on spreadsheet exports (§26) |
| Biometric protection | Fernet-encrypted embeddings; fails closed (refuses to run) if the encryption key is missing |
| Authorization | Server-side checks on every sensitive view, not just hidden UI elements |
| Production guards | Startup hard-fails (refuses to run) if `DEBUG`, a placeholder `SECRET_KEY`, a wildcard `ALLOWED_HOSTS`, `DEMO_MODE`, or a weakened liveness/PAD configuration is detected in a production/frozen build |
| Secrets | Read from environment/`.env`, never hardcoded to a real value |
| Backup permissions | NTFS ACL restricted to Administrators+SYSTEM before any sensitive file is copied into a backup |
| Path traversal | Media serving is authorized by matching a real database record, not by trusting a request-supplied filesystem path |
| Command execution | No unsanitized `subprocess`/`os.system` calls found; the backup script's SQLite invocation specifically avoids embedding a user/install path as text to sidestep a quoting/injection risk |
| Audit integrity | Honestly **not** cryptographically tamper-evident — append-only by convention and UI-restricted only (§27) |

This is a summary of controls actually found in the current codebase — not a certification. See `docs/SECURITY-CHECKLIST.md` for the full, dated, itemized checklist (including several items self-flagged `[REVIEW]`/`[RISK]` rather than claimed as solved) and §38/§39 below for an honest final assessment and known limitations.

---

## 29. Privacy and Biometric Data Lifecycle

```
 Collection (webcam capture, base64 payload)
   |
   v
 Processing (face detection, alignment, CLAHE preprocessing) — in memory
   |
   v
 Embedding (FaceNet, 512-d vector)
   |
   v
 Storage (Fernet-encrypted BinaryField — FaceEmbedding / AdditionalFaceEmbedding /
          RepresentativeFaceEmbedding)
   |
   v
 Verification (embedding compared in memory; raw verification frame bytes
               are decoded, processed, and discarded — never written to disk)
   |
   v
 Audit (AuditLog records the decision and metadata — no biometric content)
   |
   v
 Backup (db.sqlite3 + .env + media/, ACL-restricted, integrity-checked)
```

**Raw facial media vs. embeddings vs. operational records vs. evaluation records — kept distinct:**
- **Raw facial media** from a *verification* request is processed in memory and discarded; it is never written to disk or the database.
- **`Beneficiary.profile_picture`** is a separate, optional, unencrypted registration photo — distinct from the biometric template and stored only if provided at registration.
- **Embeddings** (the biometric templates) are the only persisted biometric data from the verification pipeline, and are encrypted at rest.
- **Operational records** (`VerificationAttempt`, `ClaimRecord`, `AuditLog`) store scores, decisions, and metadata — not biometric content.
- **Evaluation records** (`EvaluationDataset`/`EvaluationTrial`, §30) are a deliberately isolated research subsystem with their own documented no-raw-media guarantee.

**Retention:** *Not established by the current repository* — no automatic biometric-data deletion/retirement workflow beyond ordinary beneficiary deactivation was found in this research pass. If a beneficiary is deactivated, their embedding remains stored (encrypted) unless a separate, unaudited deletion action is taken.

**Encryption key loss:** if `EMBEDDING_ENCRYPTION_KEY` is lost or changed, all previously stored embeddings become permanently undecryptable — this is a deliberate fail-closed design choice, but it also means key backup is operationally critical (see §36).

---

## 30. Research / Biometric Evaluation Architecture

FANSC includes a deliberately isolated research subsystem for controlled biometric evaluation: `EvaluationDataset` and `EvaluationTrial` models, gated to Technical Administrator (full access) and President (read-only) — Admin and Staff have no access at all.

The design keeps **ground truth** (the true genuine/impostor or bona fide/attack status of a trial, entered by the evaluator) strictly separate from the **system decision** (what FANSC actually output), which is the only way to later compute meaningful FAR/FRR-style metrics without circular reasoning. `EvaluationTrial` explicitly documents that no raw face media (photo/video/frame) is stored for its own records.

**This subsystem's own methodology and runbook documents are exemplary on a point the broader project must get right:** [BIOMETRIC-EVALUATION-METHODOLOGY.md](BIOMETRIC-EVALUATION-METHODOLOGY.md) and [BIOMETRIC-EVALUATION-RUNBOOK.md](BIOMETRIC-EVALUATION-RUNBOOK.md) explicitly and repeatedly state that they contain "no real values, no real participant data, and no results," were written *before* any real controlled evaluation was conducted, and require ethics/consent review as a separate precondition never claimed to be satisfied by the software itself. The runbook's own closing line: **"SYSTEM READY ≠ STUDY COMPLETED."**

**This reference document reaffirms that statement:** real participant biometric evaluation data has not been represented as completed anywhere found in this repository, and this document does not represent it as completed either. Operational analytics (§25) cannot substitute for this controlled evaluation, because operational data has no independently verified ground truth.

Evaluation data, once collected under this subsystem, would be **pseudonymous, not anonymous** — trial records are linked to specific dataset/session structures for research integrity, which is a different (and more honest) characterization than "anonymous."

---

## 31. File and Folder Structure

```
Facial-Verification-System/
├── fans/              Project config: settings, urls, wsgi, middleware,
│                       context processors, production guard, report export
├── accounts/           Users, auth, OTP, officer org-chart
├── beneficiaries/       Beneficiary and representative records, offline sync
├── verification/        Biometric engine, stipends, claims, analytics, research
├── logs/               Audit trail and notifications
├── templates/          Django HTML templates (base.html + per-app templates)
├── static/             Source CSS/JS/vendor assets (Bootstrap, Chart.js, MediaPipe)
├── staticfiles/         collectstatic output (WhiteNoise-served)
├── media/               Uploaded files (profile pictures, shared-rep documents)
│                       — never bundled into the installer
├── docs/                Documentation (this file included)
├── dev/                 Build tooling: build_exe.ps1, fans_c.spec, launcher.py,
│                       installer/ (Inno Setup script), historical planning docs
├── scripts/             setup/ start/ admin/ PowerShell tooling (incl. daily backup)
├── CLIENT-SETUP/         Certificate-trust package distributed to staff devices
├── tools/                Bundled Caddy + mkcert executables (installer payload)
├── installer/            Deprecated old Inno Setup script (superseded by dev/installer/)
├── legacy/               Deprecated/retired files, kept for reference
├── assets/               Branding assets (installer/app icon, etc.)
├── backups/              Local backup output directory
├── FANS-C-Installer/     Built installer output (.exe files by version)
├── manage.py
├── requirements.txt
└── Caddyfile             Reverse-proxy / TLS configuration
```

See `docs/folder-django-app.md`, `docs/folder-scripts.md`, `docs/folder-static-templates.md`, `docs/folder-client-setup.md`, and `docs/folder-logs.md` for a deeper per-folder walkthrough.

---

## 32. Deployment Architecture

A single dedicated Windows server PC runs the installed application inside one barangay office. Staff on the same local network reach it from any browser — no software is installed on client devices beyond a one-time certificate-trust step (§33). Caddy terminates HTTPS and reverse-proxies to Waitress on `127.0.0.1:8000`; Waitress serves Django; Django talks to SQLite (default) or, for a centralized multi-office deployment, PostgreSQL. There is no cloud dependency and no external network call in the verification path itself.

Client onboarding is a short manual/USB process (copy the `CLIENT-SETUP` folder, run `trust-local-cert.bat`, browse to `https://fans-barangay.local`) — see §33.

---

## 33. HTTPS and Certificates

The server runs `mkcert -install` to create a local root CA and signs `fans-cert.pem`/`fans-cert-key.pem` for the domain `fans-barangay.local`. Caddy uses this certificate to terminate HTTPS for all LAN clients (Caddyfile: `tls fans-cert.pem fans-cert-key.pem`), and also sets HSTS, `X-Content-Type-Options`, `X-Frame-Options: DENY`, a camera-permitting/mic-denying `Permissions-Policy`, `Cache-Control: no-store`, and strips the `Server` header.

**Client trust model:** client devices do **not** run mkcert themselves. FANSC auto-copies `rootCA.pem` (but never `rootCA-key.pem`, which must never leave the server) into the `CLIENT-SETUP` folder on every server launch. A staff device imports that root CA into its own Windows Trusted Root store via `certutil -addstore Root` (`trust-local-cert.bat`/`.ps1`), optionally adds a `fans-barangay.local` hosts entry, and then browses to the HTTPS URL.

**Dev-only alternative:** for same-laptop development, plain `http://127.0.0.1:8000` works without any certificate (a secure-context exemption); for LAN/multi-device development, the same Caddy+mkcert stack is run locally. Installed/production users are unaffected by either dev path.

---

## 34. Installer Architecture

1. **`dev/build_exe.ps1`** verifies Python 3.11 and the project's `.venv`, runs `collectstatic`, and invokes PyInstaller against `dev/fans_c.spec`.
2. **Payload safety scan** — the build script explicitly fails (`exit 1`) if the staging output contains `.env`, any `*.sqlite3` file, certificate/key material, log files, or runtime `logs`/`media`/certificate directories — preventing a prior run's real secrets, beneficiary data, or logs from being baked into the shipped installer.
3. **`dev/fans_c.spec`** produces a **onedir** PyInstaller build (deliberately not onefile, to avoid multi-gigabyte TEMP extraction on every launch). It bundles TensorFlow/Keras/OpenCV/MTCNN, the Django apps, templates, static assets, the `Caddyfile`, the bundled `tools/` (Caddy + mkcert), `CLIENT-SETUP/`, and `.env.example`. It explicitly **excludes** `media/`, `.env`, and `db.sqlite3`. The FaceNet model weights (~90 MB) are **not** bundled — they download to a machine-local cache on first run.
4. **`dev/installer/fans_c.iss`** (Inno Setup 6) packages the PyInstaller output into `FANS-C-Setup-v2.1.17.exe`, installed by default to the short path `C:\FANSC` (a long install path is known to break TensorFlow), requiring administrator privileges (for the root-CA install, hosts-file edit, and Task Scheduler registration).
5. **Code signing:** the installer is **unsigned** — no `SignTool`/Authenticode directive exists anywhere in the build or Inno Setup scripts, and no documentation claims otherwise.

---

## 35. Configuration

Selected configuration variables from `fans/settings.py` (defaults shown; all are environment-variable-driven):

| Variable | Default | Meaning |
|---|---|---|
| `DEBUG` | `False` | Django debug mode — production startup hard-fails if this is `True` in a frozen/production build |
| `DEMO_MODE` | `False` | Activates a lower "assisted rollout" threshold pair for pilots; **hard-fails production startup** if enabled in a production/frozen build |
| `SECRET_KEY` | placeholder | Django's cryptographic signing key — startup hard-fails if the placeholder is still in place with `DEBUG=False` |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` (+ auto-detected LAN IP) | Startup hard-fails if `'*'` is present in production |
| `VERIFICATION_THRESHOLD` | `0.75` | Manual-review floor for face-match cosine similarity |
| `AUTO_VERIFY_THRESHOLD` | `0.88` | Automatic-verification bar — independent of the floor above (§18) |
| `ANTI_SPOOF_THRESHOLD` | `0.25` | Hard liveness anti-spoof texture-score floor |
| `LIVENESS_REQUIRED` | `True` | Must remain `True` in production — startup hard-fails otherwise |
| `PAD_REQUIRED` | `True` | Must remain `True` in production — startup hard-fails otherwise |
| `PHONE_SCREEN_SPOOF_THRESHOLD` | `0.40` | PAD composite-score deny threshold; a production floor of 0.10 is enforced |
| `MAX_RETRY_ATTEMPTS` | `2` | Face-match retry attempts before falling back to ID-based manual verification |
| `LOW_QUALITY_FORCES_MANUAL_REVIEW` | `True` | See §18's caveat — largely a no-op on the common transaction-bound path |

Related values not in the original task list but load-bearing for the same logic: `DEMO_THRESHOLD` (0.60) / `DEMO_AUTO_VERIFY_THRESHOLD` (0.80) for demo mode only; `SAME_FACE_SEQUENCE_THRESHOLD` (0.30); `LIVENESS_MAX_ATTEMPTS` (3); `LIVENESS_CHALLENGE_TRIGGER_THRESHOLD` (0.30, registration-only risk-based challenge); `PAD_LANDMARK_MOTION_MIN_DEG` (2.0°); `LOOKALIKE_BAND` (0.05) and `FACE_DEDUP_THRESHOLD` (0.80), both code-level defaults not present in `settings.py`/`.env.example`; `EMBEDDING_ENCRYPTION_KEY` (no default — required, fails closed if missing).

No real secret values are reproduced in this document.

---

## 36. Backup and Recovery

A nightly Windows Task Scheduler job (`scripts/admin/daily-backup.ps1`) backs up exactly three items: `db.sqlite3` (via SQLite's online hot-backup, no service stop needed), `.env` (which contains `EMBEDDING_ENCRYPTION_KEY` and other secrets), and `media/`. The script, in order: takes an exclusive lock to prevent concurrent runs, restricts the backup destination's NTFS ACL to Administrators+SYSTEM **before** copying in any sensitive file, verifies the copied database with `PRAGMA integrity_check`, and writes a completion manifest only after every step succeeds. Rotation keeps the 14 most recent *restore-ready* backups (verified against the live file, not just manifest presence).

For the restore procedure and disaster-recovery guidance, see [BACKUP-RESTORE.md](BACKUP-RESTORE.md) — a well-maintained, currently accurate document with no discrepancies found against the backup script in this research pass.

---

## 37. Testing and Quality Assurance

As independently confirmed by an actual full test run against the current repository on 2026-09-10:

```
python manage.py test
Ran 1511 tests in 1379.603s
OK
```

**1511 tests, 0 failures, 0 errors, 0 skips.** Test files: `accounts/tests.py` (231), `beneficiaries/tests.py` (137), `verification/tests.py` (608), nine `verification/tests_bpa*.py` files (261 combined — Biometric Performance Analytics/evaluation-subsystem coverage), `logs/tests.py` (56), `fans/tests.py` (218).

```
python manage.py check
System check identified no issues (0 silenced).

python manage.py makemigrations --check --dry-run
No changes detected
```

No pytest configuration exists — tests run exclusively via Django's built-in test runner. Coverage spans authentication, password recovery, beneficiary/representative management, the face-verification pipeline (including liveness/PAD edge cases and deliberately-mocked model-unavailable error paths), stipend/claim/payout logic, RBAC/financial-authority gating, export sanitization, and the biometric-evaluation research subsystem.

---

## 38. Final Security Review

Security controls were reviewed and identified issues were addressed within the scope of the final v2.1.17 release (see the dated, itemized `docs/SECURITY-CHECKLIST.md`, most recently updated 2026-09-08). This is not a claim of certified or absolute security. Concretely:

- Production startup fails closed on a wide range of misconfiguration (weak `SECRET_KEY`, `DEBUG=True`, wildcard `ALLOWED_HOSTS`, `DEMO_MODE`, weakened liveness/PAD settings) rather than silently running insecurely.
- A previously spoofable client-trust boundary in the liveness challenge (trusting client-supplied landmark JSON) was identified and fixed (v2.1.16 hardening).
- Several controls are honestly self-flagged as partial or unconfirmed rather than overstated: Django admin superuser database access is only "partially mitigated" against tampering with supposedly read-only models; several upload-validation items are marked `[REVIEW]`/`[RISK]` rather than claimed solved.
- The audit log is a structured append-only table with UI-layer read-only enforcement, not a cryptographically tamper-evident ledger — this reference (and, following this consolidation, the rest of the documentation set) states that plainly rather than using the word "tamper-evident" loosely.

---

## 39. Known Limitations

- **No controlled biometric calibration/evaluation has been completed.** The evaluation subsystem (§30) is built and ready, but its own documentation is explicit that no real study has been run — "SYSTEM READY ≠ STUDY COMPLETED."
- **No FAR/FRR or accuracy claim is made without ground truth**, and none currently exists (§25, §30).
- **Presentation Attack Detection is heuristic, not a trained model.** A trained CNN-based PAD/anti-spoofing model (e.g., Silent-Face-Anti-Spoofing, MiniFASNet) would close a real gap but is not implemented.
- **The installer is unsigned.** No Authenticode signing is configured.
- **The audit log is not cryptographically tamper-evident** — append-only by convention and UI-restricted only, not hash-chained or signed (§27, §38).
- **The quality-override rule (`LOW_QUALITY_FORCES_MANUAL_REVIEW`) is largely a no-op on the common transaction-bound verification path** — it only actively fires on a rarer fallback path (§18).
- **Biometric retention/deletion policy beyond deactivation is not established** by the current repository (§29).
- **Django admin superuser access to supposedly read-only models is only partially mitigated**, not fully prevented at the database layer.
- **Some upload-validation hardening items remain self-flagged `[REVIEW]`/`[RISK]`** in `docs/SECURITY-CHECKLIST.md` rather than resolved.

---

## 40. Troubleshooting

This section indexes where to look; each referenced document contains step-by-step procedures verified against the current build.

| Symptom | Where to look |
|---|---|
| Application won't start | Check `production_guard.py`'s fail-closed conditions first — `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS`, `EMBEDDING_ENCRYPTION_KEY`, `DEMO_MODE`, liveness/PAD settings |
| Certificate trust errors on a client | [DEV-HTTPS.md](DEV-HTTPS.md), `CLIENT-SETUP/README.txt`, re-run `trust-local-cert.bat` |
| Can't reach `fans-barangay.local` | Confirm the hosts entry / DNS resolution on the client, and that Caddy is running |
| Database errors | Check `USE_SQLITE`/`DB_*` environment variables and file permissions on `db.sqlite3` |
| FaceNet/model load failure (`FaceNetUnavailableError`) | Confirm TensorFlow/`keras-facenet` installed correctly and the model cache is reachable (weights are not bundled — first run downloads them) |
| Camera not working | Browser camera permissions; camera access requires a secure context (HTTPS, or the `127.0.0.1` dev exemption) |
| Liveness/verification failures | Check challenge-direction/session-binding logic and current threshold values (§18, §19) via `SystemConfig` and `.env` |
| Login/OTP issues | Confirm `EMAIL_CONFIGURED` (SMTP settings) for the self-service OTP path; the admin-mediated `PasswordResetRequest` queue is always available as a fallback |
| Installer build fails at the payload safety scan | A prior run left `.env`, `db.sqlite3`, certificates, or logs in the staging output — clean the build directory and rebuild (`dev/build_exe.ps1`) |
| Backup/restore issues | [BACKUP-RESTORE.md](BACKUP-RESTORE.md) — check the NTFS ACL, Task Scheduler job status, and the backup manifest |

---

## 41. Maintenance

- **Configuration** lives in `.env` (see `.env.example` for the full variable list).
- **Runtime logs** live under a `logs/` runtime directory (`fans-startup.log`, `fans-watchdog.log`) plus Django's own rotating error-file handler; see `docs/folder-logs.md`.
- **Backups** run nightly via Task Scheduler (§36); back up `.env` (it contains `EMBEDDING_ENCRYPTION_KEY`) with the same rigor as the database.
- **Upgrades**: see `docs/ROLLBACK-PROCEDURE.md` for the general rollback procedure; migrations are managed normally via `manage.py migrate`.
- **Database**: SQLite is the default and sufficient for a single-office deployment; PostgreSQL is available for a centralized multi-office deployment.
- **Certificates**: the server-side mkcert root CA and leaf certificate are generated by server setup tooling; client trust is a one-time `certutil` import per device (§33).
- **Model/cache**: FaceNet weights are not bundled in the installer and download to a local cache on first run — ensure the server has one-time internet access (or a pre-seeded cache) during initial setup.
- **Release procedure**: see `dev/BUILD.md` for the canonical current build procedure (PyInstaller → payload safety scan → Inno Setup).

---

## 42. Release Identity

| Field | Value |
|---|---|
| Version | v2.1.17 |
| Status | Final Official Release |
| Source commit | `103fd5d9a95ec90b177bc3e7f6c015bde6099046` |
| Installer | `FANS-C-Setup-v2.1.17.exe` |
| SHA-256 | `2f36084b1c67423786a620015c99e9bdcad4572e6b09768d9b1c17b2d4693b5d` |
| Installer size | 221,414,699 bytes |
| Code signing | Unsigned |
| Tests | 1511 tests, 0 failures, 0 errors, 0 skips |
| `manage.py check` | 0 issues |
| `manage.py makemigrations --check --dry-run` | No changes detected |

The SHA-256 and file size above were independently recomputed against the actual installer artifact present in the repository (`FANS-C-Installer/FANS-C-Setup-v2.1.17.exe`) during this documentation pass, and the test result was obtained from a live, complete `python manage.py test` run against the current repository — not copied from a prior claim.

---

## 43. Glossary

| Term | Definition |
|---|---|
| **FANSC** | Secure FaceNet-Based Facial Verification System for Senior Citizen Stipend Distribution |
| **FaceNet** | The deep-learning face-recognition model architecture used to generate embeddings (via `keras-facenet`) |
| **Embedding** | A fixed-length numeric vector (512 dimensions in FANSC) representing a face, from which the original image cannot be directly reconstructed |
| **MTCNN** | Multi-task Cascaded Convolutional Network — a face-detection model; FANSC's fallback detector after RetinaFace |
| **Cosine similarity** | A measure of the angle between two vectors, used to score how similar two face embeddings are, independent of their magnitude |
| **Liveness** | Confirming that a real, present person — not a photo, screen, or recording — is being captured |
| **PAD** | Presentation Attack Detection — detecting an attempt to spoof biometric capture with a photo, screen, or replay |
| **Presentation Attack** | An attempt to fool a biometric system with a fake artifact (printed photo, phone screen, video replay) instead of a live person |
| **Manual Review** | A verification decision requiring explicit human (admin) approval before a claim can proceed |
| **Auto Verification** | A verification decision high-confidence enough to be automatically accepted without human review |
| **Beneficiary** | The senior citizen enrolled in and eligible for the stipend program |
| **Representative** | A person authorized to claim a stipend on a beneficiary's behalf, with their own enrolled biometric template |
| **Claim** | A specific instance of a beneficiary (or representative) successfully being verified and made eligible for a payout on a given stipend event |
| **Payout** | The actual release of stipend funds against a claim |
| **RBAC** | Role-Based Access Control |
| **CSRF** | Cross-Site Request Forgery — a web attack class defended against via Django's CSRF token mechanism |
| **OTP** | One-Time Password — used here as a self-service password-recovery mechanism, delivered by email |
| **Caddy** | The reverse-proxy/TLS-termination web server FANSC uses to serve HTTPS on the LAN |
| **Waitress** | The production WSGI application server that runs Django |
| **Django** | The Python web framework FANSC's backend is built on |
| **SQLite** | The default, file-based database engine |
| **PostgreSQL** | The alternative database engine for a centralized, multi-office deployment |
| **FAR** | False Accept Rate — the rate at which a biometric system incorrectly accepts an impostor; requires controlled ground-truth evaluation to measure, which FANSC has not yet completed |
| **FRR** | False Reject Rate — the rate at which a biometric system incorrectly rejects a genuine user; same ground-truth caveat as FAR |
| **TAR** | True Accept Rate |
| **TNR** | True Negative Rate |
| **Ground Truth** | The independently known, correct answer (e.g., "this attempt was genuinely this person") used to evaluate a biometric system's real accuracy |
| **Pseudonymization** | Replacing identifying data with a consistent alias/identifier so the underlying record is not directly identifying but remains traceable within the system — distinct from true anonymization, which removes that traceability entirely |
