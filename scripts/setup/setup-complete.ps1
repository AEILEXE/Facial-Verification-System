#Requires -Version 5.1
<#
.SYNOPSIS
    FANSC Master Setup -- IT/Admin one-time entry point.
    Runs the complete server setup flow from start to finish.

.DESCRIPTION
    This is the recommended starting point for IT/Admin when setting up
    a new FANSC server. It orchestrates every required step in order,
    verifies each result, and confirms the system is actually working
    before declaring success.

    What it does (in order):
      1. Runs setup-secure-server.ps1  (venv, deps, certs, Django setup)
      2. Verifies certificate files    (fans-cert.pem / fans-cert-key.pem)
      3. Verifies Caddy executable     (bundled in tools\ or on PATH)
      4. Verifies .env configuration   (SECRET_KEY, EMBEDDING_ENCRYPTION_KEY)
      5. Runs setup-autostart.ps1      (Task Scheduler registration)
      6. Creates desktop shortcut      (optional, prompts user)
      7. Runs live startup validation  (starts services, checks ports 8000 + 443)
      8. Registers watchdog task       (self-healing monitor, auto-restarts on failure)

    At the end, prints a clear PASS / FAIL summary for every step.

    If all steps PASS, the Head Barangay daily workflow is:
        1. Turn on the PC
        2. Wait about 30 seconds for the system to start
        3. Open any browser
        4. Go to:  https://fans-barangay.local
        5. Log in normally

.PARAMETER SkipDeps
    Pass -SkipDeps to skip virtual environment creation and pip install
    in the sub-script (use when re-running and .venv is already set up).

.NOTES
    Run as Administrator:
        Right-click -> Run with PowerShell
        -- or --
        Start-Process powershell -Verb RunAs -ArgumentList "-File .\scripts\setup\setup-complete.ps1"

    Lower-level scripts (still available for advanced use):
        scripts\setup\setup-secure-server.ps1   -- server certs + Django setup
        scripts\setup\setup-autostart.ps1        -- Task Scheduler only
        scripts\start\start-fans.bat             -- debug launcher (visible windows)
        scripts\start\start-fans-quiet.bat       -- manual daily launcher
        scripts\admin\stop-fans.ps1              -- stop services
        scripts\admin\check-system-health.ps1    -- live health diagnostics

.EXAMPLE
    .\scripts\setup\setup-complete.ps1
    .\scripts\setup\setup-complete.ps1 -SkipDeps
#>

param(
    [switch]$SkipDeps,
    [switch]$ForceRegenerateCert
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $projectRoot

# -- Paths --------------------------------------------------------------------
$venvWaitress        = Join-Path $projectRoot '.venv\Scripts\waitress-serve.exe'
$caddyBundled        = Join-Path $projectRoot 'tools\caddy.exe'
$stableCert          = Join-Path $projectRoot 'fans-cert.pem'
$stableCertKey       = Join-Path $projectRoot 'fans-cert-key.pem'
$envFile             = Join-Path $projectRoot '.env'
$setupServerScript   = Join-Path $projectRoot 'scripts\setup\setup-secure-server.ps1'
$setupAutoStart      = Join-Path $projectRoot 'scripts\setup\setup-autostart.ps1'
$shortcutScript      = Join-Path $projectRoot 'scripts\setup\Create-Desktop-Shortcut.ps1'

# -- Step result tracking -----------------------------------------------------
$results = [ordered]@{}

# -- Helpers ------------------------------------------------------------------

function Write-Step {
    param([string]$Num, [string]$Text)
    Write-Host ''
    Write-Host "  [$Num] $Text" -ForegroundColor Cyan
    Write-Host "  $('-' * 60)" -ForegroundColor DarkGray
}

function Write-OK   { param([string]$Msg); Write-Host "         [OK]   $Msg" -ForegroundColor Green   }
function Write-Fail { param([string]$Msg); Write-Host "         [FAIL] $Msg" -ForegroundColor Red     }
function Write-Warn { param([string]$Msg); Write-Host "         [WARN] $Msg" -ForegroundColor Yellow  }
function Write-Info { param([string]$Msg); Write-Host "         [INFO] $Msg" -ForegroundColor DarkGray}

function Test-PortOpen {
    param([int]$Port, [int]$Retries = 5, [int]$DelayMs = 1000)
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

function Find-CaddyExe {
    if (Test-Path $caddyBundled) { return $caddyBundled }
    try {
        $found = Get-Command caddy -ErrorAction Stop
        return $found.Source
    } catch { }
    if (Test-Path 'D:\Tools\caddy.exe') { return 'D:\Tools\caddy.exe' }
    return $null
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

# -- Administrator check ------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host ''
    Write-Host '  [FAIL] This script must be run as Administrator.' -ForegroundColor Red
    Write-Host '         Right-click and choose "Run with PowerShell",' -ForegroundColor Yellow
    Write-Host '         then approve the UAC prompt.' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

# -- Banner -------------------------------------------------------------------
Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   FANSC  |  Complete Server Setup (Recommended Entry Point)' -ForegroundColor Cyan
Write-Host '   IT/Admin: run this ONCE to set up a new server machine.' -ForegroundColor DarkGray
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''
Write-Host '  This script runs all required setup steps in order, verifies' -ForegroundColor White
Write-Host '  each result, and confirms the system is actually serving' -ForegroundColor White
Write-Host '  before declaring setup complete.' -ForegroundColor White
Write-Host ''
Write-Host '  Steps:' -ForegroundColor DarkGray
Write-Host '    [1/8] setup-secure-server.ps1  -- venv, deps, certs, Django' -ForegroundColor DarkGray
Write-Host '    [2/8] Verify certificate files -- fans-cert.pem exists' -ForegroundColor DarkGray
Write-Host '    [3/8] Verify Caddy executable  -- caddy.exe found' -ForegroundColor DarkGray
Write-Host '    [4/8] Verify .env keys         -- SECRET_KEY + encryption key' -ForegroundColor DarkGray
Write-Host '    [5/8] setup-autostart.ps1      -- Task Scheduler registration' -ForegroundColor DarkGray
Write-Host '    [6/8] Desktop shortcut         -- optional' -ForegroundColor DarkGray
Write-Host '    [7/8] Live startup validation  -- verify ports 8000 + 443' -ForegroundColor DarkGray
Write-Host '    [8/8] Watchdog task            -- self-healing background monitor' -ForegroundColor DarkGray
Write-Host ''
Write-Host "  Project: $projectRoot" -ForegroundColor DarkGray
Write-Host "  Date   : $(Get-Date -Format 'yyyy-MM-dd HH:mm')" -ForegroundColor DarkGray
Write-Host ''
Read-Host '  Press Enter to begin setup'

# =============================================================================
# STEP 1 -- Run setup-secure-server.ps1
# =============================================================================
Write-Step '1/8' 'Running setup-secure-server.ps1 (venv, dependencies, certs, Django)...'
Write-Info 'This sub-script runs below. Follow its prompts.'
Write-Info 'It will install Python packages and generate TLS certificates.'
if ($SkipDeps) {
    Write-Warn 'Passing -SkipDeps: dependency installation will be skipped.'
}
Write-Host ''

if (-not (Test-Path $setupServerScript)) {
    Write-Fail "setup-secure-server.ps1 not found at: $setupServerScript"
    $results['[1/8] Server Setup'] = 'FAIL -- script missing'
    Read-Host '  Press Enter to exit'
    exit 1
}

$skipDepsArg  = if ($SkipDeps)             { ' -SkipDeps' }             else { '' }
$forceCertArg = if ($ForceRegenerateCert) { ' -ForceRegenerateCert' }  else { '' }
$proc1 = Start-Process powershell.exe `
    -ArgumentList "-ExecutionPolicy Bypass -NoLogo -File `"$setupServerScript`"$skipDepsArg$forceCertArg" `
    -Wait -PassThru -NoNewWindow

Write-Host ''
if ($proc1.ExitCode -ne 0) {
    Write-Fail "setup-secure-server.ps1 exited with code $($proc1.ExitCode)."
    Write-Fail 'Server setup did not complete successfully.'
    Write-Warn 'Review the output above, fix the reported error, then re-run this script.'
    $results['[1/8] Server Setup'] = "FAIL (exit code $($proc1.ExitCode))"
    Read-Host '  Press Enter to exit'
    exit 1
}

Write-OK 'setup-secure-server.ps1 completed.'
$results['[1/8] Server Setup'] = 'PASS'

# =============================================================================
# STEP 2 -- Verify certificate files
# =============================================================================
Write-Step '2/8' 'Verifying TLS certificate files...'

$certOK = $true

if (Test-Path $stableCert) {
    Write-OK 'fans-cert.pem found.'
} else {
    Write-Fail 'fans-cert.pem is MISSING.'
    Write-Warn 'Caddy cannot start HTTPS without this file.'
    Write-Warn 'Re-run setup-secure-server.ps1 and confirm it completes without errors.'
    $certOK = $false
}

if (Test-Path $stableCertKey) {
    Write-OK 'fans-cert-key.pem found.'
} else {
    Write-Fail 'fans-cert-key.pem is MISSING.'
    Write-Warn 'Re-run setup-secure-server.ps1 to regenerate the certificate.'
    $certOK = $false
}

$results['[2/8] Cert Files'] = if ($certOK) { 'PASS' } else { 'FAIL -- cert file(s) missing' }

if (-not $certOK) {
    Write-Host ''
    Write-Fail 'Certificate files are required. Cannot continue without them.'
    Write-Warn 'Fix: re-run setup-secure-server.ps1 and ensure no errors appear during cert generation.'
    Read-Host '  Press Enter to exit'
    exit 1
}

# =============================================================================
# STEP 3 -- Verify Caddy executable
# =============================================================================
Write-Step '3/8' 'Verifying Caddy executable...'

$caddyExe = Find-CaddyExe
if ($caddyExe) {
    Write-OK "Caddy found: $caddyExe"
    $results['[3/8] Caddy Exe'] = 'PASS'
} else {
    Write-Fail 'caddy.exe not found.'
    Write-Warn "Expected bundled location  : $caddyBundled"
    Write-Warn 'Or place caddy.exe on PATH : C:\Windows\System32\ or similar'
    Write-Warn 'Download from              : https://caddyserver.com/docs/install'
    Write-Warn 'Place as                   : tools\caddy.exe  (recommended)'
    $results['[3/8] Caddy Exe'] = 'FAIL -- caddy.exe not found'
    Read-Host '  Press Enter to exit'
    exit 1
}

# =============================================================================
# STEP 4 -- Verify .env configuration
# =============================================================================
Write-Step '4/8' 'Verifying .env configuration...'

$envOK = $true

if (-not (Test-Path $envFile)) {
    Write-Fail '.env file is missing.'
    Write-Warn 'Run setup-secure-server.ps1 to create and configure it.'
    $results['[4/8] .env Config'] = 'FAIL -- .env missing'
    Read-Host '  Press Enter to exit'
    exit 1
}

$envRaw = Get-Content $envFile -Raw -Encoding UTF8

# Check SECRET_KEY
if ($envRaw -match '(?m)^SECRET_KEY\s*=\s*\S+') {
    Write-OK 'SECRET_KEY is set.'
} else {
    Write-Fail 'SECRET_KEY is missing or empty in .env.'
    Write-Warn 'Run setup-secure-server.ps1 to generate it automatically.'
    $envOK = $false
}

# Check EMBEDDING_ENCRYPTION_KEY
if ($envRaw -match '(?m)^EMBEDDING_ENCRYPTION_KEY\s*=\s*\S+') {
    Write-OK 'EMBEDDING_ENCRYPTION_KEY is set.'
} else {
    Write-Fail 'EMBEDDING_ENCRYPTION_KEY is missing or empty in .env.'
    Write-Warn 'Without this key, stored face data cannot be decrypted.'
    Write-Warn 'Run setup-secure-server.ps1 to generate it.'
    $envOK = $false
}

# Warn if DEBUG is True (not a blocker, but important for production)
if ($envRaw -match '(?m)^DEBUG\s*=\s*True') {
    Write-Warn 'DEBUG=True is set in .env.'
    Write-Warn 'For production (real barangay use), set DEBUG=False in .env.'
} else {
    Write-OK 'DEBUG is not True (production-safe).'
}

$results['[4/8] .env Config'] = if ($envOK) { 'PASS' } else { 'FAIL -- required key(s) missing' }

if (-not $envOK) {
    Write-Host ''
    Write-Fail 'Required .env keys are missing. Cannot continue.'
    Read-Host '  Press Enter to exit'
    exit 1
}

# =============================================================================
# STEP 5 -- Run setup-autostart.ps1
# =============================================================================
Write-Step '5/8' 'Running setup-autostart.ps1 (Task Scheduler registration)...'
Write-Info 'This registers a scheduled task so FANSC starts automatically at every boot.'
Write-Info 'The Head Barangay will never need to start it manually.'
Write-Host ''

if (-not (Test-Path $setupAutoStart)) {
    Write-Fail "setup-autostart.ps1 not found at: $setupAutoStart"
    $results['[5/8] Auto-Start'] = 'FAIL -- script missing'
    Read-Host '  Press Enter to exit'
    exit 1
}

$proc2 = Start-Process powershell.exe `
    -ArgumentList "-ExecutionPolicy Bypass -NoLogo -File `"$setupAutoStart`"" `
    -Wait -PassThru -NoNewWindow

Write-Host ''
if ($proc2.ExitCode -ne 0) {
    Write-Warn "setup-autostart.ps1 exited with code $($proc2.ExitCode)."
    Write-Warn 'Auto-start may not have been registered correctly.'
    Write-Warn 'You can re-run scripts\setup\setup-autostart.ps1 separately.'
    $results['[5/8] Auto-Start'] = "WARN (exit code $($proc2.ExitCode))"
} else {
    Write-OK 'Auto-start task registered.'
    $results['[5/8] Auto-Start'] = 'PASS'
}

# =============================================================================
# STEP 6 -- Create desktop shortcut (optional)
# =============================================================================
Write-Step '6/8' 'Desktop shortcut (optional)...'

$createSC = Read-Host '  Create a desktop shortcut for the manual daily launcher? [Y/N]'
if ($createSC -match '^[Yy]') {
    if (Test-Path $shortcutScript) {
        $proc3 = Start-Process powershell.exe `
            -ArgumentList "-ExecutionPolicy Bypass -NoLogo -File `"$shortcutScript`"" `
            -Wait -PassThru -NoNewWindow
        Write-Host ''
        if ($proc3.ExitCode -eq 0) {
            Write-OK 'Desktop shortcut created.'
            $results['[6/8] Desktop Shortcut'] = 'PASS'
        } else {
            Write-Warn "Shortcut script exited with code $($proc3.ExitCode)."
            Write-Warn 'Run scripts\setup\Create-Desktop-Shortcut.ps1 manually to retry.'
            $results['[6/8] Desktop Shortcut'] = "WARN (exit code $($proc3.ExitCode))"
        }
    } else {
        Write-Warn "Create-Desktop-Shortcut.ps1 not found: $shortcutScript"
        $results['[6/8] Desktop Shortcut'] = 'SKIPPED -- script not found'
    }
} else {
    Write-Info 'Skipped. Run scripts\setup\Create-Desktop-Shortcut.ps1 later if needed.'
    $results['[6/8] Desktop Shortcut'] = 'SKIPPED (user choice)'
}

# =============================================================================
# STEP 7 -- Live startup validation
# =============================================================================
Write-Step '7/8' 'Live startup validation...'
Write-Host ''
Write-Host '  This test starts Waitress and Caddy in the background,' -ForegroundColor White
Write-Host '  then verifies they are actually accepting connections.' -ForegroundColor White
Write-Host '  Services are stopped after the test finishes.' -ForegroundColor DarkGray
Write-Host ''

$runVal = Read-Host '  Run live startup validation? [Y/N] (strongly recommended: Y)'
if ($runVal -notmatch '^[Yy]') {
    Write-Info 'Skipped. Run scripts\admin\check-system-health.ps1 to validate later.'
    $results['[7/8] Startup Validation'] = 'SKIPPED (user choice)'
} else {
    # Stop any already-running instances for a clean test
    Write-Info 'Stopping any existing Waitress / Caddy for a clean test...'
    Stop-Process -Name 'waitress-serve' -Force -ErrorAction SilentlyContinue
    Stop-Process -Name 'caddy'          -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    if (-not (Test-Path $venvWaitress)) {
        Write-Fail "waitress-serve.exe not found: $venvWaitress"
        Write-Warn 'Re-run setup-secure-server.ps1 to install Python dependencies.'
        $results['[7/8] Startup Validation'] = 'FAIL -- waitress-serve.exe missing'
    } else {
        $wProc = $null
        $cProc = $null
        $port8000OK = $false
        $port443OK  = $false

        # -- Start Waitress ---------------------------------------------------
        Write-Info 'Starting Waitress on port 8000...'
        Write-Warn 'On first run, Django downloads the FaceNet model (~90 MB). Port 8000 may take up to 90 seconds.'
        Write-Warn 'If the port check below reports FAIL on a brand-new install, wait 2 minutes and run check-system-health.ps1.'
        try {
            $wProc = Start-Hidden `
                -Exe       $venvWaitress `
                -Arguments '--listen=127.0.0.1:8000 fans.wsgi:application' `
                -WorkDir   $projectRoot
            Write-Info "Waitress started (PID $($wProc.Id)). Waiting 25 seconds for Django to load..."
            Start-Sleep -Seconds 25
        } catch {
            Write-Fail "Could not launch Waitress: $_"
        }

        # -- Check port 8000 --------------------------------------------------
        if ($wProc -and -not $wProc.HasExited) {
            $port8000OK = Test-PortOpen -Port 8000 -Retries 12 -DelayMs 5000
            if ($port8000OK) {
                Write-OK 'Port 8000 (Waitress)  -- LISTENING'
            } else {
                Write-Fail 'Port 8000 (Waitress)  -- NOT RESPONDING'
                Write-Warn 'Probable causes:'
                Write-Warn '  - Django startup error (run start-fans.bat to see the error)'
                Write-Warn '  - .env missing EMBEDDING_ENCRYPTION_KEY'
                Write-Warn '  - Database migrations not applied (manage.py migrate)'
            }
        } else {
            Write-Fail 'Waitress process exited immediately after launch.'
            Write-Warn 'Run scripts\start\start-fans.bat to see the full error output.'
        }

        # -- Start Caddy ------------------------------------------------------
        Write-Info 'Starting Caddy on port 443...'
        try {
            $cProc = Start-Hidden `
                -Exe       $caddyExe `
                -Arguments 'run --config Caddyfile' `
                -WorkDir   $projectRoot
            Write-Info "Caddy started (PID $($cProc.Id)). Waiting 8 seconds for HTTPS to bind..."
            Start-Sleep -Seconds 8
        } catch {
            Write-Fail "Could not launch Caddy: $_"
        }

        # -- Check port 443 ---------------------------------------------------
        if ($cProc -and -not $cProc.HasExited) {
            $port443OK = Test-PortOpen -Port 443 -Retries 5 -DelayMs 1000
            if ($port443OK) {
                Write-OK 'Port 443  (Caddy HTTPS) -- LISTENING'
            } else {
                Write-Fail 'Port 443  (Caddy HTTPS) -- NOT RESPONDING'
                Write-Warn 'Probable causes:'
                Write-Warn '  - fans-cert.pem missing or wrong path in Caddyfile'
                Write-Warn '  - Another process is using port 443 (netstat -ano | findstr :443)'
                Write-Warn '  - Windows Firewall blocking port 443'
                Write-Warn '  - Caddyfile syntax error'
                Write-Warn 'For details: run start-fans.bat (Caddy error will be visible)'
            }
        } else {
            Write-Fail 'Caddy process exited immediately after launch.'
            Write-Warn 'Run scripts\start\start-fans.bat to see the full Caddy error output.'
        }

        # -- HTTPS end-to-end probe -------------------------------------------
        $httpsOK = $false
        if ($port8000OK -and $port443OK) {
            Write-Info 'Verifying HTTPS end-to-end (DNS -> Caddy -> Django)...'
            $httpsOK = Test-HttpsProbe
            if ($httpsOK) {
                Write-OK 'HTTPS end-to-end  -- https://fans-barangay.local responds'
            } else {
                Write-Fail 'HTTPS end-to-end  -- ports OK but HTTPS not routing to Django'
                Write-Warn 'Likely cause: fans-barangay.local missing from server hosts file.'
                Write-Warn 'Fix: run scripts\admin\repair-hosts.ps1 (as Admin) then reboot.'
            }
        }

        # -- Record result ----------------------------------------------------
        if ($port8000OK -and $port443OK -and $httpsOK) {
            $results['[7/8] Startup Validation'] = 'PASS -- ports 8000 and 443 responding, HTTPS verified end-to-end'
        } elseif ($port8000OK -and $port443OK -and -not $httpsOK) {
            $results['[7/8] Startup Validation'] = 'WARN -- ports OK but HTTPS end-to-end failed (run repair-hosts.ps1)'
        } elseif ($port8000OK) {
            $results['[7/8] Startup Validation'] = 'FAIL -- port 8000 OK but port 443 NOT responding'
        } elseif ($port443OK) {
            $results['[7/8] Startup Validation'] = 'FAIL -- port 443 OK but port 8000 NOT responding'
        } else {
            $results['[7/8] Startup Validation'] = 'FAIL -- neither port 8000 nor 443 is responding'
        }

        # -- Stop test services -----------------------------------------------
        Write-Info 'Stopping test services...'
        if ($cProc -and -not $cProc.HasExited) {
            try { $cProc.Kill() } catch { }
        }
        if ($wProc -and -not $wProc.HasExited) {
            try { $wProc.Kill() } catch { }
        }
        Stop-Process -Name 'waitress-serve' -Force -ErrorAction SilentlyContinue
        Stop-Process -Name 'caddy'          -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
        Write-Info 'Test services stopped.'
        Write-Info 'The system will start automatically at next boot via Task Scheduler.'
    }
}

# =============================================================================
# STEP 8 -- Register watchdog task
# =============================================================================
Write-Step '8/8' 'Registering watchdog task (self-healing background monitor)...'
Write-Info 'The watchdog checks Waitress and Caddy every 45 seconds while the PC is on.'
Write-Info 'If either service stops, it restarts it automatically -- no IT visit needed.'
Write-Info 'It starts 150 seconds after every boot, after the main startup task and FaceNet model load finish.'
Write-Host ''

$watchdogScript   = Join-Path $projectRoot 'scripts\admin\watchdog.ps1'
$watchdogTaskName = 'FANS-C Watchdog'

if (-not (Test-Path $watchdogScript)) {
    Write-Fail "watchdog.ps1 not found: $watchdogScript"
    Write-Warn 'Self-healing will not be available until this file is restored.'
    $results['[8/8] Watchdog Task'] = 'FAIL -- watchdog.ps1 missing'
} else {
    try {
        $wdArgs = "-WindowStyle Hidden -ExecutionPolicy Bypass -NonInteractive -File `"$watchdogScript`""
        $wdAction = New-ScheduledTaskAction `
            -Execute          'powershell.exe' `
            -Argument         $wdArgs `
            -WorkingDirectory $projectRoot

        # Fire at system startup, delayed 150 seconds so main startup task and FaceNet load finish first
        $wdTrigger       = New-ScheduledTaskTrigger -AtStartup
        $wdTrigger.Delay = 'PT150S'

        $wdSettings = New-ScheduledTaskSettingsSet `
            -ExecutionTimeLimit      (New-TimeSpan -Hours 0) `
            -MultipleInstances       IgnoreNew `
            -StartWhenAvailable      `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -RestartCount            3 `
            -RestartInterval         (New-TimeSpan -Minutes 2)

        $wdPrincipal = New-ScheduledTaskPrincipal `
            -UserId    'SYSTEM' `
            -LogonType ServiceAccount `
            -RunLevel  Highest

        $existing = Get-ScheduledTask -TaskName $watchdogTaskName -ErrorAction SilentlyContinue
        if ($existing) {
            Write-Info 'Existing watchdog task found -- replacing with updated definition...'
            Unregister-ScheduledTask -TaskName $watchdogTaskName -Confirm:$false
        }

        Register-ScheduledTask `
            -TaskName    $watchdogTaskName `
            -Action      $wdAction `
            -Trigger     $wdTrigger `
            -Settings    $wdSettings `
            -Principal   $wdPrincipal `
            -Description 'Self-healing watchdog for FANSC. Monitors Waitress and Caddy every 45 seconds. Automatically restarts failed services. Managed by IT/Admin via setup-complete.ps1. Do not delete or disable.' `
            -Force | Out-Null

        Write-OK "Watchdog task registered: '$watchdogTaskName'"
        Write-Info 'Starts 150 seconds after every boot (after main startup task and FaceNet load).'
        Write-Info "Watchdog log: $projectRoot\logs\fans-watchdog.log"
        $results['[8/8] Watchdog Task'] = 'PASS'
    } catch {
        Write-Warn "Could not register watchdog task: $($_.Exception.Message)"
        Write-Warn 'The system will still run -- watchdog provides self-healing but is not required.'
        Write-Warn 'To register manually: re-run this script, or register FANS-C Watchdog in Task Scheduler.'
        $results['[8/8] Watchdog Task'] = "WARN (registration failed: $($_.Exception.Message))"
    }
}

# =============================================================================
# FINAL SUMMARY
# =============================================================================
Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   FANSC  |  Setup Summary' -ForegroundColor Cyan
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''

$anyFail = $false
$anyWarn = $false

foreach ($step in $results.Keys) {
    $val = $results[$step]
    if ($val -like 'PASS*') {
        Write-Host "   [PASS] $step" -ForegroundColor Green
    } elseif ($val -like 'FAIL*') {
        Write-Host "   [FAIL] $step" -ForegroundColor Red
        Write-Host "           $val" -ForegroundColor DarkGray
        $anyFail = $true
    } elseif ($val -like 'WARN*') {
        Write-Host "   [WARN] $step" -ForegroundColor Yellow
        Write-Host "           $val" -ForegroundColor DarkGray
        $anyWarn = $true
    } else {
        Write-Host "   [SKIP] $step" -ForegroundColor DarkGray
        Write-Host "           $val" -ForegroundColor DarkGray
    }
}

Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan

if ($anyFail) {
    Write-Host ''
    Write-Host '  RESULT: SETUP INCOMPLETE' -ForegroundColor Red
    Write-Host ''
    Write-Host '  One or more required steps failed (see [FAIL] lines above).' -ForegroundColor Red
    Write-Host '  Fix each reported error and re-run this script before putting' -ForegroundColor Yellow
    Write-Host '  the system into use.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  Diagnostic tools:' -ForegroundColor DarkGray
    Write-Host '    scripts\start\start-fans.bat           -- debug start (shows all output)' -ForegroundColor DarkGray
    Write-Host '    scripts\admin\check-system-health.ps1  -- live health status' -ForegroundColor DarkGray
} else {
    Write-Host ''
    Write-Host '  RESULT: SETUP COMPLETE' -ForegroundColor Green
    if ($anyWarn) {
        Write-Host '  (with warnings -- review [WARN] items above)' -ForegroundColor Yellow
    }
    Write-Host ''
    Write-Host '  Auto-start and self-healing are now active:' -ForegroundColor Green
    Write-Host '    - FANS-C Verification System  : starts Waitress + Caddy at every boot' -ForegroundColor DarkGray
    Write-Host '    - FANS-C Watchdog             : checks health every 45s, auto-restarts on failure' -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '  IT/Admin -- remaining tasks:' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '    1. Edit .env -- verify ALLOWED_HOSTS and CSRF_TRUSTED_ORIGINS' -ForegroundColor Yellow
    Write-Host '       Example (replace IP with your server LAN IP):' -ForegroundColor DarkGray
    Write-Host '         ALLOWED_HOSTS=fans-barangay.local,192.168.1.77,localhost,127.0.0.1' -ForegroundColor DarkGray
    Write-Host '         CSRF_TRUSTED_ORIGINS=https://fans-barangay.local' -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '    2. Set up each client device:' -ForegroundColor Yellow
    Write-Host '       Copy the CLIENT-SETUP\ folder to a USB drive.' -ForegroundColor DarkGray
    Write-Host '       On each client PC, run: CLIENT-SETUP\trust-local-cert.bat (as Admin)' -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '    3. Verify a client can reach: https://fans-barangay.local' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  Head Barangay daily workflow (no scripts, no terminal):' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '    1. Turn on the PC' -ForegroundColor White
    Write-Host '    2. Wait about 30 seconds for the system to start' -ForegroundColor White
    Write-Host '    3. Open any browser' -ForegroundColor White
    Write-Host '    4. Go to:  https://fans-barangay.local' -ForegroundColor Cyan
    Write-Host '    5. Log in normally' -ForegroundColor White
    Write-Host ''
    Write-Host '  IT/Admin repair and diagnostic tools:' -ForegroundColor DarkGray
    Write-Host '    scripts\admin\fans-control-center.ps1  -- all-in-one admin tool (recommended)' -ForegroundColor DarkGray
    Write-Host '    scripts\admin\check-system-health.ps1  -- live health status anytime' -ForegroundColor DarkGray
    Write-Host '    scripts\admin\start-now.ps1            -- start services without rebooting' -ForegroundColor DarkGray
    Write-Host '    scripts\admin\repair-autostart.ps1     -- fix Task Scheduler auto-start task' -ForegroundColor DarkGray
    Write-Host '    scripts\admin\repair-watchdog.ps1      -- fix watchdog task' -ForegroundColor DarkGray
    Write-Host '    scripts\admin\create-admin-user.ps1    -- add a Django admin account' -ForegroundColor DarkGray
    Write-Host '    logs\fans-startup.log                  -- last boot startup result' -ForegroundColor DarkGray
    Write-Host '    logs\fans-watchdog.log                 -- watchdog activity and recovery history' -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''
Read-Host '  Press Enter to close'
