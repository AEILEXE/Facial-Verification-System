#Requires -Version 5.1
<#
.SYNOPSIS
    Download Bootstrap CSS/JS and Bootstrap Icons into static/vendor/.

.DESCRIPTION
    FANS-C is intended to run on barangay LANs without internet. The base
    templates therefore prefer local copies of Bootstrap. Run this script
    ONCE on a machine that does have internet (or run it after copying the
    project to the offline server, while it is still online). It writes:

        static/vendor/bootstrap-5.3.2/css/bootstrap.min.css
        static/vendor/bootstrap-5.3.2/js/bootstrap.bundle.min.js
        static/vendor/bootstrap-icons-1.11.3/font/bootstrap-icons.css
        static/vendor/bootstrap-icons-1.11.3/font/fonts/bootstrap-icons.woff
        static/vendor/bootstrap-icons-1.11.3/font/fonts/bootstrap-icons.woff2
        static/vendor/chartjs-4.4.4/chart.umd.min.js

    After running, re-run `python manage.py collectstatic --noinput` so the
    files are picked up by Whitenoise's manifest storage.
#>

param(
    [string]$BootstrapVersion = '5.3.2',
    [string]$IconsVersion     = '1.11.3',
    [string]$ChartJsVersion   = '4.4.4'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$vendorRoot  = Join-Path $projectRoot 'static\vendor'

function Get-File {
    param([string]$Url, [string]$Dest)
    $dir = Split-Path $Dest -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Write-Host "  -> $Url"
    Write-Host "     $Dest"
    Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing
}

$bs = "$vendorRoot\bootstrap-$BootstrapVersion"
Get-File "https://cdn.jsdelivr.net/npm/bootstrap@$BootstrapVersion/dist/css/bootstrap.min.css" "$bs\css\bootstrap.min.css"
Get-File "https://cdn.jsdelivr.net/npm/bootstrap@$BootstrapVersion/dist/js/bootstrap.bundle.min.js" "$bs\js\bootstrap.bundle.min.js"

$bi = "$vendorRoot\bootstrap-icons-$IconsVersion"
Get-File "https://cdn.jsdelivr.net/npm/bootstrap-icons@$IconsVersion/font/bootstrap-icons.css" "$bi\font\bootstrap-icons.css"
Get-File "https://cdn.jsdelivr.net/npm/bootstrap-icons@$IconsVersion/font/fonts/bootstrap-icons.woff"  "$bi\font\fonts\bootstrap-icons.woff"
Get-File "https://cdn.jsdelivr.net/npm/bootstrap-icons@$IconsVersion/font/fonts/bootstrap-icons.woff2" "$bi\font\fonts\bootstrap-icons.woff2"

$cjs = "$vendorRoot\chartjs-$ChartJsVersion"
Get-File "https://cdn.jsdelivr.net/npm/chart.js@$ChartJsVersion/dist/chart.umd.min.js" "$cjs\chart.umd.min.js"

Write-Host ''
Write-Host '  Vendor assets downloaded.' -ForegroundColor Green
Write-Host '  Run: .\.venv\Scripts\python.exe manage.py collectstatic --noinput' -ForegroundColor Yellow
