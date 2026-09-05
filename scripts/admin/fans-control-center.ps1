#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C IT/Admin Control Center -- single-point admin tool.
    Replaces the need to remember individual PowerShell commands.

.DESCRIPTION
    Provides a simple numbered menu for all common IT/Admin operations:
      Start / Stop / Restart services
      Check system health
      View startup and watchdog logs
      Repair auto-start task
      Repair watchdog task
      Create / add admin user
      Open site in browser

.NOTES
    Run as Administrator.
    Right-click -> Run with PowerShell, approve UAC prompt.

.EXAMPLE
    .\scripts\admin\fans-control-center.ps1
#>

$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $projectRoot

# -- Admin check ---------------------------------------------------------------
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

# -- Paths ---------------------------------------------------------------------
$venvWaitress      = Join-Path $projectRoot '.venv\Scripts\waitress-serve.exe'
$caddyBundled      = Join-Path $projectRoot 'tools\caddy.exe'
$stableCert        = Join-Path $projectRoot 'fans-cert.pem'
$envFile           = Join-Path $projectRoot '.env'
$startupLog        = Join-Path $projectRoot 'logs\fans-startup.log'
$watchdogLog       = Join-Path $projectRoot 'logs\fans-watchdog.log'
$repairAutoStart   = Join-Path $projectRoot 'scripts\admin\repair-autostart.ps1'
$repairWatchdog    = Join-Path $projectRoot 'scripts\admin\repair-watchdog.ps1'
$healthScript      = Join-Path $projectRoot 'scripts\admin\check-system-health.ps1'
$createAdminScript = Join-Path $projectRoot 'scripts\admin\create-admin-user.ps1'
$repairHosts       = Join-Path $projectRoot 'scripts\admin\repair-hosts.ps1'

# -- Helpers -------------------------------------------------------------------

function Find-CaddyExe {
    if (Test-Path $caddyBundled) { return $caddyBundled }
    try { $f = Get-Command caddy -ErrorAction Stop; return $f.Source } catch {}
    if (Test-Path 'D:\Tools\caddy.exe') { return 'D:\Tools\caddy.exe' }
    return $null
}

function Test-PortOpen {
    param([int]$Port)
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect('127.0.0.1', $Port)
        $tcp.Close()
        return $true
    } catch { return $false }
}

function Test-HttpProbe {
    # Returns $true if Django responds with any HTTP reply (even 4xx/5xx).
    # Returns $false only if the connection is refused or times out (server down).
    param([string]$Url = 'http://127.0.0.1:8000/', [int]$TimeoutMs = 4000)
    try {
        $req         = [System.Net.HttpWebRequest]::Create($Url)
        $req.Timeout = $TimeoutMs
        $req.Method  = 'GET'
        $resp        = $req.GetResponse()
        $resp.Close()
        return $true
    } catch [System.Net.WebException] {
        if ($null -ne $_.Exception.Response) { return $true }
        return $false
    } catch { return $false }
}

function Test-HttpsProbe {
    # Performs an end-to-end HTTPS test: DNS -> TCP -> TLS (SNI fans-barangay.local) -> Django.
    # Bypasses certificate chain validation (mkcert CA is local; chain won't verify from PS).
    # Returns $true if Caddy+Django return any HTTP response over HTTPS.
    # Returns $false on DNS fail, TCP fail, TLS fail, or timeout (HTTPS is broken).
    param([string]$Uri = 'https://fans-barangay.local/', [int]$TimeoutMs = 5000)
    try {
        [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
        $req         = [System.Net.HttpWebRequest]::Create($Uri)
        $req.Timeout = $TimeoutMs
        $req.Method  = 'GET'
        $resp        = $req.GetResponse()
        $resp.Close()
        return $true
    } catch [System.Net.WebException] {
        # HTTP error response (4xx/5xx) means HTTPS+routing is working fine
        if ($null -ne $_.Exception.Response) { return $true }
        return $false
    } catch {
        return $false
    } finally {
        [Net.ServicePointManager]::ServerCertificateValidationCallback = $null
    }
}

function Wait-PortRelease {
    # Waits up to $MaxSec seconds for both ports to stop being bound.
    # Called after killing processes to ensure clean re-bind on restart.
    param([int]$MaxSec = 8)
    $waited = 0
    while ($waited -lt $MaxSec) {
        if (-not (Test-PortOpen 443) -and -not (Test-PortOpen 8000)) { break }
        Start-Sleep -Seconds 1
        $waited++
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

function Write-StatusLine {
    $wRunning = $null -ne (Get-Process 'waitress-serve' -ErrorAction SilentlyContinue)
    $cRunning = $null -ne (Get-Process 'caddy'          -ErrorAction SilentlyContinue)
    $p8000    = Test-PortOpen 8000
    $p443     = Test-PortOpen 443
    $httpOK   = if ($p8000) { Test-HttpProbe }   else { $false }
    $httpsOK  = if ($p443)  { Test-HttpsProbe }  else { $false }

    $waitressOK = $wRunning -and $p8000 -and $httpOK
    $caddyOK    = $cRunning -and $p443 -and $httpsOK

    if ($waitressOK -and $caddyOK) {
        Write-Host '  Status : RUNNING  -- HTTPS verified end-to-end' -ForegroundColor Green
        Write-Host '  Site   : https://fans-barangay.local' -ForegroundColor Cyan
    } elseif ($wRunning -or $cRunning -or $p8000 -or $p443) {
        Write-Host '  Status : PARTIAL  -- one or more services not healthy' -ForegroundColor Yellow
        if (-not $waitressOK) {
            if (-not $wRunning)  { Write-Host '           Waitress process    -- NOT running' -ForegroundColor Yellow }
            elseif (-not $p8000) { Write-Host '           Port 8000           -- NOT listening' -ForegroundColor Yellow }
            else                 { Write-Host '           Django HTTP probe   -- NOT responding' -ForegroundColor Yellow }
        }
        if (-not $caddyOK) {
            if (-not $cRunning)    { Write-Host '           Caddy process       -- NOT running' -ForegroundColor Yellow }
            elseif (-not $p443)    { Write-Host '           Port 443 (HTTPS)    -- NOT listening' -ForegroundColor Yellow }
            elseif (-not $httpsOK) { Write-Host '           HTTPS end-to-end   -- NOT responding (Caddy running but routing broken)' -ForegroundColor Yellow }
        }
    } else {
        Write-Host '  Status : STOPPED  -- no services running' -ForegroundColor Red
        Write-Host '           The system starts automatically on next reboot.' -ForegroundColor DarkGray
    }
}

function Show-Menu {
    Clear-Host
    Write-Host ''
    Write-Host '  ================================================================' -ForegroundColor DarkCyan
    Write-Host '   FANS-C  |  IT/Admin Control Center' -ForegroundColor Cyan
    Write-Host "   $projectRoot" -ForegroundColor DarkGray
    Write-Host '  ================================================================' -ForegroundColor DarkCyan
    Write-Host ''
    Write-StatusLine
    Write-Host ''
    Write-Host '  -- Services --------------------------------------------------' -ForegroundColor DarkCyan
    Write-Host '   [1]  Start system now' -ForegroundColor White
    Write-Host '   [2]  Stop system' -ForegroundColor White
    Write-Host '   [3]  Restart system' -ForegroundColor White
    Write-Host ''
    Write-Host '  -- Diagnostics -----------------------------------------------' -ForegroundColor DarkCyan
    Write-Host '   [4]  Check system health (full report)' -ForegroundColor White
    Write-Host '   [5]  View startup log' -ForegroundColor White
    Write-Host '   [6]  View watchdog log' -ForegroundColor White
    Write-Host ''
    Write-Host '  -- Repair ----------------------------------------------------' -ForegroundColor DarkCyan
    Write-Host '   [7]  Repair auto-start task (Task Scheduler)' -ForegroundColor White
    Write-Host '   [8]  Repair watchdog task' -ForegroundColor White
    Write-Host '   [H]  Repair hosts file  (fix https://fans-barangay.local)' -ForegroundColor White
    Write-Host ''
    Write-Host '  -- Admin -----------------------------------------------------' -ForegroundColor DarkCyan
    Write-Host '   [9]  Create / add admin user' -ForegroundColor White
    Write-Host '   [O]  Open site in browser  (https://fans-barangay.local)' -ForegroundColor White
    Write-Host ''
    Write-Host '   [X]  Exit' -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '  ================================================================' -ForegroundColor DarkCyan
    Write-Host ''
}

function Invoke-StartSystem {
    Write-Host ''
    Write-Host '  Starting FANS-C system...' -ForegroundColor Cyan
    Write-Host ''

    $wRunning = $null -ne (Get-Process 'waitress-serve' -ErrorAction SilentlyContinue)
    $cRunning = $null -ne (Get-Process 'caddy'          -ErrorAction SilentlyContinue)
    if ($wRunning -and $cRunning) {
        Write-Host '  [OK] System is already running.' -ForegroundColor Green
        Write-Host ''
        Read-Host '  Press Enter to return to menu'
        return
    }

    if (-not (Test-Path $venvWaitress)) {
        Write-Host '  [FAIL] waitress-serve.exe not found.' -ForegroundColor Red
        Write-Host "         Expected: $venvWaitress" -ForegroundColor Yellow
        Write-Host '         Run setup-complete.ps1 first.' -ForegroundColor Yellow
        Write-Host ''
        Read-Host '  Press Enter to return to menu'
        return
    }

    $caddyExe = Find-CaddyExe
    if (-not $caddyExe) {
        Write-Host "  [FAIL] caddy.exe not found (checked: $caddyBundled)" -ForegroundColor Red
        Write-Host ''
        Read-Host '  Press Enter to return to menu'
        return
    }

    if (-not (Test-Path $stableCert)) {
        Write-Host '  [FAIL] fans-cert.pem missing -- Caddy cannot start HTTPS.' -ForegroundColor Red
        Write-Host '         Fix: run scripts\setup\setup-complete.ps1 -ForceRegenerateCert' -ForegroundColor Yellow
        Write-Host ''
        Read-Host '  Press Enter to return to menu'
        return
    }

    if (-not (Test-Path $envFile)) {
        Write-Host '  [FAIL] .env file missing -- Django cannot start.' -ForegroundColor Red
        Write-Host '         Fix: run scripts\setup\setup-complete.ps1' -ForegroundColor Yellow
        Write-Host ''
        Read-Host '  Press Enter to return to menu'
        return
    }

    Stop-Process -Name 'waitress-serve' -Force -ErrorAction SilentlyContinue
    Stop-Process -Name 'caddy'          -Force -ErrorAction SilentlyContinue
    Write-Host '  Waiting for ports to release...' -ForegroundColor DarkGray
    Wait-PortRelease -MaxSec 8

    Write-Host '  Starting Waitress on port 8000...' -ForegroundColor DarkGray
    try {
        $null = Start-Hidden -Exe $venvWaitress `
            -Arguments '--listen=127.0.0.1:8000 fans.wsgi:application' `
            -WorkDir $projectRoot
    } catch {
        Write-Host "  [FAIL] Could not start Waitress: $_" -ForegroundColor Red
        Write-Host ''
        Read-Host '  Press Enter to return to menu'
        return
    }

    Write-Host '  Waiting for Django to load (first start may take up to 90s for FaceNet)...' -ForegroundColor DarkGray
    Start-Sleep -Seconds 15

    $port8000 = $false
    for ($i = 0; $i -lt 4; $i++) {
        if (Test-PortOpen 8000) { $port8000 = $true; break }
        Start-Sleep -Seconds 5
    }

    if ($port8000) {
        Write-Host '  [OK] Waitress is running on port 8000.' -ForegroundColor Green
    } else {
        Write-Host '  [WARN] Port 8000 not yet responding -- Django may still be loading.' -ForegroundColor Yellow
    }

    Write-Host '  Starting Caddy on port 443...' -ForegroundColor DarkGray
    try {
        $null = Start-Hidden -Exe $caddyExe `
            -Arguments 'run --config Caddyfile' `
            -WorkDir $projectRoot
    } catch {
        Write-Host "  [FAIL] Could not start Caddy: $_" -ForegroundColor Red
        Write-Host ''
        Read-Host '  Press Enter to return to menu'
        return
    }

    Start-Sleep -Seconds 8
    $port443 = Test-PortOpen 443

    if ($port443) {
        Write-Host '  [OK] Caddy is running on port 443.' -ForegroundColor Green
    } else {
        Write-Host '  [WARN] Port 443 not yet responding. Use [4] Check Health for details.' -ForegroundColor Yellow
    }

    # End-to-end HTTPS probe: DNS -> TCP -> TLS -> Caddy -> Django
    $httpsOK = $false
    if ($port443) {
        Write-Host '  Verifying HTTPS end-to-end (Caddy -> Django)...' -ForegroundColor DarkGray
        Start-Sleep -Seconds 2
        $httpsOK = Test-HttpsProbe
        if ($httpsOK) {
            Write-Host '  [OK] HTTPS end-to-end verified -- https://fans-barangay.local is reachable.' -ForegroundColor Green
        } else {
            Write-Host '  [WARN] HTTPS probe failed -- Caddy is running but not routing to Django.' -ForegroundColor Yellow
            Write-Host '         Likely cause: fans-barangay.local not in server hosts file.' -ForegroundColor DarkGray
            Write-Host '         Fix: run scripts\admin\repair-hosts.ps1 (as Admin)' -ForegroundColor Yellow
        }
    }

    Write-Host ''
    if ($port8000 -and $port443 -and $httpsOK) {
        Write-Host '  System started. Site: https://fans-barangay.local' -ForegroundColor Green
    } elseif ($port8000 -and $port443) {
        Write-Host '  Waitress and Caddy are up but HTTPS end-to-end failed.' -ForegroundColor Yellow
        Write-Host '  Use [4] Check Health or run repair-hosts.ps1 to diagnose.' -ForegroundColor Yellow
    } else {
        Write-Host '  Services launched with warnings. Use [4] Check Health to diagnose.' -ForegroundColor Yellow
    }
    Write-Host ''
    Read-Host '  Press Enter to return to menu'
}

function Invoke-StopSystem {
    Write-Host ''
    Write-Host '  Stopping FANS-C system...' -ForegroundColor Cyan
    Write-Host ''

    $wRunning = $null -ne (Get-Process 'waitress-serve' -ErrorAction SilentlyContinue)
    $cRunning = $null -ne (Get-Process 'caddy'          -ErrorAction SilentlyContinue)

    if (-not $wRunning -and -not $cRunning) {
        Write-Host '  [OK] System is already stopped.' -ForegroundColor Green
        Write-Host ''
        Read-Host '  Press Enter to return to menu'
        return
    }

    Stop-Process -Name 'waitress-serve' -Force -ErrorAction SilentlyContinue
    Stop-Process -Name 'caddy'          -Force -ErrorAction SilentlyContinue
    Wait-PortRelease -MaxSec 8

    $w2 = $null -ne (Get-Process 'waitress-serve' -ErrorAction SilentlyContinue)
    $c2 = $null -ne (Get-Process 'caddy'          -ErrorAction SilentlyContinue)

    if (-not $w2 -and -not $c2) {
        Write-Host '  [OK] All services stopped.' -ForegroundColor Green
    } else {
        Write-Host '  [WARN] Some processes may still be shutting down.' -ForegroundColor Yellow
    }
    Write-Host ''
    Read-Host '  Press Enter to return to menu'
}

function Invoke-RestartSystem {
    Write-Host ''
    Write-Host '  Restarting FANS-C system...' -ForegroundColor Cyan
    Write-Host ''

    Stop-Process -Name 'waitress-serve' -Force -ErrorAction SilentlyContinue
    Stop-Process -Name 'caddy'          -Force -ErrorAction SilentlyContinue
    Write-Host '  Waiting for services and ports to fully stop...' -ForegroundColor DarkGray
    Wait-PortRelease -MaxSec 10

    $stillW = $null -ne (Get-Process 'waitress-serve' -ErrorAction SilentlyContinue)
    $stillC = $null -ne (Get-Process 'caddy'          -ErrorAction SilentlyContinue)
    if ($stillW -or $stillC) {
        Write-Host '  [WARN] One or more processes did not exit cleanly. Continuing anyway...' -ForegroundColor Yellow
    } else {
        Write-Host '  [OK] All services stopped.' -ForegroundColor Green
    }

    Write-Host '  Starting fresh...' -ForegroundColor DarkGray
    # Call Invoke-StartSystem with the "already running" guard bypassed by the
    # stop above -- processes are gone so the guard will not fire.
    Invoke-StartSystem
}

function Show-Log {
    param([string]$LogPath, [string]$Label, [int]$Lines = 50)
    Write-Host ''
    Write-Host "  -- $Label" -ForegroundColor DarkCyan
    Write-Host ''
    if (-not (Test-Path $LogPath)) {
        Write-Host '  Log file not found. Has the system been started yet?' -ForegroundColor Yellow
    } else {
        $content = Get-Content $LogPath -Encoding UTF8 -ErrorAction SilentlyContinue |
                   Select-Object -Last $Lines
        if (-not $content) {
            Write-Host '  Log file is empty.' -ForegroundColor DarkGray
        } else {
            foreach ($line in $content) {
                if     ($line -match 'FAIL|ERROR|ALERT')              { Write-Host "  $line" -ForegroundColor Red     }
                elseif ($line -match 'WARN')                          { Write-Host "  $line" -ForegroundColor Yellow  }
                elseif ($line -match '\bOK\b|HEALTHY|LISTENING|SUCCESS') { Write-Host "  $line" -ForegroundColor Green   }
                else                                                   { Write-Host "  $line" -ForegroundColor DarkGray }
            }
        }
    }
    Write-Host ''
    Read-Host '  Press Enter to return to menu'
}

function Invoke-CallScript {
    param([string]$ScriptPath, [string]$Label)
    if (-not (Test-Path $ScriptPath)) {
        Write-Host ''
        Write-Host "  [FAIL] Script not found: $ScriptPath" -ForegroundColor Red
        Write-Host ''
        Read-Host '  Press Enter to return to menu'
        return
    }
    Write-Host ''
    Write-Host "  Running: $Label" -ForegroundColor Cyan
    Write-Host ''
    $proc = Start-Process powershell.exe `
        -ArgumentList "-ExecutionPolicy Bypass -NoLogo -File `"$ScriptPath`"" `
        -Wait -PassThru -NoNewWindow
    Write-Host ''
    if ($proc.ExitCode -ne 0) {
        Write-Host "  Script exited with code $($proc.ExitCode)." -ForegroundColor Yellow
    }
    Write-Host ''
    Read-Host '  Press Enter to return to menu'
}

# =============================================================================
# MAIN LOOP
# =============================================================================
while ($true) {
    Show-Menu
    $choice = Read-Host '  Enter choice'

    switch ($choice.ToUpper().Trim()) {
        '1' { Invoke-StartSystem }
        '2' { Invoke-StopSystem }
        '3' { Invoke-RestartSystem }
        '4' { Invoke-CallScript $healthScript      'System Health Check' }
        '5' { Show-Log $startupLog  'Startup Log (last 50 lines)' }
        '6' { Show-Log $watchdogLog 'Watchdog Log (last 50 lines)' }
        '7' { Invoke-CallScript $repairAutoStart   'Repair Auto-Start Task' }
        '8' { Invoke-CallScript $repairWatchdog    'Repair Watchdog Task' }
        'H' { Invoke-CallScript $repairHosts       'Repair Hosts File' }
        '9' { Invoke-CallScript $createAdminScript 'Create Admin User' }
        'O' {
            Write-Host ''
            Write-Host '  Opening https://fans-barangay.local ...' -ForegroundColor Cyan
            Start-Process 'https://fans-barangay.local'
            Start-Sleep -Seconds 1
        }
        'X' {
            Write-Host ''
            Write-Host '  Closing FANS-C Control Center.' -ForegroundColor DarkGray
            Write-Host ''
            exit 0
        }
        default {
            Write-Host ''
            Write-Host '  Invalid choice. Enter a number or letter from the menu above.' -ForegroundColor Yellow
            Start-Sleep -Seconds 1
        }
    }
}
