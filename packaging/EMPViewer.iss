; Inno Setup script for EMPViewer  (https://jrsoftware.org/isinfo.php)
;
; Build steps:
;   1. python build.py --onedir          -> produces dist\EMPViewer\EMPViewer.exe
;   2. iscc packaging\EMPViewer.iss      -> produces dist\EMPViewer-Setup.exe
;
; The installer is per-user (no admin prompt). It calls "EMPViewer.exe --register"
; so the app appears in "Open with" and Settings -> Default apps for
; .eml / .msg / .pst / .ost. Windows still requires the user to confirm making it
; the default for types another app already owns (Outlook's .msg / .pst); the
; optional task below just opens the Default-apps page for them.

#define AppName    "EMPViewer"
; Overridable from build.py:  iscc /DMyAppVersion=1.2.3 packaging\EMPViewer.iss
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define AppVersion MyAppVersion
#define AppPublisher "EMPViewer"
#define AppExe     "EMPViewer.exe"

[Setup]
AppId={{5F3B9C1E-6A2D-4E77-9C3A-EMPVIEWER0001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename={#AppName}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked
Name: "setdefault"; Description: "Open ""Default apps"" so I can make EMPViewer the default for mail files"; Flags: unchecked

[Files]
; Everything PyInstaller put in dist\EMPViewer\  (use --onedir)
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; Register file-type handlers for the current user.
Filename: "{app}\{#AppExe}"; Parameters: "--register"; Flags: runhidden runasoriginaluser
; Optionally open Settings -> Default apps.
Filename: "{app}\{#AppExe}"; Parameters: "--set-default"; Flags: runhidden runasoriginaluser nowait; Tasks: setdefault
; Offer to launch after install.
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#AppExe}"; Parameters: "--unregister"; Flags: runhidden; RunOnceId: "UnregEMPViewer"
