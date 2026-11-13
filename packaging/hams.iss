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
; It also offers, in a second question that defaults to No, to delete the Whisper
; models Hams downloaded into the shared Hugging Face cache (only Hams' own model
; folders, see CATALOG in hams\models.py). A silent uninstall asks nothing and
; deletes nothing.

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
english.RemoveModels=Also delete the downloaded speech models (about %1 GB)?%n%nThese are the Whisper models Hams downloaded into the Hugging Face cache:%n%2%n%nOther programs that use the same models would download them again. Choose No to keep them.
german.RemoveModels=Auch die heruntergeladenen Sprachmodelle löschen (etwa %1 GB)?%n%nDas sind die Whisper-Modelle, die Hams in den Hugging-Face-Cache geladen hat:%n%2%n%nAndere Programme, die dieselben Modelle nutzen, laden sie dann erneut. Mit Nein bleiben sie erhalten.

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

{ The Hugging Face cache, resolved like huggingface_hub does: HF_HUB_CACHE, then HF_HOME\hub. }
function ModelCacheDir(): String;
begin
  Result := GetEnv('HF_HUB_CACHE');
  if Result <> '' then
    Exit;
  Result := GetEnv('HF_HOME');
  if Result <> '' then
    Result := AddBackslash(Result) + 'hub'
  else
    Result := ExpandConstant('{%USERPROFILE}\.cache\huggingface\hub');
end;

{ Exactly the repositories of CATALOG in hams\models.py; nothing else in the shared cache is touched. }
const
  ModelCount = 6;

function ModelFolder(Index: Integer): String;
begin
  case Index of
    0: Result := 'models--mobiuslabsgmbh--faster-whisper-large-v3-turbo';
    1: Result := 'models--Systran--faster-whisper-large-v3';
    2: Result := 'models--Systran--faster-whisper-medium';
    3: Result := 'models--Systran--faster-whisper-small';
    4: Result := 'models--Systran--faster-whisper-base';
  else
    Result := 'models--Systran--faster-whisper-tiny';
  end;
end;

{ Bytes below a folder. Links (the snapshot entries of the cache) count as 0 and are not followed.
  Only SizeLow: no file of these models reaches 4 GB, and it keeps the arithmetic integer. }
function FolderSize(const Dir: String): Int64;
var
  FindRec: TFindRec;
begin
  Result := 0;
  if not FindFirst(AddBackslash(Dir) + '*', FindRec) then
    Exit;
  try
    repeat
      if (FindRec.Name = '.') or (FindRec.Name = '..') then
        Continue;
      if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
      begin
        if (FindRec.Attributes and $400) = 0 then  { FILE_ATTRIBUTE_REPARSE_POINT }
          Result := Result + FolderSize(AddBackslash(Dir) + FindRec.Name);
      end
      else
        Result := Result + FindRec.SizeLow;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

{ "1.6" for 1.6 GB (10^9 bytes, as the app counts), in integer arithmetic. }
function GigabytesText(Bytes: Int64): String;
var
  Tenths: Int64;
begin
  Tenths := (Bytes + 50000000) div 100000000;
  if (Tenths = 0) and (Bytes > 0) then
    Tenths := 1;
  Result := IntToStr(Tenths div 10) + '.' + IntToStr(Tenths mod 10);
end;

procedure OfferToRemoveModels();
var
  Cache, Folder, Names: String;
  Index: Integer;
  Total: Int64;
begin
  Cache := ModelCacheDir();
  Names := '';
  Total := 0;
  for Index := 0 to ModelCount - 1 do
  begin
    Folder := AddBackslash(Cache) + ModelFolder(Index);
    if DirExists(Folder) then
    begin
      Names := Names + #13#10 + '- ' + ModelFolder(Index);
      Total := Total + FolderSize(Folder);
    end;
  end;
  if Names = '' then
    Exit;
  if MsgBox(FmtMessage(CustomMessage('RemoveModels'), [GigabytesText(Total), Cache + Names]),
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> IDYES then
    Exit;
  for Index := 0 to ModelCount - 1 do
  begin
    DelTree(AddBackslash(Cache) + ModelFolder(Index), True, True, True);
    { huggingface_hub keeps a lock folder per repository next to the models. }
    DelTree(AddBackslash(Cache) + '.locks\' + ModelFolder(Index), True, True, True);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemoveOurAutostart();
  { Offered, never forced: a silent uninstall keeps the user's data and the models. }
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent() then
  begin
    if MsgBox(CustomMessage('RemoveUserData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      RemoveUserData();
    OfferToRemoveModels();
  end;
end;
