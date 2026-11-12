; Inno Setup script for Hams (Windows, per user, no admin rights).
;
; Built by packaging\build.ps1 -Installer, which passes:
;   /DAppVersion=<version from pyproject.toml>
;   /DSourceDir=<the PyInstaller folder build\dist\Hams>
;   /DOutputDir=<where Hams-Setup-<version>.exe goes>
;
; Installs to %LOCALAPPDATA%\Programs\Hams. Settings (%APPDATA%\Hams) and data
; (%LOCALAPPDATA%\Hams: logs, transcripts, GPU libraries) are never written
; here; the uninstaller offers to delete them but does not do so on its own.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\dist\Hams"
#endif
#ifndef OutputDir
  #define OutputDir "..\build\installer"
#endif

#define AppName "Hams"
#define AppExe "Hams.exe"
#define AppUrl "https://github.com/Mosaik114/hams-bachelorprojekt"
; Same name as MUTEX_NAME in hams\instance.py: setup and uninstall wait until Hams is closed.
#define AppMutexName "Global\Hams-SingleInstance-7a3f2c1b"

[Setup]
AppId={{5B0C7E7A-3C1D-4F7B-9E57-2B8E6A1D4C93}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
VersionInfoVersion={#AppVersion}
; Per-user install: {autopf} is %LOCALAPPDATA%\Programs without admin rights.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
AppMutex={#AppMutexName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=Hams-Setup-{#AppVersion}
SetupIconFile=..\hams\assets\branding\hams.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[CustomMessages]
english.AutostartTask=Start Hams when I sign in to Windows
german.AutostartTask=Hams bei der Windows-Anmeldung starten
english.ExtraOptions=Options:
german.ExtraOptions=Optionen:
english.RemoveUserData=Also delete your Hams settings, transcripts, logs and downloaded GPU libraries?%n%nThey are in the Hams folders under AppData\Roaming and AppData\Local. Choose No to keep them for a later reinstall.
german.RemoveUserData=Auch die Hams-Einstellungen, Transkripte, Logs und heruntergeladenen GPU-Bibliotheken löschen?%n%nSie liegen in den Hams-Ordnern unter AppData\Roaming und AppData\Local. Mit Nein bleiben sie für eine spätere Neuinstallation erhalten.

[Tasks]
Name: "autostart"; Description: "{cm:AutostartTask}"; GroupDescription: "{cm:ExtraOptions}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:ExtraOptions}"; Flags: unchecked

[InstallDelete]
; An update must not mix old and new libraries.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; The same entry hams\setup_autostart.py writes for a source install, so there is only ever one.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"""; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

{ Only an entry that starts this installation is ours; a source install's entry stays. }
function AutostartIsOurs(): Boolean;
var
  Value: String;
begin
  Result := RegQueryStringValue(HKCU, RunKey, '{#AppName}', Value) and
    (Pos(Lowercase(ExpandConstant('{app}\{#AppExe}')), Lowercase(Value)) > 0);
end;

procedure RemoveOurAutostart();
begin
  if AutostartIsOurs() then
    RegDeleteValue(HKCU, RunKey, '{#AppName}');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  { Reinstalled without the task: switch autostart off again. }
  if (CurStep = ssPostInstall) and not WizardIsTaskSelected('autostart') then
    RemoveOurAutostart();
end;

procedure RemoveUserData();
begin
  DelTree(ExpandConstant('{userappdata}\{#AppName}'), True, True, True);
  DelTree(ExpandConstant('{localappdata}\{#AppName}'), True, True, True);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemoveOurAutostart();
  { Offered, never forced: a silent uninstall keeps the user's data. }
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent() then
    if MsgBox(CustomMessage('RemoveUserData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      RemoveUserData();
end;
