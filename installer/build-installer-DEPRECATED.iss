; ============================================================
; DEPRECATED - DO NOT USE.
; Current production installer:
; dev/installer/fans_c.iss
; ============================================================
;
; ============================================================
; build-installer.iss  --  FANS-C Inno Setup Installer Script
;
; Requirements:
;   - Inno Setup 6.x  (https://jrsoftware.org/isinfo.php)
;   - installer\assets\python-3.11.9-embed-amd64.zip  (bundled offline)
;   - Python 3.11 installed on the TARGET machine (py launcher)
;
; Build command (run from project root):
;   ISCC installer\build-installer.iss
;
; Output: installer\output\FANS-C-Setup.exe
; ============================================================

#define AppName    "FANS-C"
#define AppVersion "1.0"

; Pre-build guard: fail immediately with a clear message if the embedded Python zip is absent.
; Place python-3.11.9-embed-amd64.zip (from python.org/downloads/windows/) in installer\assets\
#if !FileExists(SourcePath + "\assets\python-3.11.9-embed-amd64.zip")
  #error "MISSING: installer\assets\python-3.11.9-embed-amd64.zip -- Download the Python 3.11.9 Windows embeddable package (64-bit) from python.org/downloads/windows/ and place it in installer\assets\ before building."
#endif

[Setup]
AppId={{8F3A2C1D-5E6B-4A7C-9D2E-1F0B3C4A8E5F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Barangay Senior Citizen Verification System
AppSupportURL=https://fans-barangay.local/help/connect/
DefaultDirName={autopf}\FANS-C
DefaultGroupName=FANS-C
DisableProgramGroupPage=yes
OutputDir=installer\output
OutputBaseFilename=FANS-C-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
UninstallDisplayName=FANS-C Barangay Verification System
; Source root: one level above this .iss file (project root)
SourceDir={#SourcePath}\..\

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Copy ALL project source files, excluding generated/runtime directories
Source: "*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion; Excludes: ".venv\*,__pycache__\*,*.pyc,installer\output\*,.git\*,dev\*,dist\*,build\*,staticfiles\*,logs\*,media\*,.claude\*"
; Explicit copy of .env so dotfile is guaranteed regardless of wildcard behaviour
Source: ".env"; DestDir: "{app}"; Flags: ignoreversion
; Python 3.11 embeddable zip -- bundled for offline pip bootstrap if needed
Source: "installer\assets\python-3.11.9-embed-amd64.zip"; DestDir: "{app}\installer\assets"; Flags: ignoreversion

[Dirs]
Name: "{app}\logs"
Name: "{app}\media"
Name: "{app}\staticfiles"

[Icons]
Name: "{commondesktop}\FANS-C"; Filename: "{app}\scripts\start\start-fans.bat"; WorkingDir: "{app}"
Name: "{commonstartmenu}\Programs\FANS-C\FANS-C"; Filename: "{app}\scripts\start\start-fans.bat"; WorkingDir: "{app}"
Name: "{commonstartmenu}\Programs\FANS-C\Uninstall FANS-C"; Filename: "{uninstallexe}"

[Run]
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -NonInteractive -File ""{app}\setup-fans.ps1"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; StatusMsg: "Running FANS-C setup (this may take several minutes)..."

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\staticfiles"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\media"
