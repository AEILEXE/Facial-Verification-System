# Deployment

Short pointer — the actual deployment documentation is split by audience:

| Document | Audience | Covers |
|---|---|---|
| **[SETUP.md](SETUP.md)** | IT staff setting up a server | Full first-time server setup: venv, dependencies, HTTPS certs, `.env`, auto-start, watchdog. Start here for a from-scratch deployment. |
| **[CLIENT_ACCESS.md](CLIENT_ACCESS.md)** | Barangay staff (end users) | How to open FANS-C in a browser on a client device — no technical setup required. |
| **[docs/DEPLOYMENT-CHECKLIST.md](docs/DEPLOYMENT-CHECKLIST.md)** | IT staff / release engineer | Pre-flight checklist before installing/upgrading a production build. |
| **[dev/BUILD.md](dev/BUILD.md)** | Developer building a release | How the PyInstaller `.exe` + Inno Setup installer are built, and what must be verified in the payload before shipping (no `.env`, no `db.sqlite3`, no certs/keys, no logs). |
| **[docs/BACKUP-RESTORE.md](docs/BACKUP-RESTORE.md)** | IT staff | Automated daily backup job, manifest/integrity lock, and restore procedure. |
| **[docs/DATABASE-GUIDE.md](docs/DATABASE-GUIDE.md)** | IT staff / developer | SQLite vs. PostgreSQL backend selection and configuration. |

## Environment variables and secrets

All secrets (`SECRET_KEY`, `EMBEDDING_ENCRYPTION_KEY`, SMTP credentials, DB
password) belong in a local `.env` file, which is **never committed** (see
`.gitignore`). `.env.example` documents every variable with a placeholder —
copy it to `.env` and fill in real values for your deployment; do not commit
the filled-in file. `SETUP.md`'s automated setup script generates
`SECRET_KEY` and `EMBEDDING_ENCRYPTION_KEY` for you; SMTP credentials
(`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`,
`EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL`) are optional and only needed if the
self-service email-OTP password reset feature is enabled — see "Email OTP
Configuration" in `SETUP.md`. The reference implementation targets a
dedicated **Gmail** account with a Gmail **App Password** (never the normal
account password); any standard SMTP relay can be substituted by overriding
the same `.env` keys.

## Installer-based deployment (recommended path)

Most barangay sites deploy via the packaged Windows installer
(`FANS-C-Setup.exe`, built per `dev/BUILD.md`) rather than from source. At a
glance:

- **Installer behavior:** Inno Setup installs the PyInstaller-bundled app to
  `C:\FANSC`, registers Task Scheduler entries, and (fresh installs only)
  launches the first-run setup wizard (`dev/launcher.py`) — see "Installing
  from the Windows Installer" in `SETUP.md` for the full step-by-step,
  including the wizard's optional Email OTP Configuration screen and
  Technical Administrator account creation form.
- **Runtime files:** `.env`, `db.sqlite3`, `media\`, `logs\`, and the mkcert
  TLS certificate files are all generated on the target machine at first run
  — none of them are shipped inside the installer payload (`dev/BUILD.md`'s
  payload-safety scan enforces this).
- **`.env` handling:** written once by the setup wizard (or by
  `setup-complete.ps1` for source deployments); edits to `.env` after install
  require a service restart to take effect (see `SETUP.md`).
- **Backup behavior / scheduled tasks:** a daily automated backup task and the
  self-healing watchdog task are both registered by setup — see
  `docs/BACKUP-RESTORE.md` and the Self-Healing Watchdog section of
  `SETUP.md` for schedules and retention.
- **HTTPS setup:** Caddy + mkcert, detailed in `SETUP.md`.
- **Security considerations:** see `SECURITY.md` and `docs/SECURITY-CHECKLIST.md`.

## What this repository does not do for you

This project does not build or publish the installer `.exe` as part of any
automated pipeline — there is no CI/CD in this repository. Building a release
is a manual, documented step (`dev/BUILD.md`) run by a developer on their own
machine, and the resulting binary is not committed to git (`FANS-C-Installer/`,
`dist/`, `build/` are all gitignored).
