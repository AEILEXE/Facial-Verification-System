#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C -- Re-find the server after it moved to a different network.

.DESCRIPTION
    If https://fans-barangay.local stops loading after the FANS-C server PC
    was shut down and moved to a different Wi-Fi/network, it is almost
    always because this PC's hosts file still points at the SERVER'S OLD
    IP address (the server got a new address from the new network's
    router, but the entry in this PC's hosts file was never updated).

    This script:
      1. Finds this PC's own local network (from its active network adapter).
      2. Looks for a live host on that same local network answering FANS-C's
         existing health-check endpoint (GET /health/network/, already used
         by the installer -- no new server feature, no new open port).
      3. If exactly one is found, updates ONLY the fans-barangay.local line
         in this PC's hosts file to the newly found IP.

    This does NOT:
      - Search outside this PC's own local network (no Internet, no other
        subnets) -- it only looks at addresses on the same Wi-Fi/LAN this
        PC is currently connected to.
      - Change any server configuration.
      - Weaken or bypass the HTTPS certificate check, for the real site or
        for the identification probe. The probe validates each candidate's
        certificate against rootCA.pem (shipped in this same folder -- the
        same file trust-local-cert.bat already imported into this PC's
        trust store) and pins the expected hostname, so a candidate must
        present a certificate actually issued by the FANS-C server's own
        certificate authority to be accepted -- an unrelated device on the
        network cannot pass the check just by answering on port 443.

    LIMITATION: this PC must be run again (or scheduled) each time the
    server moves to a new network -- it does not run automatically in the
    background, and it cannot fix OTHER client PCs (each one needs to run
    it, or run CLIENT-SETUP\trust-local-cert.bat again with the new IP).

.EXAMPLE
    .\refresh-server-connection.ps1
#>

$ErrorActionPreference = 'Continue'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host ''
    Write-Host '  [FAIL] Must be run as Administrator.' -ForegroundColor Red
    Write-Host '         Right-click -> Run with PowerShell, approve UAC.' -ForegroundColor Yellow
    Write-Host '         (Or double-click refresh-server-connection.bat instead.)' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   FANS-C  |  Re-find Server on This Network' -ForegroundColor Cyan
Write-Host '   Use this when the server PC moved to a different Wi-Fi/network.' -ForegroundColor DarkGray
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''

$hostname  = 'fans-barangay.local'
$hostsFile = 'C:\Windows\System32\drivers\etc\hosts'

# -- Step 1: find this PC's own local network ---------------------------------
Write-Host '  [1/3] Finding this PC''s local network...' -ForegroundColor Cyan

$localAddr = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
        $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and
        $_.PrefixOrigin -ne 'WellKnown'
    } |
    Sort-Object -Property InterfaceMetric |
    Select-Object -First 1

if (-not $localAddr) {
    Write-Host '  [FAIL] Could not detect an active local network connection.' -ForegroundColor Red
    Write-Host '         Connect to the same Wi-Fi/network as the FANS-C server, then retry.' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

$myIp = $localAddr.IPAddress
$prefixLen = $localAddr.PrefixLength
Write-Host "         This PC: $myIp /$prefixLen" -ForegroundColor DarkGray

if ($prefixLen -lt 22) {
    # A very large subnet (e.g. a /16) would mean scanning tens of thousands
    # of addresses -- not appropriate for a quick client-side check. Bail
    # out with a clear message rather than hanging for a long time.
    Write-Host "  [FAIL] This network is too large to scan automatically (/$prefixLen)." -ForegroundColor Red
    Write-Host '         Ask your Technical Administrator for the server''s current LAN IP' -ForegroundColor Yellow
    Write-Host '         and run CLIENT-SETUP\trust-local-cert.bat with it instead.' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

# Compute every host address on this PC's subnet (excluding network/broadcast).
$ipBytes   = [System.Net.IPAddress]::Parse($myIp).GetAddressBytes()
$ipInt     = [BitConverter]::ToUInt32(($ipBytes[3..0]), 0)
$maskInt   = if ($prefixLen -eq 0) { 0 } else { [uint32]([uint32]::MaxValue -shl (32 - $prefixLen)) }
$networkInt = $ipInt -band $maskInt
$hostCount  = [Math]::Pow(2, 32 - $prefixLen) - 2

function ConvertTo-IPString([uint32]$intVal) {
    $b = [BitConverter]::GetBytes($intVal)
    [array]::Reverse($b)
    return ([System.Net.IPAddress]$b).ToString()
}

$candidates = for ($i = 1; $i -le $hostCount; $i++) {
    ConvertTo-IPString ($networkInt + [uint32]$i)
}

# -- Step 2: TCP-probe the subnet, then ask each live host if it's FANS-C ----
Write-Host ''
Write-Host "  [2/3] Scanning $($candidates.Count) addresses on this network..." -ForegroundColor Cyan
Write-Host '        (This takes a few seconds. No changes are made yet.)' -ForegroundColor DarkGray

# Probe TCP/443 directly instead of ICMP ping. Windows Firewall blocks ICMP
# Echo Requests by default on many machines ("File and Printer Sharing
# (Echo Request)" is off unless explicitly enabled), which would otherwise
# make a fully-reachable FANS-C server (port 443 is opened by
# setup-secure-server.ps1) invisible to a ping-based sweep and cause this
# script to wrongly report "no server found".
$connectTasks = @{}
foreach ($ip in $candidates) {
    try {
        $tcpClient = New-Object System.Net.Sockets.TcpClient
        $connectTasks[$ip] = @{ Client = $tcpClient; Task = $tcpClient.ConnectAsync($ip, 443) }
    } catch { }
}
# WaitAll throws an AggregateException if ANY task faults (e.g. a normal
# "connection refused" from a live host with port 443 closed) even though
# a timeout was given -- that is expected/routine here, not a script error,
# so it is deliberately swallowed. Per-candidate results are read from
# each TcpClient's .Connected state below regardless of fault status.
try {
    [System.Threading.Tasks.Task]::WaitAll(@($connectTasks.Values | ForEach-Object { $_.Task }), 8000) | Out-Null
} catch { }

$liveHosts = $connectTasks.Keys | Where-Object {
    $entry = $connectTasks[$_]
    try { $isLive = $entry.Client.Connected } catch { $isLive = $false }
    $entry.Client.Close()
    $isLive
}

Write-Host "        $($liveHosts.Count) host(s) responded; checking which one is FANS-C..." -ForegroundColor DarkGray

# Always use the Windows in-box curl.exe (guaranteed on Windows 10 1803+ /
# Windows 11) rather than whatever "curl" resolves to on PATH -- some
# machines have a Git-for-Windows curl.exe earlier on PATH that uses its
# own bundled CA list instead of the Windows trust store, which would make
# --cacert below unreliable.
$curlExe = Join-Path $env:SystemRoot 'System32\curl.exe'
if (-not (Test-Path $curlExe)) {
    $curlExe = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source
}
if (-not $curlExe) { $curlExe = 'curl' }

$rootCaPath = Join-Path $PSScriptRoot 'rootCA.pem'
if (-not (Test-Path $rootCaPath)) {
    Write-Host '  [FAIL] rootCA.pem is missing from this folder.' -ForegroundColor Red
    Write-Host '         Discovery cannot safely verify which host is the real FANS-C' -ForegroundColor Yellow
    Write-Host '         server without it. Copy the full CLIENT-SETUP folder from the' -ForegroundColor Yellow
    Write-Host '         server again (see README.txt) and retry.' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

$found = @()
foreach ($ip in $liveHosts) {
    try {
        # --resolve pins the connection to this candidate IP while still
        # sending/validating the real hostname over TLS (SNI + certificate
        # hostname check), and --cacert restricts trust to the FANS-C
        # server's own certificate authority. A candidate must present a
        # certificate actually issued by that CA for fans-barangay.local to
        # be accepted here -- an unrelated device answering on port 443
        # cannot pass this check.
        # --ssl-no-revoke: mkcert-issued certificates have no CRL/OCSP
        # revocation endpoint, so curl's Windows (schannel) backend would
        # otherwise hard-fail every candidate -- including the real server
        # -- with "the revocation status is unknown".
        $body = & $curlExe -s --max-time 2 --ssl-no-revoke --cacert $rootCaPath `
            --resolve "${hostname}:443:${ip}" "https://${hostname}/health/network/" 2>$null
        if ($body -match '"service"\s*:\s*"fans-c"') {
            $found += $ip
        }
    } catch { }
}

# -- Step 3: update the hosts file --------------------------------------------
Write-Host ''
Write-Host '  [3/3] Updating hosts file...' -ForegroundColor Cyan

if ($found.Count -eq 0) {
    Write-Host '  [FAIL] No FANS-C server found on this network.' -ForegroundColor Red
    Write-Host '         Make sure:' -ForegroundColor Yellow
    Write-Host '           - The server PC is powered on and connected to THIS SAME network' -ForegroundColor Yellow
    Write-Host '           - This PC is on the same Wi-Fi/network as the server' -ForegroundColor Yellow
    Write-Host '           - FANS-C is running on the server (Caddy + the FANS-C app)' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

if ($found.Count -gt 1) {
    Write-Host "  [FAIL] Found $($found.Count) machines answering as FANS-C:" -ForegroundColor Red
    $found | ForEach-Object { Write-Host "           $_" -ForegroundColor Yellow }
    Write-Host '         This should not normally happen. Contact your Technical Administrator' -ForegroundColor Yellow
    Write-Host '         rather than guessing which one is correct.' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

$serverIp = $found[0]
Write-Host "        Found FANS-C server at: $serverIp" -ForegroundColor Green

$hostsContent = Get-Content $hostsFile -Raw -ErrorAction SilentlyContinue
$existingLinePattern = "(?im)^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+$([regex]::Escape($hostname))\s*(#.*)?$"
$existingMatch = [regex]::Match($hostsContent, $existingLinePattern)

if ($existingMatch.Success -and $existingMatch.Groups[1].Value -eq $serverIp) {
    Write-Host "  [OK] Hosts file already points $hostname -> $serverIp. Nothing to change." -ForegroundColor Green
} else {
    try {
        if ($existingMatch.Success) {
            $newContent = $hostsContent.Remove($existingMatch.Index, $existingMatch.Length).Insert(
                $existingMatch.Index, "$serverIp  $hostname"
            )
            Set-Content -Path $hostsFile -Value $newContent -Encoding ASCII -NoNewline
            Write-Host "  [OK] Updated hosts entry: $hostname -> $serverIp" -ForegroundColor Green
        } else {
            Add-Content -Path $hostsFile -Value "`n$serverIp  $hostname`n" -Encoding ASCII
            Write-Host "  [OK] Added hosts entry: $hostname -> $serverIp" -ForegroundColor Green
        }
    } catch {
        Write-Host "  [FAIL] Could not write to hosts file: $_" -ForegroundColor Red
        Write-Host '         Add this line manually to:' -ForegroundColor Yellow
        Write-Host "           $hostsFile" -ForegroundColor Yellow
        Write-Host "           $serverIp  $hostname" -ForegroundColor Cyan
        Write-Host ''
        Read-Host '  Press Enter to exit'
        exit 1
    }
}

Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   Done. Open this address in the browser:' -ForegroundColor Green
Write-Host '     https://fans-barangay.local' -ForegroundColor Cyan
Write-Host ''
Write-Host '   If the browser still shows a certificate warning, re-run' -ForegroundColor DarkGray
Write-Host '   CLIENT-SETUP\trust-local-cert.bat once on this PC.' -ForegroundColor DarkGray
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''
Read-Host '  Press Enter to close'
