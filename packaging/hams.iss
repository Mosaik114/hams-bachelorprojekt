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
;
; One-click update (hams\update_install.py): Hams runs this setup with
;   /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /RESTARTHAMS=window|background /LOG=<file>
; and quits. With /RESTARTHAMS the setup first waits until Hams has ended (its
; single-instance mutex is gone), installs over the existing installation with the
; tasks chosen before, and then starts Hams again (in the tray for "background").

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
; The name under which Hams is found: Start menu, desktop shortcut, Windows "Apps" and the setup wizard.
; Folders, the autostart entry and the EXE keep the plain AppName, and so does the app window.
#define AppDisplayName "Hams Dictate"
#define AppExe "Hams.exe"
#define AppUrl "https://github.com/Mosaik114/hams-bachelorprojekt"
; Same name as MUTEX_NAME in hams\instance.py: setup and uninstall wait until Hams is closed.
#define AppMutexName "Global\Hams-SingleInstance-7a3f2c1b"
; Nothing for the Hams it starts: only a test build passes a test instance on.
#define TestArgument ""

#ifdef TestInstance
; A test build for packaging\smoke_test.ps1 on a developer machine (/DTestInstance=<name>): its own AppId, program
; folder, Start menu entry, autostart value and lock, and every Hams it starts runs as that test instance
; (hams\test_instance.py: own settings, data, lock and events). The real Hams of the machine is never touched.
  #undef AppName
  #define AppName "Hams-test-" + TestInstance
  #undef AppDisplayName
  #define AppDisplayName "Hams Dictate Test " + TestInstance
  #undef AppMutexName
  #define AppMutexName "Global\Hams-SingleInstance-7a3f2c1b-test-" + TestInstance
  #undef TestArgument
  #define TestArgument " --test-instance=" + TestInstance
#endif

[Setup]
#ifndef TestInstance
AppId={{5B0C7E7A-3C1D-4F7B-9E57-2B8E6A1D4C93}
#else
AppId={{5B0C7E7A-3C1D-4F7B-9E57-2B8E6A1D4C94}
#endif
AppName={#AppDisplayName}
AppVersion={#AppVersion}
AppVerName={#AppDisplayName} {#AppVersion}
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
UninstallDisplayName={#AppDisplayName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Every build log names the compiler: the RedirectionGuard below depends on it.
#pragma message "Hams setup compiled with Inno Setup " + DecodeVer(Ver)
#if Ver >= EncodeVer(6, 7, 0)
; Inno Setup 6.7.0 and later switch on Windows' RedirectionGuard for the setup by default, and Windows hands it
; on to every program the setup starts - Hams after the install, after "Update now" (/RESTARTHAMS) and from the
; "Start Hams" tick. Inno Setup 7.1.1 exempts those only when the setup asked for administrator rights; Hams
; installs without them (PrivilegesRequired=lowest), so it would still inherit it. A Hams with RedirectionGuard
; cannot follow the links the Hugging Face cache creates on Windows with developer mode: every model access failed
; with error 448 ("untrusted mount point"). This setup only writes to the user's own folders, where the guard
; protects nothing a normal user could not change anyway. Hams itself also restarts without it
; (hams\redirection_guard.py) and stores its models without links (hams\models.py).
; Remove this line (and its #if) once an Inno Setup release stops handing RedirectionGuard on for setups with
; PrivilegesRequired=lowest as well: then the setup may keep its own protection again. A setup started with
; administrator rights asks first (AdminAllowed below): only there would the missing protection matter.
RedirectionGuard=no
#endif

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
english.AdminWarning=This setup is running with administrator rights. Hams installs just for you and does not need them.%n%nSafest: choose No and start the setup again with a normal double-click.%n%nInstall anyway?
german.AdminWarning=Das Setup läuft mit Administratorrechten. Hams wird nur für dich installiert und braucht diese Rechte nicht.%n%nAm sichersten: Nein wählen und das Setup mit einem normalen Doppelklick neu starten.%n%nTrotzdem installieren?

[Tasks]
Name: "autostart"; Description: "{cm:AutostartTask}"; GroupDescription: "{cm:ExtraOptions}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:ExtraOptions}"; Flags: unchecked

[InstallDelete]
; An update must not mix old and new libraries.
Type: filesandordirs; Name: "{app}\_internal"
; Shortcuts of versions up to 1.3.0 were called just "Hams"; an update replaces them instead of adding a second one.
Type: files; Name: "{autoprograms}\{#AppName}.lnk"
Type: files; Name: "{autodesktop}\{#AppName}.lnk"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppDisplayName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppDisplayName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; The same entry hams\setup_autostart.py writes for a source install, so there is only ever one.
; --background: at sign-in Hams starts in the tray; started from the Start menu it shows its window.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"" --background{#TestArgument}"; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "{#TestArgument}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; After a one-click update (silent, so the entry above is skipped): Hams comes back as it was.
Filename: "{app}\{#AppExe}"; Parameters: "{code:RestartParameters}"; Flags: nowait; Check: RestartAfterUpdate

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

{ /RESTARTHAMS=window or /RESTARTHAMS=background: started by Hams' own "Update now". }
function RestartAfterUpdate(): Boolean;
begin
  Result := ExpandConstant('{param:RESTARTHAMS|}') <> '';
end;

function RestartParameters(Param: String): String;
begin
  if ExpandConstant('{param:RESTARTHAMS|}') = 'background' then
    Result := '--background'
  else
    Result := '';
  Result := Trim(Result + '{#TestArgument}');
end;

{ UAC switched off: every program of an administrator runs with full rights, so there is no boundary a missing
  RedirectionGuard could weaken. }
function UacIsOff(): Boolean;
var
  Value: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System', 'EnableLUA', Value)
    and (Value = 0);
end;

{ This setup switches RedirectionGuard off (see [Setup]). That is safe for a setup with the user's own rights, the
  normal case. Run "as administrator", it would write into the user's folders with more rights than the user, and a
  link planted there could send that elsewhere. So it asks first - with "No" as the answer offered - and a silent
  setup with administrator rights stops, unless:
  - UAC is off (no boundary to protect),
  - it is Hams' own one-click update (/RESTARTHAMS: that Hams ran with these rights already; stopping would leave
    the user with no Hams at all),
  - /ALLOWADMIN says so explicitly.
  Nobody is locked out: a normal double-click always installs. }
function AdminAllowed(): Boolean;
begin
  Result := True;
  if not IsAdmin() then
    Exit;
  if UacIsOff() then
  begin
    Log('Running with administrator rights, UAC is off: no boundary to protect');
    Exit;
  end;
  if RestartAfterUpdate() then
  begin
    Log('Running with administrator rights: one-click update of a Hams that had them already');
    Exit;
  end;
  if ExpandConstant('{param:ALLOWADMIN|}') <> '' then
  begin
    Log('Running with administrator rights: allowed by /ALLOWADMIN');
    Exit;
  end;
  if WizardSilent() then
  begin
    Log('Running with administrator rights in silent mode: stopped (start it normally, or pass /ALLOWADMIN=1)');
    Result := False;
    Exit;
  end;
  Result := MsgBox(CustomMessage('AdminWarning'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

{ The Hams that started this update quits right after starting it; wait (at most a minute) until it has, so its
  files can be replaced. Without /RESTARTHAMS, AppMutex asks the user as before. }
function InitializeSetup(): Boolean;
var
  Waited: Integer;
begin
  Result := AdminAllowed();
  if not Result then
    Exit;
  if not RestartAfterUpdate() then
    Exit;
  Waited := 0;
  while CheckForMutexes('{#AppMutexName}') and (Waited < 60000) do
  begin
    Sleep(250);
    Waited := Waited + 250;
  end;
  Log(Format('Update: waited %d ms for Hams to quit', [Waited]));
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
