# Changelog

All notable changes to FANS-C are documented here.

> **Version-numbering note:** development between 2026-08-27 and 2026-09-02
> was tracked internally under the milestone labels "v2.2.0" and "Post-UAT
> Hardening Pass," seen throughout the entries below. That work was never
> packaged as a separate v2.2.0 installer — the project's released version
> line stayed on v2.1.x (see `dev/installer/fans_c.iss`'s `AppVersion`, now
> at 2.1.17, and the `main` branch). Everything recorded under the "v2.2.0"
> label is real, shipped functionality — only the version *number* attached
> to it was superseded, not the content. See the "Release Information"
> section of [README.md](README.md) for the current released version.

---

## [2.1.17] — 2026-09-06 (UI and Operational Modernization Release)

A UI/UX modernization and operational-usability pass across authentication,
dashboard, analytics, and reporting screens, plus filtering improvements on
several admin list views. No biometric algorithm or threshold changes.

### Added

- **Authentication UI modernization** — reworked `login.html`,
  `otp_verify.html`, `otp_forgot_password.html`, and `otp_reset_password.html`
  onto a shared, contained auth-shell layout; the post-login welcome message
  now also shows the signed-in user's role.
- **Branded CSRF failure page** (`templates/403_csrf.html`) — Django's default
  CSRF failure view picks this up automatically by its conventional
  `CSRF_FAILURE_TEMPLATE_NAME`, replacing the stock technical 403 page with
  the FANSC-branded "Session or Form Expired" screen (no context processors
  available at that point, so it's a standalone template, same pattern as
  the pre-auth pages).
- **Beneficiary list barangay filter** (`beneficiaries/views.py`) — staff can
  now filter the beneficiary list by barangay, matching the filter already
  available on the Master List Report; active-filter count surfaced to the
  template.
- **Verification log filters** (`logs/views.py`) — added beneficiary name/ID,
  performed-by, and date-range (`date_from`/`date_to`) filtering to
  `verification_log_list`, with active filters preserved across pagination
  links via a precomputed querystring.
- **Analytics presentation improvements** (`verification/analytics.py`,
  `analytics_executive.html`, `analytics_operational.html`,
  `analytics_security.html`) — new pre-aggregated operational summary
  (verified/manual-review/not-verified counts and rates), a manual-review and
  not-verified breakdown added to the daily trend, per-staff claims-released
  and success-rate figures, and a bounded-range Security Events Over Time
  trend (failed logins, failed verifications, duplicate faces, payout
  overrides).
- **Payout Calendar** (`static/js/analytics.js`, `stipend_list.html`) —
  month/year calendar grid rendering, with all event fields HTML-escaped
  before insertion.
- **Manual Review, org chart, and user list usability refinements**
  (`manual_review.html`, `org_chart.html`, `user_list.html`,
  `report_event_summary.html`, `report_event_summary_print.html`,
  `master_list_report.html`, `dashboard/index.html`, `base.html`) —
  layout, responsiveness, and navigation polish; shared CSS consolidated in
  `static/css/main.css`.

### Notes

- `DEMO_MODE` and the production biometric threshold are unchanged by this
  release (see the v2.1.16 Final Hardening Patch below for the last change
  to that area).
- Full test suite: **1435 tests, 0 failures, 0 errors** (592.5s). `manage.py
  check`: 0 issues. `manage.py makemigrations --check --dry-run`: no changes
  detected.

---

## [Unreleased] — v2.1.16 Final Hardening Patch (2026-09-05)

A focused, source-only security patch closing the remaining findings from an
external review of the liveness/PAD pipeline (informally "Codex NO-GO"
findings). No architectural redesign, no EXE rebuilt, no installer built,
nothing committed as of this entry. Full test suite re-run clean:
**1407 tests, 0 failures, 0 errors.**

### Added

- **PAD production-configuration enforcement** (`fans/production_guard.py`,
  `fans/settings.py`) — `PAD_REQUIRED=True` alone did not actually enforce
  anything, since `verify_check_liveness`'s deny gate additionally requires
  `STRICT_PRESENTATION_ATTACK_CHECK=True` and
  `PRESENTATION_ATTACK_REVIEW_OR_DENY='deny'`. Production startup now
  hard-fails (refuses to start) if either of those is left in its "soft"
  state, or if `PHONE_SCREEN_SPOOF_THRESHOLD` is configured below a safe
  floor (0.10) — closing a path where a deployed server could silently run
  with presentation-attack detection reduced to logging-only.
- **Liveness challenge/session binding** (`verification/views.py`) —
  `verify_submit` now rejects a `LivenessTransaction` whose stored
  `challenge_direction` no longer matches the verification session's
  *current* challenge. A face-match retry rotates the session's challenge
  direction for the next attempt while keeping the same `session_id`;
  without this check, a token proven against a since-superseded challenge
  direction could still pass the (unchanged) session_id check.
- **`LivenessEvidenceReservation` model** (`verification/models.py`,
  migration `0032_liveness_evidence_reservation`) and
  `check_and_reserve_liveness_evidence()` (`verification/views.py`, guarded
  by a new in-process `_liveness_replay_lock`) — the perceptual-hash
  (`evidence_phash`) replay scan and the reservation of new evidence are now
  a single atomic step, closing a race where two concurrent
  transformed/recompressed replay submissions of the same evidence could
  both pass the Hamming-distance duplicate scan before either had written
  anything, and both would have gone on to receive a valid liveness token.

### Fixed

- **Migration `0031_liveness_tx_context_binding` upgrade safety** — added a
  `dedupe_evidence_hashes` data-migration step that runs before the
  `UniqueConstraint`s on `evidence_hash`/`evidence_pixel_hash` are applied.
  Any pre-existing duplicate non-blank hash value (the oldest row keeps it,
  later duplicates have only that field blanked — no row is ever deleted)
  is resolved first, so the migration can no longer fail outright against
  an already-populated database.

### Notes

- Pre-existing replay-protection layers (`evidence_hash` exact-byte
  SHA-256, `evidence_pixel_hash` decoded-pixel SHA-256, `evidence_phash`
  perceptual dHash/Hamming-distance) were already in place before this
  patch and are unchanged in behavior — only the atomicity of the
  perceptual-hash check was fixed.
- See `docs/SECURITY-CHECKLIST.md` items 8.41–8.44 for the corresponding
  checklist entries.

---

## [Unreleased] — v2.1.16 Post-UAT Stabilization (2026-09-04)

Bug fixes and UI polish from a member/officer testing round on the installed
v2.1.16 build. Every issue was audited against the actual code (root cause
identified) before any change was made; no EXE/installer rebuilt this pass.

### Added

- **Professional HTML password-reset emails** (`templates/accounts/emails/otp_code.{html,txt}`,
  `templates/accounts/emails/password_changed.{html,txt}`) — navy/gold FANS-C
  branding, large OTP display box, expiry/one-time-use/security notices, and a
  plain-text fallback, sent via `EmailMultiAlternatives`. `accounts/otp.py`'s
  OTP generation, expiry, resend cooldown, rate limiting, anti-enumeration,
  and audit logging are unchanged — presentation only.
- **`SCHEDULED` distribution-event status** (`beneficiaries/views.py`,
  `templates/dashboard/index.html`) — distinct from `OPEN`, shown when a
  payout event is active for today but outside its daily claiming time
  window, with the opening time displayed.
- **`PresentationAttackDetector.classify_denial()`** (`verification/pad.py`)
  — differentiates an attack-like PAD denial from an environment-like one
  (isolated glare) so the user sees "Possible presentation attack detected"
  vs. "Unable to verify liveness due to lighting or camera conditions"
  instead of one generic message for both.

### Fixed

- **Dashboard/Verify-page active-event inconsistency (functional bug).** The
  dashboard's "Current Distribution Event" card used
  `StipendEvent.get_active_event_for_date()` (date-only) while the Verify
  page used `get_open_events_now()`/`is_within_time_window()` (date + daily
  time window), so an event outside its daily claiming window could show
  "OPEN" on the dashboard while Verify correctly refused to start a claim.
  The dashboard now also checks `is_within_time_window()`. The actual claim
  gate in Verify was not changed — no restriction was bypassed.
- **Liveness/PAD false-rejection on real users under bright lighting.**
  `verification/pad.py::_check_specular_glare`'s composite scoring gave
  isolated glare (bright lighting, glasses reflections, an overexposed
  webcam on a real face) the same high-confidence weight as an actual
  phone/screen surface, letting it single-handedly cross
  `PHONE_SCREEN_SPOOF_THRESHOLD` (0.40). The high weight now requires
  corroborating `screen_flatness`; isolated glare gets a new, lower
  `specular_glare_uncorroborated` weight (0.35) that cannot cross the
  threshold alone. All other PAD signals (near-duplicate frames,
  static-sequence, flatness+sharpness combo) are numerically unchanged.
- **Deactivate/Suspend reason entry** — replaced a native browser `prompt()`
  popup with a Bootstrap modal (`templates/accounts/user_list.html`), same
  ≥5-char validation and same `reason` field consumed by
  `accounts/views.py::user_set_status` (permission check, audit logging
  unchanged).
- **Notification dropdown overflow** — fixed 340–380px width and no
  text-wrap protection on `n.title`/`n.message` caused long notification
  text to overflow the dropdown on narrow viewports. New
  `.notification-item` CSS classes replace the inline-styled markup in
  `templates/base.html`.
- **Forgot-password link visibility** — the login page's "Reset it with an
  email code" / "Request admin help" links were tiny inline-styled text with
  poor tap targets; restyled as two clearly labeled, full-width buttons.

### Changed

- **Responsive layout** — added a global `overflow-x` safety net
  (`html,body` in `static/css/main.css`) as defensive insurance against
  horizontal overflow; the existing, heavily-measured 992–1679px navbar
  breakpoint system from prior UX passes was deliberately left untouched.
- **Installer first-run wizard** (`dev/launcher.py`) re-themed to the
  FANS-C navy/gold identity — shared brand color constants, a `ttk.Style`
  progress bar, a branded two-line header on the first setup window, and
  matching colors on the Technical Administrator account and Email OTP
  Configuration windows. Cosmetic only — no change to the setup steps,
  certificate/Task Scheduler/`.env`/account-creation logic.

### Audited, no change needed

- **Duplicate-face notification system** — creation (fires exactly once per
  newly created `Beneficiary` row, cannot double-fire on refresh/re-login),
  data accuracy (name/ID/similarity all sourced live, not stale), the
  read/dismiss/resolve lifecycle, and role-gated visibility (President/
  Admin/IT, consistent with IT's documented "broad read, no financial
  authority" access level) were all audited against the actual code and
  found correct.

---

## [Unreleased] — Post-UAT Hardening Pass (2026-08-30 → 2026-09-02)

Builds on v2.2.0. Not yet packaged into a numbered installer build. Full
root-cause writeups: `docs/POST-UAT-FIX-REPORT.md` (16 issues, commit
`95c55bb`) and `docs/POST-UAT-FOLLOWUP-FIX-REPORT.md` (6 issues, commit
`abd3922` and `d989f6a`). Commits in this range: `abd3922`, `95c55bb`,
`d989f6a`.

### Added

- **Self-service email OTP password reset** (`accounts/otp.py`,
  `PasswordResetOTP` model): hashed codes, rate limiting, resend cooldown,
  expiry, single-use, anti-enumeration, audit logging.
- **`Beneficiary.STATUS_DISAPPROVED`** — distinct from `STATUS_INACTIVE`,
  with a backfill migration reclassifying legacy rejections.
- **Organization chart tiering** — `OfficerPosition.level` field separates
  chart tier (equal-rank siblings render side-by-side) from `order` (sort
  tiebreaker only); previously `order` served both purposes, which
  structurally prevented any two positions from ever sharing a tier.
- **Per-verification `session_id` guard** — `verify_start()` issues a
  fresh `session_id`; `verify_check_liveness` and `verify_submit` both
  refuse to evaluate on a mismatched or missing `session_id`. Closes a
  cross-tab session-collision path (see Fixed, below).
- **Representative face duplicate detection** — `check_duplicate_face()`
  now includes `RepresentativeFaceEmbedding`, surfacing
  representative-sourced matches with `source='representative'`.
- **Automatic 48-hour approval reminders** (`sync_approval_reminders()`),
  called from the admin dashboard; failures are logged, non-fatal.
- **Face-update re-enrollment duplicate check** — re-enrollment now runs
  the same cross-beneficiary duplicate check as initial registration.
- **Capture-quality gate** — rejects a face bounding box clipped by the
  camera frame edge instead of silently producing a degraded crop.
- Analytics: claim-progress, payout-completion, verification-results, and
  review-cases charts added (Executive/Operational/Security tabs).
- `beneficiary.get_full_name()` extended with `middle_name`/`suffix`.
- `duplicate_review_list.html` — badges reflect the biometric-source app.

### Fixed

- **CRITICAL — verification session-collision.** A shared login-session
  `verification_session` value was overwritten by every new
  `verify_start()` call; a stale, still-open browser tab for Beneficiary
  A could submit after a second `verify_start(B)` call and be evaluated
  against B instead of A. Fixed by the `session_id` guard above — both
  endpoints now refuse rather than silently reassigning. 37 pre-existing
  tests updated to supply `session_id`; 5 new tests
  (`VerifySubmitSessionCollisionGuardTest`) added.
- **Duplicate-face approval bypass** — the generic "Registration
  Applications" review queue had no awareness of
  `duplicate_review_required` and could approve straight past an
  unresolved duplicate-face conflict. Now excluded from that queue and
  hard-blocked (GET and POST) until resolved via Duplicate Face Review.
- **Duplicate conflict could bypass payout in an abnormal/legacy state**
  — `Beneficiary.is_eligible_to_claim` now also requires
  `not duplicate_review_required`, closing the case where a record is
  `ACTIVE` while still duplicate-flagged (e.g. pre-fix data).
- **Beneficiary Server Error (original report)** — an orphaned `{% endif
  %}` in `beneficiary/detail.html` (leftover from a removed conditional)
  caused a hard `TemplateSyntaxError` on every beneficiary detail page.
  Removed; 13 regression tests added.
- **Null-FK template crash class** (re-investigation of the above,
  found not to be the original page but the same defect class) — 13
  templates used `{{ X.get_full_name|default:X.username }}` on a
  nullable `SET_NULL` user FK without an `{% if X %}` guard, raising
  `VariableDoesNotExist` when legitimately `None`. All unguarded
  instances wrapped; already-safe instances left untouched.
- **Payout claiming window** could be bypassed for a same-day schedule
  created after the 07:00–20:00 office-hours window had ended.
- **President payout-approval notification** was missing for schedules
  awaiting approval past their scheduled date.
- **Duplicate-face notification** was not synchronized/resolved when the
  underlying case was decided; extended the same resolve-on-decision fix
  to shared-representative review and name/DOB override review.
- **Navbar overflow at common laptop resolutions (992–1439px)** — added a
  tightening media query; capped the user-badge name with ellipsis.
  *Not visually verified in a live browser this pass — see
  `docs/POST-UAT-FIX-REPORT.md` Phase 20.*
- **Multi-line `{# ... #}` Django template comments render as literal
  text** — switched to `{% comment %}...{% endcomment %}`; a pre-existing
  instance of this same bug in `templates/dashboard/index.html` (leaking
  into the upcoming-payout card) was also found and fixed.
- **Analytics correctness pass** (Phase A capstone checkpoint, full audit
  in `docs/ANALYTICS-METHODOLOGY.md`):
  - Template Match Analytics counted `NOT_VERIFIED`/`MANUAL_REVIEW`/`DENIED`
    attempts as template "wins" (including the anti-spoof sentinel
    `beneficiary_face_on_rep_claim` appearing as if it were a real
    template) — restricted to VERIFIED attempts only.
  - Re-enrollment-candidate heuristic had no minimum sample size; a single
    verified attempt on a non-primary template was enough to flag a
    beneficiary. Added a 4-attempt floor.
  - Security tab's "Fraud Alerts" case count included LOW-risk rows,
    inconsistent with the notification system's own rule that LOW is not
    alert-worthy — now counts MEDIUM+HIGH only.
  - Security tab claimed fraud-risk indicators "reflect the selected date
    range"; they actually use each rule's own fixed trailing lookback
    window and ignore the date filter — wording corrected.
  - Executive tab's "No payout scheduled today" showed even when an
    approved event was scheduled for today but outside its claiming time
    window — now distinguished from genuinely no event today.
  - Operational tab's daily verification/registration trend charts
    silently omitted zero-activity days, visually compressing real gaps
    into evenly-spaced adjacent points — now zero-filled for bounded
    date ranges.
  - An invalid date range (From after To) silently rendered an all-zero
    page with no explanation — now shows a warning banner.
  - Renamed "Fraud Risk Summary" to "Security Risk Indicators (Rule-Based)"
    and added explicit non-fraud-probability wording, to avoid overclaiming
    what a rule-based threshold crossing means.
  - 7 new/extended tests (`AnalyticsCorrectnessTest`,
    `TemplateAnalyticsTest`); full suite 911/911 passing.

### Changed

- Admin menu regrouped (Users & Access / Verification Management /
  Distribution / Monitoring / System) — link-preservation regression
  tested.
- Staff name fields: added `middle_name`/`suffix` across forms/templates.
- **Navbar-zoom UX refinement pass** — the navbar overflowed its container
  by up to ~190px at common laptop widths (1200–1536px), which is what
  forced the previously-reported 80%-zoom workaround; root-caused via a
  headless-browser measurement harness (not just source inspection) and
  fixed by: shortening the four longest nav labels ("Beneficiary
  Management"→"Beneficiaries", "Verify Claimant"→"Verify", "Audit &
  Verification"→"Audit & Logs", "Analytics & Reports"→"Analytics",
  "System Administration"→"Administration"); widening and deepening the
  laptop-width compact-nav breakpoint from 992–1439.98px to
  992–1679.98px; hiding the role pill below 1280px; and tightening navbar
  padding/font-size. Confirmed zero horizontal overflow at 1200, 1280,
  1366, 1440, 1536, 1600, 1680, and 1920px via automated screenshot
  capture (see below) — this closes the "Navbar-zoom … not confirmed in
  a live browser" item previously listed under Outstanding.
- Header branding simplified: navbar subtitle shortened from "Facial
  Verification System — Quezon City" to "Quezon City".
  Dashboard page subtitle shortened from "FANSC — Quezon City Senior
  Citizen Stipend Distribution — Overview" to "Senior Citizen Stipend
  Distribution".
- Icon audit: removed the 29 decorative per-item colored icons inside
  navbar dropdown menus (they didn't aid recognition — each item is
  already a full text label); kept the single icon per top-level nav
  item, card-header section icons, and the 3 profile-menu icons.
- Dashboard density pass: smaller KPI stat icons/numbers (52px/2rem →
  42px/1.55rem), tighter Quick Actions rows (34px icons/0.75rem padding →
  26px/0.5rem), tighter alert banners, table rows, and card spacing
  throughout the dashboard — reduces the page's total scroll height
  without shrinking body text or removing content.

### Verified, no change needed

- Payout/status wording, general UX wording — spot-checked against
  reported examples, already correct; not an exhaustive audit.
- **Beneficiary ID** — confirmed server-generated (`BEN-{year}-{seq}`),
  never a form field, DB-unique, independent of Senior Citizen ID. No
  defect found.

### Test suite

904/904 passing as of this entry (`python manage.py test`); `manage.py
check` and `makemigrations --check --dry-run` both clean. See
`docs/POST-UAT-FOLLOWUP-FIX-REPORT.md` for the 840→880 progression during
this pass; the count above reflects this repository audit's own
verification run.

### Outstanding before packaging a release build

- Org-chart tree-connector styling: code-level fix only, not confirmed
  in a live browser at 100% zoom. (Navbar-zoom, previously listed here,
  is now confirmed — see Changed, above.) Root cause confirmed this pass:
  every `OfficerPosition` in the live database has `level=0`, so the
  (correctly implemented, tested) tiering logic always produces a single
  tier — a data-configuration matter for an admin to fix via Officer
  Positions, not a template/CSS defect.
- Real SMTP OTP delivery: diagnosed this pass by triggering the actual
  `POST /accounts/password/forgot/` endpoint end-to-end (not the internal
  helper) against an account with a real, non-synthetic email address.
  FANS-C-side generation, audit logging, and SMTP handoff to Brevo all
  confirmed working (no exception, `email_sent=True`). Actual inbox
  receipt remains unconfirmed. Likely root cause identified:
  `DEFAULT_FROM_EMAIL` is configured to a `gmail.com` address, and mail
  claiming to be "from" a Gmail address but relayed through a non-Google
  SMTP server (Brevo) commonly fails Gmail's own strict SPF/DKIM/DMARC
  alignment on the receiving end — a delivery-stage issue external to
  this codebase, not an application defect. Not changed without explicit
  authorization.
- Physical camera verification flow: not re-tested with real hardware
  this pass.
- Not yet packaged into a version-bumped installer build.

### UX/UI + Analytics follow-up pass (2026-09-02, same day)

Continuation of the pass above; re-verified every item against the
*current* rendered UI (via a real headless-Chromium harness, not source
reading alone) rather than assuming prior "Fixed"/"Changed" entries were
still accurate.

- **Dashboard Recent Activity** further reduced from 10 to 5 rows
  (`beneficiaries/views.py`) — the "View All → Audit & Logs" link already
  existed, so only the query limit changed.
- **Chart empty states (real bug, not previously caught)** — 5 Chart.js
  charts (verification trend, registration trend, beneficiary growth,
  monthly distribution, verification results) rendered a fully blank
  canvas with no message when their data array was empty, which reads as
  broken rather than "no data yet." Added a `showEmptyState()` helper
  (`static/js/analytics.js`) that shows a specific, honest message per
  chart instead.
- **Template Match Analytics made genuinely analytical** — the page was a
  bare table despite its title. `template_match_report`
  (`verification/views.py`) now also surfaces total matched attempts,
  distinct beneficiaries with a match, primary-template win rate, and the
  existing (previously unused on this page) `get_reenrollment_candidates()`
  signal, as real KPI cards above the original table (kept as supporting
  detail).
- **Analytics explanatory subtitles added** to Executive, Operational, and
  Security tabs (previously only Template Match had one), each describing
  in one sentence, without exaggeration, what the page measures and its
  time scope — Security's subtitle reuses the existing "view/flag-only,
  no automatic action" language rather than restating it differently.
- **Accessibility — label/`for` association completed project-wide.**
  The prior pass explicitly left "report filter bars" and "several admin
  review/override forms" unfixed. A full scan of every template found the
  gap was wider than that: ~50 unlabeled fields across 20 templates
  (report filter bars, audit-log/beneficiary-list filters, all
  manual-review/override/registration-review notes fields, the admin
  Add/Edit User form, both password-change forms, the shared Analytics
  date-range filter, and payout-detail's three same-named
  `override_reason` textareas — given distinct ids since more than one
  can be present in the same rendered page). All fixed; each was
  spot-verified with a real click on the label text moving keyboard focus
  to its field, not just markup inspection. A small number of `<label>`s
  with no `for` were confirmed to be legitimate radio-group headings or
  static (`form-control-plaintext`) read-only displays and correctly left
  as-is.
- Keyboard focus visibility on the navbar was checked and found already
  correct — Bootstrap's default `:focus-visible` ring is present but
  fades in over a 150ms CSS transition, which produced a false negative
  in an initial screenshot taken at the instant of the keypress; a
  settled screenshot confirms the ring renders correctly.
- Re-ran the complete test suite after all of the above: 904/904 passing;
  `manage.py check` and `makemigrations --check --dry-run` both clean.

### Final UX/UI + Analytics correction checkpoint (2026-09-02)

A second, independent real-user visual review of the two passes above
found three remaining gaps. This checkpoint addresses only those three —
everything else already reviewed above (nav-label shortening, muted
dropdown icons, dashboard density, Template Match Analytics KPIs,
analytics subtitles, label/`for` accessibility) was re-checked and left
as-is.

- **Header identity was reduced too far.** The prior pass's navbar-brand
  simplification (`Facial Verification System — Quezon City` →
  `Quezon City`) fixed the overflow but also dropped the system name from
  the header entirely. Restored a three-tier `FANSC` / `Facial
  Verification System` / `Quezon City` hierarchy (`templates/base.html`,
  `static/css/main.css`), but only where there is room for it: the
  middle line is shown at wide desktop (≥1680px) and on the
  collapsed/mobile navbar (<992px), and is hidden specifically inside the
  992–1679.98px "no room to spare" band the previous pass identified —
  reusing that band's existing breakpoint rather than inventing a new
  one, so the overflow fix from the prior pass is not disturbed.
- **`Privacy & Data Consent` was the one dropdown item left with no
  icon.** The prior pass's icon de-colorization left every other item in
  the Administration menu with a muted icon and, inconsistently, removed
  this one's icon outright (`bi-spacer` placeholder). Restored
  `bi-shield-check` in the existing muted style for consistency with its
  siblings.
- **Dashboard "Recent Activity" card had disproportionate empty space.**
  It used `h-100` to match the height of the taller Quick
  Actions/Beneficiary Status/Network Access column beside it, so with
  only 5 rows of content (already reduced from 10 in the prior pass) the
  card rendered with a large empty area below the table. Removed `h-100`
  (`templates/dashboard/index.html`) so the card sizes to its actual
  content instead of stretching to match its sibling column.

Verified with a real headless-Chromium session (Playwright, already
present in the environment) against a dedicated throwaway admin account
created and deleted within this session — not source inspection alone:
screenshots at 1920/1680/1440/1366/1200/768px on Dashboard, Beneficiaries,
Verify, Distribution (Stipend Schedule), Audit Logs, Analytics Executive,
Template Match Analytics, and Staff Accounts showed zero horizontal
overflow at any width; the display-name fallback rendered a real
first/last name ("Maria Santos") rather than falling back to username;
and the three fixes above rendered as intended. `manage.py check` and
`makemigrations --check --dry-run` both clean; full test suite re-run:
904/904 passing, no changes needed to any test.

### Phase A final acceptance correction (2026-09-02, same day)

A formal Phase A acceptance check on the entry above found the header fix
had been more conservative than it needed to be: hiding the
`Facial Verification System` middle line across the *entire*
992–1679.98px band meant it was actually missing at every "normal"
desktop/laptop width (1200/1280/1366/1440/1536), not just the tightest
ones — the opposite of the "meaningfully visible at normal desktop target
widths" requirement.

Re-measured with a headless-Chromium harness: with the middle line forced
visible at every width, the navbar's real content width is a constant
1126px, and it only overflows its container below that — confirmed
empirically at 992/1000/1024/1050/1080/1100px (all overflow) vs.
1150/1180/1200px (all clean). The previous 992–1679.98px hide rule was
never actually load-bearing above ~1126px; it had just never been
re-measured after the underlying spacing/label-shortening fixes closed
most of the original overflow gap.

Narrowed the hide rule to the width band actually shown to overflow
(`static/css/main.css`): `.brand-sub-system` now hides only in
992–1149.98px (a new, narrower media query) instead of 992–1679.98px. All
other 992–1679.98px spacing/compaction rules (nav-link padding, brand
padding, user-badge sizing, role-pill hiding below 1280px) are unchanged.

Re-verified with the same live-Chromium method across every explicitly
required width — 1920/1680/1536/1440/1366/1280/1200/900/768px — recording
`clientWidth`, `scrollWidth`, overflow, and tier-by-tier visibility for
each: zero overflow at any width, and `FANSC` / `Facial Verification
System` / `Quezon City` all visible from 1150px up through 1920px and
again on the collapsed mobile navbar (<992px); only the narrow
992–1149.98px sliver still shows `FANSC` / `Quezon City` only. Also ran a
regression sweep (8 representative pages × 5 widths = 40 checks) to
confirm the narrower band didn't disturb anything else sharing
`base.html`/`main.css`: zero overflow hits. `manage.py check` and
`makemigrations --check --dry-run` both clean; full test suite re-run
after this change: 904/904 passing (`Ran 904 tests in 408.701s — OK`).

---

## [2.2.0] — 2026-08-29 (Analytics, Intelligence, Notification, UX, and Workflow Improvement Release)

### Summary

Builds on the v2.1.18 hardened baseline. Expands the notification system,
fixes two real bugs found during the release audit (a payout-labeling gap
on the dashboard and a plain-HTTP-fallback refresh-logout bug), refines UX
wording, reorganizes the admin menu, expands analytics with two new
Executive-tab charts, moves fraud detection to an explicit 0–100 risk
score with two new signal types, and re-confirms (does not change) the
existing password-recovery and face-verification-intelligence design.
No verification/authentication/backup core logic was rebuilt — this is an
expansion release, not a rewrite.

### Added

- **Notifications**: `Notification.category` expanded from 4 to 7 values
  (`APPROVAL_REQUIRED`, `APPROVAL_REMINDER`, `VERIFICATION_REVIEW`,
  `FRAUD_ALERT`, `SECURITY_ALERT`, `PASSWORD_RESET_REQUEST`,
  `SYSTEM_ALERT`); new `priority` field (LOW/MEDIUM/HIGH). New
  `sync_approval_reminders()` fires one 48-hour reminder per pending item.
  New SECURITY_ALERT notification on login lockout; new SYSTEM_ALERT
  notification on backup failure.
- **Payout workflow**: `StipendEvent.late_approval_reason` — approving a
  stipend event after its scheduled date now requires a 10+ character
  reason, stored on the event and recorded in the audit log
  (`late_approval: true`).
- **Session hardening**: `fans.middleware.DynamicCookieSecurityMiddleware`
  — strips the `Secure` cookie flag per-request, but only for requests
  that genuinely arrive over plain HTTP. The HTTPS path is byte-for-byte
  unchanged.
- **Analytics**: Executive tab gains two charts, `beneficiary_growth` and
  `monthly_distribution` (12-month trend, `TruncMonth`-based).
- **Fraud / Security Review**: two new signal types,
  `repeated_login_failures()` (by IP) and `payout_anomalies()` (by staff),
  alongside the three existing signals — five total. All five now report
  an explicit 0–100 `_risk_score()` banded LOW 0–30 / MEDIUM 31–70 /
  HIGH 71–100.
- `docs/FACE-VERIFICATION-INTELLIGENCE-AUDIT.md` — documents matching
  method, confidence thresholds, liveness/quality gates, duplicate
  detection, and mismatch handling; explicitly records why no
  age-estimation model was built (accuracy risk for elderly faces,
  unverifiable-provenance concern for a government biometric system) and
  confirms the existing appearance/score-drift advisory signal already
  satisfies "flag for review, never auto-reject."

### Fixed

- **Dashboard "Next Payout" mislabeling**: `beneficiaries/views.py`
  `upcoming_events`/`next_event` previously filtered only by `is_active` +
  date, so a *pending, unapproved* stipend event rendered identically to a
  confirmed one. Now filtered by `approval_status=APPROVED`; a separate,
  clearly-marked "awaiting approval" notice covers the pending case.
- **Refresh-logout on the plain-HTTP LAN-IP fallback path**: caused by
  `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE` being global settings, so a
  Secure-flagged cookie issued to a plain-HTTP session was silently
  dropped by the browser on the next request. Fixed by
  `DynamicCookieSecurityMiddleware` (see Added).
- Blank-avatar display bug in `templates/beneficiaries/list.html` — the
  table cell rendered empty when a beneficiary had no profile photo;
  `detail.html` already had the icon-placeholder pattern, `list.html` did
  not.

### Changed

- **UX wording**: "Fallback" (verification-method sense only — the
  ID-check path) renamed to "Manual Verification" across ~13 templates
  and 2 model `choices` labels. The unrelated "Fallback Access (HTTP)"
  network-mode wording was deliberately left untouched. "Fraud Signals"
  renamed to "Security Review" throughout.
- **Admin menu**: the System Administration dropdown's 21 existing links
  regrouped into 5 categories — User Management, Verification,
  Distribution, Monitoring, System. No link removed (regression-tested).
- Version bumped 2.1.18 → 2.2.0 across `dev/installer/fans_c.iss` and
  `dev/version_info.txt`.

### Not changed

- Core face-matching, liveness/PAD, and duplicate-detection logic —
  re-audited, not rebuilt. No age-estimation or auto-reject-by-age code
  exists anywhere (re-confirmed by fresh grep this release).
- Password-recovery architecture — re-audited, already satisfied every
  v2.2.0 requirement; `docs/PASSWORD-RECOVERY-ARCHITECTURE.md` gained a
  short confirmation note only.
- `scripts/admin/daily-backup.ps1` and backup/restore tooling — unchanged.
- No PyInstaller spec or Inno Setup `[Files]` changes were required for
  this release — only the version-number bump. Confirmed by enumerating
  every changed/new file against the existing bundled directory trees.

---

## [2.1.17] — 2026-08-28 (Packaging fix: restore tooling was missing from the installer)

### Summary

Phase 14 (restore/disaster-recovery tooling, added in the v2.1.16 hardening
work) added `scripts/admin/verify-backup.ps1` and
`scripts/admin/restore-backup.ps1`, but a clean-VM deployment audit found
they were never added to the Inno Setup `[Files]` section — a real v2.1.16
install had no way to reach them. This release adds the two missing
packaging entries. No backup/scheduler/security logic changed;
`daily-backup.ps1` is untouched.

### Changed

- `dev/installer/fans_c.iss`: added `[Files]` entries for
  `verify-backup.ps1` and `restore-backup.ps1` (installed to
  `{app}\scripts\admin\`, same pattern as the existing `daily-backup.ps1`/
  `uninstall-clean.ps1` entries). No uninstall-side changes were needed —
  Inno Setup's generated uninstaller automatically tracks and removes every
  file placed via `[Files]`.
- Version bumped 2.1.16 → 2.1.17 across `dev/installer/fans_c.iss` and
  `dev/version_info.txt`, for full installer/executable version-identity
  consistency (the exe's own embedded FileVersion/ProductVersion also
  moved to 2.1.17.0, even though the application code inside is otherwise
  identical to 2.1.16).

### Not changed

- `scripts/admin/daily-backup.ps1` — zero-byte diff from 2.1.16.
- Backup, rotation, scheduler, ACL, and SystemConfig behavior — unchanged.
- No new migration.

---

## [2.1.16] — 2026-08-28 (Backup/scheduler hardening + clean-VM deployment validation)

### Summary

Multi-pass hardening of the automated daily backup and scheduled-task
registration paths, followed by a full clean-VM (VirtualBox, Windows 11
Enterprise Evaluation) deployment validation of the built installer.
Biometric/verification/payout logic, authentication, authorization, and
PostgreSQL/SQLite application support are unchanged by this release.

### Backup hardening (`scripts/admin/daily-backup.ps1`)

- **Collision/concurrency safety:** an exclusive OS-level lock
  (`backups\.daily-backup.lock`) rejects a second overlapping invocation
  (exit code 3) instead of racing it; `Get-NextFansBackupDestination` picks
  a collision-free destination (`_2`..`_999` suffix for a same-minute
  retry) so a run can never reuse or overwrite a previous attempt.
- **Restore-readiness validation:** `Test-FansBackupRestoreReady` (mirrored
  in `fans/views.py` as `_is_fans_backup_restore_ready`) no longer trusts
  `status=complete` alone — it also requires the recorded integrity/env
  state, physical presence of `db.sqlite3`/`.env`, and an exact match
  between the manifest's `db_backup_bytes` and the live file size. Both
  rotation and the System Health page use this identical predicate.
- **`db_backup_bytes` type/range contract:** validated as a genuine,
  non-Boolean JSON integer in `1..2^63-1` on both the PowerShell
  (`Test-FansBackupDbBackupBytes`) and Python (`_is_valid_db_backup_bytes`)
  sides — no coercion from strings, floats, or booleans.
- **Retry-suffix canonicalization:** only `_2` through `_999`, no leading
  zeros, is accepted as a valid same-minute-retry suffix on both the
  PowerShell and Python sides; malformed/oversized suffixes are rejected
  without risk of integer overflow.
- **SQLite `.backup` path quoting:** the bundled `sqlite3.exe`'s
  dot-command tokenizer cannot safely embed a destination path containing
  both a space and an apostrophe (e.g. `C:\Citizen's Apps\FANSC`) no matter
  how it's quoted. `Invoke-FansSqliteBackup` now sets the sqlite3.exe child
  process's working directory to the destination folder and references it
  by a bare filename instead, sidestepping the tokenizer entirely.
- **ACL identity:** the Administrators+SYSTEM-only backup-folder ACL is now
  built from well-known SIDs (`S-1-5-32-544`, `S-1-5-18`) rather than
  localized account names.
- **Operational logging contract:** a required-log write failure can no
  longer result in a false "success" — exit code 4 signals "backup/rotation
  clean, but the operational log itself could not be written."
- **Exit-code contract:** `0` clean success, `1` required backup failure,
  `2` valid backup with rotation failure, `3` overlapping invocation
  skipped, `4` valid data path with required-log failure.

### Scheduler hardening

- `dev/launcher.py`: `_step6_autostart()` now returns success/failure and
  first-run setup aborts (via the existing `_fatal()` path) before starting
  services if a required scheduled task failed to register, instead of
  silently continuing.
- Both `dev/launcher.py` and `scripts/setup/setup-autostart.ps1` register
  the `FANS-C Daily Backup` task via `Register-ScheduledTask -Force`
  directly, with no preceding `Unregister-ScheduledTask` — a failed
  re-registration can no longer delete a known-good existing task.

### SystemConfig / Django Admin

- `SystemConfig.CONTROLLED_KEYS` (renamed from `SENSITIVE_KEYS`) now covers
  all six keys with a dedicated audited application workflow
  (`verification_threshold`, `auto_verify_threshold`, and the four
  `auto_approve_*` toggles) — Django Admin can no longer create, change,
  delete, or rename-bypass any of them.

### Packaging

- Version bumped 2.1.15 → 2.1.16 across `dev/installer/fans_c.iss` and a
  newly added `dev/version_info.txt` (wired into `dev/fans_c.spec`), giving
  `fans_c.exe` a proper Windows FileVersion/ProductVersion for the first
  time (previously unset in every prior release).
- One authorized migration: `accounts/migrations/0010_...py`
  (`AlterModelManagers` + two `help_text`-only `AlterField` ops — no
  schema/data change).

### Clean-VM deployment validation (2026-08-28)

Performed on a disposable VirtualBox VM (Windows 11 Enterprise Evaluation),
installed from the built `FANS-C-Setup-v2.1.16.exe`, not the dev machine.

**PASS (runtime-confirmed):** installation and packaged-file placement;
first-run setup; all three scheduled tasks registered with correct
settings; SYSTEM-context backup execution (exit 0, manifest/integrity
confirmed); backup folder ACL (SYSTEM+Administrators full control,
non-admin account denied access); overlap protection (exit 3, verified
lock message); missing-dependency failure handling (exit 1); rotation
(retention cap, invalid-suffix protection, disposable-fixture-only test);
media backup; application smoke test (HTTPS, login, dashboard, health
page, `/admin/` authorization boundary); session/logout enforcement;
biometric runtime resources load without error.

**UNVERIFIED (explicitly, not failed):**
- Scheduler idempotence — v2.1.16 has no supported lightweight
  "repair tasks" path in the installed product; re-triggering registration
  requires a full first-run reset (new `.env`/keys), which was intentionally
  not performed on the already-evidenced validation VM.
- Exit code 2 (rotation failure) — code path identified and reviewed
  (`Invoke-FansBackupRotation`'s `Remove-Item` failure branch); runtime
  fault injection (file lock, then an NTFS deny ACE) did not reliably
  reproduce it on this VM.
- Django Admin read-only hardening (`VerificationAttempt`/`FaceEmbedding`/
  `ClaimRecord`/`AuditLog`) — no supported packaged-product path exists to
  obtain a staff/superuser account on a clean install; covered by the
  existing automated test suite but not by clean-VM runtime evidence.

**Not yet performed:** uninstall, reinstall, and restore-drill validation
(remain pending on the same disposable VM).

---

## [2.1.13] — 2026-05-27 (Critical: PAD over-rejects real users + FaceNet false-accept risk + Manual Review wording)

### Summary

Two critical findings during v2.1.12 manual QA forced this patch. **FaceNet
is unchanged and remains the final identity verification engine. PAD/liveness
remain only a gate before FaceNet.**

### Critical fixes

- **Issue 1 — Real-person liveness false-rejected as "near-duplicate /
  possible phone screen / replay" attack.** Root cause: the v2.1.11 pixel
  heuristics (`_check_near_duplicate_frames`, `_check_sequence_static`) are
  unreliable for real webcam captures because a real person making a brief
  turn-and-return can produce visually similar frames at the 600 ms
  sampling instants. The heuristics flagged them as replay/static.
  **Fix:** PAD `analyze_sequence()` now takes a `landmark_motion_ok`
  argument. When the client's FaceMesh confirms head motion above
  `PAD_LANDMARK_MOTION_MIN_DEG` (default 2.0°), the pixel-level
  near-duplicate and static-sequence signals are suppressed to zero.
  Texture signals (glare, flatness, sharpness) are unaffected — a phone
  screen / printed photo / video replay still hits those gates.
- **Issue 2 — Wrong-person / baby-photo / low-quality captures could
  auto-verify at FaceNet score ~0.82–0.83 (above the 0.75 lower
  threshold).** This was a release-risk false-accept. Root cause: the
  decision policy was a single-zone "score >= threshold → VERIFIED". On
  webcam-quality captures, FaceNet can produce 0.80–0.86 similarity even
  for the wrong person. **Fix:** new three-zone decision band wired into
  `verify_submit`:
  - `score >= AUTO_VERIFY_THRESHOLD` (default **0.88**) → VERIFIED (auto-release)
  - `VERIFICATION_THRESHOLD <= score < AUTO_VERIFY_THRESHOLD` → MANUAL_REVIEW
  - `review_band <= score < VERIFICATION_THRESHOLD` → MANUAL_REVIEW (low band)
  - below → NOT_VERIFIED

  New `SystemConfig.get_auto_verify_threshold()` classmethod + DB-backed
  override (`auto_verify_threshold` config key). Settings: `AUTO_VERIFY_THRESHOLD=0.88`,
  `DEMO_AUTO_VERIFY_THRESHOLD=0.80`. The observed 0.823 baby-photo case
  is now routed to MANUAL_REVIEW (release blocked) instead of
  auto-VERIFIED.

  **Additional safeguard:** when face quality is low, any score in the
  VERIFIED zone is downgraded to MANUAL_REVIEW. Controlled by
  `LOW_QUALITY_FORCES_MANUAL_REVIEW=True` (default).

- **Issue 3 — Manual Review UI looked too much like a passed verification.**
  The result page still showed "LIVENESS PASSED" prominently next to a
  high similarity score, which could mislead operators into thinking the
  attempt was approved. **Fix:** result page now shows for MANUAL_REVIEW:
  - Banner: "MANUAL REVIEW — RELEASE BLOCKED"
  - Decision breakdown card with three explicit lines:
    - *Liveness:* Passed (live-face gate only — not an identity match)
    - *Identity match:* Manual review required
    - *Release status:* **BLOCKED pending administrator review**
  - Similarity score meter is now tinted by DECISION (warning for
    manual review, success only for VERIFIED) and shows both threshold
    markers — the lower threshold and the auto-verify line.
  - IT debug panel updated to document the three-zone band.

### Files changed

- `verification/pad.py` — `analyze_sequence(frames, landmark_motion_ok, landmark_motion_debug)`;
  pixel signals suppressed when landmark motion confirmed.
- `verification/views.py` — wired `landmark_motion_ok` to PAD; new
  three-zone decision logic in `verify_submit`; `verify_result` and
  `verify_config` pass `auto_verify_threshold` to templates.
- `verification/models.py` — `SystemConfig.get_auto_verify_threshold()`
  classmethod with DEMO_MODE-aware default.
- `fans/settings.py` — `AUTO_VERIFY_THRESHOLD=0.88`,
  `DEMO_AUTO_VERIFY_THRESHOLD=0.80`, `LOW_QUALITY_FORCES_MANUAL_REVIEW=True`,
  `PAD_LANDMARK_MOTION_MIN_DEG=2.0`.
- `templates/verification/result.html` — RELEASE BLOCKED banner;
  decision breakdown card; auto-verify marker on score meter;
  IT debug panel three-zone documentation.
- `dev/installer/fans_c.iss` — version 2.1.12 → 2.1.13.
- `verification/tests.py` — `PADLandmarkMotionGateTest` (4),
  `AutoVerifyThresholdBandTest` (5), `VerifySubmitThresholdBandPolicyTest` (5),
  `ManualReviewUIWordingTest` (4) = **18 new tests**. One existing
  `LivenessProofBindingTest` adjusted to use `AUTO_VERIFY_THRESHOLD=0.80`
  so its 0.85 score still lands in VERIFIED for the test's intent.

### Validation

- `python -m compileall accounts beneficiaries verification fans logs` — no errors.
- `python manage.py check` — 0 issues.
- `python manage.py test verification accounts beneficiaries logs` —
  **446 tests pass, 0 failed** (was 428 in v2.1.12; +18 v2.1.13 tests).

### Honest limitations (kept and re-stated)

- The local heuristic PAD detector is not a trained CNN. A
  high-quality replay device under good ambient lighting may still
  pass the heuristics. The strict head-movement liveness gate, the
  texture-based PAD signals, and the new three-zone FaceNet decision
  band combine to push the residual risk into MANUAL_REVIEW where a
  human must approve before release.
- `AUTO_VERIFY_THRESHOLD=0.88` is calibrated for typical webcam
  captures. Operators may tune it via `SystemConfig` (DB) or env
  override. A higher value reduces false-accepts but increases
  manual-review volume.
- FaceNet does not distinguish age in cosine space — a "baby photo"
  passed off as a senior is rejected not because FaceNet detects age
  mismatch, but because the score will not reach 0.88 against the
  enrolled senior's embedding, AND because low-quality / lookalike
  routes still trigger MANUAL_REVIEW where a human can catch the
  mismatch by visual inspection.

---

## [2.1.12] — 2026-05-27 (Liveness elapsed-scope fix + President role uniqueness + Officer Assignment integration)

### Summary

Three urgent fixes after v2.1.11 testing. No FaceNet replacement — FaceNet
remains the final identity verification engine. PAD/liveness remain only a
gate before FaceNet.

### Fixed

- **Issue 1 — Real-person liveness stuck at "Processing liveness proof…".**
  Root cause: `static/js/verify.js` declared `let elapsed = 0` inside the
  challenge `setInterval` scope and later referenced it OUTSIDE that scope
  when building the proof payload (`challenge_duration_ms: elapsed`). That
  threw a `ReferenceError`, the proof POST never completed, no `tx_token`
  was issued, and the UI stayed at "Processing liveness proof…" with
  liveness score 0%. Fix: hoisted `challengeElapsedMs` (and a
  `challengeStartTs` fallback) to the outer scope before the Promise, used
  it from inside via `const elapsed = challengeElapsedMs`, and referenced
  the hoisted value in the proof payload. Mode-B request is now wrapped in
  `try/catch/finally`; any frontend exception (`ReferenceError`,
  `TypeError`, network failure) zeroes the score, clears the token, hides
  the processing overlay, and surfaces the real failure reason to the
  operator. If the challenge completes before the backend minimum of 3
  sequence frames is collected, the frontend tops up the buffer (up to
  1.5 s) before sending proof. The failure card now displays the actual
  no-token reason returned by the server rather than the generic
  "Head movement not detected" message.
- **Issue 2 — President System Role could be created multiple times.**
  Added `CustomUser.active_president_exists(exclude_pk=...)` model helper.
  `UserCreateForm`, `UserUpdateForm`, `UserCreateFullForm`,
  `UserEditFullForm`, and `CreateAdminForm` now all call
  `_validate_president_uniqueness(...)` in `clean_role()`. The canonical
  error message is "Only one active President account is allowed.
  Deactivate or change the current President first." Inactive/suspended
  President accounts are excluded from the check so they remain for
  history/audit; only active accounts count. The role label is updated to
  "System Role / Access Role" with help text "This controls software
  permissions, not the organization chart position." The user list
  template header was updated from "Role" to "System Role".
- **Issue 3 — Creating a President user did not appear in Org Chart /
  Officer Assignments.** Implemented Option A (integrated assignment
  flow). `UserCreateFullForm` and `UserEditFullForm` now expose three
  extra fields: `officer_position` (dropdown of active positions),
  `officer_start_date`, `officer_is_current`. On save the form
  creates/updates the linked `OfficerAssignment`. When the edited user
  already had a current assignment, switching position closes the old one
  and creates a new one; clearing the position closes the existing
  assignment; changing only `start_date` / `is_current` is applied in
  place. If the selected position is unique and another active officer
  holds it, the form raises a non-field `ValidationError` with the
  position name. End-to-end: after saving, the user shows on
  `/accounts/users/`, on `/accounts/officer-assignments/`, and in the
  Organization Chart at `/accounts/org-chart/` immediately.

### Tests added

- `accounts.tests.PresidentRoleUniquenessTest` — 8 tests covering the
  model helper, all forms (create/edit/full), and an editing-the-same-user
  pass-through case.
- `accounts.tests.PresidentRoleFirstCreationTest` — first active President
  creation succeeds.
- `accounts.tests.PresidentOfficerAssignmentTest` — 7 tests covering
  officer-assignment creation, position-only edits, unique-position
  duplicate blocking, org-chart smoke, officer-assignments list smoke,
  and a DB-level multi-holder baseline.
- `accounts.tests.SystemRoleLabelTest` — 3 tests for the new label and
  help text.
- `verification.tests.LivenessElapsedScopeFixTest` — 6 tests that grep
  `static/js/verify.js` to assert the hoisted variable is in place, the
  buggy inner `let elapsed = 0` is removed, the proof payload references
  the hoisted value, `try/finally` clears the processing overlay, and
  the seq-frame top-up logic is present.
- `verification.tests.LivenessFrontendErrorHandlingTest` — 3 tests for
  exception capture, real-reason display, and immediate-zero-score on
  no-token failure.

### Files changed

- `static/js/verify.js` — hoisted `challengeElapsedMs`, added
  `challengeStartTs` fallback, `try/catch/finally` around the Mode-B
  proof POST, seq-frame top-up before the POST, real-reason failure
  card, frontend-error path that clears state.
- `accounts/models.py` — added `CustomUser.active_president_exists()`
  classmethod.
- `accounts/forms.py` — added `PRESIDENT_UNIQUE_ERROR` and
  `_validate_president_uniqueness()`; added `clean_role()` to all five
  user-management forms; added `officer_position`,
  `officer_start_date`, `officer_is_current` fields and
  `_save_officer_assignment()` / `_sync_officer_assignment()` hooks to
  `UserCreateFullForm` and `UserEditFullForm`; relabelled `role` to
  "System Role / Access Role".
- `accounts/views.py` — `user_create_full` and `user_edit_full` now
  call the form's officer-assignment hook and include the chosen
  position name in the audit log details.
- `accounts/tests.py` — new tests (above).
- `templates/accounts/user_list.html` — column header "Role" →
  "System Role".
- `verification/tests.py` — new JS-regression tests (above).
- `CHANGELOG.md`, `README.md`, `SETUP.md`, `docs/SYSTEM-OVERVIEW.md`,
  `docs/SYSTEM-STRUCTURE.md`, `docs/RESEARCH-PAPER-GUIDE.md`,
  `dev/installer/fans_c.iss` — version + behaviour updates.

### Honest limitations (unchanged)

- The local heuristic PAD detector is not a trained CNN. It catches
  static photos, A4 prints, simple phone-screen captures, video-loop
  replays held still, and near-duplicate frame sequences, but a very
  high-quality replay device under good ambient lighting may still pass
  the heuristics. Strict mandatory head-movement liveness + FaceNet
  identity match remain the canonical security gates.
- DB-level uniqueness for the President officer position is enforced
  through the form, not a SQL `UNIQUE` constraint. A future migration
  could add a partial unique index
  (`is_current=1 AND position_id=<president>`), but the form gate is
  sufficient for the current rollout.

### Validation

- `python -m compileall accounts beneficiaries verification fans logs` —
  no errors.
- `python manage.py check` — System check identified no issues.
- `python manage.py test verification accounts beneficiaries logs` —
  full suite green, including the new v2.1.12 tests.

---

## [2.1.11] — 2026-05-27 (Representative face hard-block + Schedule approval + UX/UI bundle)

### Summary

A bundled patch covering 10 issues raised in operator/security review.
No FaceNet replacement — liveness/PAD remain gates before FaceNet.

### Fixed

- **Issue 9 — Representative claim could approve senior's face.** `verify_submit` now refuses to verify when `claimant_type=representative` but the representative cannot be resolved, instead of silently comparing against the beneficiary's embedding. A secondary cross-probe denies when the live face matches the beneficiary on a representative claim. UI relabels the page to "Verify Representative Face" with a clear warning.
- **Issue 1 — Same name+DOB had no proper override path.** Added `DuplicateNameDobRequest` model + migration `beneficiaries/0012`, registration JS modal (Cancel / View Existing / Submit Override), and a President/Admin review queue (`/beneficiaries/namedob-override/`). Override-flagged records remain `PENDING` until reviewed.
- **Issue 2 — Repeated cancellations needed safeguards.** `payout_action` counts prior cancellations for the same beneficiary+event; after `CANCEL_REPEAT_PRESIDENT_THRESHOLD` (default 3) only President/Admin/IT may cancel, and a longer written reason is required. Override panel shows a clear repeated-cancellation notice. Reports already correctly excluded cancelled records from Released totals.
- **Issue 3 — Phone/photo spoof gate hardened.** `PresentationAttackDetector` weights raised (`screen_flatness` 0.30→0.55, `sharpness_texture` 0.25→0.50); combined flatness+sharpness boost added. `verify_submit` denies even when a valid TX exists if the TX PAD score crossed `PHONE_SCREEN_SPOOF_THRESHOLD`.
- **Issue 4 — IT role display.** Regression coverage extended: `get_role_display()` returns `IT` uppercase, internal DB value remains `it`.
- **Issue 5 — Schedules need President approval.** Added approval fields to `StipendEvent` + migration `verification/0019`. Admin-created schedules start in `pending_approval` and are NOT usable for claiming until the President approves. President-created schedules publish immediately. `get_active_event_for_date` only considers approved events.
- **Issue 6 — Admin Override Actions UI clarified.** Override panel rewritten with explicit "Use only for correction after review" warning, repeated-cancellation notice, per-field labels, required override reason.
- **Issue 7 — Liveness failed-retry button is now clickable.** Previously the verify button became `disabled=true` with "Retry Required" label and no way forward. Now becomes a clickable Retry that resets the capture flow.
- **Issue 8 — Audit logs UI overhauled.** Table shows summary fields with badges and status chips; new View Details modal with structured sections (Basic, Verification Result, Reason, Related IDs, Raw Technical Details for IT/President). Filters added: date range, decision, beneficiary ID, IP, keyword. CSV export with structured fields.
- **Issue 10 — Liveness too sensitive.** Challenge threshold lowered 5°→4°; peak-yaw / peak-pitch tracking so a brief natural turn counts even after returning to centre; challenge duration widened 7 s→10 s; classified failure messages ("no movement", "movement too fast / face lost", "movement too small"); on-screen instruction reworded for normal-speed natural movement.

### Migrations

- `beneficiaries/0012_duplicate_namedob_request` — new model `DuplicateNameDobRequest`.
- `verification/0019_stipend_approval` — adds `approval_status`, `approved_by`, `approved_at`, `published_at`, `rejection_reason` to `StipendEvent`; backfills existing rows to `approved`.

### PAD/liveness deep hardening (post-issue follow-up)

- **Static-sequence detection widened.** `_check_sequence_static` now scores
  `< 0.15` motion as 0.97 (very strong) and `< 0.5` as 0.60–0.95 — phone-screen
  micro-flicker now consistently crosses the suspicious threshold.
- **Near-duplicate frame detector added.** New `_check_near_duplicate_frames`
  uses 64-bit dHash with pairwise Hamming distance ≤ 4 → near-duplicate.
  ≥ 50 % near-duplicate pairs ⇒ score 0.95 (weight 0.70 applied), 30–50 % ⇒
  0.70, 15–30 % ⇒ 0.40. Catches video loops and held-still photos that pass
  the simple inter-frame motion threshold.
- **Per-decision audit log now structured.** `_log_verify(..., extra=...)`
  persists the full PAD/liveness/TX diagnostic dict:
  `anti_spoof_score`, `liveness_result`, `tx_token_present`, `tx_valid`,
  `tx_used`, `tx_expired`, `tx_claimant_match`, `final_block_reason`,
  `challenge_motion_detected`, `static_sequence_detected`, `pa_score`,
  `pa_flags`, `facenet_score`, `final_decision`,
  `reference_embedding_source`. All written into `AuditLog.details` so the
  v2.1.11 audit modal can render them as structured sections.
- **Token immediately cleared on submit dispatch.** `verify.js` now copies
  `livenessToken` to a local then sets the in-page copy to `null` BEFORE the
  fetch. A network error, aborted request, or page state desync cannot leave
  a token sitting around for a second click — combined with the existing
  server-side single-use enforcement, this closes the stale-token reuse path.
- **Client movement metrics surfaced to the backend.** `verify.js` now POSTs
  `movement={peak_yaw_delta, peak_pitch_delta, threshold, face_lost_count,
  max_lost_burst, challenge_duration_ms, mediapipe_available}` so the
  `[LIVENESS_TX] ISSUED` log line and the audit record carry the exact
  numbers the user actually produced (Issue 10 follow-up).
- **No new external dependency.** No cloud, no internet, no SDK. The
  near-duplicate detector uses pure OpenCV + NumPy already in the bundle.
  Offline PAD model integration (e.g. MiniFASNet / Silent-Face-Anti-Spoofing)
  was evaluated and **deferred** — see Honest Limitations.

### Project housekeeping

- Top-level build/install transcripts (`build_err.txt`, `build_log.txt`,
  `build_out.txt`, `build_stderr.txt`, `build_stdout.txt`, `inno_log.txt`,
  `iscc_out.txt`, `pyinstaller_log.txt`) moved to
  `dev/build-logs/archive/` and added to `.gitignore`. The archive folder
  README explains the convention; the folder is never included in the
  installer payload.
- `docs/SYSTEM-STRUCTURE.md` extended with a "Files and folders that must
  NOT move" list and a "Installer payload — must NOT include" list so
  future refactors don't break the installer or accidentally bundle runtime
  data.

### Validation

- `python -m compileall accounts beneficiaries verification fans logs` — no errors.
- `python manage.py check` — 0 issues.
- `python manage.py test verification accounts beneficiaries logs` — **400 passed, 0 failed** (3 new PAD/liveness tests added on top of the 397-test bundle).

### Honest limitations carried forward

- Webcam-only PAD remains heuristic; high-quality screens held at the right
  angle by a person who physically follows the challenge could potentially
  still pass. A trained offline CNN PAD model (MiniFASNet / Silent-Face) was
  evaluated and **deferred**: model sizes (~50–100 MB) materially grow the
  installer, untested licensing for the specific senior-citizen LAN
  deployment context, and dependency footprint risks breaking the existing
  PyInstaller bundle. Recommended as a future improvement once a trained
  model is locally licensed and packaging-tested.
- Liveness/PAD only gate before FaceNet; FaceNet is unchanged and remains
  the identity engine. A valid `tx_token` alone cannot verify.

---

## [2.1-liveness-fix-test1] — 2026-05-26 (Identity Fix + Strict Liveness TX + User Management)

> **TEST BUILD — branch `2.1` — not a production release.**
> Run all regression tests and validate on a clean PC before promoting to production.

### Summary

This update hardens the verification pipeline end-to-end and fixes a set of
User Management issues. Key changes:

- **Identity embedding now uses the neutral (frontal) frame**, not the turned
  challenge frame.  This eliminates false rejects caused by angled face captures.
- **LivenessTransaction issuance is now strict**: tx_token is blocked when
  anti-spoof, sequence-frame minimum, PAD, or embedding checks fail.
- **Minimum 3 sequence frames required** for Mode B PAD analysis.  Fewer frames
  indicate a static image that cannot produce motion and are denied outright.
- **`verify_submit` crash fixed**: `UnboundLocalError` on `face_result` when a
  TX with an embedded encoding was consumed.
- **Weak liveness pass formula removed**: the `0.6 * ANTI_SPOOF_THRESHOLD`
  server-liveness shortcut is gone; `verify_submit` now gates strictly on
  TX `anti_spoof_score ≥ ANTI_SPOOF_THRESHOLD` AND `pa_score < PAD_THRESHOLD`.
- **User Management fixes**: correct `AuditLog.ACTION_USER_CREATE` constant;
  fixed redirects; `must_change_password` enforced on login and cleared after
  change; admin password reset sets flag; login autocomplete attributes corrected.
- **No raw face images stored**: request images are processed in memory only;
  only encrypted embeddings and numeric/metadata scores are persisted.
- **356 regression tests pass** (0 failures).

---

### Fixed — `verify_submit` UnboundLocalError on `face_result`

**Root cause:** When a `LivenessTransaction` with a stored embedding was
consumed, `live_embedding` was populated from the TX and
`process_face_for_verification` was never called, leaving `face_result`
unassigned.  A subsequent reference to `face_result` at the quality-note guard
raised `UnboundLocalError: local variable 'face_result' referenced before
assignment`.

**Fix (`verification/views.py`):**
- Added `face_result = None` initialisation before the
  `if live_embedding is None:` block.
- Guarded the quality-note append with
  `if face_result is not None and face_result.get('quality') ...`.

---

### Fixed — Identity embedding computed from angled challenge frame (false rejects)

**Root cause:** Mode B (`challenge_completed=True`) previously computed the
FaceNet embedding from the turned/angled proof frame.  Cosine similarity scores
dropped on the angled view (e.g. 0.72 < 0.75 threshold) for real registered
users who completed the side challenge correctly.

**Fix:**
- `static/js/verify.js`: Captures `neutralFrameData` at initial camera
  stabilisation (before challenge).  Sends `neutral_image: neutralFrameData`
  alongside the proof frame in the Mode B POST to `CHECK_LIVENESS_URL`.
- `verification/views.py` (`verify_check_liveness`):
  - Accepts `neutral_image` from the request body.
  - Runs anti-spoof and FaceNet embedding on the **frontal** neutral frame
    when available.
  - Falls back to the proof frame if no neutral image is sent (backwards
    compatible with older clients).
  - Stores the neutral-frame anti-spoof score in the `LivenessTransaction`
    for use by `verify_submit`.
- `verification/views.py` (`verify_submit`):
  - Uses the TX-stored embedding (from the neutral frame) for identity
    comparison.
  - Logs `neutral_tx_embedding=True/False` for every decision.

**Security:** Challenge and proof frames are still used for PAD and motion
checks.  Only the identity embedding source changes.

---

### Fixed — TX issued without embedding ("best-effort" TX removed)

**Root cause:** Even when `get_embedding_only` failed, a TX was previously
issued with `embedding_data=None`.  `verify_submit` then fell through to
`process_face_for_verification` on the submitted frame — an untrusted path
that is vulnerable to face-switching after liveness.

**Fix (`verification/views.py` `verify_check_liveness`):**
- If `get_embedding_only` returns `success=False` or no embedding,
  the response is `{"passed": false, "debug_stage": "embedding_failed"}` and
  no TX is issued.
- If `get_embedding_only` raises an exception, the response is
  `debug_stage: "embedding_exception"` — same result: no TX.

---

### Fixed — Weak liveness pass formula in `verify_submit`

**Root cause:** When a TX was attached, the server liveness decision was
partially computed as `liveness_tx.liveness_score >= 0.6 * ANTI_SPOOF_THRESHOLD`
— a formula that could pass a low-quality capture that failed the proper anti-
spoof gate.

**Fix (`verification/views.py` `verify_submit`):**
- TX-based liveness gate is now:
  `liveness_tx.anti_spoof_score >= ANTI_SPOOF_THRESHOLD AND pa_score < PAD_THRESHOLD`
- Both conditions must hold.  `liveness_score` is kept for display/logging only.

---

### Fixed — Minimum sequence frame requirement (static-image gate)

**Root cause:** A static phone screen or printed photo sent with zero or one
sequence frame would pass the PAD `analyze()` call (which runs on a single
image) and potentially receive a TX.

**Fix (`verification/views.py` `verify_check_liveness`):**
- Before PAD analysis, checks `len(sequence_frames_b64) >= 3`.
- Fewer than 3 frames → `{"passed": false, "debug_stage": "insufficient_sequence_frames"}`.
- PAD `analyze_sequence()` is used when ≥ 3 frames are present;
  `analyze()` is a fallback for edge cases only.

---

### Fixed — User Management: wrong AuditLog constant

**Root cause:** `accounts/views.py` used `AuditLog.ACTION_USER_CREATED` which
does not exist, causing an `AttributeError` when user create/edit views attempted
to log.

**Fix:** All 3 occurrences replaced with `AuditLog.ACTION_USER_CREATE` (the
correct constant defined in `logs/models.py`).

---

### Fixed — User Management: redirect to wrong URL namespace

**Root cause:** `accounts/views.py` used `redirect('beneficiaries:user_list')`
and `redirect('beneficiaries:user_create')` — routes that do not exist in the
`beneficiaries` namespace.

**Fix:** All 2 occurrences corrected to `redirect('accounts:user_list')` and
`redirect('accounts:user_create')`.

---

### Fixed — `must_change_password` not enforced at login / not cleared after change

**Root cause:** When an admin reset a password, `must_change_password` was not
set to `True`.  When a user changed their password, the flag was not cleared.
Login did not check the flag and redirect.

**Fix (`accounts/views.py`):**
- `UserLoginView.form_valid`: after successful login, if `user.must_change_password`
  is `True`, redirects to `accounts:change_password` with a warning message.
- `change_password`: after a successful save, clears
  `request.user.must_change_password = False`.
- `admin_reset_password`: sets `target_user.must_change_password = True` after
  resetting.

---

### Fixed — Login form autocomplete attributes

**Root cause:** `LoginForm` used `autocomplete='new-password'` on both username
and password fields.  `new-password` tells browsers not to autofill saved
credentials — it is the correct value for *creation* fields, not *login* fields.
On shared PCs where staff have saved their credentials, the browser would refuse
to fill them.

**Fix (`accounts/forms.py`):**
- `username` field: `autocomplete='username'`
- `password` field: `autocomplete='current-password'`

---

### Fixed — Beneficiaries app user-management stub views

**Root cause:** Legacy stub views in `beneficiaries/views.py`
(`user_list`, `user_create`, `user_edit`) raised `AttributeError` when called
because they referenced `accounts:` routes that are now the canonical location.

**Fix:** Stub views replaced with clean `redirect()` calls to the `accounts:`
equivalents.

---

### Improved — Verification logging

`verification/views.py` (`verify_submit`):
- All per-template cosine similarity scores are logged individually
  (`[VERIFY_SCORE] template=… score=… threshold=… gap=…`).
- Summary log includes `neutral_tx_embedding=True/False`.
- TX liveness gate now logs a warning when anti-spoof or PAD criteria are not met.

---

### Tests

- **356 tests pass** (0 failures, 0 errors).
- `python manage.py check` — no issues.
- Updated `VerifyCheckLivenessModeBTest`: default frames = 3; all affected tests
  updated to mock `analyze_sequence` and `get_embedding_only`.
- **New:** `VerifyCheckLivenessV3GatesTest` — 4 tests covering:
  - Insufficient sequence frames (< 3) blocks TX
  - Zero frames blocked (static image gate)
  - Neutral anti-spoof failure blocks TX
  - Embedding failure blocks TX
- **New:** `VerifySubmitTXEmbeddingRegressionTest` — Bug A regression:
  TX with stored embedding path does not crash on quality-note guard.
- **New:** `MustChangePasswordTest` — 4 tests covering login redirect,
  no-redirect when flag is false, flag cleared after change, admin reset sets flag.
- **New:** `AuditLogUserCreateConstantTest` — 3 tests confirming
  `ACTION_USER_CREATE` exists and `ACTION_USER_CREATED` does not.

### Build (TEST)

- **Tests:** 356 pass (0 failures)
- **Django check:** 0 issues (0 silenced)
- **Installer:** `FANS-C-Installer\FANS-C-Setup-v2.1-liveness-fix-test1.exe`
- **Installer size:** 208.58 MB (218,716,905 bytes)
- **SHA-256:** `A5E3F9347F6F5237B9574FDBDEC835189CE2DB2CA4D5026585314FE43DB868AF`
- **Build time:** 2026-05-26 23:52:20
- **Payload safety:** SAFE — `.env`, `db.sqlite3`, certs, logs, media, private keys confirmed absent
- **Do not deploy to production without clean-PC validation.**

---

## [2.3.1] — 2026-05-25 (Baseline Yaw Fix + Lookalike Wording)

### Fixed — `baseYaw=n/a` — baseline yaw never captured during liveness challenge

**Root cause:** `liveness.js` stored the baseline inside `initialPose` (set by `setBaseline()`), but
there was no explicit public accessor for the baseline yaw value. The periodic debug log read
`initialPose.yaw`, which was correct in principle but could not be independently verified or directly
set by the challenge coordinator in `verify.js`. Adding a dedicated `_baselineYaw` variable that is
tracked in parallel with `initialPose` makes baseline capture visible in both debug logs and the
verify flow, and makes it immediately diagnosable if it is ever null after the stable wait.

**Changes:**

`static/js/liveness.js`:
- Added `let _baselineYaw = null` — a dedicated variable that mirrors `initialPose.yaw` after
  `setBaseline()` runs.
- Updated `setBaseline()` to assign `_baselineYaw` in all three branches (averaged history,
  single frame, fallback).
- Added `setBaselineYaw(value)` public method — directly sets `_baselineYaw` and updates
  `initialPose.yaw`; useful for explicit override by the verify flow if needed.
- Added `getBaselineYaw()` public method — returns `_baselineYaw` (null before baseline is set,
  a number after).
- Updated `reset()` to clear `_baselineYaw = null` alongside `initialPose`.
- Updated periodic debug log: reads `_baselineYaw` instead of `initialPose.yaw`.
  After challenge baseline is captured, logs now show `baseYaw=<number>` rather than `n/a`.
- Exported `setBaselineYaw` and `getBaselineYaw` in the return object.

`static/js/verify.js`:
- Before `setBaseline()`, reads `getCurrentPose()` and warns in console if it is null.
- After `setBaseline()`, logs `[FANS-C Challenge] baseline captured` with `stableFrameCount`,
  `baselineYaw`, `currentYaw`, and `challengeStarted` — makes it immediately visible in
  the browser console whether baseline was captured correctly.
- Added null warning: if `getBaselineYaw()` returns null after `setBaseline()`, a `console.warn`
  alerts that FaceMesh may not have landmarks yet.
- When `checkChallenge()` returns true (challenge passed), logs `[FANS-C Challenge] completed`
  with `currentYaw`, `baselineYaw`, `yawDelta`, and `absYawDelta` — confirms the exact delta
  that triggered the pass.

**Expected console output after this fix:**
```
FANS-C: MediaPipe FaceMesh initialized. Threshold = 5 deg
FANS-C: Waiting for 10 stable frames before baseline…
FANS-C: stable frames = <N> after <T> ms
FANS-C: Baseline set (avg 5 frames) yaw= <number>
[FANS-C Challenge] baseline captured { stableFrameCount: N, baselineYaw: <number>, currentYaw: <number>, challengeStarted: true }
[FANS-C FaceMesh] frame=… baseYaw=<number>   ← no longer n/a
[FANS-C Challenge] completed { currentYaw: …, baselineYaw: …, yawDelta: …, absYawDelta: >=5 }
FANS-C: Challenge PASSED at <T>ms direction=side
```

---

### Changed — Lookalike manual review message wording

**Previous:** `Manual review — possible lookalike: score … >= threshold …, but beneficiary … also scored … Staff must confirm identity with ID before releasing stipend.`

**New:** `MANUAL REVIEW — POSSIBLE DUPLICATE OR LOOKALIKE. Liveness and face matching passed (score … >= threshold …), but another beneficiary record (…) also matched this face within the lookalike safety band (…). Staff must confirm the claimant's identity using a valid ID before releasing stipend.`

**Changes:**

`verification/views.py` (`verify_submit`, lookalike detection block):
- Updated `reason` string for the lookalike escalation case.
- Begins with `MANUAL REVIEW — POSSIBLE DUPLICATE OR LOOKALIKE` for clear operator recognition.
- States explicitly that liveness and face matching both passed before describing the lookalike concern.
- Notes the specific competing beneficiary ID, name, score, and band width.

**Safety gate:** The lookalike escalation is NOT weakened. The band (default 0.05) and the
requirement for staff ID verification before stipend release are unchanged.

---

### Security note

This release keeps strict liveness enforcement enabled. Face matching cannot override failed
liveness or a failed head-movement challenge. Manual review remains required when another
beneficiary matches within the lookalike safety band (default 0.05).

---

### Added — Lookalike safety gate regression tests

`verification/tests.py` — `LookalikeSafetyGateTest` (4 tests):
1. **Lookalike within band → MANUAL_REVIEW:** Face match passes (score 0.88 ≥ threshold 0.75) and liveness passes, but another beneficiary scores 0.84 (within 0.05 band). Expected: `MANUAL_REVIEW` — gate must trigger.
2. **No ClaimRecord on lookalike hit:** When the gate fires (`MANUAL_REVIEW`), no `ClaimRecord` with `STATUS_CLAIMED` is created — stipend must not be released under a lookalike flag.
3. **No competitor → VERIFIED:** Face match passes, liveness passes, no close competitor. Expected: `verified` — gate must not false-fire.
4. **Gate active in strict liveness mode:** Lookalike gate must trigger `MANUAL_REVIEW` even when `LIVENESS_REQUIRED=True` and both anti-spoof and challenge pass.

---

### Fixed — Audit log race condition on lookalike escalation

`verification/views.py` (`verify_submit`, lookalike detection block):
- `AuditLog.log()` for `ACTION_DUPLICATE_FACE` was previously called before `attempt.save()`, meaning `attempt.id` was `None` at write time.
- Moved to after `attempt.save()` via a deferred `_lookalike_audit_details` dict — `attempt.id` is now populated when the audit record is written.
- Added detailed structured `[LOOKALIKE]` log line (beneficiary ID, claimed score, threshold, band, lookalike threshold, embeddings checked, closest match, gap, trigger flag) for operational traceability.

---

### Tests

- 339 tests pass (335 from prior build + 4 new `LookalikeSafetyGateTest` regression tests).
- `python manage.py check` — no issues.

### Build

- **Tests:** 339 pass (0 failures)
- **Django check:** 0 issues (0 silenced)
- **Payload safety:** SAFE — `.env`, `db.sqlite3`, media, private keys confirmed absent
- **MediaPipe payload:** All 5 FaceMesh assets confirmed in staging (`face_mesh.binarypb` included)
- **Installer:** `FANS-C-Installer\FANS-C-Setup.exe` — 208.5 MB (218,628,258 bytes)
- **SHA-256:** `1119DAD20D53E4B5B7357E0002C73A01CE6A0ED299C36CE0EB84E029489B62FF`
- **Build time:** 2026-05-25 11:18:34

---

## [2.3.0] — 2026-05-25 (Strict Liveness Gate + Rep Face Fix + Password + UI Fixes)

### Fixed — `CHECK_LIVENESS_URL is not defined` on representative face registration

**Root cause:** `templates/verification/register_rep_face.html` did not define the JavaScript globals
(`CHECK_LIVENESS_URL`, `REG_CHALLENGE`, `REG_CHALLENGE_DISPLAY`, `MEDIAPIPE_BASE_URL`) required by
`static/js/register.js`, and did not load the MediaPipe and `liveness.js` scripts. Clicking
"Capture Face" on the representative face registration page crashed with a `ReferenceError` in the
browser console and displayed "Cannot reach server. CHECK_LIVENESS_URL is not defined."

**Changes:**

`templates/verification/register_rep_face.html`:
- Added `CHECK_LIVENESS_URL`, `REG_CHALLENGE`, `REG_CHALLENGE_DISPLAY`, `MEDIAPIPE_BASE_URL` globals.
- Added `face_mesh.js`, `camera_utils.js`, `liveness.js` script includes.
- Added challenge UI elements: `regChallengeBox`, `regChallengeText`, `regChallengeTimer`,
  `regChallengeProgress`, `regLivenessStatus` (matching `register_face.html`).

`verification/views.py` (`register_rep_face`):
- Now generates a challenge with `get_random_challenge()` and stores it in session.
- Passes `challenge` and `challenge_display` to the template context.

`static/js/register.js`:
- Added defensive guard at start of capture handler: if `CHECK_LIVENESS_URL` is undefined or empty,
  show "Liveness configuration error" and disable the button — never fake a pass or skip liveness.

---

### Fixed — Liveness/anti-spoof: strict gate for final verification

**Security:** For final stipend verification, the head-movement challenge is now ALWAYS required
(not risk-triggered). Anti-spoofing AND active liveness are both mandatory gates before FaceNet runs.

**Problem:** Previous risk-based flow skipped the head-movement challenge if the anti-spoof texture
score was ≥ 0.30 and no other risk condition was present. A sharp, clear phone-screen photo could
score ≥ 0.30 and receive `challenge_completed=True` automatically — bypassing the active challenge.

**Changes:**

`static/js/verify.js`:
- Removed the "fast path" block that set `challenge_completed=true` without running the challenge.
- Challenge is now unconditionally triggered for every verification attempt.
- Removed `challengeCompleted = !mpAvailable && !LIVENESS_REQUIRED` unsafe timeout auto-accept.
  Timeout now always fails — real head movement must be confirmed. If MediaPipe is unavailable,
  the staff should retry with a supported browser and camera.

`verification/views.py` (`verify_submit`):
- `server_challenge_required` is now always `True` for final verification.
- `server_liveness_passed = server_anti_spoof_passed AND challenge_completed` (strict gate).
- FaceNet matching only runs after both gates pass (when `LIVENESS_REQUIRED=True`).

---

### Changed — Liveness challenge direction: 'side' replaces strict left/right/up/down

**Problem:** Look left/right/up/down challenges failed for senior users, users with limited range
of motion, and users confused by mirrored webcam preview (left ↔ right reversal).

**Changes:**

`verification/liveness.py`:
- `CHALLENGE_DIRECTIONS = ['side']` — only side-movement challenge is issued.
- `get_random_challenge()` returns `'side'`.

`verification/views.py` (`_challenge_display`):
- Added `'side': 'Move your head slightly to either side'`.

`static/js/liveness.js`:
- Added `'side'` direction: `abs(yawDelta) >= CHALLENGE_THRESHOLD_DEG` — accepts movement in
  either direction, eliminating mirrored-preview confusion.
- Updated `getDebugInfo` to compute `movementDelta = Math.abs(yawDelta)` for 'side'.

---

### Fixed — User profile picture/avatar upload removed from User Management

**Reason:** Profile picture upload via the admin UI is no longer needed. Removing it eliminates
the risk of broken image display, unnecessary media storage, and confusing UI elements.

**Changes:**

`templates/base.html`:
- Replaced the profile picture `<img>` + `onerror` fallback with a plain `bi-person-circle` icon.
  No uploaded avatar is ever shown in the navbar.

`templates/admin_panel/user_form.html`:
- Removed profile picture upload input and current photo preview from the Edit User form.
- Removed `enctype="multipart/form-data"` (no file upload in this form anymore).

`accounts/forms.py` (`UserUpdateForm`):
- Removed `profile_picture` from `fields` list.
- Removed `clean_profile_picture()` validation method.

`accounts/tests.py`:
- Replaced `ProfilePictureValidationTest` with `ProfilePictureRemovedTest` (3 tests confirming
  the field is absent from the form and that the form still validates correctly).

**Note:** The `profile_picture` database column on `CustomUser` is intentionally left in place
(migration `0004_add_profile_picture`). No risky migration is needed to drop a column that is
now simply unused. Beneficiary face registration images and verification media are unaffected.

---

### Fixed — Password validation: uppercase letter now required

**Problem:** Passwords could be accepted without any uppercase letter despite UI text suggesting
uppercase is required.

**Changes:**

`accounts/validators.py`:
- Added `UppercasePasswordValidator`: raises `ValidationError` if the password contains no
  uppercase letter (A–Z). Error code: `password_no_upper`.

`fans/settings.py` (`AUTH_PASSWORD_VALIDATORS`):
- Added `accounts.validators.UppercasePasswordValidator` to the validator list.

`accounts/forms.py`:
- Updated `help_text` on all password fields to explicitly mention "at least one uppercase letter."

`accounts/tests.py`:
- Added `UppercasePasswordValidatorTest` (4 tests: uppercase passes, all-lowercase fails,
  digits-only fails, help text mentions uppercase).

**Scope:** Applies to create user, edit/reset user password, change password, and first-admin
creation — everywhere `validate_password()` is called via Django's AUTH_PASSWORD_VALIDATORS.

---

### Added — Show/hide password toggle on login page and admin reset page

`templates/accounts/login.html`:
- Wrapped password field in `input-group` with an eye toggle button.
- Added JS toggle: switches `type="password"` ↔ `type="text"`, updates icon and `aria-*` attributes.
- Existing autofill-reduction attributes (`autocomplete="new-password"`, `autocorrect="off"`, etc.)
  are preserved.

`templates/accounts/admin_reset_password.html`:
- Added show/hide toggle on both password fields (matching `change_password.html` pattern).

---

### Known Limitations / Future Work

**Anti-spoofing (PAD — Presentation Attack Detection):**
The current anti-spoofing is based on texture analysis (Laplacian variance, LBP proxy, Sobel
edge density). This heuristic rejects many phone-screen captures but is NOT a trained CNN model.

A proper PAD implementation requires:
- MiniFASNet or Silent-Face-Anti-Spoofing model (ONNX Runtime)
- Integrated as a required `anti_spoof_passed` gate in `verify_check_liveness`
- Failing closed if the model file is absent

This is listed as the highest-priority future security improvement. Do NOT claim trained PAD
support exists until the ONNX model is integrated and tested.

**Active liveness challenges:**
- Blink detection and open-mouth detection are planned but not yet implemented.
- Current active challenge: 'side' head movement (abs yaw delta).
- These should be added to `liveness.js` and `verification/liveness.py` in a future cycle.

---

### Build

- **Tests:** 328 pass (0 failures)
- **Django check:** 0 issues (0 silenced)
- **Payload safety:** SAFE — `.env`, `db.sqlite3`, certs, logs, `media/` confirmed absent
- **Installer:** `FANS-C-Installer\FANS-C-Setup.exe` — 208.46 MB (218,583,840 bytes)
- **SHA-256:** `67AFFDBE76EC4D0A603A440E4B4E2713F32D42582F183FF15D3C9AFA5D2E00F9`
- **Build time:** 2026-05-25 01:50:12

---

## [2.2.2] — 2026-05-24 (Liveness UI Cleanup + Profile Picture Media Fix)

### Fixed — Liveness mismatch technical banner removed from operator UI

**Problem:** The verification result page showed a yellow alert banner with raw anti-spoof scores,
internal score labels (`client spoof`, `server spoof`), and text like "the server is the authoritative
source" when the client and server liveness scores diverged — even when both passed.  Normal
operators and barangay staff are not expected to understand these labels, and the banner looked
alarming even in passing cases.

**Context:**
- *Liveness* checks whether the camera is seeing a real live person (not a phone/photo/screen).
- *Face verification* checks whether that live person matches the registered beneficiary/representative.
These are distinct steps and must not be conflated in the UI.

**Changes:**

`templates/verification/result.html`:
- Removed the `alert-warning` "Liveness mismatch detected" block entirely.
- Non-mismatch notes (e.g. `"Liveness required and not passed."`) continue to render as before.
- Technical mismatch details remain stored in `attempt.notes` in the database for admin audit review.
- Server-side liveness result remains the authoritative final decision — no security change.

`verification/tests.py` (`LivenessMismatchResultTest`):
- Renamed `test_mismatch_alert_shown_on_result_page` → `test_mismatch_banner_not_shown_on_result_page`.
- Assert `assertNotContains` for `"Liveness mismatch detected"`, `"client reported"`,
  `"server measured"`, `"authoritative source"`.
- Assert `attempt.notes` still contains `"Liveness mismatch"` in the DB (audit trail preserved).

**Behaviour by case (no security change):**

| Client | Server | UI shown to operator |
|--------|--------|----------------------|
| Pass | Pass (same decision) | Nothing extra |
| Pass | Pass (different scores) | Nothing extra — mismatch note stays in DB only |
| Pass | Fail | Verification denied — denial reason shown as before |
| Fail | Pass | Server result wins — no alarming banner |

### Fixed — User profile picture (avatar) broken in production EXE

**Root cause:** `django.conf.urls.static.static()` returns an empty URL-pattern list when
`DEBUG=False`.  The installed EXE runs with `DEBUG=False`, so uploaded media files
(profile pictures) were never routed — every `/media/...` URL returned 404.

**`fans/urls.py`:**
- Added `insecure=True` to the `static()` call for `MEDIA_URL`.
  This removes the `DEBUG` guard and registers the media-serving URL pattern unconditionally.
  The name "insecure" refers to Django's dev-server caveat for high traffic, not a security
  risk — serving uploaded images from a LAN-only app with a handful of users is fine.

**`templates/base.html`** (navbar user badge):
- Added `onerror` on the profile picture `<img>` to hide the broken-image icon and reveal a
  fallback `<i class="bi bi-person-circle">` element instead.

**`templates/admin_panel/user_form.html`** (Edit User — current photo preview):
- Added `onerror` on the preview `<img>` to hide it and reveal an initials-circle fallback
  (`first initial + last initial`) when the image path is missing or corrupt.

**Validation (unchanged):** JPEG, PNG, GIF, WebP only; max 2 MB; SVG blocked.

### Build

- **Tests:** 323 pass (0 failures), ~115s
- **Django check:** 0 issues (0 silenced)
- **PyInstaller:** incremental (no -Clean)
- **ISCC:** completed 2026-05-24 23:16:39
- **EXE path:** `c:\FANS\FANS-C-Installer\FANS-C-Setup.exe`
- **Size:** 208.49 MB (218,616,396 bytes)
- **SHA-256:** `B55E772606F284F97BC1A317B3B45E06CD22E4A6A2BFC5DB657AD233CDDC5C16`
- **Payload safety:** SAFE — `.env`, `db.sqlite3`, certs, logs, `media/` confirmed absent

### Documentation updated (v2.2.2 docs cycle)

| File | Change |
|---|---|
| `README.md` | Added Latest Release section; expanded liveness vs. verification; registration liveness; duplicate review; profile picture; autofill note; QA checklist; future features |
| `SETUP.md` | Updated test count (213→323); added liveness vs. verification section |
| `CHANGELOG.md` | This entry |
| `docs/DEPLOYMENT-CHECKLIST.md` | Updated test count (316→323); added v2.2.2 checks; added clean-PC QA section |
| `docs/SECURITY-CHECKLIST.md` | Added items 8.20–8.23 for liveness UI, media serving, profile picture validation, risk-based registration challenge |
| `docs/SYSTEM-STRUCTURE.md` | Replaced verification flow (2.4) with v2.2.x accurate flow; added registration liveness flow (2.4b); added liveness vs. verification Q&A |
| `docs/SYSTEM-OVERVIEW.md` | **Created** — comprehensive reference for admins and capstone evaluators covering all 14 sections |

---

## [2.2.1] — 2026-05-24 (Registration Liveness Fix + Autocomplete Hardening + UI Polish)

### Fixed — Registration liveness: risk-based challenge (was blocking all real users)

**Root cause:** `register.js` (v2.2.0) required the head-movement challenge on EVERY registration attempt regardless of anti-spoof score. `register_submit_face` unconditionally rejected if `client_challenge_completed=False`. Real users with strong anti-spoof scores failed because MediaPipe tracking or the 7-second window was not enough to detect natural movement.

**Frontend (`static/js/register.js` — risk-based rewrite):**
- Challenge is now only triggered when risk conditions are present: anti-spoof score < `REG_CHALLENGE_TRIGGER_THRESHOLD` (0.30) or face quality poor.
- Fast path: strong anti-spoof (score ≥ 0.30) + good quality → skip challenge, `challenge_completed=true`, capture directly.
- Challenge path: progress overlay with real-time degree/threshold display (matches `verify.js`).
- Debug logging in console for pitch/yaw deltas and challenge progress.
- On challenge timeout: clear error with tips (center face, improve lighting, move slowly).
- If challenge required but MediaPipe unavailable: block immediately with informative message rather than silently failing after 7s.
- `challenge_completed=true` submitted only after actual movement detection (fast path or challenge pass).

**Backend (`beneficiaries/views.py` — `register_submit_face`):**
- Added `check_face_quality` call to the server-side liveness block.
- Risk-based challenge requirement mirrors frontend:
  - `server_challenge_required = REGISTRATION_CHALLENGE_REQUIRED or not quality_ok or server_anti_spoof_score < LIVENESS_CHALLENGE_TRIGGER_THRESHOLD`
  - Strong anti-spoof (≥ 0.30) + good quality → accepted without challenge.
  - Borderline/suspicious (< 0.30) or poor quality → challenge required; rejected if not completed.
  - Anti-spoof fail (< `ANTI_SPOOF_THRESHOLD`) → always rejected (phone/screen protection preserved).
- Audit log now records `quality_ok` and `challenge_trigger_threshold` for investigation.

**Template (`templates/beneficiaries/register_face.html`):**
- Added `<div id="regChallengeProgress">` inside the challenge box for real-time movement display.

**New settings (`fans/settings.py`, `.env.example`):**
- `REGISTRATION_CHALLENGE_REQUIRED=False` — force challenge on every registration (default off = risk-based).
- `REGISTRATION_STRONG_LIVE_THRESHOLD=0.50` — informational; documents what "strong" means for ops.

**Tests (`verification/tests.py`):**
- Added `RegistrationLivenessTest` class with 7 tests covering: strong pass without challenge, anti-spoof fail, borderline without challenge (rejected), borderline with challenge (passes), poor quality without challenge (rejected), phone/screen mock (rejected), liveness disabled path.

### Security — Disable browser autofill/autocomplete on registration and login forms

**Problem:** Chrome/browser was showing saved suggestions (names, contacts, IDs) on registration fields and showing saved credentials on the login page — both inappropriate for a shared barangay kiosk.

**`beneficiaries/forms.py`:**
- `BeneficiaryInfoForm`: added `autocomplete="off"`, `autocorrect="off"`, `autocapitalize="off"` to `first_name`, `middle_name`, `last_name`, `date_of_birth`, `contact_number`, `valid_id_number` widgets.
- `RepresentativeForm`: added same attributes to `rep_first_name`, `rep_last_name`, `rep_relationship`, `rep_contact`, `rep_id_number` widgets.

**`accounts/forms.py`:**
- `LoginForm`: set `autocomplete="new-password"` on both `username` and `password` fields — the strongest HTML-level approach to suppress credential suggestions on shared PCs.

**`templates/beneficiaries/register_step2.html`:**
- Added `autocomplete="off"` to the form tag.
- Added JS to clear all representative fields (inputs and selects) when the representative toggle is switched off, preventing stale values from being carried over.

**Deployment note:** For shared barangay PCs, browser-level password saving should be disabled in Chrome/Edge Settings → Autofill → Passwords, or via Windows/organization group policy. HTML `autocomplete` attributes reduce suggestions but browser password managers may override them.

### UI — Remove decorative shield icons from admin page headings

- `templates/verification/manual_review.html`: removed `<i class="bi bi-shield-exclamation me-2"></i>` from "Admin Review Queue" heading.
- `templates/verification/override.html`: removed `<i class="bi bi-shield-exclamation me-2"></i>` from "Admin Override" heading.

---

## [2.2.0] — 2026-05-24 (Risk-Based Liveness + Registration Liveness + Duplicate Face Review)

### Changed — Verification: risk-based liveness challenge (no longer forced for every attempt)

**Root cause fixed:** `LIVENESS_REQUIRED=True` was incorrectly included in the `needsChallenge` condition in `static/js/verify.js`, forcing a head-movement challenge on every attempt even for real users with good anti-spoof scores. This made legitimate senior citizens fail liveness while the backend still accepted high-score attempts that skipped the challenge server-side.

**Frontend (`static/js/verify.js`):**
- Removed `LIVENESS_REQUIRED` from `needsChallenge`. Challenge is now triggered only when risk conditions are present: `REQUIRE_LIVENESS_CHALLENGE` override, low anti-spoof score (<0.30), poor quality, or retry.
- `LIVENESS_REQUIRED` now controls only whether a failed liveness *blocks* verification — it no longer forces a challenge on clean captures.
- Simplified `step2Msg` failure text; removed dead `else if (LIVENESS_REQUIRED)` branch from `challengeReason` block.

**Backend (`verification/views.py` — `verify_submit`):**
- Replaced single-line `server_liveness_passed = server_anti_spoof_passed and challenge_completed` with risk-based logic mirroring the frontend:
  - `server_challenge_required` is `True` when: rep claim, retry attempt (`attempt_number > 1`), server anti-spoof score below `LIVENESS_CHALLENGE_TRIGGER_THRESHOLD` (default 0.30), or `REQUIRE_LIVENESS_CHALLENGE` global override.
  - `server_liveness_passed = server_anti_spoof_passed and (challenge_completed if server_challenge_required else True)`
- Denial reason generation updated: three branches now distinguish (a) both anti-spoof and challenge failed, (b) only anti-spoof failed, (c) only challenge failed (anti-spoof passed). Branch (c) text contains "challenge" and does NOT say "below threshold".

**New settings (`fans/settings.py`, `.env.example`):**
- `LIVENESS_CHALLENGE_TRIGGER_THRESHOLD=0.30` — score below which the head-movement challenge is required.
- `REQUIRE_LIVENESS_CHALLENGE=False` — global override to force challenge on every attempt.
- `REGISTRATION_LIVENESS_REQUIRED=True` — registration is stricter than verification (see below).
- Clarified in `.env.example` that `LIVENESS_REQUIRED` does NOT force the challenge.

### Added — Registration: mandatory liveness before face enrollment

**Root cause fixed:** Registration previously accepted phone screens and printed photos with no liveness check at all. A captured image was saved directly as a face embedding without anti-spoof validation.

**Frontend (`static/js/register.js` — complete rewrite):**
1. Camera starts.
2. Capture clicked → anti-spoof via `CHECK_LIVENESS_URL` (verify_check_liveness).
3. If anti-spoof fails → blocked with "phone screen detected" message; no submission.
4. If anti-spoof passes → mandatory head-movement challenge (always required for registration, stricter than verification).
5. Challenge passes → capture high-quality frame, show preview.
6. Submit → sends `liveness_passed`, `challenge_completed`, `anti_spoof_score` with image.

**Template (`templates/beneficiaries/register_face.html`):**
- Added MediaPipe script loading (local vendor assets).
- Added challenge UI: `regChallengeBox`, `regChallengeText`, `regChallengeTimer`, `regLivenessStatus`.
- Added template variables: `CHECK_LIVENESS_URL`, `REG_CHALLENGE`, `REG_CHALLENGE_DISPLAY`, `MEDIAPIPE_BASE_URL`.

**Backend (`beneficiaries/views.py`):**
- `register_face`: generates a random challenge via `get_random_challenge()`, stores in session, passes to template.
- `register_submit_face`: when `REGISTRATION_LIVENESS_REQUIRED=True` (default), re-validates anti-spoof and challenge server-side by calling `load_image_from_bytes`, `detect_and_align_face`, `check_anti_spoofing`. Returns `HTTP 400` JSON errors for anti-spoof failure or incomplete challenge. Registration is blocked at the server even if the client sends forged `liveness_passed=true`.

### Added — Duplicate face review workflow

**Root cause fixed:** A duplicate face match during registration previously returned a hard "Contact administrator" error with no action possible. Duplicate records were lost.

**Model (`beneficiaries/models.py`):**
- Added 6 fields to `Beneficiary`: `duplicate_review_required`, `duplicate_match_beneficiary` (FK to self), `duplicate_match_score`, `duplicate_review_notes`, `duplicate_reviewed_by` (FK to user), `duplicate_reviewed_at`.

**Migration:** `beneficiaries/migrations/0011_duplicate_face_review.py` — applied.

**Backend (`beneficiaries/views.py`):**
- `register_submit_face`: on duplicate detection, saves beneficiary as `status=pending` with `duplicate_review_required=True` instead of returning an error. Auto-approval is skipped for these records. Returns `duplicate_review_required: True` in JSON response with a warning message.
- `pending_approvals` and `bulk_approve` exclude `duplicate_review_required=True` records.
- New view `duplicate_review_list`: admin queue listing all pending duplicate-flagged beneficiaries.
- New view `duplicate_review_detail`: shows new registrant vs. matched beneficiary side-by-side with similarity score bar. POST handles `action=approve_twin` (activates record, clears flag) or `action=reject_duplicate` (deactivates record). Both actions are logged to the audit log and require admin or head_admin role.

**Templates:** `templates/beneficiaries/duplicate_review_list.html`, `templates/beneficiaries/duplicate_review_detail.html` (new files).

**URLs (`beneficiaries/urls.py`):**
- `duplicate-review/` → `duplicate_review_list`
- `duplicate-review/<uuid:pk>/` → `duplicate_review_detail`

### Security
- Registration is now strictly stronger than verification: mandatory anti-spoof + head-movement challenge before any face embedding is stored.
- Only admin/head_administrator roles can approve duplicate/twin cases.
- All duplicate review actions are audit-logged.
- Duplicate records remain `pending` (cannot claim stipends) until reviewed.
- Server-side liveness re-validation for both registration and verification cannot be bypassed by a tampered client.

### Installer — 2026-05-24 20:24:58

| Item | Value |
|---|---|
| Path | `C:\FANS\FANS-C-Installer\FANS-C-Setup.exe` |
| Size | 208.5 MB (218,597,239 bytes) |
| SHA-256 | `AABE4E95C4E91647C625398CD561B447AE15C4FC06EEBBBCFB3A406CED8E1EC7` |

### Tests
- Fixed 4 tests that assumed the old always-challenge behavior:
  - `RegisterSubmitFaceViewTest.test_data_uri_prefix_is_stripped_before_decode` — added `@override_settings(REGISTRATION_LIVENESS_REQUIRED=False)`.
  - `RepresentativeRegistrationTest.test_representative_record_created_on_registration` — added `@override_settings(REGISTRATION_LIVENESS_REQUIRED=False)`.
  - `LivenessStrictModeTest.test_denial_reason_mentions_challenge_when_only_challenge_fails` — lowered mock anti-spoof score to 0.10 (below 0.30 trigger) so challenge is required; `passed=True` verifies anti-spoof passes but challenge is the sole failure.
  - `LivenessStrictModeTest.test_failed_liveness_denies_even_if_face_would_match` — same score fix.
- **316 tests pass.**

---

## [2.1.7] — 2026-05-24 (Real-User Liveness Fix + Dashboard Layout)

### Fixed — Real live face still failing liveness challenge

**Root causes identified and fixed:**

1. **Challenge instruction said "Tilt to LEFT/RIGHT" — measures YAW, not ROLL** (`verification/views.py`):
   - `_challenge_display()` previously used "Tilt your whole head slowly to the LEFT/RIGHT".
   - Senior citizens who "tilt" their head (ear toward shoulder = roll motion) produce near-zero yaw delta.
   - The code measures **yaw** (turning/looking horizontally) and **pitch** (raising/lowering chin), not roll.
   - Fixed: instructions now say "Look LEFT — turn your head to the left" / "Look UP — raise your chin slightly" etc.

2. **CHALLENGE_THRESHOLD_DEG = 8 required ~25° physical turn** (`static/js/liveness.js`):
   - The normalized formula `((nose.x - eyeCenterX) / eyeWidth) * 45` maps a ~15° physical head turn to only ~5° in formula space. An 8° threshold required ~25° physical movement — excessive for senior citizens.
   - Fixed: threshold lowered to **5°**, meaning a natural ~15° head turn passes reliably.
   - Also: `setBaseline()` now averages recent pose history frames (rolling buffer of 5) for a more stable baseline, reducing false positives from micro-movements at baseline capture time.

3. **Warmup window too short** (`static/js/verify.js`):
   - 1.5 s warmup extended to **2.5 s** + 300 ms post-landmark delay to let pose history stabilize before baseline is set.

4. **No real-time movement feedback** (`static/js/verify.js`):
   - Added a live "Tracking: X.X° / 5° needed (YY%)" progress line inside the challenge box — users can see exactly how much movement they need to make.
   - Added console.log statements throughout: FaceMesh init, landmark state, baseline values, per-second challenge delta, pass/fail outcome, and full challenge timeout reason.

### Fixed — Liveness score 34% explained

The formula `0.6 × anti_spoof_score + 0.4 × challenge_completed = 0.6 × 0.57 + 0.4 × 0 = 0.342 = 34%` confirms `challengeCompleted` was always false. Root causes 1 and 2 above are why it never became true for a real user.

### Fixed — Dashboard stat cards unused space

- `templates/dashboard/index.html`: Changed stat card row from `row-cols-xl-6` (always 6 columns = tiny cards with 4 rendered) to responsive `col-12 col-sm-6 col-md-4 col-xl` per card.
- `col-xl` (Bootstrap 5) applies `flex: 1 0 0%` at XL+ — cards distribute equally across the full row regardless of how many are shown (4 cards → each 25%, 6 cards → each 16.7%), eliminating the empty gap.
- Responsive: 1 per row on mobile, 2 on SM, 3 on MD, auto-fill on XL+.

### Installer — 2026-05-24 18:33:00

| Item | Value |
|---|---|
| Path | `C:\FANS\FANS-C-Installer\FANS-C-Setup.exe` |
| Size | 208.5 MB (218,599,001 bytes) |
| SHA-256 | `BB92DCADE607AF30B2439727409E855801B05D286A67F20B3D45BCD6987945BF` |
| liveness.js hash | `dd607dce1fc5` |
| verify.js hash | `78caa37a1510` |

---

## [2.1.4] — 2026-05-24 (Liveness Strict Mode / Retry Button / Anti-Spoof Return Dict)

### Fixed — Liveness: Strict mode no longer auto-accepts when head tracking unavailable

- **`static/js/verify.js` — challenge timeout auto-accept blocked in strict mode**: The 5-second head-movement challenge timer previously set `challengeCompleted = !mpAvailable`, meaning a timeout when MediaPipe was unavailable auto-passed the challenge (`challengeCompleted = true`). In strict mode (`LIVENESS_REQUIRED=True`), this caused phone screens and printed photos to pass liveness because the texture anti-spoof heuristic gives high scores to sharp images (higher = more live, not more suspicious). Fixed: timeout now sets `challengeCompleted = !mpAvailable && !LIVENESS_REQUIRED`. In strict mode, head tracking unavailable is always a challenge fail.
- **`static/js/verify.js` — step 2 message**: When tracking is unavailable and challenge failed, the UI now shows "Head tracking unavailable — liveness challenge could not be verified" instead of the previous "Challenge accepted" wording.
- **`static/js/verify.js` — liveness result card**: Shows "Liveness challenge could not be verified. Please retry with your full face visible and camera tracking enabled." when tracking unavailable in strict mode.

> **Known limitation:** If MediaPipe IS available and the operator physically tilts the phone during the movement challenge, MediaPipe may detect the tilt and mark `challenge_completed=True`. Fully preventing this requires a trained Presentation Attack Detection (PAD) CNN model. The texture-based anti-spoof heuristic cannot distinguish a tilted phone screen from a tilted real face. This limitation is documented in `verification/liveness.py`.

> **Anti-spoofing score direction:** Higher score = more likely a real/live face. Score 1.0 = very real. Phone screens score high because they are very sharp. Do **not** treat a high score as suspicious.

### Fixed — Hard liveness gate: registered-person's photo on a phone no longer passes verification

The main path that allowed a phone displaying the registered beneficiary's own photo to pass verification was the MP-unavailable auto-accept described above. With that fix applied, `challenge_completed` is now `False` when tracking is unavailable in strict mode. `server_liveness_passed = anti_spoof_passed AND challenge_completed = True AND False = False`. The server denies before face matching is ever called (`process_face_for_verification` is not invoked when `server_liveness_passed=False` in strict mode — this hard gate was already in place).

### Fixed — Retry button unclickable after first verify attempt

- **`static/js/verify.js` — retry handler**: `verifyBtn.disabled = true` was set on click and never cleared in the retry decision branch. When the button was re-shown after the next capture cycle it remained disabled (non-clickable). Fixed: added full button reset (`disabled = false`, `removeAttribute('aria-disabled')`, `style.pointerEvents = ''`, `innerHTML`, `className` reset, `processingOverlay` hidden) before hiding the button in the `retry` handler.
- **`static/js/verify.js` — show verify button block (startBtn handler)**: Same full reset applied before `verifyBtn.style.display = ''` so the button is always interactive when shown.
- **`static/js/verify.js` — `resetToCaptureFlow()`**: Same three-line reset (`disabled`, `aria-disabled`, `pointerEvents`) added to cover programmatic resets.

### Fixed — Dead liveness card condition

- **`static/js/verify.js` — liveness card `else if` branch**: The pre-check (`verify_check_liveness`) always sends `challenge_completed=false`, so `livenessApiResult.passed` from the pre-check is always `False`. The `else if (!livenessApiResult.passed)` branch was always entered, making the separate "head movement not completed" message dead code. Fixed: changed condition to `else if (!livenessApiResult.anti_spoof_passed)` so the anti-spoof failure card and the challenge-failure card are correctly distinguished.

### Fixed — `anti_spoof_passed` missing from liveness return dict

- **`verification/liveness.py` — `run_full_liveness_check()` return dict**: `anti_spoof_passed` was not included in the returned dictionary. The caller (`verify_check_liveness` in `views.py`) used a fragile fallback: `liveness_result.get('anti_spoof_passed', liveness_result['anti_spoof_score'] >= anti_spoof_threshold)`. Fixed: added `'anti_spoof_passed': spoof_passed` explicitly to the return dict.

### Fixed — Strict-mode denial reason now identifies the specific failure

- **`verification/views.py` — `verify_submit` strict mode denial**: The denial reason was a single generic line. Replaced with three specific branches:
  - Both anti-spoof and challenge failed: states score is below threshold and challenge was not completed.
  - Only anti-spoof failed: "possible low-quality capture, obstructed face, or spoof attempt."
  - Anti-spoof passed but challenge not completed: "A phone screen, printed photo, or replay attack cannot complete the movement challenge." (This is the specific message for the high-texture-score / no-movement path.)
- The specific denial reason is now logged via `_log_verify` instead of the previous generic fallback string.

### Tests

- **`verification/tests.py` — `LivenessStrictModeTest`** (8 new tests): Covers all branches of the v2.1.4 fix:
  1. Failed anti-spoof + strict mode → denied before face matching
  2. Failed anti-spoof + non-strict mode → face matching still runs
  3. Anti-spoof passed, challenge not completed + strict mode → denied; denial reason mentions phone/screen/replay
  4. Anti-spoof passed, challenge completed + strict mode → continues to face matching
  5. Borderline live face → manual review (requires `VERIFICATION_THRESHOLD=0.75`)
  6. Anti-spoof passed, challenge not completed + non-strict mode → face matching still runs
  7. Anti-spoof passed, challenge not completed → denial reason mentions challenge, not "below threshold"
  8. Failed liveness denies even when face would match (hard gate: `process_face_for_verification` not called)
- Total: **316 tests, 0 failures** (308 prior + 8 new).

### Build

- Installer: `FANS-C-Setup.exe` — built 2026-05-24 15:34:20 — 196.76 MB
- SHA-256: `759F654C092FBC8F66305D400675B5D3C55D642DEA2BFBFCD06F363886E998C6`

---

## [2.1.5] — 2026-05-24 (Liveness UI / Dashboard Layout)

### Fixed — Liveness UI: strict mode fast path eliminated

- **`static/js/verify.js` — `needsChallenge`**: Added `|| LIVENESS_REQUIRED` to the condition. Previously the fast path (`!needsChallenge`) was reached in strict mode whenever anti-spoof passed and no other risk signal was present. The fast path auto-set `challenge_completed = true` and showed "No challenge required — anti-spoof passed" with a 100% liveness score. In strict mode this was both misleading (the challenge was never actually run) and a security gap (the server received `challenge_completed=true` without any real head movement). Now when `LIVENESS_REQUIRED=true`, the challenge block always runs and the actual movement result is used.
- **`static/js/verify.js` — `challengeReason`**: Added `else if (LIVENESS_REQUIRED)` branch. When strict mode is the reason the challenge runs (anti-spoof passed, no other trigger), the guidance panel now shows: "Anti-spoof pre-check passed (X%) — head movement challenge is required in strict liveness mode."
- **`static/js/verify.js` — `step2Msg` after challenge**: When MediaPipe is available but no movement was detected in strict mode AND anti-spoof passed, step 2 now shows "Anti-spoof pre-check passed, but liveness challenge is still required." instead of the generic "Challenge failed — no head movement detected."
- **`static/js/verify.js` — liveness result card**: Same specific message ("Anti-spoof pre-check passed, but liveness challenge is still required.") shown in the result card for the same scenario. The "Liveness challenge could not be verified" message for MP-unavailable and the "Anti-spoofing score too low" message for failed anti-spoof are unchanged.
- **`static/js/verify.js` — verify button**: Verify button now shows red "Process Verification (Liveness Failed)" when `!livenessData.passed && LIVENESS_REQUIRED`. Previously it showed green even when strict mode + liveness failed, giving no visible indication that the server would deny. Yellow warning button remains for non-strict mode liveness failures.
- **`static/js/verify.js` — fast path comment**: Updated to clearly document it is dead code when `LIVENESS_REQUIRED=true`.

### Fixed — Dashboard: 5 stat cards no longer orphan on wide screens

- **`templates/dashboard/index.html` — stats row**: Changed from `<div class="row g-3 mb-4">` with `col-6 col-xl-3` cards (4-column max → 4+1 orphan layout) to `<div class="row g-3 mb-4 row-cols-1 row-cols-sm-2 row-cols-md-3 row-cols-xl-5">` with plain `col` cards. Responsive layout: 1 per row (mobile), 2 per row (sm), 3+2 (md), 5 in one row (xl+).

### Tests

- **316 tests, 0 failures** — no new tests added (changes are UI/template only, no backend logic changed).

### Build

- Installer: `FANS-C-Setup.exe` — built 2026-05-24 16:23:47 — 196.76 MB
- SHA-256: `EEB76240EA5B870B14C239DA45F66133B64509E617CED78B4579F551E7703795`

---

## [2.0.7] — 2026-05-22 (HTTPS Proxy Fix / Audit Log Root Cause / Error Logging)

### Fixed — Critical: HTTPS Proxy Detection

- **`fans/settings.py` — `SECURE_PROXY_SSL_HEADER` default**: When `SECURE_PROXY_SSL_HEADER` was absent from `.env` (all installs upgraded from versions ≤ 2.0.5), Django never set `request.is_secure()` to True for Caddy-forwarded HTTPS requests. The Limited HTTP mode banner always appeared, and camera verification was always blocked, even on valid HTTPS. Fixed by defaulting to `HTTP_X_FORWARDED_PROTO,https` in `settings.py` itself so existing `.env` files work correctly. Opt-out via `SECURE_PROXY_SSL_HEADER=off` if running without a reverse proxy.
- **`fans/settings.py` — `USE_X_FORWARDED_HOST`**: Defaulted to `False`; changed to `True` so `request.get_host()` returns `fans-barangay.local` (not `127.0.0.1:8000`) when accessed through Caddy.
- **`Caddyfile` — forwarded headers**: Added `X-Real-IP`, `X-Forwarded-For`, and `X-Forwarded-Host` headers to the Caddy reverse proxy block alongside `X-Forwarded-Proto`. `X-Forwarded-Proto` now uses `{scheme}` instead of the hardcoded `https` literal — correctly sends `http` when accessed over plain HTTP.

### Fixed — Critical: Audit Log 500 Error

- **`logs/views.py` — logger name**: `audit_log_list` used `logging.getLogger('verification')`, which has no file handler in the current logging config, so audit log exceptions were never written to `django-errors.log`. Changed to `logging.getLogger('logs')`.
- **`fans/settings.py` — file-based error logging**: Added `RotatingFileHandler` (`logs/django-errors.log`, 5 MB, 3 backups) to the `logs`, `verification`, `fans`, and `django.request` loggers. Tracebacks are now captured to disk in the installed EXE (no console). The `logs/` directory is created automatically on startup if absent.
- **`logs/templatetags/fans_filters.py` — robust details rendering**: `format_audit_details` filter now handles all types Django's `JSONField` can return: `dict`, `list`, `str`, `int`, `float`, `bool`, and `None`. No exception propagates to the template engine regardless of content.
- **`dev/fans_c.spec` — hidden imports**: Added `logs.templatetags` and `logs.templatetags.fans_filters` to `hiddenimports`. Without this, Django's template engine cannot find the `{% load fans_filters %}` tag library inside the PyInstaller bundle, causing the audit log template to fail with `TemplateDoesNotExist` or `InvalidTemplateLibrary`.

### Added — HTTPS Diagnostics

- **`fans/views.py` — `system_connection` view**: Diagnostic panel now shows `request.scheme`, `request.is_secure()`, `request.get_host()`, `HTTP_HOST`, `X-Forwarded-Proto`, `X-Forwarded-Host`, `X-Forwarded-For`, `X-Real-IP`, active `SECURE_PROXY_SSL_HEADER`, `USE_X_FORWARDED_HOST`, and camera verification status. Accessible at `/system/connection/` (IT Admin only).
- **`templates/system/connection.html`**: Added "HTTPS Proxy Diagnostics" card.

### Tests

- **`fans/tests.py` — `HttpsProxyDetectionTest`** (8 new tests): HTTPS proxy header sets `request.is_secure()`; no Limited HTTP banner on HTTPS; HTTP fallback shows Limited HTTP banner; "Open Secure HTTPS" link targets `https://fans-barangay.local`; `_camera_verification_allowed()` returns True for HTTPS, False for HTTP; audit log accessible on HTTP and HTTPS.
- **`logs/tests.py` — `AuditLogDetailsRobustnessTest`** (16 new tests): All `JSONField` types (`dict`, `list`, `str`, `int`, `float`, `bool`, `None`, nested, empty, very long), pagination edge cases (page 2, invalid page number, large page number), all action types, missing user record.
- **`logs/tests.py` — `AuditLogHttpsTest`** (2 new tests): Audit log loads via HTTPS proxy header; loads via plain HTTP.
- **`verification/tests.py` — `CameraVerificationRulesTest`** (5 new tests): Camera blocked on HTTP production, allowed on HTTPS, allowed in DEBUG, `verify_submit` rejected on HTTP, no `ClaimRecord` created without active payout event.
- Total: 213 tests, 0 failures.

---

## [2.0.3] — 2026-05-22 (Security / Verification / UX Hardening)

### Security — Critical

- **`verification/views.py` — face-match threshold default**: `_get_demo_mode()` defaulted to `True` (Assisted Rollout Mode), silently lowering the face-match threshold to 0.60 when `DEMO_MODE` was absent from settings. Changed default to `False` (strict mode, threshold 0.75).
- **`verification/views.py` — liveness required default**: `_get_liveness_required()` defaulted to `False`, meaning liveness was non-blocking when the setting was absent. Changed default to `True`.
- **`verification/models.py` — SystemConfig threshold**: `SystemConfig.get_threshold()` used `DEMO_MODE` default `True`, causing the same silent threshold downgrade. Changed to `False`.

### Security — Liveness Anti-Spoofing

- **`fans/settings.py` — anti-spoof threshold**: Raised `ANTI_SPOOF_THRESHOLD` default from `0.15` to `0.25`. The previous value was permissive enough for printed photos to pass.
- **`static/js/verify.js` — challenge timeout auto-accept**: The 5-second head-movement challenge timer previously set `challengeCompleted = true` on timeout regardless of whether movement was detected — allowing a static printed photo to pass. Fixed: timeout now sets `challengeCompleted = false` when MediaPipe is available (genuine movement tracking). Timeout still accepts when MediaPipe fails to load (accessibility fallback).

### Fixed — Dashboard

- **`beneficiaries/views.py` and `verification/views.py` — timezone bug**: `timezone.now().date()` was used for all "today" comparisons, which returns UTC date instead of Asia/Manila date. Between midnight–8 AM Manila time (UTC+8), verifications were counted on the wrong day. Changed to `timezone.localdate()` in 7 locations.

### Fixed — Validation

- **`accounts/forms.py` — phone validation**: `UserCreateForm` and `UserUpdateForm` accepted any characters in the `phone` field. Added `clean_phone()` with Philippine mobile number regex (`09XXXXXXXXX` or `+639XXXXXXXXX`).
- **`beneficiaries/forms.py` — phone validation**: `BeneficiaryInfoForm`, `BeneficiaryEditForm`, and `RepresentativeForm` phone/contact fields now validate Philippine mobile format.
- **`beneficiaries/forms.py` — ID number validation**: `senior_citizen_id`, `valid_id_number`, `rep_id_number` fields now validated with `_validate_id_number()` — alphanumeric + hyphens/spaces/slashes, max 50 characters.

### Fixed — UX

- **`templates/admin_panel/user_form.html`** and **`templates/accounts/create_admin.html`**: Added show/hide password toggle button on all password fields.
- **`beneficiaries/forms.py`** and **`templates/beneficiaries/register_step1.html`**: Municipality/city field placeholder updated to clarify free-text entry is allowed; helper note added.
- **`templates/verification/verify_capture.html`**: Added "Live face only — no printed photos or screens" tip; added camera-initializing spinner hint for disabled Capture button with meaningful camera-error message; updated challenge timer text to remove misleading "auto-accepts" wording.
- **`static/js/verify.js`**: Camera error now sets descriptive hint on the disabled button. `startBtnHint` is hidden once camera is ready.

### Added — Auto-Approval Workflow

- **`verification/models.py`** — `SystemConfig.get_bool()` and `SystemConfig.set_value()` helpers.
- **`logs/models.py`** — Three new audit actions: `auto_approved`, `record_approved`, `record_rejected`.
- **`beneficiaries/views.py`** — Five new views: `auto_approval_settings`, `pending_approvals`, `approve_record`, `reject_record`, `bulk_approve`.
- **`beneficiaries/urls.py`** — New URL routes for the approval workflow.
- **`templates/admin_panel/auto_approval_settings.html`** — Toggle UI for four auto-approval keys; warns that verification security is unaffected.
- **`templates/admin_panel/pending_approvals.html`** — Pending beneficiaries and user accounts queue with per-row approve/reject and bulk-approve.
- Registration workflow (`register_submit_face`) auto-activates beneficiaries when `auto_approve_beneficiaries = true`.
- All auto-approval toggles default **OFF** (`false`). Only `is_admin` roles (head_brgy / admin_it) can change them.

### Tests

- 29 new tests added (total 56, all passing):
  - `SecurityDefaultsTest` — `_get_demo_mode`, `_get_liveness_required`, and `SystemConfig.get_threshold` defaults.
  - `DashboardTimezoneTest` — Dashboard uses `timezone.localdate()` not UTC.
  - `PhoneValidationTest` — Valid/invalid PH phone numbers in `UserCreateForm` and `BeneficiaryInfoForm`.
  - `IdNumberValidationTest` — Valid ID numbers accepted; SQL-injection-style IDs rejected.
  - `AutoApprovalSettingsTest` — Settings page access control; toggle persistence; pending approvals queue access.
  - `SystemConfigGetBoolTest` — `get_bool()` correct for `true`/`false`/missing/case-insensitive.

---

## [2.0.2] — 2026-05-21 (Bug Fix / Defensive Hardening)

### Fixed

- **`verification/face_utils.py` — root crash fix**: Face registration failed
  with `Face processing error: 'NoneType' object has no attribute 'write'` when
  running from the PyInstaller no-console bundle. Cause: `warnings.warn()` and
  `_MockFaceNet.embeddings()` both called `sys.stderr.write()` internally, but
  PyInstaller sets `sys.stderr = None` in windowed (no-console) mode. All
  `warnings.warn(...)` calls replaced with `logging.getLogger('verification').warning(...)`.
  Now routes to the configured file handler in every environment.

- **`verification/apps.py` — startup crash**: Background FaceNet warmup thread
  used `print(..., file=sys.stderr)`. Replaced with `logging.getLogger('verification')`
  calls so warmup diagnostics are written to the log file, not sys.stderr.

- **`fans/settings.py` — startup crash**: Module-level `print(..., file=sys.stderr)`
  and `warnings.warn(...)` startup diagnostics replaced with `logging.getLogger('fans.settings')`
  calls; critical-only lines guarded with `if sys.stderr is not None:`.

- **`verification/views.py` — stdout crash**: `print(..., flush=True)` calls in
  `verify_submit` replaced with `logger.info()` / `logger.debug()` (PyInstaller
  also sets `sys.stdout = None` in no-console mode).

- **`verification/face_utils.py` — mock check order**: In
  `process_face_for_registration()`, the `is_using_mock_model()` check now runs
  before `get_embedding()` to prevent the mock model's `warnings.warn()` from
  being triggered at all.

### Added

- **Input validation — base64 decode**: All three face-capture POST endpoints
  (`beneficiaries:register_submit_face`, `verification:update_face_submit`,
  `verification:register_rep_face_submit`) now wrap `base64.b64decode()` in
  try/except. A malformed or truncated base64 payload returns a clean
  `{"success": false, "error": "Invalid image data..."}` JSON response instead
  of an unhandled exception.

- **Input validation — empty image guard**: `process_face_for_registration()`
  now returns a user-friendly error immediately for empty or `None` image bytes
  rather than propagating to PIL/NumPy and raising a confusing internal error.

- **Regression tests** (`verification/tests.py`): 17 new tests added covering:
  - `process_face_for_registration()` with empty bytes, `None`, no-face ValueError,
    low-quality image, and mock model active (the original crash scenario).
  - All three face-capture views: no-image, invalid base64, data-URI prefix
    stripping, missing session, unauthenticated redirect, 404 on bad PK.
  - All new tests mock the ML pipeline — no TensorFlow model is loaded.
  - Full suite: `python manage.py test verification` → 27 tests, 0 failures.

---

## [2.0.1] — 2026-05-20 (Maintenance / Hardening)

### Added
- **Test suite** — 52 automated tests across `fans`, `accounts`,
  `verification`, and `logs` apps covering URL routing, access
  control, model creation, role helpers, and password validation.
  No webcam or TensorFlow model required to run tests.
- **`scripts/admin/verify-installation.ps1`** — 18-check
  installation health script covering Python version, all required
  package imports, Django settings load, pending migrations,
  staticfiles manifest, Caddy/mkcert binaries, TLS certs, `.env`
  key validation, and writable runtime directories.
- **`scripts/admin/run-smoke-tests.ps1`** — 6 non-destructive
  pre-deployment checks: pip check, Django system check, deploy
  check, migration drift check, collectstatic dry-run, and Django
  test runner. Exits non-zero on any failure for use in CI.
- **`docs/MAINTENANCE-PLAN.md`** — project map, bugs-found log,
  recurring maintenance schedule.
- **`docs/DEPLOYMENT-CHECKLIST.md`** — step-by-step pre/post
  deployment verification procedure.
- **`docs/SECURITY-CHECKLIST.md`** — categorized security checks
  and open-risk register.
- **`docs/DATABASE-GUIDE.md`** — SQLite vs PostgreSQL selection,
  backup/restore procedures, migration rules, schema reference.

### Fixed
- **`requirements.txt`** — added `requests>=2.31.0` (was an
  implicit transitive dependency but not declared; `beneficiaries/
  sync.py` imports it directly).
- **`dev/build_exe.ps1`** — corrected `$distDir` from
  `dist\FANS-C` to `dist\fans_c` to match the PyInstaller spec
  output path; updated all post-build messages accordingly.
- **`dev/fans_c.spec`** — removed `accounts.decorators` from
  `hidden_imports` (`accounts/decorators.py` was deleted in v2.0
  but the spec was never updated, causing a spurious warning).
- **`.gitignore`** — removed duplicate `.env` entry and spurious
  `=3.1.2` line; added section comments; noted that
  `staticfiles/` is intentionally committed.

---

## [2.0] — 2026-05 (Pre-Release)

### Added
- **One-click Windows installer** (`FANS-C-Setup.exe`) — bundles
  the entire system including Python runtime, TensorFlow, Caddy,
  mkcert, Django, and all dependencies. No prerequisites needed
  on the target machine.
- **First-run setup wizard** — automatically generates security
  keys, runs migrations, creates HTTPS certificate, creates admin
  account, and registers autostart on first launch.
- **logs Django app** — permanent tamper-evident audit trail.
  Audit Log and Verification Log accessible to Head Barangay and
  IT/Admin via Navbar → Logs.
- **Representative face enrollment** — authorized representatives
  can have their own face enrolled to claim on behalf of a
  beneficiary.
- **Sync conflict resolution** — admin interface for reviewing
  and resolving beneficiary records that failed to sync from
  offline workstations.
- **Staff Performance reports** — per-staff verification
  statistics.
- **Override & Fallback reports** — all manual overrides and
  fallback ID checks.
- **Suspicious Attempts reports** — flagged verification attempts
  for fraud review.
- **Beneficiary History reports** — per-beneficiary full
  verification and claim history.
- **Deploy-ready security** — `python manage.py check --deploy`
  returns 0 issues (4 silenced checks handled by Caddy).
- **Styled error pages** — templates/errors/ 400, 403, 404, 500
  pages extending base.html.
- **SILENCED_SYSTEM_CHECKS** — W004, W008, W012, W016 suppressed
  with documented justification (Caddy handles at proxy layer).

### Changed
- **Role system updated** — three active roles: `head_brgy`,
  `admin_it`, `staff`. Legacy `admin` role migrated via
  migration accounts/0006.
- **Staff face verification gate removed** — staff log in
  directly to the dashboard after password authentication.
  The step-up biometric gate was removed (unenrolled staff,
  file-upload UI regression, password auth is sufficient).
- **Static folder consolidated** — `static/img/` removed,
  `static/images/` is the single active image folder.
- **Custom decorators removed** — `accounts/decorators.py`
  deleted; inline role-check pattern is the established
  convention throughout views.
- **Database** — PostgreSQL is the recommended production
  database. SQLite remains the default for local/demo use.

### Fixed
- `NoReverseMatch` crash on dashboard — removed all leftover
  `verification:face_verify` references from 9 views across
  `beneficiaries/views.py` and `verification/views.py`.
- `beneficiaries` migration 0009 — `sync_error` field
  alteration applied.
- Spurious `=3.1.2` file removed from project root.
- `folder-django-app.md` corrected — was missing `logs` app,
  had phantom `@staff_required` reference, incorrectly stated
  decorators were applied to views.
- `folder-static-templates.md` corrected — `static/img/`
  changed to `static/images/`.

### Security
- `SECRET_KEY` — setup script and installer generate a proper
  50-character random key. Placeholder key causes startup
  refusal when `DEBUG=False`.
- `EMBEDDING_ENCRYPTION_KEY` — server refuses to start in
  production if key is missing (RuntimeError).
- `DEBUG=False` enforced in production. `SECURE_COOKIES`
  auto-enabled when `DEBUG=False`.

---

## [1.0] — 2026-03 (Initial)

### Added
- Initial FaceNet-based facial verification system
- Django web application with Waitress + Caddy deployment
- Beneficiary registration with face enrollment
- Real-time webcam verification
- Basic role-based access control
- SQLite database with Fernet-encrypted embeddings
- MTCNN face detection and liveness detection
- Stipend event management and claims processing
- Basic audit logging
- PowerShell setup and startup scripts
- Self-healing watchdog service
- Windows Task Scheduler autostart
