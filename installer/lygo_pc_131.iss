; ---------------------------------------------------------------------------
;  LYGO Local Agent Console - PC LOCAL - installer, 1.3.1
;
;  Payload : the SEALED 1.3.1 build for this machine
;            <DRIVE_D>\LYGO_CANON\2026-09-22_build-1.3.1_PC_LOCAL\PC_LOCAL
;  Engine  : bundled (engine\ + impl DLLs + CUDA/CPU backends)
;  Python  : system python 3 (the launcher is a .bat that runs python)
;  Models  : NOT inside this installer. models\fetch_models.py pulls them from our own vault
;            (HuggingFace DeepSeekOracle/lygo-console-models, pinned revision, sha256 checked)
;            into <install>\models - which config\console.json scans ("scan_roots": [..., "./models"]).
;            For the installer with the weights INSIDE, use LYGO_LLM_CONSOLE_1.3.1_PC_SETUP_FULL.
;  Builder : Inno Setup 6  (ISCC.exe)  ->  one EXE, no runtime dependency
; ---------------------------------------------------------------------------

#define Payload "<DRIVE_D>\LYGO_CANON\2026-09-22_build-1.3.1_PC_LOCAL\PC_LOCAL"
#define ModelPack "<USER>\AppData\Local\Temp\lygo_installer\modelpack"
#define Release "1.3.1"
#define AppTitle "LYGO Local Agent Console (PC LOCAL)"

[Setup]
AppId={{7C3A9E42-1B77-4A2E-9F31-2E5C0A6D1B10}
AppName={#AppTitle}
AppVersion={#Release}
AppVerName={#AppTitle} {#Release}
AppPublisher=LYGO / Δ9Φ963 · steward Justin Helmer (Lightfather)
AppComments=One console, three systems: USB LOCAL / PC LOCAL / WEB PORTAL (API only). Sealed 1.3.1 build, full engine included. 1.3.1 measures every turn: tokens generated vs tokens shown, the gap attributed - a two-word ask that used to generate 106 tokens now generates 2.
AppCopyright=LYGO Sovereign License v3.0 - no resale, no rebranding, no modified redistribution.
VersionInfoVersion=1.3.1.0
VersionInfoDescription=LYGO Local Agent Console (PC LOCAL) 1.3.1 - sealed build, engine included, models fetched from our own vault
DefaultDirName=C:\LYGO\LLM_CONSOLE
DefaultGroupName=LYGO Local Agent Console
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir=D:\
OutputBaseFilename=LYGO_LLM_CONSOLE_1.3.1_PC_SETUP
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#Payload}\LICENSE
InfoAfterFile=<USER>\AppData\Local\Temp\lygo_installer\INSTALL_NOTES_1.3.1_PC.txt
UninstallDisplayName={#AppTitle}
UsePreviousAppDir=yes
SetupLogging=yes
ShowLanguageDialog=no
MinVersion=10.0
ExtraDiskSpaceRequired=13000000000

[Files]
; the whole sealed build, folder shape included (empty folders come along - the build is its shape)
;
; EXCLUDES - this build is posted PUBLIC, so the operator's own runtime state must not travel:
;   workspace\memory\conversations\*  the console's RAG archive = the steward's private chat history
;   workspace\memory\*.jsonl          bench / test-campaign logs
; The seal carries them (they are part of the live tree); a published installer must not.
Source: "{#Payload}\*"; DestDir: "{app}"; Excludes: "workspace\memory\conversations\*,workspace\memory\conversations\*\*,workspace\memory\conversations\*\*\*,workspace\memory\*.jsonl"; Flags: ignoreversion recursesubdirs createallsubdirs

; our vault pointer + fetcher + licence text into models\ - the installed copy's own scan root
Source: "{#ModelPack}\models.lock.json"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelPack}\fetch_models.py"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelPack}\LICENSE-APACHE-2.0.txt"; DestDir: "{app}\models"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\LYGO Local Agent Console\LYGO Local Agent Console (PC LOCAL)"; Filename: "{app}\LYGO_LLM_CONSOLE.bat"; WorkingDir: "{app}"; Comment: "Boot the PC LOCAL console (ports 9641 / 11441)"
Name: "{autoprograms}\LYGO Local Agent Console\Stop the console"; Filename: "{app}\LYGO_LLM_CONSOLE_STOP.bat"; WorkingDir: "{app}"; Comment: "Stop the console and its engine"
Name: "{autoprograms}\LYGO Local Agent Console\Fetch the models"; Filename: "{app}\models\fetch_models.py"; WorkingDir: "{app}"; Comment: "Download the brains from our own vault (sha256 verified)"
Name: "{autoprograms}\LYGO Local Agent Console\README"; Filename: "{app}\README.md"
Name: "{autoprograms}\LYGO Local Agent Console\Uninstall"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LYGO Local Agent Console"; Filename: "{app}\LYGO_LLM_CONSOLE.bat"; WorkingDir: "{app}"; Comment: "Boot the PC LOCAL console"

[Run]
; the sealed copy is read-only on purpose; the INSTALLED tree must be writeable (save/, logs)
Filename: "{cmd}"; Parameters: "/c attrib -R ""{app}\*"" /S /D"; Flags: runhidden; StatusMsg: "Making the installed build writeable ..."

Filename: "{cmd}"; Parameters: "/k python ""{app}\src\install.py"""; WorkingDir: "{app}"; Description: "Provision this machine (seed soul / identity / memory - no steward data is copied)"; Flags: postinstall nowait skipifsilent

Filename: "{cmd}"; Parameters: "/k python ""{app}\models\fetch_models.py"" --profile full --yes --dest ""{app}\models"""; WorkingDir: "{app}"; Description: "Fetch the brains now from our own vault (gemma4-12b + projector + embeddings + coder, 12.5 GB, sha256 verified)"; Flags: postinstall nowait skipifsilent unchecked

Filename: "{app}\LYGO_LLM_CONSOLE.bat"; Description: "Start the console now (it scans models\ when it boots)"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec

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
              'The PC LOCAL console boots through LYGO_LLM_CONSOLE.bat, which runs python. The install can ' +
              'continue and lay down the full build, but the console will not start until Python 3 is on PATH.' + #13#10 + #13#10 +
              'Continue with the install anyway?',
              mbConfirmation, MB_YESNO) = IDNO then
      Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    Log('LYGO PC LOCAL ' + '{#Release}' + ' installed from the sealed 1.3.1 build to ' + ExpandConstant('{app}'));
end;
