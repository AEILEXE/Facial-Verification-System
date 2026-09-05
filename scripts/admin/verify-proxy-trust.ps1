#Requires -Version 5.1
<#
.SYNOPSIS
    Verify that FANS-C is running with Waitress proxy-trust headers configured.

.DESCRIPTION
    After installing FANS-C and waiting for fans_c.exe to start (~60 seconds
    on first boot), run this script as Administrator from the FANS-C install
    folder to confirm that the Waitress proxy-trust startup lines were written
    to logs\stderr.log.

    Expected output on a correctly configured install:
      [PASS] fans_c.exe is running (PID XXXX)
      [PASS] stderr.log found
      [PASS] Waitress trusted_proxy: 127.0.0.1
      [PASS] Waitress trusted_proxy_count: 1
      [PASS] Waitress trusted_proxy_headers: x-forwarded-proto
      [PASS] Waitress clear_untrusted_proxy_headers: True
      [PASS] Waitress log_untrusted_proxy_headers: True
      All proxy-trust checks passed.

    If any line shows [FAIL], the frozen EXE does not have the proxy-trust
    patch.  Reinstall using the latest FANS-C-Setup.exe.

.EXAMPLE
    # Run from the FANS-C install folder (e.g. C:\FANSC):
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\admin\verify-proxy-trust.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

# Resolve install folder: two levels up from this script's directory
$installDir = (Get-Item (Join-Path $PSScriptRoot '..\..')).FullName
$stderrLog  = Join-Path $installDir 'logs\stderr.log'

Write-Host ""
Write-Host "  FANS-C Proxy-Trust Verification" -ForegroundColor Cyan
Write-Host "  Install folder: $installDir" -ForegroundColor DarkGray
Write-Host ""

$allPassed = $true

function Write-Pass {
    param([string]$Label)
    Write-Host ("  [PASS] " + $Label) -ForegroundColor Green
}

function Write-Fail {
    param([string]$Label)
    Write-Host ("  [FAIL] " + $Label) -ForegroundColor Red
    $script:allPassed = $false
}

# 1. Is fans_c.exe running?
$proc = Get-Process -Name fans_c -ErrorAction SilentlyContinue | Select-Object -First 1
if ($proc) {
    Write-Pass ("fans_c.exe is running (PID " + $proc.Id + ")")
} else {
    Write-Fail "fans_c.exe is not running"
}

# 2. Does stderr.log exist?
$logExists = Test-Path $stderrLog -PathType Leaf
if ($logExists) {
    Write-Pass ("stderr.log found at " + $stderrLog)
} else {
    Write-Fail ("stderr.log not found at " + $stderrLog)
}

if ($logExists) {
    $content = Get-Content $stderrLog -Raw -ErrorAction SilentlyContinue

    # 3-7. Check each expected proxy-trust line
    $checks = @(
        @{ Label = "Waitress trusted_proxy: 127.0.0.1";                 Pattern = "\[FANS-C\] Waitress trusted_proxy: 127\.0\.0\.1" },
        @{ Label = "Waitress trusted_proxy_count: 1";                   Pattern = "\[FANS-C\] Waitress trusted_proxy_count: 1" },
        @{ Label = "Waitress trusted_proxy_headers: x-forwarded-proto"; Pattern = "\[FANS-C\] Waitress trusted_proxy_headers:.*x-forwarded-proto" },
        @{ Label = "Waitress clear_untrusted_proxy_headers: True";      Pattern = "\[FANS-C\] Waitress clear_untrusted_proxy_headers: True" },
        @{ Label = "Waitress log_untrusted_proxy_headers: True";        Pattern = "\[FANS-C\] Waitress log_untrusted_proxy_headers: True" }
    )

    foreach ($c in $checks) {
        $found = $content -match $c.Pattern
        if ($found) {
            Write-Pass $c.Label
        } else {
            Write-Fail $c.Label
        }
    }
} else {
    Write-Host "  [SKIP] Cannot read stderr.log - log checks skipped." -ForegroundColor Yellow
    $allPassed = $false
}

Write-Host ""
if ($allPassed) {
    Write-Host "  All proxy-trust checks passed." -ForegroundColor Green
    Write-Host "  The frozen EXE has the proxy-trust patch." -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Next: open https://fans-barangay.local/system/connection/" -ForegroundColor Cyan
    Write-Host "  and verify:" -ForegroundColor DarkGray
    Write-Host "    wsgi.url_scheme = https" -ForegroundColor DarkGray
    Write-Host "    request.scheme  = https" -ForegroundColor DarkGray
    Write-Host "    REMOTE_ADDR     = 127.0.0.1" -ForegroundColor DarkGray
} else {
    Write-Host "  One or more checks FAILED." -ForegroundColor Red
    Write-Host "  Reinstall using the latest FANS-C-Setup.exe." -ForegroundColor Yellow
}
Write-Host ""
