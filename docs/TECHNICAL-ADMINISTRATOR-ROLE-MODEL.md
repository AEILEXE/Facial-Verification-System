# Technical Administrator Role Model (UX / Reporting pass, v2.1.x line)

> **Version-numbering note:** this document previously headed itself
> "v2.1.19," a version number that was never actually released and does not
> appear anywhere else in the project's history (see README.md's
> version-numbering note). The change described below shipped as part of
> the ongoing v2.1.x line, most recently v2.1.16 — this document is not
> pinned to a specific patch version.

This document explains a role-model correction made after the project owner
manually reviewed the deployed application: the `IT` system role is
user-facing as **Technical Administrator**, and its authority was narrowed.
It does not change the biometric algorithm, thresholds, or financial
transaction logic.

## Why

Barangay operations have no dedicated "IT Officer" position. The `IT` role
was created for researchers, developers, technical support, and diagnostics
— not as a barangay organizational appointment. Presenting it in the UI as
"IT" invited confusion with a real office. It is now labeled **Technical
Administrator** everywhere a human reads it.

## System Role vs. Organizational Position

These are, and always have been, separate concepts in this codebase:

- **System Role** (`CustomUser.role`) — controls software permissions.
  Values: President, Admin, Technical Administrator (`it`), Staff.
- **Organizational Position** (`OfficerPosition` / `OfficerAssignment`) —
  represents an actual barangay/OSCA office (President, Treasurer, Board of
  Director, ...), tracked with start/end dates.

Technical Administrator is a **system role only**. It must never appear in
the Organization Chart, Officer Positions list, Officer Assignment history,
or any organizational staffing count *merely because of the system role* —
those pages are driven entirely by explicit `OfficerAssignment` records, so
a Technical Administrator account with no assignment simply has none. If a
real person genuinely holds both the Technical Administrator system role
and a barangay office, that's a normal, explicit `OfficerAssignment` like
any other — the two facts just happen to be true of the same person.

## Internal value unchanged

The stored `role` value is still `'it'` — no database migration was made
for this pass, deliberately. `CustomUser.get_role_display()` is overridden
to return `"Technical Administrator"` for `role='it'`
(`accounts/models.py`); `CustomUser.TECHNICAL_ADMIN_LABEL` is the single
source string. `accounts/forms.py`'s `_ACTIVE_ROLE_CHOICES` uses the same
constant for every user-management form's dropdown. The model field's own
`ROLE_CHOICES` list intentionally keeps the legacy `'IT'` label — changing
it would alter Django's migration state for a wording-only change (see the
comment above `ROLE_CHOICES` in `accounts/models.py`). The same technique
is used for `EvaluationDataset.purpose` (section 21 of the source spec):
`get_purpose_display()` is overridden rather than editing `PURPOSE_CHOICES`.

## Financial authority separation

`CustomUser.has_financial_authority` is `True` only for President and
Admin. Technical Administrator (`is_admin_it`) keeps broad **read** access
(`is_admin` is unchanged and still includes IT) for diagnostics, but is
denied the following mutations — checked with `has_financial_authority`
instead of `is_admin`:

| Action | View | Gate |
|---|---|---|
| Create/edit/delete stipend event | `stipend_create/edit/delete` | `has_financial_authority` |
| Override a verification decision | `admin_override` | `has_financial_authority` |
| Release an overridden payout | `override_release_payout` | `has_financial_authority` |
| Cancel/fail/correct a released payout | `payout_action` | `has_financial_authority` |
| Approve/reject Manual Review | `manual_verify_review` (POST only — GET stays `is_admin`) | `has_financial_authority` |
| Approve/reject a special claim | `special_claim_review` (POST only — GET stays `is_admin`) | `has_financial_authority` |
| Assign/close an officer assignment | `officer_assignment_create/close` | `has_financial_authority` |
| Approve/reject a pending claim (no active payout event) | `pending_claim_review` | `has_financial_authority` |

**Closed exception (FINAL PRE-EXE COMPLETION checkpoint, section 5):**
`pending_claim_review` previously kept a "President (and IT)" exception,
checked with `is_admin` instead of `has_financial_authority`. The owner
confirmed Technical Administrator has broad diagnostic read access but no
normal financial mutation authority, so this view now checks
`has_financial_authority` like every other financial-decision view in the
table above — Technical Administrator is denied (UI button hidden in
`manual_review.html`, and direct POST/GET both rejected server-side),
President's existing approve/reject authority is unchanged.

## Technical Administrator creation policy

- **First-run/installer** (`accounts:create_admin`, and the packaged
  `dev/launcher.py` first-run form) always creates the *initial* Technical
  Administrator — there is no role picker any more.
- **Initial President bootstrap** (`accounts:bootstrap_president`) — the
  Technical Administrator may create the barangay's first President, but
  only while zero President accounts exist. The route refuses once one
  exists (`CustomUser.active_president_exists()`).
- **Additional Technical Administrators** — only the President may create
  one, or promote an existing account to it. Enforced as a raw-POST check
  in `accounts/views.py` (`user_create_full`, `user_edit_full`), not merely
  a hidden form option — a direct POST from an Admin, Staff, or another
  Technical Administrator session is rejected the same as from the UI.

## Biometric Evaluation access matrix

`verification/views.py`'s `_evaluation_admin_required(request, write=True)`
gates every Biometric Evaluation (formerly "Dataset"/"Trial") view:

- **Technical Administrator** — full access (read + write). The
  technical/research operator for this workflow: creates/starts/finalizes/
  archives Evaluation Sessions, sets up and runs trials, and is the only
  role that may abort or withdraw a trial.
- **President** — **read-only** oversight (list, detail, analytics, Face
  Template Health). May inspect Evaluation Sessions and finalized/interim
  results but must not create, start, finalize, archive, run, abort, or
  withdraw — enforced server-side by `_evaluation_admin_required(request,
  write=True)` (the default) on every mutating view, not merely by hiding
  the buttons.

  **Correction history:** an earlier pass kept President at full read+write
  because the BPA-1..BPA-5.1 test suite was built with President as the
  "admin-tier" actor. The owner overturned that reasoning (FINAL PRE-EXE
  COMPLETION checkpoint, section 0B/3): tests validate product policy, they
  do not define it. The BPA test fixtures were updated to use the Technical
  Administrator for every write-path assertion, and President fixtures now
  represent read-only oversight (or an explicit write-denial assertion).
  This included moving `evaluation_trial_withdraw` off its previous
  President-only gate onto the same Technical-Administrator-only gate as
  every other mutation.
- **Admin / Staff** — no access at all, read or write.

## Where this is exposed in the UI

- User Management (`accounts:user_list`) shows the role badge via
  `get_role_display()` plus a secondary "Account Type" line
  (`CustomUser.account_type_display`) for Technical Administrator rows —
  never a fabricated "Barangay Position: Technical Administrator".
- A new **Technical Administration** nav dropdown (visible to Technical
  Administrator and President only) holds Evaluation Sessions, Biometric
  Performance Analytics, and Face Template Health — moved out of the
  ordinary Analytics dropdown that Staff/Admin also see.
- `templates/accounts/my_profile.html` (new — see below) never exposes role
  or account-status fields, so a user can never self-promote.

## Related fix: My Profile

Before this pass, `user_edit_full` blocked self-editing with the message
"Use the profile settings to update your own account" — but no such route
existed. `accounts:my_profile` now exists (`MyProfileForm` in
`accounts/forms.py`): first/middle/last/suffix/email/phone are editable;
username, System Role, Account Status, and Organizational Position are
read-only and shown for reference only.
