@echo off
setlocal

:: ============================================================
::  FANS-C  |  Client Setup
::  ============================================================
::  Run this ONCE on each computer that needs to access FANS-C.
::
::  HOW TO USE:
::    1. Double-click this file.
::    2. A security prompt will appear — click "Yes" to allow.
::    3. Follow the short steps on screen.
::    4. Done.  Open https://fans-barangay.local in the browser.
::
::  BEFORE RUNNING:
::    Make sure rootCA.pem is in this same folder.
::    Your IT admin will provide it.
:: ============================================================

title FANS-C Client Setup

echo.
echo  ================================================================
echo   FANS-C  ^|  Client Setup
echo   Run this once — then just open the browser.
echo  ================================================================
echo.

:: Check whether we are already running as Administrator.
net session >nul 2>&1
if %errorlevel% equ 0 goto :run_setup

:: Not elevated — re-launch with UAC elevation.
echo  Administrator access is needed to complete setup.
echo  A security prompt will appear — click "Yes" to continue.
echo.
powershell -Command "Start-Process cmd -ArgumentList '/c cd /d \"%~dp0\" && powershell -ExecutionPolicy Bypass -NoProfile -File \"%~dp0trust-local-cert.ps1\" && pause' -Verb RunAs" 2>nul
if %errorlevel% neq 0 (
    echo.
    echo  Could not request Administrator access automatically.
    echo  Please right-click this file and choose "Run as administrator".
    echo.
    pause
)
exit /b

:run_setup
:: Already elevated — run the PowerShell setup script directly.
cd /d "%~dp0"
echo.
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0trust-local-cert.ps1"
if %errorlevel% neq 0 (
    echo.
    echo  Setup encountered an error.  See messages above.
    echo.
    pause
    exit /b %errorlevel%
)
echo.
pause
