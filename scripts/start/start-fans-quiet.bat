@echo off
:: ============================================================
::  FANS-C Daily Launcher  (recommended for manual daily use)
::  ----------------------------------------------------------
::  Starts the verification system with both services running
::  minimized in the background.  This window shows startup
::  status and port verification.  Close it when done.
::
::  FOR DEBUGGING (full visible windows): use start-fans.bat
::
::  HOW TO USE:
::    Double-click this file -- or use the desktop shortcut.
::    Keep the system running as long as staff need access.
::    To stop: run scripts\admin\stop-fans.ps1
::
::  FIRST-TIME SETUP:  Run scripts\setup\setup-complete.ps1
::  DESKTOP SHORTCUT:  Run scripts\setup\Create-Desktop-Shortcut.ps1 once.
::  AUTO-START:        Run scripts\setup\setup-autostart.ps1 once (recommended).
:: ============================================================

title FANS-C Verification System
mode con cols=68 lines=38

:: ----------------------------------------------------------
:: Compute project root (two levels up: scripts\start\ -> root)
:: ----------------------------------------------------------
SET PROJECT_ROOT=%~dp0..\..
cd /D "%PROJECT_ROOT%"

echo.
echo  ================================================================
echo   FANS-C  ^|  Barangay Senior Citizen Verification System
echo   Starting up...
echo  ================================================================
echo.

:: ============================================================
:: PRE-FLIGHT CHECKS
:: ============================================================

:: Check .venv
if not exist "%PROJECT_ROOT%\.venv\Scripts\waitress-serve.exe" (
    echo  [FAIL] System setup is not complete.
    echo.
    echo         waitress-serve.exe was not found.
    echo.
    echo         IT/Admin action required:
    echo           Run scripts\setup\setup-complete.ps1 before using this launcher.
    echo.
    pause
    exit /b 1
)

:: Check .env
if not exist "%PROJECT_ROOT%\.env" (
    echo  [FAIL] Configuration file (.env) is missing.
    echo.
    echo         IT/Admin action required:
    echo           Run scripts\setup\setup-complete.ps1 to complete configuration.
    echo.
    pause
    exit /b 1
)

:: Check Caddyfile
if not exist "%PROJECT_ROOT%\Caddyfile" (
    echo  [FAIL] Caddyfile not found in the project root.
    echo.
    echo         Do not move this launcher to another folder.
    echo         Use the desktop shortcut created by Create-Desktop-Shortcut.ps1.
    echo.
    pause
    exit /b 1
)

:: Check TLS certificate -- REQUIRED for HTTPS, hard fail if missing
if not exist "%PROJECT_ROOT%\fans-cert.pem" (
    echo  [FAIL] TLS certificate not found: fans-cert.pem
    echo.
    echo         HTTPS cannot start without a valid certificate.
    echo         The system will NOT be accessible at https://fans-barangay.local.
    echo.
    echo         IT/Admin action required:
    echo           Run scripts\setup\setup-complete.ps1
    echo           This will regenerate the certificate and verify the full setup.
    echo.
    pause
    exit /b 1
)

if not exist "%PROJECT_ROOT%\fans-cert-key.pem" (
    echo  [FAIL] TLS private key not found: fans-cert-key.pem
    echo.
    echo         HTTPS cannot start without the private key.
    echo.
    echo         IT/Admin action required:
    echo           Run scripts\setup\setup-complete.ps1 to regenerate the certificate.
    echo.
    pause
    exit /b 1
)

echo  [OK]  Pre-flight checks passed.
echo.

:: ============================================================
:: STOP STALE PROCESSES (prevents duplicate-instance conflicts)
:: ============================================================

echo  [..] Stopping any existing Waitress or Caddy instances...
taskkill /F /IM waitress-serve.exe /T >nul 2>&1
taskkill /F /IM caddy.exe /T >nul 2>&1
timeout /t 2 /nobreak >nul

:: ============================================================
:: START SERVICES (MINIMIZED)
:: ============================================================

echo  [1/2] Starting application server (Waitress)...
start /MIN "FANS-C Waitress" /D "%PROJECT_ROOT%" "%PROJECT_ROOT%\.venv\Scripts\waitress-serve.exe" --listen=127.0.0.1:8000 fans.wsgi:application

echo  [..] Waiting 6 seconds for Django to load...
timeout /t 6 /nobreak >nul

echo  [2/2] Starting HTTPS service (Caddy)...
IF EXIST "%PROJECT_ROOT%\Caddyfile" (
    start /MIN "FANS-C Caddy" /D "%PROJECT_ROOT%" caddy run --config "%PROJECT_ROOT%\Caddyfile"
) ELSE (
    echo  [WARN] Caddyfile not found -- HTTPS not started.
)

echo  [..] Waiting 8 seconds for HTTPS to bind...
timeout /t 8 /nobreak >nul

:: ============================================================
:: PORT VERIFICATION -- confirm services are actually listening
:: ============================================================

echo.
echo  Verifying services...
echo.

:: Check port 8000 (Waitress)
set "PORT_8000=FAIL"
netstat -an 2>nul | findstr ":8000" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 set "PORT_8000=OK"

:: Check port 443 (Caddy)
set "PORT_443=FAIL"
netstat -an 2>nul | findstr ":443" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 set "PORT_443=OK"

if "%PORT_8000%"=="OK" (
    echo  [OK]   Port 8000  ^(Waitress app server^)  -- LISTENING
) else (
    echo  [FAIL] Port 8000  ^(Waitress app server^)  -- NOT RESPONDING
)

if "%PORT_443%"=="OK" (
    echo  [OK]   Port 443   ^(Caddy HTTPS^)          -- LISTENING
) else (
    echo  [FAIL] Port 443   ^(Caddy HTTPS^)          -- NOT RESPONDING
)

echo.

:: ============================================================
:: RESULT + DIAGNOSTIC MESSAGES
:: ============================================================

if "%PORT_8000%"=="FAIL" (
    echo  ================================================================
    echo   WARNING: Application server did not start correctly.
    echo.
    echo   The system may not be accessible from any browser.
    echo.
    echo   Probable causes:
    echo     - .env missing EMBEDDING_ENCRYPTION_KEY
    echo     - Django startup error ^(import error, missing migration^)
    echo.
    echo   For details: close this window and run start-fans.bat instead.
    echo   That shows full error output in a visible window.
    echo  ================================================================
    echo.
    pause
    exit /b 1
)

if "%PORT_443%"=="FAIL" (
    echo  ================================================================
    echo   WARNING: HTTPS service did not start correctly.
    echo.
    echo   The system may NOT be accessible at https://fans-barangay.local.
    echo   The application server ^(port 8000^) is running.
    echo.
    echo   Probable causes:
    echo     - Caddyfile configuration error ^(TLS cert path wrong?^)
    echo     - Port 443 already in use by another process
    echo     - Windows Firewall blocking port 443
    echo.
    echo   For details: close this window and run start-fans.bat instead.
    echo   The Caddy window will show the exact error message.
    echo  ================================================================
    echo.
    pause
    exit /b 1
)

:: ============================================================
:: DETECT LAN IP FOR STATUS DISPLAY
:: ============================================================

set "LAN_IP="
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /i "IPv4 Address"') do (
    if not defined LAN_IP (
        for /f "tokens=*" %%B in ("%%A") do set "LAN_IP=%%B"
    )
)

:: ============================================================
:: SUCCESS STATUS DISPLAY
:: ============================================================

echo  ================================================================
echo   FANS-C is running.  Both services verified.
echo.
echo   OPEN THIS ADDRESS IN THE BROWSER:
echo     https://fans-barangay.local
echo     (Camera enabled -- full face verification)
echo.
echo   BACKUP ADDRESS (if browser shows a security warning):
if defined LAN_IP (
echo     http://%LAN_IP%:8000
echo     Camera disabled -- for troubleshooting only
) else (
echo     Not available -- connect this PC to the network first.
)
echo.
echo   Both services are running minimized in the taskbar.
echo   Do NOT close those taskbar windows while staff use the system.
echo.
echo   TO SHUT DOWN:
echo     Run: scripts\admin\stop-fans.ps1
echo     Or close "FANS-C Waitress" and "FANS-C Caddy" in the taskbar.
echo.
echo   HEALTH CHECK (IT/Admin):
echo     Run: scripts\admin\check-system-health.ps1
echo  ================================================================
echo.
echo  You may close this window. The system will keep running.
