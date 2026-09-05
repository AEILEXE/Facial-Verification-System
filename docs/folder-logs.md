# Folder: logs/

## Purpose

The `logs/` folder contains runtime log files written by the FANS-C system during startup and continuous operation. These logs are the primary diagnostic resource for IT when troubleshooting problems — they record exactly what happened, when, and whether it succeeded or failed.

## Why it exists

Production systems need an audit trail. When something goes wrong — the server doesn't start, a service crashes, staff cannot connect — the logs tell IT what happened without requiring them to reproduce the problem live. The FANS-C system generates two distinct log files for the two main background processes: the startup launcher and the watchdog.

## Important files inside

### logs/fans-startup.log

**What it does:** Records the result of every automatic startup attempt. Written by `scripts/start/start-fans-hidden.ps1` (the script that Task Scheduler calls at every boot).

**When it is created:** On first boot after setup. Updated on every subsequent boot.

**Who writes it:** `scripts/start/start-fans-hidden.ps1` (called by Task Scheduler's `FANS-C Verification System` task).

**Structure:**

Each startup attempt is recorded as a block, separated by `===` dividers:

```
===================================================
FANS-C Startup - 2026-04-17 08:03:15
===================================================
[INFO]  Activating virtual environment...
[INFO]  Starting Waitress on 127.0.0.1:8000...
[INFO]  Waitress started (PID 4821)
[INFO]  Waiting for Waitress to initialize...
[OK]    Port 8000 is listening
[INFO]  Starting Caddy...
[INFO]  Caddy started (PID 4836)
[INFO]  Waiting for Caddy to initialize...
[OK]    Port 443 is listening
[OK]    STARTUP STATUS: OK
```

**Log levels:**
- `[INFO]` — informational step, no action needed
- `[OK]` — step completed successfully
- `[WARN]` — step succeeded with a concern (e.g., port responded but slowly)
- `[FAIL]` — step failed

**STARTUP STATUS values:**
- `OK` — both services started and both ports are listening
- `PARTIAL` — one port is listening but the other is not
- `FAIL` — neither port responded after startup

**When to check it:**
- Staff cannot access the system first thing in the morning
- Browser shows "This site can't be reached" after a reboot
- Confirming that Task Scheduler ran the startup at the expected boot time

**How to read it:** Look for the most recent `===` block (at the bottom of the file). Find the `STARTUP STATUS:` line. If `FAIL` or `PARTIAL`, look above it for the first `[FAIL]` entry to identify which service failed.

---

### logs/fans-watchdog.log

**What it does:** Records every health check cycle and every recovery attempt made by the watchdog. This is a continuous rolling log — new entries are appended every 45 seconds while the watchdog is running.

**When it is created:** On first boot after setup, when the `FANS-C Watchdog` Task Scheduler task first runs. 

**Who writes it:** `scripts/admin/watchdog.ps1` (called by Task Scheduler's `FANS-C Watchdog` task).

**Rotation:** When the file reaches 5 MB, it is renamed to `fans-watchdog.log.old` and a new `fans-watchdog.log` is started.

**Structure:**

Each log entry includes a timestamp, log level, and message:

```
2026-04-17 08:04:45 [START]   Watchdog started. Checking every 45 seconds.
2026-04-17 08:04:45 [HEALTHY] Waitress (port 8000) OK | Caddy (port 443) OK
2026-04-17 08:11:35 [HEALTHY] Waitress (port 8000) OK | Caddy (port 443) OK
2026-04-17 09:23:12 [WARN]    Waitress port 8000 not responding
2026-04-17 09:23:12 [ACTION]  Attempting Waitress restart (attempt 1 of 3)
2026-04-17 09:23:22 [OK]      Waitress recovered. Port 8000 responding.
2026-04-17 09:23:22 [HEALTHY] Waitress (port 8000) OK | Caddy (port 443) OK
```

**Log levels:**

| Level | Meaning | What to do |
|---|---|---|
| `START` | Watchdog started or restarted | Normal — appears after every boot or task restart |
| `HEALTHY` | All services are OK | Normal — logged once every ~7.5 minutes to confirm the watchdog is still running |
| `WARN` | A service is not responding | May self-resolve; watch for the next entry |
| `ACTION` | A restart is being attempted | Watchdog is working — wait for OK or FAIL |
| `OK` | Recovery was successful | Normal — service came back |
| `FAIL` | Restart attempted but service still not responding | Watchdog will try again (up to 3 times) |
| `ALERT` | Max restarts reached — IT must inspect | See below |
| `WAIT` | Cooldown period — restart deferred | Normal safeguard — watchdog will retry after 60 seconds |
| `SKIP` | Recovery skipped (already gave up) | Watchdog stopped trying; IT action required |

**What an ALERT means:**

The watchdog attempted to restart a service 3 times within a 10-minute window and the service still did not recover. The watchdog stops retrying and writes an `[ALERT]` entry, then waits for IT intervention.

When you see `[ALERT]`:
1. Run `scripts\admin\check-system-health.ps1` to see the current state
2. Run `scripts\start\start-fans.bat` to see the full error output from Waitress or Caddy
3. After fixing the underlying issue, restart the `FANS-C Watchdog` task in Windows Task Scheduler to reset the failure counters

**When to check it:**
- Staff report that the system was unavailable at a specific time
- Confirming whether the watchdog ever had to restart services
- Diagnosing persistent failures that keep recurring during the day
- Routine monitoring to confirm no silent failures have occurred

---

## How it connects to the system

```
scripts/start/start-fans-hidden.ps1
        |
        | writes startup blocks
        v
logs/fans-startup.log

scripts/admin/watchdog.ps1
        |
        | writes continuous health check entries
        v
logs/fans-watchdog.log

scripts/admin/check-system-health.ps1
        |
        | reads last entries from both logs
        | displays color-coded in terminal output
        v
(IT terminal)
```

The health check script (`check-system-health.ps1`) reads the last section of both log files and displays them with color coding in the terminal. IT does not need to open the raw log files manually unless they need to look at history beyond what the health check shows.

---

## Runtime flow

| Phase | How logs/ is involved |
|---|---|
| Setup | `logs/` folder is created during setup if it does not exist |
| Every boot | `start-fans-hidden.ps1` appends a startup block to `fans-startup.log` |
| 150 seconds after boot | `watchdog.ps1` starts and begins appending to `fans-watchdog.log` every 45 seconds |
| Any failure | Both log files capture the failure event and any recovery attempts |
| IT diagnostics | `check-system-health.ps1` reads recent entries from both logs |

---

## Defense notes

**Why are there two separate log files?**
The startup log and watchdog log serve different purposes and are written by different scripts. The startup log records a single event (boot-time startup) and needs to be readable chronologically by boot attempt. The watchdog log is continuous and high-frequency — merging them would make it hard to find startup events among thousands of health check entries.

**Why does the watchdog only log [HEALTHY] every ~7.5 minutes?**
The watchdog checks every 45 seconds. Logging on every healthy check would produce about 1,920 entries per day, making the log very large and hard to scan. By logging [HEALTHY] only every 10 cycles (~7.5 minutes), the log remains manageable while still proving the watchdog is alive and healthy.

**What does it mean if fans-watchdog.log has no recent entries?**
The watchdog is not running. This means: (a) the `FANS-C Watchdog` Task Scheduler task is not registered, (b) it was disabled, or (c) the watchdog script crashed. Run `check-system-health.ps1` to check the task state. If the watchdog is not running, the system is still functional but has no automatic recovery protection.

**What does it mean if fans-startup.log shows FAIL but the system is working?**
The startup failed at boot, but someone (likely IT or the watchdog) manually started or recovered the services afterward. The startup log only records what happened at boot — later recovery is recorded in the watchdog log.

---

## Related folders/files

- `scripts/start/start-fans-hidden.ps1` — writes `fans-startup.log`
- `scripts/admin/watchdog.ps1` — writes `fans-watchdog.log`
- `scripts/admin/check-system-health.ps1` — reads and displays both log files

---

## Summary

The `logs/` folder is the system's memory of what happened. `fans-startup.log` answers "did the server start correctly at boot?" and `fans-watchdog.log` answers "did anything crash during the day, and did the watchdog fix it?" Both logs are essential for IT diagnosis and are automatically read by the health check tool for convenient display. During a thesis or capstone defense, these logs demonstrate that the system is designed for real-world production use — not just happy-path scenarios.
