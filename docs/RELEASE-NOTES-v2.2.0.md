> **HISTORICAL — point-in-time release notes for v2.2.0 (2026-08-29).** Superseded by later work (Post-UAT hardening pass, 2026-09-02 — see `docs/POST-UAT-FIX-REPORT.md` and `docs/POST-UAT-FOLLOWUP-FIX-REPORT.md`) that has not yet been packaged into a numbered release. See [CHANGELOG.md](../CHANGELOG.md) for the current authoritative version history.

# FANS-C v2.2.0 Release Notes

**Release theme:** Analytics, Intelligence, Notification, UX, and Workflow
Improvement Release
**Release date:** 2026-08-29
**Builds on:** v2.1.18 (hardened baseline)

This is an expansion release. It adds analytics/notification/fraud-detection
capability on top of the existing v2.1.x foundation, fixes two real bugs
found during release audit, and refines wording and menu structure. It does
**not** change core face-matching, liveness, authentication, or backup
logic — those were re-audited this release, not rebuilt.

---

## What's new

### Notifications
The bell-icon notification system now covers 7 categories instead of 4:
Approval Required, Approval Reminder, Verification Review, Fraud Alert,
Security Alert, Password Reset Request, and System Alert. Each notification
now carries a LOW/MEDIUM/HIGH priority, shown as a colored badge and — for
HIGH priority — a red left border in the dropdown. A pending approval left
untouched for 48+ hours now gets exactly one reminder notification. Failed
login lockouts and backup failures now notify admins directly instead of
requiring someone to check a log.

### Payout workflow clarity
The dashboard's "Next Payout" panel previously could not distinguish a
confirmed, approved event from one still awaiting President approval — both
looked the same. That is fixed: only approved events show as "Next Payout";
pending ones appear in a separate, clearly-labeled "awaiting approval"
section. Approving a stipend event after its scheduled date now requires a
short written reason, which is stored on the event and in the audit log.

### Session fix: refresh no longer logs you out on the HTTP fallback path
Sites that use the plain-HTTP LAN-IP fallback access mode (camera disabled
on that path, used only when the HTTPS/Caddy path is unavailable) previously
experienced an intermittent logout on page refresh. This was a cookie
security setting applying globally rather than per-connection. It's now
handled automatically and per-request — the normal HTTPS path is completely
unaffected.

### Analytics
The Executive analytics tab gains two new charts: 12-month Beneficiary
Growth and 12-month Monthly Distribution (total PHP released per month).
Both render from the same locally-vendored Chart.js library already used
elsewhere — no external network requests, consistent with this system's
offline-first LAN deployment model.

### Security Review (formerly "Fraud Signals")
Renamed for clarity, and expanded from three to five signal types:
repeated verification failures, anomalous staff verification volume,
admin mass-edit detection, and two new signals — repeated login failures
by IP address, and payout anomalies (overrides/cancellations/failures) by
staff member. Every signal now reports an explicit 0–100 risk score banded
LOW (0–30) / MEDIUM (31–70) / HIGH (71–100), instead of a bare label.

**As with every release, this page remains view/flag-only.** No signal at
any score automatically blocks, suspends, or denies any beneficiary, staff,
or admin account. A human always makes the call.

### Wording and menu cleanup
"Fallback" (in the sense of the ID-check verification method) is now
labeled "Manual Verification" throughout reports and audit logs — this was
frequently confused with the separate, unrelated "Fallback Access (HTTP)"
network-mode label, which was deliberately left unchanged. The System
Administration menu's 21 links are now grouped into 5 clear categories:
User Management, Verification, Distribution, Monitoring, System. Nothing
was removed.

---

## What was re-audited but intentionally not changed

- **Password recovery.** The existing self-service request → admin-approval
  workflow (no email dependency, matching this system's offline-LAN
  deployment) already satisfied every requirement considered for this
  release. See `docs/PASSWORD-RECOVERY-ARCHITECTURE.md`.
- **Face verification intelligence.** No automatic rejection based on
  estimated age exists anywhere in the codebase, and none was added. An
  age-estimation model was deliberately not built — accuracy on elderly
  faces is a known weak point for most public models, and bundling an
  unverifiable third-party binary into a government biometric system is a
  provenance risk that isn't worth the marginal benefit over the existing
  appearance/score-drift advisory signal, which already satisfies "flag for
  human review, never auto-reject." See
  `docs/FACE-VERIFICATION-INTELLIGENCE-AUDIT.md`.

## Bug fixes

- Dashboard mislabeling of unapproved stipend events as confirmed payouts.
- Refresh-triggered logout on the plain-HTTP LAN-IP fallback access path.
- Blank profile-photo display in the beneficiary list table (icon
  placeholder was already correct on the detail page; the list page was
  missing it).

## Upgrade notes

No `.env` changes are required to upgrade from v2.1.16/17/18. Four new,
non-destructive migrations apply automatically
(`logs/0012`, `logs/0013`, `verification/0021`, `verification/0022`) — none
alter or remove existing data; two are display-label-only. See
`dev/V2.2.0-UPGRADE-CHECKLIST.md` for the full upgrade procedure.

## Known limitations

See `docs/KNOWN-LIMITATIONS.md` (v2.2.0 section) for the complete list.
Nothing new was introduced by this release beyond what's already
documented — the login-lockout counter's in-memory nature is now called
out explicitly since it's directly relevant to the new Security Alert
notification.
