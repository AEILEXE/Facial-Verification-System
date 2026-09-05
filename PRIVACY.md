# Privacy

FANS-C processes personal data and biometric (facial) data for senior citizens
and their authorized representatives. This file is a short pointer — the
in-depth material lives elsewhere:

- **In-app Privacy & Data Consent Notice** (`templates/system/privacy_consent.html`,
  served at `/system/privacy-consent/` in a running instance) — the primary,
  user-facing explanation of what is collected, why facial data is collected,
  how it is stored (embeddings encrypted at rest with Fernet/AES-128-CBC +
  HMAC-SHA256), and who can access it. This is the document staff and
  administrators actually see.
- **[docs/KNOWN-LIMITATIONS.md](docs/KNOWN-LIMITATIONS.md)** — explicitly lists
  the Privacy & Data Consent Notice as *informational*, not a signed legal
  consent form; a printed, signed consent document may still be required by
  local policy and is outside this codebase's scope.
- **[docs/FACE-VERIFICATION-INTELLIGENCE-AUDIT.md](docs/FACE-VERIFICATION-INTELLIGENCE-AUDIT.md)**
  — records what the face-matching pipeline does and does **not** do (e.g. no
  age-estimation model, by deliberate decision — see that document for why).
- **[docs/SECURITY-CHECKLIST.md](docs/SECURITY-CHECKLIST.md)** — technical
  controls protecting the data described above (encryption, access control,
  audit logging).

## What this document is not

This is a technical index, not a legal instrument. Nothing here or in the
linked documents constitutes a Data Privacy Act (or equivalent) compliance
certification. Whether FANS-C's data handling satisfies a given jurisdiction's
privacy law is a **policy/legal question outside this repository's scope** —
treat every technical control below as "implemented," never as "compliant,"
until an actual legal/privacy review has signed off.

## Data handled

- Personal identity data (name, date of birth, address, Senior Citizen ID,
  contact info) for beneficiaries and their registered representatives.
- Facial biometric embeddings (not raw photos, by default — see the in-app
  notice for the exact retention behavior of any captured frames).
- Verification/claim/payout history, tied to the above.
- Audit log entries recording who accessed or changed what, and when.

## Retention and deletion

Beneficiary and biometric records are not hard-deleted through normal
application flows — records are deactivated (`STATUS_INACTIVE`,
`STATUS_DECEASED`) rather than removed, to preserve the audit trail required
for a government stipend program. If a legal "right to erasure" requirement
applies in your deployment, that is a policy decision requiring a deliberate,
audited data-deletion procedure — not currently an application feature.
