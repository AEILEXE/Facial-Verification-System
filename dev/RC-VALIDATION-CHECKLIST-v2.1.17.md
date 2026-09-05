> **HISTORICAL — superseded by v2.1.18 and all later releases.** Kept for reference only; do not use this checklist to validate a current build. See [CHANGELOG.md](../CHANGELOG.md) for the current release.

# FANS-C v2.1.17 Release Candidate — Clean-VM Validation Checklist

Installer: `FANS-C-Setup-v2.1.17.exe`
SHA-256: `8293fa82b5fbd54dd446247c52c8d494722d0ff4434ae4124793e6a1a19f4b09`
Built: 2026-08-28

**Run this on a clean Windows VM that has never had FANS-C installed — not the build machine.** Record PASS/FAIL/UNVERIFIED for every row; do not skip rows.

## 1. Installer integrity

- [ ] Copy `FANS-C-Setup-v2.1.17.exe` to the VM. Recompute SHA-256 and confirm it matches the hash above exactly.
- [ ] Run the installer. UAC prompt appears (admin elevation required).
- [ ] Install completes without error.

## 2. First-run / fresh-install behavior

- [ ] Launch FANS-C. **"Create Admin Account" must appear** — if it does not, the installer is unsafe (contains a pre-existing DB) and this RC fails immediately.
- [ ] Create a new admin account with a fresh username/password.
- [ ] The developer's/build-machine's credentials do **not** work (they shouldn't exist on this VM at all).
- [ ] `.env` was generated on the VM (not copied from the build machine) — check `SECRET_KEY`/`EMBEDDING_ENCRYPTION_KEY` are unique to this install.
- [ ] TLS certificate (`fans-cert.pem`/`fans-cert-key.pem`) generated fresh for this machine's IP.
- [ ] `rootCA.pem` present in `CLIENT-SETUP\` for client trust import.

## 3. Core service startup

- [ ] Waitress (app server) starts and port 8000 responds.
- [ ] Caddy (HTTPS proxy) starts and the HTTPS URL loads without a certificate warning (after importing rootCA per SETUP.md).
- [ ] Browser opens automatically to the login page.
- [ ] Reboot the VM. Confirm autostart brings the service back up without manual intervention.

## 4. Version confirmation (on the installed app)

- [ ] Installed `fans_c.exe` → right-click → Properties → Details tab: File version = `2.1.17.0`, Product version = `2.1.17.0`.
- [ ] Add/Remove Programs (or Apps & Features) lists the app version as `2.1.17`.

## 5. Baseline regression (pre-existing features, must still work)

- [ ] Log in as the newly created admin.
- [ ] Register a new beneficiary with face capture.
- [ ] Run a verification attempt against that beneficiary (liveness challenge + face match).
- [ ] Create a stipend event and release a payout claim.
- [ ] View Audit Log (`/logs/audit/`) and Verification Log (`/logs/verification/`) — both load and show the actions above.
- [ ] Existing reports (Payout Claims, Distribution Summary, Staff Performance, Override/Fallback, Security Alerts) all load without error.

## 6. Phase 4 P0 — hardening

- [ ] **Password reset request (self-service):** From the login page, click "Request a password reset", submit a username. Generic "submitted for review" message appears regardless of whether the username exists (test both a real and a fake username — messages must be identical).
- [ ] As admin, open **Password Reset Requests** (System Administration → Accounts & Roles). The submitted request appears in the pending queue.
- [ ] Click "Approve & Reset" on a request tied to a real account → set a new password → confirm the target account's password actually changed and `must_change_password` forces a change on next login.
- [ ] Reject a different pending request → confirm it disappears from the pending queue and the target account's password is unchanged.
- [ ] **Backup audit trail:** Visit System Health (IT & Network menu, IT role only). Confirm no errors. If any backups exist under `backups\`, confirm corresponding `Backup Completed`/`Backup Incomplete` entries appear in the Audit Log.
- [ ] **Automated backup:** Confirm the daily backup Scheduled Task is registered (Task Scheduler) and, if time permits, manually trigger `scripts\admin\daily-backup.ps1` and confirm a new backup directory + manifest appear under `backups\`.
- [ ] Run `scripts\admin\verify-backup.ps1` against the newest backup — confirms restore-readiness.
- [ ] (Optional, destructive — use a disposable copy of the DB) Run `scripts\admin\restore-backup.ps1` against a backup and confirm the database restores correctly.

## 7. Phase 4 P1 — Analytics Dashboard

- [ ] System Administration → Reports → **Analytics Dashboard** loads (Executive tab).
- [ ] Executive tab shows non-error counts for beneficiaries, staff, verifications, claims.
- [ ] Operational tab loads; the two charts (Verification Volume, New Registrations) render visibly using the locally-vendored Chart.js — **open DevTools Network tab and confirm no request to any external/CDN host was made** (LAN-only requirement).
- [ ] Security tab loads and its "Fraud Signals (Beta)" / "Full Security Alerts Report" links work.
- [ ] Date-range filter (From/To) on any tab actually changes the displayed numbers.
- [ ] A non-admin (staff) account is denied access to all three tabs.

## 8. Phase 4 P1 — Fraud Signals (Beta)

- [ ] Reports → **Fraud Signals (Beta)** loads with three sections (Repeated Failures, Anomalous Staff Volume, Admin Mass-Edit Detection).
- [ ] Sections show "No ... over the threshold" when no data qualifies (expected on a fresh install).
- [ ] Confirm the page contains no action button that blocks, suspends, or denies anything — view/flag only.
- [ ] A non-admin (staff) account is denied access.

## 9. Phase 4 P1 — Template Match Analytics

- [ ] Reports → **Template Match Analytics** loads.
- [ ] Re-enroll a beneficiary's face via Update Face Data at least twice (to create a second template), then run several verifications so both templates accumulate wins.
- [ ] Template Match Analytics shows a per-template win breakdown for that beneficiary.
- [ ] If a non-primary template has won the majority of that beneficiary's recent verified attempts, the **advisory banner** ("Re-enrollment suggested…") appears on their Update Face Data page — confirm it is informational only and does not block or alter the capture flow.
- [ ] A staff (non-admin) account can still reach Update Face Data normally (this page is not admin-restricted).

## 10. Packaging / security sanity (on the installed machine)

- [ ] `C:\FANSC\_internal\` contains no `.env`, no `db.sqlite3`, no `*.pem` other than what this VM itself generated after install.
- [ ] `C:\FANSC\logs\` contains only logs generated on this VM (no pre-existing entries from the build machine).
- [ ] No leftover files from the build machine's own testing.

## Sign-off

| Section | Result | Tester | Date | Notes |
|---|---|---|---|---|
| 1. Installer integrity | | | | |
| 2. First-run behavior | | | | |
| 3. Core service startup | | | | |
| 4. Version confirmation | | | | |
| 5. Baseline regression | | | | |
| 6. Phase 4 P0 hardening | | | | |
| 7. Analytics Dashboard | | | | |
| 8. Fraud Signals | | | | |
| 9. Template Match Analytics | | | | |
| 10. Packaging sanity | | | | |

**Overall RC v2.1.17 result:** ☐ PASS — ready for production release  ☐ FAIL — do not release  ☐ CONDITIONAL — list blockers below

Blockers (if any):
