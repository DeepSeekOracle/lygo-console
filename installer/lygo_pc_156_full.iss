; ---------------------------------------------------------------------------
;  LYGO Local Agent Console - PC LOCAL - FULL installer, 1.5.6  (everything inside)
;
;  Payload : the SEALED 1.5.6 build  <DRIVE_D>\LYGO_CANON\2026-09-25_build-1.5.6_PC_LOCAL\PC_LOCAL
;  PLUS    : gemma4-12b + its mmproj projector + the coder + embeddings, straight from the
;            steward's vault (<MODELS>) into <install>\models - which config\console.json
;            scans ("scan_roots": [..., "./models"]), so the console boots a brain with no env var,
;            no download, no internet.
;  PLUS    : the PICTURE CHECKPOINT - SD 1.5 (v1-5-pruned-emaonly.safetensors, 4.27 GB) into
;            <install>\models\sd, with its CreativeML Open RAIL-M licence beside it. SD 1.5 is the
;            checkpoint whose licence ALLOWS redistribution (RAIL-M, licence text included); SDXL
;            Turbo does not (Stability's non-commercial research licence) and stays behind
;            tools\sd-cpu\FETCH_IMAGE_MODEL.bat.
;  Engine  : bundled (engine\llama-server.exe + impl DLLs + CUDA/CPU backends) AND the picture engine
;            (tools\sd-cpu\ = stable-diffusion.cpp, CPU build, 45 MB, no CUDA, no admin).
;
;  Windows cannot load a single Setup.exe larger than ~4.2 GB, so ~17 GB of weights cannot ride
;  inside one file: DiskSpanning keeps them in .bin slices beside SETUP.EXE and the installer reads
;  them itself. Same one-run install, a few files on disk - keep them together when you copy it.
;
;  The weights are stored nocompression: they are already quantised, so packing them would cost
;  build time and save nothing.
; ---------------------------------------------------------------------------

#define Payload "<DRIVE_D>\LYGO_CANON\2026-09-25_build-1.5.6_PC_LOCAL\PC_LOCAL"
#define ModelVault "<MODELS>"
#define SdVault "<MEDIA>\models\sd"
#define ModelPack "<USER>\AppData\Local\Temp\lygo_installer\modelpack"
#define Release "1.5.6"
#define AppTitle "LYGO Local Agent Console (PC LOCAL, everything included)"

[Setup]
AppId={{7C3A9E42-1B77-4A2E-9F31-2E5C0A6D1B10}
AppName={#AppTitle}
AppVersion={#Release}
AppVerName={#AppTitle} {#Release}
AppPublisher=LYGO / Δ9Φ963 · steward Justin Helmer (Lightfather)
AppComments=One console, three systems: USB LOCAL / PC LOCAL / WEB PORTAL (API only). Sealed 1.5.6 build with everything inside: the chat engine, the picture engine (stable-diffusion.cpp), gemma4-12b, its projector, the qwen2.5-coder, the embedding model, and the SD 1.5 image checkpoint. Install it and draw a picture - nothing to fetch. 1.5.6 adds the music systems: the exact YuE-shaped lyric template is the box's default (one press restores it), the sheet is validated and converted without spending a render, and the agent can write a whole song into the box from a brief or at random from the engine's own tag vocabulary.
AppCopyright=LYGO Sovereign License v3.0 - no resale, no rebranding, no modified redistribution. Model weights stay their authors': Apache-2.0 (Google DeepMind Gemma 4 12B, Alibaba Qwen2.5-Coder, Nomic AI) and CreativeML Open RAIL-M (SD 1.5) - both licence texts ship in models\.
VersionInfoVersion=1.5.6.0
VersionInfoDescription=LYGO Local Agent Console (PC LOCAL) 1.5.6 FULL - sealed build + picture engine + gemma4-12b + coder + embeddings + SD 1.5 checkpoint inside
DefaultDirName=C:\LYGO\LLM_CONSOLE
DefaultGroupName=LYGO Local Agent Console
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir=D:\
OutputBaseFilename=LYGO_LLM_CONSOLE_1.5.6_PC_SETUP_FULL
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#Payload}\LICENSE
InfoAfterFile=<EDRIVE>\lygo-protocol-stack\lygo_llm_console\installer\notes\INSTALL_NOTES_1.5.6_PC_FULL.txt
UninstallDisplayName={#AppTitle}
UsePreviousAppDir=yes
SetupLogging=yes
ShowLanguageDialog=no
MinVersion=10.0
; ~17 GB of weights + the console - say it before the wizard starts.
ExtraDiskSpaceRequired=23000000000

DiskSpanning=yes
DiskSliceSize=4294967295
SlicesPerDisk=1

[Files]
; the whole sealed build, folder shape included; the picture engine rides in this wildcard
; (tools\sd-cpu\).
; EXCLUDES - posted PUBLIC: no operator chat archive, no bench logs, no operator media
; (see lygo_pc_153.iss for the why of every pattern).
Source: "{#Payload}\*"; DestDir: "{app}"; Excludes: "workspace\memory\conversations\*,workspace\notes\*,workspace\notes\*\*,workspace\notes\*\*,workspace\memory\conversations\*\*,workspace\memory\conversations\*\*\*,workspace\memory\*.jsonl,workspace\audio\*,workspace\audio\*\*,workspace\audio\*\*\*,workspace\images\*,workspace\images\*\*,workspace\images\*\*\*,workspace\uploads\*,workspace\uploads\*\*,workspace\uploads\*\*\*,workspace\rust\*,workspace\rust\*\*,workspace\rust\*\*\*,workspace\memory\*.md,workspace\*.wav,workspace\*.mp3,workspace\*.flac,workspace\*.pdb,workspace\*.png,workspace\*.jpg,workspace\*.jpeg,workspace\*.webp,workspace\*.html,workspace\*.jsonl,workspace\*.txt,workspace\*.log,workspace\*.db,workspace\*.sqlite,workspace\*.sqlite3"; Flags: ignoreversion recursesubdirs createallsubdirs

; the brains, uncompressed, straight from the steward's vault
Source: "{#ModelVault}\gemma4-12b.gguf"; DestDir: "{app}\models"; Flags: ignoreversion nocompression
Source: "{#ModelVault}\gemma4-12b-mmproj.gguf"; DestDir: "{app}\models"; Flags: ignoreversion nocompression
Source: "{#ModelVault}\qwen2.5-coder-7b.gguf"; DestDir: "{app}\models"; Flags: ignoreversion nocompression
Source: "{#ModelVault}\nomic-embed-text-latest.gguf"; DestDir: "{app}\models"; Flags: ignoreversion nocompression

; the picture checkpoint, into the folder the console actually looks in (<install>\models\sd),
; with its licence beside it - RAIL-M requires that the licence travel with the weights
Source: "{#SdVault}\v1-5-pruned-emaonly.safetensors"; DestDir: "{app}\models\sd"; Flags: ignoreversion nocompression
Source: "{#ModelPack}\LICENSE-CREATIVEML-OPEN-RAIL-M.txt"; DestDir: "{app}\models\sd"; Flags: ignoreversion

; the vault pointer + fetcher, so this machine can also pull anything added later
Source: "{#ModelPack}\models.lock.json"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelPack}\fetch_models.py"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelPack}\LICENSE-APACHE-2.0.txt"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelVault}\manifest.json"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelVault}\sidecars\*"; DestDir: "{app}\models\sidecars"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
Name: "{autoprograms}\LYGO Local Agent Console\LYGO Local Agent Console (PC LOCAL)"; Filename: "{app}\LYGO_LLM_CONSOLE.bat"; WorkingDir: "{app}"; Comment: "Boot the PC LOCAL console (ports 9641 / 11441) - brains, picture engine and checkpoint included"
Name: "{autoprograms}\LYGO Local Agent Console\Stop the console"; Filename: "{app}\LYGO_LLM_CONSOLE_STOP.bat"; WorkingDir: "{app}"; Comment: "Stop the console and its engine"
Name: "{autoprograms}\LYGO Local Agent Console\Picture engine notes"; Filename: "{app}\tools\sd-cpu\README_IMAGE_ENGINE.txt"; Comment: "The bundled picture engine and the checkpoint that came with it (SD 1.5, RAIL-M)"
Name: "{autoprograms}\LYGO Local Agent Console\Fetch a faster checkpoint"; Filename: "{app}\tools\sd-cpu\FETCH_IMAGE_MODEL.bat"; WorkingDir: "{app}\tools\sd-cpu"; Comment: "Optional: SDXL Turbo (6.94 GB, 4 steps, much faster) - non-commercial licence, so you fetch it and the licence is shown"
Name: "{autoprograms}\LYGO Local Agent Console\Fetch anything added later"; Filename: "{app}\models\fetch_models.py"; WorkingDir: "{app}"; Comment: "Pull any model added to our vault after this build (sha256 verified)"
Name: "{autoprograms}\LYGO Local Agent Console\README"; Filename: "{app}\README.md"
Name: "{autoprograms}\LYGO Local Agent Console\Uninstall"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LYGO Local Agent Console"; Filename: "{app}\LYGO_LLM_CONSOLE.bat"; WorkingDir: "{app}"; Comment: "Boot the PC LOCAL console (everything included)"

[Run]
; the sealed copy is read-only on purpose; the INSTALLED tree must be writeable (save/, logs)
Filename: "{cmd}"; Parameters: "/c attrib -R ""{app}\*"" /S /D"; Flags: runhidden; StatusMsg: "Making the installed build writeable ..."

Filename: "{cmd}"; Parameters: "/k python ""{app}\src\install.py"""; WorkingDir: "{app}"; Description: "Provision this machine (seed soul / identity / memory - no steward data is copied)"; Flags: postinstall nowait skipifsilent

Filename: "{app}\LYGO_LLM_CONSOLE.bat"; Description: "Start the console now (the brains and the picture checkpoint are already in models\)"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec

Filename: "{app}\README.md"; Description: "Open the README"; Flags: postinstall shellexec unchecked skipifsilent

[Code]
function PythonFound(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/c python --version', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
            and (ResultCode = 0);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not PythonFound() then
  begin
    if MsgBox('Python 3 was not found on this machine.' + #13#10 + #13#10 +
              'The console boots through LYGO_LLM_CONSOLE.bat, which runs python. The install will lay ' +
              'down the full build, every model and the picture checkpoint, but the console will not start ' +
              'until Python 3 is on PATH.' + #13#10 + #13#10 +
              'Continue with the install anyway?',
              mbConfirmation, MB_YESNO) = IDNO then
      Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    Log('LYGO PC LOCAL FULL ' + '{#Release}' + ' installed (brains + picture engine + SD 1.5 checkpoint inside) to ' + ExpandConstant('{app}'));
end;
