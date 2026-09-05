# FANS-C Controlled Biometric Evaluation — Data Foundation & Workflow (BPA-1/BPA-2/BPA-2.1)

Status as of this writing: **data model + a two-stage, production-parity
data-collection workflow.** No Accuracy/FAR/FRR/TAR/EER value has ever been
computed by FANS-C. No REAL controlled biometric evaluation data has been
collected — only synthetic/mocked data used for automated testing exists.
This document describes the database foundation added in BPA-1, the
trial-collection workflow added in BPA-2, and the production-parity
correction made in BPA-2.1 (liveness policy, same-face binding,
representative support, and post-score decision-level audit), so a future
controlled evaluation can be conducted and its results computed truthfully
— it is not a results document and contains no results.

## 1. Purpose

The capstone manuscript's Chapter 3 promises technical metrics: Accuracy,
FAR, FRR, Verification Time, liveness/anti-spoof performance, Claim
Recording Reliability, and Manual Review Rate. Computing any of these
correctly requires data FANS-C's live operational tables do not capture.
BPA-1 adds the two models that make that data representable. It does not
compute any metric and does not collect any real evaluation data.

## 2. Why operational `VerificationAttempt` rows cannot produce FAR/FRR

`VerificationAttempt` (`verification/models.py`) records what the system
decided — `verified` / `not_verified` / `manual_review` / `denied` — for
every real claim attempt. It has no field for what the protocol
independently knows about who actually presented their face. A
`not_verified` row is silent on whether that was:

- a genuine enrolled beneficiary who was incorrectly rejected (a false
  reject), or
- an impostor who was correctly rejected (a true reject).

FAR (False Accept Rate) and FRR (False Reject Rate) are only computable
when **ground truth** (who really presented) is known independently of the
**system decision** (what FANS-C output). Live operational data never
carries that independent ground truth — a genuine beneficiary is claiming
their own stipend, and the system has no controlled way to also present
verified impostor attempts safely against real accounts. That is why a
controlled evaluation with deliberately staged genuine/impostor trials is
required, and why it must be modeled as data separate from operational
records.

## 3. Ground truth vs. system decision

The `EvaluationTrial` model keeps these fields strictly separate:

| Concept | Field(s) | Set by |
|---|---|---|
| **Ground truth** | `identity_ground_truth` (`genuine`/`impostor`), `presentation_ground_truth` (`bona_fide`/`print_photo`/`screen_replay`/`other_attack`/`not_tested`) | The research protocol / test operator, independently of any system output |
| **System output** | `system_decision`, `similarity_score`, liveness/anti-spoof/PAD snapshot fields | FANS-C's actual verification pipeline for that trial |

Neither ground-truth field is ever derived from a system field. This is
what makes false-accept and false-reject representable at all:

- `identity_ground_truth=GENUINE`, `system_decision=NOT_VERIFIED` → false reject
- `identity_ground_truth=IMPOSTOR`, `system_decision=VERIFIED` → false accept

Identity ground truth and presentation (spoof) ground truth are modeled as
**separate fields** because they are different concepts — a genuine
beneficiary trial has no attack type (`presentation_ground_truth` defaults
to `not_tested`), and an impostor trial may or may not also be a
presentation attack (an impostor can present their own live face and still
be the wrong identity).

## 4. Three-outcome architecture is preserved

FANS-C's actual decision vocabulary is `VERIFIED` / `MANUAL_REVIEW` /
`NOT_VERIFIED` / `DENIED` — not a binary accept/reject. `EvaluationTrial.
system_decision` reuses `VerificationAttempt.DECISION_CHOICES` directly
rather than inventing an incompatible two-value vocabulary. All six
combinations of `{genuine, impostor} × {verified, manual_review,
not_verified}` are representable and covered by
`ThreeOutcomeArchitectureTest` in `verification/tests_bpa1.py`. A future
metrics checkpoint can therefore report genuine/impostor manual-review
rates explicitly instead of silently counting every Manual Review as a
failure.

## 5. Session/dataset concept

`EvaluationDataset` groups a set of `EvaluationTrial` rows collected under
one controlled protocol, so every trial is traceable to which study
produced it and a partially-collected dataset can never be presented as a
finished result.

Fields: `id` (UUID), `name`, `protocol_version`, `description`, `status`
(`draft` / `collecting` / `completed` / `archived`), `created_by`,
`created_at`, `updated_at`, `started_at`, `completed_at`.

Only `status=completed` (with `completed_at` set — enforced by
`EvaluationDataset.clean()`) should ever be treated as a finished
evaluation result by a future metrics checkpoint. `draft` and `collecting`
are explicitly incomplete.

## 6. Trial fields

`EvaluationTrial` (see `verification/models.py` for full field
documentation):

- `dataset` — FK to `EvaluationDataset` (`on_delete=CASCADE`; see §9).
- `trial_status` — `pending` / `completed` / `aborted` (§16). A `pending`
  trial may legitimately lack `system_decision`, `similarity_score`, and
  duration; a `completed` trial must have `system_decision` set
  (`EvaluationTrial.clean()`).
- `participant_code`, `target_identity_code` — pseudonymous codes (§7).
- `identity_ground_truth`, `presentation_ground_truth` — ground truth (§3).
- `target_beneficiary`, `target_representative` — restricted FK resolution
  of `target_identity_code` to a real enrolled identity (§7/BPA-2.1 §28).
- `system_decision`, `similarity_score` — FINAL system output (post
  post-score overrides — §30).
- `matcher_base_decision` — RAW `decide_base_outcome()` result before any
  override (BPA-2.1 §30).
- `quality_override_applied`, `lookalike_escalation_applied`,
  `representative_fallback_blocked` — which post-score rule, if any,
  changed the decision (BPA-2.1 §29/§30).
- `review_threshold_snapshot`, `auto_verify_threshold_snapshot` — threshold
  snapshot (§8).
- `liveness_passed`, `liveness_score`, `anti_spoof_passed`,
  `anti_spoof_score`, `pa_score` — liveness/anti-spoof/PAD snapshot.
- `liveness_proof_embedding`, `liveness_captured_at` — same-face-binding
  proof from stage 1 (BPA-2.1 §26).
- `liveness_pathway`, `challenge_direction`, `head_movement_completed` —
  which production liveness pathway this trial exercised (BPA-2.1 §25).
- `verification_duration_ms` — Controlled Verification Elapsed Time (§9/§27).
- `verification_attempt` — optional FK to the real operational
  `VerificationAttempt` this trial reused.
- `human_review_outcome`, `human_review_notes`, `human_review_by`,
  `human_review_at` — optional supplementary human adjudication; never
  required, and not part of the primary three-outcome evaluation.
- `created_by`, `created_at`, `updated_at`, `evaluated_at`, `notes`.

## 7. Privacy / data minimization

**Correction (BPA-2):** an earlier summary of this work stated "PII
stored: none." That wording is too strong and is corrected here. The
precise statement is:

- **Direct participant PII is not duplicated** into `EvaluationTrial` —
  there is no name/DOB/address/Senior-Citizen-ID/phone/email field on this
  model, and there never will be one; `participant_code` and
  `target_identity_code` are pseudonymous codes, not identifiers copied
  from a person's record.
- **Controlled evaluation data is pseudonymous, not anonymous.** BPA-2
  added `EvaluationTrial.target_beneficiary` / `target_representative` —
  restricted foreign keys to the real enrolled `Beneficiary` /
  `Representative` record the trial actually compared against (§13). These
  are relationship pointers, the same pattern `VerificationAttempt.
  beneficiary` already uses — not copied PII — but their existence means a
  trial IS linkable back to a real identity by anyone with query access to
  both tables.
- **Indirect re-identification is possible for authorized users** through
  several restricted relationships, not just the target FKs above:
  `EvaluationTrial.created_by` / `human_review_by` and
  `EvaluationDataset.created_by` identify the staff member who ran/reviewed
  a trial; `EvaluationTrial.verification_attempt`, when set, links to a
  real operational `VerificationAttempt` (and from there to a real
  beneficiary). None of this is anonymization — it is pseudonymization
  under the same access-control model as the rest of the application
  (§14). Do not describe this dataset as anonymous in any downstream
  document (e.g. the manuscript) — "pseudonymous, re-identifiable by
  authorized users" is the accurate description.

**Stored:** pseudonymous `participant_code` and `target_identity_code`,
the restricted `target_beneficiary`/`target_representative` resolution
FKs, numeric scores, system decision, ground-truth labels, threshold
snapshots, liveness/PAD outputs, timing, and standard audit fields
(`created_by`, timestamps).

**Intentionally NOT stored on this table:** beneficiary/representative
name, date of birth, address, Senior Citizen ID, phone, or email as
free-text fields — those are only reachable by following the restricted
`target_beneficiary`/`target_representative` FK into the operational
tables, under the same access control as any other staff query.

**Raw media:** no raw face photographs, video, or camera frames are
persisted by this model or by the BPA-2 workflow. `evaluation_trial_run`
(§13) processes a browser-submitted frame transiently in memory — exactly
like the production `verify_submit` flow — and stores only the resulting
scores/decision, never the image itself.

**Pseudonymization, not anonymization:** see the correction above. This
document does not claim legal compliance certification.

**Access assumptions:** admin-tier only (President/Admin/IT) — see §14.
No new access surface exists beyond the six BPA-2 URLs listed there; there
is still no Django Admin registration for either model.

## 8. Threshold / score snapshots

`review_threshold_snapshot` and `auto_verify_threshold_snapshot` capture
`SystemConfig.get_threshold()` / `SystemConfig.get_auto_verify_threshold()`
at the moment of the trial. Reason: admins can change these thresholds
later (`verification.views.verify_config`), and a trial's historical
system decision must remain interpretable against the threshold that was
actually active when it was made — not whatever `SystemConfig` happens to
contain months later. `ThresholdSnapshotTest` in `tests_bpa1.py` verifies
the snapshot is unaffected by a later `SystemConfig` change.

## 9. Timing — CONTROLLED VERIFICATION ELAPSED TIME (implemented in BPA-2)

`verification_duration_ms` is a nullable `PositiveIntegerField`. As of
BPA-2 it **is** populated — but only for a specific, narrowly-defined
metric, not the manuscript's broader "capture through result display"
description. See §15 for the full definition. It is never fabricated:
`EvaluationTrial.clean()` forbids setting it on any trial that is not
`COMPLETED`, and a `CheckConstraint` plus `MinValueValidator` reject
negative values.

## 10. Operational separation

`EvaluationDataset` and `EvaluationTrial` are new tables with no
relationship to `verification/analytics.py`, `verification/
template_analytics.py`, or any dashboard/executive/operational/security
Analytics query. The BPA-2 workflow (§13) reinforces this at the code
level, not just the schema level: `evaluation_trial_run` calls the same
face-matching/liveness/PAD *functions* production uses
(`face_utils.compare_with_all_embeddings`, `liveness.check_anti_spoofing`,
`pad.PresentationAttackDetector`, `face_utils.decide_base_outcome`) but
never calls `verify_submit`, `verify_check_liveness`, or any other
production *view*, and never instantiates `VerificationAttempt` or
`ClaimRecord`. `SafetyIsolationTest` in `verification/tests_bpa2.py`
proves this with before/after assertions on
`VerificationAttempt.objects.count()`, `ClaimRecord.objects.count()`, and
`get_executive_metrics()`. Neither model is registered in Django Admin.

## 11. Deletion / retention semantics

- `EvaluationTrial.dataset` → `EvaluationDataset`: `on_delete=CASCADE`. A
  trial has no independent meaning outside the dataset defining its
  protocol; deleting an entire dataset is treated as a deliberate research
  action.
- `EvaluationTrial.verification_attempt` → `VerificationAttempt`:
  `on_delete=SET_NULL`. Deleting or modifying the linked operational
  attempt must never delete research data.
- `EvaluationTrial.target_beneficiary` / `target_representative` →
  `Beneficiary` / `Representative`: `on_delete=SET_NULL` (added BPA-2).
  Removing an operational beneficiary/representative record must not
  cascade-delete research trials that referenced it as a target — the
  trial survives with the FK nulled, preserving its ground-truth and
  system-output fields.
- `EvaluationTrial.created_by` / `human_review_by`, and
  `EvaluationDataset.created_by` → user: `on_delete=SET_NULL`. Disabling or
  removing an operator/researcher account must not take evaluation data
  with it.

Covered by `DeletionRetentionSemanticsTest` (`tests_bpa1.py`) and
`SafetyIsolationTest`/other tests in `tests_bpa2.py` that exercise the
`target_beneficiary` SET_NULL path indirectly via the deletion tests. This
is not a full retention/deletion *workflow* (e.g. scheduled purging, a
participant-withdrawal UI) — that remains out of scope until a later BPA
checkpoint. The `target_beneficiary`/`target_representative` design
deliberately avoids a separate participant-mapping model so a future
withdrawal workflow only has to null two FK columns, not migrate data out
of a second table.

## 12. What BPA-1 established (unchanged by BPA-2)

- No Accuracy/FAR/FRR/TAR/EER/AUC value is computed, displayed, or implied
  — still true after BPA-2.
- No verification threshold was ever changed by either checkpoint.
- Neither model is registered in Django Admin.
- The manuscript's Chapter 3 metric promises are **not** fulfilled by
  either checkpoint; BPA-1/BPA-2 only make their future truthful
  computation possible (§18).

---

## 13. BPA-2 — Controlled trial workflow

Implemented in `verification/views.py` (the "BPA-2 — Controlled Biometric
Evaluation Trial Workflow" section) and six templates under
`templates/verification/evaluation_*.html`. Every page carries a visible
"CONTROLLED EVALUATION — NOT LIVE STIPEND DISTRIBUTION" / "NO CLAIM OR
PAYOUT WILL BE CREATED" warning.

**Dataset lifecycle** (`EvaluationDataset.status`):

| Transition | View | Rule |
|---|---|---|
| (create) → `draft` | `evaluation_dataset_create` | name + protocol_version required |
| `draft` → `collecting` | `evaluation_dataset_start` | sets `started_at` if unset |
| `collecting` → `completed` | `evaluation_dataset_finalize` | **blocked** if zero trials exist, or if any trial is still `pending` (§7 completeness rule) |
| `completed` → `archived` | `evaluation_dataset_archive` | one-way; archived datasets are read-only through this workflow |

Trials may only be created (`evaluation_trial_setup`) while the dataset is
`collecting` (`EvaluationDataset.can_accept_trials`).

**Trial setup** (`evaluation_trial_setup`) — ground truth is recorded
BEFORE any biometric processing, by construction: the view creates the
`EvaluationTrial` row (`trial_status=pending`, `system_decision=None`) from
form fields the researcher fills in — `participant_code`,
`target_identity_code`, `identity_ground_truth`, `presentation_ground_truth`
— and nothing in the creation path reads a similarity score or system
decision (neither exists yet). `GroundTruthRecordedBeforeSystemResultTest`
in `tests_bpa2.py` proves the created row has `system_decision=None`.

**Target identity resolution** — the same page includes a search box
(reusing the exact query pattern `verify_select` already uses: name /
`beneficiary_id` / Senior Citizen ID, `status=ACTIVE` only) that lets the
researcher pick a real enrolled `Beneficiary`, setting
`EvaluationTrial.target_beneficiary`. This is the FK described in §7 — it
is what lets `evaluation_trial_run` actually retrieve a real stored
template to compare against. A trial with no resolved target cannot be
run (the runner aborts it rather than fabricating a result — §16).
Representative targets (`target_representative`) are supported by the
model and the runner's comparison branch, but the current trial-setup page
only exposes a beneficiary search UI; representative-target trials must be
created by setting the field directly (e.g. via shell/fixture) until a
future checkpoint adds that search UI too — this is a deliberate scope cut
for BPA-2, not a hidden limitation.

**Controlled trial runner** (`evaluation_trial_run`, GET+POST):

- **GET** authorizes the trial to begin: if `evaluation_session_token` is
  not yet set, it generates one (`uuid4`) and stamps
  `evaluation_started_at = timezone.now()` — this is the server-
  authoritative start of the timing window (§15). A page refresh does
  **not** reissue the token or reset the clock (`EvaluationTimingTest.
  test_get_runner_page_does_not_reissue_token_or_reset_clock`).
- **POST** must include the matching `session_token`; a mismatch (e.g. a
  stale tab, or a token copy-pasted against the wrong trial) is refused
  outright with no processing — same purpose as `VerificationAttempt`'s
  own stale-session guard, and covered by
  `test_session_token_mismatch_cannot_attach_to_wrong_trial`.
- Processing reuses production's actual functions, never production's
  views: `load_image_from_bytes` → `detect_and_align_face` →
  `check_anti_spoofing` (same gate `verify_submit`'s authoritative frontal
  check uses) → `PresentationAttackDetector.analyze`/`analyze_sequence`
  (single frame, or the same ≥3-frame sequence path production uses) →
  (if liveness/PAD pass) `get_embedding` → `compare_with_all_embeddings`
  (beneficiary target) or `compare_with_stored` (representative target) →
  `face_utils.decide_base_outcome` (§18, the literal function
  `verify_submit` also calls).
- A liveness/PAD failure denies **before** comparison: `system_decision =
  DENIED`, `similarity_score` stays `NULL` (never `0.0` — §12 of BPA-1),
  liveness/PAD outputs are retained, and the trial is still a valid
  `COMPLETED` result. Covered by `PreComparisonDenialTest`.
- Every outcome — `VERIFIED`, `MANUAL_REVIEW`, `NOT_VERIFIED`, `DENIED` —
  ends in `_complete_trial()`, which sets `trial_status=COMPLETED`,
  snapshots both thresholds, and computes the duration. A processing
  failure that never reaches a decision (no face detected, malformed
  image, embedding exception, unresolved target) ends in `_abort_trial()`
  instead: `trial_status=ABORTED`, `system_decision` stays `None`,
  `verification_duration_ms` stays `None` — this is the ABORTED-vs-
  COMPLETED+DENIED distinction required by BPA-2 §17 (see §16 below).

**Scope cut, stated plainly:** the capture page
(`evaluation_trial_run.html`) is a simplified single/multi-frame still
capture — a "Capture Frame" button (up to 3 frames total: 1 primary + 2
burst) and a "Submit Trial" button. It does **not** replicate production's
interactive JS head-turn challenge overlay (`static/js/verify.js` /
`liveness.js`). The server-side anti-spoof and PAD functions are the exact
same ones production uses, run on whatever frame(s) are submitted;
`liveness.run_full_liveness_check`'s challenge-direction/motion-validation
layer (which requires the interactive overlay) is not invoked. This means
BPA-2 evaluation trials characterize FaceNet matching, texture-based
anti-spoof, and heuristic PAD faithfully, but do not exercise the
head-movement liveness challenge itself — a limitation to state plainly in
any paper that cites this data, not to gloss over.

## 14. Authorization

Admin-tier only (`request.user.is_admin`, i.e. President, Admin, or IT —
never plain Staff), via `_evaluation_admin_required()` in `views.py`. This
is the exact same convention `verify_config` and `auto_approval_settings`
already use (`if not request.user.is_admin: ... redirect`) — no new
decorator or permission architecture was introduced. All nine BPA-2 URLs
(`evaluation/datasets/`, `.../create/`, `.../<pk>/`, `.../<pk>/start/`,
`.../<pk>/finalize/`, `.../<pk>/archive/`, `.../<pk>/trials/new/`,
`evaluation/trials/<pk>/`, `.../run/`, `.../abort/`) share this gate.
President/Admin/IT were not further differentiated (e.g. IT-only) because
this data is technical research tooling, not a payout/claims decision —
the existing "admin-tier" precedent was judged the correct narrowest fit
rather than inventing a new distinction. `EvaluationAccessControlTest`
proves Staff is denied on both the list page and the trial runner, and
that all three admin-tier roles succeed.

No navigation entry was added to `templates/base.html` — these pages are
reached by direct URL only, consistent with BPA-1's "no general
interactive entry surface" stance and BPA-2 §22's "restrained... not the
final dashboard" instruction. A nav entry can be added once this workflow
has been reviewed.

## 15. Timing definition — CONTROLLED VERIFICATION ELAPSED TIME

**Metric name:** Controlled Verification Elapsed Time (a BPA-2-specific,
narrowly-defined metric — not a claim about the manuscript's broader
"capture through result display" Verification Time definition; see §18).

**Start:** the server timestamp taken when `evaluation_trial_run`'s GET
handler authorizes the trial to begin (`evaluation_started_at`).

**Stop:** the server timestamp taken inside `_complete_trial()` /
`_abort_trial()` when the final outcome is computed
(`evaluated_at`, and `verification_duration_ms = evaluated_at -
evaluation_started_at`, clamped to a minimum of 0 and rounded to the
nearest millisecond — `EvaluationTrial._evaluation_trial_duration_ms` in
`views.py`).

**Storage:** `EvaluationTrial.verification_duration_ms`, only ever set
when `trial_status=COMPLETED` (enforced by `clean()` — see §9/§17).

**Included, depending on the actual path taken:** the browser round-trip
to POST the captured frame(s), image decode/detect/align, the anti-spoof
check, PAD analysis, FaceNet embedding + comparison (when reached), and
the decision computation itself.

**Excluded, explicitly:**
- Any time before the researcher loads the runner page (dataset browsing,
  trial setup, deciding to click "Run Controlled Trial").
- Camera warm-up and the researcher's own capture-button click timing —
  the clock starts at page-authorization (server GET), not at the
  moment a photo is actually taken client-side.
- Time after the server responds until the browser finishes rendering the
  result page — this is a **server-measured duration**, not "capture-to-
  screen-render latency." No claim is made about total perceived latency.

**Why this is reproducible:** both endpoints are server timestamps
(`timezone.now()`), never a client-supplied duration — `EvaluationTimingTest`
proves this with a mocked, fixed server clock (`mock.patch('verification.
views.timezone.now', return_value=<fixed t1>)`) asserting the stored
duration exactly equals the fixed delta, for `VERIFIED`, `MANUAL_REVIEW`,
`NOT_VERIFIED`, and pre-comparison `DENIED` outcomes, plus a clock-skew
case proving the result clamps to 0 rather than going negative. No
browser-side timing field was added — BPA-2 judged it would not
materially improve the methodology given the server-authoritative
definition above (§15 of the BPA-2 checkpoint instructions permits
omitting it on that basis).

## 16. Timing failure / abort behavior

- **A legitimate `COMPLETED` + `DENIED` security decision** (liveness/PAD
  gate failed, or the final-frame integrity checks would have failed had
  this been production) — the trial reached a real decision. It gets a
  real `verification_duration_ms`. This is scientifically meaningful data
  (a security-correct rejection), not a technical failure.
- **A technical failure** (no face detected, malformed image, embedding
  exception, comparison against an unresolved target) — the trial never
  reached ANY decision. `trial_status=ABORTED`, `system_decision=None`,
  `verification_duration_ms=None`. `EvaluationTrial.clean()` enforces both
  nulls at the model layer, independent of whatever the view does.
- **Manual abandonment** (`evaluation_trial_abort`, researcher-initiated,
  only valid while `trial_status=pending`) — same ABORTED path, `reason`
  recorded in `notes`.

`EvaluationTimingTest.test_pending_trial_has_no_fabricated_duration` and
`.test_aborted_trial_has_no_fabricated_duration` cover the "never
fabricate a completed timing value for an unfinished trial" requirement
directly.

## 17. System decision parity

`face_utils.decide_base_outcome(score, threshold, auto_verify_threshold)`
is a new pure function extracted from `verify_submit`'s previously-inline
three-zone decision logic (`verification/views.py`, the "v2.1.13
three-zone band" branch). **Both** `verify_submit` (production) and
`evaluation_trial_run` (BPA-2) call this exact function — not two
independently-maintained copies. `EvaluationDecisionParityTest.
test_views_module_uses_the_shared_face_utils_function` asserts
`verification.views.decide_base_outcome is verification.face_utils.
decide_base_outcome` (literal object identity), and
`VerifySubmitThresholdBandPolicyTest` (updated in `tests.py`) is the
regression guard proving `verify_submit` still delegates to it rather than
reverting to an inline copy. Production behavior is unchanged: the
extraction preserves the exact same branches, operators (`>=` at every
boundary), and `review_band = threshold * 0.85` — verified by the existing
`ThreeZoneDecisionBoundaryTest` suite plus 753 other pre-existing
verification/accounts/beneficiaries/logs tests re-run after the
extraction with zero regressions.

Threshold snapshot behavior (§8) is unchanged by BPA-2: `evaluation_trial_run`
snapshots `SystemConfig.get_threshold()`/`get_auto_verify_threshold()` at
call time onto the trial, exactly as BPA-1 specified.

**BPA-2.1 note:** `decide_base_outcome()` parity (this section) covers only
the MATCHER-level decision. Production applies further post-score rules
(quality override, lookalike escalation, representative-fallback-block)
before persisting a final decision — BPA-2 evaluation trials did not apply
these, so `system_decision` there reflected the matcher only, not the
FINAL FANS-C decision. BPA-2.1 closes this gap — see §29/§30 below.

## 18. Presentation-attack collection

`presentation_ground_truth` (BONA_FIDE / PRINT_PHOTO / SCREEN_REPLAY /
OTHER_ATTACK / NOT_TESTED) is set by the researcher at trial setup, before
any capture — never inferred from the PAD score. `evaluation_trial_run`
computes a real PAD score (`pa_score`) from whatever frames were
submitted, using the same `PresentationAttackDetector` production uses,
and stores it as the SYSTEM'S observation — a separate field from the
researcher's `presentation_ground_truth` label. Nothing in the code path
overwrites one from the other; a future metrics checkpoint can therefore
legitimately cross-tabulate "researcher said PRINT_PHOTO" against "system
scored 0.83" without circularity. Pre-comparison denial behavior (PAD or
anti-spoof gate trips) is documented in §13/§16 above:
`similarity_score` stays `NULL`, the PAD/anti-spoof scores are retained,
and the trial is still a valid `COMPLETED` result with `system_decision=
DENIED`.

## 19. Audit logging

Six new `AuditLog.ACTION_*` constants (`logs/models.py`):
`EVALUATION_DATASET_CREATED`, `_STARTED`, `_FINALIZED`,
`EVALUATION_TRIAL_CREATED`, `_COMPLETED`, `_ABORTED` — logged via the
existing `AuditLog.log(action=..., user=..., target_type=..., target_id=...,
details=..., request=...)` helper, the same call pattern every other
FANS-C audit action uses. No raw face data, decrypted embeddings, or
secrets are ever placed in `details` — only IDs, decision strings, and
ground-truth labels (see the `details=` dicts in `views.py`).

These are **new, distinct action constants** — an evaluation trial's
completion is never logged as `ACTION_VERIFY`, and no `ACTION_CLAIM` /
`ACTION_PAYOUT_*` / fraud-signal action is ever written for a trial
(there is nothing to release, so nothing to log as released).
`EvaluationAuditLogTest.
test_evaluation_trial_completion_is_not_logged_as_live_verify_or_claim`
proves this directly: after running a `VERIFIED`-outcome trial, the audit
log contains zero `ACTION_VERIFY` rows for that trial and zero
`ACTION_CLAIM` rows at all.

## 20. Safety isolation — how false accepts remain safe

The controlled workflow is **architecturally incapable** of creating a
payout, not merely configured not to: `evaluation_trial_run` never
imports or calls `verify_submit`, `verify_fallback`,
`override_release_payout`, `manual_verify_review`, `special_claim_review`,
or `_create_claim_record` — the five and only places `ClaimRecord` is ever
constructed anywhere in this codebase (confirmed by a full-repo trace
before implementation; there are also zero Django signals in this
codebase, so no `post_save` hook on `VerificationAttempt` could create one
either). It never instantiates `VerificationAttempt` at all. So a
controlled false accept — `identity_ground_truth=IMPOSTOR`,
`system_decision=VERIFIED` — is recorded purely as data on
`EvaluationTrial`: no `ClaimRecord`, no payout, no operational
`VerificationAttempt`, no change to any Executive/Operational Analytics
number. `SafetyIsolationTest` and `GroundTruthDecisionCombinationsTest.
test_false_accept_impostor_verified` in `tests_bpa2.py` prove this with
direct before/after counts on `VerificationAttempt`, `ClaimRecord`, and
`get_executive_metrics()`.

## 21. Manuscript alignment note

The draft manuscript's Chapter 3 describes Verification Time broadly as
"time from facial image capture through processing and result display."
The metric BPA-2 actually implements and can defend —
**Controlled Verification Elapsed Time** (§15) — is narrower and
server-side only: it excludes camera warm-up/researcher capture timing on
the front end, and excludes browser rendering time after the server
responds. **Before any Chapter 3 number is written**, the manuscript's
Verification Time definition must be reconciled with this implemented
definition (either narrow the manuscript's definition to match what BPA-2
measures, or extend the implementation with client-side instrumentation
that measures what the manuscript currently claims — a decision for
BPA-3+, not made here). Do not present `verification_duration_ms` values
as satisfying the manuscript's broader definition without that
reconciliation.

---

## 23. BPA-2.1 — Why a correction checkpoint was needed

BPA-2's runner (§13) used a simplified single-frame capture with no
same-face binding, no representative-target selection UI, and applied only
the raw matcher decision (`decide_base_outcome`) — never production's
post-score overrides. Two methodological parity gaps meant BPA-2 trials
could not yet be described as measuring "the FANS-C system": (1) the
runner did not reproduce production's liveness/same-face-binding
architecture, and (2) representative-target trials had no setup UI and the
persisted decision was the raw matcher output, not the final system
decision. BPA-2.1 closes both gaps. It does not compute any metric.

## 24. Production vs. BPA pipeline — traced, not assumed

Before writing any BPA-2.1 code, the actual production implementation was
re-traced directly from source (`verification/views.py` lines ~1495-2035,
`verification/liveness.py`, `verification/pad.py`) rather than assumed.
Key findings that shaped the design:

- **`server_liveness_passed` is NOT gated on `challenge_completed` once a
  liveness proof exists.** The naive default at `views.py:1513`
  (`server_anti_spoof_passed and bool(challenge_completed)`) is
  **overridden** at `views.py:1538-1539` to `True` whenever
  `_tx_anti_spoof_passed and _tx_pad_ok` — a condition that never
  references `challenge_completed` at all. In the common path (a proof
  exists), the real gate is anti-spoof + PAD only.
- **The quality-override rule is dormant on the common path.** The
  low-quality-forces-manual-review check (`views.py`, v2.1.13) tests
  `face_result is not None` — but `face_result` is only assigned on the
  rare "no proof embedding available" fallback branch (`views.py:1694`).
  On the normal proof-bound path this rule silently never fires, even
  though quality IS computed and stored for the audit record via a
  separate code path. This is a genuine (pre-existing) production quirk,
  not something BPA-2.1 introduced or fixed — see §30.
- **The lookalike-escalation rule is NOT path-conditional** — it runs
  whenever `decision == VERIFIED and score >= threshold`, regardless of
  which identity-retrieval path was used.
- **The representative-beneficiary-fallback probe is a hard, unconditional
  rule** for representative claims: if the live face also matches the
  BENEFICIARY at or above the review threshold, the claim is blocked
  outright, regardless of the representative-comparison score.

## 25. Liveness policy

**When the interactive challenge is shown to the user (production):**
risk-based — `verify_start` sets `require_liveness_challenge=True` always
for representative claims, and leaves it to client-side risk logic
(`static/js/verify.js`) for beneficiary claims (low anti-spoof score,
poor quality, a retry, etc.). This is a **UX display decision**, not a
security-strictness toggle.

**When active challenge affects the security decision (production):**
almost never, in the common path — see §24. `challenge_completed` mainly
affects the *stored* `liveness_score` number (0.6×anti-spoof + 0.4×challenge,
via `run_full_liveness_check`) and, more importantly, PAD's
`landmark_motion_ok` suppression of pixel-level static/near-duplicate
signals when real motion is independently confirmed — this is the actual
security value of the challenge: raising confidence against certain replay
attacks, not gating pass/fail on its own.

**BPA-2.1's design choice (§4 option B — explicit controlled-test mode):**
rather than force one policy, the researcher chooses per trial whether to
attempt an active challenge (a checkbox: "I performed the head-turn
challenge shown above") and the system records which pathway actually ran
via `liveness_pathway` (`passive_only` / `active_challenge` /
`failed_passive` / `failed_active`). The gate itself
(`evaluation_trial_liveness` in `views.py`) mirrors production's TX-bound
override exactly: anti-spoof (`check_anti_spoofing` on the neutral frame)
+ PAD, independent of the challenge attestation — so BPA-2.1 is neither
stricter nor weaker than production's actual common-path gate.

**Shared code:** `check_anti_spoofing` (same function, same call site
pattern as production's frontal-frame check), `PresentationAttackDetector`
(same class), `run_full_liveness_check` (same function, used for the
stored `liveness_score` number).

**Differences remaining (stated plainly, not glossed over):** BPA-2.1 does
not implement an automated interactive JS head-turn overlay or MediaPipe
landmark tracking. `head_movement_completed` is researcher-attested, not
machine-verified — a genuine limitation for any paper citing "active
challenge" trial results. See §33.

## 26. Session / same-face binding

**Production:** the liveness-approved frame's embedding is stored on
`LivenessTransaction.embedding_data` (encrypted) and is what
`verify_submit` actually uses for identity matching — never the
final-submission frame's own embedding. A separate "final-frame integrity
gate" then cosine-compares the submitted frame against this stored
embedding (`SAME_FACE_SEQUENCE_THRESHOLD`, default 0.30); a mismatch
denies with `ACTION_VERIFY_SUBJECT_CHANGED`.

**Controlled evaluation (BPA-2.1):** the same architecture, scoped to the
trial. Stage 1 (`evaluation_trial_liveness`) stores an encrypted
`liveness_proof_embedding` on the `EvaluationTrial` row itself — a smaller
mechanism than reusing `LivenessTransaction` directly (see below for why).
Stage 2 (`evaluation_trial_run` POST) decrypts this proof, cosine-compares
it against the newly-submitted final frame using the exact same
`SAME_FACE_SEQUENCE_THRESHOLD` setting, and denies before any target
comparison on a mismatch — `SameFaceBindingTest` in `tests_bpa2_1.py`.
Identity matching itself always uses the PROOF embedding, never the final
frame's, exactly mirroring production.

**Why not reuse `LivenessTransaction` directly:** it requires a non-null
`beneficiary` FK and carries operational fields (`stipend_event`,
`used_by_attempt` → `VerificationAttempt`) that would create an unwanted
coupling between research trials and the live payout schema — a research
trial's proof should not exist in a table one FK-hop from a real
`VerificationAttempt`/`ClaimRecord`, even though nothing would currently
consume it that way. Storing the proof directly on `EvaluationTrial`
(§6) keeps the research data self-contained, matching this project's
existing operational-separation principle (§10/§20).

**Replay protection:** `evaluation_trial_liveness` refuses to run a second
time once `liveness_proof_embedding` is set —
`LivenessProofBindingTest.test_liveness_proof_cannot_be_recaptured_once_set`.

**Cross-trial protection:** the SAME `evaluation_session_token` introduced
in BPA-2 binds both stages to one trial — a liveness POST or final-submit
POST bearing another trial's token is refused outright, with no processing
—`test_liveness_token_from_another_trial_rejected` /
`test_session_token_mismatch_cannot_attach_to_wrong_trial`.

## 27. Timing — reassessed after parity work

**Current BPA time definition (unchanged in substance):** Controlled
Verification Elapsed Time, server `evaluation_started_at` (stage-1 GET
authorization) → server `evaluated_at` (stage-2 completion, or stage-1
completion for a pre-comparison denial).

**Production user flow:** capture → liveness challenge (optional
interactive UI) → server round-trip → final capture → server round-trip →
result display. Client-side capture/render time is not observable
server-side with the same precision as the server-to-server timestamps.

**Final recommended research metric:** keep **Controlled Verification
Elapsed Time** exactly as defined — it is genuinely what is measured
(two server timestamps, never a client-supplied duration), it now spans
the SAME two-stage architecture production actually uses (not an
artificially simplified one-shot version), and it works identically for
beneficiary and representative trials (`TimingParityTest`). Do not rename
it to imply "capture to screen render" — that quantity is not measured and
would require client-side instrumentation, which §21 already flagged as
not implemented.

## 28. Representative support

**Selection UI:** `evaluation_trial_setup` now has an explicit
`target_type` choice (Beneficiary / Authorized Representative). Choosing
Representative searches `Representative` records (name, or the associated
beneficiary's name/ID), restricted to admin-tier staff — the same
access level as the rest of this workflow. A representative with no
enrolled face is shown with a visible "No face data" badge so the
researcher can make an informed choice (including deliberately testing
the no-face-data path, §13).

**Target relation:** `EvaluationTrial.target_representative` (added
BPA-2, wired into the setup UI in BPA-2.1). Mutually exclusive with
`target_beneficiary` (`clean()`).

**Embedding used:** the representative's OWN stored embedding
(`compare_with_stored`, single template — representatives have no
multi-template matching in production either). Never the beneficiary's.

**Beneficiary fallback:** architecturally impossible, not just
policy-disabled. `check_representative_beneficiary_fallback` (shared
helper, `face_utils.py`) probes the live proof embedding against the
BENEFICIARY's stored templates; if it scores at or above the review
threshold, `representative_fallback_blocked=True` and the FINAL decision
is forced to `DENIED` regardless of what the representative-comparison
score was. If the representative has no enrolled face at all, the trial
**aborts** (a setup/configuration issue — not a fabricated security
decision) rather than ever attempting a beneficiary-embedding comparison.

**Tests:** `BeneficiaryRepresentativeComparisonParityTest`,
`RepresentativeGroundTruthCombinationsTest` (all six ground-truth ×
decision combinations), both in `tests_bpa2_1.py`.

## 29. Post-score decision audit

Every production rule that can run between `decide_base_outcome()` and
the persisted `VerificationAttempt.decision`, traced from
`verification/views.py`:

| Rule | Production effect | Include in BPA final decision? | Why |
|---|---|---|---|
| Quality override (v2.1.13) | Forces `VERIFIED` → `MANUAL_REVIEW` if quality is poor — but ONLY on the rare fallback identity path (`face_result is not None`); dormant on the common proof-bound path (§24) | **Dormant, by faithful parity** — BPA-2.1's proof-bound path deliberately does not apply it (`quality_override_applied` stays `False`), matching what the common production path actually does today | Measuring the REAL deployed system means reproducing its actual behavior, quirks included, not an idealized version. Documented here so it is never mistaken for an oversight. |
| Lookalike/twin escalation | Forces `VERIFIED` → `MANUAL_REVIEW` if another beneficiary's embedding also matches within `LOOKALIKE_BAND` — runs on every path, both claimant types | **Included** — `check_lookalike_escalation` (shared helper) is called by `evaluation_trial_run` whenever the matcher decision is `VERIFIED`, exactly mirroring production, excluding the trial's own target beneficiary (or the representative's associated beneficiary) | Active on the common path in production; omitting it would understate MANUAL_REVIEW rates and overstate VERIFIED (false-accept) rates for lookalike-adjacent test identities. |
| Representative→beneficiary fallback block | Forces the decision to `DENIED` outright if a representative claim's live face also matches the beneficiary | **Included** — `check_representative_beneficiary_fallback` (shared helper) is called for every representative-target trial | Hard security rule, unconditional in production; §13/§28. |
| Final-frame integrity gate (no-face / multi-face / frame-invalid) | Denies before comparison | **Partially included** — BPA-2.1 implements the same-face-check portion (§26); it does not separately implement no-face/multi-face frame-invalid sub-checks as distinct denial reasons (a face-detection failure in BPA-2.1 is an ABORTED technical failure, not a categorized DENIED reason) | Scope decision for this checkpoint — see §33. |
| Claim/payout release | Creates `ClaimRecord`, marks the beneficiary as claimed | **Never included, by design** | Explicitly out of scope for the entire BPA program (§9 of BPA-2.1 instructions, §20/§10 of this doc). |

## 30. Decision levels

**Matcher base decision:** `EvaluationTrial.matcher_base_decision` — the
raw `decide_base_outcome(score, threshold, auto_verify_threshold)` result.
Represents pure similarity-score classification, before any safety
overlay.

**Final automated FANS-C decision:** `EvaluationTrial.system_decision` —
`matcher_base_decision` after the rules in §29 that BPA-2.1 includes
(lookalike escalation, representative-fallback-block). This is what
`VerificationAttempt.decision` would actually be for the equivalent live
attempt (modulo the dormant quality-override quirk, which is identical in
both).

**What future BPA-3 metrics will measure**, using this distinction:

- **A. Matcher performance** — computed from `matcher_base_decision`
  against ground truth. A pure biometric-classifier metric.
- **B. Automated FANS-C decision performance** — computed from
  `system_decision` against ground truth. This is the metric a thesis
  should describe as "FANS-C system accuracy," since it reflects the
  actual deployed decision pipeline (matcher + safety overlay), not a
  simplified classifier.
- **C. End-to-end claim performance** — NOT computable from this data by
  design. Controlled trials never create a `ClaimRecord`; this dataset can
  never speak to claim-release correctness, only to the biometric/security
  decision that would precede one.

BPA-3 must report which of A/B it is presenting, explicitly, every time.

## 31. Presentation-attack / liveness data — BPA-2.1 additions

**Ground truth:** unchanged from BPA-1 — `presentation_ground_truth`,
researcher-set at trial creation, before any capture.

**System output:** `pa_score` (from the SAME `PresentationAttackDetector`
production uses), plus now `liveness_pathway` recording which policy
pathway actually ran. Neither is ever used to infer or overwrite ground
truth (§18 of the BPA-2 doc section, unchanged principle).

**Active/passive pathway:** see §25. `liveness_pathway` distinguishes
`passive_only` / `active_challenge` (both are successful pathways —
`liveness_proof_embedding` gets stored) from `failed_passive` /
`failed_active` (denied before any comparison).

**Pre-comparison denial:** unchanged principle from BPA-2 §12 —
`similarity_score` stays `NULL`, never `0.0`, and the trial is still a
valid `COMPLETED` result with `system_decision=DENIED`.

## 32. Remaining BPA work

- **BPA-3:** metrics-computation layer (Accuracy/FAR/FRR/TAR/EER with
  correctly-defined denominators, and the matcher-vs-final-decision
  distinction from §30 applied consistently), built on the
  `EvaluationTrial` rows this workflow now collects.
- **BPA-4:** the actual Biometric Performance Analytics UI (dashboard,
  ROC/confusion-matrix visualization) — explicitly deferred by BPA-1 §11,
  BPA-2 §22/§23, and BPA-2.1 alike.
- **BPA-5:** final validation, participant-withdrawal/retention workflow
  (§11 notes the schema already anticipates this), and manuscript
  methodology alignment (§21, §33).

## 33. Manuscript alignment note (BPA-2.1 update)

Building on §21: the manuscript's Chapter 3 must, before any number is
written, state precisely and separately:

- **Liveness procedure:** that the controlled evaluation's active-challenge
  pathway is researcher-attested, not automatically verified via
  interactive JS/MediaPipe motion tracking (§25) — a stated limitation,
  not a hidden one.
- **Verification-time measurement:** Controlled Verification Elapsed Time
  as defined in §27 — server-to-server only, not client capture-to-render.
- **Beneficiary vs. representative trials:** both are now supported and
  timed identically (§16/§28), so results should be reportable separately
  for each claimant type if the sample size allows.
- **Automated decision metrics:** whether a reported number is "matcher
  performance" (A) or "automated FANS-C decision performance" (B) per
  §30 — never presented as unqualified "system accuracy" without saying
  which.

Do not present any of the above as resolved until a controlled dataset
actually exists — this section describes what the manuscript must SAY,
not results it may claim.

---

## 34. BPA-3 — Biometric Performance Metric Engine

Status as of this writing: **calculation layer only.** `verification/
biometric_analytics.py` implements every formula below as pure/query
functions over already-collected `EvaluationTrial` rows. No Accuracy/FAR/
FRR/TAR/EER/AUC/ROC value has been computed on real study data — BPA-3 adds
the ability to compute these truthfully once a finalized dataset exists; it
does not itself produce or report any such result. No migration was
required (`makemigrations --check --dry-run` reports no changes) — every
field this checkpoint reads already existed from BPA-1/BPA-2/BPA-2.1.

No Analytics UI, navigation entry, chart, or confusion-matrix page was
added — that is BPA-4, explicitly deferred (§32).

### 34.1 Rate convention

Every rate-shaped result is a dict `{'numerator', 'denominator', 'rate'}`.
`rate` is a **fraction 0.0-1.0**, never a pre-multiplied percentage, and is
**`None` (never `0.0`) whenever `denominator == 0`** — an unavailable rate
must never render as a measured zero. A caller/UI multiplies by 100 for
display. `RateHelperTest` and the `ZeroEmptyDatasetTest` class in
`verification/tests_bpa3.py` cover this directly, including the distinct
case of a genuinely measured zero (`numerator=0, denominator>0` → `rate=
0.0`, not `None`).

### 34.2 Dataset eligibility

`get_dataset_info(dataset)` returns the dataset id/name/protocol_version/
status/`is_finalized_dataset` plus trial-status counts and the unique
`participant_code` count. Every other function in this module takes an
explicit `EvaluationDataset` argument — trials from other datasets are
never combined (`DatasetIsolationTest`).

`is_finalized_dataset` is `True` only when `dataset.status == COMPLETED`.
`build_metric_report()` attaches this flag plus a warning string
(`'INTERIM CONTROLLED-EVALUATION RESULTS — DATA COLLECTION NOT
FINALIZED.'`) whenever it is `False` — interim results are still computed
(for research monitoring) but are never silently presented as final
(`FinalizationNoticeTest`).

### 34.3 Primary identity-performance cohort

**Eligibility:** `trial_status=COMPLETED` AND `identity_ground_truth ∈
{GENUINE, IMPOSTOR}` AND `presentation_ground_truth = BONA_FIDE`.

**Exclusions:** `PENDING` and `ABORTED` trials (never enter any
denominator — a technical abort is neither a false/true reject nor a
false/true accept); any trial whose `presentation_ground_truth` is
`PRINT_PHOTO` / `SCREEN_REPLAY` / `OTHER_ATTACK` / **`NOT_TESTED`**.

**Presentation-ground-truth policy:** `NOT_TESTED` is deliberately **not**
treated as equivalent to `BONA_FIDE`. A trial where presentation truth was
never established is methodologically ambiguous and is excluded from the
primary cohort, not silently folded in as a clean presentation
(`PresentationCohortTest.test_not_tested_excluded_from_primary_but_included_in_exploratory`).

**Exploratory cohort:** `get_exploratory_identity_performance()` computes
the same formulas over a broader cohort (identity ground truth known,
presentation ground truth unrestricted) — explicitly labeled
`'population': 'exploratory_all_identity_cohort'` with a warning string,
offered only for research monitoring, never as the primary capstone number.

### 34.4 Primary three-outcome matrix

Implemented as `_identity_performance(qs, decision_field)`, called once
with `decision_field='system_decision'` (FINAL) and once with
`'matcher_base_decision'` (MATCHER):

|                | VERIFIED | MANUAL_REVIEW | REJECTED (NOT_VERIFIED ∪ DENIED) |
|---|---|---|---|
| **GENUINE**  | `matrix.genuine.verified` | `matrix.genuine.manual_review` | `matrix.genuine.rejected` |
| **IMPOSTOR** | `matrix.impostor.verified` | `matrix.impostor.manual_review` | `matrix.impostor.rejected` |

`REJECTED` is exactly `{NOT_VERIFIED, DENIED}` — `MANUAL_REVIEW` is never
included in it. One aggregate query per cohort computes all 8 cell/total
counts (no per-trial Python loop) — see §34.13.

### 34.5 Final-system formulas

All computed by `get_final_system_performance(dataset)` from
`system_decision` over the primary bona-fide cohort:

- **TAR** = genuine→VERIFIED / genuine_total. DATA: `system_decision`,
  `identity_ground_truth=GENUINE`. MEANING: fraction of genuine presenters
  the deployed FANS-C pipeline correctly auto-accepted. LIMITATION: a
  controlled-study number, not a guarantee for uncontrolled real-world
  presentation conditions.
- **FRR** = genuine→(NOT_VERIFIED∪DENIED) / genuine_total. MANUAL_REVIEW is
  **never** counted as a false rejection.
- **GMRR** = genuine→MANUAL_REVIEW / genuine_total. Invariant: TAR + FRR +
  GMRR = 1 for a complete eligible genuine cohort (`MetricInvariantTest`).
- **FAR** = impostor→VERIFIED / impostor_total. MEANING: fraction of
  impostor presenters the deployed pipeline incorrectly auto-accepted —
  the safety-critical number.
- **TNR** = impostor→(NOT_VERIFIED∪DENIED) / impostor_total.
- **IMRR** = impostor→MANUAL_REVIEW / impostor_total. Invariant: FAR + TNR
  + IMRR = 1.
- **Coverage** = (genuine_verified + genuine_rejected + impostor_verified +
  impostor_rejected) / eligible_identity_trial_count. MEANING: fraction of
  eligible trials FANS-C resolved automatically (without a human decision).
- **Conditional Automatic Accuracy** = (genuine_verified +
  impostor_rejected) / auto_decided_count. MEANING: "how accurate was
  FANS-C when it *did* decide automatically" — MANUAL_REVIEW trials are
  excluded from this denominator (they remain visible via Coverage/GMRR/
  IMRR), so this is never presented as unqualified "Accuracy."
- **Correct Automatic Outcome Rate** (optional/descriptive) = (genuine_
  verified + impostor_rejected) / eligible_identity_trial_count. MEANING:
  "what fraction of ALL eligible trials were correctly and automatically
  resolved" — NOT standard binary accuracy, and never substituted for
  Conditional Automatic Accuracy.

LIMITATION common to all of the above: computed only over trials with
`presentation_ground_truth=BONA_FIDE` — see §34.3 for why staged attack
trials are excluded from this cohort.

### 34.6 Matcher formulas

`get_matcher_performance(dataset)` — identical formula set, but:

- **Population:** matcher-evaluable cohort = primary cohort further
  restricted to `similarity_score IS NOT NULL AND matcher_base_decision IS
  NOT NULL` — i.e. trials that actually reached FaceNet comparison.
  Pre-comparison liveness/PAD denials are excluded (the matcher was never
  run on them; including them would understate matcher performance for a
  reason unrelated to the matcher).
- **Decision field:** `matcher_base_decision` — the raw
  `decide_base_outcome()` result, before any post-score safety overlay.
- **Difference from final system:** matcher metrics characterize the
  similarity-score classifier alone; final-system metrics characterize the
  classifier plus FANS-C's safety overlay (lookalike escalation,
  representative-fallback-block). They will diverge whenever an escalation
  fires — see §34.7 and `MatcherVsFinalDecisionTest`, the single most
  important test in this checkpoint (BPA-3 §35).

### 34.7 Matcher → final-system delta

`get_matcher_to_final_delta(dataset)` computes, over the matcher-evaluable
cohort, a single grouped query
(`.values('matcher_base_decision','system_decision').annotate(Count)`)
producing every observed `{matcher}_to_{final}` transition count, plus
`changed_count` (trials where the two decisions differ) and counts of
`quality_override_applied` / `lookalike_escalation_applied` /
`representative_fallback_blocked`. These are reported as **FANS-C safety
escalations**, never as FaceNet matcher errors — see §29 above.

### 34.8 Zero-denominator behavior

Every rate uses `_rate(numerator, denominator)` (§34.1): `denominator=0` ⇒
`rate=None`. Verified for TAR/FRR/GMRR/FAR/TNR/IMRR/Coverage/Conditional
Automatic Accuracy, attack-metric rates, timing statistics
(`mean`/`sample_stddev`=`None` when count is 0 or <2 respectively), score
distributions, ROC/AUC/EER (all `None` with an explicit `warnings` list
when a class is empty) — see `ZeroEmptyDatasetTest` and
`RocAucEerTest.test_one_class_missing_all_none_with_warnings`.

### 34.9 Participant / trial counts

`get_participant_and_trial_counts(dataset)` distinguishes
`unique_participant_count` (distinct `participant_code`) from trial counts:
`total_trial_count`, `pending_trial_count`, `completed_trial_count`,
`aborted_trial_count`, `eligible_identity_trial_count` (primary cohort),
`genuine_trial_count` / `impostor_trial_count` (all COMPLETED trials with
that ground truth, not restricted to bona-fide), `attack_trial_count`
(COMPLETED trials with an actual attack `presentation_ground_truth`).

### 34.10 Beneficiary / representative stratification

`get_target_type_stratification(dataset)` runs the full final-system
formula set separately for `target_beneficiary__isnull=False` and
`target_representative__isnull=False` subsets of the primary cohort.
Returned with an explicit note that these are descriptive subgroup rates
only — no significance test is performed, and a difference between the two
subgroups' rates must not be presented as a proven performance difference.

### 34.11 Liveness / attack metrics

**Liveness pathway breakdown** (`get_liveness_pathway_breakdown`):
descriptive `system_decision` counts grouped by the stored
`liveness_pathway` (`passive_only` / `active_challenge` / `failed_passive`
/ `failed_active`). Carries an explicit
`active_challenge_attestation_limitation` string stating that
`active_challenge`/`head_movement_completed` is **researcher-attested**,
not machine-verified via the production browser's MediaPipe pipeline —
never labeled "Machine-Verified Active Liveness Accuracy."

**Presentation-attack metrics** (`get_presentation_attack_metrics`):

- **Bona-Fide (Genuine) Pass Rate** = (BONA_FIDE ∩ GENUINE trials →
  VERIFIED) / (BONA_FIDE ∩ GENUINE trial count). Restricted to `GENUINE`
  identity truth deliberately — a bona-fide **impostor** being "verified"
  is a false accept, not a presentation-attack pass, so it must not inflate
  this rate (`test_bona_fide_impostor_verified_not_counted_as_attack_pass`).
- **Attack Block Rate** = attack trials → (NOT_VERIFIED ∪ DENIED) / attack
  trial count.
- **Attack Manual Review Rate** = attack trials → MANUAL_REVIEW / attack
  trial count.
- **Attack Automatic Acceptance Rate** = attack trials → VERIFIED / attack
  trial count.

All three attack rates are computed both overall and per attack type
(`PRINT_PHOTO`/`SCREEN_REPLAY`/`OTHER_ATTACK`) and preserved as a
three-way split — never collapsed into a single pass/fail number
(`AttackMetricTest`). These are deliberately **not** labeled APCER/BPCER —
see BPA-3 §18: that would be an unearned standards-conformance claim this
controlled protocol does not establish.

### 34.12 Timing analytics

`get_timing_analytics(dataset, group_by=None|'system_decision'|
'target_type'|'liveness_pathway')` reports **count / mean / median / min /
max / sample standard deviation** over `verification_duration_ms`, restated
under its established name, **Controlled Verification Elapsed Time** (§15;
not "capture-to-display latency" — see §21 for why). Uses **sample**
standard deviation (`n-1` denominator), returning `None` for `n<2`
(`_sample_stddev`). `TimingAnalyticsTest.test_exact_statistics_for_
1000_2000_3000` hand-verifies n=3, mean=2000.0, median=2000.0, sample
stddev=1000.0 exactly.

### 34.13 Score distributions

`get_score_distributions(dataset, bins=10)` returns, for genuine and
impostor scores separately over the matcher-evaluable bona-fide cohort:
n/mean/median/min/max/sample_stddev, the raw sorted score list, and a
fixed-range `[0.0, 1.0]` histogram (`bin_edges`/`counts`) — controlled-study
scale, so returning the raw list is acceptable (§21 of the checkpoint
instructions); a production-scale dataset would need pagination or
server-side binning only, not a design change here.

### 34.14 Threshold sweep

`run_threshold_sweep(dataset, candidate_policies)` takes an explicit list
of `(manual_review_threshold, auto_verify_threshold)` pairs (BPA-3 §22
option A) and, for each pair, re-evaluates `face_utils.decide_base_outcome`
— **the exact same shared function** `verify_submit` and
`evaluation_trial_run` already use (identity-checked by
`MatcherVsFinalDecisionTest.test_decide_base_outcome_is_the_shared_
function`) — against every matcher-evaluable trial's real, persisted
`similarity_score`. **Review floor:** unchanged, computed automatically
inside `decide_base_outcome` as `manual_review_threshold * 0.85` — no
independent approximate band was implemented (§23). For each candidate
pair it returns the full genuine/impostor verified/manual_review/rejected
counts and the complete TAR/FRR/GMRR/FAR/TNR/IMRR/Coverage/Conditional-
Automatic-Accuracy set. **Limitation, stated in the result itself:** this
is a MATCHER-LEVEL COUNTERFACTUAL — it does not re-apply the final-system
post-score safety overlay (quality override / lookalike escalation /
representative-fallback-block), so it characterizes what the raw matcher
would have decided at that threshold, not what the FINAL FANS-C decision
would have been. No "optimal threshold" is selected, computed, or implied.

### 34.15 ROC

`compute_roc(dataset)` sweeps every observed similarity-score threshold
(plus the two extremes) over the matcher-evaluable bona-fide cohort,
computing `TPR = genuine≥threshold / n_genuine` and `FPR =
impostor≥threshold / n_impostor` at each point. **Population:** continuous
similarity score + identity ground truth only — Manual Review is
deliberately not a third axis; this characterizes score separability, not
the deployed three-zone policy. **Output:** an ordered list of
`{threshold, tpr, fpr}` points, `n_genuine`/`n_impostor`, and a `warnings`
list (non-empty, with `points=[]`) when either class has zero trials.

### 34.16 AUC

`compute_auc(dataset)` computes a **deterministic trapezoidal** AUC over
the ROC points above (`RocAucEerTest.test_deterministic_repeatable` proves
repeatability). **Unavailable cases:** returns `auc=None` with `roc`'s
warnings whenever either class is empty — never a fabricated placeholder.
Not a claim about production correctness — only about matcher score
separability on this controlled cohort.

### 34.17 EER

`compute_eer(dataset)` estimates the Equal Error Rate via **linear
interpolation** between the two adjacent swept-threshold points where
`FAR (=FPR)` and `FRR (=1-TPR)` cross (or an exact match, when one swept
point has `FAR==FRR` precisely). **Meaning:** MATCHER CHARACTERIZATION —
the threshold at which the matcher's false-accept and false-reject rates
would be approximately equal under a simple binary cut. **Why not "the
optimal threshold":** FANS-C's actual deployed policy is a three-zone band
with a Manual Review buffer, not a single binary cut chosen to equalize
FAR/FRR — EER characterizes the score distribution's separability, not an
operating recommendation. Returns `eer=None` with a `warnings` entry when
no crossing is found (e.g. perfect class separation with no reject-side
data) or when a class is empty.

### 34.18 Claim Recording Reliability

**Implemented:** NO — `get_claim_recording_reliability()` returns
`{'computable': False, 'reason': ..., 'recommendation': ...}` rather than a
number.

**Why historical reconstruction is not trustworthy:** this metric is not
derived from `EvaluationTrial` at all — it concerns operational
`VerificationAttempt`/`ClaimRecord` data. `ClaimRecord.status` (and
`claimed_at`/`updated_at`) is a **mutable current-state** column with no
append-only transition log. Reconstructing, for a past `VerificationAttempt`,
whether "a valid claimed record already existed at the moment that
attempt was decided" (Population A) or "one already existed" (Population
B) requires knowing the claim's status **at that historical moment** — but
a claim that is `claimed` today may have been `cancelled` and re-claimed,
or `claimed` and later `cancelled`, at some point since, and the current
schema cannot distinguish these histories after the fact. Computing this
retrospectively from current-state columns would silently misclassify any
attempt whose associated claim's status has changed since. Per BPA-3 §28,
this checkpoint reports "not retrospectively computable with current
schema" rather than fabricate the metric, and recommends prospective
instrumentation (an immutable claim-state snapshot on `VerificationAttempt`
at decision time, or an append-only `ClaimRecord` status-history table) so
a future checkpoint can compute this correctly **going forward**.

### 34.19 Performance

All aggregate-producing functions use Django's conditional `Count(filter=
Q(...))` aggregation or a single `.values(...).annotate(Count)` grouped
query — no per-trial Python loop or per-trial query for any matrix/rate/
delta/breakdown calculation. `run_threshold_sweep`, `compute_roc`,
`compute_auc`, and `compute_eer` do iterate the matcher-evaluable cohort's
rows in Python (score-by-score classification/threshold sweep is
inherently row-wise), but this is a single fetch (`.values(...)` once) plus
in-memory computation, not a query per row. `QueryCountPerformanceTest`
proves `build_metric_report()`'s query count is identical for a 5-trial and
a 50-trial dataset — it does not scale with trial count. Expected
study-scale behavior: hundreds to low thousands of trials, well within this
approach; a much larger dataset would first need pagination on the raw
score-list/histogram outputs (§34.13), not a change to this aggregation
strategy.

### 34.20 Security / privacy

Every function in this module takes only an `EvaluationDataset` object and
reads/aggregates `EvaluationTrial` fields already documented in §6-7 as
pseudonymous. No function reads `Beneficiary`/`Representative` PII fields,
decrypted embeddings, images, or video — `target_beneficiary`/
`target_representative` are used only via `__isnull` filters (existence
checks), never dereferenced for name/DOB/ID/etc. Result dicts contain only
counts, rates, scores, and dataset/decision labels.

### 34.21 What BPA-3 does NOT do

- Does not create the Biometric Performance Analytics UI (BPA-4).
- Does not report any number derived from real study participants — every
  test in `verification/tests_bpa3.py` uses clearly synthetic fixtures, and
  this module has never been invoked against a real completed dataset.
- Does not implement Claim Recording Reliability or Duplicate-Block
  Correctness (§34.18).
- Does not change `SystemConfig` thresholds, `decide_base_outcome()`, or
  any production decision path — `run_threshold_sweep` only *reads*
  `similarity_score` and calls the existing pure function.
- Does not add a migration — no schema change was needed.

---

## 35. BPA-3.1 — Metric Semantics + Score-Domain Hardening

Status as of this writing: **small correctness pass over §34, no new
metrics UI, no new schema.** Two genuine defects in the BPA-3 engine were
found and fixed; both are audit findings, not hypotheticals — see §35.1 and
§35.3. No migration was added or required.

### 35.1 Cosine-similarity score domain

**The defect:** `face_utils.cosine_similarity()` (`verification/
face_utils.py`) computes a raw cosine similarity —
`np.dot(emb1/‖emb1‖, emb2/‖emb2‖)` — with no clamping anywhere in that
function. Its true mathematical range is **[-1.0, 1.0]**, not [0, 1], even
though real FaceNet embeddings of faces commonly land in a positive band in
practice. BPA-3's `_histogram()` used a fixed `[0.0, 1.0]` range with a
clamp (`max(0, min(idx, bins-1))`) that, for any legitimately negative
score, would have silently placed it in the same bin as a small positive
score (e.g. `-0.20` and `0.05` both landing in "bin 0") — exactly the
"silently clamp a negative value to zero" failure mode this checkpoint was
asked to rule out.

**The fix:** `biometric_analytics._SCORE_DOMAIN = (-1.0, 1.0)`. `_histogram()`
now spans the full legal cosine domain; `get_score_distributions()` returns
`score_domain` alongside every genuine/impostor distribution so a caller
never has to guess the assumed range. `_numeric_stats()` (mean/median/min/
max/sample_stddev) never assumed a domain and needed no change — only the
histogram binning was defective. `NegativeScoreDomainTest` in
`verification/tests_bpa3_1.py` proves a `-0.20` score lands in its own bin,
survives `get_score_distributions()`'s `min`/`values` output, and is
correctly swept as an ROC threshold.

**Model validation finding (not fixed this checkpoint):**
`EvaluationTrial.similarity_score` carries `validators=[MinValueValidator
(0.0), MaxValueValidator(1.0)]` — narrower than the true cosine domain.
Tracing every call site that writes this field
(`verification/views.py`, `_complete_trial()` and `evaluation_trial_liveness`)
shows both call `trial.full_clean()` **before** `trial.save()` — so if
`compare_with_all_embeddings`/`compare_with_stored` ever actually returned
a negative score for a real controlled trial, `full_clean()` would raise a
`ValidationError` and the trial would fail to complete. This is a real
(if currently unobserved — no test or fixture has ever produced a negative
match score) latent defect, not merely a cosmetic restriction. **This
checkpoint does not change the model.** Widening the validator to
`[-1.0, 1.0]` would be a backward-compatible loosening (no currently-valid
data becomes invalid) and Django's migration framework treats a validators
change as a state change requiring a migration — this checkpoint's
instructions call for **zero** migrations, so the fix is intentionally
deferred. **Recommendation for a future checkpoint:** widen
`MinValueValidator(0.0)` to `MinValueValidator(-1.0)` on
`EvaluationTrial.similarity_score` (and audit `VerificationAttempt.
similarity_score`/`FaceUpdateRequest.duplicate_match_score` for the same
issue) in a dedicated schema-touching checkpoint, with the migration
reviewed and approved explicitly. **Resolved by BPA-3.2 §36.1** —
`EvaluationTrial.similarity_score` now validates `[-1.0, 1.0]`.

### 35.2 BONA_FIDE presentation is not the same as GENUINE identity

**The defect:** BPA-3's `get_presentation_attack_metrics()` computed its
"Bona-Fide Pass Rate" over `presentation_ground_truth=BONA_FIDE AND
identity_ground_truth=GENUINE` — silently making GENUINE identity a hidden
requirement of what should have been a purely presentation-truth-scoped
cohort. `identity_ground_truth` (GENUINE/IMPOSTOR) and
`presentation_ground_truth` (BONA_FIDE/attack types) are independent
dimensions (§3 above) — a live IMPOSTOR presenting their own real face is
still a BONA_FIDE presentation from the liveness/PAD gate's perspective,
and excluding such trials from the "bona-fide" cohort silently understated
its true size and conflated a presentation-truth measurement with an
identity-truth one.

**The fix:** the corrected gate metrics (§35.3) group by
`presentation_ground_truth` alone. `BonaFideIndependentOfIdentityTest`
proves a GENUINE and an IMPOSTOR BONA_FIDE trial are both counted in the
overall bona-fide gate cohort, with identity stratification offered
**separately** and explicitly (`bona_fide.by_identity_ground_truth`), never
as a hidden filter on the headline number.

**Primary identity-performance cohort unaffected:** BPA-3 §5's primary
cohort definition (`trial_status=COMPLETED AND identity_ground_truth ∈
{GENUINE,IMPOSTOR} AND presentation_ground_truth=BONA_FIDE`) is intentional
and correct for TAR/FRR/GMRR/FAR/TNR/IMRR — those formulas measure identity
performance and must exclude staged-attack trials; only the "Bona-Fide Pass
Rate" gate-adjacent metric had the erroneous extra identity filter, and it
is now expressed correctly via the two separate functions in §35.3, not by
changing §34.3-34.5.

### 35.3 Presentation-attack GATE performance vs. END-TO-END attack outcome

**The defect:** BPA-3's single `get_presentation_attack_metrics()` computed
its "block rate" from `system_decision` (the FINAL FANS-C decision) and
implicitly presented it as attack-detection behavior. But a trial can be
automatically rejected for reasons that have nothing to do with the
liveness/PAD gate — an identity mismatch at the matcher, a lookalike
escalation, the representative-fallback block — so calling every
non-VERIFIED attack trial "PAD detected this" overstates what the PAD/
liveness gate itself actually did. Conversely, a trial where the PAD gate
was fooled (passed a genuine attack through) but the matcher happened to
reject it anyway would previously have been counted as "blocked," crediting
PAD for a rejection PAD had no part in.

**The fix — two now-separate functions:**

`get_presentation_attack_gate_metrics(dataset)` — **PRESENTATION-ATTACK
GATE PERFORMANCE.** Derived from the trial's stored `liveness_pathway`
(the BPA-2.1 runner's own record of what the liveness/anti-spoof/PAD gate
decided), never from `system_decision`:
- Gate PASS pathways: `passive_only`, `active_challenge`.
- Gate BLOCKED pathways: `failed_passive`, `failed_active`.
- Trials with no recorded pathway (blank) are excluded from the
  denominator and reported as `excluded_unknown_gate_outcome_count`.
- **Attack Gate Block Rate** = attack trials with a blocked pathway /
  eligible attack trials (recorded pathway).
- **Attack Gate Pass Rate** = attack trials with a pass pathway / eligible
  attack trials.
- **Bona-Fide Gate Pass Rate** / **Bona-Fide Gate Rejection Rate** —
  identical shape, over the BONA_FIDE cohort (§35.2), NOT APCER/BPCER.
- Computed overall and per attack type (`PRINT_PHOTO`/`SCREEN_REPLAY`/
  `OTHER_ATTACK`), never collapsed.
- Still carries the `active_challenge_attestation_limitation` string (§34.11
  / BPA-3.1 §10) — `active_challenge` remains researcher-attested, never
  machine-verified, and no "Machine-Verified Active Liveness Accuracy" is
  derived from it.

`get_end_to_end_attack_outcome(dataset)` — **END-TO-END FANS-C
ATTACK-TRIAL OUTCOME** (explicitly labeled as such, in the result itself,
so it can never be mistaken for a PAD number). Derived from
`system_decision`, exactly as BPA-3's original attack breakdown was, but
renamed and re-documented: `automatically_intercepted` (attack
non-acceptance rate), `routed_to_manual_review` (attack manual review
rate), `incorrectly_automatically_accepted` (attack automatic acceptance
rate) — a three-way split, never collapsed, per attack type and overall.
Its `note` field states plainly that interception may come from the gate,
the matcher, lookalike escalation, or the representative-fallback block —
never attributed to PAD alone.

**Proof these stay analytically separate:**
`GateVsEndToEndSyntheticCaseTest.
test_pad_gate_passed_incorrectly_but_matcher_still_rejects` (BPA-3.1 §15)
constructs exactly the scenario the checkpoint specified —
`presentation_ground_truth=PRINT_PHOTO`, `liveness_pathway=passive_only`
(gate incorrectly passed the spoof), `system_decision=NOT_VERIFIED` (the
matcher rejected it on identity grounds) — and asserts: Attack Gate Block
Rate for this trial = 0 (gate did NOT block it), Attack Non-Acceptance
Rate = 1.0 (the trial WAS ultimately intercepted, end-to-end), and the two
rates are asserted `assertNotEqual` to guarantee the engine never conflates
them.

`build_metric_report()` now exposes `presentation_attack_gate_metrics` and
`end_to_end_attack_outcome` as two separate top-level keys; the old
`presentation_attack_metrics` key/function no longer exists.

### 35.4 ROC / AUC / EER audit (no defect found)

Re-derived from first principles against §35.5's requirements — all
already correct, confirmed by new tests rather than by inspection alone:

- **Threshold direction:** `compute_roc()` computes `TPR = (genuine scores
  ≥ threshold)/n_genuine`, `FPR = (impostor scores ≥ threshold)/n_impostor`
  — higher cosine similarity is unconditionally "more likely accepted."
  `test_higher_score_means_more_genuine_like_threshold_direction` sweeps
  threshold downward and asserts TPR/FPR are monotonically non-decreasing.
- **Full domain support:** the sweep uses `sorted(set(genuine_scores) |
  set(impostor_scores), reverse=True)` — actual observed values, not a
  fixed [0,1] assumption — so negative scores sweep correctly with no
  change needed (`NegativeScoreDomainTest.
  test_negative_score_present_in_roc_and_does_not_crash`).
- **Boundary/endpoint completeness:** `candidate_thresholds` always
  prepends `max(scores)+1e-9` and appends `min(scores)-1e-9`, guaranteeing
  the curve reaches both `(0,0)` (reject everything) and `(1,1)` (accept
  everything) regardless of the actual score values — so AUC is never
  understated by a missing endpoint
  (`test_roc_boundary_endpoints_present_so_auc_not_understated`).
- **Tie determinism:** repeated calls on tied scores produce byte-identical
  point lists (`test_tied_scores_are_deterministic_across_repeated_calls`).
- **AUC ordering:** `compute_auc()` explicitly re-sorts by `(fpr, tpr)`
  ascending before trapezoidal integration, independent of the order
  `compute_roc()` produced them in —
  `test_auc_integration_uses_ascending_fpr_order` confirms this holds.
- **Perfect separation → AUC=1.0**
  (`test_perfect_separation_auc_is_exactly_one`, exact to 9 decimal places).
- **Fully reversed separation → AUC≈0.0** — a new case BPA-3 did not test:
  when genuine scores are uniformly LOWER than impostor scores (the matcher
  behaving backwards), AUC must reflect near-zero separability, not be
  accidentally miscomputed as near 1.0
  (`test_fully_reversed_ordering_auc_near_zero`).
- **One-class datasets:** unchanged from BPA-3 — `points=[]`,
  `auc=None`, `eer=None`, each with an explicit warning; never fabricated.

### 35.5 EER threshold-domain audit (no defect found, now proven)

`compute_eer()`'s interpolated threshold is always a strict convex
combination (`ratio ∈ (0,1)` whenever the crossing condition
`d_prev·d_curr < 0` holds) of two **adjacent candidate thresholds from the
sweep**, both of which lie within `[min(scores)-1e-9, max(scores)+1e-9]` by
construction (§35.4) — so the estimated EER threshold can never fall
outside the observed/legal score range. This was true in BPA-3's original
implementation; BPA-3.1 adds `EerThresholdDomainAuditTest` to prove it
rather than leave it as an unverified property, including with a negative
score in the mix. EER continues to be labeled `linear_interpolation` or
`exact_match` — never `"optimal"` — and remains a MATCHER
characterization, not the FANS-C deployed operating point (§34.17
unchanged).

### 35.6 Primary identity-performance formulas — confirmed unchanged

No defect was found in TAR/FRR/GMRR/FAR/TNR/IMRR/Coverage/Conditional
Automatic Accuracy (§34.5) or the primary 2×3 matrix (§34.4).
`PrimaryIdentityMetricsUnchangedRegressionTest` re-runs BPA-3's original
hand-verified fixture through the unmodified `get_final_system_performance()`
and asserts byte-for-byte identical rates, and separately confirms Manual
Review remains a distinct third outcome, never folded into either
VERIFIED or REJECTED.

### 35.7 What BPA-3.1 does NOT do

- Does not create the Biometric Performance Analytics UI (still BPA-4).
- Does not change the `EvaluationTrial.similarity_score` model field or add
  a migration (§35.1) — reported as a finding for a future checkpoint,
  resolved by BPA-3.2 (§36).
- Does not change any primary identity-performance formula (§35.6).
- Does not report any number from real study participants — every test in
  `verification/tests_bpa3_1.py` uses synthetic fixtures.
- Does not modify `MANUSCRIPT.pdf`.

## 36. BPA-3.2 — Cosine Similarity Schema Alignment

Status as of this writing: **single-field schema correction, no new
metrics, no new UI.** Closes the one model-validation finding BPA-3.1 left
deferred (§35.1) — no other defect was found or fixed.

### 36.1 EvaluationTrial.similarity_score now validates the true cosine domain

**The defect (carried over from §35.1):** `EvaluationTrial.similarity_score`
validated `[0.0, 1.0]` even though `face_utils.cosine_similarity()` (verified
again in this checkpoint — `np.dot(emb1/‖emb1‖, emb2/‖emb2‖)`, still no
clamping anywhere in that function) has a legal mathematical range of
`[-1.0, 1.0]`. The one call site that writes a real matcher score to this
field, `_complete_trial()` (`verification/views.py`), calls
`trial.full_clean()` before `trial.save()`, so a legitimately negative
matcher score would have raised a `ValidationError` and the controlled
trial would have failed to complete instead of recording a valid
NOT_VERIFIED result. (`_abort_trial()` also calls `full_clean()`, but only
ever sets this field to `None`, so it was never affected by the defect.)

**The fix:** `EvaluationTrial.similarity_score`'s validators changed from
`[MinValueValidator(0.0), MaxValueValidator(1.0)]` to
`[MinValueValidator(-1.0), MaxValueValidator(1.0)]`
(`verification/migrations/0027_evaluationtrial_similarity_score_cosine_domain.py`).
`null=True`/`blank=True` are unchanged — a trial denied before comparison
still legitimately has no score. No other field's validators were touched:
the three threshold-snapshot fields (`review_threshold_snapshot`,
`auto_verify_threshold_snapshot`) and the liveness/anti-spoof/PAD score
snapshots (`liveness_score`, `anti_spoof_score`, `pa_score`) are configured
operating-policy or bounded-heuristic values, not raw cosine similarities,
and correctly remain `[0.0, 1.0]`.

**Concepts kept distinct (per this checkpoint's brief):** the cosine
**score domain** (`[-1, 1]`, a mathematical property of the matcher) is not
the same thing as the **operating threshold configuration**
(`SystemConfig.get_threshold()` / `get_auto_verify_threshold()`, positive
policy values chosen by FANS-C operators). Widening the score-storage field
does not widen, and must never widen, the threshold configuration domain.

### 36.2 VerificationAttempt audit — no change made

`VerificationAttempt.similarity_score` is `models.FloatField(null=True,
blank=True)` with **no validators at all** — it already accepts any float,
including negative cosine scores; there is no `[0, 1]` restriction to widen.
Production writes (`verification/views.py`, `verify_submit`) assign the
field directly and save via the ORM without calling `full_clean()` or
routing through a `ModelForm`, so no validator would run against it even if
one existed, and no database-level `CheckConstraint` exists for this field
(confirmed against every `verification` migration). **Match: the field
already matches the true `[-1, 1]` cosine domain — trivially, by having no
narrower restriction — so it needed no change in this checkpoint.**

### 36.3 What BPA-3.2 does NOT do

- Does not change `VerificationAttempt.similarity_score` (§36.2 — already
  unrestricted, changing it would be a no-op).
- Does not change any threshold, threshold-snapshot field, or liveness/
  anti-spoof/PAD score field — those remain `[0.0, 1.0]` by design (§3 of
  the original checkpoint brief).
- Does not create the Biometric Performance Analytics UI (still BPA-4).
- Does not audit `FaceUpdateRequest.duplicate_match_score` or any other
  field outside the two named in this checkpoint's brief — flagged for a
  future checkpoint if it independently proves defective.
- Does not report any number from real study participants — every test in
  `verification/tests_bpa3_2.py` uses synthetic fixtures.
- Does not modify `MANUSCRIPT.pdf`.

## 37. BPA-4 — Biometric Performance Analytics UI

BPA-4 is a presentation/integration checkpoint: it builds the "Biometric
Performance" Analytics page and wires it to the reviewed BPA-3/BPA-3.1/
BPA-3.2 calculation layer (`verification/biometric_analytics.py`). It does
**not** change any FAR/FRR/TAR/GMRR/IMRR/TNR/Coverage/Conditional-Automatic-
Accuracy/ROC/AUC/EER/attack-gate/end-to-end formula — see §37.8.

### 37.1 URL, view, template

- URL: `verification:analytics_biometric_performance` →
  `/verification/analytics/biometric-performance/`.
- View: `verification.views.analytics_biometric_performance` — gated by
  `_evaluation_admin_required()`, the same admin-tier (President/Admin/IT)
  gate already used by the rest of the controlled-evaluation workflow
  (`evaluation_dataset_list`/`_detail`/etc., §14 above). Staff and anonymous
  users are denied.
- Template: `templates/verification/analytics_biometric.html`, extending
  `base.html` like every other Analytics tab.
- Nav: added as a fourth tab, "Biometric Performance", in
  `templates/verification/_analytics_nav.html`, between Operational and
  Security (Executive → Operational → Biometric Performance → Security).
  The shared date-range filter form in that partial (meant for the
  operational Executive/Operational/Security tabs) is now wrapped in
  `{% if active_tab != 'biometric' %}` so the Biometric Performance tab
  never shows an irrelevant operational date filter — it uses its own
  dataset selector instead.

### 37.2 Dataset selector and status banner

The page operates on exactly one explicitly-selected `EvaluationDataset` at
a time (`?dataset=<uuid>` query parameter) — it never aggregates across
datasets. A `<select>` lists every dataset with name, protocol version,
status, and trial count. If exactly one dataset exists it is preselected
for convenience, but the dataset is still named explicitly in the banner
and Dataset Context section below. An invalid or unknown UUID renders a
safe "could not be found" message (never a stack trace) and falls back to
the selection state.

Banner wording by `EvaluationDataset.status`:

| Status | Banner |
|---|---|
| `completed` | "CONTROLLED EVALUATION RESULTS — FINALIZED DATASET" |
| `collecting` / `draft` | "INTERIM CONTROLLED-EVALUATION RESULTS — DATA COLLECTION NOT FINALIZED" |
| `archived` | "ARCHIVED CONTROLLED-EVALUATION DATASET" |

### 37.3 No-data vs. measured-zero

Every rate is rendered through the `rate_pct` template filter
(`verification/templatetags/bpa_filters.py`), which reproduces
`biometric_analytics._rate`'s convention in the presentation layer: a
`None` rate (zero denominator) renders as **"Not available"**, never
"0.00%"; a genuinely measured zero (positive denominator, zero numerator)
renders as **"0.00%"**. Covered by `ZeroDenominatorViewTest` and
`PercentageFormatFilterTest` in `verification/tests_bpa4.py`.

### 37.4 Matcher vs. final system, and the Manual Review outcome

The page always shows the Three-Outcome Identity Decision Matrix
(Verified / Manual Review / Reject-Denied × Genuine / Impostor) before any
binary framing, and explicitly labels Manual Review as an intentional
safety outcome, never a rejection. FaceNet Matcher Performance is rendered
as a clearly secondary section with an explicit "excludes FANS-C's
post-score safety escalation rules" note, followed by the Matcher → Final
FANS-C Decision Transitions table (from
`get_matcher_to_final_delta`) so a reader can see exactly how lookalike
escalation / representative-fallback-block / quality override changed the
raw matcher outcome into the final one.

### 37.5 Score distribution, thresholds, and threshold sensitivity

The genuine/impostor score-distribution chart spans the full cosine domain
`[-1.0, 1.0]` (never `[0, 1]`), sourced verbatim from
`get_score_distributions`'s histogram output — no client-side clamping.
Two new **additive** helpers in `biometric_analytics.py` support threshold
presentation without touching any reviewed formula:

- `get_threshold_snapshot_summary(dataset)` — the distinct
  `(review_threshold_snapshot, auto_verify_threshold_snapshot)` pairs a
  dataset's trials actually recorded. If more than one distinct pair is
  present, the UI shows a "multiple threshold policies represented"
  warning instead of drawing one single (potentially misleading) threshold
  reference line on the score-distribution chart.
- `default_exploratory_threshold_candidates(dataset)` — a small,
  transparent candidate set for the Threshold Sensitivity table: the
  dataset's own snapshotted pair(s) plus the current live `SystemConfig`
  operating pair. No candidate is ever labeled "Optimal", "Best", or
  "Recommended" — the table uses "Candidate Policy" / "Observed Trade-off"
  language, and the section is explicitly badged "Exploratory". This
  never resurrects a fixed "0.70–0.80" recommendation.

### 37.6 ROC / AUC / EER, and attack sections

ROC/AUC render only when both genuine and impostor matcher-evaluable
scores exist; otherwise the page shows the same "Not available"/"ROC
cannot be computed" wording the engine already returns. EER is always
labeled "Estimated Equal Error Rate" / "Estimated EER Threshold" — the
words "Optimal Threshold" and "Recommended Threshold" never appear on this
page (enforced by `EerWordingTest`).

The Liveness/PAD Security-Gate Performance section and the End-to-End
Attack-Trial Outcome section are kept as two visually separate cards, each
sourced from its own BPA-3.1 function
(`get_presentation_attack_gate_metrics` / `get_end_to_end_attack_outcome`)
— never merged into one "attack block rate." The end-to-end section
carries an explicit "not equivalent to PAD detection" caveat.

### 37.7 Timing, subgroups, Claim Recording Reliability

Controlled Verification Elapsed Time is displayed in seconds (millisecond
precision retained internally, via the `ms_to_seconds` filter), with the
same start/end/included/excluded definitions `get_timing_analytics`
already documents. Beneficiary vs. Representative results are shown with
an explicit "no significance test performed" disclaimer, never ranked.
Claim Recording Reliability always renders the engine's "not
retrospectively computable" explanation — never a bare percentage.

### 37.8 What BPA-4 does NOT do

- Does not change any BPA-3/BPA-3.1/BPA-3.2 formula, cohort definition, or
  rate convention in `biometric_analytics.py` — the two functions added
  (§37.5) are new, additive, read-only presentation helpers, not
  modifications to `_identity_performance`, `run_threshold_sweep`,
  `compute_roc`/`compute_auc`/`compute_eer`, or either attack-metric
  function.
- Does not recompute any rate, count, or threshold in a template or in
  JavaScript — `verification/templatetags/bpa_filters.py` only formats
  values the calculation layer already produced (percentage/decimal/ms→s
  string conversion), and `static/js/analytics_biometric.js` only plots
  server-provided histogram/ROC-point arrays.
- Does not require a migration (confirmed via `makemigrations --check
  --dry-run`).
- Does not remove, rename, or change the metric formulas of the existing
  Executive/Operational/Security Analytics tabs or the Template Match
  report.
- Does not claim, compute, or display any real study Accuracy/FAR/FRR/PAD
  performance — the page presents whatever `EvaluationTrial` rows exist in
  the database (synthetic/QA rows during this checkpoint's own browser
  verification, deleted afterward — see the BPA-4 checkpoint report), the
  same non-claim `biometric_analytics.py`'s own module docstring already
  makes.
- Does not add a Claim Recording Reliability percentage — still rendered
  as the engine's structured "not computable" result (§34.18/§37.7).
- Does not begin BPA-5, Phase B/C/D, or build an installer/EXE.

---

## 38. BPA-5 — Final Biometric Evaluation Readiness

BPA-5 is an audit + methodology-synchronization + data-lifecycle checkpoint,
not a new metric or UI checkpoint. It adds exactly two schema fields (dataset
purpose, trial withdrawal), one data-minimization change (clearing the
liveness-proof embedding once it is no longer needed), and a read-only
data-quality/readiness layer in `biometric_analytics.py` — see
`verification/tests_bpa5.py` (30 tests) for behavioral coverage. It does
**not** change any BPA-3/BPA-3.1/BPA-3.2 formula, does not begin real
participant data collection, and does not modify `MANUSCRIPT.pdf`.

### 38.1 Dataset purpose classification

**Problem found:** nothing distinguished a real controlled study from
QA/synthetic/developer/browser-test data. BPA-4's own browser-verification
rows were created and manually deleted afterward specifically because no
field existed to mark them non-authoritative (BPA-4 checkpoint report). A
future researcher opening the dataset list has no way to tell, from the
data alone, which datasets are real.

**Fix:** `EvaluationDataset.purpose` — `RESEARCH_STUDY` / `PILOT_CALIBRATION`
/ `QA_SYNTHETIC`, required at creation (`evaluation_dataset_create`),
defaulting to `QA_SYNTHETIC` at the model level (the conservative choice for
any row that predates this field or is created outside the form, e.g. via
shell/fixture — an unlabeled dataset is never silently treated as a real
result). `EvaluationDataset.is_final_study_result` is `True` only when
`status=COMPLETED` **and** `purpose=RESEARCH_STUDY` — this is the single
gate a future manuscript number, or the study-readiness classification
(§38.8), must check before citing any rate from a dataset.

**Pilot vs. final evaluation, folded into the same field (deliberately):**
a separate `is_pilot` flag was considered and rejected — a dataset's
purpose and its pilot/final status are the same research decision, not two
independent axes. `PILOT_CALIBRATION` covers threshold-calibration/pilot
data explicitly, distinct from both a final `RESEARCH_STUDY` dataset and
non-research `QA_SYNTHETIC` data.

**No UI purpose-editor was added** (§Runbook post-session checklist) —
changing a dataset's purpose after data already exists is a deliberate
research-integrity decision, not a routine action, so it is left to direct
database access (the same restraint already applied to not adding a
dataset-deletion UI in BPA-1).

### 38.2 Calibration vs. final evaluation — leakage risk

**Risk documented, not solved by new mechanics:** if a researcher uses ONE
dataset to both (a) run `run_threshold_sweep`/ROC/AUC/EER to pick or
confirm a threshold, and (b) report that same dataset's Accuracy/FAR/FRR at
that threshold, the reported performance is optimistically biased —
textbook train/test leakage applied to a biometric threshold. FANS-C's
threshold-sensitivity tooling (BPA-4 §37.5) makes this easy to do
accidentally because it operates on whatever dataset is selected.

**Recommended protocol (this checkpoint's finding, not a code change):**
Option A — a separate `PILOT_CALIBRATION` dataset (§38.1) collected first
and used only for `run_threshold_sweep`/ROC exploration; the operating
threshold is fixed BEFORE the `RESEARCH_STUDY` dataset's collection begins;
the `RESEARCH_STUDY` dataset is then evaluated using `SystemConfig`'s
already-fixed live threshold (the trials' own
`review_threshold_snapshot`/`auto_verify_threshold_snapshot`), never
re-swept for a "better" number. This is Option A/B from the checkpoint
brief, merged: a prespecified deployed threshold (B), established via a
genuinely separate pilot dataset (A) rather than by manual promise alone.
Advanced cross-validation (k-fold, held-out folds within one dataset) is
NOT recommended for a capstone-scale controlled dataset — it would overstate
the methodological rigor this protocol can actually support and was
explicitly ruled out by the checkpoint brief.

**Enforcement is procedural, not code-blocking:** BPA-5 does not prevent a
researcher from sweeping a `RESEARCH_STUDY` dataset's own thresholds — doing
so remains useful for EXPLORATORY reporting (already labeled as such,
BPA-4 §37.5). The runbook (§0.4, §1) and this section are the controls;
`get_study_readiness()` flags dataset purpose but cannot detect "was this
same dataset already used to pick a threshold" — that is a protocol
discipline question, not a technical one.

### 38.3 Withdrawal / participant deletion

**Chosen pattern: exclusion flag, not physical deletion — Option B from
the checkpoint brief.** `EvaluationTrial.withdrawn` (+ `withdrawal_reason`,
`withdrawn_at`, `withdrawn_by`) marks a trial withdrawn without ever
deleting the row. Rejected alternative (Option A, physical deletion): would
destroy the audit trail proving a withdrawal actually happened and when,
and would silently shrink dataset trial counts in a way indistinguishable
from data loss — worse for both auditability and research integrity than a
flagged exclusion.

**Mechanism:** `EvaluationDataset.active_trials` /
`biometric_analytics._active_trials(dataset)` (`dataset.trials.exclude(
withdrawn=True)`) is now the base queryset for every cohort/metric function
in `biometric_analytics.py` — `_primary_identity_cohort_qs`,
`_exploratory_identity_cohort_qs`, the liveness-pathway breakdown, both
attack-metric functions, and timing analytics. `get_dataset_info()` and
`get_participant_and_trial_counts()` are the deliberate exceptions: they
report `withdrawn_trial_count` transparently (from the UNFILTERED
`dataset.trials`) precisely so withdrawal is never hidden from the
researcher, even though it is excluded from every RATE calculation.
`WithdrawalMetricExclusionTest` (`tests_bpa5.py`) proves a withdrawn
GENUINE/NOT_VERIFIED trial does not appear in `get_final_system_performance
()`'s genuine denominator, while `get_dataset_info()` still reports it.

**Restricted to President** (`request.user.is_president`), narrower than
the admin-tier gate (`_evaluation_admin_required`) used for the rest of the
BPA-2/BPA-2.1/BPA-4 workflow — see §38.7 for why. A trial may be withdrawn
regardless of `trial_status` (before or after it ran); a still-`PENDING`
trial that a participant abandons before running uses the existing
**Abort Trial** action instead (technical/administrative, not a withdrawal
of already-collected data).

**Operational isolation, unaffected:** withdrawal only ever writes to the
`EvaluationTrial` row itself. `WithdrawalMetricExclusionTest.
test_withdrawal_does_not_affect_operational_records` asserts
`VerificationAttempt`/`ClaimRecord` counts and the target `Beneficiary` row
are unchanged by a withdrawal POST — the same before/after-count proof
pattern `SafetyIsolationTest` (BPA-2) already established for the rest of
this workflow.

### 38.4 Withdrawn-trial semantics

A withdrawn trial is never counted as an aborted biometric failure, a false
rejection, an attack block, or a timing observation — it is excluded from
every calculation `_active_trials` feeds, full stop, the same way an
`ABORTED` trial is excluded from the identity-performance cohort (BPA-3
§3) but for a different reason (participant request, not technical
failure). `EvaluationTrial.clean()` requires a non-empty
`withdrawal_reason` whenever `withdrawn=True` (`WithdrawalSemanticsTest.
test_model_clean_requires_reason_when_withdrawn`) — a withdrawal with no
recorded reason is rejected at the model layer, not just the view layer.

### 38.5 Data retention policy

Distinguishes:

- **Operational biometric enrollment records** (`FaceEmbedding`,
  representative `face_embedding`, `VerificationAttempt`, `ClaimRecord`) —
  governed entirely by FANS-C's EXISTING operational retention/backup
  policy (`docs/BACKUP-RESTORE.md`), unaffected by anything in this
  document. BPA-5 makes no change here.
- **Research `EvaluationTrial`/`EvaluationDataset` records** — pseudonymous
  research data, retained for as long as the approved research protocol /
  institutional requirement specifies. **This document does not state a
  legal retention period** — none has been established by an institution or
  law as of this writing; retention duration must follow the approved
  research protocol / institutional requirements once those exist.
- **Who can access it:** admin-tier (President/Admin/IT) for read/create/
  collect/finalize/archive; President-only for withdrawal (§38.7).
- **Archival:** `EvaluationDataset.status=ARCHIVED` (BPA-2 §13) — read-only
  through the normal workflow, one-way from `COMPLETED`. Archiving is NOT
  deletion; archived data remains queryable by `biometric_analytics.py` and
  subject to the same withdrawal mechanism.
- **When it should be deleted:** not specified by this checkpoint — a
  research-protocol/institutional decision, not an application default.
  No automatic purge exists or is recommended here.
- **After thesis completion:** whether aggregated reports (rates, counts,
  ROC points — never raw trial rows) may remain after raw/pseudonymous
  `EvaluationTrial` rows are deleted is answerable in principle (aggregates
  contain no participant-linkable data once the underlying rows are gone)
  but is a protocol decision to make explicitly if/when raw-row deletion is
  ever requested — no such deletion mechanism exists as of BPA-5 beyond the
  withdrawal exclusion flag (§38.3) and the pre-existing `EvaluationDataset`
  cascade-delete (BPA-1 §11, unchanged).

### 38.6 Raw media / encrypted liveness proof retention

**Raw media — reconfirmed, unchanged:** no raw image, frame, or video is
ever persisted by `EvaluationDataset`/`EvaluationTrial` or the BPA-2/BPA-2.1
workflow (methodology doc §7/§13). `evaluation_trial_run`/
`evaluation_trial_liveness` process a browser-submitted frame transiently in
memory, exactly like production `verify_submit`. Browser-side memory,
request-body transit, and Django request/error logs are the only places a
frame transiently exists outside this codebase's control — the same
exposure production's own capture flow already carries, not something BPA-5
introduces or can eliminate from inside this workflow. `django-errors.log`
does not log request bodies (confirmed — no logging middleware in this
codebase logs raw POST bodies).

**Encrypted liveness-proof embedding — data-minimization fix (§38.9):**
`EvaluationTrial.liveness_proof_embedding` (an encrypted FaceNet embedding,
BPA-2.1) is now cleared (`= None`) inside `_complete_trial()`, i.e. the
moment ANY trial reaches its final, persisted outcome. **Why safe:** by
construction, both places that read this field —
the same-face-binding cosine comparison and the identity-comparison call —
run strictly BEFORE `_complete_trial()` is invoked (traced directly in
`evaluation_trial_run`'s POST handler); no analytics function in
`biometric_analytics.py` ever reads `liveness_proof_embedding` at all — only
the derived, non-reversible `similarity_score`/`liveness_score`/`pa_score`
numbers. **Why not removed from the model:** the field is still genuinely
needed WHILE a trial is `PENDING` (stage 1 → stage 2 binding, and the
replay-protection check that refuses to recapture liveness once it is set)
— only the value on a trial that has already reached a final decision is
cleared, never the field itself. `LivenessProofMinimizationTest`
(`tests_bpa5.py`) proves this for both a real `VERIFIED` completion and a
pre-comparison `DENIED` completion (the safe no-op case where the proof was
never set).

### 38.7 Pseudonym linkability — reconfirmed

Unchanged finding from BPA-2 (methodology doc §7): `target_beneficiary`/
`target_representative`, `created_by`/`human_review_by`,
`EvaluationDataset.created_by`, and (when set) `verification_attempt` remain
restricted relationships making a trial re-identifiable by an authorized
user — never anonymous, only pseudonymous. **BPA-5 adds one more such
relationship:** `EvaluationTrial.withdrawn_by`. This is necessary
referential context (who authorized the withdrawal, for audit) — removing
it would weaken auditability for no data-minimization benefit, since a
President-tier account is already a small, named set of users with broad
system access; it is not additional exposure beyond what `created_by`
already represents.

### 38.8 Access matrix (final)

| Action | President | Admin | IT | Staff |
|---|---|---|---|---|
| Create / view / list datasets | ✅ | ✅ | ✅ | ❌ |
| Start collection (`DRAFT→COLLECTING`) | ✅ | ✅ | ✅ | ❌ |
| Create / run / abort trials | ✅ | ✅ | ✅ | ❌ |
| Finalize dataset (`COLLECTING→COMPLETED`) | ✅ | ✅ | ✅ | ❌ |
| Archive dataset (`COMPLETED→ARCHIVED`) | ✅ | ✅ | ✅ | ❌ |
| View Biometric Performance Analytics | ✅ | ✅ | ✅ | ❌ |
| **Withdraw a trial (BPA-5)** | ✅ | ❌ | ❌ | ❌ |

**Rationale for the narrower withdrawal gate:** every other action in this
table is routine data-collection/reporting work appropriate for the
existing admin-tier precedent (methodology doc §14 — "technical research
tooling, not a payout/claims decision"). Withdrawal is different: it is a
one-way action that silently changes every future metric computed against
a dataset, with research-integrity and (if the withdrawn trial had already
been cited) manuscript-correction consequences. This codebase already
reserves several destructive/override-adjacent actions for
`is_president` specifically (self-override guards in the payout workflow,
`verification/views.py` — see the payout-override/self-verification checks
this table's precedent is drawn from) — withdrawal follows that existing
narrower-role precedent rather than inventing a new permission tier. No new
role/permission architecture was added.

### 38.9 Audit log privacy

**Logged** (all six pre-existing evaluation actions, plus the new one):
dataset/trial UUID (`target_id`), action, actor (`user`), timestamp
(automatic), and a `details` dict containing only IDs, decision strings,
ground-truth labels, and (for creation) the dataset name/protocol_version/
purpose — never raw image data, decrypted embeddings, or the encrypted
`liveness_proof_embedding` bytes themselves.
`ACTION_EVALUATION_TRIAL_WITHDRAWN`'s `details` deliberately omits the
free-text `withdrawal_reason` — that field is researcher-entered prose
which could (even if it never should) contain more context than an audit
log needs; the trial row itself (queryable by an authorized admin) is the
place to read the reason, not the audit trail.

**Excluded, by construction:** raw images/video (never processed anywhere
near `AuditLog.log()`), embeddings (decrypted or encrypted), and any
`Beneficiary`/`Representative` PII beyond what a `target_id`/pseudonymous
code already implies.

**If withdrawal/deletion occurs:** the audit rows for the ORIGINAL trial
actions (`_CREATED`/`_COMPLETED`/`_ABORTED`) and the new
`_WITHDRAWN` row are ALL retained — audit history is never pruned by a
withdrawal. This is intentional: the audit log's job is to prove what
happened and when, including the withdrawal itself; it is not part of the
research dataset and carries none of the exclusion-from-metrics logic
`_active_trials` implements.

### 38.10 Claim Recording Reliability — resolution

**Chosen path: Option B — manuscript reframing, not prospective
instrumentation.** BPA-3 §34.18 already established that historical
reconstruction is impossible (mutable `ClaimRecord.status`, no
append-only history). This checkpoint evaluated Option A (prospective
instrumentation — an immutable claim-state snapshot on `VerificationAttempt`
at decision time, or an append-only `ClaimRecord` status-history table) and
judged it **out of scope for an audit/readiness checkpoint**: the only safe
implementation touches the live claim-creation pipeline at all five
`ClaimRecord`-construction call sites (methodology doc §20) inside the
real payout path — genuine feature work with real regression risk to a
payout-critical system, not a "simple and safe" addition, and squarely the
kind of broad architecture change the checkpoint brief says to defer rather
than force into an audit checkpoint. **No code was added for this metric.**

**Recommended manuscript reframing:** Chapter 3 should not promise "Claim
Recording Reliability" as a measured historical percentage. Recommended
replacement language: *"Claim Recording Reliability was evaluated
qualitatively through architectural review of the claim-creation pathway
(a single authoritative `_create_claim_record` function, verified duplicate-
claim blocking, and admin-override auditing) rather than through a
retrospective statistical reliability rate, because FANS-C's current claim
history is not structured to reconstruct historical claim-state
transitions."* If a future non-BPA checkpoint later adds prospective
instrumentation, THIS is where a real forward-looking rate could be
introduced — never fabricated retroactively.

### 38.11 Active liveness — final wording lock

**Implementation, unchanged since BPA-2.1:** the controlled workflow's
active head-movement challenge is a researcher-attested checkbox
(`head_movement_completed`), not machine-verified through production's
interactive MediaPipe landmark-tracking overlay (methodology doc §25/§33).

**Research limitation:** any number derived from `liveness_pathway=
active_challenge` characterizes the PASSIVE anti-spoof/PAD gate (which IS
machine-measured, faithfully mirroring production) plus a self-reported
attestation of motion — it does NOT characterize production's machine-
verified active-liveness accuracy.

**Recommended manuscript wording:** *"Active head-movement liveness
completion in the controlled evaluation protocol was researcher-attested
rather than independently verified via the production system's automated
motion-tracking pipeline. Reported liveness/PAD figures characterize the
passive anti-spoof and presentation-attack-detection gate, which IS
machine-measured; they do not constitute a measurement of machine-verified
active-liveness accuracy."*

**Classification: study limitation, NOT a blocker.** The passive gate
(anti-spoof + PAD) is genuinely machine-measured and is the security-
relevant gate in production's own common path (methodology doc §24 — active
challenge barely affects production's own pass/fail decision either). If
the adviser's required liveness evaluation specifically demands a
machine-verified ACTIVE-challenge accuracy number, that would require new
instrumentation (automated landmark tracking in the controlled capture UI)
— out of scope for BPA-5, and flagged here as the one limitation that COULD
become a blocker depending on the adviser's specific requirement, not
something this checkpoint can resolve unilaterally.

### 38.12 Verification Time — final research definition (locked)

Unchanged from methodology doc §15/§27: **Controlled Verification Elapsed
Time** = server `evaluation_started_at` (stage-1 GET authorization) →
server `evaluated_at` (final decision persisted), both server timestamps,
never client-supplied. Excludes pre-runner-page time, camera warm-up,
capture-button timing, and post-response browser rendering. This is final
— no BPA-5 change was needed or made; this section exists so the runbook,
UI, and any future manuscript wording cite the identical definition.

### 38.13 Manual Review — final primary methodology (locked)

Unchanged from BPA-3/BPA-4: Manual Review is a real THIRD automated outcome,
never folded into VERIFIED or REJECTED. Primary reporting uses
TAR/FRR/GMRR/FAR/TNR/IMRR/Coverage/Conditional Automatic Accuracy
(§34.5/§37.4). `human_review_outcome` is an OPTIONAL secondary field for
later human adjudication and is never substituted into the primary
automated three-outcome metrics. No BPA-5 change was needed.

### 38.14 Accuracy methodology — recommended Chapter 3 definition

The draft manuscript's "Accuracy Rate" (conventional binary sense) does not
match FANS-C's three-outcome decision architecture. **Recommended
replacement, for Chapter 3:**

> *Primary automated performance is reported as **Conditional Automatic
> Accuracy** — the proportion of trials FANS-C decided automatically
> (VERIFIED or REJECTED, excluding Manual Review) that were decided
> correctly against ground truth — together with **Automatic Decision
> Coverage** (the proportion of all eligible trials FANS-C resolved
> automatically at all, without routing to Manual Review). These are
> reported alongside True Accept Rate (TAR), False Accept Rate (FAR), False
> Reject Rate (FRR), Genuine Manual Review Rate (GMRR), and Impostor Manual
> Review Rate (IMRR). If a single conventional "Accuracy" figure is retained
> for readability, it is explicitly defined as Conditional Automatic
> Accuracy and its Manual-Review-exclusion is stated in the same sentence —
> Chapter 3 never presents an unqualified "Accuracy Rate" without saying
> what happened to Manual Review trials.*

### 38.15 Participant sample size vs. trial sample size

The draft's "~100–200 senior citizens" (survey respondent sample size) is
**not** automatically the biometric evaluation N. Three distinct numbers
must never be conflated in Chapter 3/4:

1. **Survey respondent sample size** — whatever the ISO/IEC 25010
   questionnaire's own sampling plan specifies (ties to ~100–200 figure,
   subject to the adviser/statistician's actual survey design).
2. **Biometric participant count** — `get_participant_and_trial_counts()`'s
   `unique_participant_count` for the FINAL `RESEARCH_STUDY` dataset (§38.1)
   — the count of distinct consented individuals who took part in ANY
   controlled trial.
3. **Comparison/trial count** — total `EvaluationTrial` rows (genuine +
   impostor + attack), which is expected to exceed (2) due to repeated
   trials (§this doc's runbook §13) and multiple impostor pairings per
   participant (§this doc's runbook §11).

**No statistically "required" biometric sample size is invented here.**
Final sample-size determination for the biometric evaluation specifically
should be reviewed with the adviser/statistician before real collection
begins — this checkpoint provides the counting/reporting mechanism, not the
target number.

### 38.16 Controlled dataset finalization gate — audit result

**Unchanged, confirmed correct:** `evaluation_dataset_finalize` blocks only
on zero trials or any remaining `PENDING` trial (methodology doc §13) —
never on sample size or class balance, which correctly remains a
research-method decision rather than an application validation (per this
checkpoint's own instruction not to hard-block on statistical adequacy).

**Additive, non-blocking checks now available** (§38.17/§38.18, both
read-only): `get_dataset_quality_report()` (schema-level defense-in-depth
audit) and `get_study_readiness()` (technical readiness classification,
which DOES weigh a missing genuine/impostor class and non-Research-Study
purpose — but only as `READY_WITH_LIMITATIONS`, a warning, never a hard
block on finalization itself). The dataset-detail page surfaces both as an
informational banner; neither disables the Finalize button.

### 38.17 Data-quality audit (`get_dataset_quality_report`)

Read-only, defense-in-depth only — every check is already enforced by
`EvaluationTrial.clean()`/model constraints at save time; a non-zero count
means a row bypassed that validation (e.g. shell/fixture-created, or from
an older schema version), not that a new rule was invented. Operates on
`_active_trials` — a withdrawn row's pre-existing issues are not reported
(it is already excluded from analysis). Checks: missing participant code /
target identity code / identity ground truth; a completed trial with no
`system_decision`; a `matcher_base_decision` set with a null
`similarity_score` (an internal inconsistency — the matcher decision is
derived FROM the score); a completed trial missing either threshold
snapshot; a negative `verification_duration_ms`; a gate-blocked trial
(`failed_passive`/`failed_active`) whose `presentation_ground_truth` was
never set past `NOT_TESTED` (ambiguous — was this really an attack?); a
still-`PENDING` trial with no resolved target (can never be run). Returns
per-check counts, a total `issue_count`, and `has_issues`.

### 38.18 Study readiness (`get_study_readiness`)

Read-only, technical-only classification: `READY` / `READY_WITH_LIMITATIONS`
/ `NOT_READY`. `NOT_READY` when the dataset is not `COMPLETED`, has zero
trials, has any remaining `PENDING` trial, or the data-quality audit
(§38.17) found any issue. Otherwise `READY_WITH_LIMITATIONS` when
`purpose != RESEARCH_STUDY`, either ground-truth class (GENUINE/IMPOSTOR)
is missing from the primary bona-fide cohort, any trial is withdrawn
(reduces effective sample size), or the dataset is `ARCHIVED`; `READY`
only when none of those apply. **Explicitly not an ethics/IRB approval, a
statistical-power determination, or a claim that real participant data has
been collected** — the result's own `scope` field states this, and
`build_metric_report()` now includes both this and the quality report as
additive keys (`study_readiness`, `data_quality`) alongside every existing
BPA-3/BPA-3.1/BPA-3.2/BPA-4 metric, without altering any of them
(`tests_bpa3.QueryCountPerformanceTest` continues to pass unmodified,
confirming these additions do not introduce per-trial-scaling queries).

### 38.19 Technical research instrument (final naming)

**Name:** *Controlled Biometric Evaluation Protocol and System-Generated
Evaluation Trial Record.*

**Purpose:** measures MATCHER/SECURITY technical performance — FaceNet
similarity-score separability, the three-outcome decision pipeline's
accuracy/coverage, presentation-attack gate and end-to-end interception
behavior, and timing — computed from `EvaluationTrial` rows collected under
this protocol (BPA-2 through BPA-5).

**Distinct from the ISO/IEC 25010 questionnaire**, which measures
USER/SYSTEM QUALITY PERCEPTIONS (usability, reliability, etc., as reported
by respondents) — a subjective-perception instrument, not a technical
biometric-performance measurement. The two instruments answer different
research questions and their results must never be merged into one score or
presented as validating each other.

### 38.20 Manuscript alignment audit — itemized correction list

Every item below is a finding for a FUTURE Chapter 3/4 revision — none of
these edits were made to `MANUSCRIPT.pdf` by this checkpoint.

| # | Topic | Current draft (as understood) | Correction needed |
|---|---|---|---|
| A | FaceNet embedding dimension | Confirm draft states 512-D | Must read **512-dimensional** — verify against `face_utils.py`'s actual model output size before Chapter 3 is finalized. |
| B | Preprocessing terminology | May say "prewhitening" | The loaded checkpoint uses **`fixed_image_standardization`**, not classic per-image prewhitening — use the precise term. |
| C | SQLite deployment statement | Confirm matches actual deployment | Verify Chapter 3's database description matches the actual SQLite single-file deployment model (`docs/DATABASE-GUIDE.md`) — no change identified as needed here, but re-confirm before submission. |
| D | Decision terminology | May say binary accept/reject | Must read **Verified / Manual Review / Not Verified / Denied** (four-way `VerificationAttempt.DECISION_CHOICES`), with Manual Review and Denied kept distinct — Denied is a security-gate/liveness/PAD block, not the same as a similarity-score rejection. |
| E | Accuracy methodology | "Accuracy Rate," undefined re: Manual Review | Use §38.14's Conditional Automatic Accuracy + Coverage framing; never an unqualified Accuracy Rate. |
| F | FAR/FRR formulas | Confirm denominators | Must use `get_final_system_performance()`'s exact definitions (§34.5): FAR = impostor→VERIFIED / impostor_total (Manual Review excluded from both numerator and this denominator's "rejected" cell, but NOT from the total — see IMRR); FRR = genuine→(NOT_VERIFIED∪DENIED) / genuine_total, Manual Review never counted as a false reject. |
| G | Verification Time definition | May say "capture through result display" | Must be narrowed to **Controlled Verification Elapsed Time** (§38.12) or the manuscript must add real client-side instrumentation to support the broader claim — neither exists yet beyond the server-to-server definition. |
| H | Liveness evaluation limitation | Likely unstated | Must add §38.11's exact limitation wording — active-challenge completion is researcher-attested, not machine-verified. |
| I | Anti-spoof/PAD description | May say "trained CNN" | Must describe as a **heuristic** detector (`PresentationAttackDetector`, texture/frequency/motion heuristics), not a trained CNN classifier, unless the underlying implementation has since changed — verify against `verification/pad.py` before Chapter 3 submission. |
| J | Biometric evaluation research instrument | May be conflated with the ISO/IEC 25010 questionnaire | Must be described separately per §38.19. |
| K | Genuine/impostor controlled protocol | May be under-specified | Point to this runbook's §9/§10/§11 for the exact procedure and pairing-design language. |
| L | Respondent sample size vs. biometric trial count | ~100-200 figure may be presented as biometric N | Must be disambiguated per §38.15 — three distinct numbers, never conflated. |
| M | Threshold-selection evidence | May imply a single number was "found optimal" | Must state which candidate set was used (§34.14/§37.5's exploratory framing) and must NOT claim an "optimal threshold" was discovered — EER/ROC/AUC in this system are explicitly labeled as characterizing separability, never a recommendation (§34.16/§34.17). Must also state whether calibration and final-evaluation data were the same or different datasets (§38.2) — if the same, the reported numbers must be qualified as potentially optimistic. |
| N | Claim recording reliability | May promise a measured percentage | Must be reframed per §38.10's recommended language — not retrospectively computable, no prospective instrumentation built in this program to date. |
| O | Representative trials | May be unaddressed as a distinct claimant type | Must state representative trials are supported and timed identically to beneficiary trials (methodology doc §16/§28), reportable separately if sample size allows, per this runbook §Representative note below. |
| P | Data retention / withdrawal | Likely unaddressed | Must cite §38.3/§38.5 — pseudonymous retention, withdrawal-by-exclusion mechanism, no stated legal retention period absent institutional/legal establishment. |
| Q | Raw media statement | May overstate as "no data collected" | Must state precisely per §38.6 — no raw media persisted by this application, but browser/transit-level transient exposure during capture is inherent to any camera-based system and is not eliminated by this workflow. |
| R | Controlled evaluation dataset vs. live operational analytics | May be conflated | Must state these are architecturally separate (methodology doc §10/§20) — the controlled dataset never contributes to, and is never derived from, Executive/Operational/Security Analytics. |

**Representative-trial note (item O):** because representative sample sizes
are likely to be small relative to beneficiary trials, report representative
results DESCRIPTIVELY (counts, rates with wide implied uncertainty) rather
than making strong subgroup claims — `get_target_type_stratification()`
already carries this exact disclaimer (BPA-3 §15/BPA-4 §37.7); Chapter 4
should repeat it verbatim, not soften it.

### 38.21 Ethics / privacy wording flags

**Not a legal determination — flags for review only.** Inspect the draft
for wording that may contradict the actual system or its own privacy
statements described throughout this document:

- Any claim that **"no sensitive personal data"** is collected — biometric
  face data and derived embeddings ARE collected and used; the accurate
  statement is that raw images are not PERSISTED (§38.6), not that no
  sensitive data is processed.
- Any claim of **anonymity** — controlled evaluation data is **pseudonymous,
  re-identifiable by authorized users** (methodology doc §7, reconfirmed
  §38.7), never anonymous. Replace "anonymized" with "pseudonymized" or
  "de-identified with restricted re-identification capability" throughout.
- Any statement implying **institutional ethics/IRB approval** has been
  obtained, if it has not actually been obtained through the institution's
  own process — this application makes no such determination and this
  document does not certify one (§Preconditions in the runbook, §38.18's
  `scope` field).
- Any statement implying a **specific retention period** (permanent or
  time-bounded) without an approved protocol establishing it — use
  "retention duration follows the approved research protocol /
  institutional requirements" (§38.5) rather than a specific claimed
  duration.

### 38.22 Chapter 4 data map

| Research metric | System field/calculation | BPA section/page | Suggested table/figure |
|---|---|---|---|
| Conditional Automatic Accuracy | `get_final_system_performance()['conditional_automatic_accuracy']` | Biometric Performance tab, Final-System card | Single-value table alongside Coverage |
| Coverage | `get_final_system_performance()['coverage']` | same | same table |
| TAR / FAR / FRR | `get_final_system_performance()` | same | Three-Outcome Matrix table |
| GMRR / IMRR | `get_final_system_performance()` | same | same table |
| Three-Outcome Matrix | `_identity_performance()['matrix']` | Three-Outcome Identity Decision Matrix section | 2×3 confusion-style table |
| Matcher vs. Final delta | `get_matcher_to_final_delta()` | Matcher→Final transitions table | Transition table with escalation counts |
| Verification Time | `get_timing_analytics()` | Timing section | Descriptive stats table (mean/median/sample stddev) |
| Score Distribution | `get_score_distributions()` | Score distribution chart | Histogram figure, genuine vs. impostor overlay |
| Threshold Sensitivity | `run_threshold_sweep()` / `default_exploratory_threshold_candidates()` | Threshold Sensitivity table | Exploratory candidate-policy table (never "optimal") |
| ROC / AUC / EER | `compute_roc()`/`compute_auc()`/`compute_eer()` | ROC/AUC/EER section | ROC curve figure + AUC/EER value table |
| PAD Gate Performance | `get_presentation_attack_gate_metrics()` | Liveness/PAD Gate card | Gate pass/block rate table, overall + per attack type |
| Attack Outcomes (end-to-end) | `get_end_to_end_attack_outcome()` | End-to-End Attack Outcome card | Three-way outcome table, overall + per attack type |
| Beneficiary vs. Representative | `get_target_type_stratification()` | Subgroup section | Descriptive side-by-side table, "no significance test" caveat retained |
| Data Quality | `get_dataset_quality_report()` | Dataset detail readiness panel | Appendix table (issue counts, if any) |
| Study Readiness | `get_study_readiness()` | Dataset detail readiness panel | Appendix statement (READY/LIMITATIONS/NOT READY + reasons) |

No actual values are given anywhere in this table — it is a mapping for
future use once real data exists.

### 38.23 Browser verification — not required for this checkpoint

BPA-5 changed one Analytics-adjacent page (`evaluation_dataset_detail.html`
— new purpose badge, readiness banner, withdrawn-count tile) and one
workflow page (`evaluation_trial_detail.html` — withdrawn banner, withdrawal
form) plus the dataset-creation form (purpose selector). These are small,
server-rendered additions using the same Bootstrap card/badge patterns
already used throughout this workflow — no new JavaScript, no new chart, no
new client-side logic. Given the primarily backend/schema/documentation
scope of this checkpoint and the low visual/interaction surface of the
additions, live Playwright browser verification was judged unnecessary for
this checkpoint; a future checkpoint that adds real interactive UI (e.g. an
in-app data-export flow) should verify visually. If the project owner wants
visual confirmation of these specific additions regardless, they are
reachable at `/verification/evaluation/datasets/<uuid>/` (readiness banner)
and `/verification/evaluation/trials/<uuid>/` (withdrawal control, President
login required) using existing QA fixtures, cleaned up afterward per this
program's established convention.

**Addendum (BPA-5.1):** this deferral was closed, not overridden — BPA-5.1
performed the live Playwright verification this section describes as
optional, against an isolated QA sqlite database (never `db.sqlite3`), after
BPA-5.1 itself changed the same pages further (amended-dataset banner,
stale-pending-proof warning, withdrawn-trial action suppression). See §39.6.

### 38.24 Documentation

- `docs/BIOMETRIC-EVALUATION-METHODOLOGY.md` (this file) — definitions,
  rationale, and the itemized findings above.
- `docs/BIOMETRIC-EVALUATION-RUNBOOK.md` (new) — step-by-step operational
  procedure for actually running a session, kept synchronized with this
  file's definitions (§38.11/§38.12/§38.13 are restated, not duplicated
  with different wording, in the runbook's corresponding sections).

### 38.25 What BPA-5 does NOT do

- Does not change any BPA-3/BPA-3.1/BPA-3.2 formula, cohort definition, or
  rate convention.
- Does not add prospective Claim Recording Reliability instrumentation
  (§38.10) — recommends manuscript reframing instead, a deliberate scope
  decision given the risk of touching the live claim-creation pipeline.
- Does not add an in-UI dataset-purpose editor, export feature, or new
  permission/role architecture beyond the existing `is_president` precedent.
- Does not start real participant data collection, generate synthetic
  study-looking rows, or fabricate any result.
- Does not modify `MANUSCRIPT.pdf`.
- Does not begin Phase B/C/D or build an installer/EXE.

## 39. BPA-5.1 — Final Readiness Closure

Narrow closure checkpoint immediately after BPA-5, resolving four
outstanding questions before the BPA program (data-collection engineering)
could be declared complete: finalized-dataset withdrawal integrity,
liveness-proof retention across every trial exit path, the live-browser
verification BPA-5 deferred (§38.23), and exact BPA-5 test-count
reconciliation. Adds no new biometric metric, does not touch
`MANUSCRIPT.pdf`, does not collect real participant data.

### 39.1 Finalized-dataset withdrawal — chosen design

**Before BPA-5.1:** `evaluation_trial_withdraw` applied `withdrawn=True` to
any trial regardless of `dataset.status`. A withdrawal on a `COMPLETED`
dataset left `status`/`completed_at`/`is_final_study_result` all unchanged
and gave no visible indication anywhere that a "finalized" dataset's figures
had since changed — reconstructing that fact required manually comparing
`EvaluationTrial.withdrawn_at` against `EvaluationDataset.completed_at`,
which no UI surface did.

**Chosen design (restrained Direction A):** withdrawal remains ALLOWED at
any dataset status — a participant's right to withdraw is never blocked to
protect a statistic. What changes is disclosure: `EvaluationDataset` gains
one new nullable field, `amended_after_finalization_at` (migration 0029,
alongside a `completed_at` help-text clarification — no other schema
change). `evaluation_trial_withdraw` sets it to the withdrawal timestamp
whenever `dataset.completed_at is not None` (true for `COMPLETED` and, since
there is no unarchive path, `ARCHIVED` too). `completed_at` — the ORIGINAL
finalization date — is never rewritten. `status` never reverts to
`COLLECTING`; Direction B (force re-finalization) was rejected as more
disruptive than necessary: it would make an already-cited finalized result
temporarily un-finalized and implicitly reopen the dataset's identity as
"still collecting," which is a bigger workflow change than the actual
problem (missing disclosure) requires.

`has_post_finalization_amendment` (a property, not a stored flag) is `True`
whenever `amended_after_finalization_at is not None`. `is_finalized` and
`is_final_study_result` are UNCHANGED by amendment — an amended finalized
research-study dataset is still a genuine finalized result, just one that
must be visibly disclosed as amended, never presented as untouched.

### 39.2 Disclosure surfaces

- `evaluation_dataset_detail.html`: status badge appends "— Amended" and
  turns warning-colored; a dedicated alert states
  "FINALIZED DATASET — AMENDED AFTER PARTICIPANT WITHDRAWAL", the original
  finalization date, and the most recent amendment date.
- `analytics_biometric.html`: the existing `finalized`/`archived` banners
  gain amended variants with the same wording, replacing (never
  supplementing without warning) the plain "FINALIZED DATASET" banner.
- `evaluation_trial_detail.html`: a withdrawn trial's notice additionally
  states when its dataset was already finalized at the time of withdrawal.

### 39.3 Audit reconstructability

`ACTION_EVALUATION_TRIAL_WITHDRAWN`'s `details` now also carries
`dataset_status_at_withdrawal`, `dataset_was_finalized_at_withdrawal`, and
`dataset_completed_at` (ISO timestamp). A new action,
`ACTION_EVALUATION_DATASET_AMENDED`, is logged against the dataset itself
whenever an amendment occurs, carrying `original_completed_at` and the
withdrawn trial's id — so the original finalization date, and the fact/
timing of every post-finalization withdrawal, are reconstructable from
`AuditLog` alone, independent of the live dataset row.

### 39.4 Liveness-proof retention — the gap BPA-5 did not close

BPA-5 verified only that `_complete_trial()` clears
`liveness_proof_embedding` — true, but that only covers trials that reach a
final system decision. Every `_abort_trial()` call site left the proof on
the row indefinitely, including the ones that fire AFTER stage 1
(`evaluation_trial_liveness`) already stored a proof: a stage-2 final-frame
capture/comparison failure (no image, malformed image, face-detection
failure, unresolved target, unexpected processing error) and the explicit
operator "Abort Trial" action on a still-`PENDING` trial. Withdrawal of a
`PENDING` trial that had already captured a proof had the same gap.

**Fix:** `_abort_trial()` now also sets `liveness_proof_embedding = None`
(mirroring `_complete_trial()`'s identical line, same rationale — `ABORTED`
is a terminal state exactly like `COMPLETED`; neither can legitimately
resume, since both `evaluation_trial_run` and `evaluation_trial_liveness`
require `trial_status == PENDING`). `evaluation_trial_withdraw` also clears
it directly. Both `evaluation_trial_run` and `evaluation_trial_liveness` now
refuse a `withdrawn` trial outright (previously only `trial_status` was
checked), closing the matching UI gap: `evaluation_trial_detail.html`
no longer offers "Run Controlled Trial"/"Capture Final Frame" for a
withdrawn `PENDING` trial.

### 39.5 Abandoned PENDING trials — documented, not solved

A trial that completes stage 1 (proof captured) and is then abandoned
(browser closed) stays `PENDING` with an encrypted proof indefinitely — no
automatic expiry exists, and building a background/scheduled cleanup job is
explicitly out of scope for this checkpoint (no such architecture exists
elsewhere in this project to extend cleanly). The smallest safe policy
implemented instead: `get_stale_pending_trials_with_proof()`
(`verification/biometric_analytics.py`) flags a `PENDING`, non-withdrawn
trial whose `liveness_captured_at` is more than `STALE_PENDING_PROOF_HOURS`
(24h) old, and `evaluation_dataset_detail.html` surfaces a passive dashboard
warning naming the count and recommending an explicit operator abort (which
clears the proof via §39.4's fix). This is a warning, not a gate — it does
not block finalization or any other workflow action. **Known limitation
carried forward:** a trial abandoned for less than 24h, or on a dataset an
operator never revisits, still holds its proof with no automatic remedy.

### 39.6 Live-browser verification (closes §38.23's deferral)

Performed against an isolated QA sqlite database (`fans_c_qa_settings.py`,
scratch-only, deleted afterward — never `db.sqlite3`), Chromium via
Playwright, at 1920/1440/1366/1200/900/768px (100% zoom). Verified: dataset
create form; a `COLLECTING` dataset (readiness banner, purpose badge, trial
counts, stale-pending-proof warning); an untouched `COMPLETED` dataset
(plain FINALIZED banner, both on the dataset page and the Biometric
Performance Analytics page); a `COMPLETED` dataset amended by a REAL
withdrawal performed through the UI (not a DB shortcut) — both surfaces
show the amended banner; a trial detail page as President (withdrawal
control visible) and the SAME trial as Admin (control absent entirely, not
merely disabled); a withdrawn `PENDING` trial (withdrawn state obvious, no
Run/Capture action offered); a `QA_SYNTHETIC`-purpose dataset (purpose badge
unambiguous). Zero horizontal overflow (`scrollWidth - clientWidth == 0`) at
every width for every state; zero browser console errors. QA data: 2 users,
4 datasets, 7 trials created; all deleted by removing the isolated sqlite
file wholesale (0 remaining) — `db.sqlite3` was never opened by this pass.

### 39.7 Test-count reconciliation (BPA-5's report)

BPA-5's own report stated "31/31 passing" for `tests_bpa5.py` (accurate —
the file has exactly 31 `def test_` methods) but then stated the full suite
was "1153" (from "1123 baseline + 30 new"), which contradicts its own
"31/31" line. Reconciliation: static per-file counts
(`accounts/tests.py` 171 + `beneficiaries/tests.py` 111 + `logs/tests.py` 47
+ `fans/tests.py` 167 + `verification/tests.py` 424 +
`tests_bpa1..bpa4.py` 25+36+30+48+23+9+32 = 203 + `tests_bpa5.py` 31) sum to
exactly **1154**, and `manage.py test` (no changes yet applied) confirms:
`Ran 1154 tests ... OK`. `1154 - 31 = 1123` — exactly the baseline BPA-5's
own report cited. **Conclusion: no test was removed, renamed, or silently
replaced; "1153" was a plain arithmetic slip in the prior report (it should
have read 1123 + 31 = 1154, consistent with its own "31/31 passing" line).**
This checkpoint's own 23 new tests (`tests_bpa5_1.py`) bring the total to
1177 — see the final report for the executed confirmation.

### 39.8 What BPA-5.1 does NOT do

- Does not add a background/scheduled cleanup job for abandoned pending
  trials (§39.5) — documented limitation, not solved.
- Does not add any new biometric metric or change any BPA-3/BPA-3.1/BPA-3.2
  formula.
- Does not revert a finalized dataset's `status` to `COLLECTING` on
  withdrawal (Direction B, rejected — see §39.1).
- Does not collect real participant data, modify `MANUSCRIPT.pdf`, or begin
  Phase B/C/D or an installer/EXE build.
