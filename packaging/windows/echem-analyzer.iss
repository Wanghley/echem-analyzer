; Inno Setup script for the Windows installer. build.py runs it:  iscc /DAppVersion=0.4.0 /DSourceDir=... /DOutDir=... echem-analyzer.iss
; A per-user install by default (no administrator needed); the user can choose "for all users" in the first dialog.
#define AppName "Echem Analyzer"
#define AppExe "EchemAnalyzer.exe"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\EchemAnalyzer"
#endif
#ifndef OutDir
  #define OutDir "..\..\dist\release"
#endif

[Setup]
; never change this GUID: it is how Windows recognises an upgrade of the same program
AppId={{AFE880CE-B0EC-4DAE-ACD4-77F880B83A33}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Wanghley
AppPublisherURL=https://github.com/Wanghley/echem-analyzer
AppSupportURL=https://github.com/Wanghley/echem-analyzer/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#OutDir}
OutputBaseFilename=Echem-Analyzer-{#AppVersion}-windows-x64-setup
SetupIconFile=..\..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; double-clicking a .echemproj opens it in Echem Analyzer (HKA = this user, or all users for an administrator install)
Root: HKA; Subkey: "Software\Classes\.echemproj"; ValueType: string; ValueName: ""; ValueData: "EchemAnalyzer.Project"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\EchemAnalyzer.Project"; ValueType: string; ValueName: ""; ValueData: "Echem Analyzer project"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\EchemAnalyzer.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"
Root: HKA; Subkey: "Software\Classes\EchemAnalyzer.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
