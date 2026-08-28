; Inno Setup script for FitzLCD.
;
; Wraps the PyInstaller one-dir build (dist\FitzLCD\) in a Windows installer.
; Build the app first, then compile this:
;
;   python -m PyInstaller packaging\fitzlcd.spec --noconfirm
;   iscc packaging\fitzlcd.iss /DMyAppVersion=1.1.0
;
; Without /DMyAppVersion the version falls back to 0.0.0-dev so local test
; compiles still work. The release workflow always passes the real one.
;
; Defaults to a per-user install under %LOCALAPPDATA%\Programs\FitzLCD so the
; in-app self-updater keeps working (it needs a writable install dir; see
; updater.install_writable). The user can still elect an all-users install into
; Program Files, in which case self-update steps aside and offers the release
; page instead.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

#define MyAppName "FitzLCD"
#define MyAppPublisher "Matt"
#define MyAppURL "https://github.com/mattlol85/Fitz-Net-PC-Case-LCD-Driver"
#define MyAppExeName "FitzLCD.exe"
; Must match COLLECT(name=...) in fitzlcd.spec.
#define BuildDir "..\dist\FitzLCD"

[Setup]
AppId={{44D99DFA-FE10-4F32-A0BD-CB41EC6F721E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Per-user by default; the "dialog" override lets the user pick all-users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir=..\dist\installer
OutputBaseFilename=FitzLCD-{#MyAppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Let the Restart Manager shut a running FitzLCD before we overwrite it.
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Start {#MyAppName} automatically when I sign in"; GroupDescription: "Startup:"

[Files]
; The whole PyInstaller one-dir tree: FitzLCD.exe plus _internal/.
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Same per-user Run value the in-app autostart toggle writes
; (platform\autostart.py: HKCU Run, value "FitzLCD", bare quoted exe path), so
; the installer checkbox and the tray toggle stay in sync.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Belt and braces: drop the autostart value even if the task was unticked but
; the user later enabled it from the tray.
Filename: "{cmd}"; Parameters: "/c reg delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Run"" /v ""{#MyAppName}"" /f"; Flags: runhidden; RunOnceId: "DelAutostart"

[UninstallDelete]
; PyInstaller / self-update scratch that can accumulate next to the exe.
Type: filesandordirs; Name: "{app}\updates"

; User scenes and config live in %APPDATA%\FitzLCD and are deliberately left
; behind on uninstall.
