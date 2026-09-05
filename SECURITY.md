# Security

FANS-C is a biometric government system (facial verification for senior-citizen
stipend distribution). This file is a short pointer for anyone scanning the repo
root — GitHub looks for `SECURITY.md` here specifically. The actual technical
security documentation lives in `docs/`:

- **[docs/SECURITY-CHECKLIST.md](docs/SECURITY-CHECKLIST.md)** — the authoritative,
  item-by-item technical security review: authentication, session/cookie security,
  CSRF, TLS, Django admin exposure, liveness/anti-spoof gates, rate limiting, and
  more. Each item is marked `[OK]`, `[ACTION]`, `[RISK]`, or `[KNOWN RISK]` —
  read the marker, not just the item title.
- **[docs/FACE-VERIFICATION-INTELLIGENCE-AUDIT.md](docs/FACE-VERIFICATION-INTELLIGENCE-AUDIT.md)**
  — matching method, confidence thresholds, duplicate detection, and mismatch
  handling for the biometric pipeline specifically.
- **[docs/KNOWN-LIMITATIONS.md](docs/KNOWN-LIMITATIONS.md)** — limitations that are
  known and accepted, not hidden.
- **[docs/PASSWORD-RECOVERY-ARCHITECTURE.md](docs/PASSWORD-RECOVERY-ARCHITECTURE.md)**
  — design rationale for the OTP-based password reset flow.
- **[CHANGELOG.md](CHANGELOG.md)** — every security-relevant fix is called out by
  release; search for "security" or "CRITICAL" for the highest-severity entries.

## Reporting a vulnerability

This is a closed-deployment academic/municipal project, not a public service with
a bug bounty program. If you find a security issue, report it directly to the
project maintainer rather than opening a public GitHub issue — biometric and PII
handling means even a description of a vulnerability can be sensitive.

## Scope note

Nothing in this repository's documentation should be read as a claim of legal or
regulatory compliance (e.g. Data Privacy Act certification). Technical controls
are documented as **implemented** or **known limitation**; legal/policy
compliance review is a separate, unperformed step — see
[docs/KNOWN-LIMITATIONS.md](docs/KNOWN-LIMITATIONS.md).
