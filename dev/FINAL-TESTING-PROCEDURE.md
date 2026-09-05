# FANS-C v2.1.18 — Clean Windows Machine Validation Plan

**Do not execute destructive steps (Uninstall section, restore-from-backup) without explicit approval from whoever owns the test machine's data.** Everything else here is safe to run in sequence on a clean laptop that has never had FANS-C installed.

Installer: `FANS-C-Setup-v2.1.18.exe`
SHA-256: `1298ed678f14a206c89a609d2087db1cc7982b173ac0cfb0070b1552b9bf1579`

Record PASS / FAIL / UNVERIFIED for every row. Do not mark PASS without actually performing the step.

---

## 1. Installation

- [ ] Copy the installer to the clean machine. Recompute SHA-256 and confirm it matches the value above exactly.
- [ ] Run the installer as Administrator (UAC prompt appears). Installation completes without error.
- [ ] Right-click installed `fans_c.exe` → Properties → Details: File version / Product version = `2.1.18.0`.
- [ ] Add/Remove Programs lists the app as version `2.1.18`.

## 2. Application launch & browser access

- [ ] Launch FANS-C. **"Create Admin Account" must appear** — if not, stop immediately, the installer is unsafe.
- [ ] Create a fresh admin account.
- [ ] Waitress + Caddy start; browser opens automatically.
- [ ] `.env`, TLS cert, and `rootCA.pem` were generated fresh on this machine (not copied from the build machine).

## 3. Certificate trust

- [ ] Without importing `rootCA.pem`, the browser shows a certificate warning on `https://fans-barangay.local`.
- [ ] Import `_internal\CLIENT-SETUP\rootCA.pem` into the Windows trust store (or run `scripts\admin\verify-proxy-trust.ps1` per SETUP.md).
- [ ] After import, the site loads with no certificate warning.

## 4. Authentication

- [ ] Login page rejects a wrong password with a clear (non-technical) error.
- [ ] Login succeeds with the correct credentials for the admin created in step 2.
- [ ] Create one user per role (President, Admin, IT, Staff) from Accounts & Roles → Staff & User Accounts.
- [ ] Log in as **Staff**: System Administration menu, Analytics, Fraud Signals, and admin-only report pages are all inaccessible (redirected/denied).
- [ ] Log in as **IT**: IT & Network menu (System Health, Network Status) is visible; visible to no other role.
- [ ] Log in as **President**: can approve a pending claim / stipend event schedule that Admin cannot.
- [ ] Only one active President account is allowed — attempting to assign a second is blocked.

## 5. Beneficiary flow

- [ ] Register a new beneficiary (Step 1 personal info → Step 2 representative/consent → face capture).
- [ ] Edit an existing beneficiary's details and save — changes persist.
- [ ] Search beneficiaries by name, beneficiary ID, and Senior Citizen ID — each returns the expected result.
- [ ] Beneficiary list pagination works (50/page) — navigate to page 2+ without error.
- [ ] Deactivate a beneficiary and confirm they no longer appear in active verification search.

## 6. Face verification

- [ ] **Registration**: face capture succeeds, quality check accepts a clear photo and rejects a severely blurry one with a plain-language message.
- [ ] **Verification**: run a full verification (liveness challenge → face match) against the beneficiary registered above — reaches a decision (verified / manual review / denied).
- [ ] **Liveness / PAD**: the head-movement challenge triggers correctly; a static photo held up to the camera is flagged as suspicious rather than passing.
- [ ] **Failed attempt logging**: deliberately fail a verification (wrong person or no challenge completed) — confirm a `VerificationAttempt` row appears in Verification Log with the correct decision and reason.
- [ ] Verification result page shows the plain-language confidence label ("High confidence match" / "Moderate confidence — review band" / "Low confidence") next to the similarity score.
- [ ] Register a second face template for the same beneficiary (Update Face Data) and confirm Face Template Analytics shows both templates.
- [ ] Confirm **no automatic denial based on estimated age** anywhere — the appearance-drift signal (if triggered) only shows an advisory banner on Update Face Data, never blocks capture or verification.

## 7. Notifications

Full example flow to verify end-to-end:

```
Admin approval required (e.g. submit a fallback ID verification request)
        ↓
Log in as an admin — bell badge count increases by 1
        ↓
Click the bell — dropdown shows the new notification with category icon + message
        ↓
Click the notification
        ↓
Browser navigates DIRECTLY to the relevant approval/review page
        ↓
Notification is marked read — badge count decreases
```

- [ ] Run the flow above and confirm every arrow actually happens (not just the first/last step).
- [ ] Trigger at least one notification of each category and confirm it fires from a real action:
  - **Approval reminder** — submit a fallback ID verification request, a face update request, or a password reset request.
  - **Fraud alert** — see Section 10; a MEDIUM/HIGH risk signal notifies admins.
  - **Verification review** — a fallback ID request routes to Manual Review and notifies admins.
- [ ] "Mark all read" (bell dropdown and full Notification Center page) clears all unread badges.
- [ ] Full Notification Center (`/logs/notifications/`) lists all notifications, paginated, newest first.
- [ ] A Staff account only ever sees notifications addressed to it, never another user's.

## 8. Backup

- [ ] **Scheduled backup**: confirm the "FANS-C Daily Backup" Task Scheduler task is registered (Task Scheduler Library) and set for 21:00 daily.
- [ ] **Manual backup**: run `scripts\admin\daily-backup.ps1` directly and confirm a new `backups\<timestamp>\` directory appears with `db.sqlite3`, `.env`, and a `_backup_manifest.json`.
- [ ] **Verification**: run `scripts\admin\verify-backup.ps1` against the new backup directory — reports restore-ready.
- [ ] Confirm the backup's completion was mirrored into the Audit Log (`Backup Completed` entry) after visiting System Health.
- [ ] **Restore** *(destructive — get explicit approval before running against real data; use a disposable copy of the DB if possible)*: run `scripts\admin\restore-backup.ps1` and confirm the database restores correctly.

## 9. Analytics

- [ ] Executive, Operational, and Security dashboard tabs all load without error.
- [ ] Operational tab's charts render using the bundled Chart.js — confirm in DevTools → Network that **no request goes to any external/CDN host**.
- [ ] Date-range filter changes the displayed numbers.
- [ ] **Export** button on each of the three tabs downloads a valid CSV — open it and check the contents are correct, not just that a file appeared.
- [ ] A Staff account is denied access to all three tabs.

## 10. Fraud

- [ ] Fraud Signals page loads with three sections (Repeated Failures, Anomalous Staff Volume, Admin Mass-Edit Detection), each showing a **LOW / MEDIUM / HIGH** risk badge.
- [ ] Manufacture a MEDIUM or HIGH case (e.g. repeatedly fail verification for one test beneficiary past the configured threshold: default 3/30 days) and confirm it appears with the correct badge, and a **fraud alert notification** was created for admins (see Section 7).
- [ ] Confirm there is **no button anywhere on this page that blocks, suspends, or denies** a beneficiary or staff account — review-only.
- [ ] Admin review is possible: each flagged row links to the relevant beneficiary/staff detail page.
- [ ] A Staff account is denied access.

## 11. Password Recovery

Current behavior (document what you observe matches this):
- [ ] From the login page, "Request a password reset" → submit a username → identical generic confirmation message whether the username exists or not.
- [ ] As admin, the submitted request appears in **Password Reset Requests** (Accounts & Roles menu), and a notification was created.
- [ ] Approve a request tied to a real account → password changes → target account is forced to change it again on next login.
- [ ] Reject a different pending request → it disappears from the queue; target account's password is unchanged.

Known limitations (by design — see `docs/PASSWORD-RECOVERY-ARCHITECTURE.md`):
- No email/SMTP-based reset exists or is planned for this deployment profile (LAN-only, no guaranteed internet at barangay sites).
- Recovery always requires an admin/president to act — there is no fully self-service path, by design.

Future recommendation: if a deployment site is later confirmed to have reliable outbound SMTP, an email-reset option could be added *alongside* (not replacing) the current approval workflow — see the architecture doc's Option C.

## 12. Uninstall

*(Destructive — run only after all sections above are complete and approved.)*

- [ ] Run the Windows uninstaller (Add/Remove Programs) or `scripts\admin\uninstall-clean.ps1`.
- [ ] Application files, Start Menu entries, and desktop shortcut are removed.
- [ ] "FANS-C Verification System" and "FANS-C Watchdog" scheduled tasks are removed (check Task Scheduler Library).
- [ ] No sensitive leftovers: confirm `.env`, `db.sqlite3`, `*.pem`, and `media\` do not remain anywhere on disk unless the uninstaller explicitly warned it would preserve them for reinstall purposes.
- [ ] `fans-barangay.local` hosts-file entry is removed (or documented as intentionally left, if that's the uninstaller's designed behavior).

---

## Sign-off

| Section | Result | Tester | Date | Notes |
|---|---|---|---|---|
| 1. Installation | | | | |
| 2. Application launch & browser access | | | | |
| 3. Certificate trust | | | | |
| 4. Authentication | | | | |
| 5. Beneficiary flow | | | | |
| 6. Face verification | | | | |
| 7. Notifications | | | | |
| 8. Backup | | | | |
| 9. Analytics | | | | |
| 10. Fraud | | | | |
| 11. Password Recovery | | | | |
| 12. Uninstall | | | | |

**Overall result:** ☐ PASS — ready for official production release  ☐ FAIL — do not release  ☐ CONDITIONAL — list blockers below

Blockers (if any):
