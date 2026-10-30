; ---------------------------------------------------------------------------
;  LYGO Local Agent Console - PC LOCAL - installer, 1.3.2
;
;  Payload : the SEALED 1.3.2 build for this machine
;            <DRIVE_D>\LYGO_CANON\2026-09-24_build-1.3.2_PC_LOCAL\PC_LOCAL
;  Engine  : bundled (engine\ + impl DLLs + CUDA/CPU backends)
;  Pictures: bundled - tools\sd-cpu\ is the CPU build of stable-diffusion.cpp (45 MB, no CUDA, no
;            card, no admin). media_root() falls back to the install root, so a fresh install finds
;            its own picture engine with no config. The CHECKPOINT is not redistributed (SDXL Turbo
;            is Stability's non-commercial research licence, SD 1.5 is CreativeML Open RAIL-M):
;            tools\sd-cpu\FETCH_IMAGE_MODEL.bat fetches one once, resumable and size-checked, from
;            the author's own repo, and says which licence applies.
;  Python  : system python 3 (the launcher is a .bat that runs python)
;  Models  : NOT inside this installer. models\fetch_models.py pulls them from our own vault
;            (HuggingFace DeepSeekOracle/lygo-console-models, pinned revision, sha256 checked)
;            into <install>\models - which config\console.json scans ("scan_roots": [..., "./models"]).
;            For the installer with the weights INSIDE, use LYGO_LLM_CONSOLE_1.3.2_PC_SETUP_FULL.
;  Builder : Inno Setup 6  (ISCC.exe)  ->  one EXE, no runtime dependency
; ---------------------------------------------------------------------------

#define Payload "<DRIVE_D>\LYGO_CANON\2026-09-24_build-1.3.2_PC_LOCAL\PC_LOCAL"
#define ModelPack "<USER>\AppData\Local\Temp\lygo_installer\modelpack"
#define Release "1.3.2"
#define AppTitle "LYGO Local Agent Console (PC LOCAL)"

[Setup]
AppId={{7C3A9E42-1B77-4A2E-9F31-2E5C0A6D1B10}
AppName={#AppTitle}
AppVersion={#Release}
AppVerName={#AppTitle} {#Release}
AppPublisher=LYGO / Δ9Φ963 · steward Justin Helmer (Lightfather)
AppComments=One console, three systems: USB LOCAL / PC LOCAL / WEB PORTAL (API only). Sealed 1.3.2 build, full engine included, and the picture engine now travels INSIDE the package: a fresh install draws a picture with no config at all once the checkpoint is fetched (one file, 6.9 GB, fetched from its author, not redistributed by us).
AppCopyright=LYGO Sovereign License v3.0 - no resale, no rebranding, no modified redistribution.
VersionInfoVersion=1.3.2.0
VersionInfoDescription=LYGO Local Agent Console (PC LOCAL) 1.3.2 - sealed build, chat engine + picture engine included, weights fetched from our own vault
DefaultDirName=C:\LYGO\LLM_CONSOLE
DefaultGroupName=LYGO Local Agent Console
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir=D:\
OutputBaseFilename=LYGO_LLM_CONSOLE_1.3.2_PC_SETUP
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#Payload}\LICENSE
InfoAfterFile=<USER>\AppData\Local\Temp\lygo_installer\INSTALL_NOTES_1.3.2_PC.txt
UninstallDisplayName={#AppTitle}
UsePreviousAppDir=yes
SetupLogging=yes
ShowLanguageDialog=no
MinVersion=10.0
; the build itself, plus room for the picture checkpoint (6.9 GB) - the console boots without it
ExtraDiskSpaceRequired=20000000000

[Files]
; the whole sealed build, folder shape included (empty folders come along - the build is its shape)
; The picture engine rides in this wildcard: tools\sd-cpu\ (sd-cli.exe + stable-diffusion.dll + the
; ggml CPU kernels + get_model.ps1 / FETCH_IMAGE_MODEL.bat / README_IMAGE_ENGINE.txt).
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
Name: "{autoprograms}\LYGO Local Agent Console\Fetch the brains"; Filename: "{app}\models\fetch_models.py"; WorkingDir: "{app}"; Comment: "Download the brains from our own vault (sha256 verified)"
Name: "{autoprograms}\LYGO Local Agent Console\Fetch the picture checkpoint"; Filename: "{app}\tools\sd-cpu\FETCH_IMAGE_MODEL.bat"; WorkingDir: "{app}\tools\sd-cpu"; Comment: "One file, 6.9 GB, fetched once from its author (SDXL Turbo) - then image_generate works offline"
Name: "{autoprograms}\LYGO Local Agent Console\Picture engine notes"; Filename: "{app}\tools\sd-cpu\README_IMAGE_ENGINE.txt"; Comment: "What the bundled picture engine is, and what is not bundled"
Name: "{autoprograms}\LYGO Local Agent Console\README"; Filename: "{app}\README.md"
Name: "{autoprograms}\LYGO Local Agent Console\Uninstall"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LYGO Local Agent Console"; Filename: "{app}\LYGO_LLM_CONSOLE.bat"; WorkingDir: "{app}"; Comment: "Boot the PC LOCAL console"

[Run]
; the sealed copy is read-only on purpose; the INSTALLED tree must be writeable (save/, logs)
Filename: "{cmd}"; Parameters: "/c attrib -R ""{app}\*"" /S /D"; Flags: runhidden; StatusMsg: "Making the installed build writeable ..."

Filename: "{cmd}"; Parameters: "/k python ""{app}\src\install.py"""; WorkingDir: "{app}"; Description: "Provision this machine (seed soul / identity / memory - no steward data is copied)"; Flags: postinstall nowait skipifsilent

Filename: "{cmd}"; Parameters: "/k python ""{app}\models\fetch_models.py"" --profile full --yes --dest ""{app}\models"""; WorkingDir: "{app}"; Description: "Fetch the brains now from our own vault (gemma4-12b + projector + embeddings + coder, 12.5 GB, sha256 verified)"; Flags: postinstall nowait skipifsilent unchecked

Filename: "{app}\tools\sd-cpu\FETCH_IMAGE_MODEL.bat"; Description: "Fetch the picture checkpoint now (SDXL Turbo, 6.94 GB, once - image_generate needs it; resumes if the line drops)"; WorkingDir: "{app}\tools\sd-cpu"; Flags: postinstall nowait skipifsilent unchecked

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
    Log('LYGO PC LOCAL ' + '{#Release}' + ' installed from the sealed 1.3.2 build to ' + ExpandConstant('{app}') +
        ' (chat engine + bundled picture engine; checkpoint fetched separately)');
end;
