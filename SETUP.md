# FANSC Developer Setup Guide (Windows)

> For barangay staff accessing the system from a browser, see [CLIENT_ACCESS.md](CLIENT_ACCESS.md) instead.
>
> **New to this repo?** The project was reorganized for clarity. Scripts are now under `scripts/` (setup, start, admin), dev/build tools under `dev/`, and deprecated files under `legacy/`. See the folder structure below.
>
> **Current system version:** FANS-C v2.1.17 — Final Official Release (builds on branch `4.0-Final-v2.1.16-hardening`) — the development work tracked internally under the "v2.1.18" / "v2.2.0" / "Post-UAT hardening" milestone labels was folded into this v2.1.x release line rather than shipping as a separate version (see [README.md](README.md#latest-release) for the full note). See [CHANGELOG.md](CHANGELOG.md) for exact version history; do not rely on a hardcoded version number in this file. Five Django apps: `fans`, `accounts`, `beneficiaries`, `verification`, `logs`. The staff face verification gate (step-up biometric after login) was removed in v2.0 — staff log in directly to the dashboard. The `logs` app provides a permanent, structured, append-only audit trail of all system actions (read-only in Django admin as a UI-layer restriction; no cryptographic tamper-evidence such as hash chaining is implemented).

---

## Quick Setup — Barangay Server (IT)

> **This is the recommended IT setup path.** Run `setup-complete.ps1` once on the server machine. It orchestrates every required step, verifies the result of each one, and confirms the system is actually serving before declaring success.

### Prerequisites

Before running setup, ensure:

- **Python 3.11** is installed (TensorFlow does not support 3.12 or 3.13)
  Download: https://www.python.org/downloads/release/python-3119/
  During install, check **"Add Python to PATH"**
- **caddy.exe** is placed in `tools\caddy.exe` — download from https://caddyserver.com/docs/install
- **mkcert.exe** is placed in `tools\mkcert\mkcert.exe` — download from https://github.com/FiloSottile/mkcert/releases

Confirm Python 3.11 is installed:
```powershell
py -3.11 --version
```

> Tip: Keep the project at a short path like `D:\FANS`. Windows has a 260-character path limit that causes TensorFlow installs to fail on long paths.

### Step 1 — Clone the repo

Using GitHub Desktop: File → Clone Repository → paste the repo URL.

Or using git:
```powershell
git clone <repo-url> D:\FANS
cd D:\FANS
```

### Step 2 — Run the master setup (recommended)

Right-click `scripts\setup\setup-complete.ps1` and choose **Run with PowerShell** (as Admin), or:

```powershell
cd D:\FANS
.\scripts\setup\setup-complete.ps1
```

This single script runs every required setup step in order:

1. Runs `setup-secure-server.ps1` — creates `.venv`, installs dependencies, generates TLS certs, runs Django migrations
2. Verifies `fans-cert.pem` and `fans-cert-key.pem` exist
3. Verifies `caddy.exe` is found
4. Verifies `.env` has `SECRET_KEY` and `EMBEDDING_ENCRYPTION_KEY`
5. Runs `setup-autostart.ps1` — registers the Task Scheduler task for automatic startup at boot
6. Optionally creates a desktop shortcut
7. Runs a **live startup validation** — starts services and confirms ports 8000 and 443 are actually listening
8. Registers the **watchdog task** — a self-healing background monitor that automatically restarts Waitress or Caddy if either stops responding during the day

At the end, prints a clear PASS / FAIL summary for every step.

> If you get an error about execution policy, run this first:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

> **Re-running setup:** Pass `-SkipDeps` to skip the long pip install when `.venv` already exists:
> `.\scripts\setup\setup-complete.ps1 -SkipDeps`

### Step 3 — Edit .env (required for production)

After setup completes, open `.env` and set:

```
ALLOWED_HOSTS=fans-barangay.local,192.168.1.77,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://fans-barangay.local
DEBUG=False
```

Replace `192.168.1.77` with your server's actual LAN IP (shown during setup).

### Step 4 — Set up client devices

Copy the `CLIENT-SETUP\` folder to a USB drive. On each client PC, run:

```
CLIENT-SETUP\trust-local-cert.bat
```

(as Administrator — double-click and approve the UAC prompt)

### Step 5 — Daily use

After setup is complete, the daily workflow for all staff is:

1. Turn on the PC
2. Wait about 30 seconds for the system to start automatically
3. Open any browser
4. Go to `https://fans-barangay.local`
5. Log in normally

No scripts, no terminal, no troubleshooting for daily use.

The system is now **self-healing**: if Waitress or Caddy stops responding during the day, the watchdog detects it within 45 seconds and restarts the failed service automatically. No manual intervention required for daily use.

---

## Installing from the Windows Installer (FANS-C-Setup-v2.1.x.exe)

This section applies to IT admins deploying the packaged `.exe` installer on a barangay PC — not source-code developers.

### Fresh Install vs Upgrade Install

**Fresh install** — the install folder (`C:\FANSC`) does not exist or has no previous data:

1. Run `FANS-C-Setup-v2.1.x.exe` as Administrator.
2. Accept the default install path `C:\FANSC`.
3. When installation completes, launch FANS-C.
4. The 8-step first-run setup wizard appears automatically, including an optional **"Email OTP Configuration"** screen and the **"Create Admin Account"** form (see below for both).
5. Fill in the admin account details and follow the wizard through to completion.
6. The browser opens `https://fans-barangay.local` when setup is complete.

### Email OTP Configuration (optional, first-run wizard)

Early in the wizard, before `.env` is written, a screen offers to configure **self-service "forgot password" email delivery** (a 6-digit code emailed to the user). This is entirely optional:

- **Leave "Enable Email OTP delivery" unchecked** (the default) to skip it. Nothing changes from previous behavior — locked-out staff use the built-in **admin-assisted recovery** instead (a user submits a request from the login page; an admin approves it from **Settings → Account Recovery Administration**). This is the right choice for sites with no reliable internet access, which is most barangay LAN deployments.
- **Check the box** to enable it. The screen is pre-filled with FANS-C's dedicated system Gmail account (`fansc.system@gmail.com`, `smtp.gmail.com`, port `587`) — you only need to paste that account's **Gmail App Password** (see below) into the Password field. All fields can be overridden if your site uses a different mailbox or relay.

Whichever you choose, the setting is not final — you can turn email on or off later by editing `.env` directly (see "Full .env Reference" below) and restarting FANS-C; no reinstall is required.

**Generating a Gmail App Password** (only needed if using a Gmail account as the sender):
1. The Gmail account must have 2-Step Verification enabled (Google Account → Security).
2. Go to Google Account → Security → 2-Step Verification → **App passwords**.
3. Create an app password (name it e.g. "FANS-C"), copy the 16-character code shown.
4. Paste it into the wizard's Password field — spaces in the copied code are stripped automatically, so pasting it exactly as Google displays it is fine.
5. Regular Gmail login passwords do **not** work here — SMTP requires an App Password specifically.

**Restart requirement:** any change to `EMAIL_*` values in `.env` (whether made by the wizard or by hand) only takes effect the next time FANS-C's services are restarted — stop and start FANS-C (or reboot) after editing `.env` manually.

**Email appearance:** the OTP code and password-changed emails are sent as branded HTML (with a plain-text fallback for clients that don't render HTML) — navy/gold FANS-C header, a large OTP display box, expiration and one-time-use notices, and a security warning for unsolicited requests. See `templates/accounts/emails/` and `accounts/otp.py`.

**Upgrade install** — a previous FANS-C version is already installed at `C:\FANSC`:

1. Run `FANS-C-Setup-v2.1.x.exe` as Administrator.
2. The installer shows a dialog: **UPGRADE INSTALL DETECTED**.
3. All existing data is preserved: `.env`, `db.sqlite3`, `media\`, and HTTPS certificates.
4. When installation completes, launch FANS-C normally.
5. **"Create Admin Account" does NOT appear** — your existing user accounts are retained.
6. The browser opens `https://fans-barangay.local` and existing users can log in.

> **If Create Admin did not appear and you expected a fresh install:**
> You had residual data from a previous installation.
> To perform a true fresh install:
> 1. Uninstall FANS-C from Windows Add/Remove Programs.
> 2. Manually delete the install folder: `C:\FANSC`
> 3. Run the installer again.
> 4. Create Admin will appear on first launch.

### After an Upgrade Install — rootCA.pem for Client PCs

On an upgrade install, the setup wizard (including certificate generation) does **not** re-run. FANS-C automatically copies `rootCA.pem` to `_internal\CLIENT-SETUP\` on every launch once it can locate the mkcert certificate authority.

If you need to distribute rootCA.pem to new client PCs after an upgrade:

1. Launch FANS-C on the server (wait for it to fully start).
2. Open the install folder: `C:\FANSC\_internal\CLIENT-SETUP\`
3. Copy the entire `CLIENT-SETUP` folder to a USB drive.
4. Run `trust-local-cert.bat` as Administrator on each client PC.

If `rootCA.pem` is still missing from `CLIENT-SETUP` after launch, get it manually:

```powershell
# On the SERVER — run this in PowerShell
& "C:\FANSC\_internal\tools\mkcert\mkcert.exe" -CAROOT
# This prints the folder path; copy rootCA.pem from that folder.
```

> **Never copy `rootCA-key.pem` to client PCs** — that is the private key. Only `rootCA.pem` goes to clients.

### Troubleshooting: `fans-barangay.local refused to connect` (ERR_CONNECTION_REFUSED)

`ERR_CONNECTION_REFUSED` means the browser could not reach the server. This is different from a certificate warning.

**Step 1 — Is fans_c.exe running?**

Open Task Manager. Look for `fans_c.exe` in the Processes list.
If it is not running, double-click the FANS-C desktop shortcut to start it.

**Step 2 — Did FANS-C start successfully?**

After launching, wait 30–60 seconds. A small status window appears while TensorFlow loads.
If an error dialog appeared, read the message — it will state the cause.

**Step 3 — Are HTTPS certificates present?**

Check that these files exist in `C:\FANSC\`:
- `fans-cert.pem`
- `fans-cert-key.pem`

If they are missing, FANS-C will show an error dialog with instructions to delete `.env` and re-run setup (which regenerates the certificates).

**Step 4 — Is the hosts file entry present?**

FANS-C adds `fans-barangay.local` to the hosts file during setup and re-adds it automatically on every launch if missing. To verify:

```powershell
Get-Content C:\Windows\System32\drivers\etc\hosts | Select-String fans-barangay
```

If the entry is missing, run FANS-C once as Administrator — it will re-add it.

**Step 5 — Is port 443 listening?**

After FANS-C starts fully, run:

```powershell
netstat -ano | Select-String ":443"
```

If nothing shows, Caddy failed to start. Check `C:\FANSC\logs\fans-startup.log` for errors.

**Step 6 — Firewall**

Windows Firewall must allow inbound connections on port 443. FANS-C does not auto-configure the firewall. To add a rule:

```powershell
# Run as Administrator
New-NetFirewallRule -DisplayName "FANS-C HTTPS" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow
```

### Troubleshooting: Certificate Security Warning in Browser

A security warning (red lock, "Your connection is not private") means the browser does not trust the FANS-C HTTPS certificate. This is **separate from** `ERR_CONNECTION_REFUSED`.

Fix: run `trust-local-cert.bat` (from the `CLIENT-SETUP` folder) as Administrator on the affected PC.

---

## Quick Deployment Guide

> A simplified step-by-step reference for IT deploying FANSC for the first time.
> For detailed configuration, see the sections below.

---

### Part 1 — Server PC Setup (once)

**Before you start:**
- Python 3.11 installed (check "Add Python to PATH")
- `caddy.exe` in `tools\caddy.exe`
- `mkcert.exe` in `tools\mkcert\mkcert.exe`
- Logged in as Administrator
- Project at a short path, e.g. `D:\FANS`

**Steps:**

1. Open the project folder (`D:\FANS`)
2. Right-click `scripts\setup\setup-complete.ps1` → **Run with PowerShell**
3. Wait until all steps show **PASS**
4. Note the **Server IP** shown at the end (e.g., `192.168.1.77`)

The system will now auto-start every time the server PC boots.

---

### Part 2 — Staff PC Setup (once per device)

**You need:**
- The `CLIENT-SETUP` folder (copy to USB from the server)
- The server's IP address from Part 1

**Steps:**

1. Copy `CLIENT-SETUP` folder to the staff PC
2. Double-click `CLIENT-SETUP\trust-local-cert.bat` → approve the prompt
3. Open `C:\Windows\System32\drivers\etc\hosts` in Notepad **(as Administrator)**
4. Add this line at the bottom (use your actual server IP):
   ```
   192.168.1.77   fans-barangay.local
   ```
5. Save and close

> **Better alternative:** Configure "Local DNS" or "Hostname Mapping" on your office router once, and skip the hosts file step on every device entirely. See [Recommended Network Setup](#recommended-network-setup-no-per-device-configuration) below.

---

### Part 3 — Daily Use

1. Turn on the **server PC**, wait ~30 seconds
2. Open any browser on a staff device
3. Go to: **`https://fans-barangay.local`**
4. Log in and begin

No scripts. No terminal. No IT involvement for daily use.

---

## Script Reference

| Script | Who runs it | When |
|---|---|---|
| `scripts\setup\setup-complete.ps1` | IT | **Once** — recommended master setup entry point |
| `scripts\setup\setup-secure-server.ps1` | IT | Once (called by setup-complete, or standalone) |
| `scripts\setup\setup-autostart.ps1` | IT | Once (called by setup-complete, or standalone) |
| `scripts\setup\Create-Desktop-Shortcut.ps1` | IT | Optional, once |
| `CLIENT-SETUP\trust-local-cert.bat` | IT | Once per client device |
| `scripts\start\start-fans-hidden.ps1` | Task Scheduler | Called automatically at boot — never run manually |
| `scripts\start\start-fans-quiet.bat` | IT | Manual start (if auto-start not configured) |
| `scripts\start\start-fans.bat` | IT | Debug start (visible windows, full output) |
| `scripts\admin\fans-control-center.ps1` | IT | All-in-one admin menu: start/stop/restart, health check, logs, repair, admin user |
| `scripts\admin\check-system-health.ps1` | IT | Live health diagnostic — read-only, checks every component |
| `scripts\admin\watchdog.ps1` | Task Scheduler | Called automatically 150s after boot — never run manually |
| `scripts\admin\start-now.ps1` | IT | Start services now without rebooting (no setup re-run) |
| `scripts\admin\stop-fans.ps1` | IT | Stop Waitress and Caddy cleanly |
| `scripts\admin\repair-autostart.ps1` | IT | Re-register auto-start Task Scheduler task only |
| `scripts\admin\repair-watchdog.ps1` | IT | Re-register watchdog Task Scheduler task only |
| `scripts\admin\repair-hosts.ps1` | IT | Add fans-barangay.local to server hosts file (targeted fix) |
| `scripts\admin\create-admin-user.ps1` | IT | Create or add a Django admin account |
| `scripts\admin\verify-installation.ps1` | IT | 18-check installation health script (pre-deployment) |
| `scripts\admin\run-smoke-tests.ps1` | IT | 6 non-destructive pre-deployment smoke tests |

---

## Running Tests

The project has a Django test suite that covers URL routing, access control, model creation, role helpers, and password validation. No webcam or TensorFlow model is required.

```powershell
# Run all tests
.\.venv\Scripts\python.exe manage.py test fans accounts logs verification --verbosity=1

# Run tests for a single app
.\.venv\Scripts\python.exe manage.py test accounts
```

Test files are at:
- `fans/tests.py` — settings, URL resolution, unauthenticated access
- `accounts/tests.py` — role helpers, password validator, login view
- `logs/tests.py` — AuditLog model, audit view access
- `verification/tests.py` — verification URL access, Beneficiary model

All tests should pass on a correctly configured development environment — run `python manage.py test` and check for `OK`; see [CHANGELOG.md](CHANGELOG.md) for the test count as of the latest release (it changes with every release, so it is not hardcoded here). FaceNet is not imported during any test — the suite is safe to run offline with no model weights present.

---

## Quick Setup — Developer

For a developer on a fresh Windows laptop running the app locally:

### Step 1 — Clone

```powershell
git clone <repo-url> D:\FANS
cd D:\FANS
```

### Step 2 — Run setup

```powershell
.\scripts\setup\setup-secure-server.ps1
```

### Step 3 — Start the server

For a visible debug start (shows all output):
```powershell
.\scripts\start\start-fans.bat
```

For normal daily use (minimized background services):
```powershell
.\scripts\start\start-fans-quiet.bat
```

Or manually:
```powershell
.\.venv\Scripts\Activate.ps1
python manage.py runserver
```

### Step 4 — Open the app

| Mode | URL | Notes |
|---|---|---|
| **Production (installer / Waitress + Caddy)** | `https://fans-barangay.local` | HTTPS, webcam + liveness work on the LAN. |
| **Dev — same laptop** | `http://127.0.0.1:8000/` *(or `http://localhost:8000/`)* | Browser treats localhost as a secure context, so the camera/liveness flow works over plain HTTP. |
| **Dev — LAN / multi-device** | `https://fans-barangay.local` (via Caddy + `runserver`) | Plain LAN HTTP (e.g. `http://192.168.x.x:8000`) is **not supported** for the camera/liveness flow — browsers block `getUserMedia` outside a secure context. See [docs/DEV-HTTPS.md](docs/DEV-HTTPS.md). |

> **`python manage.py runserver` is HTTP-only.** It has no TLS support.
> For same-laptop dev that's fine — the browser's localhost exemption
> lets the camera open. For LAN / phone testing, front `runserver` with
> Caddy (`caddy run --config Caddyfile`) and browse to
> `https://fans-barangay.local`. The full step-by-step (mkcert,
> hosts file, `.env`) is in [docs/DEV-HTTPS.md](docs/DEV-HTTPS.md).
>
> **End users who installed FANS-C via the installer do not need any of
> this** — the installer ships the Waitress + Caddy HTTPS stack and runs
> it automatically.

Log in with the admin account you created during setup.

---

## That's it for development mode.

The steps above are everything needed to run the system locally. The sections below cover optional configuration and production deployment.

---

## Detailed Setup

### Installing Python 3.11

1. Download from: https://www.python.org/downloads/release/python-3119/
2. Run the installer
3. Check **"Add Python to PATH"** on the first screen
4. Click Install Now

After install, confirm:
```powershell
py -3.11 --version
```

**Why Python 3.11 specifically?** `tensorflow-cpu 2.13.x` (used by keras-facenet) does not support Python 3.12 or 3.13. Using the wrong version produces a DLL load error at runtime, even if pip install succeeds.

### Path Length Warning

Windows limits file paths to 260 characters by default. TensorFlow's wheel contains deeply nested files that exceed this limit. The symptom is pip failing with `No such file or directory` on a path that clearly exists.

**Option 1 (recommended):** Clone to a short path like `D:\FANS`.

**Option 2:** Enable long path support (requires Admin PowerShell):
```powershell
reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f
```
Restart Windows, then retry.

### Manual Setup (without setup-secure-server.ps1)

If you prefer to set up manually instead of using `setup-secure-server.ps1`:

```powershell
# Create virtual environment
py -3.11 -m venv D:\FANS\.venv
D:\FANS\.venv\Scripts\Activate.ps1

# Upgrade pip
python -m pip install --upgrade pip

# Install dependencies (includes openpyxl for Excel export)
pip install -r requirements.txt
```

Copy and edit the environment file:
```powershell
copy .env.example .env
```

Minimum `.env` values for local development:
```
SECRET_KEY=fans-demo-secret-change-for-production
DEBUG=True
USE_SQLITE=True
DEMO_MODE=True
LIVENESS_REQUIRED=False
DEMO_THRESHOLD=0.60
ANTI_SPOOF_THRESHOLD=0.15
MAX_RETRY_ATTEMPTS=1
```

Generate an encryption key (do this once — losing it makes stored face data unreadable):
```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Paste the output into `.env` as `EMBEDDING_ENCRYPTION_KEY=<value>`.

Run migrations and finish setup:
```powershell
python manage.py migrate
python manage.py init_config
python manage.py createsuperuser
python manage.py collectstatic --noinput
python manage.py runserver 127.0.0.1:8000
```

> **Webcam/liveness requires a secure context.** `runserver` speaks HTTP
> only, so the camera flow only works when the browser sees a
> "localhost" origin (`http://127.0.0.1:8000/` or `http://localhost:8000/`).
> Raw LAN HTTP (`http://192.168.x.x:8000`) is **not supported** for
> camera/liveness — see [docs/DEV-HTTPS.md](docs/DEV-HTTPS.md) for the
> Caddy-fronted HTTPS dev setup if you need multi-device testing.

### Full .env Reference

| Variable | Description | Development value |
|---|---|---|
| `SECRET_KEY` | Django secret key | Any random string |
| `DEBUG` | Enable debug mode | `True` |
| `USE_SQLITE` | Use SQLite instead of PostgreSQL | `True` |
| `DEMO_MODE` | Lower thresholds, non-blocking liveness | `True` |
| `LIVENESS_REQUIRED` | Block verification if liveness fails | `False` |
| `DEMO_THRESHOLD` | Face similarity threshold in demo mode | `0.60` |
| `ANTI_SPOOF_THRESHOLD` | Anti-spoof confidence cutoff | `0.15` |
| `PAD_REQUIRED` | Enforce presentation-attack (phone/screen/photo) detection | `True` (production refuses to start if `False`) |
| `PRESENTATION_ATTACK_REVIEW_OR_DENY` | What a suspected presentation attack does | `deny` (production refuses to start with anything else, e.g. `review`) |
| `STRICT_PRESENTATION_ATTACK_CHECK` | Whether the PAD score can actually deny (not just log) | `True` (production refuses to start if `False`) |
| `PHONE_SCREEN_SPOOF_THRESHOLD` | Phone/screen composite PAD denial threshold | `0.40` (production refuses to start below `0.10`) |
| `MAX_RETRY_ATTEMPTS` | Retries before failing a verification | `2` |
| `EMBEDDING_ENCRYPTION_KEY` | Fernet key for stored face embeddings | Generate once |
| `ALLOWED_HOSTS` | Comma-separated allowed hostnames | `localhost,127.0.0.1` |
| `VERIFICATION_THRESHOLD` | Face similarity threshold in strict mode | `0.75` |
| `CSRF_TRUSTED_ORIGINS` | Required for HTTPS form POSTs via Caddy | `https://fans-barangay.local` |
| `SECURE_PROXY_SSL_HEADER` | Tell Django the real protocol from Caddy | `HTTP_X_FORWARDED_PROTO,https` |
| `USE_X_FORWARDED_HOST` | Use Host header forwarded by Caddy | `True` |
| `SECURE_COOKIES` | Force Secure flag on session/CSRF cookies | `False` for dev, omit for production (auto) |
| `CONN_MAX_AGE` | DB connection keep-alive seconds (PostgreSQL) | `0` for dev, `60` for production |
| `SYNC_API_URL` | Central sync server URL (offline mode only) | leave empty for centralized deployment |
| `SYNC_API_KEY` | Bearer token for sync API | leave empty |
| `LOGIN_MAX_FAILED_ATTEMPTS` | Login lockout threshold | `8` |
| `LOGIN_LOCKOUT_WINDOW_S` | Window for counting failures (seconds) | `600` |
| `LOGIN_LOCKOUT_DURATION_S` | Lockout duration (seconds) | `900` |
| `EMAIL_HOST` | SMTP server for self-service password-reset OTP emails | empty (disables email; leave empty on offline/LAN-only sites) |
| `EMAIL_PORT` | SMTP port | `587` |
| `EMAIL_HOST_USER` | SMTP account username (also required for `EMAIL_CONFIGURED` to be true) | empty |
| `EMAIL_HOST_PASSWORD` | SMTP account password / app password | empty |
| `EMAIL_USE_TLS` | Use STARTTLS for the SMTP connection | `True` |
| `DEFAULT_FROM_EMAIL` | "From" address on OTP / password-changed emails | `no-reply@fans-c.local` |

> **SECURE_COOKIES behaviour:** When omitted from .env, the system automatically sets Secure cookies when DEBUG=False (production) and disables them when DEBUG=True (development). Only set this explicitly to override the default.

> **Email OTP:** the installer's first-run wizard can write these for you (see "Email OTP Configuration" above). To change them on an existing install, edit `.env` directly and restart FANS-C. `EMAIL_HOST` and `EMAIL_HOST_USER` must both be set for email delivery to activate (`EMAIL_CONFIGURED`); leaving either empty keeps the system on admin-assisted recovery only, with no error shown to end users (this is intentional — see `docs/PASSWORD-RECOVERY-ARCHITECTURE.md`).

### Database Setup (PostgreSQL)

By default the project uses SQLite (`db.sqlite3` in the project root). This is fine for capstone demos.

To use PostgreSQL:
1. Install PostgreSQL and create a database
2. In `.env`, set `USE_SQLITE=False` and add:
   ```
   DB_NAME=fans_db
   DB_USER=fans_user
   DB_PASSWORD=yourpassword
   DB_HOST=localhost
   DB_PORT=5432
   ```
3. `psycopg2-binary` is already installed — no change to requirements.txt needed.
4. Reinstall: `pip install -r requirements.txt`
5. Run `python manage.py migrate`

### Admin Account Setup

`setup-secure-server.ps1` calls `create_admin` interactively. If you need to set a role manually:

```powershell
python manage.py shell -c "
from accounts.models import CustomUser
u = CustomUser.objects.get(username='yourusername')
u.role = 'president'  # or 'admin', 'it', 'staff'
u.save()
print('Role set for:', u)
"
```

**Active roles:** `president` (President), `admin` (Admin), `it` (IT), `staff` (Staff).

Legacy roles `head_brgy` and `admin_it` are no longer assignable. Existing accounts were automatically migrated via `accounts/0009` and `accounts/0006`.

Django's `is_superuser`/`is_staff` (set by `createsuperuser`) and the FANSC `role` field are separate. Both must be set for full access to the Django `/admin/` panel.

---

## Production Deployment (Waitress + Caddy)

For barangay deployment on a LAN server, run Django with Waitress behind Caddy for HTTPS.

### Prerequisites

```powershell
pip install waitress
```

Caddy must be installed (single `.exe`) and TLS certificates must be generated with `mkcert`.
See the `Caddyfile` in the project root for the full checklist.

### Collect static files (once before first production start)

Bootstrap 5.3.2 vendor files are committed to the repository under
`static/vendor/`, so no manual download is required on a fresh clone.
Running `collectstatic` will work out of the box.

```powershell
python manage.py collectstatic --noinput
```

### Start the system (two PowerShell windows)

**Window 1 — Waitress:**
```powershell
cd D:\FANS
.\.venv\Scripts\Activate.ps1
waitress-serve --listen=127.0.0.1:8000 fans.wsgi:application
```

**Window 2 — Caddy:**
```powershell
cd D:\FANS
caddy run --config Caddyfile
```

### Required .env values for HTTPS

```
DEBUG=False
ALLOWED_HOSTS=fans-barangay.local,192.168.1.77,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://fans-barangay.local
SECURE_PROXY_SSL_HEADER=HTTP_X_FORWARDED_PROTO,https
USE_X_FORWARDED_HOST=True
```

### Verify production configuration

After configuring .env for production, run the security check:

```powershell
python manage.py check --deploy
```

Expected result:
```
System check identified no issues (4 silenced).
```

The 4 silenced checks (W004, W008, W012, W016) are intentionally suppressed because Caddy handles HSTS, SSL redirect, and secure cookies at the proxy layer. Any additional warnings (not in the silenced list) require attention before going live.

If you see W009 (SECRET_KEY too short), generate a proper key:
```powershell
python -c "import secrets; print(secrets.token_urlsafe(50))"
```
Paste the output into .env as `SECRET_KEY=<value>`.

### How HTTPS proxy detection works

FANS-C runs behind Caddy, which terminates HTTPS/TLS and forwards plain HTTP to Waitress on `127.0.0.1:8000`. Django sees an HTTP request internally, so `request.is_secure()` returns False unless told otherwise.

**What makes it work:** The `Caddyfile` adds `X-Forwarded-Proto: https` to every proxied request. Django reads this header and sets `request.is_secure() = True` when `SECURE_PROXY_SSL_HEADER = HTTP_X_FORWARDED_PROTO,https` is in `.env` (or defaulted to this value since v2.0.7).

**Why this is safe:** Waitress only listens on `127.0.0.1` (loopback). No external client can connect directly to port 8000 and forge the `X-Forwarded-Proto` header. Only Caddy can send requests to Waitress.

**Limited HTTP mode banner:** The banner `"Limited HTTP mode — camera verification requires secure HTTPS"` appears when `request.is_secure()` is False. It disappears when:
1. The request came through Caddy (HTTPS), AND
2. Django is configured to trust the forwarded protocol header.

### Troubleshooting: "Limited HTTP mode" still shows on HTTPS

If the Limited HTTP mode banner still appears after accessing via `https://fans-barangay.local`:

**Step 1 — Check Django's view of the request**

Log in as IT Admin and go to: `https://fans-barangay.local/system/connection/`

Look at the **HTTPS Proxy Diagnostics** card. It shows:
- `request.scheme` — should be `https` (if it's `http`, Django isn't trusting the header)
- `X-Forwarded-Proto` — should be `https` (if blank, Caddy isn't sending the header)
- `SECURE_PROXY_SSL_HEADER` — should be `HTTP_X_FORWARDED_PROTO, https` (if `None`, settings.py isn't loading it)
- Camera verification — should show `Allowed`

**Step 2 — Check `.env`**

Open `.env` in the installation folder and confirm:
```
SECURE_PROXY_SSL_HEADER=HTTP_X_FORWARDED_PROTO,https
USE_X_FORWARDED_HOST=True
CSRF_TRUSTED_ORIGINS=https://fans-barangay.local
```

Since v2.0.7, `SECURE_PROXY_SSL_HEADER` defaults to `HTTP_X_FORWARDED_PROTO,https` in code, so it only needs to be in `.env` if the default was ever overridden. If it's set to `off` or `false`, clear it.

**Step 3 — Confirm Caddy is actually running**

```powershell
.\scripts\admin\check-system-health.ps1
```

If Caddy is not running, `request.scheme` will always be `http` because requests reach Waitress directly without the forwarded-proto header.

**Step 4 — Restart to pick up .env changes**

```powershell
.\scripts\admin\stop-fans.ps1
.\scripts\admin\start-now.ps1
```

### What `trust-local-cert.bat` does (and does not do)

`trust-local-cert.bat` installs the server's `rootCA.pem` into the Windows Trusted Root Certification Authorities store. This makes the browser trust the FANSC HTTPS certificate (removes the "Your connection is not private" warning).

It does **not**:
- Fix the "Limited HTTP mode" banner (that is a Django/proxy configuration issue, not a certificate issue)
- Configure hostname resolution (separate hosts-file or router DNS step)
- Make camera verification work on its own (requires HTTPS proxy headers to be correct)

Run `trust-local-cert.bat` once per client device. After it runs, the browser padlock appears. If the Limited HTTP mode banner still appears after that, follow the troubleshooting steps above.

### What remains manual on the server

| Task | When |
|---|---|
| Run `mkcert -install` (as Admin) | Once per server machine — creates the server's local CA |
| Run `mkcert fans-barangay.local 192.168.1.77 localhost 127.0.0.1` | Once; redo only if cert expires or IP changes |
| Run `mkcert -CAROOT` and copy `rootCA.pem` to each client device | Once; distribute to clients alongside `trust-local-cert.bat` |
| On each client: run `trust-local-cert.bat` (as Admin) | Once per client device — imports the **server's** rootCA.pem into Windows trust store |
| Add firewall rule for port 443 (as Admin) | Once per server machine |
| Start Waitress and Caddy after each reboot | Every time the server restarts (see startup scripts below) |
| Configure hostname resolution on client devices | Once-total if router DNS is used; once per device if hosts file fallback is used (see CLIENT_ACCESS.md) |

> **Certificate trust model:** The server generates the HTTPS certificate with its own mkcert CA. Client devices must trust the **server's** CA (`rootCA.pem`) — they do not generate or install their own mkcert CA. Running `mkcert -install` on a client machine creates a different CA that does not sign the server's certificate and does not fix the browser warning.

---

## Starting the Server After a Reboot

The project does **not** need to be reinstalled after every reboot. The `.venv`, `.env`, database, and certificates all remain in place. Only the two server processes — Waitress and Caddy — need to be started again each time the server PC is turned on.

### Option 1 — Auto-start via Task Scheduler (recommended)

If you ran `setup-complete.ps1`, the Task Scheduler task is already registered. The system starts automatically at every boot — no scripts, no windows, no IT visits required.

To verify the task is registered:
```powershell
.\scripts\admin\check-system-health.ps1
```

To register the task manually (if not already done):
```powershell
.\scripts\setup\setup-autostart.ps1
```

### Option 2 — Double-click startup (manual, with verification)

For normal manual start (minimized background, verifies ports after startup):

```
scripts\start\start-fans-quiet.bat
```

This now verifies that ports 8000 and 443 are actually listening before reporting success.

For debugging (all server windows visible, full output):

```
scripts\start\start-fans.bat
```

### Option 3 — Desktop shortcut

Run once to create a branded shortcut on the Desktop:

```powershell
.\scripts\setup\Create-Desktop-Shortcut.ps1
```

The shortcut points to `scripts\start\start-fans-quiet.bat` with the correct working directory.

### Option 4 — Manual startup

If you prefer to start manually each time:

**Window 1 — Waitress:**
```powershell
cd D:\FANS
.\.venv\Scripts\Activate.ps1
waitress-serve --listen=127.0.0.1:8000 fans.wsgi:application
```

**Window 2 — Caddy (run from project root so cert paths resolve):**
```powershell
cd D:\FANS
caddy run --config Caddyfile
```

### Diagnosing startup problems

If the browser shows `ERR_CONNECTION_REFUSED` or `This site can't be reached`:

```powershell
.\scripts\admin\check-system-health.ps1
```

This reports which services are running, which ports are listening, whether certs are present, and shows the last startup log and recent watchdog activity — without changing anything.

---

## Self-Healing Watchdog

The watchdog is a background monitor registered automatically by `setup-complete.ps1`. It runs continuously after every boot and catches failures that startup validation cannot — problems that happen hours into the day while the system is in use.

### What it does

- Checks Waitress and Caddy every **45 seconds**
- If Waitress is down: stops any stale process and starts a clean replacement
- If Caddy is down: stops any stale process and starts a clean replacement
- Confirms recovery by re-checking the port after restart

### Safeguards

| Safeguard | Detail |
|---|---|
| No duplicate processes | Kills any stale instance before starting a new one |
| Pre-restart port check | If the port recovered on its own, skips the restart |
| 60-second cooldown | Waits 60 seconds between successive restart attempts |
| Max 3 restarts per 10 minutes | Stops trying after 3 failures in a 10-minute window |
| ALERT on repeated failure | Logs a clear ALERT message and instructs IT to inspect |
| Auto-reset | If services recover (e.g., IT manually fixed them), resets failure counters |

### Log file

```
logs\fans-watchdog.log
```

Entries use these levels:

| Level | Meaning |
|---|---|
| `HEALTHY` | All services OK (logged every ~7.5 minutes to reduce noise) |
| `WARN` | A service is not responding |
| `ACTION` | A restart is being attempted |
| `OK` | Recovery was successful |
| `FAIL` | Restart was attempted but service still not responding |
| `ALERT` | Max restart attempts reached — IT inspection required |
| `WAIT` | Cooldown period — restart deferred |
| `SKIP` | Recovery skipped (already gave up after repeated failures) |

### How to read ALERT entries

If the watchdog log shows `[ALERT]` entries:

1. Run `scripts\admin\check-system-health.ps1` to see current state
2. Run `scripts\start\start-fans.bat` to see the full error output from Waitress or Caddy
3. After fixing the underlying issue, reset the watchdog by restarting the `FANS-C Watchdog` task in Windows Task Scheduler

### Task Scheduler details

| Property | Value |
|---|---|
| Task name | `FANS-C Watchdog` |
| Trigger | System startup + 150-second delay |
| Account | SYSTEM (no UAC, always elevated) |
| Window | Hidden (no visible window) |
| Auto-restart if crashed | Yes (up to 3 times, 2-minute interval) |

The 150-second delay ensures the main startup task (`FANS-C Verification System`) has fully started Waitress and Caddy (and the FaceNet model has loaded) before the watchdog takes its first reading.

---

## Recommended Network Setup (No Per-Device Configuration)

This section explains the best-practice network setup for a real barangay deployment. The goal is simple: once the server is configured, staff just connect to the office Wi-Fi and open one link in a browser — no one has to touch their individual devices.

### The problem with editing hosts files on every device

The default approach (editing `C:\Windows\System32\drivers\etc\hosts` on each PC) works, but has real drawbacks in practice:

- Every new staff device requires an admin to manually edit a system file
- If the server IP ever changes, every device must be updated again
- Android and iOS devices do not support hosts file editing at all
- It requires running Notepad as Administrator — staff cannot do it themselves

### The better approach: configure the router once

A standard office Wi-Fi router can be configured to handle both problems — stable IP assignment and automatic domain name resolution — in one place, for the whole network.

---

### A. DHCP Reservation — Give the Server a Stable IP

**What is DHCP?**
DHCP (Dynamic Host Configuration Protocol) is the service your router uses to automatically assign IP addresses to devices that join the network. By default, these addresses can change — your server PC might get `192.168.1.77` today and `192.168.1.82` after a reboot.

**What is a DHCP Reservation (also called a Static Lease)?**
A DHCP reservation tells the router: "always give this specific device the same IP address, every time." The device is identified by its MAC address (a permanent hardware identifier burned into its network card).

**Why this is better than setting a static IP in Windows:**

| Approach | How it works | Drawback |
|---|---|---|
| Windows static IP | You manually type the IP into Windows Network Settings | Breaks if the router's DHCP range overlaps; can cause IP conflicts; requires knowing the correct gateway and DNS values |
| Router DHCP Reservation | Router always hands out the same IP to the server's MAC address | Zero risk of IP conflict; router manages everything; survives Windows reinstall |

**How to set it up (generic steps — exact menu names vary by router brand):**

1. Log in to your router admin panel. The address is usually `192.168.1.1` or `192.168.0.1` — type it in a browser on any device connected to the router.
2. Look for a section called **DHCP**, **LAN**, or **Network Settings**.
3. Inside it, find **DHCP Reservations**, **Static Leases**, or **Address Reservation**.
4. Find the server PC's MAC address. You can find it on the server PC by running this in Command Prompt:
   ```
   ipconfig /all
   ```
   Look for **Physical Address** under your active network adapter (Wi-Fi or Ethernet). It looks like: `A4-B1-C2-D3-E4-F5`
5. Enter the MAC address and choose a fixed IP (e.g., `192.168.1.150`). Pick a number that is outside the router's normal DHCP range (e.g., if DHCP range is `.100` to `.149`, pick `.150`).
6. Save and apply. Restart the server PC — it should now always get that IP.

> **What this achieves:** The server's IP address never changes, even after a reboot or power cut. You never need to update any configuration because of an IP change.

---

### B. Local DNS / Hostname Mapping — Resolve the Domain Automatically

**What is DNS?**
DNS (Domain Name System) is the service that translates a name like `fans-barangay.local` into an IP address like `192.168.1.150`. Normally this happens via public internet DNS servers. For local names, the router can handle it instead.

**What does "hostname mapping" mean?**
It means telling the router: "when any device on this network asks what IP `fans-barangay.local` is, respond with `192.168.1.150`."

Once this is set on the router, every device on the network — Windows PCs, laptops, tablets, phones — can automatically resolve `fans-barangay.local` without any changes to the device itself.

**How to set it up (generic steps):**

1. Log in to your router admin panel.
2. Look for a section called **DNS**, **Advanced DNS**, **Local DNS**, **Hostname Mapping**, or **Static Hosts**. This varies by router — check under Advanced Settings if you do not see it immediately.
3. Add an entry:
   - Hostname / Domain: `fans-barangay.local`
   - IP Address: the fixed IP you assigned in step A (e.g., `192.168.1.150`)
4. Save and apply.

> **What this achieves:** Every device that joins the office Wi-Fi can immediately reach `https://fans-barangay.local` — no hosts file edits, no per-device setup, no IT visit needed for new devices.

---

### Does every router support this?

Most modern home and office routers support DHCP reservations. Local DNS / hostname mapping is less universal but available on most branded routers (TP-Link, ASUS, Netgear, D-Link, Mikrotik, Ubiquiti, and most ISP-provided routers).

If the router does not support local DNS entries, use the hosts file fallback described in the next section.

---

### Comparison: Which approach is right for your barangay?

| Method | Setup Effort | Maintenance | Works on mobile? | Recommended |
|---|---|---|---|---|
| Hosts file (manual) | Per device — IT must visit each PC | High — must redo if IP changes | No (iOS/Android) | Fallback only |
| Static IP in Windows | One device | Medium — risk of IP conflict | N/A | Not recommended |
| Router DHCP Reservation + Local DNS | One-time on the router | Low — set and forget | Yes | **BEST** |

---

### Fallback / Manual Setup (for testing or when router config is not available)

If router DNS configuration is not available, use the hosts file method on each client device:

1. Open Notepad **as Administrator** on the client device
2. Open the file: `C:\Windows\System32\drivers\etc\hosts`
3. Add this line at the bottom (replace the IP with your server's actual IP):
   ```
   192.168.1.150   fans-barangay.local
   ```
4. Save the file. Open a new browser tab and go to `https://fans-barangay.local`.

This must be done once per device. If the server IP ever changes, every device's hosts file must be updated.

See [CLIENT_ACCESS.md](CLIENT_ACCESS.md) for the staff-facing version of these instructions.

---

## LAN vs Internet — What This System Actually Needs

This system is **LAN-based (on-premise)**. It runs on a server PC inside the barangay office and is accessed by staff using browsers on the same local Wi-Fi or wired network.

| Scenario | System status |
|---|---|
| Server PC is on, LAN is working, internet is down | **System works normally.** No internet required. |
| Server PC is on, internet is working | System also works (internet is irrelevant to operation). |
| Server PC is off | System is unavailable. Staff cannot log in until the server is restarted. |
| Staff device loses Wi-Fi but server is still on | Staff device cannot reach the system until Wi-Fi reconnects. |

### What requires internet?

- **FaceNet model download** — only on the very first startup after setup. After that it is cached locally.
- **Software updates** — only when manually updating the project.
- **Nothing else.** Day-to-day operation is 100% local.

### Staff PCs

Staff PCs do not need Python, PostgreSQL, GitHub Desktop, or any project files. They only need:
- A web browser (Chrome, Edge, or Firefox)
- Connection to the same Wi-Fi or LAN as the server PC
- Domain resolution for `fans-barangay.local` — handled automatically if the router is configured with a local DNS entry (recommended), or done once per device via the hosts file (fallback)

---

## Troubleshooting

### DLL Load Failure

```
DLL load failed while importing _pywrap_tensorflow_lite_metrics_wrapper
```

**Cause:** Wrong Python version. Your venv was created with Python 3.12 or 3.13.

**Fix:** Delete `.venv`, install Python 3.11, recreate the venv with `py -3.11 -m venv .venv`, reinstall.

### keras-facenet Import Error

```
Cannot import name 'xxx' from 'keras'
```

**Cause:** keras-facenet requires Keras 2 (bundled with TF 2.13.x). TF 2.16+ bundles Keras 3, which breaks it.

**Fix:** Stay on `tensorflow-cpu>=2.13.0,<2.14.0` with Python 3.11.

### TensorFlow Install Fails with Long Path Error

```
OSError: [Errno 2] No such file or directory: ...memory_allocator_impl.h
```

**Fix:** Move the project to `D:\FANS` and retry, or enable long path support (see above).

### Face Verification Is Blocked / "Model Unavailable"

FANS-C fails closed if the real FaceNet model cannot be loaded — it never falls back to random/mock similarity scores. If registration or verification returns "Face recognition model is unavailable," go to `/verification/config/`; the "Face Recognition Model Status" card shows the underlying error. Check it and follow the TF troubleshooting steps above, or (on a clean install) confirm this machine has internet access for the one-time FaceNet weights download, then restart FANS-C.

### Recovery After Moving the Project Folder

Virtual environments store absolute paths and break after a folder move.

1. Delete the old `.venv`: `rmdir /s /q .venv`
2. Move the project to a short path (e.g., `D:\FANS`)
3. Re-run `.\scripts\setup\setup-secure-server.ps1`

### NoReverseMatch error on dashboard or any page

```
NoReverseMatch: Reverse for 'face_verify' not found
```

**Cause:** A view is referencing a URL that was removed or commented out. This specific error means a leftover reference to the staff face verification gate exists somewhere in the codebase.

**Fix:**
```powershell
python manage.py check
```
Then search for the reference:
```powershell
Select-String -Path "**\*.py","**\*.html" -Pattern "face_verify" -Recurse
```
Remove or comment out every reference found outside of the face_verify view itself and its template.

### Internal Server Error (500) on first load after config change

**Cause:** Waitress is still serving the old code. Waitress does not auto-reload when files change.

**Fix:** Stop and restart the server:
```powershell
.\scripts\admin\stop-fans.ps1
.\scripts\start\start-fans.bat
```

### System check shows W009 (SECRET_KEY too short or insecure)

**Cause:** .env has a placeholder or short SECRET_KEY value.

**Fix:** Generate a proper key and paste it into .env:
```powershell
python -c "import secrets; print(secrets.token_urlsafe(50))"
```
Then restart the server.

---

## Usage Modes

### Developer Mode

- Full setup required (Python, venv, requirements, `.env`)
- Used for development, testing, and running the server
- Access via: http://127.0.0.1:8000/ (or HTTPS if Waitress+Caddy are running)

### Client Mode (Barangay Staff)

- No installation required
- Access the system from any browser on the same LAN
- URL: `https://fans-barangay.local`
- See [CLIENT_ACCESS.md](CLIENT_ACCESS.md) for setup instructions

---

## Liveness vs. Face Verification

These are two separate security checks that run in sequence. Both are required.

| Check | What it confirms |
|---|---|
| **Liveness** | The camera sees a real live person — not a phone screen, printed photo, or replay video |
| **Face Verification** | That live person matches the registered beneficiary (or their representative) |

In strict mode (`LIVENESS_REQUIRED=True`, the default), face matching never runs if liveness fails. The server makes the final liveness decision. Client-side scores (browser MediaPipe) are advisory only.

**Final verification:** The head-movement challenge ('side' direction) is **always required** before FaceNet runs. There is no fast path for high anti-spoof scores. Challenge timeout is a denial.

**Liveness baseline:** Baseline yaw is captured into a dedicated `_baselineYaw` variable after 10 stable frames. Console logs show `baseYaw=<number>` confirming capture. The `baseYaw=n/a` issue in prior builds is resolved.

**Two-phase LivenessTransaction (v2.1.x):** After the challenge completes, `verify_check_liveness` issues a one-time `tx_token` that binds the liveness proof to the FaceNet embedding computed from the **neutral/frontal frame** (not the angled challenge frame). `verify_submit` only runs face matching after consuming a valid token. The token is never issued if the embedding step fails, if anti-spoof fails on the neutral frame, or if fewer than 3 sequence frames are submitted.

**Registration liveness** is also enforced server-side. Anti-spoof score and an optional head-movement challenge run before any face embedding is saved. The challenge is risk-based for registration only — required when the anti-spoof score is below 0.30 or face quality is poor. The hard anti-spoof threshold (0.25) always rejects phone screens regardless of challenge.

## Demo Mode vs. Model Unavailable

These are different things.

| What | Meaning |
|---|---|
| `DEMO_MODE=True` | Uses a lower threshold and non-blocking liveness. Real face matching still runs if the model loaded. |
| Model unavailable | keras-facenet failed to load (or, on a clean machine, the FaceNet weights haven't downloaded yet). FANS-C fails closed: registration and verification are blocked with a clear message. There is no mock/random-score mode in production — the system never guesses. |
| `DEMO_MODE=False` + model loaded | Production strict mode — 0.75 threshold, liveness blocks verification |

`DEMO_MODE` is a threshold/UX setting. The model being unavailable is a system availability failure, not a UX setting — it blocks biometric operations rather than degrading them.

> Set `DEMO_MODE=True` during initial rollout (assisted mode, 0.60 threshold, non-blocking liveness). Switch to `DEMO_MODE=False` only after validating FaceNet performance on your hardware and enrollment quality.

---

## User Roles

| Role | DB Value | Permissions |
|---|---|---|
| **President** | `president` | All operational tasks: verify, register, approve claims and manual reviews, manage users, run reports, reset passwords for all roles. |
| **Admin** | `admin` | Administrative tasks: register, approve claims/manual-reviews, manage users and officer assignments, run reports, reset Staff passwords. |
| **IT** | `it` | All President and Admin permissions + system diagnostics, connection info, technical setup pages. |
| **Staff** | `staff` | Register beneficiaries, run verification, submit manual-review or special-claim requests. No user management, no reports, no approvals. |

**Legacy roles (historical reference only — no longer assignable):**

| Legacy Role | DB Value | Notes |
|---|---|---|
| Head Barangay | `head_brgy` | Migrated → `president` (accounts/0009) |
| IT | `admin_it` | Migrated → `admin` (accounts/0006) |

Staff log in directly to the dashboard after password authentication.
No additional biometric step-up is required for system access.

To create users, use:
```powershell
scripts\admin\create-admin-user.ps1
```
Or via the web UI: Admin → User Management → Create User.

To set a role manually via the Django shell:
```powershell
python manage.py shell -c "
from accounts.models import CustomUser
u = CustomUser.objects.get(username='yourusername')
u.role = 'president'  # or 'admin', 'it', 'staff'
u.save()
print('Role set for:', u)
"
```

Active roles: `president`, `admin`, `it`, `staff`.
Do NOT assign the legacy `head_brgy` or `admin_it` values to new users.

---

## ML Model Notes

| Model | How it loads |
|---|---|
| FaceNet (keras-facenet) | Downloaded ~90 MB on first use from GitHub (`faustomorales/keras-facenet` releases). Cached in `<install folder>\models\keras-facenet\` (e.g. `C:\FANSC\models\keras-facenet\` — NOT `%USERPROFILE%\.keras-facenet\` or `~/.keras\`, which keras-facenet would use by default). Requires internet on first run. |
| Anti-spoofing | Texture analysis only. No external model needed. |
| MediaPipe (head movement) | Loaded from CDN in the browser. No server-side install. |

> **After first run:** The FaceNet model (~90 MB) is downloaded once and cached in `<install folder>\models\keras-facenet\20180402-114759\` — a machine-local location next to `fans_c.exe`, not under any particular Windows account's `%USERPROFILE%`. All subsequent startups on this machine load from that same cache — no internet required — regardless of which Windows account starts FANS-C. If the cache is deleted or the server is moved to a new machine, internet access is needed again for the next load. FANS-C never silently substitutes a fake/random model when the real one is unavailable — registration and verification are blocked with a clear "model unavailable" message instead (see Troubleshooting below).
>
> **Interactive setup and SYSTEM autostart share the same cache.** The one-click installer's autostart Task Scheduler entry ("FANS-C Verification System") registers `fans_c.exe` to run as the **SYSTEM** account. Because the cache path is derived from the install directory (where `fans_c.exe` lives) rather than from any account's `%USERPROFILE%`, a model downloaded during interactive first-run setup is immediately usable by the SYSTEM-run autostart process after a reboot — no separate download is needed. An advanced operator can relocate the cache (e.g. to a drive with more free space) by setting `FANS_FACENET_CACHE_DIR` to an absolute path in `.env`.

---

## Troubleshooting — Face Registration Errors

### `Face processing error: 'NoneType' object has no attribute 'write'`

**Symptom:** Submitting the face capture form during beneficiary registration,
representative enrollment, or face update returns this error in the browser.

**Cause (fixed in 2.0.2):** PyInstaller's no-console (`--windowed`) mode sets
`sys.stderr = None`. Python's `warnings.warn()` routes warnings through
`sys.stderr.write()` internally. When `keras-facenet` / TensorFlow are not
installed and the mock FaceNet path is taken, `warnings.warn()` was called and
crashed immediately.

**Versions affected:** Any 2.0 / 2.0.1 build from the installer (`fans_c.exe`).
Source + venv setups are unaffected because `sys.stderr` is always set there.

**Resolution:** Update to 2.0.2 or later. All `warnings.warn()` and
`print(file=sys.stderr)` calls in face-related code have been replaced with
Python `logging` calls that route to `logs\fans_c.log`.

---

### Face registration returns `Face recognition model not loaded`

**Symptom:** Face capture succeeds (camera preview works, photo taken) but the
server returns an error about the model not being installed.

**Cause:** `keras-facenet` or TensorFlow is not importable in the current
environment, or (on a clean machine) the FaceNet weights could not be
downloaded. FANS-C fails closed in this state — it does not fall back to a
mock/random model; registration and verification are blocked instead.

**Diagnosis:**
```powershell
.\.venv\Scripts\python.exe -c "from keras_facenet import FaceNet; print('OK')"
```
If this fails, TensorFlow or keras-facenet is missing, or (on a clean
machine) the weights could not be downloaded — check internet connectivity.

**Resolution (source / manual setup):**
```powershell
.\.venv\Scripts\pip install keras-facenet tensorflow==2.13.0
```
Then restart the server. On first start, FaceNet (~90 MB) downloads to
`<install folder>\models\keras-facenet\20180402-114759\`. Subsequent starts —
under any Windows account, including the SYSTEM-run autostart task — load
from that same machine-local cache.

**Resolution (installer build):** TensorFlow is bundled in the PyInstaller
package. If the error appears after a fresh install, check `logs\fans_c.log`
for the warmup error and run `scripts\admin\verify-installation.ps1` to
identify any broken package.

---

### `Invalid image data. Please retake the photo and try again.`

**Symptom:** Face capture fails with this message. The camera and preview work
normally.

**Cause:** The base64-encoded image sent by the browser was truncated or
corrupted before reaching the server. This can happen if the browser tab was
throttled during the photo capture burst, or if a network proxy altered the
request body.

**Resolution:**
1. Reload the page and retry — the camera burst is retried from scratch.
2. Check browser console (`F12 → Console`) for JavaScript errors during capture.
3. If it only happens on one workstation, check for browser extensions that
   might modify outgoing POST requests.

---

## EMBEDDING_ENCRYPTION_KEY — Critical Security Setting

This key encrypts every face embedding stored in the database using Fernet (AES-128-CBC + HMAC-SHA256).

**If this key is blank in .env:**
- In development (DEBUG=True): the server starts with a warning printed to the console. Face operations will fail at runtime.
- In production (DEBUG=False): the server REFUSES TO START and raises a RuntimeError. This is intentional — silent operation without encryption is not permitted.

**If this key is lost or changed:**
All stored face embeddings become permanently unreadable. Every beneficiary must re-enroll from scratch. There is no recovery path.

**Generate once:**
```powershell
python manage.py generate_key
```
Or:
```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the output into .env as `EMBEDDING_ENCRYPTION_KEY=<value>`.

**Back it up securely** — store a copy off the server (USB, encrypted file, secure note). This key must be identical on every device that shares the same database.

---

## Uninstall / Removal

### Normal uninstall

Use the Windows uninstaller (preserves user data by default):

- **Settings → Apps → FANS-C Verification System → Uninstall**
- or **Control Panel → Programs and Features → Uninstall**

The uninstaller stops running processes, removes Task Scheduler tasks, removes shortcuts, and removes the `fans-barangay.local` hosts file entry. User data (`.env`, `db.sqlite3`, `media/`, `logs/`, certs) is **not** deleted so that data survives reinstall.

### Interactive cleanup helper

```powershell
# Run as Administrator
powershell.exe -ExecutionPolicy Bypass -File C:\FANSC\cleanup-fansc.ps1
```

Prompts per item; reports PASS/FAIL. Safe to run after or instead of the installer uninstaller.

### Full cleanup (wipes all data)

```powershell
# Run as Administrator — DESTROYS all beneficiary data
powershell.exe -ExecutionPolicy Bypass -File "C:\FANSC\scripts\admin\uninstall-clean.ps1"
```

Type `YES` at the prompt. Removes everything including the database, photos, and `C:\FANSC`.

### Kill stuck processes

```powershell
Stop-Process -Name "fans_c"         -Force -ErrorAction SilentlyContinue
Stop-Process -Name "caddy"          -Force -ErrorAction SilentlyContinue
Stop-Process -Name "waitress-serve" -Force -ErrorAction SilentlyContinue
```
