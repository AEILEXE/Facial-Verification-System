#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C Watchdog -- continuous health monitor and self-healing background service.

.DESCRIPTION
    Started automatically by Windows Task Scheduler 150 seconds after every boot.
    Runs continuously in the background. Checks Waitress and Caddy every 45 seconds
    and attempts automatic recovery if either service stops responding.

    Health checks performed every 45 seconds:
      1. Is the Waitress process running?
      2. Is port 8000 listening? (TCP connection test)
      3. Does Django respond to an HTTP probe on port 8000? (confirms app is alive)
      4. Is the Caddy process running?
      5. Is port 443 listening? (TCP connection test)

    Recovery behavior:
      - On failure: stops any stale process, starts a clean replacement
      - 60-second cooldown between restart attempts
      - Maximum 3 restart attempts per service in any 10-minute window
      - After 3 failed attempts: stops trying, writes ALERT, waits for IT/Admin
      - Resets failure counters automatically if services recover

    Safeguards:
      - Never spawns duplicate processes (kills existing before starting new)
      - Re-checks current port state before each restart (skips if already up)
      - Logs every check, action, and outcome with timestamp

    Log file: logs\fans-watchdog.log
    Log rotation: renames to fans-watchdog.log.old when file exceeds 5 MB

    Task name in Task Scheduler: FANS-C Watchdog

    To temporarily stop the watchdog:
      Stop "FANS-C Watchdog" task in Windows Task Scheduler.
    To reset failure counters after repeated failures:
      Restart "FANS-C Watchdog" task in Windows Task Scheduler.
    For live diagnostics:
      Run scripts\admin\check-system-health.ps1

.NOTES
    Called by Task Scheduler only. IT/Admin should not run this manually.
    Registered by scripts\setup\setup-complete.ps1 during one-time server setup.
#>

$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $projectRoot

# =============================================================================
# CONFIGURATION
# =============================================================================
$CHECK_INTERVAL_SEC = 45      # seconds between health checks
$MAX_RESTARTS       = 3       # max restart attempts per service per window
$RESTART_WINDOW_SEC = 600     # rolling window for restart count (10 minutes)
$COOLDOWN_SEC       = 60      # minimum seconds between successive restarts
$HTTP_TIMEOUT_MS    = 5000    # HTTP probe timeout in milliseconds
$LOG_MAX_BYTES      = 5242880 # rotate log after 5 MB

# =============================================================================
# LOG SETUP
# =============================================================================
$logsDir = Join-Path $projectRoot 'logs'
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
}
$logFile = Join-Path $logsDir 'fans-watchdog.log'

function Rotate-WatchLog {
    if (Test-Path $logFile) {
        $size = (Get-Item $logFile).Length
        if ($size -gt $LOG_MAX_BYTES) {
            $oldLog = $logFile -replace '\.log$', '.log.old'
            if (Test-Path $oldLog) {
                Remove-Item $oldLog -Force -ErrorAction SilentlyContinue
            }
            Move-Item $logFile $oldLog -Force -ErrorAction SilentlyContinue
        }
    }
}

function Write-WatchLog {
    param([string]$Level, [string]$Message)
    $ts   = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "$ts  [$Level]  $Message"
    $line | Add-Content -Path $logFile -Encoding UTF8
}

# =============================================================================
# PATH HELPERS
# =============================================================================
$waitressExe  = Join-Path $projectRoot '.venv\Scripts\waitress-serve.exe'
$caddyBundled = Join-Path $projectRoot 'tools\caddy.exe'

function Find-CaddyExe {
    if (Test-Path $caddyBundled) { return $caddyBundled }
    try {
        $found = Get-Command caddy -ErrorAction Stop
        return $found.Source
    } catch { }
    if (Test-Path 'D:\Tools\caddy.exe') { return 'D:\Tools\caddy.exe' }
    return $null
}

# =============================================================================
# HEALTH CHECK HELPERS
# =============================================================================
function Test-PortOpen {
    param([int]$Port)
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect('127.0.0.1', $Port)
        $tcp.Close()
        return $true
    } catch {
        return $false
    }
}

function Test-HttpProbe {
    # Returns $true if Django responds with any HTTP reply (even 4xx/5xx).
    # Returns $false only on connection refused or timeout (server is down).
    param([string]$Url, [int]$TimeoutMs = $HTTP_TIMEOUT_MS)
    try {
        $req         = [System.Net.HttpWebRequest]::Create($Url)
        $req.Timeout = $TimeoutMs
        $req.Method  = 'GET'
        $resp        = $req.GetResponse()
        $resp.Close()
        return $true
    } catch [System.Net.WebException] {
        # HTTP error response (4xx, 5xx): app is alive, just returning an error
        if ($null -ne $_.Exception.Response) { return $true }
        # Connection refused or timeout: app is down
        return $false
    } catch {
        return $false
    }
}

function Start-Hidden {
    param([string]$Exe, [string]$Arguments, [string]$WorkDir)
    $psi                  = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName         = $Exe
    $psi.Arguments        = $Arguments
    $psi.WorkingDirectory = $WorkDir
    $psi.CreateNoWindow   = $true
    $psi.UseShellExecute  = $false
    return [System.Diagnostics.Process]::Start($psi)
}

# =============================================================================
# RESTART TRACKING STATE
# =============================================================================
$waitressRestartTimes = [System.Collections.Generic.List[datetime]]::new()
$caddyRestartTimes    = [System.Collections.Generic.List[datetime]]::new()
$waitressGivenUp      = $false
$caddyGivenUp         = $false
$waitressLastRestart  = [datetime]::MinValue
$caddyLastRestart     = [datetime]::MinValue

function Prune-RestartWindow {
    # Remove restart entries older than the rolling window.
    # Iterates backward to safely remove by index.
    param([System.Collections.Generic.List[datetime]]$Times)
    $cutoff = [datetime]::Now.AddSeconds(-$RESTART_WINDOW_SEC)
    $i = $Times.Count - 1
    while ($i -ge 0) {
        if ($Times[$i] -lt $cutoff) { $Times.RemoveAt($i) }
        $i--
    }
}

# =============================================================================
# WAITRESS RECOVERY
# =============================================================================
function Invoke-WaitressRecovery {
    # Skip if we already gave up after repeated failures
    if ($script:waitressGivenUp) {
        Write-WatchLog 'SKIP' 'Waitress recovery skipped -- max attempts already reached. IT/Admin action required.'
        return
    }

    Prune-RestartWindow -Times $script:waitressRestartTimes

    # Check if we have hit the max restart limit
    if ($script:waitressRestartTimes.Count -ge $MAX_RESTARTS) {
        $script:waitressGivenUp = $true
        Write-WatchLog 'ALERT' "Waitress has failed $MAX_RESTARTS times in the last $([int]($RESTART_WINDOW_SEC / 60)) minutes."
        Write-WatchLog 'ALERT' 'Automatic recovery stopped to prevent a restart storm.'
        Write-WatchLog 'ALERT' 'The application server is not responding and needs manual inspection.'
        Write-WatchLog 'ALERT' 'IT/Admin: run scripts\admin\check-system-health.ps1 to diagnose.'
        Write-WatchLog 'ALERT' 'To reset the watchdog: restart "FANS-C Watchdog" in Task Scheduler.'
        return
    }

    # Enforce cooldown between restart attempts
    if ($script:waitressLastRestart -ne [datetime]::MinValue) {
        $elapsed = ([datetime]::Now - $script:waitressLastRestart).TotalSeconds
        if ($elapsed -lt $COOLDOWN_SEC) {
            $remaining = [int]($COOLDOWN_SEC - $elapsed)
            Write-WatchLog 'WAIT' "Waitress restart cooldown active: $remaining seconds remaining before next attempt."
            return
        }
    }

    $attemptNum = $script:waitressRestartTimes.Count + 1
    Write-WatchLog 'ACTION' "Attempting automatic recovery of Waitress (attempt $attemptNum of $MAX_RESTARTS)..."

    # Kill any stale processes to prevent duplicate instances
    $stale = Get-Process -Name 'waitress-serve' -ErrorAction SilentlyContinue
    if ($stale) {
        Write-WatchLog 'ACTION' "Stopping $($stale.Count) stale Waitress process(es) before restart..."
        foreach ($p in $stale) {
            try { $p.Kill() } catch { }
        }
        Start-Sleep -Seconds 2
    }

    # Verify the executable is present
    if (-not (Test-Path $waitressExe)) {
        Write-WatchLog 'FAIL' "Cannot restart Waitress: waitress-serve.exe not found at $waitressExe"
        Write-WatchLog 'FAIL' 'IT/Admin: run scripts\setup\setup-complete.ps1 to repair the installation.'
        $script:waitressGivenUp = $true
        return
    }

    # Re-check the port -- if it recovered on its own (race condition), skip
    if (Test-PortOpen -Port 8000) {
        Write-WatchLog 'OK' 'Port 8000 is now responding -- Waitress recovered on its own. Restart skipped.'
        return
    }

    # Start a clean Waitress instance
    try {
        $proc = Start-Hidden `
            -Exe       $waitressExe `
            -Arguments '--listen=127.0.0.1:8000 fans.wsgi:application' `
            -WorkDir   $projectRoot

        $script:waitressLastRestart = [datetime]::Now
        $script:waitressRestartTimes.Add([datetime]::Now)
        Write-WatchLog 'ACTION' "Waitress restarted (PID $($proc.Id)). Waiting 25 seconds for Django and FaceNet to initialize..."
        Start-Sleep -Seconds 25

        if (Test-PortOpen -Port 8000) {
            Write-WatchLog 'OK' 'Recovery successful -- Waitress is now responding on port 8000.'
        } else {
            Write-WatchLog 'FAIL' 'Waitress restarted but port 8000 is still not responding after 25 seconds.'
            Write-WatchLog 'FAIL' 'Likely cause: Django startup error (.env misconfiguration or missing migration).'
            Write-WatchLog 'FAIL' 'IT/Admin: run scripts\start\start-fans.bat to see the Django error output.'
        }
    } catch {
        Write-WatchLog 'FAIL' "Could not launch Waitress: $_"
    }
}

# =============================================================================
# CADDY RECOVERY
# =============================================================================
function Invoke-CaddyRecovery {
    if ($script:caddyGivenUp) {
        Write-WatchLog 'SKIP' 'Caddy recovery skipped -- max attempts already reached. IT/Admin action required.'
        return
    }

    Prune-RestartWindow -Times $script:caddyRestartTimes

    if ($script:caddyRestartTimes.Count -ge $MAX_RESTARTS) {
        $script:caddyGivenUp = $true
        Write-WatchLog 'ALERT' "Caddy has failed $MAX_RESTARTS times in the last $([int]($RESTART_WINDOW_SEC / 60)) minutes."
        Write-WatchLog 'ALERT' 'Automatic recovery stopped to prevent a restart storm.'
        Write-WatchLog 'ALERT' 'HTTPS is down and needs manual inspection.'
        Write-WatchLog 'ALERT' 'IT/Admin: run scripts\admin\check-system-health.ps1 to diagnose.'
        Write-WatchLog 'ALERT' 'To reset the watchdog: restart "FANS-C Watchdog" in Task Scheduler.'
        return
    }

    if ($script:caddyLastRestart -ne [datetime]::MinValue) {
        $elapsed = ([datetime]::Now - $script:caddyLastRestart).TotalSeconds
        if ($elapsed -lt $COOLDOWN_SEC) {
            $remaining = [int]($COOLDOWN_SEC - $elapsed)
            Write-WatchLog 'WAIT' "Caddy restart cooldown active: $remaining seconds remaining before next attempt."
            return
        }
    }

    $caddyExe = Find-CaddyExe
    if (-not $caddyExe) {
        Write-WatchLog 'FAIL' 'Cannot restart Caddy: caddy.exe not found (checked tools\, PATH, D:\Tools\).'
        Write-WatchLog 'FAIL' 'IT/Admin: place caddy.exe in the tools\ folder and restart the watchdog task.'
        $script:caddyGivenUp = $true
        return
    }

    $attemptNum = $script:caddyRestartTimes.Count + 1
    Write-WatchLog 'ACTION' "Attempting automatic recovery of Caddy (attempt $attemptNum of $MAX_RESTARTS)..."

    $stale = Get-Process -Name 'caddy' -ErrorAction SilentlyContinue
    if ($stale) {
        Write-WatchLog 'ACTION' "Stopping $($stale.Count) stale Caddy process(es) before restart..."
        foreach ($p in $stale) {
            try { $p.Kill() } catch { }
        }
        Start-Sleep -Seconds 2
    }

    # Re-check the port before proceeding
    if (Test-PortOpen -Port 443) {
        Write-WatchLog 'OK' 'Port 443 is now responding -- Caddy recovered on its own. Restart skipped.'
        return
    }

    try {
        $proc = Start-Hidden `
            -Exe       $caddyExe `
            -Arguments 'run --config Caddyfile' `
            -WorkDir   $projectRoot

        $script:caddyLastRestart = [datetime]::Now
        $script:caddyRestartTimes.Add([datetime]::Now)
        Write-WatchLog 'ACTION' "Caddy restarted (PID $($proc.Id)). Waiting 8 seconds for HTTPS to bind..."
        Start-Sleep -Seconds 8

        if (Test-PortOpen -Port 443) {
            Write-WatchLog 'OK' 'Recovery successful -- Caddy is now listening on port 443.'
        } else {
            Write-WatchLog 'FAIL' 'Caddy restarted but port 443 is still not responding after 8 seconds.'
            Write-WatchLog 'FAIL' 'Likely cause: Caddyfile error, missing fans-cert.pem, or port 443 blocked.'
            Write-WatchLog 'FAIL' 'IT/Admin: run scripts\start\start-fans.bat to see the Caddy error output.'
        }
    } catch {
        Write-WatchLog 'FAIL' "Could not launch Caddy: $_"
    }
}

# =============================================================================
# STARTUP
# =============================================================================
Rotate-WatchLog

Write-WatchLog 'START' '============================================================'
Write-WatchLog 'START' 'FANS-C Watchdog is starting.'
Write-WatchLog 'START' "Project root  : $projectRoot"
Write-WatchLog 'START' "Check interval: ${CHECK_INTERVAL_SEC}s | Max restarts: $MAX_RESTARTS per $([int]($RESTART_WINDOW_SEC / 60))min | Cooldown: ${COOLDOWN_SEC}s"
Write-WatchLog 'START' '============================================================'

$cycleCount = 0

# =============================================================================
# MAIN WATCHDOG LOOP
# =============================================================================
while ($true) {
    $cycleCount++
    $cycleStart = [datetime]::Now

    # Rotate log if it has grown past 5 MB (checked every cycle, rotates silently)
    Rotate-WatchLog

    # ---- Collect health state ------------------------------------------------
    $waitressProcs = Get-Process -Name 'waitress-serve' -ErrorAction SilentlyContinue
    $waitressAlive = ($null -ne $waitressProcs -and $waitressProcs.Count -gt 0)
    $port8000      = Test-PortOpen -Port 8000
    # Only run the HTTP probe if the port is already open (saves time)
    $httpProbeOK   = if ($port8000) { Test-HttpProbe -Url 'http://127.0.0.1:8000/' } else { $false }

    $caddyProcs = Get-Process -Name 'caddy' -ErrorAction SilentlyContinue
    $caddyAlive = ($null -ne $caddyProcs -and $caddyProcs.Count -gt 0)
    $port443    = Test-PortOpen -Port 443

    # ---- Evaluate overall service health ------------------------------------
    # Waitress is OK only when process is alive AND port responds AND HTTP probe passes.
    # Caddy is OK when process is alive AND port 443 responds.
    $waitressOK = $waitressAlive -and $port8000 -and $httpProbeOK
    $caddyOK    = $caddyAlive -and $port443

    # ---- Log health status --------------------------------------------------
    if ($waitressOK -and $caddyOK) {
        # Log healthy state briefly every 10 cycles (~7.5 minutes) to reduce log noise
        if ($cycleCount % 10 -eq 1) {
            Write-WatchLog 'HEALTHY' 'All services running normally. Waitress OK (port 8000, HTTP probe OK). Caddy OK (port 443).'
        }
    } else {
        # Log each specific problem clearly for IT/Admin reading the log
        if (-not $waitressAlive) {
            Write-WatchLog 'WARN' 'Waitress is not running.'
        } elseif (-not $port8000) {
            Write-WatchLog 'WARN' 'Waitress process is running but port 8000 is not responding.'
        } elseif (-not $httpProbeOK) {
            Write-WatchLog 'WARN' 'Port 8000 is open but the application is not responding to HTTP requests.'
        }

        if (-not $caddyAlive) {
            Write-WatchLog 'WARN' 'Caddy is not running -- HTTPS is unavailable.'
        } elseif (-not $port443) {
            Write-WatchLog 'WARN' 'Caddy is running but port 443 is not listening.'
        }
    }

    # Reset failure counters independently -- each service recovers on its own terms.
    # Previously this only fired when both were healthy, which meant a broken Caddy
    # would permanently block Waitress counter resets even after Waitress recovered.
    if ($waitressOK -and $waitressGivenUp) {
        $waitressGivenUp = $false
        $waitressRestartTimes.Clear()
        Write-WatchLog 'OK' 'Waitress is now healthy -- failure counter has been reset.'
    }
    if ($caddyOK -and $caddyGivenUp) {
        $caddyGivenUp = $false
        $caddyRestartTimes.Clear()
        Write-WatchLog 'OK' 'Caddy is now healthy -- failure counter has been reset.'
    }

    # ---- Attempt recovery if unhealthy --------------------------------------
    if (-not $waitressOK) {
        Invoke-WaitressRecovery
    }

    if (-not $caddyOK) {
        Invoke-CaddyRecovery
    }

    # ---- Sleep until the next check cycle -----------------------------------
    $elapsed  = ([datetime]::Now - $cycleStart).TotalSeconds
    $sleepSec = [math]::Max(1, $CHECK_INTERVAL_SEC - [int]$elapsed)
    Start-Sleep -Seconds $sleepSec
}
