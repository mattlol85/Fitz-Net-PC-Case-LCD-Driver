; Classic Windows installer for the PyInstaller one-dir build.
;
; Build with:
;   ISCC.exe packaging\installer.iss /DMyAppVersion=1.2.3
;
; Expects `dist\FitzLCD\` (from `python -m PyInstaller packaging\fitzlcd.spec`)
; to already exist, relative to the repo root, before compiling.
;
; Installs per-user under %LocalAppData%\Programs\FitzLCD with no UAC prompt
; (PrivilegesRequired=lowest) so the in-app auto-updater (fitzlcd/updater.py)
; keeps working exactly as it does today: it swaps files in the install
; directory itself without ever needing to elevate. Don't change
; PrivilegesRequired or the default install dir without also revisiting
; `updater.install_writable()` — see the "Releases" section of CLAUDE.md.

; VersionInfoVersion below requires a strictly numeric x.x.x.x string, so the
; fallback can't carry a "-dev" style suffix the way AppVersion could.
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "FitzLCD"
#define MyAppExeName "FitzLCD.exe"
#define MyAppPublisher "FitzLCD"
#define MyAppURL "https://github.com/mattlol85/Fitz-Net-PC-Case-LCD-Driver"

[Setup]
; Fixed AppId so future installer versions upgrade this same install rather
; than registering a second Add/Remove Programs entry. Do not regenerate.
AppId={{BEF1127B-03B5-4630-846E-53835DE0D157}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\FitzLCD
DefaultGroupName=FitzLCD
DisableProgramGroupPage=yes
; No admin rights needed to install or, later, to self-update.
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=FitzLCD-{#MyAppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
; User scene JSON and settings live under %APPDATA%\FitzLCD, untouched by
; install/uninstall (see CLAUDE.md "Scenes are data, not code").

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\FitzLCD\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\FitzLCD"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall FitzLCD"; Filename: "{uninstallexe}"
Name: "{autodesktop}\FitzLCD"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch FitzLCD"; Flags: nowait postinstall skipifsilent
