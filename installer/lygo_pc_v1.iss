; ---------------------------------------------------------------------------
;  LYGO Local Agent Console - PC LOCAL  -  full installer
;
;  Payload : the SEALED V1 build, copied out of the vault read-only copy:
;            <DRIVE_D>\LYGO_CANON\2026-09-20_build-1.1.0_PC_LOCAL-WORKING\PC_LOCAL
;  Engine  : bundled (engine\llama-server.exe + impl DLLs + CUDA/CPU backends)
;  Builder : Inno Setup 6  (ISCC.exe)  ->  one EXE, no runtime dependency
;
;  Nothing here edits a sealed build. The vault copy stays reference-only; this
;  installer carries a copy of it out to the machine it is run on.
; ---------------------------------------------------------------------------

#define Payload "<DRIVE_D>\LYGO_CANON\2026-09-20_build-1.1.0_PC_LOCAL-WORKING\PC_LOCAL"
#define Release "1.1.0"
#define AppTitle "LYGO Local Agent Console (PC LOCAL)"

[Setup]
AppId={{7C3A9E42-1B77-4A2E-9F31-2E5C0A6D1B10}
AppName={#AppTitle}
AppVersion={#Release}
AppVerName={#AppTitle} {#Release}
AppPublisher=LYGO / Δ9Φ963 · steward Justin Helmer (Lightfather)
AppComments=One console, three systems: USB LOCAL / PC LOCAL / WEB PORTAL (API only). Built from the sealed V1 build; full engine included.
AppCopyright=LYGO Sovereign License v3.0 - no resale, no rebranding, no modified redistribution.
VersionInfoVersion=1.1.0.0
VersionInfoDescription=LYGO Local Agent Console (PC LOCAL) installer, built from the sealed V1 build
DefaultDirName=C:\LYGO\LLM_CONSOLE
DefaultGroupName=LYGO Local Agent Console
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir=D:\
OutputBaseFilename=LYGO_LLM_CONSOLE_V1_PC_SETUP
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#Payload}\LICENSE
InfoAfterFile=<USER>\AppData\Local\Temp\lygo_installer\INSTALL_NOTES_PC.txt
UninstallDisplayName={#AppTitle}
UsePreviousAppDir=yes
SetupLogging=yes
ShowLanguageDialog=no
MinVersion=10.0

[Files]
; recursesubdirs + createallsubdirs: the whole build, folder shape included
; (the kit's own models\ folder and every fixture folder come along empty).
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Our own vault pointer + fetcher, into models\ - which the kit already scans
; (config\console.json: "scan_roots": ["./models"]). The weights themselves are
; NOT inside this installer: fetch_models.py pulls them from the steward's own
; store, SHA-256 verified, and refuses any URL that is not ours.
Source: "<USER>\AppData\Local\Temp\lygo_installer\modelpack\models.lock.json"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "<USER>\AppData\Local\Temp\lygo_installer\modelpack\fetch_models.py"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "<USER>\AppData\Local\Temp\lygo_installer\modelpack\LICENSE-APACHE-2.0.txt"; DestDir: "{app}\models"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\LYGO Local Agent Console\LYGO Local Agent Console (PC LOCAL)"; Filename: "{app}\LYGO_LLM_CONSOLE.bat"; WorkingDir: "{app}"; Comment: "Boot the PC LOCAL console (ports 9641 / 11441)"
Name: "{autoprograms}\LYGO Local Agent Console\Stop the console"; Filename: "{app}\LYGO_LLM_CONSOLE_STOP.bat"; WorkingDir: "{app}"; Comment: "Stop the console and its engine"
Name: "{autoprograms}\LYGO Local Agent Console\README"; Filename: "{app}\README.md"
Name: "{autoprograms}\LYGO Local Agent Console\Uninstall"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LYGO Local Agent Console"; Filename: "{app}\LYGO_LLM_CONSOLE.bat"; WorkingDir: "{app}"; Comment: "Boot the PC LOCAL console"

[Run]
; The sealed copy is read-only on purpose. Clear that on the INSTALLED tree only -
; a console that cannot write save/ or its logs is not a working console.
Filename: "{cmd}"; Parameters: "/c attrib -R ""{app}\*"" /S /D"; Flags: runhidden; StatusMsg: "Making the installed build writeable ..."

; Provision this machine (seeds workspace identity, never steward vaults).
Filename: "{cmd}"; Parameters: "/k python ""{app}\src\install.py"""; WorkingDir: "{app}"; Description: "Provision this machine (seed soul / identity / memory - no steward data is copied)"; Flags: postinstall nowait skipifsilent

Filename: "{app}\LYGO_LLM_CONSOLE.bat"; Description: "Start the console now"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec

Filename: "{cmd}"; Parameters: "/k python ""{app}\models\fetch_models.py"" --profile basic --yes"; WorkingDir: "{app}"; Description: "Download the basic models now from our own vault (gemma4-12b + projector + embeddings, 7.3 GB, SHA-256 verified)"; Flags: postinstall nowait skipifsilent unchecked

Filename: "{app}\README.md"; Description: "Open the README"; Flags: postinstall shellexec unchecked skipifsilent

[Code]
function PythonFound(): Boolean;
var
  ResultCode: Integer;
begin
  { the launcher is a .bat that runs python - say so up front if it is missing }
  Result := Exec(ExpandConstant('{cmd}'), '/c python --version', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
            and (ResultCode = 0);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not PythonFound() then
  begin
    if MsgBox('Python 3 was not found on this machine.' + #13#10 + #13#10 +
              'The PC LOCAL console boots through LYGO_LLM_CONSOLE.bat, which runs python. ' +
              'The install can continue and lay down the full build, but the console will not ' +
              'start until Python 3 is on PATH.' + #13#10 + #13#10 +
              'Continue with the install anyway?',
              mbConfirmation, MB_YESNO) = IDNO then
      Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    Log('LYGO PC LOCAL ' + '{#Release}' + ' installed from the sealed V1 build to ' + ExpandConstant('{app}'));
end;
