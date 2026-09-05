# Building FANS-C-Setup.exe — Release Guide

This document describes how to produce a **safe, releasable** Windows installer
`FANS-C-Setup.exe` from the project source.

---

## CRITICAL SECURITY RULE

**DO NOT run `dist\fans_c\fans_c.exe` before compiling the installer.**

Running the exe on the build machine writes runtime data into the `dist\fans_c\`
folder:

| File written by test run | Why it must NOT be bundled |
|---|---|
| `dist\fans_c\.env` | Contains the developer's SECRET_KEY and EMBEDDING_ENCRYPTION_KEY |
| `dist\fans_c\_internal\db.sqlite3` | Contains the developer's admin account |
| `dist\fans_c\fans-cert.pem` | Machine-specific TLS certificate |
| `dist\fans_c\fans-cert-key.pem` | TLS **private key** |
| `dist\fans_c\_internal\CLIENT-SETUP\rootCA.pem` | Developer's local CA certificate |
| `dist\fans_c\logs\` | Runtime log files |

If any of these reach the installer:
- The target machine will have the developer's login credentials pre-loaded.
- `Create Admin` will never appear (setup is skipped because `.env` exists).
- The developer's private keys are shipped to every deployment site.

The build script (`build_exe.ps1`) enforces this with a **payload safety scan**
that fails immediately if any sensitive file is present in the installer payload.
**If the scan fails, do not build or release the installer.**

---

## Prerequisites (build machine only)

| Requirement | Version | Notes |
|---|---|---|
| Python | **3.11 exactly** | Other versions break TensorFlow |
| Project `.venv` | — | All `requirements.txt` installed |
| PyInstaller | ≥ 6.x | Installed by the build script |
| Inno Setup 6 | ≥ 6.3 | https://jrsoftware.org/isdl.php |
| robocopy | built-in | Part of Windows — used for clean staging |

---

## Safe release build process

### Step 1 — Start from a completely clean state

Stop all running instances:

```powershell
wmic process where "name='fans_c.exe'" call terminate
wmic process where "name='caddy.exe'" call terminate
```

Delete all previous build artefacts:

```powershell
Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue
```

### Step 2 — Build the PyInstaller bundle + create clean staging

From the **project root**:

```powershell
.\dev\build_exe.ps1 -Clean
```

This script:
1. Checks Python 3.11 and `.venv`
2. Installs PyInstaller and waitress
3. Runs `collectstatic`
4. Runs PyInstaller → `dist\fans_c\`
5. Copies `.env.example` and `SETUP.md` into `dist\fans_c\`
6. **Creates a clean staging folder** `build\installer-staging\fans_c\` using
   robocopy with explicit exclusions (no `.env`, `db.sqlite3`, `*.pem`, `*-key.pem`,
   `rootCA.pem`, `logs\`, `media\`)
7. **Runs payload safety scan** — fails with a non-zero exit code if any
   sensitive file is present in the staging folder

The script must print:

```
[OK]  SAFE: no runtime/private files found in installer payload.
```

If it instead prints `PAYLOAD SAFETY SCAN FAILED`, **stop here and do not
compile the installer**. Follow the fix instructions the script prints.

### Step 3 — Compile the installer

**Immediately after step 2**, before running the exe:

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" ".\dev\installer\fans_c.iss"
```

Output: `FANS-C-Installer\FANS-C-Setup-v2.1.x.exe` (filename matches `OutputBaseFilename` in `fans_c.iss`; update the version number in `fans_c.iss` before each release build)

Verify the Inno log does **not** show any of these being compressed:
- `.env`
- `db.sqlite3`
- `fans-cert.pem` / `fans-cert-key.pem`
- `rootCA.pem`
- Any file from `logs\`

### Step 4 — Test the installer on a CLEAN PC

**Do not test on the build machine.** Use a PC that has never had FANS-C
installed, or fully clean one first using `cleanup-fansc.ps1`.

1. Run `FANS-C-Setup.exe` → UAC prompt → Install
2. Launch FANS-C
3. **`Create Admin Account` must appear** — if it does not, the installer is unsafe
4. Create a new admin account
5. Log in — only the newly created account must work
6. The developer's credentials must **not** work
7. Reboot and confirm autostart works

---

## Why the staging folder exists

Inno Setup's source directive was previously:

```iss
Source: "..\..\dist\fans_c\*"; Flags: recursesubdirs
```

This packaged **everything** in `dist\fans_c\`, including runtime files written
during local testing. The staging folder (`build\installer-staging\fans_c\`) is
a clean copy created by `build_exe.ps1` with runtime files stripped out.

Inno Setup now reads from staging:

```iss
Source: "..\..\build\installer-staging\fans_c\*"; Flags: recursesubdirs; Excludes: ".env,db.sqlite3,..."
```

The `Excludes` on the Inno directive is defense-in-depth only — the staging
folder should already be clean, and the safety scan should have verified it.

---

## Architecture: where runtime data lives

After installation to `C:\FANSC`:

| Data | Location | Written by |
|---|---|---|
| `.env` | `C:\FANSC\.env` | First-run wizard (step 1) |
| `db.sqlite3` | `C:\FANSC\db.sqlite3` | Django `migrate` (step 2) |
| `media\` | `C:\FANSC\media\` | App at runtime |
| `logs\` | `C:\FANSC\logs\` | Launcher on every start |
| `fans-cert.pem` | `C:\FANSC\fans-cert.pem` | mkcert (step 3) |
| `fans-cert-key.pem` | `C:\FANSC\fans-cert-key.pem` | mkcert (step 3) |
| Bundled files | `C:\FANSC\_internal\` | PyInstaller (read-only bundle) |

The bundled `_internal\` folder contains only read-only app code, templates,
static files, and third-party libraries. It never contains user data.

---

## Rebuilding after code changes

```powershell
# Stop any running instance first
wmic process where "name='fans_c.exe'" call terminate
wmic process where "name='caddy.exe'" call terminate

# Clean rebuild
.\dev\build_exe.ps1 -Clean

# Compile installer immediately — do NOT run fans_c.exe first
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" ".\dev\installer\fans_c.iss"
```

---

## What the installer bundles vs. what it does not

### Bundled (safe, read-only)
- `fans_c.exe` + PyInstaller `_internal\` (Python runtime, TensorFlow, Django apps)
- `templates\`, `static\`, `staticfiles\` (UI assets)
- `tools\caddy.exe`, `tools\mkcert\mkcert.exe`, `tools\sqlite3.exe`
- `CLIENT-SETUP\trust-local-cert.bat` and related scripts (no private keys)
- `.env.example` (template only — no secrets)
- `SETUP.md`
- `scripts\admin\` operational tools (see below — **not the whole folder**, an explicit list)

### `scripts\admin\` — bundled vs. source-only (v2.1.18 packaging audit)

`scripts\admin\` is never bundled wholesale — each file is listed individually in `fans_c.iss`'s `[Files]` section on purpose, because some of these scripts require the developer `.venv` (a full source checkout) and would fail or mislead if shipped to an installed machine that only has the frozen `fans_c.exe`.

**Bundled** — no `.venv`/`manage.py` dependency, safe on an installed machine:
| Script | Purpose |
|---|---|
| `uninstall-clean.ps1` | Full cleanup helper |
| `verify-proxy-trust.ps1` | Confirms Waitress trusted-proxy config after first launch |
| `daily-backup.ps1` | Scheduled backup (registered by first-run setup) |
| `verify-backup.ps1` | Read-only backup restore-readiness check |
| `restore-backup.ps1` | Disaster-recovery restore |
| `fans-control-center.ps1` | Single-point IT/Admin menu wrapping the tools below |
| `check-system-health.ps1` | Live status diagnostic (processes, certs, DB, media, logs) |
| `check-runtime-network.ps1` | HTTPS/proxy diagnostic (ports, Caddy forwarding) |
| `repair-autostart.ps1` | Re-registers the main autostart Task Scheduler task |
| `repair-hosts.ps1` | Re-adds the `fans-barangay.local` hosts-file entry |
| `repair-watchdog.ps1` | Re-registers the watchdog Task Scheduler task — **must ship with `watchdog.ps1`**, it registers a task pointing directly at that file |
| `watchdog.ps1` | The watchdog task's actual target script |
| `stop-fans.ps1` | Stops Waitress/Caddy for maintenance without touching autostart |

**Intentionally NOT bundled** — require the developer `.venv`/source checkout, or duplicate what the installed app already does; packaging these would give an IT admin a tool that fails or misleads on their machine:
| Script | Why it stays source-only |
|---|---|
| `run-smoke-tests.ps1` | Runs `manage.py test` against a `.venv` — also unsafe to run against a live production database |
| `verify-installation.ps1` | Explicitly checks a **manual/source-checkout** deployment (`.venv`, `py -3.11`, `manage.py check`), not an EXE-installer deployment — its own docstring says so |
| `create-admin-user.ps1` | Needs `.venv`'s `manage.py shell`; the installed machine's first admin comes from the first-run wizard, and additional admins are created from the running app's own Users page |
| `start-now.ps1` | Needs `.venv\Scripts\waitress-serve.exe`; the installed machine starts services via `fans_c.exe`/Task Scheduler instead |

### Never bundled (generated at first run on target machine)
| File | Reason |
|---|---|
| `.env` | Contains machine-specific secrets (`SECRET_KEY`, `EMBEDDING_ENCRYPTION_KEY`) |
| `db.sqlite3` | Fresh database created by `migrate` |
| `fans-cert.pem` | TLS cert is IP-specific |
| `fans-cert-key.pem` | TLS private key |
| `rootCA.pem` | Local CA installed by mkcert into Windows trust store |
| `media\` | Beneficiary photos and user-uploaded files |
| `logs\` | Runtime logs |
| Raw face / liveness images | Never stored — processed in-memory only; no image files on disk |
| LivenessTransaction images | Never stored — only the encrypted embedding vector and numeric scores are kept |

---

## Common build errors

| Symptom | Fix |
|---|---|
| `PAYLOAD SAFETY SCAN FAILED` | Stop fans_c.exe/caddy.exe, delete dist\ and build\, run `build_exe.ps1 -Clean` |
| `robocopy failed (exit code 8+)` | Check that `dist\fans_c\` was created successfully by PyInstaller |
| `ModuleNotFoundError: tensorflow` | Activate `.venv` before running the build script |
| Build hangs at "Collecting tensorflow" | Normal — TF has thousands of files; wait |
| `Create Admin not appearing on test PC` | Installer still contains `.env` or `db.sqlite3` — re-run safety scan |
| Old credentials work on test PC | `db.sqlite3` was bundled — clean build required |
