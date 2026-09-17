@echo off
setlocal

:: ============================================================
::  FANS-C  |  Re-find Server on This Network
::  ============================================================
::  Run this if https://fans-barangay.local stops loading after
::  the server PC was shut down and moved to a different
::  Wi-Fi/network.
::
::  HOW TO USE:
::    1. Connect this PC to the SAME Wi-Fi/network as the server.
::    2. Double-click this file.
::    3. A security prompt will appear — click "Yes" to allow.
::    4. Wait a few seconds while it looks for the server.
:: ============================================================

title FANS-C - Re-find Server

echo.
echo  ================================================================
echo   FANS-C  ^|  Re-find Server on This Network
echo  ================================================================
echo.

net session >nul 2>&1
if %errorlevel% equ 0 goto :run_setup

echo  Administrator access is needed to update this PC's network settings.
echo  A security prompt will appear — click "Yes" to continue.
echo.
powershell -Command "Start-Process cmd -ArgumentList '/c cd /d \"%~dp0\" && powershell -ExecutionPolicy Bypass -NoProfile -File \"%~dp0refresh-server-connection.ps1\" && pause' -Verb RunAs" 2>nul
if %errorlevel% neq 0 (
    echo.
    echo  Could not request Administrator access automatically.
    echo  Please right-click this file and choose "Run as administrator".
    echo.
    pause
)
exit /b

:run_setup
cd /d "%~dp0"
echo.
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0refresh-server-connection.ps1"
if %errorlevel% neq 0 (
    echo.
    echo  Could not find the server. See messages above.
    echo.
    pause
    exit /b %errorlevel%
)
echo.
pause
