#Requires -Version 5.1
<#
.SYNOPSIS
    Build the FANS-C Windows desktop application (.exe + dist folder).

.DESCRIPTION
    This script automates the full PyInstaller packaging process:

      1. Verifies Python 3.11 and the project .venv are present.
      2. Installs packaging-only dependencies (PyInstaller, waitress).
      3. Runs `collectstatic` to ensure staticfiles/ is up to date.
      4. Runs `pyinstaller fans_c.spec` to produce dist/FANS-C/.
      5. Copies post-build assets (.env.example, SETUP.md) into dist/FANS-C/
         so the distribution folder is self-contained for a new user.
      6. Prints a summary of the build output.

    After this script succeeds, run the Inno Setup compiler on
    installer/fans_c.iss to produce the final Windows installer
    (FANS-C-Setup.exe).

.NOTES
    Prerequisites
    -------------
    * Python 3.11 must be installed and accessible via `py -3.11`.
    * The project .venv must exist (run .\setup-secure-server.ps1 first if it does not).
    * PyInstaller and waitress are installed into .venv by this script.
    * UPX (optional)  --  if present in PATH, it will compress binaries slightly.
      Download from https://upx.github.io/.  Not required.

    Why waitress?
    -------------
    waitress is a pure-Python threaded WSGI server that works reliably
    inside a PyInstaller bundle.  Django's development server (runserver)
    uses multiprocessing internally for its autoreloader, which cannot be
    safely frozen.  waitress replaces it for the packaged build only.
    Django itself remains unchanged.

    Expected output
    ---------------
    dist\fans_c\            --  the packaged application directory
    dist\fans_c\fans_c.exe  --  the launcher executable
    dist\fans_c\.env.example  --  user copies this to .env before first launch
    dist\fans_c\SETUP.md    --  first-run setup instructions for the target machine

.EXAMPLE
    .\build_exe.ps1
    .\build_exe.ps1 -SkipCollectStatic
    .\build_exe.ps1 -Clean
#>

param(
    # Skip `collectstatic` (faster rebuild when templates/static haven't changed)
    [switch]$SkipCollectStatic,

    # Delete dist/ and build/ before building (full clean build)
    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot  = Split-Path $PSScriptRoot -Parent
$venvPython   = Join-Path $projectRoot '.venv\Scripts\python.exe'
$specFile     = Join-Path $PSScriptRoot 'fans_c.spec'
# PyInstaller outputs to dist\fans_c\ because fans_c.spec uses name='fans_c' in COLLECT.
# dev\installer\fans_c.iss reads from ..\dist\fans_c\ (relative to dev\installer\).
$distDir      = Join-Path $projectRoot 'dist\fans_c'

Set-Location $projectRoot

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host "   FANS-C  |  Windows Build Script  |  PyInstaller packaging      " -ForegroundColor Cyan
Write-Host "  ================================================================" -ForegroundColor DarkCyan
Write-Host ""

# ---------------------------------------------------------------------------
# Step 0  --  Prerequisites
# ---------------------------------------------------------------------------
Write-Host "  [1/6] Checking prerequisites ..." -ForegroundColor DarkGray

# Require .venv
if (-not (Test-Path $venvPython)) {
    Write-Host "  [FAIL] .venv not found at $venvPython" -ForegroundColor Red
    Write-Host "         Run .\setup-secure-server.ps1 first to create the virtual environment." -ForegroundColor Yellow
    exit 1
}

# Require fans_c.spec
if (-not (Test-Path $specFile)) {
    Write-Host "  [FAIL] fans_c.spec not found at $specFile" -ForegroundColor Red
    exit 1
}

# Verify Python version is 3.11.x
$pyVersion = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1
if ($pyVersion -ne '3.11') {
    Write-Host "  [FAIL] .venv Python is $pyVersion  --  FANS-C requires Python 3.11." -ForegroundColor Red
    Write-Host "         TensorFlow 2.13 does not support Python 3.12 or later." -ForegroundColor Yellow
    exit 1
}

Write-Host "  [OK]  Python $pyVersion in .venv" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 1  --  Optional clean
# ---------------------------------------------------------------------------
if ($Clean) {
    Write-Host "  [..] Cleaning previous build artefacts ..." -ForegroundColor DarkGray
    $toRemove = @('dist', 'build')
    foreach ($d in $toRemove) {
        $path = Join-Path $projectRoot $d
        if (Test-Path $path) {
            Remove-Item -Recurse -Force $path
            Write-Host "       Removed $path" -ForegroundColor DarkGray
        }
    }
    Write-Host "  [OK]  Clean complete." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Step 2  --  Install packaging dependencies
# ---------------------------------------------------------------------------
Write-Host "  [2/6] Installing packaging dependencies ..." -ForegroundColor DarkGray

# WHY CAPTURE-THEN-CHECK (not pipe-through-ForEach):
# $ErrorActionPreference = 'Stop' is set above.  When a native command writes
# to stderr, PowerShell wraps those lines as ErrorRecord objects in the merged
# 2>&1 stream.  Piping through ForEach-Object promotes those ErrorRecords to
# terminating errors, crashing the script even when pip exited 0.
# Capturing to a variable first avoids the pipeline entirely, so pip warnings
# never trigger Stop.  The same pattern is used for collectstatic above.
#
# WHY python -m pip (not $venvPip / bare pip):
# Invoking pip.exe directly can resolve to a different Python if PATH is dirty.
# python -m pip always uses the interpreter that launched it, which is $venvPython.

# PyInstaller  --  produces the .exe and dist/ folder.
#
# --disable-pip-version-check is REQUIRED here, not just cosmetic. Without it,
# newer pip releases print a "[notice] A new release of pip is available"
# message to stderr, which PowerShell 5.1 (with $ErrorActionPreference='Stop'
# and Set-StrictMode) wraps as a NativeCommandError and kills the script
# before we even get to inspect $LASTEXITCODE. Suppressing the version notice
# keeps pip silent on stderr unless something is actually wrong.
$pipOutput = & $venvPython -m pip install --disable-pip-version-check --upgrade pyinstaller pyinstaller-hooks-contrib 2>&1
$pipExit = $LASTEXITCODE

if ($pipExit -ne 0) {
    Write-Host "  [FAIL] Failed to install PyInstaller." -ForegroundColor Red
    $pipOutput | ForEach-Object { Write-Host "       $_" -ForegroundColor DarkGray }
    exit 1
}

# Verify PyInstaller is actually runnable before proceeding
$pyiOutput = & $venvPython -m PyInstaller --version 2>&1
$pyiExit = $LASTEXITCODE

if ($pyiExit -ne 0) {
    Write-Host "  [FAIL] PyInstaller installed but cannot run." -ForegroundColor Red
    $pyiOutput | ForEach-Object { Write-Host "       $_" -ForegroundColor DarkGray }
    exit 1
}

Write-Host "  [OK]  PyInstaller $pyiOutput ready." -ForegroundColor Green

# waitress  --  the WSGI server used by launcher.py in packaged mode.
# Pure Python, no compiled extensions -> reliable inside PyInstaller.
$pip2Output = & $venvPython -m pip install --disable-pip-version-check --upgrade "waitress>=3.0,<4.0" 2>&1
$pip2Exit = $LASTEXITCODE

if ($pip2Exit -ne 0) {
    Write-Host "  [FAIL] Failed to install waitress." -ForegroundColor Red
    $pip2Output | ForEach-Object { Write-Host "       $_" -ForegroundColor DarkGray }
    exit 1
}

Write-Host "  [OK]  waitress ready." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 3  --  collectstatic
# ---------------------------------------------------------------------------
# whitenoise (DEBUG=False) serves from staticfiles/ using hashed filenames.
# The bundle must include the collected files, not just the source static/.
# If staticfiles/ is empty or stale, CSS/JS will 404 in the packaged app.
#
# WHY THE SPECIAL stderr HANDLING BELOW:
# When Python writes to stderr (e.g. Django's RuntimeWarning about SECRET_KEY
# being the placeholder value), PowerShell wraps each stderr line in an
# ErrorRecord object.  Under $ErrorActionPreference = 'Stop' those objects are
# promoted to terminating errors inside ForEach-Object, crashing the script
# even when collectstatic itself succeeded (exit code 0).
#
# Fix: lower ErrorActionPreference to 'Continue' only for this call, then
# type-check every pipeline object.  ErrorRecord items are Django warnings --
# display them in yellow.  Plain strings are normal Django output -- display
# them in gray.  Real failures are still caught via $LASTEXITCODE after the
# pipeline completes.  ErrorActionPreference is always restored afterwards.

if (-not $SkipCollectStatic) {
    Write-Host "  [3/6] Running collectstatic ..." -ForegroundColor DarkGray

    # Save and lower ErrorActionPreference so stderr lines from Python do not
    # become terminating errors in the pipeline.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'

    # Collect warning lines so we can report a count in the summary.
    $csWarnings = [System.Collections.Generic.List[string]]::new()

    & $venvPython manage.py collectstatic --noinput --clear 2>&1 |
        ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                # Stderr from Python -- typically Django startup RuntimeWarnings
                # (e.g. SECRET_KEY placeholder, missing .env).  Not a crash.
                $csWarnings.Add($_.ToString())
                Write-Host "       [warn] $_" -ForegroundColor Yellow
            } else {
                # Normal stdout from collectstatic (file counts, paths copied)
                Write-Host "       $_" -ForegroundColor DarkGray
            }
        }

    # Capture exit code before restoring ErrorActionPreference.
    $csExit = $LASTEXITCODE

    # Always restore -- even if something throws unexpectedly above.
    $ErrorActionPreference = $prevEAP

    if ($csExit -ne 0) {
        # Non-zero exit = real failure (missing app, broken template, bad
        # INSTALLED_APPS, etc.).  Stop the build so the user sees it clearly.
        Write-Host "  [FAIL] collectstatic failed (exit code $csExit)." -ForegroundColor Red
        Write-Host "         Check the output above for errors, then re-run build_exe.ps1." -ForegroundColor Yellow
        exit 1
    }

    if ($csWarnings.Count -gt 0) {
        Write-Host "  [OK]  staticfiles/ up to date ($($csWarnings.Count) warning(s) shown above)." -ForegroundColor Green
        Write-Host "        Warnings during collectstatic are usually non-fatal." -ForegroundColor DarkGray
        Write-Host "        The SECRET_KEY warning is expected when .env uses the placeholder value." -ForegroundColor DarkGray
    } else {
        Write-Host "  [OK]  staticfiles/ up to date." -ForegroundColor Green
    }
} else {
    Write-Host "  [3/6] Skipping collectstatic (-SkipCollectStatic)." -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# Step 4  --  PyInstaller
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  [4/6] Running PyInstaller (this will take several minutes) ..." -ForegroundColor DarkGray
Write-Host "        TensorFlow has thousands of files  --  the first build is slow." -ForegroundColor DarkGray
Write-Host "        Subsequent builds use the cache and are much faster." -ForegroundColor DarkGray
Write-Host ""

# Run PyInstaller using the .venv Python so it picks up the .venv packages.
#
# PyInstaller streams its progress lines to stderr (INFO, WARNING). Under
# `$ErrorActionPreference = 'Stop'` + `Set-StrictMode`, the merged 2>&1 stream
# promotes those informational ErrorRecords to terminating errors and kills
# the script even when PyInstaller returns exit code 0. Temporarily lower
# the preference around the call so PyInstaller's stderr is treated as data,
# then check $LASTEXITCODE for the real outcome.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $venvPython -m PyInstaller $specFile --noconfirm 2>&1 |
    ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) {
            Write-Host "       $_" -ForegroundColor DarkGray
        } else {
            Write-Host "       $_" -ForegroundColor DarkGray
        }
    }
$pyiBuildExit = $LASTEXITCODE
$ErrorActionPreference = $prevEAP

if ($pyiBuildExit -ne 0) {
    Write-Host ""
    Write-Host "  [FAIL] PyInstaller build failed (exit code $pyiBuildExit)." -ForegroundColor Red
    Write-Host "         Common causes:" -ForegroundColor Yellow
    Write-Host "           * ImportError for a package  --  add it to hiddenimports in fans_c.spec" -ForegroundColor Yellow
    Write-Host "           * Missing data file  --  add it to the datas list in fans_c.spec" -ForegroundColor Yellow
    Write-Host "           * TensorFlow DLL load error  --  ensure Python 3.11 is in use" -ForegroundColor Yellow
    Write-Host "           * Long path issue  --  move project to D:\FANS or shorter path" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "  [OK]  PyInstaller build succeeded." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 5  --  Post-build: copy distribution assets
# ---------------------------------------------------------------------------
Write-Host "  [5/6] Copying distribution assets into dist\FANS-C\ ..." -ForegroundColor DarkGray

# .env.example  --  user creates .env from this on the target machine
$envExample = Join-Path $projectRoot '.env.example'
if (Test-Path $envExample) {
    Copy-Item $envExample -Destination $distDir -Force
    Write-Host "       Copied .env.example" -ForegroundColor DarkGray
}

# SETUP.md  --  first-run instructions for the person who installs the app
$setupMd = Join-Path $projectRoot 'SETUP.md'
if (Test-Path $setupMd) {
    Copy-Item $setupMd -Destination $distDir -Force
    Write-Host "       Copied SETUP.md" -ForegroundColor DarkGray
}

# Icon  --  included for the installer script if present
$ico = Join-Path $projectRoot 'fans_c.ico'
if (Test-Path $ico) {
    Copy-Item $ico -Destination $distDir -Force
    Write-Host "       Copied fans_c.ico" -ForegroundColor DarkGray
}

Write-Host "  [OK]  Assets copied." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 5a  --  Pre-build source directory check
# ---------------------------------------------------------------------------
# Warn if runtime log files exist inside the Django app source directories.
# PyInstaller bundles the entire logs/ app dir; *.log files are excluded from
# staging by robocopy (/XF *.log) and from the installer by Inno Excludes,
# so they never reach the payload.  This check makes the situation visible
# at build time so developers know to clean them up.
# ---------------------------------------------------------------------------
Write-Host "  [5a] Checking source directories for runtime artifacts ..." -ForegroundColor DarkGray

$sourceLogWarnings = [System.Collections.Generic.List[string]]::new()
$dirsToCheck = @('logs', 'accounts', 'beneficiaries', 'verification', 'fans')
foreach ($d in $dirsToCheck) {
    $srcPath = Join-Path $projectRoot $d
    if (-not (Test-Path $srcPath -PathType Container)) { continue }
    $found = Get-ChildItem -Path $srcPath -Filter '*.log' -Recurse -File -ErrorAction SilentlyContinue
    foreach ($f in $found) {
        $sourceLogWarnings.Add($f.FullName)
    }
}
if ($sourceLogWarnings.Count -gt 0) {
    Write-Host ""
    Write-Host "  [WARN] Runtime log files found in source app directories:" -ForegroundColor Yellow
    foreach ($w in $sourceLogWarnings) {
        Write-Host "    ~ $w" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "         These files are excluded from the installer payload by" -ForegroundColor DarkGray
    Write-Host "         robocopy /XF *.log and Inno Excludes=*.log, so the" -ForegroundColor DarkGray
    Write-Host "         installer is NOT affected.  To suppress this warning," -ForegroundColor DarkGray
    Write-Host "         delete the *.log files from the source directories." -ForegroundColor DarkGray
    Write-Host ""
} else {
    Write-Host "  [OK]  No runtime log files found in source app directories." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Step 5b  --  Create clean installer staging folder
# ---------------------------------------------------------------------------
# PURPOSE: Inno Setup must NEVER package runtime/private files that the app
# writes into dist\fans_c\ when it is run on the build machine for testing.
# These include:  .env, db.sqlite3, fans-cert.pem, fans-cert-key.pem,
#                 rootCA.pem, and the logs\ directory.
#
# We copy dist\fans_c\ into a clean staging folder using robocopy with
# explicit file exclusions, then manually remove any runtime directories
# that robocopy cannot exclude by position (only by name).
# Inno Setup is pointed at the staging folder, not the dirty dist\ folder.
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  [5b] Creating clean installer staging folder ..." -ForegroundColor DarkGray

$stagingBase = Join-Path $projectRoot 'build\installer-staging'
$stagingDir  = Join-Path $stagingBase 'fans_c'

if (Test-Path $stagingDir) {
    Remove-Item -Recurse -Force $stagingDir
    Write-Host "       Removed previous staging folder." -ForegroundColor DarkGray
}
New-Item -ItemType Directory -Force $stagingDir | Out-Null

# robocopy copies every file/subdir from dist\fans_c\ into staging,
# but excludes runtime/private files by exact name.
# /E        = copy all subdirectories including empty
# /XF       = exclude files matching these names/patterns
# /NP /NFL /NDL = suppress noisy progress/file/dir output lines
#
# NOTE: *.sqlite3 uses a wildcard; robocopy handles it without shell glob expansion
# when passed via array splatting (@rcArgs).
# NOTE: certifi/cacert.pem and grpc/roots.pem are LEGITIMATE library files and
# are NOT listed here -- only runtime-generated cert files are excluded.
# NOTE: *.log files are runtime-generated (Django startup/watchdog logs written
# into the logs/ app directory). They must never be shipped. The logs/ Django
# app itself is legitimate and is included; only *.log files are excluded.
$rcArgs = @(
    $distDir, $stagingDir, '/E',
    '/XF', '.env', 'db.sqlite3', '*.sqlite3',
            'fans-cert.pem', 'fans-cert-key.pem',
            'rootCA.pem', 'rootCA-key.pem',
            '*.log',
    '/NP', '/NFL', '/NDL'
)
& robocopy @rcArgs
$rcExit = $LASTEXITCODE
# robocopy exit codes 0-7 are success bit-flags (0=nothing done, 1=files copied,
# 2=extra files in dest, 4=mismatched, 8+=real errors).
if ($rcExit -ge 8) {
    Write-Host "  [FAIL] robocopy staging failed (exit code $rcExit)." -ForegroundColor Red
    exit 1
}

# Remove runtime directories from the staging ROOT only.
# Using robocopy /XD would also strip _internal\logs\ (the Django app), so we
# instead delete the runtime folders from staging after the copy.
foreach ($d in @('logs', 'media', 'certs', 'certificates')) {
    $p = Join-Path $stagingDir $d
    if (Test-Path $p -PathType Container) {
        Remove-Item -Recurse -Force $p
        Write-Host "       Removed runtime dir from staging: $d\" -ForegroundColor DarkGray
    }
}

Write-Host "  [OK]  Staging folder ready: $stagingDir" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 5c  --  Payload safety scan
# ---------------------------------------------------------------------------
# Fail the build immediately if any sensitive file is present in the staging
# folder.  This is the last line of defense before Inno Setup runs.
# If this scan FAILS the installer must NOT be built or released.
# ---------------------------------------------------------------------------
Write-Host "  [5c] Running payload safety scan ..." -ForegroundColor DarkGray

$violations = [System.Collections.Generic.List[string]]::new()

# Check for private/runtime files by exact name or pattern.
# *.log: Django writes fans-startup.log and fans-watchdog.log into the logs/
# app directory at runtime; they must never reach the installer payload.
$sensitiveFilePatterns = @(
    '.env', 'db.sqlite3', '*.sqlite3',
    'fans-cert.pem', 'fans-cert-key.pem',
    'rootCA.pem', 'rootCA-key.pem',
    '*-key.pem',
    '*.log'
)
foreach ($pattern in $sensitiveFilePatterns) {
    $found = Get-ChildItem -Path $stagingDir -Filter $pattern -Recurse -File -ErrorAction SilentlyContinue
    foreach ($f in $found) {
        $violations.Add($f.FullName)
    }
}

# Check for runtime directories at the staging ROOT level only
foreach ($d in @('logs', 'media', 'certs', 'certificates')) {
    $p = Join-Path $stagingDir $d
    if (Test-Path $p -PathType Container) {
        $violations.Add("$p  [DIRECTORY]")
    }
}

if ($violations.Count -gt 0) {
    Write-Host ""
    Write-Host "  ================================================================" -ForegroundColor Red
    Write-Host "   PAYLOAD SAFETY SCAN FAILED -- DO NOT BUILD THE INSTALLER       " -ForegroundColor Red
    Write-Host "  ================================================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Sensitive files found in installer payload:" -ForegroundColor Red
    foreach ($v in $violations) {
        Write-Host "    ! $v" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "  Root cause: fans_c.exe was run from dist\fans_c\ before the     " -ForegroundColor Yellow
    Write-Host "  installer was compiled, writing runtime data into the bundle.    " -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Fix:" -ForegroundColor Yellow
    Write-Host "    1. Stop fans_c.exe and caddy.exe (wmic or Task Manager)." -ForegroundColor Yellow
    Write-Host "    2. Delete dist\ and build\." -ForegroundColor Yellow
    Write-Host "    3. Run: .\dev\build_exe.ps1 -Clean" -ForegroundColor Yellow
    Write-Host "    4. Do NOT run dist\fans_c\fans_c.exe before compiling Inno." -ForegroundColor Yellow
    Write-Host "    5. Compile Inno Setup directly after this script succeeds." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

Write-Host "  [OK]  SAFE: no runtime/private files found in installer payload." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 5d  --  Required-template presence check
# ---------------------------------------------------------------------------
# Fail the build if the logs templates that were previously stripped by the
# broad logs\* Inno Excludes are missing from the staging payload.
# ---------------------------------------------------------------------------
Write-Host "  [5d] Verifying required templates in staging payload ..." -ForegroundColor DarkGray

$requiredTemplates = @(
    '_internal\templates\logs\audit_logs.html',
    '_internal\templates\logs\verification_logs.html'
)
$missingTemplates = [System.Collections.Generic.List[string]]::new()
foreach ($rel in $requiredTemplates) {
    $p = Join-Path $stagingDir $rel
    if (-not (Test-Path $p -PathType Leaf)) {
        $missingTemplates.Add($rel)
    }
}
if ($missingTemplates.Count -gt 0) {
    Write-Host ""
    Write-Host "  ================================================================" -ForegroundColor Red
    Write-Host "   TEMPLATE PRESENCE CHECK FAILED -- INSTALLER WOULD BE BROKEN    " -ForegroundColor Red
    Write-Host "  ================================================================" -ForegroundColor Red
    Write-Host ""
    foreach ($t in $missingTemplates) {
        Write-Host "    MISSING: $t" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "  These templates are required for /logs/audit/ and /logs/verification/." -ForegroundColor Yellow
    Write-Host "  Check that templates\logs\ exists in the source and PyInstaller spec." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}
Write-Host "  [OK]  All required templates present in staging payload." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Step 5e  --  PowerShell script syntax check
# ---------------------------------------------------------------------------
# Validate every bundled .ps1 in scripts\admin\ with the PowerShell parser.
# A parser error here means the script will fail when IT staff run it on the
# target PC.  Catches nested-quote interpolation bugs and typos at build time.
# ---------------------------------------------------------------------------
Write-Host "  [5e] Validating bundled PowerShell scripts ..." -ForegroundColor DarkGray

$psScriptsDir   = Join-Path $projectRoot 'scripts\admin'
$psScripts      = Get-ChildItem -Path $psScriptsDir -Filter '*.ps1' -File -ErrorAction SilentlyContinue
$psParseErrors  = [System.Collections.Generic.List[string]]::new()

foreach ($psFile in $psScripts) {
    $errors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile(
        $psFile.FullName, [ref]$null, [ref]$errors
    )
    if ($errors.Count -gt 0) {
        foreach ($e in $errors) {
            $psParseErrors.Add(($psFile.Name + ':' + $e.Extent.StartLineNumber + ' -' + $e.Message))
        }
        Write-Host ("  [FAIL] " + $psFile.Name + " -" + $errors.Count + " parse error(s)") -ForegroundColor Red
    } else {
        Write-Host ("  [OK]   " + $psFile.Name + " -syntax OK") -ForegroundColor Green
    }
}

if ($psParseErrors.Count -gt 0) {
    Write-Host ""
    Write-Host "  ================================================================" -ForegroundColor Red
    Write-Host "   POWERSHELL SYNTAX CHECK FAILED -- INSTALLER WOULD SHIP BROKEN  " -ForegroundColor Red
    Write-Host "  ================================================================" -ForegroundColor Red
    Write-Host ""
    foreach ($e in $psParseErrors) {
        Write-Host "    ! $e" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "  Fix the parser errors above, then re-run build_exe.ps1." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

if ($psScripts.Count -eq 0) {
    Write-Host "  [WARN] No .ps1 files found in scripts\admin\  -nothing to check." -ForegroundColor Yellow
} else {
    Write-Host ("  [OK]  All " + $psScripts.Count + " bundled PowerShell script(s) pass syntax check.") -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Step 6  --  Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  [6/6] Build complete." -ForegroundColor Green
Write-Host ""

if (Test-Path $distDir) {
    $sizeBytes = (Get-ChildItem $distDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
    $sizeMB = [math]::Round($sizeBytes / 1MB, 1)
    $fileCount = (Get-ChildItem $distDir -Recurse -File).Count
    Write-Host "  Output folder : $distDir" -ForegroundColor Cyan
    Write-Host "  Total size    : $sizeMB MB ($fileCount files)" -ForegroundColor Cyan
} else {
    Write-Host "  WARNING: dist\fans_c\ not found after build." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  -- Next steps -------------------------------------------------" -ForegroundColor DarkCyan
Write-Host "  1. Compile the installer NOW (before running fans_c.exe):" -ForegroundColor White
Write-Host "       & 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' dev\installer\fans_c.iss" -ForegroundColor DarkGray
Write-Host "       Output: FANS-C-Installer\FANS-C-Setup.exe" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  2. Test on a CLEAN PC (not this build machine):" -ForegroundColor White
Write-Host "       Run FANS-C-Setup.exe -> Create Admin must appear on first launch" -ForegroundColor DarkGray
Write-Host "       Your dev credentials must NOT work on the clean PC" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  3. If you need to smoke-test locally:" -ForegroundColor White
Write-Host "       Run dist\fans_c\fans_c.exe AFTER step 1 only." -ForegroundColor DarkGray
Write-Host "       For the next release, run .\dev\build_exe.ps1 -Clean to start fresh." -ForegroundColor DarkGray
Write-Host ""
Write-Host "  !! WARNING: do NOT run dist\fans_c\fans_c.exe before step 1 !!" -ForegroundColor Red
Write-Host "     Running the exe writes .env, db.sqlite3, certs, and logs into" -ForegroundColor Red
Write-Host "     dist\fans_c\.  The safety scan above blocks them from reaching" -ForegroundColor Red
Write-Host "     the installer, but a clean build is always safest." -ForegroundColor Red
Write-Host ""
Write-Host "  -- Payload safety confirmed by scan above ---------------------" -ForegroundColor DarkCyan
Write-Host "  SAFE: .env, db.sqlite3, fans-cert*.pem, rootCA.pem, *.log, logs\, media\" -ForegroundColor Green
Write-Host "        are NOT present in the installer payload." -ForegroundColor Green
Write-Host ""
Write-Host "  * FaceNet weights download to <install folder>\models\keras-facenet\ on" -ForegroundColor Yellow
Write-Host "    first import (~90 MB) -- a machine-local cache shared by interactive" -ForegroundColor Yellow
Write-Host "    setup and the SYSTEM-account autostart process (not per Windows account)." -ForegroundColor Yellow
Write-Host "  * See dev\BUILD.md for the full release security checklist." -ForegroundColor Yellow
Write-Host ""
