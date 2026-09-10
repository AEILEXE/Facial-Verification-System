# FANS-C Deployment Checklist

**Current version:** v2.1.17 — 2026-09-06 (Final Official Release; development was tracked internally through 2026-08-29 as "v2.1.18" and "v2.2.0 — Analytics, Intelligence, Notification, UX, and Workflow Improvement Release"; that work shipped under the v2.1.x line, not as a separate release — see [README.md](../README.md#latest-release))

Use this checklist before putting the system into production or after any significant update. Work top to bottom. Do not skip items.

---

## Production Fail-Closed Configuration Guard

`fans/settings.py` (via `fans/production_guard.py`) runs an automatic
startup check whenever the app is a frozen/packaged EXE **or** `DEBUG=False`
— this is checked independently, so a misconfigured `DEBUG=True` on a
packaged build does not skip the rest of the checks. If any of the
following are true, **the server refuses to start** and raises a
`RuntimeError` listing every violation (not just the first one) — this is
by design, not a bug to work around by silencing it:

- `DEBUG=True` in a frozen/packaged build
- `DEMO_MODE=True`
- `LIVENESS_REQUIRED=False`
- `PAD_REQUIRED=False`
- `ANTI_SPOOF_THRESHOLD` below `0.05`
- `SAVE_LIVENESS_DEBUG_FRAMES=True`
- `PRESENTATION_ATTACK_REVIEW_OR_DENY` not equal to `deny` (v2.1.16 Final
  Hardening Patch) — a suspected presentation attack must be denied
  outright, not routed to manual review
- `STRICT_PRESENTATION_ATTACK_CHECK=False` (v2.1.16 Final Hardening Patch)
  — this silently turns `PAD_REQUIRED=True` into a no-op
- `PHONE_SCREEN_SPOOF_THRESHOLD` below `0.10` (v2.1.16 Final Hardening
  Patch)
- `ALLOWED_HOSTS` containing `*`
- `SECRET_KEY` left at the placeholder value

If the server won't start after an update, check `logs/fans_c.log` /
`django-errors.log` for a line beginning `[FANS-C] Refusing to start in
production:` — it lists every specific setting that needs to change. See
`docs/SECURITY-CHECKLIST.md` items 8.41 and `fans/tests.py::ProductionGuardTest`
for the full test coverage of this guard.

---

## Pre-Deployment — Code and Config

- [ ] **Branch** is the current source-of-truth branch (`4.0-Final-v2.1.16-hardening` as of 2026-09-02 — verify against `git branch --show-current` and CHANGELOG.md, this checklist is not updated every time the branch name changes) and working tree is clean (`git status`)
- [ ] **Python version** is 3.11 (`py -3.11 --version`)
- [ ] **Virtual environment** exists and is up to date:
  ```powershell
  .\.venv\Scripts\python.exe -m pip install -r requirements.txt
  ```
- [ ] **pip check** passes (no broken or conflicting packages):
  ```powershell
  .\.venv\Scripts\python.exe -m pip check
  ```
- [ ] **Django system check** passes:
  ```powershell
  .\.venv\Scripts\python.exe manage.py check
  ```
- [ ] **Django deploy check** passes (or suppressions are documented):
  ```powershell
  .\.venv\Scripts\python.exe manage.py check --deploy
  ```
- [ ] **No unapplied migrations**:
  ```powershell
  .\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
  ```
- [ ] **Static files collected**:
  ```powershell
  .\.venv\Scripts\python.exe manage.py collectstatic --noinput
  ```
- [ ] **Regression tests pass** (1511 tests across fans/verification/accounts/beneficiaries/logs, 0 failures, 0 errors, 0 skips, as of v2.1.17 Final Official Release, 2026-09-10 — see CHANGELOG.md for the current count; do not treat this number as fixed):
  ```powershell
  .\.venv\Scripts\python.exe manage.py test fans verification accounts logs beneficiaries --verbosity=1
  ```
- [ ] **Smoke tests pass**:
  ```powershell
  .\scripts\admin\run-smoke-tests.ps1
  ```
- [ ] **Verification security defaults confirmed**:
  - `DEMO_MODE` in `.env` is `False` or absent (defaults to strict)
  - `LIVENESS_REQUIRED` in `.env` is `True` or absent (defaults to enforced)
  - `LIVENESS_PROOF_REQUIRED` in `.env` is `True` or absent (TX gate enforced)
  - `ANTI_SPOOF_THRESHOLD` is `0.25` or higher (printed photos blocked)
  - `PAD_REQUIRED` is `True` or absent (presentation attack detection enforced)
  - `LIVENESS_CHALLENGE_TRIGGER_THRESHOLD` is `0.30` or higher (challenge triggers on low-score captures)
  - `REGISTRATION_LIVENESS_REQUIRED` is `True` or absent (registration always checks liveness)
  - `REGISTRATION_CHALLENGE_REQUIRED` is `False` or absent (challenge only for borderline captures, not all)
  - `REQUIRE_LIVENESS_CHALLENGE` is `False` or absent (do not force challenge on all clean attempts)
  - `PRESENTATION_ATTACK_REVIEW_OR_DENY` is `deny` (v2.1.16 Final Hardening Patch — see below)
  - `STRICT_PRESENTATION_ATTACK_CHECK` is `True` or absent (v2.1.16 Final Hardening Patch — see below)
  - `PHONE_SCREEN_SPOOF_THRESHOLD` is `0.40` (default) or higher, and at least `0.10` (v2.1.16 Final Hardening Patch — see below)
- [ ] **LivenessTransaction gate** functional — submit verification; confirm a `tx_token` is issued before the identity comparison, and that the token is consumed after the result
- [ ] **Neutral frame embedding** active — confirm identity comparisons use the frontal frame embedding (check `neutral_tx_embedding=True` in server logs after a successful verification)
- [ ] **Sequence frame gate** active — attempt verification with zero or one sequence frame; confirm `insufficient_sequence_frames` is returned and no tx_token is issued
- [ ] **Duplicate face review queue** is empty or all entries have been reviewed before first production payout (`/duplicate-review/`)
- [ ] **Auto-approval settings** are all OFF by default (check SystemConfig table after first migration)
- [ ] **Liveness mismatch UI** verified: verify that `"Liveness mismatch detected"` banner does NOT appear in the operator-facing result page (technical details should be in audit log only)
- [ ] **Profile picture upload confirmed absent** — `UserUpdateForm` must not include a `profile_picture` field; no avatar upload UI in Admin → User Management
- [ ] **Verification crash regression** — complete a full verification with a valid face + liveness challenge; confirm no HTTP 500 errors in the server log
- [ ] **must_change_password flow** — after an admin password reset, confirm the affected account is redirected to Change Password on next login
- [ ] **User Management routes** — confirm Admin → User Management loads at `/accounts/users/` and all create/edit/reset actions work without redirect errors

---

## .env Configuration

- [ ] `.env` file exists in project root
- [ ] `SECRET_KEY` is set to a long random value (not the placeholder)
- [ ] `EMBEDDING_ENCRYPTION_KEY` is set to a valid Fernet key
  - Generate: `python manage.py generate_key`
- [ ] `DEBUG=False`
- [ ] `ALLOWED_HOSTS` includes:
  - `fans-barangay.local`
  - Server LAN IP (e.g., `192.168.1.77`)
  - `localhost`
  - `127.0.0.1`
  - Example: `ALLOWED_HOSTS=fans-barangay.local,192.168.1.77,localhost,127.0.0.1`
- [ ] `CSRF_TRUSTED_ORIGINS` includes `https://fans-barangay.local`
  - Example: `CSRF_TRUSTED_ORIGINS=https://fans-barangay.local`
- [ ] `SECURE_PROXY_SSL_HEADER=HTTP_X_FORWARDED_PROTO,https`
- [ ] `USE_X_FORWARDED_HOST=True`
- [ ] `USE_SQLITE=True` (for standalone/demo) or `USE_SQLITE=False` with correct `DB_*` vars
- [ ] `DEMO_MODE=False` for full production (or `True` during assisted rollout)
- [ ] `LIVENESS_REQUIRED=True` for full production
- [ ] Email OTP password recovery (optional — decide per site):
  - If enabling: `EMAIL_HOST`, `EMAIL_PORT` (`587`), `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` (a Gmail **App Password**, never the normal account password, if using Gmail), `EMAIL_USE_TLS=True`, `DEFAULT_FROM_EMAIL` are all set — either via the first-run installer wizard's "Email OTP Configuration" step or by hand-editing `.env` (requires a service restart to take effect)
  - If leaving disabled (no internet access, or intentionally admin-only recovery): leave `EMAIL_HOST`/`EMAIL_HOST_USER` empty — the system falls back to admin-assisted recovery with no error shown to end users; this is intentional (see `docs/PASSWORD-RECOVERY-ARCHITECTURE.md`)

---

## TLS / Certificate Setup

- [ ] `fans-cert.pem` exists in project root
- [ ] `fans-cert-key.pem` exists in project root
- [ ] Certificate covers `fans-barangay.local` and the server LAN IP
- [ ] Certificate is not expired:
  ```powershell
  & tools\mkcert\mkcert.exe -CAROOT
  ```
- [ ] `CLIENT-SETUP/rootCA.pem` is the current CA root (for client trust distribution)
- [ ] `trust-local-cert.bat` tested on at least one client device
- [ ] Server hosts file has `fans-barangay.local` entry:
  ```powershell
  .\scripts\admin\repair-hosts.ps1
  ```

---

## Services and Auto-Start

- [ ] `tools/caddy.exe` exists (downloaded from caddyserver.com)
- [ ] `tools/mkcert/mkcert.exe` exists (downloaded from github.com/FiloSottile/mkcert)
- [ ] Task Scheduler task "FANS-C Verification System" is registered and enabled
- [ ] Task Scheduler task "FANS-C Watchdog" is registered and enabled
- [ ] Live startup test passes:
  ```powershell
  .\scripts\admin\check-system-health.ps1
  ```

---

## Database

- [ ] `db.sqlite3` exists (or PostgreSQL connection is verified)
- [ ] All migrations applied:
  ```powershell
  .\.venv\Scripts\python.exe manage.py showmigrations
  ```
- [ ] At least one admin account exists:
  ```powershell
  .\.venv\Scripts\python.exe manage.py shell -c "from accounts.models import CustomUser; print(CustomUser.objects.filter(is_active=True).count(), 'users')"
  ```
- [ ] **Database is backed up** before any production update:
  ```powershell
  Copy-Item db.sqlite3 "db.sqlite3.backup-$(Get-Date -Format 'yyyyMMdd-HHmm')"
  ```
- [ ] **`.env` is backed up** before any update (contains encryption keys):
  ```powershell
  Copy-Item .env ".env.backup-$(Get-Date -Format 'yyyyMMdd-HHmm')"
  ```

---

## Browser Access Test

Run from at least one **client device** (not the server itself):

- [ ] Open browser to `https://fans-barangay.local`
- [ ] No certificate warning appears
- [ ] Login page loads correctly with styles (CSS working)
- [ ] Camera access is granted when verification page is opened (HTTPS required)
- [ ] Login succeeds with a valid admin account
- [ ] Dashboard loads and displays data

---

## Security Checklist

- [ ] Full security checklist reviewed: [SECURITY-CHECKLIST.md](SECURITY-CHECKLIST.md)
- [ ] No real secrets in any committed file
- [ ] `.env` is not world-readable (Windows: check file permissions)
- [ ] `db.sqlite3` is not world-readable
- [ ] `fans-cert-key.pem` is not world-readable
- [ ] Firewall allows only necessary ports:
  - Port 443 (HTTPS, from LAN)
  - Port 8000 (internal loopback only — Caddy to Waitress)
  - Block port 8000 from external access

---

## Post-Deployment Verification

- [ ] `.\scripts\admin\verify-installation.ps1` reports all PASS
- [ ] `.\scripts\admin\check-system-health.ps1` reports all OK
- [ ] Reboot server and confirm auto-start works
- [ ] Confirm watchdog starts ~150 seconds after boot
- [ ] Check `logs/fans-startup.log` for any errors
- [ ] Check `logs/fans-watchdog.log` for any [FAIL] or [ALERT] entries

---

## Clean-PC Functional QA

Run on a freshly installed machine or after each build release.

- [ ] Install completes without error; **Create Admin** screen appears on first launch
- [ ] Developer credentials from build machine **do not work** (dev database not bundled)
- [ ] Real person registers successfully (embedding saved, no liveness block)
- [ ] Phone/screen registration attempt is **rejected** (anti-spoof or challenge gate)
- [ ] Real person verification passes (face match + liveness both pass; head-movement challenge completed)
- [ ] Phone/screen verification attempt is **denied** at liveness gate (before face match runs)
- [ ] Real person verification **always** requires head-movement challenge — no fast path for high anti-spoof score
- [ ] **Neutral frame embedding active** — server log shows `neutral_tx_embedding=True` for a successful verification
- [ ] **TX token issued** — browser receives `tx_token` after liveness challenge; verify_submit consumes it
- [ ] **Sequence frame gate** — attempt with zero motion frames returns a denial (no tx_token); operator sees retry message
- [ ] Technical liveness mismatch banner is **not shown** — no raw score text visible to operators
- [ ] User Management edit form has **no** profile picture upload field
- [ ] **User Management route** — Admin → User Management at `/accounts/users/`; create/edit/reset all functional
- [ ] **Admin password reset** → sets `must_change_password`; user redirected to Change Password on next login
- [ ] MediaPipe FaceMesh loads without 404 — `face_mesh.binarypb` included in bundle
- [ ] Browser console shows `baseYaw=<number>` (not `n/a`) after 10 stable frames
- [ ] Liveness challenge debug log shows `[FANS-C Challenge] completed` with `absYawDelta ≥ 5`
- [ ] Navbar user badge shows person-icon for all users (no profile photo)
- [ ] Password without uppercase letter is **rejected** at create/reset
- [ ] Duplicate face registration goes to review queue (not blocked, not auto-approved)
- [ ] Duplicate review: approve activates record; reject leaves it inactive; both are audit-logged
- [ ] HTTPS camera verification works on `https://fans-barangay.local`
- [ ] Auto-start confirmed after reboot (system up within 60 seconds)
- [ ] **Phase 1B — DOB validation:** Edit a beneficiary's date of birth to a date that would make them under 60 years old; confirm the form rejects it with a validation error and does not save
- [ ] **Phase 1B — DOB validation:** Edit a beneficiary's date of birth to a date that makes them exactly 60 years old (today minus 60 years); confirm the form accepts it
- [ ] **Phase 1C — Override release payout button:** After an admin performs `admin_override` setting decision to VERIFIED on an attempt that has a stipend event set, confirm the "Release Payout" button appears on the result page (visible only to admins; not visible when a claim record already exists)
- [ ] **Phase 1C — Release payout creates ClaimRecord:** Click "Release Payout", confirm the GET confirmation page renders, submit the POST; confirm a ClaimRecord is created, a reference number is shown, and the "Release Payout" button disappears
- [ ] **Phase 1C — Duplicate release blocked:** Attempt to reload the release-payout POST on the same attempt; confirm the duplicate is rejected and no second ClaimRecord is created
- [ ] **Phase 1C — ACTION_CLAIM audit event:** After a successful override release, confirm an `ACTION_CLAIM` AuditLog entry exists with `via='override_release_payout'` in its details

---

## Verification Troubleshooting Quick Reference

| Symptom | Likely cause | Action |
|---|---|---|
| "Verification error" / HTTP 500 | Server crash in verify_submit | Check `logs/django-errors.log`; ensure v2.1.8+ is installed |
| "Face not detected" | Poor lighting, occlusion, or camera issue | Move to better light; face camera directly; remove glasses/mask |
| "Live face check failed — anti-spoof" | Phone/screen/photo detected, or very dark frame | Use real face; good lighting; no glare |
| "Insufficient sequence frames" | No head movement captured or camera too slow | Turn head clearly during challenge; stay 30–50 cm from camera |
| "Liveness failed" / denied before face match | Anti-spoof, PAD, or challenge gate | Stay 30–50 cm from camera; good lighting; turn head visibly |
| "Manual review — lookalike" | Another beneficiary matched within safety band | Staff must confirm identity with valid ID before releasing stipend |
| "Not verified — score too low" | Face mismatch or poor embedding quality | Confirm correct beneficiary; re-register face if quality is consistently poor |
| Challenge never starts | MediaPipe not loaded (404 on .binarypb) | Confirm bundle includes `face_mesh.binarypb`; HTTPS required |
| tx_token not issued | Any liveness gate failure | Check `debug_stage` in browser console; address the specific gate (anti-spoof / frames / embedding) |

### Verification retry guidance for operators

Give this guidance to staff when a beneficiary must retry:

1. Face the camera directly — look straight ahead for the neutral capture.
2. Stay **30–50 cm** from the camera lens.
3. Use **good, even lighting** — avoid backlighting or strong overhead glare.
4. **Remove glasses** if anti-spoof consistently fails.
5. When the challenge prompt appears, **turn your head clearly** to one side and then back.
6. Only the **real person** should be in front of the camera — no photos or phone screens.

---

## Notes for This Deployment

_Fill in before each deployment:_

- Date: _______________
- Server hostname/IP: _______________
- Django admin username: _______________
- Backup location (`.env` + `db.sqlite3`): _______________
- Performed by: _______________

---

## Dev vs Installer: HTTPS / camera scope

This checklist is for **installer deployments** (Waitress + Caddy + the
shipped mkcert root CA). On a deployed machine, camera/liveness works
because Caddy serves `https://fans-barangay.local` with a locally-trusted
certificate.

If you are verifying a **source / `runserver`** environment instead, see
[DEV-HTTPS.md](DEV-HTTPS.md). Key facts to remember during a checklist
walkthrough:

- `python manage.py runserver` is HTTP-only.
- `http://127.0.0.1:8000/` and `http://localhost:8000/` are valid for
  same-laptop dev (browsers exempt localhost from the secure-context
  rule).
- `http://192.168.x.x:8000` and any other LAN HTTP URL are **not
  supported** for the camera/liveness flow — devs must front `runserver`
  with Caddy on `https://fans-barangay.local`.
