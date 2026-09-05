# FANS-C Security Checklist

Security review performed: 2026-05-20, branch `2.0`. Updated through Phase 1 (2026-08-27, branch `4.0-Final`), through the "v2.2.0" development milestone (2026-08-29), through the Post-UAT hardening pass (2026-09-02, branch `4.0-Final-v2.1.16-hardening`), through the v2.1.16 Post-UAT Stabilization pass (2026-09-04, items 8.39–8.40), and through the v2.1.16 Final Hardening Patch (2026-09-05, items 8.41–8.44) — see CHANGELOG.md for full version history. The "v2.2.0" milestone label was never packaged as a separate release; that work shipped under the v2.1.x version line (`AppVersion=2.1.16` in `dev/installer/fans_c.iss`) — see README.md's version-numbering note. Active system roles: `president`, `admin`, `it`, `staff`. Legacy roles `head_brgy` and `admin_it` are no longer assignable (migrated via accounts/0006 and accounts/0009). v2.1.13 closes the FaceNet wrong-person / baby-photo / low-quality false-accept window with a three-zone decision band.

**2026-09-02 security-relevant additions (see `docs/POST-UAT-FOLLOWUP-FIX-REPORT.md` for full detail):**
- **Verification session-collision guard (CRITICAL fix).** `verify_start()` now issues a fresh `session_id` per verification attempt; `verify_check_liveness` and `verify_submit` both refuse to evaluate if the client-echoed `session_id` doesn't match the server's current session value. Closes a cross-browser-tab path where a stale tab for Beneficiary A could submit after a second `verify_start(B)` call had overwritten the shared session, previously letting the server silently evaluate against whichever beneficiary currently occupied the session.
- **Duplicate-face conflict now blocks payout at the shared choke point.** `Beneficiary.is_eligible_to_claim` requires `not duplicate_review_required`; a flagged record cannot be verified, claimed, or released via override until the conflict is resolved through the review queue — closing a gap where the generic registration-approval queue had no awareness of an unresolved duplicate-face flag.
- **Null-FK template crash class fixed in 13 templates.** `{{ X.get_full_name|default:X.username }}` without an `{% if X %}` guard raised a hard 500 when `X` (a `SET_NULL` user FK such as `registered_by`, `requested_by`, `performed_by`) was legitimately `None`. All unguarded instances across registration-review, manual-review, duplicate-review, and claims/payout templates were wrapped; already-safe instances were left untouched.

**v2.1.13 security additions:**
- **FaceNet three-zone decision band.** `score >= AUTO_VERIFY_THRESHOLD` (default **0.88**) → VERIFIED. `VERIFICATION_THRESHOLD <= score < AUTO_VERIFY_THRESHOLD` → MANUAL_REVIEW (release BLOCKED). Below → DENIED. Closes the observed 0.82–0.83 baby-photo false-accept window above the 0.75 lower threshold.
- **Low-quality capture forces MANUAL_REVIEW.** `LOW_QUALITY_FORCES_MANUAL_REVIEW=True` (default) downgrades any auto-verify decision to MANUAL_REVIEW when face quality is degraded. Low-quality embeddings produce coincidental high similarities; a human must confirm.
- **PAD landmark-motion gate.** Pixel-level near-duplicate and static-sequence PAD signals are suppressed when the client's FaceMesh confirms head movement (peak yaw or pitch ≥ `PAD_LANDMARK_MOTION_MIN_DEG`, default 2.0°). Eliminates false-rejects of real users while leaving the texture-based PAD gates (glare, flatness, sharpness) fully in force — phone screens and printed photos still get blocked.
- **Result page release-status clarity.** "MANUAL REVIEW — RELEASE BLOCKED" banner, explicit decision-breakdown card (Liveness / Identity match / Release status), score meter tinted by DECISION not by raw-score-vs-threshold. Operators can no longer be misled by a "Liveness PASSED" indicator on a release-blocked attempt.

**v2.1.12 security/usability additions:**
- **Single active President System Role** enforced at the form layer in all five user-management forms (`UserCreateForm`, `UserUpdateForm`, `UserCreateFullForm`, `UserEditFullForm`, `CreateAdminForm`). Inactive/suspended former Presidents remain for audit but do not count toward the active-uniqueness check.
- **Liveness stuck-at-Processing fix.** A frontend `ReferenceError` (`elapsed` used out of scope) prevented the Mode-B proof POST from completing, so no `tx_token` was issued for real users. The proof POST is now wrapped in `try/catch/finally` and the duration variable is hoisted. FaceNet remains the final identity verification engine — this change does not weaken any gate; it just ensures real users can produce a valid proof.
- **Officer Assignment integrated with user create/edit.** Reduces the chance of a "President System Role with no Org Chart entry" misconfiguration; the unique-officer-position gate explicitly blocks assigning a current position that another officer already holds.

**v2.1.11 security additions:**
- Representative claims may never compare against the beneficiary face (Issue 9 fix). Cross-probe denies when live face matches beneficiary on a representative claim.
- Schedules created by Admin are not usable for claiming until the President approves (Issue 5). Closed a path where an Admin could publish a schedule without President oversight.
- PAD signal weights raised; combined flatness+sharpness boost; verify_submit denies even with a valid TX when PAD score crossed the suspicious threshold (Issue 3).
- Static-sequence band widened (< 0.5 px ⇒ 0.6–0.95) so phone-screen flicker scores suspicious.
- New near-duplicate frame detector (perceptual dHash) flags replayed video / held-still photos (weight 0.70, score 0.95 when ≥ 50 % near-dup pairs).
- Repeated payout cancellations require President/Admin override and longer reason after `CANCEL_REPEAT_PRESIDENT_THRESHOLD` (default 3) (Issue 2).
- Different-Person override workflow now records the override request with auditable reason; the new beneficiary remains PENDING until reviewed (Issue 1).
- **Stale token reuse defence**: `verify.js` clears the in-page `livenessToken` immediately on submit dispatch — a network error or aborted request cannot leave a token reusable. Server-side single-use enforcement is unchanged.
- **Structured PAD/liveness audit log**: each verification decision persists `anti_spoof_score`, `liveness_result`, `tx_token_present`, `tx_valid`, `tx_used`, `tx_expired`, `tx_claimant_match`, `challenge_motion_detected`, `static_sequence_detected`, `pa_score`, `pa_flags`, `facenet_score`, `final_decision`, `final_block_reason`, `reference_embedding_source`. Visible in Audit Log → View Details modal.
- **Decision rule** (re-stated): anti-spoof alone, liveness alone, and a valid `tx_token` alone are each **not sufficient** to verify. A `VERIFIED` decision requires liveness gates passing AND FaceNet similarity ≥ threshold. FaceNet is unchanged and remains the identity engine.

This document records the security posture of the system. Items marked [OK] were confirmed during the review. Items marked [ACTION] required or received a fix. Items marked [RISK] are known acceptable risks with mitigations documented.

---

## 0. Installer / Release Build Security

**Root cause of the 2026-05-21 incident:** Running `dist\fans_c\fans_c.exe` on
the build machine before compiling the Inno Setup installer caused runtime data
(`.env`, `db.sqlite3`, certs, logs) to be written into `dist\fans_c\`. Inno's
catch-all `Source: "dist\fans_c\*"; Flags: recursesubdirs` then packaged all of
it. The deployed installer contained the developer's admin credentials, bypassed
the `Create Admin` wizard on every target machine, and shipped private keys.

**Fixes applied 2026-05-21:**
- `fans/settings.py`: `BASE_DIR` now points to the writable exe directory
  (`sys.executable.parent`) when frozen, so `db.sqlite3` and `.env` go next to
  `fans_c.exe` instead of inside read-only `_internal\`.
- `dev/launcher.py`: Removed `.env` mirror to `BUNDLE_DIR` — settings.py now
  finds `.env` directly at `BASE_DIR`.
- `dev/fans_c.spec`: `media\` is never bundled regardless of content.
- `dev/build_exe.ps1`: Steps 5b/5c create a clean staging folder and run a
  payload safety scan that fails the build if any sensitive file is present.
- `dev/installer/fans_c.iss`: Sources from `build\installer-staging\fans_c\`
  (not raw `dist\fans_c\`); `Excludes` added as defense-in-depth.

| # | Check | Status | Notes |
|---|---|---|---|
| 0.1 | Installer does NOT contain `.env` | [OK] | Excluded by staging + safety scan + Inno Excludes |
| 0.2 | Installer does NOT contain `db.sqlite3` | [OK] | Excluded by staging + safety scan + Inno Excludes |
| 0.3 | Installer does NOT contain `fans-cert.pem` | [OK] | Excluded by staging + safety scan |
| 0.4 | Installer does NOT contain `fans-cert-key.pem` | [OK] | Excluded by staging + safety scan |
| 0.5 | Installer does NOT contain `rootCA.pem` | [OK] | Excluded by staging + safety scan |
| 0.6 | Installer does NOT contain `logs\` directory | [OK] | Deleted from staging + Inno Excludes |
| 0.7 | Installer does NOT contain `media\` directory | [OK] | Never bundled by spec; deleted from staging |
| 0.8 | `build_exe.ps1` payload scan passes before Inno runs | [ACTION] | Safety scan added 2026-05-21; scan must print SAFE |
| 0.9 | `Create Admin` appears on fresh install | [ACTION] | Fixed: no `.env` in installer payload |
| 0.10 | Developer credentials do NOT work on fresh install | [ACTION] | Fixed: no `db.sqlite3` in installer payload |
| 0.11 | Inno Setup sources from staging, not dirty dist\ | [ACTION] | Fixed: `fans_c.iss` updated 2026-05-21 |

**Release build procedure (mandatory):**
1. Stop `fans_c.exe` and `caddy.exe`.
2. Delete `dist\` and `build\`.
3. Run `.\dev\build_exe.ps1 -Clean` — must print `SAFE`.
4. Compile Inno **immediately** — do NOT run `dist\fans_c\fans_c.exe` first.
5. Test on a clean PC — `Create Admin` must appear; dev credentials must fail.

See [dev/BUILD.md](../dev/BUILD.md) for the full release checklist.

---

## 1. Secrets and Environment Configuration

| # | Check | Status | Notes |
|---|---|---|---|
| 1.1 | `.env` is in `.gitignore` | [OK] | Confirmed not committed to git |
| 1.2 | No real `SECRET_KEY` committed | [OK] | Only placeholder in `.env.example` |
| 1.3 | No real `EMBEDDING_ENCRYPTION_KEY` committed | [OK] | Only placeholder in `.env.example` |
| 1.4 | No database passwords committed | [OK] | `DB_PASSWORD=your_db_password` is clearly a placeholder |
| 1.5 | No API keys committed | [OK] | `SYNC_API_KEY` is empty in example |
| 1.6 | `SECRET_KEY` placeholder rejected in production | [OK] | `settings.py` raises `RuntimeError` if placeholder is used with `DEBUG=False` |
| 1.7 | `EMBEDDING_ENCRYPTION_KEY` required before face operations | [OK] | `settings.py` raises `RuntimeError` on startup if key is missing/invalid with `DEBUG=False` |
| 1.8 | Private key files (`.pem`, `.key`) not committed | [OK] | Confirmed via `git ls-files` — not tracked |
| 1.9 | Runtime log files not committed | [OK] | Confirmed via `git ls-files` — not tracked |

---

## 2. Django Security Settings

| # | Check | Status | Notes |
|---|---|---|---|
| 2.1 | `DEBUG=False` is the production default | [OK] | `settings.py` defaults `DEBUG=False` |
| 2.2 | `SESSION_COOKIE_HTTPONLY=True` | [OK] | Hardcoded in `settings.py` |
| 2.3 | `CSRF_COOKIE_HTTPONLY=True` | [OK] | Hardcoded in `settings.py` |
| 2.4 | `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` set correctly | [OK] | Dynamic: True when `DEBUG=False`; operator can override via `SECURE_COOKIES=False` for plain-HTTP fallback testing |
| 2.5 | `SESSION_COOKIE_SAMESITE=Lax` | [OK] | Hardcoded |
| 2.6 | `CSRF_COOKIE_SAMESITE=Lax` | [OK] | Hardcoded |
| 2.7 | `SESSION_COOKIE_AGE=8h` (auto-logout) | [OK] | 8-hour idle session expiry |
| 2.8 | `SESSION_EXPIRE_AT_BROWSER_CLOSE=True` | [OK] | Session ends on tab close |
| 2.9 | `X_FRAME_OPTIONS=DENY` | [OK] | Clickjacking protection |
| 2.10 | `SECURE_CONTENT_TYPE_NOSNIFF=True` | [OK] | MIME sniffing protection |
| 2.11 | `SECURE_REFERRER_POLICY=strict-origin-when-cross-origin` | [OK] | Privacy-safe referrer |
| 2.12 | HSTS set by Caddy (not duplicated in Django) | [OK] | `SECURE_HSTS_SECONDS=0` in Django; Caddy sets it via `Caddyfile` header |
| 2.13 | SSL redirect suppressed (handled by Caddy) | [OK] | `security.W008` silenced; Caddy handles HTTP→HTTPS redirect |
| 2.14 | CSRF protection active on all form views | [OK] | `CsrfViewMiddleware` in `MIDDLEWARE`; no unjustified `@csrf_exempt` found |

### 2.15 CSRF Trusted Origins (Action Required for Production)

In `.env`, set:
```
CSRF_TRUSTED_ORIGINS=https://fans-barangay.local
```
Without this, all form POST requests (login, registration, verification) return HTTP 403 when Caddy is fronting Django.

---

## 3. Authentication and Authorization

| # | Check | Status | Notes |
|---|---|---|---|
| 3.1 | Login throttling | [OK] | `accounts/views.py` limits failed attempts (configurable: `LOGIN_MAX_FAILED_ATTEMPTS=8` in `.env`) |
| 3.2 | Strong password policy | [OK] | Min 10 chars, requires letter + digit/symbol via `CharacterClassValidator` |
| 3.3 | `@login_required` on protected views | [OK] — review regularly | All non-public views require authentication |
| 3.4 | Role-based access controls | [OK] — review regularly | Staff roles checked in views; roles: `president`, `admin`, `it`, `staff` |
| 3.5 | Admin-only pages protected | [OK] | User management requires `president`, `admin`, or `it` role |
| 3.6 | Django admin (`/admin/`) restricted | [PARTIALLY MITIGATED, Phase 3A] | Django admin is available; only superusers can access. As of Phase 3A (2026-08-27), `VerificationAttempt`, `FaceEmbedding`, `ClaimRecord`, and `AuditLog` are registered read-only in `admin.py` (no add/change/delete), reducing the blast radius of a compromised superuser session on the most sensitive tables. Other models remain fully editable via `/admin/` for authorized superusers — this is a deliberate operational tradeoff, not an oversight; disabling `/admin/` entirely would remove a tool IT staff rely on for data correction. |
| 3.7 | Django admin cannot be used to modify or delete the President account | [OK] | `accounts/admin.py`'s `CustomUserAdmin` — a President-role user row is read-only (`get_readonly_fields`) and its change/delete views are denied (`has_change_permission`, `has_delete_permission`, `save_model` defense-in-depth) to any acting admin who is not themselves President, including a superuser without the President role; bulk delete is excluded at the queryset level. Separately, `role` is removed from the form's choices entirely for a non-President actor, so `/admin/` cannot be used either to create a new President account or to promote an existing account to President. Password reset via `/admin/` is likewise blocked for a President target when the acting user is not President. |

---

## 4. Face Data and Biometric Security

| # | Check | Status | Notes |
|---|---|---|---|
| 4.1 | Face embeddings encrypted at rest | [OK] | Fernet encryption using `EMBEDDING_ENCRYPTION_KEY` |
| 4.2 | Encryption key not stored in database | [OK] | Key is in `.env` only |
| 4.3 | Media path traversal protection | [OK] | `MEDIA_ROOT` is set; Django's `FileField` uses relative paths. Review upload validators. |
| 4.4 | Upload file extension validated | [REVIEW] | Confirm `beneficiaries/forms.py` and `verification/views.py` validate image extensions and reject non-images |
| 4.5 | Upload file size limited | [REVIEW] | Confirm a reasonable MAX_UPLOAD_SIZE is enforced; Django has no default limit — set `DATA_UPLOAD_MAX_MEMORY_SIZE` if needed |
| 4.6 | Face images not served publicly | [OK] | `fans/views.py::serve_protected_media` — media is NOT served by Django's generic `static()` handler in production; every request is resolved by `_resolve_media_owner()` to the specific `Beneficiary`/`SharedRepresentativeReview`/`CustomUser` record whose `FileField` stores that exact path, then object-level authorization is applied per kind: a `SharedRepresentativeReview` document requires `request.user.is_admin`; a user's own profile picture requires the owner or an admin role; a path matching no tracked record's stored file returns 404 (does not confirm or deny existence). This is real per-object authorization, not merely `@login_required`. |

---

## 5. HTTPS / Network Security

| # | Check | Status | Notes |
|---|---|---|---|
| 5.1 | HTTPS enforced by Caddy | [OK] | Caddy terminates TLS; all traffic to Waitress is loopback HTTP |
| 5.2 | HSTS header set | [OK] | `Strict-Transport-Security: max-age=31536000; includeSubDomains` via Caddyfile |
| 5.3 | `X-Content-Type-Options: nosniff` | [OK] | Set by both Caddy and Django |
| 5.4 | `X-Frame-Options: DENY` | [OK] | Set by both Caddy and Django |
| 5.5 | Referrer-Policy set | [OK] | `strict-origin-when-cross-origin` via Caddy |
| 5.6 | Permissions-Policy (camera only) | [OK] | `camera=(self)` only; mic, geolocation, payment, USB blocked |
| 5.7 | `Cache-Control: no-store` default | [OK] | Prevents LAN proxies from caching biometric response pages |
| 5.8 | Server identity header stripped | [OK] | `-Server` in Caddyfile removes the Caddy version header |
| 5.9 | Waitress port 8000 LAN-accessible? | [RISK] | Waitress binds to `127.0.0.1:8000` (loopback only). Windows Firewall should block 8000 from the LAN. Verify with `netstat -ano | findstr :8000` |
| 5.10 | System exposed to internet? | [RISK] | This system is designed for LAN-only. Do NOT expose port 443 to the internet without a full security review, VPN, or WAF. |
| 5.11 | Dev `runserver` is HTTP-only — never used in production | [OK] | `python manage.py runserver` has no TLS. Camera/liveness requires a secure context (HTTPS, or the browser localhost exemption). Same-laptop dev uses `http://127.0.0.1:8000/`; LAN dev must front `runserver` with Caddy on `https://fans-barangay.local`. Raw LAN HTTP is not supported for camera/liveness. See [DEV-HTTPS.md](DEV-HTTPS.md). The installer flow continues to use Waitress + Caddy — unaffected. |

---

## 6. Error Handling and Information Disclosure

| # | Check | Status | Notes |
|---|---|---|---|
| 6.1 | Custom error pages | [OK] | `templates/errors/400.html`, `403.html`, `404.html`, `500.html` exist |
| 6.2 | `DEBUG=False` hides stack traces | [OK] | Production default |
| 6.3 | Error log doesn't expose secrets | [OK] | `launcher.py` explicitly redacts `SECRET` and `EMBEDDING` keys from `diagnostic.log` |
| 6.4 | Tracebacks not shown to end users | [OK] | All exceptions in `launcher.py` are shown as friendly messageboxes |
| 6.5 | `sys.stderr = None` (PyInstaller windowed) crash vector closed | [FIXED 2.0.2] | All `warnings.warn()` and `print(file=sys.stderr)` calls in `face_utils.py`, `apps.py`, `settings.py`, `views.py` replaced with `logging` module calls that route to `logs\fans_c.log` |
| 6.6 | Invalid base64 in face-capture endpoints returns clean JSON error | [FIXED 2.0.2] | `base64.b64decode()` in all three face-capture POST views wrapped in try/except; no raw exception propagates to the browser |

---

## 8. Verification Security (Added 2026-05-22, v2.0.3)

| # | Check | Status | Notes |
|---|---|---|---|
| 8.1 | `_get_demo_mode()` defaults to strict mode (False) | [FIXED 2.0.3] | Was defaulting `True` → threshold silently dropped to 0.60. Now defaults `False` → threshold stays at 0.75. |
| 8.2 | `_get_liveness_required()` defaults to enforced (True) | [FIXED 2.0.3] | Was defaulting `False` → liveness was non-blocking when setting absent. Now defaults `True`. |
| 8.3 | `SystemConfig.get_threshold()` defaults to strict threshold | [FIXED 2.0.3] | `demo_mode` fallback was `True` → threshold downgrade. Changed to `False`. |
| 8.4 | Anti-spoof threshold rejects printed photos | [FIXED 2.0.3] | Default raised from 0.15 → 0.25. At 0.15, printed photos routinely scored above threshold. |
| 8.5 | Liveness challenge timeout does not auto-pass static images | [FIXED 2.0.3] | 5-second challenge timer no longer sets `challengeCompleted=True` when MediaPipe is available; timeout = fail. Accessibility fallback preserved when MediaPipe cannot load. |
| 8.6 | Phone number fields reject non-numeric input | [FIXED 2.0.3] | All phone/contact fields in UserCreateForm, UserUpdateForm, BeneficiaryInfoForm, BeneficiaryEditForm, RepresentativeForm now enforce PH mobile format. |
| 8.7 | ID number fields reject SQL/special characters | [FIXED 2.0.3] | senior_citizen_id, valid_id_number, rep_id_number validated: alphanumeric + hyphens/spaces/slashes only, max 50 chars. |
| 8.8 | Dashboard "Verifications Today" uses Manila time | [FIXED 2.0.3] | Changed from `timezone.now().date()` (UTC) to `timezone.localdate()` (Asia/Manila) in 7 locations. |
| 8.9 | Auto-approval toggles default OFF | [OK] | All four auto-approval SystemConfig keys default `false` — require explicit admin action to enable. |
| 8.10 | Auto-approval does NOT bypass face verification or liveness | [OK] | Auto-approval only transitions beneficiary `pending → active`. Verification and liveness security are separate pipelines and unaffected. |
| 8.11 | Strict mode blocks challenge auto-accept when head tracking unavailable | [FIXED 2.1.4] | `challengeCompleted = !mpAvailable` (auto-pass on MP failure) changed to `challengeCompleted = !mpAvailable && !LIVENESS_REQUIRED`. In strict mode, tracking unavailable is a challenge fail, not a pass. |
| 8.12 | Hard liveness gate: face matching not called when liveness fails in strict mode | [OK since 2.0.3, confirmed 2.1.4] | `process_face_for_verification` is never called when `server_liveness_passed=False` in strict mode. A correct registered-person face photo displayed on a phone cannot bypass this gate. |
| 8.13 | `anti_spoof_passed` explicit in liveness return dict | [FIXED 2.1.4] | `run_full_liveness_check()` now includes `anti_spoof_passed` in its return dict. Callers no longer rely on a fallback re-computation from the raw score. |
| 8.14 | Verify button fully reset between retry attempts | [FIXED 2.1.4] | `disabled`, `aria-disabled`, and `pointer-events` are all cleared before re-showing the verify button. A stale disabled state from a previous attempt can no longer leave the button permanently unclickable. |
| 8.15 | Active liveness challenge now ALWAYS required for final verification | [CHANGED 2.3.0] | Previously risk-based (skipped for high anti-spoof scores). Now unconditional. A static photo/phone-screen scoring ≥ 0.30 anti-spoof cannot bypass the head-movement gate. FaceNet identity matching only runs after both anti-spoof and challenge pass. |
| 8.16 | Server-side challenge always enforced in verify_submit | [FIXED 2.3.0] | `server_challenge_required = True` always. Previous risk-based logic (`server_anti_spoof_score < 0.30 or rep claim or retry`) could be bypassed by a high-texture phone-screen photo. Now `server_liveness_passed = server_anti_spoof_passed AND challenge_completed` with no exceptions. |
| 8.17 | Registration requires anti-spoof + head movement before face enrollment | [OK since 2.2.0] | `register_submit_face` re-validates anti-spoof and challenge server-side when `REGISTRATION_LIVENESS_REQUIRED=True` (default). Registration is blocked at the server even if the client sends forged `liveness_passed=true`. Phone screens and printed photos cannot be enrolled as face embeddings. |
| 8.18 | Duplicate face: no hard-block, pending review workflow | [OK since 2.2.0] | Duplicate detections at registration save the record as `pending` with `duplicate_review_required=True` instead of hard-blocking. Duplicate records cannot be auto-approved or claim stipends. Admin/head_admin must explicitly approve (twin/lookalike) or reject (fraud) via the duplicate review queue. All decisions are audit-logged. |
| 8.19 | Duplicate review restricted to admin roles | [OK] | `duplicate_review_detail` POST restricts approve/reject actions to `president`, `admin`, and `it` roles. |
| 8.20 | Liveness mismatch technical details not shown in normal UI | [OK since 2.2.2] | `templates/verification/result.html` no longer renders the "Liveness mismatch detected" alert or any raw client/server anti-spoof score. Mismatch details are stored in `attempt.notes` for audit purposes only. |
| 8.21 | Media files (`/media/...`) served correctly with `DEBUG=False` | [OK since 2.2.2] | `fans/urls.py` passes `insecure=True` to `static()`. Verification media (face images) unaffected by profile picture removal. |
| 8.22 | User profile picture upload removed | [CHANGED 2.3.0] | `profile_picture` removed from `UserUpdateForm`. No avatar upload in admin UI. DB column left intact (no risky migration). Beneficiary face registration images are unaffected. |
| 8.23 | Registration liveness is risk-based | [OK since 2.2.1] | Challenge only required when anti-spoof score < 0.30 or face quality is poor. Strong live captures skip challenge. Strict at server level when `REGISTRATION_LIVENESS_REQUIRED=True`. |
| 8.24 | Password requires uppercase letter | [ADDED 2.3.0] | `UppercasePasswordValidator` in `AUTH_PASSWORD_VALIDATORS`. All password creation/change paths enforced. |
| 8.25 | Challenge direction uses 'side' (abs yaw) | [CHANGED 2.3.0] | Replaced left/right/up/down with 'side' challenge: `abs(yawDelta) >= CHALLENGE_THRESHOLD_DEG`. Eliminates mirrored-preview confusion and accidental direction-ambiguity. Static images still fail (cannot move). |
| 8.26 | CHECK_LIVENESS_URL defined on all face capture pages | [FIXED 2.3.0] | `register_rep_face.html` now defines all required JS globals. Defensive guard in `register.js` blocks capture if `CHECK_LIVENESS_URL` is undefined. |
| 8.27 | Unsafe challenge timeout auto-accept removed | [FIXED 2.3.0] | `challengeCompleted = !mpAvailable && !LIVENESS_REQUIRED` removed from `verify.js`. Timeout always fails. If MediaPipe is unavailable, challenge cannot be verified; staff must retry with supported browser. |
| 8.28 | Anti-spoofing is a heuristic — NOT a trained PAD model | [KNOWN RISK] | Current anti-spoof uses texture analysis (Laplacian, LBP proxy, Sobel). A sharp phone-screen photo may still score above threshold. MiniFASNet/Silent-Face-Anti-Spoofing (ONNX) is required for production-grade PAD. This is the highest-priority future security upgrade. See 8.39 for a v2.1.16 calibration fix to one specific false-positive path — it does not change this underlying limitation. |
| 8.29 | Identity embedding uses neutral/frontal frame, not challenge frame | [FIXED 2.1-test1] | FaceNet embedding is now computed from the neutral frame captured before the head-movement challenge. The challenge/proof frame is used for PAD and motion analysis only. Prevents false rejects from angled face captures. |
| 8.30 | TX not issued when embedding fails | [FIXED 2.1-test1] | `verify_check_liveness` blocks TX issuance if `get_embedding_only` returns `success=False` or raises an exception. `debug_stage='embedding_failed'` is returned. No empty-embedding TX is ever created. |
| 8.31 | TX not issued when anti-spoof fails on neutral frame | [FIXED 2.1-test1] | Anti-spoof is re-checked on the neutral/frontal frame before TX issuance. Failure returns `debug_stage='neutral_antispoof_failed'` with no tx_token. |
| 8.32 | TX not issued with fewer than 3 sequence frames | [FIXED 2.1-test1] | Mode B requires ≥ 3 sequence frames. Fewer frames indicate a static photo/screen that cannot produce motion. Returns `debug_stage='insufficient_sequence_frames'`. |
| 8.33 | `verify_submit` does not crash when TX has an embedding | [FIXED 2.1-test1] | `face_result = None` is initialised before the TX embedding path. The quality-note guard `if face_result is not None ...` is always safe. Previously raised `UnboundLocalError` when the TX embedding path was taken. |
| 8.34 | Weak `0.6 * ANTI_SPOOF_THRESHOLD` liveness pass formula removed | [FIXED 2.1-test1] | `verify_submit` now gates strictly on `TX.anti_spoof_score >= ANTI_SPOOF_THRESHOLD AND TX.pa_score < PAD_THRESHOLD`. The old formula that allowed borderline captures to pass via a lower multiplier is gone. |
| 8.35 | `AuditLog.ACTION_USER_CREATE` constant used correctly | [FIXED 2.1-test1] | Three uses of `AuditLog.ACTION_USER_CREATED` (which does not exist) in `accounts/views.py` replaced with `AuditLog.ACTION_USER_CREATE`. Prevents `AttributeError` on user create/edit audit writes. |
| 8.36 | `must_change_password` enforced on login, cleared after change | [FIXED 2.1-test1] | `UserLoginView.form_valid` redirects users with `must_change_password=True` to `accounts:change_password` immediately after login. Flag is cleared after successful password change. Admin password reset sets the flag. |
| 8.37 | `BeneficiaryEditForm` DOB age validation | [FIXED Phase 1B] | `beneficiaries/validators.py` — new shared `validate_senior_citizen_dob()` validator. Rejects future DOBs and ages < 60. Uses a precise birthday comparison (`today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))`) — not the inaccurate `(today - dob).days // 365` floor-division which produces off-by-one errors for leap-year birthdays. Wired into both `BeneficiaryInfoForm.clean_date_of_birth()` (registration — replaces inaccurate formula) and `BeneficiaryEditForm.clean_date_of_birth()` (edit — was missing entirely). Before this fix, an operator could edit a registered beneficiary's date of birth to under 60 and the system would accept it. |
| 8.38 | Override release payout security controls | [ADDED Phase 1C] | `verification/views.py` — new `override_release_payout` view. Security controls: (1) `@login_required` + `is_admin` check; (2) CSRF-protected POST form; (3) same segregation-of-duties rule as `admin_override` — `performed_by` cannot release the payout unless they are President; (4) `is_eligible_to_claim` (STATUS_ACTIVE + consent_given) re-checked at POST time inside the atomic block — beneficiary status may change between override and release; (5) `transaction.atomic()` with `select_for_update()` on both `VerificationAttempt` and `Beneficiary`; (6) three-layer duplicate guard: Python pre-flight → SQL event-level check inside atomic block → DB `UniqueConstraint` on (beneficiary, stipend_event, status='claimed'); (7) `IntegrityError` caught inside atomic block — concurrent race yields a user-facing error, not HTTP 500; (8) Separate `ACTION_CLAIM` audit event with `via='override_release_payout'` — distinct from the existing `ACTION_OVERRIDE` event logged by `admin_override()`. `admin_override()` itself is unchanged. |
| 8.39 | Specular-glare PAD signal recalibrated (false-rejection fix) | [FIXED v2.1.16] | `verification/pad.py::_check_specular_glare` scored isolated bright glare (bright lighting, glasses reflections, overexposed webcam on a *real* face) the same as an actual phone/screen surface, both reaching the high-confidence weight (0.80) and able to single-handedly cross `PHONE_SCREEN_SPOOF_THRESHOLD` (0.40) on their own. Now the high weight requires corroborating `screen_flatness` (a genuine screen/photo is both glary AND unnaturally flat); isolated glare without flatness gets a new lower weight (`specular_glare_uncorroborated=0.35`) that cannot cross the threshold by itself. All other PAD signals (near-duplicate frames, static-sequence, flatness+sharpness combo) are numerically unchanged — this only narrows the isolated-glare false-positive path, it does not weaken detection of an actual screen/photo/replay attack. `PresentationAttackDetector.classify_denial()` also now distinguishes attack-like denials ("Possible presentation attack detected...") from environment-like ones ("Unable to verify liveness due to lighting or camera conditions...") so real users get accurate guidance instead of an "attack" message for a lighting problem. |
| 8.40 | Dashboard "Current Distribution Event" respects the same claim-time-window gate as Verify | [FIXED v2.1.16] | `beneficiaries/views.py` dashboard query previously used `StipendEvent.get_active_event_for_date()` (date-only) while `verification/views.py`'s Verify page used `get_open_events_now()`/`is_within_time_window()` (date + daily `payout_start_time`/`payout_end_time`). An event active for today but outside its daily window showed "OPEN" on the dashboard while Verify correctly refused to start a claim. The dashboard now also calls `is_within_time_window()` and shows a new `SCHEDULED` status (with the opening time) instead of `OPEN` when outside the window — the actual claim gate in Verify was not changed; this only makes the dashboard's display accurate, it does not loosen or bypass any restriction. |
| 8.41 | Production startup fails closed on a "soft" PAD configuration | [ADDED v2.1.16 Final Hardening Patch] | `fans/production_guard.py::collect_production_errors()` — `PAD_REQUIRED=True` alone does not make `verify_check_liveness` deny a suspected presentation attack; the actual gate also requires `STRICT_PRESENTATION_ATTACK_CHECK=True` and `PRESENTATION_ATTACK_REVIEW_OR_DENY='deny'`. Production startup (frozen EXE, or `DEBUG=False`) now refuses to start if `PRESENTATION_ATTACK_REVIEW_OR_DENY != 'deny'`, if `STRICT_PRESENTATION_ATTACK_CHECK=False`, or if `PHONE_SCREEN_SPOOF_THRESHOLD` is below a safe floor (0.10, mirroring the existing `ANTI_SPOOF_THRESHOLD` floor of 0.05) — same fail-closed pattern as the pre-existing `DEMO_MODE`/`LIVENESS_REQUIRED`/`PAD_REQUIRED` checks. See `fans/tests.py::ProductionGuardTest`. |
| 8.42 | Liveness token bound to the session's *current* challenge direction | [ADDED v2.1.16 Final Hardening Patch] | `verification/views.py::verify_submit` — a `LivenessTransaction` was already bound to the issuing `session_id`, stipend event, representative, and operator (8.x context-binding, prior rounds), but not to the challenge direction itself. A face-match retry rotates `session_data['challenge']` to a new direction for the next attempt while keeping the same `session_id`; a token proven against the now-superseded direction previously still passed. `verify_submit` now also rejects when `liveness_tx.challenge_direction` differs from the session's current `challenge` (legacy rows with no recorded direction are exempt, matching the pattern of the other context-binding checks). See `verification/tests.py::LivenessTokenContextBindingAttackTest.test_token_with_superseded_challenge_direction_rejected`. |
| 8.43 | Perceptual-hash replay scan is atomic (reservation + lock) | [FIXED v2.1.16 Final Hardening Patch] | `verification/views.py::check_and_reserve_liveness_evidence()` — the `evidence_phash` duplicate scan (Hamming-distance near-match, not exact equality — it cannot carry a DB unique constraint) previously ran, then the actual `LivenessTransaction` was only created much later (after PAD scoring and embedding computation). Two concurrent requests replaying the same transformed/recompressed evidence could both pass the scan before either had written anything, and both would go on to receive a valid token. A new `LivenessEvidenceReservation` table (migration `0032`) plus an in-process lock (`_liveness_replay_lock`) make the scan-then-reserve step atomic — this app is a single Waitress process (see `dev/launcher.py`), so a process-local lock is sufficient. `evidence_hash`/`evidence_pixel_hash` exact-match checks and the DB `UniqueConstraint`s on them are unchanged. See `verification/tests.py::LivenessReplayReservationConcurrencyTest` and `LivenessReplayLockMutualExclusionTest`. |
| 8.44 | Migration `0031` is upgrade-safe against pre-existing duplicate evidence hashes | [FIXED v2.1.16 Final Hardening Patch] | `verification/migrations/0031_liveness_tx_context_binding.py` adds `UniqueConstraint`s on `evidence_hash`/`evidence_pixel_hash` (scoped to non-blank values) but, before this fix, would fail outright if an installed deployment's database already contained duplicate non-blank values for either field. A new `dedupe_evidence_hashes` data-migration step runs first: for each field, the oldest row (by `created_at`) keeps its value and every later duplicate has only that field blanked — no row is ever deleted. See `verification/tests.py::Migration0031EvidenceHashDedupeTest`. |

---

## 7. Audit Logging

| # | Check | Status | Notes |
|---|---|---|---|
| 7.1 | Audit log model present | [OK] | `logs/models.py` — `AuditLog` records all system actions |
| 7.2 | Login/logout audited | [OK] | Login attempts, successes, lockouts are logged |
| 7.3 | Verification attempts audited | [OK] | Every face verification attempt creates an `AuditLog` entry |
| 7.4 | Admin actions audited | [OK] | User creation, role changes, beneficiary edits are logged |
| 7.5 | Audit logs are read-only to staff | [REVIEW] | Confirm no staff role can delete audit log entries via the UI |

---

## 8. .gitignore Verification

The following sensitive file types are in `.gitignore` and confirmed not committed:

```
.env
*.pem
*.key
*.sqlite3
media/
logs/
.venv/
dist/
build/
staticfiles/   (exception: vendor files are intentionally committed — see note in MAINTENANCE-PLAN.md)
```

---

## 9. Open Items / Remaining Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Waitress port 8000 may be reachable on LAN if Windows Firewall is misconfigured | Medium | Verify: `netstat -ano \| findstr :8000` should show `127.0.0.1:8000` only |
| R2 | `db.sqlite3` file permissions on Windows | Low | Ensure only the service account can read the file. SQLite has no built-in auth. |
| R3 | LAN network is assumed trusted | Medium | This system is designed for a single-building LAN. Any rogue device on the LAN can send requests. Physical access control to the LAN is required. |
| R4 | FaceNet model quality depends on camera quality | Medium | Documented threshold settings allow operator to adjust. Test under deployment lighting conditions. |
| R5 | No rate limiting on verification endpoint | Low-Medium | Login has throttling; verification endpoint should be evaluated for abuse resistance if system is used in high-volume events. |
| R6 | Upload size not explicitly limited in Django settings | Low | `DATA_UPLOAD_MAX_MEMORY_SIZE` is not set (defaults to 2.5 MB). Adequate for face images but review if large files could be submitted. |

---

## 10. Recommendations

1. **Before going live:** Run `.\scripts\admin\verify-installation.ps1` and `.\scripts\admin\run-smoke-tests.ps1` and confirm all checks pass.
2. **After every maintenance window:** Re-run the deployment checklist.
3. **Back up `.env` and `db.sqlite3` off-server** (USB drive stored securely) before any update.
4. **Set Windows Firewall rules** to block TCP port 8000 from all external sources.
5. **Review upload validation** in `beneficiaries/forms.py` and `verification/views.py` to confirm file types and sizes are checked.
6. **Consider disabling Django `/admin/`** if not needed by removing `django.contrib.admin` from `INSTALLED_APPS` or restricting the URL pattern.
