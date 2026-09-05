# v2.2.0 Follow-Up Critical Fix Pass — Issues 31–36

**Date:** 2026-09-01
**Branch:** 4.0-Final-v2.1.16-hardening
**Scope:** Biometric identity-binding integrity issues found in a second round of manual testing, on top of the completed 20-phase post-UAT pass.
**Full test suite:** 880 tests, 880 passed, 0 failures, 0 errors (`python manage.py test`) — up from 840 before this pass (40 new regression tests added)
**System checks:** `python manage.py check` — clean. `python manage.py makemigrations --check --dry-run` — no pending changes (no schema changes were needed this pass).

---

## Issue 32 — Verification appears to bind to the wrong beneficiary (CRITICAL)

**Investigated first per the stated priority — this was the real root cause behind the reported "wrong Toni Anek Fowler" verification.**

**Root cause:** `verify_check_liveness` and `verify_submit` both read the beneficiary to verify against exclusively from `request.session['verification_session']['beneficiary_id']` — never from anything the client submits. That part is correctly architected. The gap: **`request.session` is keyed on the login cookie, which is shared across every browser tab.** `verify_start(pk)` unconditionally *overwrites* `verification_session` with a new `beneficiary_id`. If an operator starts verification for beneficiary A, then (in another tab, or after navigating away and back) starts verification for beneficiary B, the shared session now points at B. A still-open tab A — visually still showing "Verifying Toni Anek Fowler BEN-2026-00001" — would submit its capture, and the server would silently evaluate (and could release a claim for) whichever beneficiary currently occupies the session: B, not A. This is not a "global best-match" bug (there is no such search — `compare_with_all_embeddings()` only ever compares against the one beneficiary object passed to it); it is a session-collision bug, exactly matching the two-duplicate-registrations test setup that made it easy to trigger (same name, two records, natural to flip between their pages).

**Fix:** `verify_start()` already generates a fresh UUID `session_id` per call; this is now embedded in the capture page (`SESSION_ID` JS constant) and echoed back on every request to `verify_check_liveness` (both Mode A and Mode B) and `verify_submit`. Both endpoints now validate the submitted `session_id` against the current server-side session and **refuse** (never fall back, never reassign) on any mismatch or omission — closing the gap for a stale tab, a second tab, or a client that simply drops the field. Verified this does not weaken any existing liveness/TX/threshold check — it is a new, additive guard checked before all of them.

**Files changed:** `verification/views.py` (`verify_check_liveness`, `verify_submit`), `templates/verification/verify_capture.html`, `static/js/verify.js`.

**Tests added:** `VerifySubmitSessionCollisionGuardTest` (5 tests) — stale tab after a second `verify_start` is rejected and creates zero `VerificationAttempt`/`ClaimRecord` for either beneficiary; the current (non-stale) session_id still works normally; Mode B refuses to issue a `LivenessTransaction` for a stale session (closing the gap before `verify_submit` is even reached); a client omitting `session_id` entirely is also refused; restarting verification for the *same* beneficiary correctly rotates the session_id too.

**Targeted test result:** 5/5 pass. Fixing this required updating **37 pre-existing tests** across 11 classes in `verification/tests.py` that posted directly to these two endpoints without a `session_id` (all now use a shared `_FIXED_TEST_SESSION_ID` constant, echoed in both the session setup and the POST payload) — all now pass, confirmed via the full `verification` app suite (363/363) before moving on.

**Status:** FIXED — this was the critical finding of this pass.

---

## Issue 31 — Same face still registerable to another pending beneficiary

**Root cause (two-part, neither is what might be assumed):**

1. **Detection itself was already correct.** `check_duplicate_face()` queries `FaceEmbedding.objects.select_related('beneficiary').all()` with **no status filter at all** — pending, active, inactive, deceased, and disapproved records are all included, and name plays no role (only the biometric embedding does). Proven directly with 8 new fast unit tests (`CheckDuplicateFaceStatusMatrixTest`) covering every status plus same-name/different-face (not flagged) and different-name/same-face (flagged).
2. **The real gap was downstream: nothing surfaced the flag, and a second approval path bypassed it entirely.** `duplicate_review_required` was correctly set on the second "Toni Anek Fowler" registration, but (a) neither `beneficiary_list.html` nor `beneficiary_detail.html` showed any indicator of it, so the record looked like an ordinary pending registration everywhere except the dedicated Duplicate Face Review queue, and (b) — the more serious half — the **generic** "Registration Applications" queue (`registration_review_list` / `registration_review`, the one now in the main nav from the earlier Phase 11 work) had **zero awareness of `duplicate_review_required`** and would let an admin approve straight past an unresolved conflict.

**Fix:**
- `registration_review_list` now excludes `duplicate_review_required=True` records (mirroring the exclusion the auto-approval queue already had) and shows a count + link directing admins to Duplicate Face Review instead.
- `registration_review` now hard-blocks both GET and POST (approve *and* reject) for a flagged beneficiary, re-checked again under the row lock for defense against a concurrent flag — redirects to the dedicated case instead.
- Added a prominent red "Duplicate Face Detected — Unresolved" banner (with match beneficiary, score, and a review link) to `beneficiary_detail.html`, and a "Duplicate Face" badge to `beneficiary_list.html`.

**Files changed:** `verification/views.py` (`registration_review_list`, `registration_review`), `templates/verification/registration_review_list.html`, `templates/beneficiaries/detail.html`, `templates/beneficiaries/list.html`.

**Tests added:** `CheckDuplicateFaceStatusMatrixTest` (8 tests, direct function-level), `RegistrationReviewDuplicateFaceGateTest` (8 tests — flagged record excluded from generic queue, count shown, GET/approve/reject all redirect to Duplicate Face Review, status never advances, a *clean* pending registration is unaffected, banner and badge render correctly).

**Targeted test result:** 16/16 pass.

**Status:** FIXED.

---

## Issue 33 — Duplicate biometric + manual review must not bypass payout safety

**Root cause:** by design, `duplicate_review_required=True` only ever coexists with `status=PENDING` — a flagged record was already blocked from verification by the existing `is_eligible_to_claim` status check, **before** this pass's Issue 31 fix closed the approval-bypass gap. The residual risk (explicitly asked to be investigated) is the abnormal/legacy case: a record that is somehow `ACTIVE` while `duplicate_review_required` is still `True` — e.g. data from before this fix, or any future code path that changes status without going through the two now-gated review flows. Before this fix, nothing else in the claim chain re-checked the flag, so such a record's verification and even a manual-review override-release could proceed to a real payout despite the unresolved identity conflict.

**Fix:** `Beneficiary.is_eligible_to_claim` — the single property every claim-adjacent view already calls (`verify_start`, `override_release_payout`'s pre-flight guard, `update_face_data`) — now also requires `not duplicate_review_required`. This is deliberate defense-in-depth at the one shared choke point rather than duplicating the check in each view. Verified explicitly that overriding a **separate** manual-review condition on a `VerificationAttempt` (the literal "MANUAL REVIEW — RELEASE BLOCKED... later a verification succeeded" scenario from the report) does **not** silently clear the beneficiary's distinct duplicate-face conflict — the release is still refused. Error messages at all three call sites were made duplicate-aware instead of the generic "not eligible" wording, so the operator sees the real reason.

**Files changed:** `beneficiaries/models.py` (`is_eligible_to_claim`), `verification/views.py` (`verify_start`, `override_release_payout` — messaging only, gate was inherited from the model property).

**Tests added:** `DuplicateConflictBlocksPayoutTest` (5 tests) — property is `False` while flagged and `True` once cleared; `verify_start` refuses and redirects (never renders the capture page) for a flagged-but-active beneficiary; an admin override-release on a verified, overridden attempt for a flagged beneficiary creates **zero** `ClaimRecord`s; the same flow succeeds normally once the flag is cleared through the proper resolution.

**Targeted test result:** 5/5 pass.

**Status:** FIXED.

---

## Issues 34 & 35 — Beneficiary detail server error (re-investigation)

**Investigated as instructed: did not assume the previously-fixed orphaned `{% endif %}` was the only possible cause.** Re-tested `beneficiary_detail` with every combination named in the ticket (normal/pending/approved/inactive/deceased/disapproved, duplicate-face review history, multiple biometric templates, manual review, successful/failed verification, claim history, representative data, conflicting duplicate data) across Admin, President, and Staff roles — **20 tests, all pass, no server error found in `beneficiary_detail` itself.**

**However, while building the duplicate-flagged test data, a real, separate crash was found and traced to its actual cause** — directly relevant to "template assumptions that only one related object exists" / "nullable/missing relationships" from the investigation brief:

**Root cause:** a chained-filter template pattern — `{{ X.get_full_name|default:X.username }}` — used **without** an `{% if X %}` guard wherever `X` is a nullable `on_delete=SET_NULL` user FK (`registered_by`, `requested_by`, `performed_by`, `released_by`, `override_by`, `decided_by`). When `X` is `None` (a fully legitimate state — e.g. the registering staff account was later deleted, or a request was never explicitly assigned), Django's filter-**argument** resolution raises `VariableDoesNotExist` instead of silently degrading the way the *main* variable position does, producing a hard 500. This is **not** the reported beneficiary-detail page (which has no such unguarded instance — confirmed by grep and by the 20 passing tests), but it is the exact same defect class, and it was live in **13 other templates** across the registration-review, manual-review, duplicate-review, and claims/payout workflows — several of which are precisely the pages an admin would visit immediately after the Issue 31/32 duplicate-face scenario described in this ticket. This is very plausibly what the tester actually hit, reported generically as "beneficiary detail."

**Fix:** wrapped every unguarded instance in `{% if X %}...{% else %}—{% endif %}`, matching the safe pattern already used elsewhere in the codebase. Left the several instances that were *already* safely guarded, and the instances where the underlying FK is non-nullable (`OfficerAssignment.user` is `CASCADE`, not `SET_NULL`) or is guaranteed non-None in context (`request.user`, a `get_object_or_404`-fetched instance, a loop variable itself) untouched — verified each field's actual `null=`/`on_delete=` before changing anything, per the "do not guess" instruction.

**Files changed:** `templates/verification/registration_review_list.html`, `registration_review.html`, `manual_review.html` (5 instances), `manual_verify_review.html`, `face_update_review.html`, `special_claim_review.html`, `override_release_confirm.html`, `payout_detail.html`, `result.html` (2 instances), `shared_rep_review_detail.html`, `templates/beneficiaries/duplicate_review_list.html`, `duplicate_review_detail.html`, `sync_conflict_review.html`.

**Tests added:** extended `BeneficiaryDetailViewTest` with 7 new tests (duplicate-conflict banner, multiple `AdditionalFaceEmbedding` templates, the *original* beneficiary referenced by two other records' duplicate flags, President role, Staff role, and one test combining duplicate-face + multiple templates + representative + manual review + failed verification + claim history all at once). The specific crash found above is caught by `RegistrationReviewDuplicateFaceGateTest` and `CheckDuplicateFaceStatusMatrixTest`'s supporting fixtures (a beneficiary created via `_make_beneficiary` has `registered_by=None` by construction — exactly the null state that was crashing these pages) — those 16 tests would have failed with the old templates and now pass.

**Targeted test result:** 20/20 (`BeneficiaryDetailViewTest`) + 16/16 (Issue 31 classes, which exercise the fixed templates) pass.

**Status:** FIXED (the newly-found template bug); the originally-reported page itself was UNVERIFIABLE as the crash's actual source since it could not be reproduced in `beneficiary_detail` under any tested combination — most likely the tester encountered one of the 13 now-fixed pages while working through the duplicate-face scenario and described it by the general area ("beneficiary detail viewing").

---

## Issue 36 — Beneficiary ID input vs. automatic generation

**Investigated completely, per the instruction not to change anything without first confirming a real field exists.**

**Finding: no such field exists.** Checked every path that could plausibly expose it:
- `BeneficiaryInfoForm` (registration) and `BeneficiaryEditForm` (admin edit) — neither lists `beneficiary_id` in `Meta.fields`; confirmed via `'beneficiary_id' not in form.fields`.
- The actual rendered registration template — confirmed no `name="beneficiary_id"` input exists in the HTML.
- `beneficiaries/sync.py` (offline sync) — only *reads* `beneficiary.beneficiary_id` to report already-generated IDs to a central server; never accepts one as input.
- `Beneficiary.save()` already auto-generates `BEN-{year}-{sequence}` under a `select_for_update()` row lock whenever `beneficiary_id` is blank, and the field carries a DB-level `unique=True` constraint — concurrent-safe and duplicate-proof by construction, confirmed with a direct `IntegrityError` test.

**Conclusion:** the reported "input field for Beneficiary ID" is almost certainly a mistaken identification of **Senior Citizen ID Number** — a genuinely required, clearly-labeled input field on the same registration screen, and the concept the ticket itself contrasts with "Beneficiary ID" as the internal identifier. No code change was needed for the core concern; the two identifiers were already independent (verified with a dedicated test), and the internal ID was already never user-editable.

**Files changed:** none (investigation only; no defect found to fix).

**Tests added:** `BeneficiaryIdGenerationTest` (8 tests) — auto-generation format and sequencing, DB-level uniqueness rejection, absence from both forms, absence from the rendered registration page's HTML, and independence from Senior Citizen ID (changing one never touches the other).

**Targeted test result:** 8/8 pass.

**Status:** VERIFIED, NO DEFECT FOUND — documented above per the "if there is a legitimate reason the field exists, document and verify" instruction (there is no such field, so nothing to document beyond this finding).

---

## Regression tests required by the brief — cross-reference

| # | Requirement | Covered by |
|---|---|---|
| 1 | Pending + same face → duplicate triggers | `CheckDuplicateFaceStatusMatrixTest.test_pending_beneficiary_same_face_triggers_duplicate` |
| 2 | Approved + same face → duplicate triggers | `test_active_beneficiary_same_face_triggers_duplicate` |
| 3 | Same name + different face → allowed | `test_same_name_different_face_not_a_duplicate` |
| 4 | Different name + same face → duplicate triggers | `test_different_name_same_face_triggers_duplicate` |
| 5 | Verification for A + correct face → stays bound to A | Covered by the full pre-existing `verification` suite (363 tests, all still pass unmodified in intent) |
| 6 | Verification for A + face belonging to B → must not switch | `VerifySubmitSessionCollisionGuardTest.test_stale_tab_after_second_verify_start_is_rejected_not_reassigned` |
| 7 | Duplicate conflict + verification → payout cannot bypass | `DuplicateConflictBlocksPayoutTest.test_override_release_payout_refuses_flagged_beneficiary` |
| 8 | Manual review of one condition ≠ resolves another | Same test — an override on the manual-review condition does not clear the separate duplicate flag |
| 9 | Beneficiary detail works with duplicate/review history | `BeneficiaryDetailViewTest.test_beneficiary_with_duplicate_review_flag_shows_conflict_banner`, `test_beneficiary_with_full_realistic_combination` |
| 10 | Beneficiary detail works with multiple biometric records | `test_beneficiary_with_multiple_additional_face_embeddings` |
| 11 | Beneficiary detail works for every authorized role | `test_beneficiary_detail_accessible_to_president_role`, `test_beneficiary_detail_accessible_to_staff_role` (Admin already covered by the base test class) |
| 12 | Beneficiary ID cannot be supplied/manipulated | `BeneficiaryIdGenerationTest.test_beneficiary_id_not_a_registration_form_field`, `test_beneficiary_id_not_an_edit_form_field`, `test_beneficiary_id_not_in_registration_template` |
| 13 | Generated IDs remain unique | `test_duplicate_beneficiary_id_blocked_at_db_level` |
| 14 | Senior Citizen ID independent of Beneficiary ID | `test_senior_citizen_id_independent_of_beneficiary_id` |

All 14 requested regression scenarios are covered by name, not just by incidental overlap.

---

## Security regression check

- **Liveness / anti-spoof / thresholds / PAD:** untouched. The new session_id guard runs *before* any of these checks and only adds a refusal path — it cannot cause a request to pass a check it would otherwise have failed.
- **Duplicate-face detection:** logic and thresholds untouched (explicitly not rewritten, per instruction) — only its *consequences* (visibility, approval-path enforcement, claim-eligibility gating) were fixed.
- **Claim/payout authorization:** `is_eligible_to_claim` became *more* restrictive, never less. `override_release_payout`'s existing Separation-of-Duties and row-lock protections are untouched.
- **Role authorization:** no `is_admin`/`login_required` check was added, removed, or weakened anywhere in this pass — Issue 31's queue exclusion and gate are additional refusals layered on top of the existing `is_admin` check, not a replacement for it.
- **CSRF / session security:** the session-collision guard *strengthens* session handling (previously, session-bound data was trusted with no freshness check at all). No new client-controllable trust was introduced — `session_id` is only ever compared, never used to look anything up.
- **Audit logging:** no existing `AuditLog.log()` call was removed; the new duplicate-conflict refusals raise user-facing messages but do not need new audit actions (the underlying `ACTION_DUPLICATE_FACE` and `ACTION_SHARED_REP_FLAGGED` entries already exist from registration time).
- **No thresholds were lowered, no detection was disabled, no frontend-supplied beneficiary identity was ever trusted, and no duplicate records were merged or deleted.**

---

## Database / migration safety

No schema changes were required for this pass — `is_eligible_to_claim` is a Python property, not a field, and every other change is view/template logic. `makemigrations --check --dry-run` confirms no pending model changes. No existing data was touched, deleted, or migrated.

---

## Full test results

```
python manage.py test        → Ran 880 tests — OK (0 failures, 0 errors). [840 before this pass + 40 new]
python manage.py check       → System check identified no issues (0 silenced).
python manage.py makemigrations --check --dry-run → No changes detected.
```

Targeted suites run individually before the full run: `verification` app alone (363/363), `BeneficiaryDetailViewTest` (20/20), `RegistrationReviewDuplicateFaceGateTest` (8/8), `CheckDuplicateFaceStatusMatrixTest` (8/8), `DuplicateConflictBlocksPayoutTest` (5/5), `VerifySubmitSessionCollisionGuardTest` (5/5), `BeneficiaryIdGenerationTest` (8/8).

---

## Files changed this pass

`beneficiaries/models.py` · `verification/views.py` · `verification/tests.py` · `beneficiaries/tests.py` · `static/js/verify.js` · `templates/verification/verify_capture.html`, `registration_review_list.html`, `registration_review.html`, `manual_review.html`, `manual_verify_review.html`, `face_update_review.html`, `special_claim_review.html`, `override_release_confirm.html`, `payout_detail.html`, `result.html`, `shared_rep_review_detail.html` · `templates/beneficiaries/detail.html`, `list.html`, `duplicate_review_list.html`, `duplicate_review_detail.html`, `sync_conflict_review.html`.

No migrations added.

---

## Manual verification checklist — what still needs a real browser/device

Per the instruction not to claim browser-only behavior is verified when it was only exercised through Django's test client:

| Item | Still needs manual/physical testing? |
|---|---|
| Same pending face registration (Issue 31 UI flow) | **Yes** — the banner/badge rendering was checked via test client HTML assertions, not a live browser |
| Same approved face registration | **Yes** — same caveat |
| Same-name different-face registration | **Yes** |
| Wrong-beneficiary face during claim verification (Issue 32) | **Yes, especially this one** — the session-collision guard's *server-side* refusal is fully proven by automated tests, but the actual two-tab browser interaction (opening verify_start for A, then B, in real separate tabs, then submitting A's stale page) was not physically reproduced in a browser this session |
| Duplicate-face review workflow end-to-end | **Yes** |
| Manual-review workflow end-to-end | **Yes** |
| Beneficiary detail page (Admin / President / Staff) | **Yes** — status-code and content-string checks only |
| Beneficiary ID registration UI | **Yes** (though no defect was found to verify) |
| Analytics/navbar/org-chart visual behavior | **Still outstanding from the previous pass** — unchanged this session |
| Real SMTP OTP delivery | **Still outstanding from the previous pass** — unchanged this session |
| Physical camera verification | **Still outstanding** — this pass touched `verify_submit`/`verify_check_liveness` request validation only; the face-processing pipeline itself was not modified, but a live camera smoke test including the new session_id field has not been performed |

**Nothing in this list was claimed as verified beyond what the automated test suite actually exercises.**

## Any issue that could not be conclusively verified

- **Issue 34/35's exact original crash** could not be reproduced in `beneficiary_detail` itself under any combination tested — see the writeup above. High confidence (not certainty) that the tester actually hit one of the 13 other now-fixed pages, based on the workflow they described.
- **Issue 32's browser-tab reproduction** — the server-side mechanism and fix are proven by automated tests simulating the exact session-overwrite sequence, but a literal two-physical-tab browser reproduction was not performed (no browser automation tool was available this session).

## Final release recommendation

All six reported issues were investigated to a concrete root cause, fixed at that root cause (not patched at the symptom), and covered by new automated regression tests — 40 new tests, all passing, alongside the full pre-existing 840-test suite. The CRITICAL identity-binding issue (32) is confirmed closed at the mechanism level. No security control was weakened; several were strengthened (session freshness, claim eligibility, approval-path coverage).

**READY FOR RELEASE BUILD**, conditional on the manual verification checklist above — most importantly, a real two-tab browser reproduction of the Issue 32 scenario before shipping, since that was this pass's highest-severity finding and has not been physically confirmed end-to-end outside the automated test client.
