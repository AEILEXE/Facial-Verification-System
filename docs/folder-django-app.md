# Folder: Django Application Folders

This document covers the five main Django folders in the FANS-C project: `fans/` (project configuration), `accounts/` (user authentication), `beneficiaries/` (beneficiary management), `verification/` (face verification engine), and `logs/` (audit and verification logging).

---

## Overview

Django organizes a project into one project package and multiple app packages. Each app handles a specific part of the system:

```
fans/           Project root — settings, URL routing, WSGI
accounts/       User accounts, login, role-based access control
beneficiaries/  Beneficiary registration, records, data sync
verification/   FaceNet face verification, liveness detection
logs/           Audit trail and verification history
```

---

## fans/ — Django Project Configuration

### Purpose

The `fans/` folder is the Django project package. It is the top-level configuration that ties all apps together. It defines global settings, the URL routing table, and the WSGI entry point used by Waitress.

### Why it exists

Every Django project requires a project package that contains the settings file and URL configuration. This is where Django looks for the list of installed apps, the database configuration, middleware stack, and security settings.

### Important files inside

**fans/settings.py**
- Django's global configuration file
- Reads from `.env` (via python-dotenv) to load secrets and environment-specific settings
- Configures: database (SQLite or PostgreSQL), installed apps, middleware, static file paths, HTTPS settings (SECURE_PROXY_SSL_HEADER, USE_X_FORWARDED_HOST), WhiteNoise for static file serving, session security, CSRF protection
- Key settings sourced from `.env`: SECRET_KEY, DEBUG, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, EMBEDDING_ENCRYPTION_KEY, DEMO_MODE, LIVENESS_REQUIRED, face recognition thresholds

**fans/urls.py**
- Maps URL paths to views across all installed apps
- Includes URL patterns from `accounts.urls`, `beneficiaries.urls`, and `verification.urls`
- Serves the Django admin interface at `/admin/`
- Serves media files (uploaded images) in development

**fans/wsgi.py**
- The WSGI callable that Waitress imports to serve the Django application
- Waitress calls this as: `waitress-serve --listen=127.0.0.1:8000 fans.wsgi:application`

**fans/views.py**
- Project-level views (dashboard, home page, error pages)

**fans/context_processors.py**
- Adds global context variables available to all templates (e.g., system name, version, current user role)

### How it connects to the system

- Waitress imports `fans.wsgi:application` to serve the application
- Caddy forwards all HTTPS requests to Waitress, which passes them through Django's middleware stack defined in `fans/settings.py`
- All other apps (`accounts`, `beneficiaries`, `verification`) are listed in `INSTALLED_APPS` in settings.py — without that listing, Django ignores them entirely
- The `EMBEDDING_ENCRYPTION_KEY` from `.env` is read in settings.py and passed to the verification app for face embedding encryption

### Runtime flow

| Phase | How fans/ is involved |
|---|---|
| Setup | `manage.py migrate` reads settings.py to create database tables; `manage.py collectstatic` reads STATICFILES_DIRS |
| Startup | Waitress imports fans.wsgi:application and begins serving |
| Runtime | Every HTTP request passes through middleware defined in settings.py |
| All phases | settings.py is loaded once at startup and stays in memory for the process lifetime |

### Defense notes

**Why are settings in a Python file, not a JSON or YAML file?**
Django's settings.py is a Python module, which means it can use conditionals, environment variable reads, and imports. This flexibility is used in FANS-C to read all secrets from the `.env` file via `python-dotenv`, so no secrets are hardcoded.

**What breaks if settings.py is misconfigured?**
- `SECRET_KEY` missing: Django refuses to start
- `ALLOWED_HOSTS` missing: All requests are rejected with 400 Bad Request
- `CSRF_TRUSTED_ORIGINS` missing: Staff cannot submit forms (stipend distribution stops)
- `SECURE_PROXY_SSL_HEADER` missing: Django doesn't know it's behind HTTPS and may reject secure cookies

---

## accounts/ — User Authentication and Role Management

### Purpose

The `accounts/` app manages user accounts: login, logout, password management, and role-based access control. It defines the custom user model with four active FANS-C roles (President, Admin, IT, Staff); legacy roles `admin_it` (migrated → Admin via accounts/0006) and `head_brgy` (migrated → President via accounts/0009) are no longer assignable.

### Why it exists

Django's built-in user model has no application-specific roles. FANS-C extends it with a `CustomUser` model that adds a `role` field and helper properties used by every view decorator in the system.

### Role system

| Role | DB value | Key helper properties | Intended user |
|---|---|---|---|
| President | `president` | `is_admin=True`, `is_president=True`, `is_head_barangay=True` (alias) | Operational head; approves claims, oversees distribution |
| Admin | `admin` | `is_admin=True` | Administrative; manages users and beneficiary records |
| IT | `it` | `is_admin=True`, `is_admin_it=True` | Technical; full system access, server setup, diagnostics |
| Staff | `staff` | `is_staff_member=True` | Frontline; runs verifications, no admin access |

`is_admin` = True when role is president, admin, or it (any non-Staff role). Gates all management-level access.
`is_admin_it` = True only for the `it` role. Gates system-diagnostic, connection, and network pages.
`is_president` / `is_head_barangay` = True only for the `president` role. Gates President-exclusive actions (pending claim approval, resetting other admin passwords).

**Legacy roles** (not assignable to new users):
| Legacy DB value | Migrated to | Migration |
|---|---|---|
| `head_brgy` | `president` | accounts/0009 |
| `admin_it` | `admin` | accounts/0006 |

### Important files inside

**accounts/models.py**
- Defines `CustomUser` — extends Django's `AbstractUser`
- Adds `role` CharField and role-helper properties (`is_admin`, `is_admin_it`, `is_head_barangay`, `is_staff_member`)

**accounts/views.py**
- Login / logout views
- `change_password` — any logged-in user can change their own password; session is kept alive after the change; clears `must_change_password` flag
- `admin_reset_password` — President can reset any user's password; Admin and IT can reset Staff accounts only; sets `must_change_password=True` on the affected user; all resets logged in `AuditLog`
- `user_list` / `user_create` / `user_edit` — User Management views at `/accounts/users/`; restrict profile-picture upload (field absent from `UserUpdateForm`)
- `user_set_status` — deactivate, suspend, or reactivate a user account; requires a written reason (≥5 chars, enforced client-side by a Bootstrap modal in `user_list.html` as of v2.1.16 — previously a native `prompt()` popup) for deactivate/suspend; permission-checked (`is_admin` only, no self-modification), fully audit-logged
- `otp_forgot_password` / `otp_verify` / `otp_reset` (in `accounts/otp.py` + `accounts/views.py`) — self-service email-OTP password recovery; `password_reset_request_create` / `password_reset_request_list` — the always-available admin-assisted fallback. See `docs/PASSWORD-RECOVERY-ARCHITECTURE.md` for the full design.

**accounts/forms.py**
- `LoginForm`, `UserCreateForm`, `UserUpdateForm` — role dropdowns show only the four active roles (President, Admin, IT, Staff); legacy `head_brgy` and `admin_it` values never appear for new users; `UserUpdateForm` does not include a `profile_picture` field
- `PasswordChangeForm` — self-service change (requires current password)
- `AdminPasswordResetForm` — admin reset (no current password needed); runs Django's full password validator suite; requires at least one uppercase letter

**accounts/urls.py**
- `login/`, `logout/`
- `password/change/` — self-service change for any logged-in user
- `password/reset/<user_id>/` — admin reset (President, Admin, and IT only)
- `users/` — user list; `users/create/` — create user; `users/<id>/edit/` — edit user

**accounts/admin.py**
- Registers `CustomUser` in Django's admin interface (`/admin/`)
- Allows IT to manage users directly from the admin panel

**Role checking in views**
Views in `beneficiaries/` and `verification/` use an inline check pattern: `if not request.user.is_admin: raise PermissionDenied`. This is the established convention across the codebase. The `accounts/decorators.py` file was removed because it was never wired up and its definitions (`admin_required`, `it_admin_required`) duplicated the inline checks without being used anywhere.

**accounts/management/commands/**

| Command | What it does |
|---|---|
| `create_admin.py` | Creates an admin-role user non-interactively (used in scripts) |
| `generate_key.py` | Generates a new Fernet encryption key (for EMBEDDING_ENCRYPTION_KEY) |
| `init_config.py` | Initializes required system configuration (called during setup) |
| `normalize_roles.py` | Ensures all user role values are valid (migration/cleanup tool) |
| `check_system.py` | System-level diagnostics from within Django |

### How it connects to the system

- All other apps (`beneficiaries`, `verification`) protect their views with inline role checks against `request.user.is_admin` (President/Admin/IT) or `request.user.is_admin_it` (IT only)
- `fans/settings.py` sets `AUTH_USER_MODEL = 'accounts.CustomUser'` to use the custom model system-wide
- The setup script calls `python manage.py createsuperuser` (which creates a Django superuser and defaults the FANS-C `role` to `it`) via the overridden `CustomUserManager.create_superuser`
- Both Django's `is_superuser`/`is_staff` flags and the FANS-C `role` field must be set for full admin access

### Runtime flow

| Phase | How accounts/ is involved |
|---|---|
| Setup | `migrate` creates the CustomUser table; `createsuperuser` creates the first admin user |
| Runtime (every request) | Django's session middleware validates the session; decorators check the role before allowing access |
| Daily use | Staff log in via the login view; session persists until logout or timeout |

### Defense notes

**Why a custom user model?**
FANS-C needs role-based access control beyond what Django's built-in permissions system provides out of the box. A custom user model with a `role` field is the standard Django approach for application-level roles. It also allows adding future fields (e.g., assigned barangay, phone number) without a separate profile model.

**What happens if the admin role is not set?**
A user created with `createsuperuser` gets Django's `is_superuser=True` and the FANS-C `role` defaults to `it` (set by `CustomUserManager.create_superuser`). If the role is not set or is set to `staff`, the user can access the Django admin panel (via `is_superuser`) but cannot access FANS-C admin views (which check the `role` field). The `create_admin` management command ensures the correct role is set.

---

## beneficiaries/ — Beneficiary Management

### Purpose

The `beneficiaries/` app manages senior citizen beneficiary records: registration, listing, editing, and data synchronization. Each beneficiary in the system has a profile with personal information and an enrolled face embedding (stored in the database, encrypted).

### Why it exists

The beneficiary registry is the core data layer of the system. Before any verification can happen, a beneficiary must be registered with their face. This app handles that registration process and provides the interface for managing the beneficiary list.

### Important files inside

**beneficiaries/models.py**
- `Beneficiary` model — represents a registered senior citizen
- Stores: name, date of birth, address, contact information, barangay assignment, face embedding (encrypted bytes), enrollment date, status

**beneficiaries/views.py**
- Registration view: collects personal information and captures a face image via the browser camera
- List view: displays all registered beneficiaries with search and filter
- Detail view: shows a single beneficiary's profile and verification history
- Edit view: update beneficiary information (Admin only)

**beneficiaries/forms.py**
- BeneficiaryRegistrationForm — validates all required fields for registration
- BeneficiaryUpdateForm — subset of fields for editing existing records

**beneficiaries/urls.py**
- Maps URLs for registration, list, detail, and edit views

**beneficiaries/admin.py**
- Registers `Beneficiary` in the Django admin panel
- Allows IT and Admin to inspect or edit records directly

**beneficiaries/sync.py**
- Synchronization logic for updating beneficiary records from an external data source (if configured)
- Called by the `sync_beneficiaries` management command

**beneficiaries/management/commands/sync_beneficiaries.py**
- Django management command to trigger a beneficiary data sync
- Can be run from the command line: `python manage.py sync_beneficiaries`

### How it connects to the system

- The `verification` app looks up beneficiary records (and their stored face embeddings) when processing a verification request
- The face embedding stored in `Beneficiary.face_embedding` is encrypted with `EMBEDDING_ENCRYPTION_KEY` from `.env` — the `verification` app decrypts it before comparing
- The `accounts` app's decorators protect beneficiary views — Staff can register and view; Admin can also edit
- The `templates/beneficiaries/` folder contains all HTML templates for beneficiary pages

### Runtime flow

| Phase | How beneficiaries/ is involved |
|---|---|
| Setup | `migrate` creates the Beneficiary table |
| Daily use (registration) | Staff fills out registration form, camera captures face, face embedding is computed by verification/face_utils.py and stored encrypted |
| Daily use (verification) | verification/ looks up the beneficiary record to retrieve the stored face embedding |
| Administration | Admin views the beneficiary list, checks for duplicates, manages records |

### Defense notes

**Why store face embeddings instead of photos?**
Face embeddings are compact (128 floating-point numbers vs. kilobytes for a photo), encrypted at rest, and are not directly reversible to a photo. Storing embeddings also means the computationally expensive FaceNet model only needs to run during enrollment — verification is just a vector comparison.

**What happens if the embedding key changes?**
If `EMBEDDING_ENCRYPTION_KEY` changes (or is not set, causing a random key to be generated each restart), all previously stored embeddings cannot be decrypted. Verification will fail for all previously enrolled beneficiaries. This is why backing up `.env` is critical.

---

## verification/ — Face Verification Engine

### Purpose

The `verification/` app is the core of the FANS-C system. It processes face verification requests: capturing a live camera image, running it through FaceNet to generate an embedding, comparing it against the stored embedding, and returning a match decision.

### Why it exists

Face verification is the primary innovation of FANS-C and requires dedicated logic separate from general Django views. The verification app encapsulates the FaceNet model loading, face detection (MTCNN), embedding generation, anti-spoofing (liveness detection), and the similarity comparison threshold logic.

### Important files inside

**verification/apps.py**
- `VerificationConfig(AppConfig)` — registers the app and runs `ready()` on Django startup
- `ready()` starts a background daemon thread (`fans-facenet-warmup`) that calls `get_facenet_model()` and `_get_mtcnn()` immediately after Django initializes
- This means the FaceNet model is loaded into memory by the time any staff opens a browser, eliminating the 5-15 second first-request delay that would otherwise occur when TensorFlow builds its computation graph
- If the model is unavailable (TensorFlow not installed, or the weights could not be downloaded), the warmup logs a clear message; it does NOT fall back to a mock/random model — registration and verification fail closed until the real model loads (see `FaceNetUnavailableError` in `face_utils.py`)

**verification/face_utils.py**
- The core FaceNet integration module
- Loads the keras-facenet model (~90 MB, downloaded from GitHub — `faustomorales/keras-facenet` releases — and cached in `<install folder>\models\keras-facenet\`, a machine-local directory next to `fans_c.exe` — see `get_facenet_cache_dir()` — not `%USERPROFILE%\.keras-facenet\` or `~/.keras`) as a module-level singleton — loaded once per process, reused for every request. The machine-local location means an elevated interactive first run and the SYSTEM-account autostart process always share the same cache.
- `get_facenet_model()` — lazy-init with global cache; called by `apps.py` at startup and by every embedding request
- `get_embedding(face_img)` — CLAHE → BGR→RGB → FaceNet → L2-normalize; identical pipeline for registration and verification
- `compare_with_all_embeddings(live, beneficiary)` — best cosine similarity across primary + all additional templates
- Face similarity threshold is read from `SystemConfig` (db-backed) respecting `DEMO_MODE`

**verification/liveness.py**
- Anti-spoofing module
- Analyzes image texture using frequency domain analysis to distinguish a live face from a printed photo or screen
- Returns a liveness confidence score and a pass/fail decision
- Threshold is read from `.env` (`ANTI_SPOOF_THRESHOLD`)
- If `LIVENESS_REQUIRED=True` in `.env`, a failed liveness check blocks verification entirely

**verification/views.py**
- Handles the two-phase verification flow:

  **Phase 1 — `verify_check_liveness` (issues `tx_token`):**
  1. Receive neutral frame + sequence frames + `challenge_completed=True`
  2. Anti-spoof neutral frame; require ≥ 3 sequence frames
  3. Run PAD sequence analysis
  4. Compute FaceNet embedding from the **neutral/frontal frame** (not the challenge frame)
  5. Create a `LivenessTransaction` binding the liveness proof to the embedding
  6. Return `tx_token` to the browser

  **Phase 2 — `verify_submit` (consumes `tx_token`):**
  1. Validate and consume the `tx_token`
  2. Retrieve the embedding stored in the `LivenessTransaction`
  3. Look up the beneficiary's stored embedding from the database
  4. Decrypt the stored embedding (using `EMBEDDING_ENCRYPTION_KEY`)
  5. Compute cosine similarity
  6. Apply threshold → VERIFIED or NOT VERIFIED
  7. Write the verification and claim records to the database
  8. Return the result to the browser

**verification/models.py**
Key models:
- `StipendEvent` — distribution event with `date` (reference/announcement), `payout_start_date`, `payout_end_date`, and an optional daily `payout_start_time`/`payout_end_time`; supports multi-day payout periods. Admin-created schedules start `approval_status=pending_approval` and are not usable for claiming until the President approves; President-created schedules publish immediately. Two different "active" queries exist on purpose: `get_active_event_for_date()` (date-window + approval only, no time-of-day check — used for display) vs. `get_open_events_now()`/`is_within_time_window()` (date window + daily time window — the actual claim gate used by Verify). The dashboard's event card calls the time-window check too (as of v2.1.16) so it shows `SCHEDULED` rather than `OPEN` when the event is active for today but outside its daily window.
- `VerificationAttempt` — records every face scan attempt (score, liveness, decision, claimant type)
- `ClaimRecord` — completed or pending payout claim; `STATUS_CLAIMED` = finalized, `STATUS_PENDING_APPROVAL` = awaiting President approval (used when no active event exists), `STATUS_REJECTED`
- `ManualVerificationRequest`, `FaceUpdateRequest`, `SpecialClaimRequest` — admin approval queue items
- `FaceEmbedding`, `AdditionalFaceEmbedding`, `RepresentativeFaceEmbedding` — encrypted biometric data

**verification/views.py**
Handles the full verification workflow plus:
- `report_claims` — filterable claims report; `?export=excel` → Excel download, `?export=print` → print-ready HTML
- `report_event_summary` — per-event payout totals (claimed / pending / rejected / attempts / fallback)
- `pending_claim_review` — President approves or rejects a claim that was submitted without an active event
- Pending-claim logic in `verify_submit`: if face passes but no active event, President creates `STATUS_CLAIMED` directly; Staff/Admin/IT creates `STATUS_PENDING_APPROVAL` logged as `ACTION_CLAIM_PENDING`

**verification/urls.py**
- Core verification flow, admin queue, stipend events, face updates, registration review, rep face registration
- `reports/claims/` — Claims Report (HTML + Excel + print)
- `reports/event-summary/` — Event Summary Report
- `manual-review/pending-claim/<claim_id>/` — pending claim approval page

**verification/admin.py**
- Registers `VerificationLog` in the Django admin panel
- Allows IT and Admin to review the full audit trail of all verification attempts

### How it connects to the system

- Depends on `beneficiaries/` to look up the registered face embedding for a beneficiary
- Depends on `accounts/` for authentication (staff must be logged in to run a verification)
- Depends on `fans/settings.py` for configuration values (thresholds, LIVENESS_REQUIRED, EMBEDDING_ENCRYPTION_KEY)
- The browser JavaScript sends the webcam frame as a base64-encoded image in a POST request; the view decodes it and passes it through the verification pipeline
- FaceNet model is loaded once at Django startup (not on every request) and kept in memory — this is why the first request after a cold start may be slightly slower

### Runtime flow

| Phase | How verification/ is involved |
|---|---|
| Startup | face_utils.py loads the FaceNet model into memory (or logs a failure — verification then fails closed rather than falling back to mock) |
| Runtime | Every verification request flows through views.py → liveness.py → face_utils.py → beneficiary lookup → similarity comparison |
| Diagnostics | `/verification/config/` shows the model status (loaded or unavailable) and current threshold settings |

### Defense notes

**Why does FaceNet need to download ~90 MB on first use?**
The keras-facenet package contains the model architecture but not the trained weights. The weights are downloaded from GitHub (`faustomorales/keras-facenet` releases) on the first call to load the model and then cached in `<install folder>\models\keras-facenet\` — a machine-local directory next to `fans_c.exe` (see `get_facenet_cache_dir()` in `face_utils.py`), **not** `%USERPROFILE%\.keras-facenet\` or `~/.keras` (keras-facenet's own default, which this project deliberately overrides so an elevated interactive first run and the SYSTEM-account autostart process share the same cache). After the first startup, the model is fully local to this machine — no internet access is needed. This is a standard practice for pre-trained deep learning models distributed via Python packages.

**What happens if the FaceNet model fails to load?**
If the real model fails to load (wrong Python version, TensorFlow error, path issue, or no internet on a clean machine's first run), FANS-C fails closed: `get_facenet_model()` raises `FaceNetUnavailableError` instead of ever returning a mock/random-embedding model, so no embedding is ever computed from it. Registration and verification return a clear "model unavailable" error and refuse to proceed — staff can still log in and use non-biometric features, but no FaceEmbedding is stored and no verification decision is produced from fabricated data. The model status is visible at `/verification/config/`.

**Why is liveness detection important?**
Without liveness detection, an attacker could hold a photo of a beneficiary's face in front of the camera and pass verification. The liveness check analyzes image texture patterns — a photo printed on paper or displayed on a screen has different frequency characteristics than a real face. This is a software-only check (no specialized hardware required).

**How is the similarity threshold chosen?**
The threshold is the minimum cosine similarity score required to declare a match. A score of 1.0 means identical embeddings; a score of 0.0 means completely unrelated. The default threshold in `DEMO_MODE` (0.60) is intentionally lower to account for variations in lighting and camera quality. A stricter threshold reduces false positives but increases false rejections. The threshold can be tuned in `.env` without changing any code.

---

## logs/ — Audit Trail and Verification History

### Purpose

The `logs/` app records every significant system action in a tamper-evident audit trail and exposes a filtered view of the verification history. It is the primary accountability mechanism for the system — every login, verification, admin override, and stipend claim is logged here.

### Why it exists

Barangay stipend distribution requires an auditable record for accountability and dispute resolution. The `logs/` app provides that trail without coupling audit logic into every other app — other apps call `AuditLog.log(...)` as a fire-and-forget operation.

### Important files inside

**logs/models.py**
- `AuditLog` — records every auditable action with: `user` (FK to CustomUser), `action` (one of 30+ action constants), `target_type`, `target_id`, `details` (JSONField), `ip_address`, `user_agent`, `timestamp`
- Action constants cover: login/logout/login_failed, registration, verification, override, user management, config changes, face update workflow, claim lifecycle (pending/approved/rejected), payout events (cancelled/failed/override), duplicate detection, offline sync, password management, report exports
- `AuditLog.log(action, user, ...)` — class method used by all apps to write a log entry; extracts IP from `X-Forwarded-For` if present
- `Notification` — in-app notification shown via the navbar bell icon; `category` (7 categories including fraud alert / security alert / approval reminder), `priority` (LOW/MEDIUM/HIGH), `title`, `message`, `url` (click-through target), `dedupe_key` (prevents duplicate notifications for the same underlying event), `is_read`/`read_at`. Created via `logs/notifications.py` helpers (`notify_admins()` — role-gated to President/Admin/IT; `notify_user()` — a specific user) and resolved via `resolve_notification(dedupe_key)` when the underlying case is decided.

**logs/notifications.py**
- `notify_admins(category, title, message, url, dedupe_key, ...)` / `notify_user(user, category, title, message, ...)` — creation helpers; deduped per `(recipient, dedupe_key)` so the same underlying event doesn't spam multiple notifications
- `resolve_notification(dedupe_key)` — marks matching notifications resolved when a case (duplicate-face review, override request, etc.) is decided

**logs/views.py**
- `audit_log_list` — filterable list of all AuditLog entries; restricted to `is_admin` roles (President, Admin, IT); supports filter by action type and username
- `verification_log_list` — list of VerificationAttempt records; admins see all, staff see only their own; supports filter by decision (verified/not verified/manual review/etc.)
- `notification_center`, `notification_open`, `notification_mark_all_read` — the in-app notification list/bell dropdown views

**logs/urls.py**
- `logs/audit/` → `audit_log_list` (name: `logs:audit_logs`)
- `logs/verification/` → `verification_log_list` (name: `logs:verification_logs`)
- `logs/notifications/` → `notification_center` (name: `logs:notification_center`)

**logs/templatetags/fans_filters.py**
- `format_audit_details` template filter — renders a `details` JSONField dict as a human-readable bullet-separated key-value string with cleaned-up labels (e.g. `similarity_score` → "Similarity Score"); handles None, bool, float, and string values safely

### Who can access

- `audit_log_list`: President, Admin, and IT only (`is_admin=True`). Staff are redirected.
- `verification_log_list`: All authenticated users. Admins see all records; Staff see only their own attempts.

### How it connects to the system

- Every app (`accounts`, `beneficiaries`, `verification`) imports `from logs.models import AuditLog` and calls `AuditLog.log(...)` at key decision points
- The Logs menu in `base.html` links to both views; the Audit Logs item is hidden from Staff via `{% if user.is_admin %}`
- `fans/urls.py` includes `logs.urls` at the `/logs/` prefix

---

## How all apps connect

```
accounts/           Provides authentication and authorization
    |
    |-- CustomUser referenced throughout the system
    |-- role properties (is_admin, is_admin_it, is_president) checked inline by views
    |
beneficiaries/      Provides beneficiary records
    |
    |-- Beneficiary model referenced by verification/
    |-- face embedding stored here, encrypted
    |
verification/       Core verification engine
    |
    |-- Reads beneficiary embeddings from beneficiaries/
    |-- Writes VerificationAttempt records
    |-- face_utils.py: FaceNet (keras-facenet + MTCNN)
    |-- liveness.py: anti-spoofing
    |
logs/               Audit trail
    |
    |-- AuditLog.log() called by all other apps
    |-- audit_log_list: filterable log for admins
    |-- verification_log_list: per-staff history
    |
fans/               Ties everything together
    |
    |-- settings.py: configures all apps, reads .env
    |-- urls.py: routes requests to the correct app
    |-- wsgi.py: entry point for Waitress
```

---

## Related folders/files

- `templates/` — all HTML templates for all apps live here
- `static/` — CSS, JavaScript, and images referenced by templates
- `staticfiles/` — production copy of static files (served by WhiteNoise)
- `media/` — runtime-generated files (beneficiary records, documents); face images are **not** stored — only encrypted embeddings are saved to the database
- `.env` — provides all configuration values read by `fans/settings.py`
- `db.sqlite3` — the database where all model data is stored
- `.venv/` — contains Django, Waitress, TensorFlow, keras-facenet, and all dependencies

---

## Summary

The five Django folders form the application core of FANS-C. `fans/` is the project glue. `accounts/` controls who can access what. `beneficiaries/` manages the senior citizen registry. `verification/` runs the FaceNet biometric verification. `logs/` records the full audit trail. Each app is self-contained but connected through Django's standard mechanisms: shared models, inline role checks, and URL includes.
