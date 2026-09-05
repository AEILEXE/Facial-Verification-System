# FANS-C Final Manual QA Checklist

**Purpose:** Step-by-step manual test script for the final human testing pass before release. This is a *procedure*, not a policy document — for security posture and pre-deployment config verification, see [SECURITY-CHECKLIST.md](SECURITY-CHECKLIST.md) and [DEPLOYMENT-CHECKLIST.md](DEPLOYMENT-CHECKLIST.md).

**Automated status going into this pass:** 740 tests passing, `manage.py check` clean, no unapplied migrations (verified 2026-08-30).

**How to use this document:**
- Work top to bottom, section by section. Each item has a Test Objective, Steps, Expected Result, and a Pass/Fail checkbox.
- Run on the actual installed EXE (Waitress + Caddy) over `https://fans-barangay.local`, not `runserver`, whenever camera/liveness is involved — see the "Dev vs Installer" note in DEPLOYMENT-CHECKLIST.md.
- Record the tester name, date, and build SHA-256 at the top of your filled-in copy.
- Any FAIL blocks release until re-tested and passed.

```
Tester: _______________   Date: _______________   Build/commit: _______________
```

---

## A. Authentication

### A1. President login
- **Objective:** Confirm a President-role account can log in and reaches the full-privilege dashboard.
- **Steps:**
  1. Navigate to `https://fans-barangay.local/accounts/login/`.
  2. Enter valid President credentials and submit.
  3. Observe the dashboard and the navbar menu.
- **Expected result:** Login succeeds; dashboard loads; navbar shows all admin sections (Reports, Analytics, User Management, Officer Assignment, Org Chart, Password Reset Requests, System Config, Audit Log). President-only actions (e.g. approving a payout schedule, `admin_override`, `override_release_payout`) are available.
- [ ] Pass  [ ] Fail

### A2. Admin login
- **Objective:** Confirm an Admin-role account logs in with admin-level but not President-exclusive access.
- **Steps:**
  1. Log in with a valid Admin account.
  2. Open User Management, Reports, and Manual Review.
  3. Attempt an action reserved for President only (e.g. approving a stipend schedule awaiting President sign-off, or releasing an override payout as a non-President `performed_by`).
- **Expected result:** Admin reaches dashboard and admin sections normally. President-exclusive actions are blocked or hidden with a clear message (segregation-of-duties: `performed_by` must be President for override release).
- [ ] Pass  [ ] Fail

### A3. IT login
- **Objective:** Confirm an IT-role account has the access IT needs (user/officer management, system health) without unrelated business privileges being missing.
- **Steps:**
  1. Log in with a valid IT account.
  2. Open User Management, Officer Assignment, System Health, System Connection.
  3. Attempt Manual Review / duplicate face review actions (IT is permitted per role matrix — confirm against `templates/system/role_matrix.html`).
- **Expected result:** IT reaches all pages the role matrix grants; no server error; no unrelated page is silently broken.
- [ ] Pass  [ ] Fail

### A4. Staff login
- **Objective:** Confirm a Staff-role account is restricted to front-line operations only.
- **Steps:**
  1. Log in with a valid Staff account.
  2. Confirm the navbar does NOT show User Management, Officer Assignment, Org Chart, Analytics, Audit Log, System Config.
  3. Attempt to browse directly to an admin URL (e.g. `/accounts/users/`) while logged in as Staff.
- **Expected result:** Staff can register beneficiaries, capture faces, and run verification/claims. Admin-only URLs redirect with "Admin access required" rather than rendering.
- [ ] Pass  [ ] Fail

### A5. Login lockout
- **Objective:** Confirm brute-force protection works and recovers correctly.
- **Steps:**
  1. Attempt login with a valid username and a wrong password 8 times in a row (default `LOGIN_MAX_FAILED_ATTEMPTS=8`) within 10 minutes.
  2. Attempt a 9th login, even with the correct password.
  3. Check that an admin received a Security Alert notification.
  4. Wait for the lockout window to pass (default 15 minutes) or use a fresh test account, then log in correctly.
- **Expected result:** The 9th attempt is rejected with a lockout message even though credentials are now correct. A Security Alert notification/audit entry is created at the moment of lockout. After the lockout duration, login with correct credentials succeeds.
- [ ] Pass  [ ] Fail

### A6. Password reset request flow
- **Objective:** Confirm the self-service "forgot password" request and admin-side approval work end to end.
- **Steps:**
  1. From the login page, submit a password reset request for a valid username.
  2. Log in as President/Admin/IT, open Password Reset Requests.
  3. Approve (or reject) the request.
  4. Confirm the affected user can log in with the new/reset credential and is forced through Change Password (`must_change_password`).
- **Expected result:** Request appears in the queue; approve/reject both work and are audit-logged; the affected user is redirected to Change Password on next login after an admin reset.
- [ ] Pass  [ ] Fail

---

## B. Beneficiary Workflow

### B1. Create beneficiary
- **Objective:** Confirm the 3-step registration wizard completes and produces a correctly-linked record.
- **Steps:**
  1. Dashboard → Register New Beneficiary → Step 1 (personal info) → Step 2 → Step 3.
  2. Fill all required fields realistically (name, DOB making the person ≥ 60, address, contact).
  3. Leave Senior Citizen ID blank on one test record, and filled on another.
- **Expected result:** All three steps save without error; the record lands in Pending/Active per auto-approval settings. On the blank-SC-ID record, the beneficiary detail page shows "Not provided — add it" with a working link to Edit (not a crash) — this was a bug fixed during this QA pass, re-verify it here.
- [ ] Pass  [ ] Fail

### B2. Capture face
- **Objective:** Confirm face registration with liveness succeeds for a real person.
- **Steps:**
  1. From beneficiary detail (or the registration wizard), open Register Face.
  2. Complete the anti-spoof + head-movement challenge if triggered (registration liveness is risk-based; low-confidence captures trigger the challenge).
  3. Submit.
- **Expected result:** Embedding saves; no liveness block for a genuine live face; beneficiary record now shows a registered face.
- [ ] Pass  [ ] Fail

### B3. Duplicate face attempt
- **Objective:** Confirm a face that closely matches an already-registered beneficiary is routed to the duplicate review queue, not silently accepted or hard-blocked.
- **Steps:**
  1. Register a second beneficiary record using the same live person's face (or a very similar face — e.g. a twin/lookalike photo used in test data).
  2. Save the registration.
  3. Log in as President/Admin/IT and open Duplicate Review (`beneficiaries:duplicate_review_list`).
  4. Open the flagged entry, review the side-by-side match, then Approve (confirms real twin/lookalike) and separately test Reject (confirms fraud/duplicate) on two different test records.
- **Expected result:** The new registration is not hard-blocked; it lands in the review queue as `pending` with `duplicate_review_required=True`. Approve activates the record; Reject leaves it inactive. Both actions are audit-logged. Only president/admin/it can act on this queue.
- [ ] Pass  [ ] Fail

### B4. Invalid camera position
- **Objective:** Confirm poor-quality or badly-framed captures are rejected with a clear, actionable message rather than silently accepted or crashing.
- **Steps:**
  1. Attempt a face capture with the subject too far from the camera (small face / large inter-eye distance shortfall).
  2. Attempt a capture with the face partially out of frame or at a steep angle.
  3. Attempt a capture in very poor lighting.
- **Expected result:** Each case is rejected with a specific, human-readable reason (e.g. "Face too small", quality/lighting guidance) — no HTTP 500, no silent pass-through to a low-quality embedding. Low-quality captures that do get through are routed to MANUAL_REVIEW rather than auto-verified later (`LOW_QUALITY_FORCES_MANUAL_REVIEW`).
- [ ] Pass  [ ] Fail

### B5. Edit / deactivate / reactivate beneficiary
- **Objective:** Confirm the record lifecycle actions work and are constrained correctly.
- **Steps:**
  1. Edit an existing beneficiary's details (change address/contact).
  2. Attempt to edit DOB to make the person under 60 — confirm rejection.
  3. Deactivate the beneficiary (with reason), then reactivate.
- **Expected result:** Edits save; under-60 DOB is rejected with a validation error; deactivate/reactivate both work and are audit-logged.
- [ ] Pass  [ ] Fail

---

## C. Face Verification Workflow

### C1. Successful verification
- **Objective:** Confirm a genuine, correctly-matched beneficiary verifies end to end and is releasable.
- **Steps:**
  1. Dashboard → Verify → select/search the beneficiary → Start Verification.
  2. Capture the neutral frame, then complete the head-movement challenge when prompted.
  3. Submit.
- **Expected result:** Liveness passes (anti-spoof + challenge), FaceNet similarity is at/above `AUTO_VERIFY_THRESHOLD` (0.88 default) for a clean auto-verify, result page shows VERIFIED with no release block. If a stipend event is active, the claim proceeds; browser console / server log shows `tx_token` issued and consumed, `neutral_tx_embedding=True`.
- [ ] Pass  [ ] Fail

### C2. Failed verification
- **Objective:** Confirm a genuine mismatch (wrong person) or a spoof attempt is correctly denied.
- **Steps:**
  1. Attempt verification against the wrong beneficiary record (correct live person, wrong target record).
  2. Separately, attempt verification with a printed photo or phone-screen image of the beneficiary.
- **Expected result:** Wrong-person case: NOT_VERIFIED, denied, no claim created. Spoof case: denied at the liveness/PAD gate *before* FaceNet identity matching runs — result page does not show raw anti-spoof scores or a "Liveness mismatch detected" banner to the operator (technical detail is audit-log only).
- [ ] Pass  [ ] Fail

### C3. Manual review (lookalike / low-quality / mid-band score)
- **Objective:** Confirm the three-zone decision band correctly routes borderline scores to a human reviewer instead of auto-verifying or auto-denying.
- **Steps:**
  1. Produce a verification attempt that scores between `VERIFICATION_THRESHOLD` (0.75) and `AUTO_VERIFY_THRESHOLD` (0.88) — e.g. a lookalike/family-member face, or a deliberately lower-quality capture of the correct person.
  2. Observe the result page.
  3. Log in as President/Admin/IT, open Manual Review, locate the attempt, and approve or deny it with a reason.
- **Expected result:** Result page shows "MANUAL REVIEW — RELEASE BLOCKED" with the decision-breakdown card (Liveness / Identity match / Release status) — the score meter is tinted by decision, not raw score. No payout is releasable until a reviewer explicitly approves. Reviewer action is audit-logged.
- [ ] Pass  [ ] Fail

---

## D. Representative Workflow

### D1. Register a representative for a beneficiary
- **Objective:** Confirm a beneficiary can have an authorized representative registered with their own face data.
- **Steps:**
  1. Open a beneficiary's detail page → Register Representative Face.
  2. Enter representative details (name, relationship, ID).
  3. Capture the representative's face (liveness challenge is always required for representative face registration/claims).
- **Expected result:** Representative record saves and links to the beneficiary; representative face embedding is distinct from the beneficiary's.
- [ ] Pass  [ ] Fail

### D2. Representative claims on behalf of a beneficiary
- **Objective:** Confirm a representative can verify and claim, and that the system cannot be tricked into matching the representative against the beneficiary's own face.
- **Steps:**
  1. Start a verification as "claim via representative" for the beneficiary.
  2. Complete the representative's liveness challenge and face capture.
  3. Separately, attempt to submit the *beneficiary's own* face on a representative claim.
- **Expected result:** Genuine representative claim succeeds against the representative's registered embedding. Beneficiary's own face on a representative-claim path is denied (representative claims must never cross-match the beneficiary).
- [ ] Pass  [ ] Fail

### D3. Shared representative review queue
- **Objective:** Confirm cases needing admin oversight of representative claims are queued and actionable.
- **Steps:**
  1. Trigger a condition that lands in Shared Representative Review (e.g. a representative shared across multiple beneficiaries, or a flagged rep claim).
  2. Log in as President/Admin/IT, open Shared Rep Review, inspect and resolve an entry.
- **Expected result:** Queue lists the flagged item with enough context to decide; resolving it is audit-logged and removes it from the pending queue.
- [ ] Pass  [ ] Fail

### D4. Deactivate a representative
- **Objective:** Confirm a representative can be removed from a beneficiary without deleting history.
- **Steps:**
  1. From beneficiary detail, deactivate an existing representative.
  2. Confirm the representative can no longer claim on that beneficiary's behalf.
- **Expected result:** Deactivation succeeds; past claims remain in history; deactivated representative cannot start a new claim.
- [ ] Pass  [ ] Fail

---

## E. Payout Workflow

### E1. Create and approve a stipend event
- **Objective:** Confirm the schedule → approval → claimable lifecycle works.
- **Steps:**
  1. Log in as Admin, create a new stipend event (title, date/time window, amount).
  2. Log out, log in as President, open Stipend Schedules, approve the pending event.
  3. Confirm the event is now claimable within its office-hours window.
- **Expected result:** Admin-created schedules are not usable for claiming until President approves. After approval, the event appears as active/claimable during its configured window and is rejected outside it (past-date, outside 07:00–20:00 office hours, etc.).
- [ ] Pass  [ ] Fail

### E2. Rejection
- **Objective:** Confirm a President can reject a proposed stipend event, and that a claim itself can be rejected.
- **Steps:**
  1. Create a second test stipend event as Admin; reject it as President with a reason.
  2. Separately, produce a claim that lands in Pending Claim Review (recorded when no active event existed) and reject it with review notes.
- **Expected result:** Rejected event never becomes claimable. Rejected claim is marked accordingly, is audit-logged, and does not release a payout.
- [ ] Pass  [ ] Fail

### E3. Release / override / cancel a payout
- **Objective:** Confirm the post-release lifecycle actions (cancel, fail, override) work with the required controls.
- **Steps:**
  1. Complete a successful verification with an active stipend event to auto-create a claim, OR use "Release Payout" after an `admin_override` on a VERIFIED attempt.
  2. As Admin, open the payout detail and attempt Cancel with no reason — confirm it's rejected.
  3. Retry Cancel with a written reason.
  4. Confirm a duplicate release/claim attempt on the same attempt is blocked (no second ClaimRecord).
- **Expected result:** A written reason is mandatory for cancel/fail/override. Release Payout is visible only to admins and only when no claim record already exists. Duplicate release attempts are rejected. Every action produces an `ACTION_CLAIM`/`ACTION_OVERRIDE` audit entry.
- [ ] Pass  [ ] Fail

### E4. Reminders
- **Objective:** Confirm stale pending approvals surface a reminder notification instead of aging silently.
- **Steps:**
  1. Create a claim/face-update/special-claim/name-DOB override/stipend-schedule/password-reset request and leave it pending for 48+ hours (or adjust a test record's `created_at` backward in a non-prod DB for testing purposes).
  2. Load the Manual Review page as an admin (reminders are synced on page load via `sync_approval_reminders`).
  3. Check the notification bell.
- **Expected result:** A one-time "Approval Reminder" notification (category `CATEGORY_APPROVAL_REMINDER`, Medium priority) appears for the still-pending item, deduplicated so it does not repeat on every page load.
- [ ] Pass  [ ] Fail

---

## F. Notifications

### F1. Notification bell and center
- **Objective:** Confirm real-time-feeling notification delivery to the right roles.
- **Steps:**
  1. Trigger events that generate each of the 7 categories where feasible: Approval Required, Approval Reminder, Verification Review, Fraud Alert, Security Alert, Password Reset Request, System Alert.
  2. Check the navbar bell (unread count + recent list) as the intended recipient role.
  3. Open the full Notification Center.
- **Expected result:** Bell shows an accurate unread count; clicking a notification opens/navigates to the relevant record (`notification_open`) and marks it read; "Mark all read" clears the badge.
- [ ] Pass  [ ] Fail

### F2. Notification priority and targeting
- **Objective:** Confirm notifications reach the correct roles and priority is visually distinguishable.
- **Steps:**
  1. Generate a High-priority event (e.g. Fraud Alert or Security Alert from a lockout) and a Low/Medium one.
  2. Confirm only the intended roles (e.g. admins, not Staff) receive admin-facing categories.
- **Expected result:** Priority (Low/Medium/High) is visually distinct; role targeting matches the category (e.g. password reset requests go to admin roles, not Staff).
- [ ] Pass  [ ] Fail

---

## G. Analytics Dashboards

### G1. Executive analytics
- **Objective:** Confirm the executive dashboard loads and reflects real data.
- **Steps:**
  1. Log in as President/Admin, open Analytics → Executive.
  2. Cross-check one chart's figures (e.g. total verified today) against a manual count from Verification Logs.
- **Expected result:** Page loads without error; figures are plausible and match a manual spot-check; charts render (no blank/broken chart widgets).
- [ ] Pass  [ ] Fail

### G2. Operational analytics
- **Objective:** Confirm the operational dashboard (throughput/queue-focused) loads and updates.
- **Steps:**
  1. Open Analytics → Operational.
  2. Perform one new verification, then refresh the dashboard.
- **Expected result:** New activity reflects in the relevant metric after refresh; no stale-forever counters.
- [ ] Pass  [ ] Fail

### G3. Security / fraud analytics
- **Objective:** Confirm fraud scoring and the security analytics view surface real signals.
- **Steps:**
  1. Open Analytics → Security (or Fraud Signals Report directly).
  2. Trigger a signal (e.g. repeated failed verifications for one beneficiary above `FRAUD_REPEATED_FAILURE_THRESHOLD`, or a login-failure burst).
  3. Confirm the signal appears with its LOW/MED/HIGH risk classification.
- **Expected result:** Signal appears within the configured window; risk level matches the documented 0–100/LOW-MED-HIGH scale; page links through to Audit Log / Suspicious Attempts detail correctly.
- [ ] Pass  [ ] Fail

### G4. Report exports
- **Objective:** Confirm report/analytics exports (PDF/CSV where offered) produce correct, non-empty output.
- **Steps:**
  1. From Report: Claims, Event Summary, Staff Distribution Activity, or Template Match, run an export.
  2. Open the exported file.
- **Expected result:** File downloads successfully, opens without corruption, and contains data matching the on-screen report for the same filter/date range.
- [ ] Pass  [ ] Fail

---

## H. Backup and Restore

### H1. Manual/scheduled backup runs successfully
- **Objective:** Confirm `scripts\admin\daily-backup.ps1` produces a usable backup.
- **Steps:**
  1. Run `.\scripts\admin\daily-backup.ps1` manually (or confirm the Task Scheduler entry fired).
  2. Inspect the `backups\<timestamp>\` folder and its manifest.
  3. Run `.\scripts\admin\verify-backup.ps1` against the new backup.
- **Expected result:** Backup folder contains `db.sqlite3` (or DB dump) + `.env` + media as configured, with a manifest file. Verify script reports the backup as valid/restore-ready.
- [ ] Pass  [ ] Fail

### H2. Backup status visible in-app
- **Objective:** Confirm backup health is visible to IT/Admin without shelling into the server.
- **Steps:**
  1. Run `python manage.py sync_backup_audit`.
  2. Open System Health page and Audit Log; look for `ACTION_BACKUP_COMPLETED` / `ACTION_BACKUP_INCOMPLETE` entries.
- **Expected result:** Latest backup status is reflected in System Health and mirrored into the Audit Log exactly once per backup (idempotent re-run does not duplicate entries).
- [ ] Pass  [ ] Fail

### H3. Restore drill
- **Objective:** Confirm a backup can actually be restored — the only real test of a backup.
- **Steps:**
  1. On a **non-production** copy of the install (or a scratch VM/folder), run `.\scripts\admin\restore-backup.ps1` pointing at a known-good backup folder.
  2. Start the app against the restored data.
  3. Log in and confirm beneficiary/user data matches what was in the backup.
- **Expected result:** Restore completes without manual file surgery; app starts; data matches the backup snapshot; face verification still works post-restore (encryption key carried over correctly).
- [ ] Pass  [ ] Fail

---

## I. Security Checks

> These are spot-checks for this QA pass. The authoritative, exhaustive list lives in [SECURITY-CHECKLIST.md](SECURITY-CHECKLIST.md) — run through it in full separately before sign-off.

### I1. Production config sanity
- **Objective:** Confirm the `.env` that will actually ship with this build is production-safe.
- **Steps:**
  1. Inspect the `.env` intended for the release build (not the developer's local `.env`).
  2. Confirm: `DEBUG=False`, `DEMO_MODE=False`, `LIVENESS_REQUIRED=True`, `SECRET_KEY` is a real generated value (not the placeholder), `EMBEDDING_ENCRYPTION_KEY` is a valid Fernet key, `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS` match the deployment hostname/IP.
- **Expected result:** All values match production defaults. `manage.py check --deploy` against this `.env` reports no unsuppressed warnings.
- [ ] Pass  [ ] Fail

### I2. Installer payload does not leak dev secrets
- **Objective:** Confirm the built installer never ships developer data.
- **Steps:**
  1. Follow the mandatory release build procedure in SECURITY-CHECKLIST.md §0 (stop the exe, clean `dist`/`build`, run `build_exe.ps1 -Clean`, confirm it prints `SAFE`).
  2. Install on a clean machine; confirm the Create Admin wizard appears and developer credentials do not work.
- **Expected result:** No `.env`, `db.sqlite3`, `*.pem`, or `logs\` present in the installed payload; fresh install has no pre-existing accounts.
- [ ] Pass  [ ] Fail

### I3. Session and cookie behavior
- **Objective:** Confirm sessions expire correctly and cookies aren't accessible to JS.
- **Steps:**
  1. Log in, note the time, remain idle past 8 hours (or reduce `SESSION_COOKIE_AGE` temporarily in a test config) and confirm forced re-login.
  2. Close the browser tab and reopen — confirm the session ended (`SESSION_EXPIRE_AT_BROWSER_CLOSE`).
  3. Inspect cookies in devtools — confirm `HttpOnly` is set on session/CSRF cookies.
- **Expected result:** Idle/closed sessions require re-login; cookies are not readable from `document.cookie`.
- [ ] Pass  [ ] Fail

### I4. Role-based access enforcement (negative tests)
- **Objective:** Confirm URL-level access control, not just hidden nav links.
- **Steps:**
  1. As Staff, directly browse to admin-only URLs: `/accounts/users/`, an audit log URL, an analytics URL, a payout override URL.
- **Expected result:** Every attempt redirects with an access-denied message; none render admin content or throw an unhandled exception.
- [ ] Pass  [ ] Fail

### I5. Audit trail completeness
- **Objective:** Confirm the actions exercised across this entire QA pass are all captured.
- **Steps:**
  1. After completing sections A–H, open Audit Log and filter by today's date.
  2. Spot-check that logins, verifications, overrides, payout actions, duplicate-review decisions, and user-management changes performed above all appear.
- **Expected result:** No gaps for the actions actually performed; entries have enough detail (actor, action, target, timestamp) to reconstruct what happened.
- [ ] Pass  [ ] Fail

### I6. Error handling / information disclosure
- **Objective:** Confirm production error pages never leak internals.
- **Steps:**
  1. With the release `.env` (`DEBUG=False`), trigger a 404 (bad URL) and, if safely reproducible, a handled error path.
  2. Check `logs\django-errors.log` for the corresponding server-side detail.
- **Expected result:** User sees the branded 404/error page with no stack trace or file paths; full detail is only in the server log file.
- [ ] Pass  [ ] Fail

---

## Sign-off

| Section | Result | Notes |
|---|---|---|
| A. Authentication | ☐ Pass ☐ Fail | |
| B. Beneficiary Workflow | ☐ Pass ☐ Fail | |
| C. Face Verification | ☐ Pass ☐ Fail | |
| D. Representative Workflow | ☐ Pass ☐ Fail | |
| E. Payout Workflow | ☐ Pass ☐ Fail | |
| F. Notifications | ☐ Pass ☐ Fail | |
| G. Analytics Dashboards | ☐ Pass ☐ Fail | |
| H. Backup and Restore | ☐ Pass ☐ Fail | |
| I. Security Checks | ☐ Pass ☐ Fail | |

**Overall release recommendation:** ☐ GO  ☐ NO-GO  ☐ GO WITH CONDITIONS (list below)

_______________________________________________
