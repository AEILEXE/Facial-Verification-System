# FANSC &mdash; Known Limitations & First Defense Scope

**Full name:** FANSC: Secure FaceNet-Based Facial Verification System for Senior Citizen Stipend Distribution

This document describes the boundaries of the current build &mdash; what is in scope for the First Defense, what is intentionally deferred, and the operating conditions assumed by the system.

---

## 1. First Defense Scope (Approx. 75&ndash;80% Complete)

The version presented for the First Defense covers the **end-to-end stipend distribution workflow** from beneficiary registration through facial verification, manual review, claim release, audit, and reporting.

### In scope &mdash; implemented and exercised
- **Account roles:** President, Admin, IT, Staff with role-aware menus and permission checks.
- **Beneficiary registration:** Multi-step form, address selection, valid-ID capture, optional representative, registration approval queue.
- **Facial verification:** FaceNet embeddings with multi-template matching, three-zone decision band (auto-verify / manual review / deny), retry &amp; fallback flows.
- **Liveness checking:** Server-side anti-spoofing (texture analysis + presentation-attack detection), risk-based head-movement challenge, liveness-proof-bound token.
- **Manual review:** Admin queue for borderline matches, face update requests, pending-claim approvals, and special-claim requests.
- **Claim processing:** ClaimRecord lifecycle (claimed / pending / failed / cancelled / rejected), reference number, override/fallback flagging.
- **Audit logs:** Tamper-evident AuditLog with structured `details`, searchable + filterable, CSV export.
- **Reports:** Payout Claims Report, Distribution Summary Report, Staff Distribution Activity, Override/Fallback, Security Alerts &mdash; with Excel, CSV, and Print-to-PDF export.
- **Privacy &amp; consent notice:** Documented data collection, retention, correction, and deactivation flow.
- **Backup &amp; restore documentation:** This repository ships `docs/BACKUP-RESTORE.md` and the Database &amp; Backup Status page.
- **Local LAN deployment:** Caddy-fronted HTTPS, mkcert-issued local cert, Windows Task Scheduler autostart.

### Phase 1 hardening completed (2026-08-27, branch `4.0-Final`)
- **Startup key enforcement (Phase 1A):** `SECRET_KEY` and `EMBEDDING_ENCRYPTION_KEY` are now validated at startup; the server refuses to start if either is missing or set to a placeholder value.
- **Beneficiary DOB validation (Phase 1B):** `BeneficiaryEditForm` now rejects date-of-birth edits that would make the beneficiary under 60 years old, using a shared `validate_senior_citizen_dob()` validator in `beneficiaries/validators.py`.
- **Admin override payout gap closed (Phase 1C):** `admin_override()` only sets `decision=VERIFIED`; it does not create a `ClaimRecord`. A dedicated `override_release_payout` endpoint now provides the explicit second step (GET confirmation + POST with `transaction.atomic()` + `select_for_update()`). The "Release Payout" button appears on the result page only when an override is VERIFIED, no `ClaimRecord` exists, and the beneficiary is eligible. An `ACTION_CLAIM` AuditLog entry is created with `via='override_release_payout'`. Three-layer duplicate guard prevents double-release.

### Phase 3A operational improvements completed (2026-08-27, branch `4.0-Final`)
- **Django admin hardening:** `VerificationAttempt`, `FaceEmbedding`, `ClaimRecord`, and `AuditLog` are now fully read-only through the Django admin interface — no add, change, delete, or bulk-delete permitted. `ClaimRecord` was previously unregistered; `AuditLog` deletions were previously possible. `SystemConfig` remains intentionally writable for IT runtime toggles.
- **Automated daily backup:** `scripts/admin/daily-backup.ps1` runs nightly at 21:00 under the SYSTEM account via Windows Task Scheduler (`FANS-C Daily Backup` task). It performs a hot SQLite backup (consistent even while Waitress is serving requests), copies `.env` and `media\`, restricts the backup folder to Administrators + SYSTEM via NTFS ACL, rotates to keep the 14 most recent backups, and logs every run to `logs\fans-backup.log`. The Task Scheduler task is registered automatically by the application launcher during first-run setup. **Off-site copy (USB or secure shared folder) remains a manual operator responsibility.**
- **Beneficiary list pagination:** The beneficiary list is now paginated at 50 records per page with filter and search state preserved across page links.

### v2.1.16 hardening + clean-VM validation completed (2026-08-28)
- **Backup hardening:** collision/concurrency-safe destination selection with an exclusive run lock, a restore-readiness predicate shared identically between the backup script and the System Health page (status alone is no longer trusted — physical file presence and an exact `db_backup_bytes` size match are required), strict `_2`..`_999` retry-suffix canonicalization, and a working-directory-based fix for SQLite backup destinations containing an apostrophe.
- **Scheduler hardening:** first-run setup now aborts before declaring success if a required scheduled task fails to register (previously silent); the `FANS-C Daily Backup` task is registered via `Register-ScheduledTask -Force` with no preceding unregister step, in both the installer's first-run wizard and the alternate IT setup script.
- **Clean-VM deployment validation:** performed on a disposable VirtualBox Windows 11 VM installed from the actual release installer (not the dev machine). Confirmed at runtime: installation, first-run setup, all three scheduled tasks, SYSTEM-context backup execution and content, backup-folder ACL (including a genuine ordinary-user access-denial test), overlap/lock protection, rotation and retention, media backup, and an application/security smoke test. Explicitly left **UNVERIFIED** (not failed): scheduler idempotence (no lightweight repair path exists — only a full first-run reset, not performed to preserve the validated VM state), the rotation-failure exit code (2) under real fault injection, and the Django Admin read-only hardening check (no supported path to a staff account on a clean install). Uninstall/reinstall/restore-drill validation remains pending on the same VM.

### v2.2.0 Analytics, Intelligence, Notification, UX, and Workflow release (2026-08-29)
- **Notification system expanded:** 7 categories (was 4) plus a LOW/MEDIUM/HIGH priority field; a 48-hour approval-reminder sweep; new Security Alert (login lockout) and System Alert (backup failure) notification triggers.
- **Two real bugs fixed:** the dashboard "Next Payout" widget could previously show an unapproved stipend event with no visual distinction from a confirmed one; logging in over the plain-HTTP LAN-IP fallback and refreshing the page could silently log the user out (Secure-flagged cookies dropped by the browser over plain HTTP). Both are fixed — see `CHANGELOG.md [2.2.0]`.
- **Fraud/Security Review reworked:** moved from threshold-multiple LOW/MED/HIGH banding to an explicit 0-100 point score with two additional signal types (repeated login failures by IP, payout anomalies by staff). As with every fraud/risk signal in this system, this remains **view/flag-only** — no signal at any score automatically blocks, suspends, or denies any account or beneficiary.
- **Face-verification intelligence re-audited, not changed:** confirmed no age-estimation or auto-reject-by-age logic exists anywhere; documented in `docs/FACE-VERIFICATION-INTELLIGENCE-AUDIT.md` including the explicit reasoning for not building one (elderly-face accuracy risk, unverifiable-provenance risk for a government biometric system).

### v2.2.0 UX/UI review pass (2026-09-02)
- **Full UX audit performed** across header/navigation, dashboard, analytics, beneficiary list/detail, registration, face-verification UI, representative screens, claims/payout, notifications, system administration, org chart, and error/empty states. Most items were found already implemented correctly from prior sessions (v2.1.16 responsive-navbar fix, fixed-height Chart.js containers, wording/status-clarity passes).
- **Accessibility — form label association:** `<label>` elements for text/select/date inputs across the app were not programmatically associated with their inputs (no `for`/`id_for_label`), so screen readers could not announce which field a label described and clicking a label did not focus its field. Fixed on the highest-traffic forms: beneficiary registration (steps 1–2), beneficiary edit, login, all OTP/password-reset flows, face-image capture forms, user account create/edit, officer assignment/position forms, and the distribution-event form. Remaining forms (report filter bars, several admin review/override forms) still need the same fix.
- **Follow-up pass (same date):** reconciled against the original UX brief item-by-item. Two genuinely new changes made: (1) the navbar link and every breadcrumb's root link were shortened from "Distribution Dashboard" to "Dashboard" (the Dashboard page's own `<h4>` heading keeps the fuller "Distribution Dashboard" title) to reduce navbar/breadcrumb width; (2) the Dashboard gained a compact "Beneficiary Status" doughnut chart (Active/Pending/Inactive/Deceased) sourced from the same `get_executive_metrics()` aggregate the Analytics Executive tab already uses, so the two can never drift apart — a point-in-time snapshot chart was judged Dashboard-appropriate, while historical trend charts (claims/payout over time) were deliberately kept Analytics-only to avoid duplicating that page.

### v2.2.0 UX/UI review pass, submenu icon + header composition follow-up (2026-09-02)
- **Submenu icons restored selectively, not blanket-reverted:** a prior pass had removed icons from essentially every dropdown item. Re-audited each item individually and restored a small, single-tone (muted-gray, accent-on-hover) Bootstrap Icon wherever it distinguishes the action from its siblings (e.g. Duplicate Face Review vs. Duplicate Name/DOB Review). Left icon-less only where genuinely redundant: "Privacy & Data Consent" in the Administration menu (already iconed in the profile dropdown; would otherwise clash with the shield icon already used for "Roles & Permissions"). That item reserves the icon-gutter width via an empty `.bi-spacer` span so its label still lines up with its iconed siblings instead of sitting flush against the menu edge.
- **Header composition fixed:** the primary nav (`.navbar-nav`) was `me-auto`, hugging the brand and leaving a ~325px dead gap before the notification bell/profile control at common desktop widths (measured at 1440px). Changed to `mx-auto` so the nav centers in the space between branding and the right-hand controls (now a balanced ~170px on each side). Re-verified zero horizontal overflow at 1200/1280/1366/1440/1536/1600/1680/1920/900/768px.
- **Regression fixed — "Quezon City" brand subtitle was invisible at nearly all desktop widths:** a leftover rule from the earlier navbar-overflow fix (`@media (min-width:992px) and (max-width:1679.98px) { .brand-sub { display:none } }`) hid the subtitle across almost the entire normal desktop range, even though the subtitle only affects the brand block's vertical layout and was not load-bearing for that overflow fix. Removed; subtitle now renders at all tested widths with no overflow regression.
- **Org Chart connector lines confirmed non-functional, root-caused, not fixed (pre-existing, out of scope):** the template/CSS for tier-to-tier connector lines (`.org-connector`, `.org-tier.multi::before`) is implemented correctly, but every active `OfficerPosition` in the live database has `level=0`, so `org_chart()` (accounts/views.py) always produces a single tier — the "rows" seen on screen are ordinary flex-wrap, not real tiers, so zero connector elements ever render. This is a data-configuration/business-logic matter (no admin has differentiated position levels), unrelated to this session's navbar/CSS work, and was left unchanged per this pass's explicit backend/business-logic scope boundary.
- **UI refinement** &mdash; remaining form-label accessibility sweep (see above), mobile layout pass.
- **Final hardening** &mdash; dependency upgrade audit (Django 4.2 LTS extended support ended April 2026; migration target is Django 5.2 LTS), log-rotation tuning.
- **Deployment testing** &mdash; multi-barangay LAN pilot, postgres migration test, restore-from-cold drill (uninstall/reinstall/restore-drill phases of the v2.1.16 clean-VM validation above remain pending).

### Final UX/UI + Analytics correction checkpoint (2026-09-02, same day)

A second real-user visual review of the rendered app (not source reading)
found the passes above had gone too far in two places and left one layout
issue unaddressed. All three fixed this checkpoint:

- **Header lost the system name entirely.** The 2026-09-02 header-composition
  follow-up above ("Regression fixed") correctly restored the "Quezon City"
  subtitle's visibility, but an earlier pass in the same range had already
  shortened it from "Facial Verification System — Quezon City" down to just
  "Quezon City" — so the system name was permanently gone at every width,
  not only in the squeeze band. Restored a three-tier `FANSC` / `Facial
  Verification System` / `Quezon City` hierarchy, showing all three tiers
  everywhere *except* the existing 992–1679.98px squeeze band (same
  breakpoint the header-composition fix above already established as
  "no room to spare"), where only `FANSC` / `Quezon City` shows, exactly as
  it did before this checkpoint. Re-verified zero horizontal overflow at
  1920/1680/1440/1366/1200/900/768px via a live headless-Chromium session.
- **"Privacy & Data Consent" icon reconsidered.** The icon-audit bullet
  above deliberately left this one item icon-less to avoid "clashing" with
  the shield icon on "Roles & Permissions." On review the two are not
  adjacent (different sub-groups, separated by a divider and five other
  items) and don't read as a clash in practice; every *other* item in the
  same dropdown kept a muted icon, so this was the one inconsistent
  exception rather than a deliberate pattern. Restored `bi-shield-check`
  in the same muted style as its siblings.
- **Dashboard "Recent Activity" card had excessive empty space below its
  5 rows** (`h-100` stretched it to match the taller column beside it).
  Removed `h-100` so the card sizes to its actual content.

### Phase A final acceptance correction (2026-09-02, same day)

A formal Phase A acceptance check found the header fix directly above had
hidden the `Facial Verification System` middle line across the *entire*
992–1679.98px band — meaning it was actually missing at every normal
desktop/laptop width (1200/1280/1366/1440/1536), not just the tightest
ones. Re-measured with a headless-Chromium harness (middle line forced
visible at every width): the navbar's real content need is a constant
1126px, overflowing only below that — confirmed at 992–1100px (overflow)
vs. 1150px+ (clean). Narrowed the hide rule to 992–1149.98px only
(`static/css/main.css`); all other squeeze-band spacing rules unchanged.
Re-verified zero overflow at 1920/1680/1536/1440/1366/1280/1200/900/768px
with tier-by-tier visibility recorded per width, plus a 40-check
regression sweep (8 representative pages × 5 widths) confirming no other
shared-layout page was disturbed.

### v2.1.16 Post-UAT Stabilization pass (2026-09-04)

A member/officer UAT round on the installed v2.1.16 build surfaced 9
issues; all were audited against actual code before any change (root
cause first, then fix). Full detail in `CHANGELOG.md`.

- **OTP email now professionally branded.** `accounts/otp.py` sent plain
  `send_mail()` text with no HTML template at all. Added
  `templates/accounts/emails/{otp_code,password_changed}.{html,txt}`
  (navy/gold FANS-C branding, large OTP display box, expiry/one-time-use/
  security notices, plain-text fallback) and switched to
  `EmailMultiAlternatives`. OTP generation, expiry, resend cooldown, rate
  limiting, anti-enumeration, and audit logging are unchanged.
- **Forgot-password UI hierarchy improved.** The login page's recovery
  links were tiny inline-styled text; restyled as two clearly labeled,
  full-width buttons ("Reset Password" / "Request Administrator
  Assistance"), same URLs/views.
- **Responsive-layout overflow fixes.** Added a global `overflow-x`
  safety net (`html,body`) and fixed the concrete overflow source (the
  notification dropdown, below); the already heavily-measured 992–1679px
  navbar breakpoint system from the prior UX passes above was
  deliberately left untouched to avoid regressing it.
- **Notification dropdown reworked.** Fixed 340–380px width and no
  text-wrap protection caused long titles/messages to overflow the
  dropdown on narrow viewports. New `.notification-item` CSS
  (responsive width, word-wrap, aligned icon/timestamp) replaces the
  inline-styled markup in `templates/base.html`.
- **Deactivate/Suspend reason entry replaced a native `prompt()`
  popup with a Bootstrap modal** (`templates/accounts/user_list.html`),
  patterned on the existing reject-reason modal elsewhere in the app.
  Same ≥5-char client validation, same hidden `reason` field, same
  server-side validation/permission-check/audit-log payload in
  `accounts/views.py::user_set_status` — presentation only.
- **Dashboard/Verify-page active-event inconsistency fixed** (see
  security-relevant detail in `docs/SECURITY-CHECKLIST.md` item 8.40) —
  the dashboard could show a payout event as "OPEN" while Verify
  correctly refused to start a claim because it was outside the event's
  daily claiming window. New `SCHEDULED` status makes the dashboard
  match Verify's actual gate.
- **Duplicate-face notification system audited, no bug found.**
  Creation, data accuracy, read/dismiss lifecycle, and role-gated
  visibility all checked out against the actual code; no change made.
- **Liveness/PAD false-rejection calibration** (see
  `docs/SECURITY-CHECKLIST.md` item 8.39) — isolated bright-lighting/
  glasses-reflection glare on a real face could, by itself, cross the
  same denial threshold as an actual phone-screen replay. Recalibrated
  so isolated glare requires corroborating screen-flatness to reach the
  high-confidence weight; all other PAD signals unchanged. Denial
  messages now distinguish "possible presentation attack" from
  "lighting/camera conditions."
- **Installer first-run wizard (`dev/launcher.py`) re-themed** to the
  FANS-C navy/gold identity (shared color constants, a `ttk.Style`
  progress bar, a branded header on the first setup window, matching
  colors on the admin-account and email-OTP-configuration windows).
  Purely cosmetic — no change to the setup steps, certificate/Task
  Scheduler/`.env`/account-creation logic.

This pass did not touch code covered by the "Phase A final acceptance
correction" navbar/header work above, and did not rebuild the installer
`.exe` (still last built at v2.1.18 — see README.md's "Latest Release").

### v2.1.16 Final Hardening Patch (2026-09-05)

A focused security patch closing the remaining findings from an external
review of the liveness/PAD pipeline. No UI/UX change. Full detail in
`CHANGELOG.md` and `docs/SECURITY-CHECKLIST.md` items 8.41–8.44: PAD
production-configuration enforcement (fails closed on a "soft" PAD
config), liveness challenge/session binding (closes a retry-rotation
gap), an atomic perceptual-hash replay reservation (closes a concurrent-
replay race — see 2.8 below for its single-process scope), and an
upgrade-safety data migration for pre-existing evidence-hash duplicates.
Source-only — no EXE/installer built, nothing committed as of this entry.
1407 tests pass, 0 failures.

---

## 2. Known Limitations

### 2.1 Deployment Environment

- **Controlled local / LAN only.** The system is designed for a barangay office network. It is **not** intended to be exposed to the public internet without additional hardening (reverse proxy, WAF, rate-limiting, monitoring).
- **Single server.** All four roles (President / Admin / IT / Staff) connect to one FANSC instance. No multi-server replication is shipped.

### 2.2 Database

- **SQLite is the prototype default.** SQLite handles dozens of concurrent staff stations reliably for the read/write profile of a barangay office, but is **not recommended for multi-station production use** at city scale.
- **PostgreSQL is recommended for multi-station production.** Switch by setting `USE_SQLITE=False` and providing `DB_*` values in `.env`. The schema and migrations are PostgreSQL-compatible.

### 2.3 Browser / Camera

- **HTTPS is required for camera access.** Modern browsers expose `getUserMedia` only on `https://` or `http://localhost`. The fallback `http://<LAN-IP>:8000` URL works for everything except camera-based verification.
- **Proper lighting required.** FaceNet accuracy drops sharply under heavy shadows, back-lighting, or extreme overexposure. The capture room should have even, front-facing light.
- **Stable camera position required.** A handheld webcam that jitters during capture produces blurry frames and is more likely to be routed to manual review.
- **A modern browser is required** &mdash; Chrome, Edge, or Firefox from the last two years. Internet Explorer is not supported and will not load the verification page.

### 2.4 Backup &amp; Encryption-Key Management

- **Automated nightly backup (Phase 3A, hardened in v2.1.16).** A Windows Task Scheduler task (`FANS-C Daily Backup`) runs `scripts/admin/daily-backup.ps1` nightly at 21:00 under the SYSTEM account. It backs up `db.sqlite3`, `.env`, and `media\` to a timestamped subfolder under `backups\`, with NTFS permissions restricted to Administrators + SYSTEM (by well-known SID). An exclusive run lock prevents an overlapping scheduled/manual invocation from colliding with an in-progress backup. The 14 most recent *restore-ready* backups (verified integrity/env state plus an exact recorded-vs-actual size match, not just directory presence) are retained automatically; older ones, and any incomplete/failed attempt, are never silently mistaken for a valid backup. See `docs/BACKUP-RESTORE.md` Section 3 for details and manual fallback procedures. **Copying the latest backup to an off-site location (USB drive or secure shared folder) remains a manual operator responsibility after each automated run.**
- **`EMBEDDING_ENCRYPTION_KEY` is irrecoverable.** If the key is lost, every stored face embedding becomes permanently undecryptable and every beneficiary must be re-enrolled. The key must be backed up separately from the database.

### 2.5 Verification Accuracy

- **FaceNet on webcam captures is imperfect.** The system intentionally uses a three-zone decision band (verify / manual-review / deny) and routes borderline matches to a human reviewer. No single similarity score should be treated as final.
- **Significant appearance changes** (illness, surgery, large weight change) may require face re-enrollment by an admin.

### 2.6 Login Lockout Counter Durability

- **In-memory, per-process counter.** Failed-login lockout counts (and the v2.2.0 Security Alert notification that fires on lockout) are held in Django's default `LocMemCache`, not a shared store. This is correct and sufficient for FANS-C's single-process Waitress deployment model, but means the counter resets if the process restarts, and would not be shared across multiple worker processes if the deployment model ever changed to one. Not a concern under the current single-instance-per-server architecture.

### 2.7 Network / Sync

- **Offline-sync is optional.** When `SYNC_API_URL` is empty the system runs fully offline. Multi-site synchronization is implemented in `beneficiaries/sync.py` but is not part of the First Defense scope.

### 2.8 Liveness Replay-Evidence Lock Durability (v2.1.16 Final Hardening Patch)

- **In-process lock, not a distributed lock.** `verification/views.py::check_and_reserve_liveness_evidence()` uses a Python-level lock (`_liveness_replay_lock`) to make the perceptual-hash replay scan atomic. This is correct and sufficient for FANS-C's single-process Waitress deployment model (see 2.6 above for the same pattern applied to login-lockout counting) but would not serialize concurrent requests across multiple worker **processes** if the deployment model ever changed to one — the underlying `LivenessEvidenceReservation` DB rows and `LivenessTransaction.evidence_hash`/`evidence_pixel_hash` unique constraints would still catch an exact/pixel-identical replay in that scenario, but the perceptual-hash (near-duplicate) race the lock closes could reopen. Not a concern under the current single-instance-per-server architecture.

---

## 3. Operating Assumptions

The system is designed around the following deployment assumptions. Operating outside these assumptions is supported but not warranted.

| Area | Assumption |
|---|---|
| Concurrency | Up to ~10 simultaneous staff stations on one LAN. |
| Catalogue size | Tens of thousands of beneficiaries per barangay; not millions. |
| Network | Wired Ethernet or stable Wi-Fi inside a barangay office. |
| Power | UPS-backed server. Sudden power loss may interrupt an in-flight claim and require the audit log to reconcile. |
| Operator skill | At least one designated IT person who can run the setup scripts and read this guide. |
| Camera | 720p webcam or built-in laptop camera. Industrial cameras are not required. |

---

## 4. What This System Does NOT Replace

- **Government-issued senior citizen verification.** The OSCA/Senior ID remains the legal proof of eligibility. FANSC accelerates and audits the *payout* moment.
- **Manual oversight.** Manual review, admin override, and fallback ID claim paths are by design. A facial verification system should never be the sole gatekeeper.
- **A signed agreement / consent document.** The Privacy &amp; Data Consent Notice in the system is informational; a printed consent form signed at registration may still be required by local policy.
