# Password Recovery Architecture Decision

**Status:** Hybrid, as of v2.1.16 (development tracked internally as "v2.2.0 Post-UAT Phase 6" / installer wizard update, 2026-09-04 — see README.md's version-numbering note; the released version line is v2.1.x). **Decision:** Option C (hybrid) — self-service email-OTP recovery where a site has configured SMTP, with the internal admin-approval workflow (Option B) always available as a fallback, whether or not email is configured.

**History:** Phase 8 (documented below, now superseded) re-confirmed the original Option B-only decision for this LAN-only deployment profile. Phase 6 subsequently added self-service email-OTP recovery (`accounts/otp.py`, `accounts:otp_forgot_password`) as an *opt-in* addition, not a replacement — it never removed or weakened Option B. Until the 2026-09 installer update, though, there was no way to turn email on from a packaged install: the first-run setup wizard (`dev/launcher.py`) never wrote `EMAIL_*` keys to `.env` and had no UI for them, so every installed site defaulted to `EMAIL_CONFIGURED=False` regardless of intent. The wizard now includes an optional "Email OTP Configuration" step (skippable, unchecked by default) that writes `EMAIL_HOST`/`EMAIL_PORT`/`EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD`/`DEFAULT_FROM_EMAIL` into `.env` when an administrator opts in during setup; existing installs can still add the same keys by hand-editing `.env` and restarting (see `SETUP.md`).

## Options considered (original Phase 6/8 analysis, still accurate as background)

### Option A — Email-based reset
Originally evaluated as Django's built-in tokenized-link `PasswordResetView`; what Phase 6 actually built instead is a 6-digit OTP code (`accounts/otp.py`) with its own expiry/attempt/rate-limit/cooldown model — same category of solution (self-service via email), different mechanics, so the reasoning below still applies to *why email needs to stay optional*, just not to the original "rejected outright" framing.

**Why email must stay opt-in, not mandatory, for this deployment:**
- FANS-C runs on isolated barangay/government LAN sites with no guaranteed outbound internet access — the whole system is explicitly designed to work offline (SQLite default, LAN-only HTTPS via a self-signed cert + local root CA, no CDN dependencies anywhere in the frontend). Sites without internet simply leave the wizard's email step unchecked.
- Requires per-site SMTP relay configuration (host/port/credentials) — a per-deployment decision an installer wizard can offer but never assume; a new operational dependency (mail delivery monitoring, spam filtering, bounce handling) beyond what a barangay IT team otherwise needs.
- Delivery depends on infrastructure outside this system's control (recipient's mailbox, the mail transport) that this project cannot audit or harden — which is exactly why `send_otp_email()` treats delivery failure as best-effort and never surfaces it to the requester (see Security considerations below).
- A misconfigured SMTP relay fails silently ("email never arrives") from the requester's point of view — which is why Option B remains mandatory as a fallback rather than being replaced.

### Option B — Internal (admin) approval workflow
A locked-out user submits a self-service request; an admin/president reviews it and performs the reset in-app. **Still fully implemented and always available**, independent of whether a site has email configured.

**Why it remains, even where email is enabled:**
- Matches the security model already in place for every other sensitive action in this codebase: duplicate-face review, shared-representative review, face re-enrollment, manual verification review, name/DOB override — all human-approved, none automatic. Password recovery via the same pattern is consistent, not a special case.
- No infrastructure dependency (no SMTP, no outbound internet requirement) — works identically whether the deployment site has internet or not, and is the only path for a user whose registered email is wrong, unreachable, or was never set.
- The admin-reset mechanics already existed before this feature (`accounts.views.admin_reset_password`) for admin-initiated resets; this feature only adds the missing self-service *request* side, funneling into that same, already-audited path.
- Anti-enumeration by design: the public request endpoint never reveals whether a submitted username matches a real account (identical response either way).
- Fully audit-logged: request creation, approval, and rejection all write `AuditLog` entries (`ACTION_PASSWORD_RESET_REQUEST`, `ACTION_PASSWORD_RESET_REQUEST_REJECTED`, and the existing `ACTION_PASSWORD_RESET` on approval), with a `source: self_service_request` marker tying the approval back to the originating request.

### Option C — Hybrid (email where available, approval elsewhere) — **current implementation**
Each site independently decides at install time (or later, via manual `.env` edit) whether to enable email-OTP; Option B keeps working regardless. There is exactly one code path per feature — no per-site branching in application code, only in configuration (`EMAIL_CONFIGURED`, computed once from `.env` in `fans/settings.py`). This is deployment-specific by design: a barangay site with no internet leaves it off; a site with a reliable SMTP relay (e.g. a dedicated Gmail account with an App Password) turns it on. The two options are not mutually exclusive and were built to coexist from Phase 6 onward — see `accounts/otp.py`'s `send_otp_email()`, which fails closed (silently, server-audit-logged only) whenever `EMAIL_CONFIGURED` is False, precisely so Option C degrades to Option B-only with no code change required.

## Implementation summary

- SMTP configuration is deployment-specific and lives only in `.env` (`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL`) — never in source. It is set either by the first-run installer wizard's optional "Email OTP Configuration" step, or by hand-editing `.env` on an already-installed site (both require a service restart to take effect; see `SETUP.md`).
- OTP and password-changed emails are sent as branded HTML with a plain-text
  fallback via `EmailMultiAlternatives` (`templates/accounts/emails/otp_code.{html,txt}`,
  `templates/accounts/emails/password_changed.{html,txt}`) — presentation
  only; none of the OTP generation/expiry/cooldown/rate-limit/anti-enumeration
  logic below changed to add this.
- `fans/settings.py` computes `EMAIL_CONFIGURED = bool(EMAIL_HOST and EMAIL_HOST_USER)` once at startup; `accounts/otp.py` checks this before ever attempting SMTP, so an unconfigured site never touches the network.
- Model: `accounts.models.PasswordResetRequest` (pending/approved/rejected, mirrors the existing `FaceUpdateRequest`/`ManualVerificationRequest` request→approval pattern).
- Public entry point: `accounts:password_reset_request_create` — no login required, generic response regardless of username match.
- Admin queue: `accounts:password_reset_request_list`, gated the same way every other admin queue in this app is (`request.user.is_admin`).
- Approval funnels into the existing `accounts:admin_reset_password` view via an optional `?prr=<id>` parameter — a `select_for_update()` guard inside a transaction prevents two admins from both consuming the same pending request.
- A notification is created for all active admin-level users when a request is submitted (see `docs` / the Notification Center — `logs.notifications.notify_admins`).

## Security considerations addressed

- **Username enumeration:** identical response for matched/unmatched usernames; unmatched requests are still queued (with `user=None`) so an admin can see and dismiss junk.
- **Forced password change:** every reset (self-service-originated or not) sets `must_change_password=True`, so the admin-chosen temporary password is never the user's long-term password.
- **Race safety:** double-approval of the same request is blocked via `select_for_update()`.
- **Rate limiting:** duplicate pending requests for the same username are deduplicated at creation time; the existing login-lockout throttle (`fans/settings.py` `LOGIN_*` constants) remains the defense against credential-stuffing on the login form itself.
