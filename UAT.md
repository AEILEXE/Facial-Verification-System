# User Acceptance Testing

Short pointer, plus the automated-vs-manual distinction that matters most for
anyone relying on this document.

## Automated regression suite (proves correctness of logic, not the live UI)

Run `python manage.py test` from the project root. As of this repository
audit (v2.1.16 Final Hardening Patch, 2026-09-05): **1407/1407 tests pass**,
`python manage.py check` reports 0 issues, and
`python manage.py makemigrations --check --dry-run` reports no pending
changes. This number changes with every release — treat any specific count
written elsewhere as a snapshot, not a promise; re-run the command for the
current number.

The automated suite exercises view logic, model behavior, permissions, and
(via Django's test client) rendered HTML content — it does **not** exercise
an actual browser, an actual camera, or actual visual layout at a given zoom
level. Where a fix in this codebase's history is marked "code-level fix,
visual verification outstanding," that distinction is real: the server-side
logic is proven, the pixels have not been looked at.

## Manual/UAT testing records

- **[docs/POST-UAT-FIX-REPORT.md](docs/POST-UAT-FIX-REPORT.md)** and
  **[docs/POST-UAT-FOLLOWUP-FIX-REPORT.md](docs/POST-UAT-FOLLOWUP-FIX-REPORT.md)**
  — the most recent UAT feedback passes: 16 + 6 issues, each with a root
  cause, a fix, and an explicit FIXED / VERIFIED-NO-CHANGE-NEEDED status.
  Each report ends with a **"still needs manual/browser/physical testing"**
  checklist — read that section before assuming an item is fully verified.
- **[dev/FINAL-TESTING-PROCEDURE.md](dev/FINAL-TESTING-PROCEDURE.md)** — the
  step-by-step manual test script for a release candidate (installer
  install, HTTPS, camera/liveness, auto-start, watchdog).
- **[docs/FINAL_QA_CHECKLIST.md](docs/FINAL_QA_CHECKLIST.md)** — a broader
  pre-release QA checklist.

## What is currently unverified by any automated means

As of this audit, the following require a real browser and/or real hardware
and have **not** been confirmed since the most recent code changes touching
them:

- Organization chart tree-connector visual styling with real sibling-level
  data.
- Two-browser-tab reproduction of the verification session-collision fix
  (server-side mechanism is proven by automated tests; the literal two-tab
  browser interaction has not been physically reproduced).
- Real SMTP delivery of an OTP password-reset email (unit-tested against
  Django's in-memory email backend only).
- Physical-camera face capture and liveness challenge end-to-end.

None of these are marked as defects — they are marked as **unverified**,
which is a different, narrower claim. Do not read "the automated suite
passes" as "the UI has been visually confirmed."

**Navbar layout at 100% zoom** was confirmed during the 2026-09-02
UX/UI refinement pass, but by headless-Chrome screenshot capture (a
real Chromium layout/paint engine, driven via the DevTools protocol at
1200/1280/1366/1440/1536/1600/1680/1920px, with `scrollWidth`/
`clientWidth` measured to prove zero horizontal overflow) rather than a
human looking at a physical laptop screen — the previously-reported
~190px overflow is fixed and re-confirmed absent at every width tested.
A human spot-check on real hardware is still worth doing before release,
but this is materially stronger evidence than source inspection alone.
