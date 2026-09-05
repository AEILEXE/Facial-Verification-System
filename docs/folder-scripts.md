# Folder: scripts/

## Purpose

The `scripts/` folder contains all PowerShell and batch scripts that manage the FANS-C system. This includes one-time setup, daily startup, diagnostics, and the self-healing watchdog. Scripts are organized into three subfolders based on their lifecycle role.

## Why it exists

Django itself is just the application — it does not know how to install itself, configure Windows Task Scheduler, generate TLS certificates, or monitor its own health. These operational tasks are handled by the scripts in this folder. Separating them from the application code keeps the Django codebase clean and makes the server management layer independently understandable.

---

## Subfolder Structure

```
scripts/
|-- setup/      One-time setup scripts (run once by IT)
|-- start/      Service launcher scripts (start Waitress and Caddy)
|-- admin/      Diagnostic and maintenance scripts (run anytime)
```

---

## scripts/setup/

### setup-complete.ps1

**What it does:** Master setup entry point. Runs all 8 setup steps in order, tracks the result of each step, and prints a PASS/FAIL summary at the end.

**When it is used:** Once, during initial server setup. Safe to re-run if a step failed.

**Who calls it:** IT (must be run as Administrator).

**What it does internally:**

1. Calls `setup-secure-server.ps1` — creates `.venv`, installs packages, generates TLS certs, configures Django
2. Verifies `fans-cert.pem` and `fans-cert-key.pem` exist
3. Verifies `caddy.exe` is found in `tools\`, PATH, or `D:\Tools\`
4. Verifies `.env` has `SECRET_KEY` and `EMBEDDING_ENCRYPTION_KEY`
5. Calls `setup-autostart.ps1` — registers Windows Task Scheduler task for auto-start
6. Optionally creates a desktop shortcut
7. Performs live startup validation — starts both services and tests TCP connections on ports 8000 and 443
8. Registers the watchdog Task Scheduler task

**Parameters:** `-SkipDeps` — skips pip install when `.venv` already exists (speeds up re-runs)

**Output:** Color-coded step results (green PASS, red FAIL, yellow WARN) and a final summary with next-step instructions.

**How it connects to the system:** This is the only script most IT users ever need to run. Everything else — the venv, the cert, the database, the Task Scheduler tasks, and the watchdog — flows from this one script.

**Defense note:** Centralizing setup in one script eliminates human error from partial setup. If any step fails, the summary tells the admin exactly which step failed and what to do. This is important for real barangay deployments where IT may not have deep technical knowledge.

---

### setup-secure-server.ps1

**What it does:** Lower-level setup script. Creates the Python virtual environment, installs all Python dependencies, runs mkcert to generate TLS certificates, writes initial keys to `.env`, runs Django database migrations, runs `collectstatic`, and prompts to create the admin user.

**When it is used:** Called automatically by `setup-complete.ps1`. Can also be run standalone for manual or advanced setups.

**Who calls it:** IT (directly), or `setup-complete.ps1` (indirectly).

**Runtime phase:** Setup only.

**How it connects to the system:** This script creates the `.venv` that all startup and runtime scripts depend on. It also generates `fans-cert.pem` and `fans-cert-key.pem` which Caddy requires for HTTPS.

**Defense note:** Separating lower-level setup from the orchestration layer (`setup-complete.ps1`) means the setup can be re-run at a granular level. If only the TLS cert needs to be regenerated, this script can be run without re-running the full orchestration.

---

### setup-autostart.ps1

**What it does:** Registers the `FANS-C Verification System` task in Windows Task Scheduler. This task runs `scripts\start\start-fans-hidden.ps1` at every system boot, starting Waitress and Caddy automatically.

**When it is used:** Called by `setup-complete.ps1`. Can be run standalone if the Task Scheduler task needs to be re-registered.

**Who calls it:** IT (directly or via `setup-complete.ps1`).

**Runtime phase:** Setup only.

**How it connects to the system:** Without this script, the system does not start automatically after a reboot. IT would need to manually start the server every morning.

**Defense note:** Windows Task Scheduler is the standard Windows mechanism for running processes at boot. Using it means the system starts even if no one logs in to the server PC — important for unattended server operation in a barangay office.

---

### Create-Desktop-Shortcut.ps1

**What it does:** Creates a desktop shortcut that launches `scripts\start\start-fans-quiet.bat` with the correct working directory.

**When it is used:** Optional, once, after `setup-complete.ps1`.

**Who calls it:** IT (optionally, or prompted by `setup-complete.ps1`).

**Runtime phase:** Setup only (optional).

**How it connects to the system:** Provides a user-friendly way to manually start the server without opening a terminal and navigating to the project folder.

---

## scripts/start/

### start-fans-hidden.ps1

**What it does:** Silent startup launcher used exclusively by Windows Task Scheduler. Activates the virtual environment, starts Waitress on port 8000, starts Caddy on port 443, verifies both ports respond, and writes the result to `logs\fans-startup.log`. No visible windows are shown.

**When it is used:** Automatically, at every boot, by the `FANS-C Verification System` Task Scheduler task.

**Who calls it:** Task Scheduler (SYSTEM account). IT should never run this manually.

**Runtime phase:** Startup.

**How it connects to the system:** This is the bridge between Task Scheduler and the running services. It is responsible for the system being available to staff within 30 seconds of the server booting.

**Defense note:** Running as SYSTEM (not as a specific user) means the task runs even before anyone logs in, and it always has the required elevated privileges without UAC prompts.

---

### start-fans-quiet.bat

**What it does:** Manual startup launcher. Starts Waitress (minimized) and Caddy (minimized) and then verifies that both ports (8000 and 443) are actually listening using `netstat`. Prints a status report and the HTTPS URL. Shows all error details if a port does not respond.

**When it is used:** Manual startup — when the auto-start task is not configured or failed, or when IT wants to start the server without rebooting.

**Who calls it:** IT (double-click or from a terminal). Also used by the desktop shortcut created by `Create-Desktop-Shortcut.ps1`.

**Runtime phase:** Startup (manual).

**How it connects to the system:** Port verification is a key feature — it does not just launch processes and assume success. It uses `netstat` to confirm that both ports are in a `LISTENING` state before declaring success. If a port is not listening, it prints specific diagnostic suggestions.

**Pre-flight checks it performs:**
- `.venv\Scripts\waitress-serve.exe` is present
- `.env` exists
- `Caddyfile` exists
- `fans-cert.pem` and `fans-cert-key.pem` exist

All checks are hard-fail: if any file is missing, the script stops with a clear error message.

---

### start-fans.bat

**What it does:** Debug launcher. Starts Waitress and Caddy in visible (non-minimized) terminal windows so all output — including errors — is visible. Shows the server's LAN IP and the HTTPS access URL.

**When it is used:** Troubleshooting. When a service is failing to start and you need to see the exact error message.

**Who calls it:** IT (for diagnosis only).

**Runtime phase:** Startup (debug).

**How it connects to the system:** Uses the same underlying commands as `start-fans-hidden.ps1` and `start-fans-quiet.bat`, but with visible windows. Close the two terminal windows to stop both services.

**Defense note:** Having a separate debug launcher is important for diagnosing production failures. A silent launcher is better for daily use (no distracting windows), but a verbose launcher is essential when something breaks and the error needs to be read.

---

### start-fans-production.ps1

**What it does:** Production-oriented variant of the startup script with additional production configuration checks.

**When it is used:** IT who prefers a PowerShell-native startup over the batch file.

**Who calls it:** IT (optional).

**Runtime phase:** Startup (alternative).

---

## scripts/admin/

### check-system-health.ps1

**What it does:** Read-only diagnostic tool. Checks the live state of every system component and prints a color-coded status report. Makes no changes.

**When it is used:** Any time a problem is suspected. Also useful for confirming everything is healthy before a stipend distribution day.

**Who calls it:** IT (run from PowerShell — does not require Administrator privileges).

**Runtime phase:** Diagnostics (any time).

**Checks performed:**

| Check | What it looks for |
|---|---|
| Waitress process | Is waitress-serve.exe running? Which PID(s)? |
| Port 8000 | Is it accepting TCP connections? |
| Caddy process | Is caddy.exe running? Which PID(s)? |
| Port 443 | Is it accepting TCP connections? |
| TLS certificate files | Do fans-cert.pem and fans-cert-key.pem exist? How old are they? |
| .env configuration | Does .env exist? Are SECRET_KEY and EMBEDDING_ENCRYPTION_KEY set? Is DEBUG False? |
| Auto-start task | Is "FANS-C Verification System" registered in Task Scheduler? What is its state and last run time? |
| Watchdog task | Is "FANS-C Watchdog" registered? What is its state and last run time? |
| Startup log | Shows the most recent block from logs\fans-startup.log |
| Watchdog log | Shows the last 15 lines from logs\fans-watchdog.log |

**Output:** Each check prints [OK] (green), [FAIL] (red), or [WARN] (yellow). After all checks, prints a summary with fix instructions for any failures.

**How it connects to the system:** This script is the first step in any troubleshooting workflow. IT should run it before checking anything else — it surfaces all common failure causes in under 30 seconds.

**Defense note:** A diagnostic-only tool that makes no changes is important in production environments. Running a diagnostic should never make a problem worse. This script can safely be run at any time, even when the system appears to be working normally.

---

### watchdog.ps1

**What it does:** Continuous self-healing monitor. Runs indefinitely, checking Waitress and Caddy every 45 seconds and automatically restarting either service if it stops responding.

**When it is used:** Automatically, 150 seconds after every boot, by the `FANS-C Watchdog` Task Scheduler task. Never run manually.

**Who calls it:** Task Scheduler (SYSTEM account). IT should not run this manually.

**Runtime phase:** Runtime (always running in background after startup).

**Health checks (every 45 seconds):**
1. Is the process (waitress-serve.exe or caddy.exe) running?
2. Is the port (8000 or 443) accepting TCP connections?
3. Does Django respond to an HTTP probe on port 8000?

**Recovery safeguards:**

| Safeguard | Detail |
|---|---|
| No duplicate processes | Kills any stale instance before starting a new one |
| Pre-restart port check | If the port recovered on its own, skips the restart |
| 60-second cooldown | Minimum wait between successive restart attempts |
| Max 3 restarts per 10 minutes | Stops retrying after 3 failures in a 10-minute window |
| ALERT on repeated failure | Logs [ALERT] and instructs IT to inspect |
| Auto-reset on recovery | Failure counters reset when services stay healthy |

**Log file:** `logs\fans-watchdog.log` (rotates at 5 MB to `fans-watchdog.log.old`)

**Log levels:**

| Level | Meaning |
|---|---|
| `HEALTHY` | All services OK (logged once every ~7.5 minutes to reduce noise) |
| `WARN` | A service is not responding |
| `ACTION` | A restart is being attempted |
| `OK` | Recovery was successful |
| `FAIL` | Restart attempted but service still not responding |
| `ALERT` | Max restarts reached — IT inspection required |
| `WAIT` | Cooldown period — restart deferred |
| `SKIP` | Recovery skipped (gave up after repeated failures) |

**How it connects to the system:** The watchdog is the system's resilience layer. Without it, any crash during the workday requires IT to be called in to manually restart services. With the watchdog, most transient failures are resolved automatically within 45-60 seconds — transparent to staff.

**Task Scheduler configuration:**
- Task name: `FANS-C Watchdog`
- Trigger: System startup + 150-second delay
- Account: SYSTEM
- Window: Hidden
- Auto-restart if crashed: Yes (3 times, 2-minute interval)

**Defense note:** The watchdog illustrates a key system design principle: assume failure will happen and build recovery in. The 150-second boot delay, rate limiting, cooldown periods, and ALERT escalation are all deliberate design choices that prevent the watchdog itself from causing problems (e.g., restart storms from a script that is configured wrongly).

---

### stop-fans.ps1

**What it does:** Cleanly stops both Waitress and Caddy processes. Useful for maintenance or before running a manual setup step.

**When it is used:** When IT needs to stop the system for maintenance.

**Who calls it:** IT.

**Runtime phase:** Maintenance / shutdown.

**How it connects to the system:** Stopping services cleanly (rather than killing them abruptly) allows open connections to complete and prevents file corruption on the database or log files.

---

### fans-control-center.ps1

**What it does:** All-in-one IT menu. Provides numbered options: Start / Stop / Restart services, Check health (runs check-system-health.ps1), View startup log, View watchdog log, Repair auto-start task, Repair watchdog task, Repair hosts file, Create/add admin user, Open site in browser. Shows live RUNNING / PARTIAL / STOPPED status (including HTTPS end-to-end probe) at the top of every screen.

**When it is used:** Any time IT needs to manage or diagnose the system. Recommended as the single admin entry point after setup.

**Who calls it:** IT (must be run as Administrator).

**Runtime phase:** Any time.

**How it connects to the system:** Delegates to the other admin scripts rather than duplicating logic. Provides a guided menu so IT does not need to remember individual script names or paths.

---

### start-now.ps1

**What it does:** Starts Waitress and Caddy immediately without re-running any setup. Performs pre-flight checks (waitress-serve.exe, .env, fans-cert.pem, caddy.exe), kills stale processes, starts services, waits for ports to respond, then verifies HTTPS end-to-end.

**When it is used:** When IT needs to start the system without rebooting, without going through the full setup flow.

**Who calls it:** IT (must be run as Administrator for port 443).

**Runtime phase:** Manual startup.

---

### repair-autostart.ps1

**What it does:** Re-registers the `FANS-C Verification System` Task Scheduler task only. Does not re-run any setup steps, reinstall dependencies, or change any configuration.

**When it is used:** When check-system-health.ps1 shows the auto-start task is missing or broken, or after moving the project folder.

**Who calls it:** IT (must be run as Administrator).

**Runtime phase:** Repair only.

---

### repair-watchdog.ps1

**What it does:** Re-registers the `FANS-C Watchdog` Task Scheduler task only. Does not affect running services or other configuration.

**When it is used:** When check-system-health.ps1 shows the watchdog task is missing or broken.

**Who calls it:** IT (must be run as Administrator).

**Runtime phase:** Repair only.

---

### repair-hosts.ps1

**What it does:** Adds `127.0.0.1  fans-barangay.local` to `C:\Windows\System32\drivers\etc\hosts` on the server PC. This is needed for the browser on the server PC to resolve `fans-barangay.local` to the local Caddy instance. Does not restart services or change any other configuration.

**When it is used:** When `https://fans-barangay.local` times out or shows "site not found" from the server PC itself, and check-system-health.ps1 shows the hosts file entry is missing.

**Who calls it:** IT (must be run as Administrator).

**Runtime phase:** Repair only.

**How it connects to the system:** The server PC uses `127.0.0.1` for `fans-barangay.local` (Caddy listens on all interfaces). Client devices use the server's LAN IP instead (added by `trust-local-cert.bat`). These are different entries on different machines — this repair script only fixes the server PC.

---

### create-admin-user.ps1

**What it does:** Checks how many Django superuser accounts exist, then prompts to create a new one using `manage.py createsuperuser`. Safe to run at any time — will not delete or overwrite existing accounts.

**When it is used:** When a new admin account is needed, or when the first admin was not created during setup.

**Who calls it:** IT.

**Runtime phase:** Any time after setup.

---

## How scripts/ connects to the system

```
scripts/setup/
    setup-complete.ps1          -> creates everything from scratch
        setup-secure-server.ps1 -> .venv, certs, Django config
        setup-autostart.ps1     -> Task Scheduler auto-start task

scripts/start/
    start-fans-hidden.ps1       -> used by Task Scheduler at boot
    start-fans-quiet.bat        -> used by IT for manual start
    start-fans.bat              -> used for debugging startup failures

scripts/admin/
    fans-control-center.ps1     -> recommended all-in-one IT menu
    check-system-health.ps1     -> read-only diagnostics, any time
    watchdog.ps1                -> used by Task Scheduler, always running
    start-now.ps1               -> manual start without setup re-run
    stop-fans.ps1               -> clean shutdown for maintenance
    repair-autostart.ps1        -> fix auto-start task only
    repair-watchdog.ps1         -> fix watchdog task only
    repair-hosts.ps1            -> fix server hosts file only
    create-admin-user.ps1       -> add Django admin account
```

---

## Runtime flow

| Phase | Scripts used |
|---|---|
| First-time setup | `setup-complete.ps1` (which calls `setup-secure-server.ps1` and `setup-autostart.ps1`) |
| Every boot (auto) | `start-fans-hidden.ps1` (Task Scheduler), then `watchdog.ps1` (Task Scheduler, 150s delay) |
| Manual start | `start-fans-quiet.bat` |
| Debugging | `start-fans.bat` |
| Diagnostics | `check-system-health.ps1` |
| Maintenance | `stop-fans.ps1` |

---

## Defense notes

**Why separate setup, start, and admin scripts?**
Each group has a different audience and risk level. Setup scripts are run once by a technical user who understands the consequences. Start scripts are run by IT who may not be developers. Admin scripts are safe to run by anyone with access to the server, because they either make no changes (health check) or stop cleanly (stop-fans). Mixing these groups would create confusion and increase the risk of running the wrong script.

**What happens if a script is moved?**
All scripts calculate the project root from their own file location using `$PSScriptRoot` (PowerShell) or `%~dp0` (batch). Moving a script to a different folder breaks its path resolution and it will fail to find `manage.py`, `.venv`, `Caddyfile`, or other project files.

**What happens if watchdog.ps1 is run manually?**
It starts a second instance of the monitor alongside the one already running via Task Scheduler. Both instances will try to restart services simultaneously during a failure, which can cause duplicate process launches. Always manage the watchdog through Task Scheduler.

---

## Related folders/files

- `logs/` — watchdog.ps1 writes to `logs\fans-watchdog.log`; start-fans-hidden.ps1 writes to `logs\fans-startup.log`
- `.venv/` — all startup scripts activate this before launching Waitress
- `.env` — read by Django at startup; check-system-health.ps1 verifies its contents
- `fans-cert.pem`, `fans-cert-key.pem` — verified by setup-complete.ps1; used by Caddy
- `Caddyfile` — used by all scripts that start Caddy
- `tools/caddy.exe` — the Caddy binary that all start scripts look for

---

## Summary

The `scripts/` folder is the operational control layer of FANS-C. The Django application code handles the business logic (face matching, records, users), while the scripts in this folder handle everything around it: installation, startup, health monitoring, and diagnostics. A correctly set-up server needs no manual script interaction from the President or Staff — scripts only exist for IT and for Task Scheduler to use.
