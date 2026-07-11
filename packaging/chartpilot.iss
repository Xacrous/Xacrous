; Inno Setup script for ChartPilot. Wraps the PyInstaller onedir build
; (dist\ChartPilot\) into a standard Windows installer.
;
; Build (from repo root, after `pyinstaller --noconfirm packaging\chartpilot.spec`):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\chartpilot.iss

#define MyAppName "ChartPilot"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "ChartPilot"
#define MyAppExeName "ChartPilot.exe"

[Setup]
AppId={{B6E2B9C1-6F2A-4E3B-9C1D-0F1A2B3C4D5E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist_installer
OutputBaseFilename=ChartPilot-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "..\dist\ChartPilot\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
