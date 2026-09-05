#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C runtime network and HTTPS proxy diagnostic.

.DESCRIPTION
    Checks the running FANS-C installation for common HTTPS/HTTP-mode issues.

    Reports:
      - Running fans_c.exe and caddy.exe process paths
      - Ports 80, 443, 8000 listening state
      - HTTPS end-to-end reachability (fans-barangay.local)
      - X-Forwarded-Proto header forwarding by Caddy
      - Django /system/connection/ diagnostics (is_secure, camera allowed)
      - HTTP fallback (192.168.x.x:8000) reachability
      - Audit log page HTTP status
      - Location of django-errors.log
      - Last 50 lines of django-errors.log (if errors exist)

    Run this whenever HTTPS appears broken or the "Limited HTTP mode"
    banner shows on a machine that should have HTTPS.

.EXAMPLE
    .\scripts\admin\check-runtime-network.ps1
#>

Set-StrictMode -Off   # allow missing variables without crashing
$ErrorActionPreference = 'SilentlyContinue'

# -- Helpers ------------------------------------------------------------------

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "  -- $Title $('-' * [Math]::Max(0, 55 - $Title.Length))" -ForegroundColor DarkCyan
}

function Write-Ok  { param([string]$msg) Write-Host "  [OK ]  $msg" -ForegroundColor Green  }
function Write-Warn{ param([string]$msg) Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Write-Fail{ param([string]$msg) Write-Host "  [FAIL] $msg" -ForegroundColor Red    }
function Write-Info{ param([string]$msg) Write-Host "  [INFO] $msg" -ForegroundColor Cyan   }

# -- Banner -------------------------------------------------------------------

Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "   FANS-C  |  Runtime Network Diagnostic                          " -ForegroundColor Cyan
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# -- 1. Running Processes ------------------------------------------------------

Write-Section "Running Processes"

$procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match "fans_c|caddy|waitress" } |
    Select-Object ProcessId, Name, ExecutablePath

if ($procs) {
    foreach ($p in $procs) {
        Write-Ok "PID $($p.ProcessId)  $($p.Name)  ->  $($p.ExecutablePath)"
    }
} else {
    Write-Warn "No fans_c.exe / caddy.exe / waitress processes found - is the app running?"
}

# Derive install dir from running fans_c.exe
$fansExe = ($procs | Where-Object { $_.Name -eq 'fans_c.exe' } | Select-Object -First 1).ExecutablePath
$installDir = if ($fansExe) { Split-Path $fansExe -Parent } else { $null }

if ($installDir) {
    Write-Info "Install directory: $installDir"
} else {
    Write-Warn "Could not determine install directory (fans_c.exe not running)"
    # Fall back to common paths
    foreach ($candidate in @('C:\FANSC', 'C:\FANS\dist\fans_c')) {
        if (Test-Path "$candidate\fans_c.exe") {
            $installDir = $candidate
            Write-Info "Found EXE at (not running): $installDir\fans_c.exe"
            break
        }
    }
}

# -- 2. Key Files -------------------------------------------------------------

Write-Section "Key Runtime Files"

# .env
if ($installDir) {
    $envFile = Join-Path $installDir '.env'
    if (Test-Path $envFile) {
        Write-Ok ".env found: $envFile"
        # Check for SECURE_PROXY_SSL_HEADER
        $envContent = Get-Content $envFile -ErrorAction SilentlyContinue
        $sslHeader  = $envContent | Where-Object { $_ -match 'SECURE_PROXY_SSL_HEADER' }
        if ($sslHeader) {
            if ($sslHeader -match 'off|false|0' -and $sslHeader -notmatch '#') {
                Write-Fail "SECURE_PROXY_SSL_HEADER is disabled in .env: $sslHeader"
                Write-Warn "  Fix: remove or comment out this line to use the safe default."
            } else {
                Write-Ok "SECURE_PROXY_SSL_HEADER in .env: $sslHeader"
            }
        } else {
            Write-Ok "SECURE_PROXY_SSL_HEADER not in .env - using built-in default (HTTP_X_FORWARDED_PROTO,https)"
        }
    } else {
        Write-Fail ".env not found at $envFile - app will fail to start"
    }

    # Caddyfile
    $caddyFile = Join-Path $installDir '_internal\Caddyfile'
    if (-not (Test-Path $caddyFile)) { $caddyFile = Join-Path $installDir 'Caddyfile' }
    if (Test-Path $caddyFile) {
        Write-Ok "Caddyfile: $caddyFile"
        $caddyContent = Get-Content $caddyFile -Raw -ErrorAction SilentlyContinue
        if ($caddyContent -match 'X-Forwarded-Proto') {
            Write-Ok "Caddyfile contains X-Forwarded-Proto header_up directive"
        } else {
            Write-Fail "Caddyfile MISSING X-Forwarded-Proto - Caddy will not tell Django the request is HTTPS"
        }
    } else {
        Write-Warn "Caddyfile not found in $installDir"
    }

    # Logs
    $logDir = Join-Path $installDir 'logs'
    if (Test-Path $logDir) {
        Write-Ok "Logs directory: $logDir"
        $errLog = Join-Path $logDir 'django-errors.log'
        if (Test-Path $errLog) {
            $errSize = (Get-Item $errLog).Length
            Write-Info "django-errors.log: $errLog  ($errSize bytes)"
        } else {
            Write-Ok "django-errors.log does not exist (no errors logged yet)"
        }
    } else {
        Write-Warn "Logs directory not found at $logDir"
    }
}

# -- 3. Port Listeners ---------------------------------------------------------

Write-Section "Port Listeners"

$netstat = netstat -ano 2>$null | Select-String "LISTENING"

foreach ($port in @(80, 443, 8000)) {
    $match = $netstat | Where-Object { $_ -match ":$port\s" }
    if ($match) {
        $pid_match = ($match | Select-Object -First 1) -replace '.*\s+(\d+)$', '$1'
        try {
            $proc = Get-Process -Id ([int]$pid_match.Trim()) -ErrorAction SilentlyContinue
            $procName = if ($proc) { $proc.Name } else { "PID $($pid_match.Trim())" }
        } catch { $procName = "PID $($pid_match.Trim())" }
        Write-Ok "Port $port LISTENING  [$procName]"
    } else {
        if ($port -eq 443) {
            Write-Fail "Port 443 NOT listening - Caddy HTTPS is down (or not yet started)"
        } elseif ($port -eq 8000) {
            Write-Fail "Port 8000 NOT listening - Django/Waitress is down"
        } else {
            Write-Warn "Port $port not listening"
        }
    }
}

# -- 4. HTTPS End-to-End -------------------------------------------------------

Write-Section "HTTPS End-to-End (fans-barangay.local)"

$curlExe = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source
if (-not $curlExe) { $curlExe = 'curl' }

# HTTPS root
try {
    $httpsRoot = & $curlExe -k -s -o $null -w "%{http_code}" --max-time 5 https://fans-barangay.local/ 2>$null
    if ($httpsRoot -match '^[23]') {
        Write-Ok "https://fans-barangay.local/  ->  HTTP $httpsRoot"
    } else {
        Write-Fail "https://fans-barangay.local/  ->  HTTP $httpsRoot (expected 2xx/3xx)"
    }
} catch {
    Write-Fail "https://fans-barangay.local/ unreachable: $_"
}

# HTTPS connection/system page
try {
    $connBody = & $curlExe -k -s --max-time 5 https://fans-barangay.local/system/connection/ 2>$null
    $connCode  = & $curlExe -k -s -o $null -w "%{http_code}" --max-time 5 https://fans-barangay.local/system/connection/ 2>$null
    if ($connCode -eq '200') {
        Write-Ok "https://fans-barangay.local/system/connection/  ->  200"
        # Parse key diagnostics from the page
        foreach ($keyword in @('is_secure.*True', 'X-Forwarded-Proto.*https', 'camera.*allowed', 'HTTPS')) {
            if ($connBody -match $keyword) {
                Write-Ok "  Diagnostics: found '$keyword'"
            }
        }
    } elseif ($connCode -match '^[23]') {
        Write-Ok "/system/connection/  ->  $connCode (login redirect expected without auth)"
    } else {
        Write-Warn "/system/connection/  ->  $connCode"
    }
} catch {
    Write-Warn "Could not reach /system/connection/: $_"
}

# -- 5. X-Forwarded-Proto Header Check ----------------------------------------

Write-Section "X-Forwarded-Proto Header Check"

try {
    $headers = & $curlExe -k -s -D - -o $null --max-time 5 https://fans-barangay.local/ 2>$null
    if ($headers -match 'X-Forwarded-Proto') {
        Write-Warn "X-Forwarded-Proto appears in RESPONSE headers - check if Caddy strips it correctly"
    } else {
        Write-Info "X-Forwarded-Proto not in response headers (Caddy sets it on the upstream request, not the response)"
    }
    # Strict-Transport-Security tells us Caddy is actually serving HTTPS
    if ($headers -match 'Strict-Transport-Security') {
        Write-Ok "Strict-Transport-Security header present - Caddy is serving HTTPS correctly"
    } else {
        Write-Warn "Strict-Transport-Security not found - Caddy may not be serving HTTPS"
    }
} catch {
    Write-Warn "Could not check response headers: $_"
}

# -- 6. HTTP Fallback ----------------------------------------------------------

Write-Section "HTTP Fallback (LAN IP)"

# Detect LAN IP
try {
    $lanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
              Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } |
              Select-Object -First 1).IPAddress
} catch { $lanIp = $null }

if ($lanIp) {
    try {
        $httpCode = & $curlExe -s -o $null -w "%{http_code}" --max-time 5 http://${lanIp}:8000/ 2>$null
        if ($httpCode -match '^[23]') {
            Write-Ok "http://${lanIp}:8000/  ->  HTTP $httpCode (fallback reachable)"
        } else {
            Write-Warn "http://${lanIp}:8000/  ->  HTTP $httpCode"
        }
    } catch {
        Write-Warn "http://${lanIp}:8000/ unreachable: $_"
    }
} else {
    Write-Warn "Could not detect LAN IP - skipping HTTP fallback check"
}

# -- 7. Audit Log Page --------------------------------------------------------

Write-Section "Audit Log Page (unauthenticated check)"

try {
    $auditCode = & $curlExe -k -s -o $null -w "%{http_code}" --max-time 5 https://fans-barangay.local/logs/audit/ 2>$null
    if ($auditCode -eq '302') {
        Write-Ok "/logs/audit/  ->  302 redirect (login required - expected without auth)"
    } elseif ($auditCode -eq '200') {
        Write-Ok "/logs/audit/  ->  200 (already authenticated session)"
    } elseif ($auditCode -eq '500') {
        Write-Fail "/logs/audit/  ->  500 Internal Server Error - check django-errors.log!"
    } else {
        Write-Info "/logs/audit/  ->  $auditCode"
    }
} catch {
    Write-Warn "Could not reach /logs/audit/: $_"
}

# -- 8. django-errors.log tail ------------------------------------------------

Write-Section "django-errors.log (last 50 lines)"

$errLogPaths = @()
if ($installDir) { $errLogPaths += Join-Path $installDir 'logs\django-errors.log' }
$errLogPaths += @(
    'C:\FANSC\logs\django-errors.log',
    'C:\FANS\logs\django-errors.log',
    'C:\FANS\dist\fans_c\logs\django-errors.log'
)

$foundLog = $false
foreach ($logPath in $errLogPaths) {
    if (Test-Path $logPath) {
        $logSize = (Get-Item $logPath).Length
        if ($logSize -gt 0) {
            Write-Info "Found django-errors.log at: $logPath  ($logSize bytes)"
            Write-Host ""
            Write-Host "  --- Last 50 lines of $logPath ---" -ForegroundColor DarkGray
            Get-Content $logPath -Tail 50 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
        } else {
            Write-Ok "django-errors.log is empty (no errors): $logPath"
        }
        $foundLog = $true
        break
    }
}
if (-not $foundLog) {
    Write-Ok "django-errors.log not found in any known location - no errors logged yet"
}

# -- Summary -------------------------------------------------------------------

Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "   Diagnostic complete. Review FAIL/WARN items above.             " -ForegroundColor Cyan
Write-Host "   For HTTPS issues: check Caddy is running (port 443) and that   " -ForegroundColor Cyan
Write-Host "   .env does not have SECURE_PROXY_SSL_HEADER=off.                " -ForegroundColor Cyan
Write-Host "   For audit log issues: check django-errors.log above.           " -ForegroundColor Cyan
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host ""
