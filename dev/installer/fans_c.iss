; fans_c.iss  --  Inno Setup 6 Installer Script for FANS-C
; =========================================================
;
; WHAT THIS SCRIPT PRODUCES
; -------------------------
; A single self-contained Windows installer:
;   D:\FANS\FANS-C-Installer\FANS-C-Setup.exe
;
; The installer:
;   1. Asks where to install (default C:\FANSC -- short path avoids TF failures)
;   2. Copies the PyInstaller bundle (dist\fans_c\) to the chosen location
;   3. Creates a desktop shortcut and Start Menu group
;   4. Offers to launch fans_c.exe after install
;
; On first launch fans_c.exe detects the missing .env and runs the full
; 8-step setup wizard automatically (no manual PowerShell required).
;
; PREREQUISITES (build machine only)
; -----------------------------------
;   1. Run: pyinstaller fans_c.spec          -> produces dist\fans_c\  (at repo root)
;   2. Install Inno Setup 6 from https://jrsoftware.org/isdl.php
;   3. Open this file in Inno Setup Compiler and press F9
;
; OUTPUT LOCATION
; ---------------
;   OutputDir=..\..\FANS-C-Installer
;   Output lands in the project root at FANS-C-Installer\FANS-C-Setup.exe,
;   easy to find and copy to a USB drive for deployment.
;
; INSTALL PATH
; ------------
;   DefaultDirName=C:\FANSC
;   Short path is REQUIRED -- TensorFlow fails silently on paths longer than
;   ~80 characters due to Windows MAX_PATH limitations.
;   C:\FANSC is the safest default for barangay deployments.


; ===========================================================================
; [Setup]
; ===========================================================================

[Setup]

AppName=FANS-C Verification System
AppVersion=2.1.16
AppPublisher=OLFU Quezon City - College of Computer Studies
AppPublisherURL=https://github.com/AEILEXE/FANS-C-A-Secure-FaceNet-Based-Facial-Verification-System-for-Senior-Citizen-Stipend-Distribution
AppSupportURL=https://github.com/AEILEXE/FANS-C-A-Secure-FaceNet-Based-Facial-Verification-System-for-Senior-Citizen-Stipend-Distribution
AppUpdatesURL=https://github.com/AEILEXE/FANS-C-A-Secure-FaceNet-Based-Facial-Verification-System-for-Senior-Citizen-Stipend-Distribution

; Install to C:\FANSC by default.
; Short path is REQUIRED for TensorFlow -- do not change to Program Files.
DefaultDirName=C:\FANSC
DefaultGroupName=FANS-C

; Admin rights are required for:
;   - Writing to C:\FANSC (outside %AppData%)
;   - mkcert installing a root CA into the Windows trust store
;   - Modifying C:\Windows\System32\drivers\etc\hosts
;   - Registering Task Scheduler tasks under SYSTEM
PrivilegesRequired=admin

; Output installer location and filename
OutputDir=..\..\FANS-C-Installer
OutputBaseFilename=FANS-C-Setup-v2.1.16

; App icon (from project assets folder, bundled into the installer exe)
SetupIconFile=..\..\assets\logo.ico

; Compression -- lzma2/ultra64 gives the best ratio for TensorFlow's many files
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

; Modern wizard style
WizardStyle=modern

; Force 64-bit install mode (TensorFlow requires 64-bit Python)
ArchitecturesInstallIn64BitMode=x64compatible

; Uninstaller display
UninstallDisplayName=FANS-C Verification System
UninstallDisplayIcon={app}\fans_c.exe

; Version info embedded in the installer binary
VersionInfoVersion=2.1.16.0
VersionInfoCompany=OLFU Quezon City - College of Computer Studies
VersionInfoDescription=FANS-C Verification System Installer
VersionInfoProductName=FANS-C Verification System
VersionInfoProductVersion=2.1.16.0


; ===========================================================================
; [Languages]
; ===========================================================================

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"


; ===========================================================================
; [Tasks]  --  Optional install-time choices
; ===========================================================================

[Tasks]
Name: "desktopicon"; \
  Description: "Create a &desktop shortcut"; \
  GroupDescription: "Additional shortcuts:"


; ===========================================================================
; [Files]  --  What gets installed
; ===========================================================================

[Files]

; Clean installer staging folder (NOT the raw dist\fans_c\ directory).
;
; The staging folder is created by build_exe.ps1 step 5b using robocopy with
; explicit exclusions, then verified by the payload safety scan (step 5c).
; It is guaranteed to contain NO runtime or private files.
;
; The Excludes parameter below is a defense-in-depth safety net.  If staging
; is already clean, these patterns match nothing.  If any sensitive file
; somehow reached staging, Inno will skip it and the scan would have already
; failed the build.
;
; NEVER change the source back to dist\fans_c\ -- that folder is written to
; by fans_c.exe during local testing and WILL contain .env, db.sqlite3,
; certs, and logs from the developer's machine.
Source: "..\..\build\installer-staging\fans_c\*"; \
  DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs; \
  Excludes: ".env,db.sqlite3,*.sqlite3,fans-cert.pem,fans-cert-key.pem,rootCA.pem,rootCA-key.pem,*-key.pem,*.log"

; Full cleanup helper script (so IT staff can run it from the install folder)
Source: "..\..\scripts\admin\uninstall-clean.ps1"; \
  DestDir: "{app}\scripts\admin"; \
  Flags: ignoreversion

; Proxy-trust verification script — IT runs this after first launch to confirm
; that stderr.log contains the expected Waitress trusted-proxy startup lines.
Source: "..\..\scripts\admin\verify-proxy-trust.ps1"; \
  DestDir: "{app}\scripts\admin"; \
  Flags: ignoreversion

; Daily backup script — registered as a Task Scheduler task by launcher first-run setup.
; Runs at 21:00 daily under SYSTEM account; backs up db.sqlite3, .env, and media\.
Source: "..\..\scripts\admin\daily-backup.ps1"; \
  DestDir: "{app}\scripts\admin"; \
  Flags: ignoreversion

; Backup verification tool (read-only) — checks whether a specific backup
; directory is restore-ready without restoring from it. Added in v2.1.17;
; dot-sources daily-backup.ps1 for the restore-readiness functions.
Source: "..\..\scripts\admin\verify-backup.ps1"; \
  DestDir: "{app}\scripts\admin"; \
  Flags: ignoreversion

; Disaster-recovery restore tool — verifies the chosen backup, snapshots the
; current live state, then restores db.sqlite3/.env/media\ from it. Added in
; v2.1.17; see docs\BACKUP-RESTORE.md Section 7a.
Source: "..\..\scripts\admin\restore-backup.ps1"; \
  DestDir: "{app}\scripts\admin"; \
  Flags: ignoreversion

; ── Operational tools added in v2.1.18 ─────────────────────────────────────
; Packaging audit (v2.1.18) found these already-written, already-documented
; IT/Admin tools were never added to [Files], so a clean install was missing
; them entirely. None of these require the developer .venv or a source
; checkout -- they resolve their install root the same way daily-backup.ps1
; above does ($PSScriptRoot\..\..) and operate only on the installed
; environment (processes, ports, the Windows hosts file, Task Scheduler).

; Single-point IT/Admin tool — menu wrapping the other scripts in this list.
Source: "..\..\scripts\admin\fans-control-center.ps1"; \
  DestDir: "{app}\scripts\admin"; \
  Flags: ignoreversion

; Live status diagnostic — process/service/cert/DB/media/logs health report.
Source: "..\..\scripts\admin\check-system-health.ps1"; \
  DestDir: "{app}\scripts\admin"; \
  Flags: ignoreversion

; HTTPS/proxy diagnostic — ports, Caddy forwarding, /system/connection/ checks.
Source: "..\..\scripts\admin\check-runtime-network.ps1"; \
  DestDir: "{app}\scripts\admin"; \
  Flags: ignoreversion

; Re-registers the "FANS-C Verification System" autostart Task Scheduler task.
Source: "..\..\scripts\admin\repair-autostart.ps1"; \
  DestDir: "{app}\scripts\admin"; \
  Flags: ignoreversion

; Re-adds the fans-barangay.local hosts-file entry if HTTPS stops resolving.
Source: "..\..\scripts\admin\repair-hosts.ps1"; \
  DestDir: "{app}\scripts\admin"; \
  Flags: ignoreversion

; Re-registers the "FANS-C Watchdog" self-healing Task Scheduler task.
; HARD DEPENDENCY: registers a task pointing at watchdog.ps1 below — the two
; must always ship together, or the repaired task points at a missing file.
Source: "..\..\scripts\admin\repair-watchdog.ps1"; \
  DestDir: "{app}\scripts\admin"; \
  Flags: ignoreversion

; Continuous health-monitor/self-healing script — the actual target of the
; "FANS-C Watchdog" scheduled task that repair-watchdog.ps1 (re)registers.
Source: "..\..\scripts\admin\watchdog.ps1"; \
  DestDir: "{app}\scripts\admin"; \
  Flags: ignoreversion

; Stops the Waitress/Caddy background processes for maintenance — the
; autostart task is untouched, so services resume on next boot.
Source: "..\..\scripts\admin\stop-fans.ps1"; \
  DestDir: "{app}\scripts\admin"; \
  Flags: ignoreversion

; SQLite command-line tool required by daily-backup.ps1 for hot-backup (.backup command).
; Installed to {app}\tools\ — the path daily-backup.ps1 resolves via $PSScriptRoot navigation.
; Source: sqlite-tools-win-x64-3530400.zip from https://www.sqlite.org/download.html
; Version: 3.53.4 (2026-07-24)  SHA-256: 5DA2398D4913B893BD1EA578D85403B3A83A06FABF9D2303CA9F63EF0849FC6F
Source: "..\..\tools\sqlite3.exe"; \
  DestDir: "{app}\tools"; \
  Flags: ignoreversion


; ===========================================================================
; [Icons]  --  Shortcuts
; ===========================================================================

[Icons]

; Desktop shortcut (only if user selected the task)
Name: "{commondesktop}\FANS-C Verification System"; \
  Filename: "{app}\fans_c.exe"; \
  IconFilename: "{app}\assets\logo.ico"; \
  Tasks: desktopicon; \
  Comment: "Start the FANS-C Facial Verification System"

; Start Menu shortcut
Name: "{group}\FANS-C Verification System"; \
  Filename: "{app}\fans_c.exe"; \
  IconFilename: "{app}\assets\logo.ico"; \
  Comment: "Start the FANS-C Facial Verification System"

; Start Menu uninstall link
Name: "{group}\Uninstall FANS-C"; \
  Filename: "{uninstallexe}"; \
  Comment: "Remove FANS-C from this computer"


; ===========================================================================
; [Run]  --  Actions after installation completes
; ===========================================================================

[Run]

; Offer to launch immediately.
; fans_c.exe will auto-detect the missing .env and run the setup wizard.
Filename: "{app}\fans_c.exe"; \
  Description: "Launch &FANS-C now (runs setup wizard on first launch)"; \
  Flags: nowait postinstall skipifsilent


; ===========================================================================
; [UninstallRun]  --  Stop services before uninstalling
; ===========================================================================

[UninstallRun]

; Kill the standalone Waitress process (no-op for the packaged build where
; Waitress runs inside fans_c.exe, but covers manual-install side-by-side use)
Filename: "taskkill"; \
  Parameters: "/F /IM waitress-serve.exe"; \
  Flags: runhidden; \
  RunOnceId: "KillWaitress"

; Kill the HTTPS proxy
Filename: "taskkill"; \
  Parameters: "/F /IM caddy.exe"; \
  Flags: runhidden; \
  RunOnceId: "KillCaddy"

; Kill the launcher (which terminates the embedded Waitress thread)
Filename: "taskkill"; \
  Parameters: "/F /IM fans_c.exe"; \
  Flags: runhidden; \
  RunOnceId: "KillFansC"

; Remove Task Scheduler autostart tasks
Filename: "schtasks"; \
  Parameters: "/Delete /TN ""FANS-C Verification System"" /F"; \
  Flags: runhidden; \
  RunOnceId: "DelMainTask"

Filename: "schtasks"; \
  Parameters: "/Delete /TN ""FANS-C Watchdog"" /F"; \
  Flags: runhidden; \
  RunOnceId: "DelWatchdog"

Filename: "schtasks"; \
  Parameters: "/Delete /TN ""FANS-C Daily Backup"" /F"; \
  Flags: runhidden; \
  RunOnceId: "DelDailyBackup"

; Remove fans-barangay.local from Windows hosts file (PowerShell one-liner)
Filename: "powershell.exe"; \
  Parameters: "-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -Command ""$h='C:\Windows\System32\drivers\etc\hosts'; (Get-Content $h) | Where-Object {{ $_ -notmatch 'fans-barangay\.local' -and $_ -notmatch '# FANS-C' }} | Set-Content $h -Encoding UTF8"""; \
  Flags: runhidden; \
  RunOnceId: "RemoveHostsEntry"


; ===========================================================================
; [UninstallDelete]  --  Extra cleanup on uninstall
; ===========================================================================

[UninstallDelete]

; Remove runtime-created Python cache directories
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\fans\__pycache__"
Type: filesandordirs; Name: "{app}\accounts\__pycache__"
Type: filesandordirs; Name: "{app}\beneficiaries\__pycache__"
Type: filesandordirs; Name: "{app}\verification\__pycache__"
Type: filesandordirs; Name: "{app}\logs\__pycache__"

; NOTE: .env, db.sqlite3, media\, fans-cert.pem, fans-cert-key.pem are
; intentionally NOT listed here so user data survives a reinstall.
; A clean uninstall that also removes data requires manual deletion.


; ===========================================================================
; [Messages]  --  Custom wizard text
; ===========================================================================

[Messages]
FinishedLabel=FANS-C has been installed successfully.%n%nDouble-click the desktop shortcut or Start Menu entry to launch.%n%nFRESH INSTALL: The setup wizard will run on first launch to create your admin account and configure HTTPS.%n%nUPGRADE INSTALL: Your existing users and data are preserved. Launch FANS-C normally.%n%nCLIENT SETUP: After launch, copy %n  _internal\CLIENT-SETUP\%nfrom the install folder to a USB drive for each staff device.%n%nFor HTTPS trust verification, run as Administrator:%n  scripts\admin\verify-proxy-trust.ps1


; ===========================================================================
; [Code]  --  Pascal script for pre-install checks
; ===========================================================================

[Code]

{ ---------------------------------------------------------------------------
  CurStepChanged
  Kill running FANS-C processes before Inno Setup starts copying files
  (ssInstall step).  Without this, a reinstall over a running installation
  leaves fans_c.exe and caddy.exe alive, which causes:
    - fans_c.exe to hold file locks that block the copy step
    - the old caddy.exe to keep port 443 bound, so the newly installed
      caddy.exe cannot start -> HTTPS broken -> camera verification blocked
  ResultCode is intentionally ignored: taskkill exits 128 when the process
  is not running, which is not an error.
  --------------------------------------------------------------------------- }
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
  begin
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM fans_c.exe',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM caddy.exe',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    { Give the OS time to release file handles before the copy begins. }
    Sleep(1500);
  end;
end;


{ ---------------------------------------------------------------------------
  PrepareToInstall
  Detect whether this is an UPGRADE install or a FRESH install and inform
  the operator clearly.  Gives them a chance to back up data, or to do a
  proper clean install if that is what they intended.
  --------------------------------------------------------------------------- }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  existingEnv     : String;
  existingDb      : String;
  existingCert    : String;
  hasCerts        : Boolean;
begin
  Result := '';

  existingEnv  := ExpandConstant('{app}\.env');
  existingDb   := ExpandConstant('{app}\db.sqlite3');
  existingCert := ExpandConstant('{app}\fans-cert.pem');
  hasCerts     := FileExists(existingCert);

  if FileExists(existingEnv) then
  begin
    { ── UPGRADE INSTALL ── .env present means system was previously set up. }
    MsgBox(
      'UPGRADE INSTALL DETECTED' + Chr(13)+Chr(10) +
      '─────────────────────────────────────────────────' + Chr(13)+Chr(10) +
      '' + Chr(13)+Chr(10) +
      'A previous FANS-C installation was found.' + Chr(13)+Chr(10) +
      'Application files will be updated.  Your data is preserved:' + Chr(13)+Chr(10) +
      '' + Chr(13)+Chr(10) +
      '  .env          encryption key and settings' + Chr(13)+Chr(10) +
      '  db.sqlite3    beneficiary and audit records' + Chr(13)+Chr(10) +
      '  media\        captured beneficiary photos' + Chr(13)+Chr(10) +
      '  fans-cert.pem HTTPS certificate' + Chr(13)+Chr(10) +
      '' + Chr(13)+Chr(10) +
      'After installation, launch FANS-C normally.' + Chr(13)+Chr(10) +
      'Existing user accounts and all records are retained.' + Chr(13)+Chr(10) +
      '' + Chr(13)+Chr(10) +
      '─────────────────────────────────────────────────' + Chr(13)+Chr(10) +
      'Need a completely fresh install instead?' + Chr(13)+Chr(10) +
      '  1. Click Cancel now.' + Chr(13)+Chr(10) +
      '  2. Uninstall FANS-C from Add/Remove Programs.' + Chr(13)+Chr(10) +
      '  3. Delete the folder: ' + ExpandConstant('{app}') + Chr(13)+Chr(10) +
      '  4. Run this installer again.' + Chr(13)+Chr(10) +
      'A fresh install shows the Create Admin wizard on first launch.',
      mbInformation,
      MB_OK
    );
  end
  else if FileExists(existingDb) and not hasCerts then
  begin
    { ── PARTIAL STATE ── db exists but .env and cert are missing. }
    MsgBox(
      'INCOMPLETE PREVIOUS INSTALLATION' + Chr(13)+Chr(10) +
      '─────────────────────────────────────────────────' + Chr(13)+Chr(10) +
      '' + Chr(13)+Chr(10) +
      'A database was found but configuration files (.env / certificates)' + Chr(13)+Chr(10) +
      'are missing.  This can happen if a previous install was not' + Chr(13)+Chr(10) +
      'completed, or if configuration files were manually deleted.' + Chr(13)+Chr(10) +
      '' + Chr(13)+Chr(10) +
      'On first launch after installation, FANS-C will run the setup' + Chr(13)+Chr(10) +
      'wizard and generate a NEW encryption key.' + Chr(13)+Chr(10) +
      '' + Chr(13)+Chr(10) +
      'WARNING: existing face-embedding data in the database cannot be' + Chr(13)+Chr(10) +
      'decrypted with a new key.  If you need to keep those records:' + Chr(13)+Chr(10) +
      '  After install, restore the original EMBEDDING_ENCRYPTION_KEY' + Chr(13)+Chr(10) +
      '  value in .env before launching FANS-C.' + Chr(13)+Chr(10) +
      '' + Chr(13)+Chr(10) +
      'For a completely clean fresh install:' + Chr(13)+Chr(10) +
      '  1. Click Cancel, delete the folder ' + ExpandConstant('{app}') + Chr(13)+Chr(10) +
      '  2. Run this installer again.',
      mbConfirmation,
      MB_OK
    );
  end;
  { else: no .env and no db = true fresh install, no dialog needed. }
end;

