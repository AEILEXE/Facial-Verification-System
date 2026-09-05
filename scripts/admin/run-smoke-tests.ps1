#Requires -Version 5.1
<#
.SYNOPSIS
    Run safe, non-destructive smoke tests against the FANSC Django project.

.DESCRIPTION
    Executes a set of read-only checks to confirm the Django application is
    in a valid state before deployment.  No database writes, no migrations,
    no destructive resets.

    Tests run:
      1. pip check      -- no broken or conflicting packages
      2. manage.py check                 -- Django system check
      3. manage.py check --deploy        -- production security checks
      4. manage.py makemigrations --check --dry-run  -- no unapplied model changes
      5. manage.py collectstatic --dry-run --noinput -- static files can be collected
      6. manage.py test                  -- Django test suite (if test files exist)

    A failing test prints the error and marks that test FAIL. The script
    always continues to the next test.

.NOTES
    Run from the project root:
        .\scripts\admin\run-smoke-tests.ps1

    For a more comprehensive installation check use:
        .\scripts\admin\verify-installation.ps1

.EXAMPLE
    .\scripts\admin\run-smoke-tests.ps1
    .\scripts\admin\run-smoke-tests.ps1 -SkipTests   # skip the Django test runner
#>

param(
    # Skip the Django test runner entirely
    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $projectRoot

# Disable FaceNet/TensorFlow warmup for all management commands in this script.
# The warmup is only useful when the server is actually serving HTTP requests.
$env:FANS_SKIP_FACENET_WARMUP = '1'
$env:FANS_SKIP_MODEL_WARMUP   = '1'

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    Write-Host '  [FAIL] .venv\Scripts\python.exe not found.' -ForegroundColor Red
    Write-Host '         Run setup-complete.ps1 or: py -3.11 -m venv .venv' -ForegroundColor Yellow
    exit 1
}

$pass = 0
$fail = 0
$results = [ordered]@{}

function Run-Check {
    param(
        [string]$Label,
        [scriptblock]$Action
    )
    Write-Host "  [..] $Label ..." -ForegroundColor DarkGray

    # Lower EAP so Python stderr lines don't become terminating errors
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $output = [System.Collections.Generic.List[string]]::new()

    try {
        & $Action | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                $output.Add("[stderr] $($_.ToString())")
            } else {
                $output.Add($_.ToString())
            }
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEAP
    }

    if ($exitCode -eq 0 -or $null -eq $exitCode) {
        Write-Host "  [PASS] $Label" -ForegroundColor Green
        $script:results[$Label] = 'PASS'
        $script:pass++
    } else {
        Write-Host "  [FAIL] $Label  (exit code $exitCode)" -ForegroundColor Red
        $script:results[$Label] = "FAIL (exit $exitCode)"
        $script:fail++
        # Print the last few lines of output as context
        $tail = $output | Select-Object -Last 10
        foreach ($line in $tail) {
            Write-Host "         $line" -ForegroundColor DarkGray
        }
    }
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   FANS-C  |  Smoke Tests' -ForegroundColor Cyan
Write-Host "   Project: $projectRoot" -ForegroundColor DarkGray
Write-Host "   Date   : $(Get-Date -Format 'yyyy-MM-dd HH:mm')" -ForegroundColor DarkGray
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''

# ---------------------------------------------------------------------------
# Test 1 -- pip check
# ---------------------------------------------------------------------------
Run-Check 'pip check (no broken packages)' {
    & $venvPython -m pip check 2>&1
}

# ---------------------------------------------------------------------------
# Test 2 -- Django system check
# ---------------------------------------------------------------------------
Run-Check 'manage.py check' {
    & $venvPython manage.py check 2>&1
}

# ---------------------------------------------------------------------------
# Test 3 -- Django deploy check
# ---------------------------------------------------------------------------
# Expected warnings (silenced in settings.py):
#   W004 HSTS -- handled by Caddy
#   W008 SSL redirect -- handled by Caddy
#   W012 SESSION_COOKIE_SECURE -- dynamic in settings
#   W016 CSRF_COOKIE_SECURE -- dynamic in settings
#
# These are known and intentional. The check still passes if only silenced
# warnings remain. Any new ERROR or unsilenced WARNING should be investigated.
Run-Check 'manage.py check --deploy' {
    & $venvPython manage.py check --deploy 2>&1
}

# ---------------------------------------------------------------------------
# Test 4 -- No uncommitted model changes
# ---------------------------------------------------------------------------
Run-Check 'makemigrations --check --dry-run (no model drift)' {
    & $venvPython manage.py makemigrations --check --dry-run 2>&1
}

# ---------------------------------------------------------------------------
# Test 5 -- collectstatic dry run
# ---------------------------------------------------------------------------
Run-Check 'collectstatic --dry-run' {
    & $venvPython manage.py collectstatic --dry-run --noinput 2>&1
}

# ---------------------------------------------------------------------------
# Test 6 -- Django test suite
# ---------------------------------------------------------------------------
if ($SkipTests) {
    Write-Host '  [SKIP] Django test suite (-SkipTests)' -ForegroundColor DarkGray
    $results['Django test suite'] = 'SKIPPED'
} else {
    Write-Host '  [..] Django test suite (manage.py test) ...' -ForegroundColor DarkGray
    Write-Host '       Django test suite runs with FaceNet warmup disabled.' -ForegroundColor DarkGray

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $testOutput = [System.Collections.Generic.List[string]]::new()

    & $venvPython manage.py test --verbosity=1 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) {
            $testOutput.Add("[stderr] $($_.ToString())")
        } else {
            $testOutput.Add($_.ToString())
            Write-Host "       $_" -ForegroundColor DarkGray
        }
    }
    $testExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP

    if ($testExit -eq 0) {
        Write-Host '  [PASS] Django test suite' -ForegroundColor Green
        $results['Django test suite'] = 'PASS'
        $pass++
    } else {
        Write-Host "  [FAIL] Django test suite (exit $testExit)" -ForegroundColor Red
        $results['Django test suite'] = "FAIL (exit $testExit)"
        $fail++
    }
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   Smoke Test Summary' -ForegroundColor Cyan
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''

foreach ($key in $results.Keys) {
    $val = $results[$key]
    if ($val -like 'PASS*') {
        Write-Host "   [PASS]  $key" -ForegroundColor Green
    } elseif ($val -like 'FAIL*') {
        Write-Host "   [FAIL]  $key  -- $val" -ForegroundColor Red
    } else {
        Write-Host "   [SKIP]  $key" -ForegroundColor DarkGray
    }
}

Write-Host ''
Write-Host '  ----------------------------------------------------------------' -ForegroundColor DarkCyan
Write-Host ("   PASS: $pass   FAIL: $fail") -ForegroundColor White
Write-Host '  ----------------------------------------------------------------' -ForegroundColor DarkCyan
Write-Host ''

if ($fail -gt 0) {
    Write-Host '  RESULT: SMOKE TESTS FAILED' -ForegroundColor Red
    Write-Host '  Fix failing tests before deploying.' -ForegroundColor Yellow
    exit 1
} else {
    Write-Host '  RESULT: ALL SMOKE TESTS PASSED' -ForegroundColor Green
    exit 0
}
