# FANS-C Controlled Biometric Evaluation — Operational Runbook (BPA-5)

Companion to `docs/BIOMETRIC-EVALUATION-METHODOLOGY.md` (the "methodology
doc" below), which explains WHY every field/formula/decision exists. This
document is the step-by-step procedure for actually running a controlled
evaluation session using the workflow BPA-2/BPA-2.1/BPA-5 implemented. It
contains **no real values, no real participant data, and no results** — it
is a procedure document only, written before any real controlled evaluation
has been conducted.

**Do not use this runbook to begin real participant data collection until
the BPA-5 checkpoint report's readiness classification has been reviewed by
the adviser/project owner.** System readiness is not the same as ethics/IRB
clearance, and neither is established by this document.

---

## 0. Preconditions

Before running any real session:

1. Institutional/ethics review, if required by the program, has been
   obtained separately — this system makes no such determination (see
   methodology doc, "Ethics / privacy wording").
2. Participant consent has been obtained through whatever paper/verbal
   process the approved protocol specifies — FANS-C has no in-app consent
   capture; this runbook assumes consent is confirmed before Step 1.
3. A `protocol_version` string has been agreed for this session (e.g. an
   identifier for the specific consent form / procedure version in effect)
   — this is what `EvaluationDataset.protocol_version` records.
4. The operator has decided this session's **purpose**
   (`EvaluationDataset.purpose` — Research Study / Pilot-Calibration /
   QA-Synthetic) BEFORE creating the dataset (§1 below) — see methodology
   doc "Dataset purpose" for why this cannot be changed casually afterward
   and must never default to "Research Study" by habit.

---

## 1. Create the dataset

`Controlled Biometric Evaluation → New Dataset`
(`verification:evaluation_dataset_create`, admin-tier).

- **Name**: a human-readable session/study title.
- **Protocol / Version Identifier**: the agreed protocol_version (§0.3).
- **Purpose** (required): Research Study (Final Evaluation) / Pilot /
  Calibration / QA-Synthetic — see methodology doc "Calibration vs. final
  evaluation" before choosing Research Study for a dataset that will also be
  used to pick a threshold.
- Dataset is created `DRAFT`. It cannot accept trials yet.

## 2. Participant study-code assignment

FANS-C does not generate participant codes for you — the research protocol
must define its own pseudonymous code scheme BEFORE the session (e.g.
`P-001`, `P-002`, sequential per session) and the operator enters it
verbatim as `participant_code` at trial setup (§7 below). The code must
never be a name, Senior Citizen ID, or beneficiary_id — those remain
reachable only through the restricted `target_beneficiary`/
`target_representative` relationship, never through this pseudonymous field
(methodology doc §7).

## 3. Start collection

`Start Collection` button on the dataset detail page
(`verification:evaluation_dataset_start`). `DRAFT → COLLECTING`. Trials can
now be created.

## 4. Enrollment requirement

A genuine or impostor trial's TARGET must already be enrolled in FANS-C
(an ACTIVE `Beneficiary` with a stored face template, or a `Representative`
with `face_embedding`) before the trial is created — the trial-setup search
box (§7) only finds already-enrolled records; this workflow never enrolls
someone as a side effect. If the intended target has no enrolled face, the
representative-target trial will abort at run time rather than fabricate a
result (methodology doc §28).

## 5. Target identity selection

At trial setup (`verification:evaluation_trial_setup`), choose
**Beneficiary** or **Authorized Representative** as `target_type`, then
search by name / beneficiary ID / Senior Citizen ID (beneficiary) or by
name / associated beneficiary (representative). Selecting a result sets the
restricted `target_beneficiary`/`target_representative` FK — this is what
lets the trial runner retrieve a real stored template later (§13).

## 6. Ground-truth assignment (identity)

On the SAME trial-setup page, before any capture, set
**Identity Ground Truth**: Genuine (the enrolled presenter) or Impostor (a
different, consented participant presenting against this target). This is
locked in at trial creation — nothing in the runner can change it once set
(methodology doc §3).

## 7. Presentation-ground-truth assignment

On the same page, set **Presentation Ground Truth**: Bona Fide / Print-Photo
Attack / Screen-Replay Attack / Other Attack / Not Tested. Leave Bona Fide
for an ordinary genuine/impostor trial; use an attack label only when this
specific trial is a staged presentation-attack trial (§13/§14 below). Do not
leave this at "Not Tested" out of habit — it is excluded from the primary
bona-fide identity cohort (methodology doc §5).

## 8. Camera/device/environment setup

Before capture, fix for the session (and record outside the app, e.g. in
the dataset `description`, if the protocol requires it):

- **Device/camera**: the same device model each session where feasible (a
  laptop webcam vs. an external USB camera can shift score distributions).
- **Approximate distance**: consistent seating/standing distance from the
  camera, matching realistic barangay-hall deployment distance.
- **Lighting**: consistent, adequate frontal lighting — avoid strong
  backlighting.
- **Pose**: frontal, matching the production capture UI's guidance.
- **Background**: not a strict requirement, but avoid another face visible
  in-frame (this workflow does not implement multi-face rejection as a
  separate labeled reason — methodology doc §29).
- **Eyeglasses**: use the participant's normal daily-wear glasses if any —
  do not artificially remove them if they are worn during real claims.

Do not over-engineer a laboratory setup — the purpose is reproducing the
actual barangay deployment context, not a forensic-lab standard.

## 9. Genuine trial procedure

1. Presenter = the enrolled identity's own consented participant.
2. Target = their own beneficiary/representative enrollment (§5).
3. Identity Ground Truth = Genuine (§6).
4. Presentation Ground Truth = Bona Fide, unless this trial is deliberately
   also a staged-attack trial for the SAME identity (state this explicitly
   in `notes` if so — avoid an undocumented multi-purpose trial).
5. Run the trial (§13). Record: participant code, target code, target type,
   similarity score, matcher decision, final FANS-C decision, liveness/PAD
   outputs, timing — all captured automatically by the runner; nothing is
   hand-transcribed.

## 10. Impostor trial procedure

An impostor trial means a **consented study participant** intentionally
presents against another enrolled identity for controlled evaluation — it
is never a fraud accusation, an unauthorized real payout attempt, or real
stipend misuse. This workflow is architecturally incapable of creating a
`ClaimRecord` or payout (methodology doc §20) — an impostor "VERIFIED"
result is recorded purely as `EvaluationTrial` data.

1. Presenter = a different consented participant than the target's own
   enrolled identity.
2. Target = the OTHER identity's beneficiary/representative enrollment.
3. Identity Ground Truth = Impostor.
4. Presentation Ground Truth = Bona Fide (the impostor is presenting their
   own real, live face — see methodology doc §35.2 on why BONA_FIDE and
   GENUINE/IMPOSTOR are independent).
5. Run the trial exactly as a genuine trial (§13).

## 11. Impostor pairing design

Do not test each participant against only one arbitrarily chosen target if
a broader design is feasible. Recommended: define a prespecified pairing
schedule BEFORE the session (e.g. a round-robin or randomized subset of
other enrolled participants each person is tested against) rather than
picking targets ad hoc during collection — this avoids an unintentional
bias toward "easy" or "hard" pairings. Distinguish, in the final report:

- **Number of participants** — `get_participant_and_trial_counts()`'s
  `unique_participant_count` (distinct `participant_code`).
- **Number of impostor comparisons** — `impostor_trial_count` (COMPLETED
  trials with `identity_ground_truth=IMPOSTOR`).

Repeated impostor trials from the same participant against different
targets are correlated observations, not independent samples — do not
present a raw impostor-trial count as if it were an independent-participant
count in any statistical claim (methodology doc, "Participant vs. trial
counts").

## 12. Presentation-attack trial procedure

1. Target = an enrolled identity (genuine's own, typically — an attack
   trial's IDENTITY ground truth is usually Genuine, since the participant
   is attacking their own enrollment with a photo/screen, but this is a
   protocol decision, not a system requirement).
2. Presentation Ground Truth = Print-Photo / Screen-Replay / Other Attack —
   the KNOWN attack type, set BEFORE capture, never derived from what the
   PAD score turns out to be (methodology doc §18/§35.2).
3. Run the trial. Record, per trial: the known attack type (already set),
   the gate outcome (`liveness_pathway` — recorded automatically), and the
   final FANS-C outcome (`system_decision`).
4. Do not require an attack type this project cannot safely and legally
   reproduce (e.g. a 3-D mask). Print-photo and screen-replay are the two
   implemented, safely reproducible types.
5. **Two different metrics will result from this data — never conflate
   them:** `get_presentation_attack_gate_metrics()` (did the liveness/PAD
   GATE itself pass or block) vs. `get_end_to_end_attack_outcome()` (what
   FANS-C's FINAL decision turned out to be, which can differ from the gate
   outcome — methodology doc §35.3).

## 13. Repeated-trial procedure

Multiple genuine attempts per participant are useful to characterize
natural capture variation (positioning, expression, lighting, eyewear,
day/session). Record each as its own trial (its own `participant_code`
reused across multiple trial rows is expected and fine — the model does
not require uniqueness). **Do not** treat every repeated attempt as an
independent participant in statistical interpretation — the Analytics UI
and `get_participant_and_trial_counts()` already separate
`unique_participant_count` from trial counts; keep that separation in any
manuscript table (methodology doc §11 / this doc §11).

## 14. Liveness pathway recording

At the runner (`verification:evaluation_trial_run`), stage 1 captures a
neutral/liveness frame. The operator decides, per trial, whether to attempt
the shown head-turn challenge (checkbox: "I performed the head-turn
challenge") — this is **researcher-attested**, not machine-verified (see
§15 below and methodology doc §25/§33). The system records which pathway
actually ran (`liveness_pathway`) automatically; nothing here is manually
transcribed.

## 15. Active-liveness limitation (read before citing "liveness accuracy")

**In this controlled workflow, active head-movement completion is
researcher-attested, not independently verified through the full production
MediaPipe interaction.** Do not present any number derived from
`liveness_pathway=active_challenge`/`head_movement_completed` as
"Machine-Verified Active Liveness Accuracy." See the methodology doc,
"Active liveness limitation," for the full resolution and recommended
manuscript wording. This is a stated LIMITATION of the controlled protocol,
not a blocker to running it — the passive anti-spoof/PAD gate IS
machine-measured and faithfully mirrors production (methodology doc §25).

## 16. Timing definition (read before citing "Verification Time")

**Controlled Verification Elapsed Time** = server timestamp at runner-page
GET authorization → server timestamp at final decision persistence. It
EXCLUDES pre-runner-page time (dataset browsing, trial setup, deciding to
click "Run"), camera warm-up, the researcher's own capture-button timing,
and browser rendering time after the server responds. It is NOT
"capture-to-screen-render latency." See methodology doc §15/§27 for the
full definition, and §"Verification Time" in the BPA-5 section for the
locked manuscript wording.

## 17. Incomplete / aborted trials

A trial that never reaches a decision (no face detected, malformed image,
embedding failure, unresolved target) becomes `ABORTED` automatically — it
is NEVER counted as a false reject, an attack block, or a timing
observation (methodology doc §16). If a participant simply withdraws mid-
session before their trial ever ran, use **Abort Trial** on the trial-detail
page (only available while `PENDING`) with a plain-language reason in the
confirmation prompt.

If the participant withdraws AFTER a result was already recorded (this
trial reached `COMPLETED`), use **Withdraw Trial** instead (§20 below) — a
completed trial cannot be "aborted," only withdrawn.

**Abandoned mid-capture trials (BPA-5.1):** if a browser closes after
stage-1 liveness capture but before the final frame is submitted, the trial
stays `PENDING` holding an encrypted liveness-proof embedding — there is no
automatic expiry. The dataset detail page shows a passive warning once such
a trial is more than 24h old ("N stale PENDING trial(s) still holding a
captured liveness proof"); if it will not be resumed, **Abort Trial**
explicitly to clear the proof rather than leaving it stored indefinitely.

## 18. Manual-review treatment

`MANUAL_REVIEW` is a real, intentional THIRD outcome of the automated
system — never folded into "rejected" or "verified" in any report drawn
from this data. See methodology doc §17/"Manual Review" for the locked
primary methodology (GMRR/IMRR/Coverage/Conditional Automatic Accuracy).
`human_review_outcome`/`human_review_notes` on the trial detail page are an
OPTIONAL secondary field for what a human reviewer later decided — never
required, and never substituted into the primary automated metrics.

## 19. Dataset finalization

Once every trial in the dataset is either `COMPLETED` or `ABORTED` (no
`PENDING` rows remain) and at least one trial exists, click **Finalize
Dataset** (`COLLECTING → COMPLETED`). This is a workflow-completeness gate
only — it does NOT check statistical sample size or class balance (that is
a research/adviser decision, not an application validation — see
methodology doc §20/BPA-5 "Finalization / data quality"). Before finalizing,
consider reviewing the dataset detail page's **Technical Readiness** panel
(`get_study_readiness()`) and the underlying data-quality audit
(`get_dataset_quality_report()`) — both are read-only and informational,
never blocking.

## 20. Withdrawal / deletion

If a participant withdraws consent — before OR after their trial(s) ran —
a **President**-tier user withdraws each affected trial from the trial
detail page (**Withdraw Trial**, requires a reason). This:

- Sets `withdrawn=True` (never deletes the row — retained for audit).
- Immediately excludes the trial from every future
  `biometric_analytics.py` calculation for this dataset.
- Never touches operational `Beneficiary`/`Representative`/
  `FaceEmbedding`/`VerificationAttempt`/`ClaimRecord` records — those are
  untouched regardless of withdrawal.
- Is audit-logged (`AuditLog.ACTION_EVALUATION_TRIAL_WITHDRAWN`).

If a participant withdraws before their trial ever ran (still `PENDING`),
**Abort Trial** (§17) is the correct action instead — nothing to withdraw
from analysis yet. If that `PENDING` trial had already captured a stage-1
liveness proof, both Abort and Withdraw clear the encrypted embedding
(BPA-5.1) — neither leaves it on the row.

**Withdrawal after finalization (BPA-5.1):** withdrawing a trial from an
already-`COMPLETED` (or `ARCHIVED`) dataset is still allowed — it is NEVER
blocked to protect a statistic — but the dataset is then marked
`amended_after_finalization_at` and both the dataset detail page and the
Biometric Performance Analytics page switch to an
"AMENDED AFTER PARTICIPANT WITHDRAWAL" banner instead of the plain
FINALIZED banner. The dataset's original `completed_at` is never rewritten.
Re-check the Technical Readiness panel and any cited figures after such a
withdrawal — they now reflect the amended, not the original, dataset.

See methodology doc §39 ("BPA-5.1 — Final Readiness Closure") for the full
design rationale, and "Withdrawal" and "Retention" in the earlier sections
for the base BPA-5 policy this extends.

---

## Post-session checklist

- [ ] Dataset finalized (`COMPLETED`) once collection is truly done.
- [ ] Technical Readiness panel reviewed (READY / READY WITH LIMITATIONS /
      NOT READY) and any limitation understood before citing results.
- [ ] Data-quality audit shows zero issues, or every issue is understood
      and explained.
- [ ] Any withdrawal requests already actioned.
- [ ] Dataset `purpose` correctly reflects what this dataset actually is —
      corrected NOW if a QA/pilot session was accidentally left as
      "Research Study" (the dataset can still be edited via shell before
      any manuscript number is drawn from it; there is no in-UI purpose
      editor as of BPA-5 — a deliberate scope cut, since changing a
      dataset's purpose after data exists is a research-integrity decision
      that should be deliberate, not a routine UI action).

**SYSTEM READY ≠ STUDY COMPLETED.** Completing this checklist confirms the
SYSTEM behaved correctly — it is not itself a claim that a statistically
adequate, ethically-approved real study has been completed.
