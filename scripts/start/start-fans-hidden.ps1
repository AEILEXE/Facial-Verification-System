#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C Hidden Launcher -- called by Windows Task Scheduler at boot.
    Starts Waitress and Caddy as background processes with NO visible windows.

.DESCRIPTION
    This script is invoked automatically by Task Scheduler every time the
    PC boots. The Head Barangay does not run it manually.

    What it does:
      1. Waits 12 seconds for Windows networking to fully initialize
      2. Verifies required files (.env, waitress-serve.exe, fans-cert.pem, caddy.exe)
      3. Starts Waitress (Django WSGI server) -- no window, port 8000
      4. Waits 25 seconds for Django to load
      5. Starts Caddy (HTTPS reverse proxy) -- no window, port 443
      6. Waits 8 seconds for Caddy to bind its port
      7. Verifies port 8000 and port 443 are actually listening
      8. Writes a detailed startup log to logs\fans-startup.log

    STARTUP STATUS values logged:
      STARTUP OK      -- both ports responding, system is ready
      STARTUP PARTIAL -- Waitress OK but HTTPS failed (HTTPS unavailable)
      STARTUP FAILED  -- Waitress did not start (system unavailable)

    FOR DAILY USE:    Runs automatically. The Head Barangay does nothing.
    TO CHECK STATUS:  Read logs\fans-startup.log or run check-system-health.ps1
    TO STOP:          Run scripts\admin\stop-fans.ps1 (IT/Admin only)
    TO DEBUG:         Run scripts\start\start-fans.bat (visible windows + full output)
    TO SET UP:        Run scripts\setup\setup-complete.ps1 (IT/Admin, one-time)
#>

$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $projectRoot

# -- Log setup ----------------------------------------------------------------
$logsDir = Join-Path $projectRoot 'logs'
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
}
$logFile = Join-Path $logsDir 'fans-startup.log'

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    "$ts  $Message" | Add-Content -Path $logFile -Encoding UTF8
}

# -- Port check helper --------------------------------------------------------
function Test-PortListening {
    param([int]$Port, [int]$Retries = 6, [int]$DelayMs = 1000)
    for ($i = 0; $i -lt $Retries; $i++) {
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $tcp.Connect('127.0.0.1', $Port)
            $tcp.Close()
            return $true
        } catch { }
        if ($i -lt ($Retries - 1)) { Start-Sleep -Milliseconds $DelayMs }
    }
    return $false
}

# -- HTTPS end-to-end probe ---------------------------------------------------
function Test-HttpsProbe {
    param([string]$Uri = 'https://fans-barangay.local/', [int]$TimeoutMs = 6000)
    try {
        [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
        $req         = [System.Net.HttpWebRequest]::Create($Uri)
        $req.Timeout = $TimeoutMs
        $req.Method  = 'GET'
        $resp        = $req.GetResponse()
        $resp.Close()
        return $true
    } catch [System.Net.WebException] {
        if ($null -ne $_.Exception.Response) { return $true }
        return $false
    } catch {
        return $false
    } finally {
        [Net.ServicePointManager]::ServerCertificateValidationCallback = $null
    }
}

# =============================================================================
# BEGIN STARTUP
# =============================================================================
Write-Log '============================================================'
Write-Log "FANS-C auto-start: beginning startup sequence"
Write-Log "Project root: $projectRoot"

# -- Wait for Windows networking ----------------------------------------------
Write-Log 'Waiting 12 seconds for Windows networking to initialize...'
Start-Sleep -Seconds 12

# -- PRE-FLIGHT: verify waitress-serve.exe ------------------------------------
$waitressExe = Join-Path $projectRoot '.venv\Scripts\waitress-serve.exe'
if (-not (Test-Path $waitressExe)) {
    Write-Log 'FAIL: waitress-serve.exe not found.'
    Write-Log "      Expected: $waitressExe"
    Write-Log '      Fix: IT/Admin must run scripts\setup\setup-complete.ps1 first.'
    Write-Log 'STARTUP FAILED -- system is not installed correctly'
    Write-Log '============================================================'
    exit 1
}

# -- PRE-FLIGHT: verify .env --------------------------------------------------
$envFile = Join-Path $projectRoot '.env'
if (-not (Test-Path $envFile)) {
    Write-Log 'FAIL: .env file not found.'
    Write-Log "      Expected: $envFile"
    Write-Log '      Fix: IT/Admin must run scripts\setup\setup-complete.ps1 first.'
    Write-Log 'STARTUP FAILED -- server is not configured'
    Write-Log '============================================================'
    exit 1
}

# -- PRE-FLIGHT: verify TLS certificate (REQUIRED for HTTPS) ------------------
$stableCert    = Join-Path $projectRoot 'fans-cert.pem'
$stableCertKey = Join-Path $projectRoot 'fans-cert-key.pem'
$certAvailable = $true

if (-not (Test-Path $stableCert)) {
    Write-Log 'FAIL: fans-cert.pem is missing.'
    Write-Log '      HTTPS will NOT be available this session.'
    Write-Log '      Fix: IT/Admin must run scripts\setup\setup-complete.ps1 to regenerate.'
    $certAvailable = $false
}

if (-not (Test-Path $stableCertKey)) {
    Write-Log 'FAIL: fans-cert-key.pem is missing.'
    Write-Log '      HTTPS will NOT be available this session.'
    Write-Log '      Fix: IT/Admin must run scripts\setup\setup-complete.ps1 to regenerate.'
    $certAvailable = $false
}

# -- PRE-FLIGHT: locate caddy.exe ---------------------------------------------
$caddyExe = $null

$candidate = Join-Path $projectRoot 'tools\caddy.exe'
if (Test-Path $candidate) { $caddyExe = $candidate }

if (-not $caddyExe) {
    try {
        $found    = Get-Command caddy -ErrorAction Stop
        $caddyExe = $found.Source
    } catch { }
}

if (-not $caddyExe -and (Test-Path 'D:\Tools\caddy.exe')) {
    $caddyExe = 'D:\Tools\caddy.exe'
}

if (-not $caddyExe) {
    Write-Log 'FAIL: caddy.exe not found (checked tools\, PATH, D:\Tools\).'
    Write-Log '      HTTPS will NOT be available this session.'
    Write-Log '      Fix: place caddy.exe in the project tools\ folder.'
}

# -- STOP STALE INSTANCES -----------------------------------------------------
$staleWaitress = Get-Process -Name 'waitress-serve' -ErrorAction SilentlyContinue
if ($staleWaitress) {
    Write-Log "Stopping $($staleWaitress.Count) stale Waitress process(es) before start..."
    foreach ($p in $staleWaitress) { try { $p.Kill() } catch { } }
}
$staleCaddy = Get-Process -Name 'caddy' -ErrorAction SilentlyContinue
if ($staleCaddy) {
    Write-Log "Stopping $($staleCaddy.Count) stale Caddy process(es) before start..."
    foreach ($p in $staleCaddy) { try { $p.Kill() } catch { } }
}
if ($staleWaitress -or $staleCaddy) {
    Write-Log 'Waiting 3 seconds for ports to release...'
    Start-Sleep -Seconds 3
}

# -- START WAITRESS (no window, WorkingDirectory = project root) --------------
Set-Location $projectRoot
Write-Log "Starting Waitress: $waitressExe"

$wProc = $null
try {
    $wProc = Start-Process `
        -FilePath    $waitressExe `
        -ArgumentList '--listen=127.0.0.1:8000 fans.wsgi:application' `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -PassThru
    $wProc.Id | Set-Content (Join-Path $projectRoot '.fans-waitress.pid') -Encoding UTF8
    Write-Log "Waitress launched (PID $($wProc.Id)). Waiting 25 seconds for Django to load..."
} catch {
    Write-Log "FAIL: Could not launch Waitress: $_"
    Write-Log 'STARTUP FAILED -- Waitress did not start'
    Write-Log '============================================================'
    exit 1
}

Start-Sleep -Seconds 25

# -- START CADDY (no window, only if cert exists and caddy found) -------------
$cProc = $null

if ($caddyExe -and $certAvailable) {
    Set-Location $projectRoot
    Write-Log "Starting Caddy: $caddyExe"
    try {
        $cProc = Start-Process `
            -FilePath    $caddyExe `
            -ArgumentList 'run --config Caddyfile' `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden `
            -PassThru
        $cProc.Id | Set-Content (Join-Path $projectRoot '.fans-caddy.pid') -Encoding UTF8
        Write-Log "Caddy launched (PID $($cProc.Id)). Waiting 8 seconds for HTTPS to bind..."
    } catch {
        Write-Log "FAIL: Could not launch Caddy: $_"
        Write-Log '      Waitress is still running. HTTP fallback is available.'
        Write-Log '      HTTPS will NOT be available until Caddy is fixed.'
    }
    Start-Sleep -Seconds 8
} elseif (-not $certAvailable) {
    Write-Log 'SKIP: Caddy not started -- TLS certificate is missing (see FAIL above).'
    Write-Log '      Waitress will still run. HTTP access at port 8000 is available.'
    Write-Log '      To restore HTTPS: IT/Admin must re-run setup-complete.ps1 and reboot.'
} else {
    Write-Log 'SKIP: Caddy not started -- caddy.exe not found.'
    Write-Log '      Waitress will still run. HTTP access at port 8000 is available.'
}

# =============================================================================
# VERIFY PORTS ARE ACTUALLY LISTENING
# =============================================================================
Write-Log 'Verifying services are actually listening...'

# -- Check port 8000 (Waitress) -----------------------------------------------
$port8000OK = Test-PortListening -Port 8000 -Retries 25 -DelayMs 1000

if ($port8000OK) {
    Write-Log 'CHECK port 8000 (Waitress)  : LISTENING -- OK'
} else {
    Write-Log 'CHECK port 8000 (Waitress)  : NOT RESPONDING -- FAIL'
    Write-Log '      The Django application did not start correctly.'
    Write-Log '      Possible causes:'
    Write-Log '        - .env EMBEDDING_ENCRYPTION_KEY is missing or invalid'
    Write-Log '        - Database migrations have not been applied (manage.py migrate)'
    Write-Log '        - Python import error during Django startup'
    Write-Log '      For details: ask IT/Admin to run scripts\start\start-fans.bat'
}

# -- Check port 443 (Caddy) ---------------------------------------------------
$port443OK = $false

if ($cProc) {
    if ($cProc.HasExited) {
        Write-Log "CHECK port 443  (Caddy)     : PROCESS EXITED (code $($cProc.ExitCode)) -- FAIL"
        Write-Log '      Caddy crashed immediately after launch.'
        Write-Log '      Possible causes:'
        Write-Log '        - fans-cert.pem referenced incorrectly in Caddyfile'
        Write-Log '        - Caddyfile syntax error'
        Write-Log '        - Port 443 already in use by another process'
        Write-Log '      For details: ask IT/Admin to run scripts\start\start-fans.bat'
    } else {
        $port443OK = Test-PortListening -Port 443 -Retries 6 -DelayMs 1000
        if ($port443OK) {
            Write-Log 'CHECK port 443  (Caddy)     : LISTENING -- OK'
        } else {
            Write-Log 'CHECK port 443  (Caddy)     : NOT RESPONDING -- FAIL'
            Write-Log '      Caddy process is running but port 443 is not accepting connections.'
            Write-Log '      Possible causes:'
            Write-Log '        - fans-cert.pem path in Caddyfile does not match actual file location'
            Write-Log '        - Windows Firewall blocking port 443'
            Write-Log '        - Caddyfile TLS configuration error'
            Write-Log '      For details: ask IT/Admin to run scripts\start\start-fans.bat'
        }
    }
} else {
    Write-Log 'CHECK port 443  (Caddy)     : SKIPPED (Caddy not started)'
    Write-Log '      HTTPS is unavailable. HTTP fallback (port 8000) may still work.'
}

# =============================================================================
# HTTPS END-TO-END PROBE (only if both ports are up)
# =============================================================================
$httpsOK = $false
if ($port8000OK -and $port443OK) {
    Write-Log 'Verifying HTTPS end-to-end (fans-barangay.local -> Caddy -> Django)...'
    $httpsOK = Test-HttpsProbe
    if ($httpsOK) {
        Write-Log 'CHECK HTTPS end-to-end   : OK -- https://fans-barangay.local responds'
    } else {
        Write-Log 'CHECK HTTPS end-to-end   : FAIL -- port 443 open but HTTPS not routing'
        Write-Log '      Likely cause: fans-barangay.local missing from server hosts file.'
        Write-Log '      Fix: IT/Admin run scripts\admin\repair-hosts.ps1 (as Admin).'
    }
}

# =============================================================================
# STARTUP STATUS SUMMARY
# =============================================================================
if ($port8000OK -and $port443OK -and $httpsOK) {
    Write-Log 'STARTUP STATUS: OK -- Waitress (8000) and HTTPS (443) verified end-to-end'
    Write-Log '                System is accessible at https://fans-barangay.local'
} elseif ($port8000OK -and $port443OK -and -not $httpsOK) {
    Write-Log 'STARTUP STATUS: PARTIAL -- Ports OK but HTTPS end-to-end failed'
    Write-Log '                Fix: IT/Admin run scripts\admin\repair-hosts.ps1 (as Admin).'
} elseif ($port8000OK -and -not $cProc) {
    Write-Log 'STARTUP STATUS: PARTIAL -- Waitress OK, Caddy not started (missing cert or exe)'
    Write-Log '                HTTPS is unavailable. IT/Admin action required.'
} elseif ($port8000OK) {
    Write-Log 'STARTUP STATUS: PARTIAL -- Waitress OK, but Caddy (port 443) failed'
    Write-Log '                HTTPS is unavailable. IT/Admin action required.'
    Write-Log '                Run scripts\start\start-fans.bat to see Caddy error details.'
} else {
    Write-Log 'STARTUP STATUS: FAILED -- Waitress (port 8000) did not respond'
    Write-Log '                System is NOT accessible from any browser.'
    Write-Log '                IT/Admin must investigate immediately.'
    Write-Log '                Run scripts\start\start-fans.bat to see error details.'
}

Write-Log '============================================================'
