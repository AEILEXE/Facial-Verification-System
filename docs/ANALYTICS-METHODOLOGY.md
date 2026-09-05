# FANS-C Analytics Methodology

This is the factual reference for every metric, chart, and rule-based
indicator FANS-C presents as "Analytics." It exists so the capstone paper,
defense, and any future maintainer can cite an exact data source and
formula instead of re-deriving one from template text. Written from the
actual implementation as of the Phase A analytics-correctness checkpoint
(2026-09-02) — not from feature descriptions or intent.

Source files: `verification/analytics.py` (Executive/Operational/Security
aggregates), `verification/fraud_signals.py` (rule-based risk indicators),
`verification/template_analytics.py` (face-template match analytics),
`verification/views.py` (`analytics_executive`, `analytics_operational`,
`analytics_security`, `fraud_signals_report`, `template_match_report`),
`beneficiaries/views.py` (`dashboard`).

## 0. Scope boundary

FANS-C Analytics is **descriptive, operational, and rule-based**. Every
metric in this document is one of:

- a **count** (how many records match a defined condition),
- a **rate** (a ratio of two counts, e.g. verified / total attempts),
- a **distribution** (counts broken down by category or time bucket),
- a **trend** (a distribution over time), or
- a **rule-based indicator** (a threshold crossing, expressed as a
  0-100 explainable score — not a machine-learned probability).

**Safe claims**: FANS-C aggregates existing records into counts, rates,
distributions, and trends; it flags threshold-crossing patterns for human
review; every flag links to the underlying records so an admin can verify
it manually.

**Claims FANS-C must not make**: predictive analytics, machine-learned
fraud prediction, automatic fraud determination, statistical significance
testing, causal inference, or automatic biometric re-enrollment. None of
these exist anywhere in the codebase as of this writing. A HIGH-risk
security indicator means a rule's configured threshold was exceeded by a
wide margin — it does not mean fraud occurred, and no such indicator ever
blocks, suspends, or denies anything automatically (see fraud_signals.py's
own module docstring, which states this explicitly).

## 1. Executive Analytics (`get_executive_metrics`, `verification/analytics.py`)

Admin-only, at `/verification/analytics/executive/`. Accepts optional
`date_from`/`date_to` (inclusive both ends, see §5).

| Metric | Population counted | Scope |
|---|---|---|
| Total Beneficiaries | Every `Beneficiary` row, any status | Lifetime (unfiltered) |
| Active / Pending / Inactive / Deceased Beneficiaries | `Beneficiary.status` breakdown | Lifetime |
| Active Staff/Admin Accounts | `CustomUser` where `is_active=True` and `account_status=ACTIVE`, **all roles** (President, Admin, IT, Staff) — not "staff and admin" narrowly, despite the label; every login-capable role in this system falls under this umbrella since beneficiaries have no login account | Lifetime |
| Verification Attempts | `VerificationAttempt` rows, any decision (verified/not_verified/manual_review/denied) | Selected range (by `timestamp`) |
| Verification Success Rate | `100 × verified / total_attempts`, where `total_attempts` is ALL decisions in range, not just resolved ones. `None` (rendered "—") when `total_attempts == 0` | Selected range |
| Routed to Manual Review | Count of `VerificationAttempt` rows with `decision=manual_review` | Selected range (event count, not unique cases or unique beneficiaries — the same beneficiary retrying twice counts twice) |
| Total Released | `Sum(amount)` over `ClaimRecord` rows with `status=claimed` | Selected range (by `claimed_at`) |
| Active Stipend Event | The `StipendEvent` whose date+time payout window contains right now (Asia/Manila) | Point-in-time (now), not range-scoped |
| Beneficiary Growth (chart) | Monthly new registrations + running cumulative total | Fixed trailing 365 days — independent of the date-range selector, which is a short-window filter for the stat tiles above; a 12-month trend is more useful un-narrowed |
| Monthly Distribution (chart) | Monthly `Sum(amount)` of claimed `ClaimRecord`s | Fixed trailing 365 days, same reasoning |
| Claim Progress / Payout Completion | Eligible-beneficiary count vs. claimed count for the current (or nearest upcoming) event | Point-in-time, scoped to one event, not the date-range selector |

**Verification Success Rate — denominator justification.** The denominator
is every attempt recorded in the range, including `not_verified`,
`manual_review`, and `denied`. This was deliberately kept as-is rather than
narrowed to "resolved" attempts: the rate answers "what share of attempts
were fully auto-verified with no human step," which is the number that
matters operationally (it tracks how much manual-review workload the
system is generating). Renaming was considered unnecessary because the
label "Verification Success Rate" already reads correctly under this
definition once "success" is understood as "auto-verified, no manual
step" — the alternative (narrowing the denominator) would answer a less
useful question. Attempts that never generate a `VerificationAttempt` row
at all (a browser tab closed mid-flow before submission) are not counted
in either the numerator or denominator — the metric only describes
attempts that reached a stored decision.

**Active Stipend Event — the two "no event" cases.** `get_active_event_now()`
requires the event's date window to include today AND (if
`payout_start_time`/`payout_end_time` are set) the current time to fall
inside the daily claiming window. An event dated today but outside its
claiming window (e.g. checked at 9 PM against a 7 AM-8 PM window) is
distinguished from no event existing at all — the Executive tab shows "a
payout is scheduled today, but outside its claiming time window right now"
in the first case, and "No payout scheduled today" only in the second.
(Fixed in this checkpoint — previously both cases showed the same "No
payout scheduled today" text, which was false in the first case.)

## 2. Operational Analytics (`get_operational_metrics`)

Admin-only, at `/verification/analytics/operational/`.

| Metric | Population | Notes |
|---|---|---|
| Decision Breakdown | `VerificationAttempt` grouped by `decision` | Selected range |
| Verification Volume (Daily) | Daily `total`/`verified` count of attempts | Selected range; **zero-filled** when both `date_from` and `date_to` are given (see §5) |
| New Registrations (Daily) | Daily `Beneficiary.created_at` count | Selected range; zero-filled under the same rule |
| Top Staff by Verification Volume | Top 10 `performed_by` users by attempt count | Selected range. Deliberately labeled "Volume," not "Performance" — a count of verifications performed says nothing about accuracy, speed, or quality of work, and this project does not compute or claim any of those |

## 3. Security Analytics (`get_security_metrics`)

Admin-only, at `/verification/analytics/security/`. **Different parts of
this page use different time scopes — this is the single most important
thing to get right when citing this page**, see §5.

| Metric | Population | Scope |
|---|---|---|
| Failed Logins / Duplicate Face Detections / Blocked Duplicate Payout Attempts / Payout Overrides / Config Changes | `AuditLog` action counts | Selected range |
| Manual Review Queue | `VerificationAttempt` where `decision=manual_review`, current | Point-in-time (current queue depth, not range) |
| Beneficiaries with 3+ Attempts | Distinct beneficiaries with ≥3 `VerificationAttempt` rows | Selected range |
| Security Risk Indicators (HIGH/MEDIUM/LOW) | See §4 | **Fixed lookback window per rule** (not the date-range selector — see §5) |
| Review & Security Cases — Duplicate Face / Manual Review / Representative Review | Current open-queue counts (`duplicate_review_required=True`, manual-review pending, `SharedRepresentativeReview.status=pending`) | Point-in-time |
| Review & Security Cases — Fraud Alerts | Count of MEDIUM+HIGH rows only (LOW excluded — see §4) | Fixed lookback window per rule |

## 4. Security Risk Indicators (`verification/fraud_signals.py`)

Five independent rule-based checks, each settings-driven
(`FRAUD_*_THRESHOLD` / `FRAUD_*_WINDOW_DAYS`/`_HOURS` in `fans/settings.py`,
`.env`-overridable):

| Signal | Counts | Window (default) |
|---|---|---|
| Repeated Verification Failures | `not_verified`/`denied` attempts per beneficiary | trailing 30 days, threshold 3 |
| Anomalous Staff Verification Volume | attempts per staff member | trailing 24 hours, threshold 50 |
| Admin Mass-Edit Detection | user/payout/config/approval-edit `AuditLog` entries per admin | trailing 1 hour, threshold 20 |
| Repeated Login Failures | failed-login `AuditLog` entries per IP | trailing 24 hours, threshold 10 |
| Payout Anomalies | override/cancel/fail/duplicate-block/fallback `AuditLog` entries per staff/admin | trailing 24 hours, threshold 5 |

**Scoring**: `score = min(100, round(count / threshold × 30))` — exactly at
threshold scores 30, at 3.33× threshold caps at 100. **Bands**: LOW 0-30,
MEDIUM 31-70, HIGH 71-100. This is an explicit, fully explainable point
score ("how far past the configured line is this"), not a black-box or
machine-learned probability — there is no model behind it and no claim
that a HIGH score means fraud occurred.

**Note on overlapping rules**: `ACTION_PAYOUT_OVERRIDE` audit entries count
toward both Mass-Edit Detection and Payout Anomalies. This is intentional,
not a double-counting bug — the two rules ask different questions ("is
this admin editing an unusual amount of stuff in general" vs. "is this
admin doing an unusual amount of payout-specific overrides/failures
specifically"), and the same underlying event can genuinely be relevant to
both questions. An admin who trips both rules over the same events will
appear as two separate rows.

**"Fraud Alerts" excludes LOW.** `sync_fraud_notifications()` (which
creates admin `Notification` rows) never notifies on LOW-risk rows, by
design, to avoid alert fatigue on borderline threshold crossings. The
"Fraud Alerts" count in Review & Security Cases now matches that same
cutoff (MEDIUM+HIGH only) — before this checkpoint it counted LOW rows
too, which inflated the "cases needing action" count with rows the system
itself does not consider worth flagging. LOW rows remain visible on the
full Security Review page (`/verification/reports/fraud-signals/`) for
completeness; they are simply not counted as an open case on the summary
tile.

**Never automatic**: no function in `fraud_signals.py` writes to any
account, beneficiary, or claim status. Every row only ever produces a
Notification for a human to review (regression-tested:
`FraudSignalsTest.test_signals_never_write_to_account_or_beneficiary_status`).

## 5. Date-range semantics

`_apply_date_range()` filters with `field__date__gte`/`field__date__lte` —
both bounds inclusive, and `__date` extracts the date component in the
active timezone (`TIME_ZONE = 'Asia/Manila'`, `USE_TZ = True`), so a record
timestamped late on the final selected day is correctly included, not
dropped at midnight UTC.

**Three different scopes coexist on the Security tab, and they do not all
follow the date-range selector**:
1. AuditLog-based counts and `high_attempt_beneficiaries` — follow the
   selected range.
2. Manual Review / Duplicate Face / Representative Review queue counts —
   always current (point-in-time), regardless of any date filter.
3. Security Risk Indicators and Fraud Alerts — each rule uses its own
   fixed trailing window measured from now (see §4's table), regardless of
   the date filter. This is intentional: "give me unusual-volume alerts
   from March 1-15" does not make sense for a rule whose entire premise is
   a trailing window from the present moment. The page text was corrected
   in this checkpoint to say so explicitly — it previously claimed fraud
   alerts "reflect the selected date range," which was false.

**Executive tab's 12-month charts** (Beneficiary Growth, Monthly
Distribution) are independent of the date-range selector by design — a
short selected window is not a useful basis for a 12-month trend chart.

**Invalid range (From after To)**: previously failed silently to an
all-zero page. Both `_apply_date_range` filters combine to match nothing,
which is safe (no wrong data), but reads as "no activity" rather than "the
range you asked for is impossible." A warning banner now appears on all
three Analytics tabs when `date_from > date_to`.

**Missing zero-value days**: `daily_trend` and `registration_trend`
(Operational tab) previously omitted days with no attempts/registrations
entirely, which visually compresses a real gap into evenly-spaced adjacent
points on a line/bar chart (e.g. two attempts 20 days apart would render
as if they were on consecutive days). Both series are now zero-filled for
every day in range **only when both `date_from` and `date_to` are
supplied** — an unbounded range has no natural start/end to fill between,
so it is left as-is, and a defensive cap (400 days) skips filling for
implausibly large ranges rather than allocating one row per day.

## 6. Template Match Analytics (`verification/template_analytics.py`)

Admin-only, at `/verification/reports/template-match/`. Advisory-only:
nothing here writes to `FaceEmbedding` or `AdditionalFaceEmbedding`, and no
re-enrollment ever happens automatically.

| Metric | Definition |
|---|---|
| Total Matched Attempts | Count of **VERIFIED** `VerificationAttempt` rows with a non-empty `matched_template` |
| Beneficiaries With a Recorded Match | Distinct beneficiaries appearing in the above |
| Matches Won by Primary Template | `100 × primary_matches / total_matches`, `None` ("—") when `total_matches == 0` |
| Re-enrollment Candidates | See below |

**Why "VERIFIED only" (fixed in this checkpoint)**: `matched_template` is
set in `verify_submit` (`verification/views.py`) from whichever stored
template scored highest in `compare_with_all_embeddings()`
(`verification/face_utils.py`) — this happens *before* that score is
compared against the verification threshold, so a `NOT_VERIFIED` or
`MANUAL_REVIEW` attempt can carry a non-empty `matched_template` too. Prior
to this fix, `get_template_win_stats()` counted every attempt with a
non-empty `matched_template` regardless of decision, which:
1. Mislabeled failed/unresolved comparisons as successful template
   "wins," and
2. Could surface the anti-spoof sentinel value
   `'beneficiary_face_on_rep_claim'` (set on a `DENIED` representative-claim
   cross-probe block — not a real stored face template) in the "Matches by
   Beneficiary and Template" table as if it were one.

Both are fixed by restricting the query to `decision=VERIFIED`, which also
brings this function in line with `get_reenrollment_candidates()`, which
already used that filter.

**Re-enrollment Candidates — exact rule.** Over the trailing 180 days
(`_RECENT_WINDOW_DAYS`), a beneficiary needs:
- at least 4 VERIFIED attempts (`_REENROLLMENT_MIN_ATTEMPTS`, added in this
  checkpoint), AND
- a non-primary template winning more than half of them.

**This is a project-defined heuristic — no statistical literature was
consulted to derive 180 days, 4 attempts, or the 50% cutoff.** They were
chosen for internal consistency with the appearance-drift signal (which
requires a minimum of 3 attempts on each side of its before/after
comparison) and to give "majority" a non-trivial sample to be majority
*of*. Before this checkpoint there was no minimum-attempts floor at all: a
beneficiary with exactly 1 verified attempt on a non-primary template
satisfied `1 win × 2 > 1 total` and was flagged from a single data point.
The floor does not eliminate small-sample risk entirely (4 attempts is
still a small sample), but it removes the most degenerate case. Advisory
only — `reenrollment_suggestion_for()` only ever produces a banner text a
staff member sees on the beneficiary's Update Face Data page; no automatic
action follows from either signal.

**Appearance drift** (`appearance_drift_flag_for`) is a separate advisory
signal: it compares the average similarity score of a beneficiary's
earliest 3 VERIFIED attempts against their most recent 3, flagging a drop
≥0.08. This exists specifically as an alternative to an image-based
age-estimation model — such models are trained/validated mostly on
younger demographics and are known to be less reliable for elderly faces
on low-quality webcam captures, which is exactly FANS-C's target
population (see `docs/FACE-VERIFICATION-INTELLIGENCE-AUDIT.md`).

## 7. Dashboard (`beneficiaries/views.py::dashboard`)

The Dashboard answers "what's happening operationally right now," not a
full analytics breakdown — it deliberately does not duplicate the
Analytics module beyond one shared aggregate:

- **Analytical**: the Beneficiary Status doughnut chart, which reuses
  `get_executive_metrics()['beneficiary_counts']` (the exact same
  aggregate the Executive tab computes) so the two numbers can never drift
  apart.
- **Operational preview, not analytics**: "Verification Attempts Today" /
  "Successful Verifications Today" (today only, staff see only their own
  via `performed_by=request.user`, admins see all), "Manual Review Queue"
  (current), upcoming stipend events (next 60 days).
- **Record/activity component, not analytics**: "Recent Activity" — the
  last 5 `AuditLog` rows, a raw event list with no aggregation.

## 8. Reporting vs. Analytics classification

| Page | Classification | Why |
|---|---|---|
| Analytics — Executive / Operational / Security | Analytics | Aggregates, rates, distributions, trends |
| Template Match Analytics | Analytics | Distribution + a computed rate (primary-template win share) + an advisory heuristic |
| Security Review (`fraud_signals_report`) | Rule-based review queue | Row-per-flagged-actor lists from the 5 signal functions — not aggregated further, so it's the detail view the Security tab's indicators link out to, not itself a distinct analytics surface |
| Payout Claims Report, Event Summary Report, Staff Distribution Activity Report, Override/Fallback Report, Suspicious Attempts Report, Beneficiary History Report | Reports | Structured record-level output (filterable lists), not computed aggregates — the "Staff Distribution Activity Report" (renamed from "Staff Performance Report" in Phase B.1) lists verification counts per staff member, i.e. activity volume, not a performance/quality judgment |
| Manual Review, Duplicate Review, Name/DOB Review, Shared Rep Review queues | Review queues | Individual pending items requiring action, not aggregated |
| Audit Logs | Activity/log | Individual events |

## 9. Capstone defense table

| Feature | Data | Calculation | Result meaning | Limitation | Analytics type |
|---|---|---|---|---|---|
| Verification Success Rate | `VerificationAttempt.decision` in selected range | `100 × verified / total` | Share of attempts that were fully auto-verified with no manual step | Denominator includes manual-review/failed/denied attempts by design (see §1); abandoned attempts with no stored row are excluded entirely | Rate |
| Manual Review volume | `VerificationAttempt` where `decision=manual_review` | Count | How many attempts needed a human decision | Counts events, not unique beneficiaries or open cases (a beneficiary retrying twice counts twice) | Descriptive aggregate |
| Beneficiary status distribution | `Beneficiary.status` | Count per status | Snapshot of the beneficiary registry's current composition | Lifetime snapshot, not date-scoped | Distribution |
| Verification trend | `VerificationAttempt.timestamp` by day | Daily count, zero-filled when range is bounded | Shape of verification activity over the selected period | Unbounded ranges are not zero-filled (see §5) | Trend |
| Distribution amount (Total Released) | `ClaimRecord.amount` where `status=claimed` | `Sum()` | Total PHP paid out in the selected range | Only counts fully claimed records; does not include pending/rejected/cancelled claims | Descriptive aggregate |
| Security risk indicators | `AuditLog`/`VerificationAttempt` counts per rule | `min(100, round(count/threshold×30))`, banded LOW/MED/HIGH | How far past a configured threshold an actor is, in a fixed trailing window | Explicitly not a fraud-probability score; HIGH ≠ confirmed fraud; never auto-blocks anything | Rule-based indicator |
| Template Match primary-template rate | `VerificationAttempt.matched_template` on VERIFIED attempts | `100 × primary_matches / total_matches` | How often the originally-enrolled photo, vs. a later re-enrolled one, is winning matches | Restricted to VERIFIED attempts only (fixed this checkpoint); a beneficiary with very few verified attempts contributes proportionally more variance to the global rate than one with many | Rate |
| Re-enrollment candidate heuristic | `VerificationAttempt.matched_template` on VERIFIED attempts, trailing 180 days | Non-primary majority (>50%) AND ≥4 attempts | Advisory suggestion that a beneficiary's primary photo may be stale | Project-defined heuristic, not statistically derived; 4 attempts is still a small sample; advisory only, never automatic | Advisory heuristic |

## 10. Testing

Analytics correctness is covered in `verification/tests.py`:
`AnalyticsDashboardTest`, `AnalyticsPhase13ChartsTest`,
`AnalyticsCorrectnessTest` (added this checkpoint — zero-fill, event/window
distinction, fraud-alert LOW exclusion, invalid-range flag),
`FraudSignalsTest`, `TemplateAnalyticsTest` (extended this checkpoint —
VERIFIED-only win stats, minimum-sample-size floor).
