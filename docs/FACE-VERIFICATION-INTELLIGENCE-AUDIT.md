# Face Verification Intelligence — v2.2.0 Phase 9 Audit

> **Note:** written during development under the temporary "v2.2.0"
> milestone naming scheme; the capabilities described shipped under the
> v2.1.x release line (final release: v2.1.17), not as a separate v2.2.0
> installer. See the version-numbering note in
> [CHANGELOG.md](../CHANGELOG.md).

Audit-only pass. Every capability below already existed before this release; nothing was rebuilt. Where a genuine gap existed, it's called out explicitly rather than silently assumed away.

## Matching method

FaceNet embeddings (512-d, `keras-facenet`, checkpoint `20180402-114759` — runtime-verified, not the 128-d checkpoints the package also ships), compared via cosine similarity (`verification/face_utils.py compare_with_all_embeddings`). Multi-template: a beneficiary's primary `FaceEmbedding` plus any `AdditionalFaceEmbedding` rows (added via the re-enrollment workflow) are all compared; the best-scoring template wins the match. `VerificationAttempt.matched_template` records which one won, per attempt — analyzed by `verification/template_analytics.py` (per-template win stats, advisory re-enrollment suggestions).

## Confidence threshold

Three-zone decision band (`SystemConfig.get_threshold()` / `get_auto_verify_threshold()`):
- Below the lower threshold → denied.
- Between the lower threshold and the auto-verify threshold → `manual_review` (a human must approve before release).
- At or above the auto-verify threshold → auto-verified.

Both thresholds are admin-configurable through the audited `verify_config` workflow, not hardcoded. The verification result page shows a plain-language confidence label next to the raw score (added v2.2.0 Phase 4: "High confidence match" / "Moderate confidence — review band" / "Low confidence").

## Liveness / anti-spoofing

`verification/liveness.py` + `verification/pad.py` (`PresentationAttackDetector`). Risk-based challenge (not forced on every verification — reduces friction for elderly beneficiaries), server-side anti-spoof texture scoring, head-movement challenge, and presentation-attack heuristics (`pa_score`/`pa_flags` on `LivenessTransaction`) that catch static-photo/screen-replay attempts. A single-use liveness token (`LivenessTransaction`) binds the liveness-verified frame's embedding to the final submitted frame, preventing face-switching between the two steps.

## Image quality detection

`check_face_quality()` (`verification/face_utils.py`) — Laplacian-variance blur detection, brightness/exposure checks, glare detection. Gates both registration (lenient, staff can retake) and verification (stricter, rejects severely blurry frames outright before they reach the matcher). Composite `quality_score` is stored on every `VerificationAttempt`. **No separate "lighting detection" beyond the existing brightness/glare/overexposure checks was added** — the audit found the existing checks already cover this; a dedicated lighting-only detector would be duplicating logic that already exists under a different name.

## Duplicate face detection

`check_duplicate_face()` — runs at registration; a match routes the new registration into `duplicate_review_required` (admin review, never a silent auto-reject) via `Beneficiary.duplicate_match_beneficiary`/`duplicate_match_score`. Representative faces get the equivalent `SharedRepresentativeReview` workflow. Both are also now Fraud Signals-adjacent: a duplicate-face event fires a `FRAUD_ALERT` notification (v2.1.18) and is counted in the Security analytics tab.

## Suspicious mismatch detection

`decision = manual_review` plus the lookalike-detection band (a second beneficiary's stored embedding also scores near the submitted frame) already escalates to manual review rather than silently accepting the higher-scoring match. Submit-frame integrity checks (`ACTION_VERIFY_NO_FACE`, `ACTION_VERIFY_MULTIPLE_FACES`, `ACTION_VERIFY_SUBJECT_CHANGED`) deny outright rather than escalating — these are structural frame-validity failures (no face / multiple faces / a different face than the liveness step), not identity ambiguity, so a hard deny is correct there, not a review-band case.

## Age-based rejection — explicitly NOT implemented, by design

No image-based age-estimation model exists anywhere in this codebase (re-confirmed by a full-repo grep at the end of this Phase 9 audit — zero matches for age-estimation code). This is intentional: such models are trained/validated mostly on younger demographics and are measurably less reliable on elderly faces from low-quality webcam captures — exactly this system's population — so building one would risk exactly the "automatic rejection of a legitimate senior citizen" failure mode the requirements explicitly forbid.

Instead, `verification/template_analytics.py appearance_drift_flag_for()` (added v2.1.18) serves the same real-world purpose — flagging a beneficiary whose stored template may no longer represent them well — using a transparent, explainable signal (declining match-confidence trend over time) instead of a black-box age guess. It is advisory only: it can suggest re-enrollment on the Update Face Data page, and never blocks, denies, or auto-triggers anything.

**If a hypothetical registered-75/detected-25-35 mismatch example is required as a concrete workflow**: today, a similarity score that low would already route to `manual_review` (never auto-deny) via the normal three-zone threshold band — the system doesn't need to know *why* the score is low (age, lighting, camera angle, a different person) to apply the correct human-in-the-loop response. Adding an explicit "estimated age" data point to that review screen was considered and **not implemented** in this pass, since it would require the same rejected age-estimation model to produce — the review-routing behavior it would inform already exists and already defaults to the safe, review-required outcome without it.
