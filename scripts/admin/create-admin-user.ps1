#Requires -Version 5.1
<#
.SYNOPSIS
    FANS-C -- Create or add a user account with selectable role.
    Safe to run any time: checks if admin accounts already exist first.

.DESCRIPTION
    Checks how many superuser accounts currently exist, then optionally
    creates a new account with one of these roles:

      - president  (President — operational head, approves claims)
      - admin      (Admin — manages users, beneficiaries, reports)
      - it         (IT — full system/technical access)
      - staff      (Staff — registers beneficiaries and runs verifications)

    Use this when:
      - Setting up the first admin account on a fresh install
      - Adding another privileged account
      - Creating a Staff account from PowerShell

    Does NOT require Administrator rights.
    Requires the .venv virtual environment to be set up first.

.EXAMPLE
    .\scripts\admin\create-admin-user.ps1
#>

$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $projectRoot

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'

Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host '   FANS-C  |  Create / Add User' -ForegroundColor Cyan
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''

if (-not (Test-Path $venvPython)) {
    Write-Host '  [FAIL] .venv not found.' -ForegroundColor Red
    Write-Host "         Expected: $venvPython" -ForegroundColor Yellow
    Write-Host '         Run setup-complete.ps1 first to set up the virtual environment.' -ForegroundColor Yellow
    Write-Host ''
    Read-Host '  Press Enter to exit'
    exit 1
}

function Convert-SecureStringToPlainText {
    param(
        [Parameter(Mandatory = $true)]
        [Security.SecureString] $SecureString
    )
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureString)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

# -- Check existing superusers -------------------------------------------------
Write-Host '  Checking existing admin accounts...' -ForegroundColor DarkGray

$suCheckRaw = & $venvPython manage.py shell --no-color -c `
    "from django.contrib.auth import get_user_model; U=get_user_model(); print(U.objects.filter(is_superuser=True).count())" `
    2>&1
$suCheckStr = (($suCheckRaw | Where-Object { $_ -match '^\d+$' }) -join '').Trim()

if ($suCheckStr -match '^\d+$') {
    $suCount = [int]$suCheckStr
    if ($suCount -gt 0) {
        Write-Host "  [OK]  $suCount admin account(s) already exist." -ForegroundColor Green
        Write-Host ''
        $createMore = Read-Host '  Create another user account? [Y/N]'
        if ($createMore -notmatch '^[Yy]') {
            Write-Host ''
            Write-Host '  No changes made.' -ForegroundColor DarkGray
            Write-Host '  To reset a password: log in at https://fans-barangay.local/admin' -ForegroundColor DarkGray
            Write-Host ''
            Read-Host '  Press Enter to close'
            exit 0
        }
    } else {
        Write-Host '  [INFO] No admin accounts found yet.' -ForegroundColor Yellow
    }
} else {
    Write-Host '  [WARN] Could not check existing admin count. Proceeding anyway.' -ForegroundColor Yellow
}

# -- Role selection ------------------------------------------------------------
Write-Host ''
Write-Host '  Select account role:' -ForegroundColor White
Write-Host '    1. President  (operational head, approves claims)' -ForegroundColor Cyan
Write-Host '    2. Admin      (manages users, beneficiaries, reports)' -ForegroundColor Cyan
Write-Host '    3. IT         (full system/technical access)' -ForegroundColor Cyan
Write-Host '    4. Staff      (registers beneficiaries, runs verifications)' -ForegroundColor Cyan
Write-Host ''

$roleChoice = Read-Host '  Enter choice [1/2/3/4]'

switch ($roleChoice) {
    '1' {
        $selectedRole = 'president'
        $roleLabel = 'President'
        $isSuperuser = $false
        $isStaffFlag = $false
    }
    '2' {
        $selectedRole = 'admin'
        $roleLabel = 'Admin'
        $isSuperuser = $false
        $isStaffFlag = $false
    }
    '3' {
        $selectedRole = 'it'
        $roleLabel = 'IT'
        $isSuperuser = $true
        $isStaffFlag = $true
    }
    '4' {
        $selectedRole = 'staff'
        $roleLabel = 'Staff'
        $isSuperuser = $false
        $isStaffFlag = $false
    }
    default {
        Write-Host ''
        Write-Host '  [FAIL] Invalid choice. Please run the script again.' -ForegroundColor Red
        Write-Host ''
        Read-Host '  Press Enter to close'
        exit 1
    }
}

# -- Prompt for user details ---------------------------------------------------
Write-Host ''
Write-Host "  Creating role: $roleLabel ($selectedRole)" -ForegroundColor Green
Write-Host ''

$username = Read-Host '  Username'
if ([string]::IsNullOrWhiteSpace($username)) {
    Write-Host ''
    Write-Host '  [FAIL] Username is required.' -ForegroundColor Red
    Write-Host ''
    Read-Host '  Press Enter to close'
    exit 1
}

$email = Read-Host '  Email (optional)'

$passwordSecure = Read-Host '  Password' -AsSecureString
$confirmSecure  = Read-Host '  Confirm password' -AsSecureString

$password = Convert-SecureStringToPlainText $passwordSecure
$confirm  = Convert-SecureStringToPlainText $confirmSecure

if ([string]::IsNullOrWhiteSpace($password)) {
    Write-Host ''
    Write-Host '  [FAIL] Password is required.' -ForegroundColor Red
    Write-Host ''
    Read-Host '  Press Enter to close'
    exit 1
}

if ($password -ne $confirm) {
    Write-Host ''
    Write-Host '  [FAIL] Passwords do not match.' -ForegroundColor Red
    Write-Host ''
    Read-Host '  Press Enter to close'
    exit 1
}

# -- Create user ---------------------------------------------------------------
Write-Host ''
Write-Host '  Creating account...' -ForegroundColor DarkGray

$usernamePy = $username.Replace('\', '\\').Replace("'", "\'")
$emailPy = $email.Replace('\', '\\').Replace("'", "\'")
$passwordPy = $password.Replace('\', '\\').Replace("'", "\'")

$pythonCode = @"
from django.contrib.auth import get_user_model

User = get_user_model()
username = '$usernamePy'
email = '$emailPy'
password = '$passwordPy'
selected_role = '$selectedRole'
is_superuser = $($isSuperuser.ToString())
is_staff = $($isStaffFlag.ToString())

if User.objects.filter(username=username).exists():
    print('ERROR:USERNAME_EXISTS')
else:
    user = User.objects.create_user(username=username, email=email, password=password)
    if hasattr(user, 'role'):
        user.role = selected_role
    user.is_superuser = is_superuser
    user.is_staff = is_staff
    user.save()
    print('OK:USER_CREATED')
"@

$createRaw = & $venvPython manage.py shell --no-color -c $pythonCode 2>&1
$createOutput = ($createRaw | Out-String).Trim()

Write-Host ''
if ($createOutput -match 'OK:USER_CREATED') {
    Write-Host "  [OK]  $roleLabel account created successfully." -ForegroundColor Green
    Write-Host "        Username: $username" -ForegroundColor Cyan
    if ($selectedRole -in @('it', 'admin', 'president')) {
        Write-Host '        Admin panel: https://fans-barangay.local/admin' -ForegroundColor Cyan
    } else {
        Write-Host '        App login : https://fans-barangay.local/accounts/login/' -ForegroundColor Cyan
    }
}
elseif ($createOutput -match 'ERROR:USERNAME_EXISTS') {
    Write-Host "  [FAIL] Username '$username' already exists." -ForegroundColor Red
    Write-Host '         Choose a different username and try again.' -ForegroundColor Yellow
}
else {
    Write-Host '  [WARN] Account creation may have failed. Details:' -ForegroundColor Yellow
    Write-Host $createOutput -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '  ================================================================' -ForegroundColor DarkCyan
Write-Host ''
Read-Host '  Press Enter to close'