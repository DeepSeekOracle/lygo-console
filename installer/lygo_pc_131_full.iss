; ---------------------------------------------------------------------------
;  LYGO Local Agent Console - PC LOCAL - FULL installer, 1.3.1  (weights INSIDE)
;
;  Payload : the SEALED 1.3.1 build  <DRIVE_D>\LYGO_CANON\2026-09-22_build-1.3.1_PC_LOCAL\PC_LOCAL
;  PLUS    : gemma4-12b + its mmproj projector + the coder + embeddings, straight from the
;            steward's vault (<MODELS>) into <install>\models - which config\console.json
;            scans ("scan_roots": ["<MODELS>", "./models"]), so the console boots a brain
;            with no env var, no download, no internet.
;  Engine  : bundled (engine\llama-server.exe + impl DLLs + CUDA/CPU backends)
;
;  Windows cannot load a single Setup.exe larger than ~4.2 GB, so 12.5 GB of weights cannot ride
;  inside one file: DiskSpanning keeps them in .bin slices beside SETUP.EXE and the installer reads
;  them itself. Same one-run install, a few files on disk - keep them together when you copy it.
;
;  The weights are stored nocompression: they are already quantised, so packing them would cost
;  build time and save nothing.
; ---------------------------------------------------------------------------

#define Payload "<DRIVE_D>\LYGO_CANON\2026-09-22_build-1.3.1_PC_LOCAL\PC_LOCAL"
#define ModelVault "<MODELS>"
#define ModelPack "<USER>\AppData\Local\Temp\lygo_installer\modelpack"
#define Release "1.3.1"
#define AppTitle "LYGO Local Agent Console (PC LOCAL, everything included)"

[Setup]
AppId={{7C3A9E42-1B77-4A2E-9F31-2E5C0A6D1B10}
AppName={#AppTitle}
AppVersion={#Release}
AppVerName={#AppTitle} {#Release}
AppPublisher=LYGO / Δ9Φ963 · steward Justin Helmer (Lightfather)
AppComments=One console, three systems: USB LOCAL / PC LOCAL / WEB PORTAL (API only). Sealed 1.3.1 build, full engine, and every brain inside: gemma4-12b (text + images + audio), its projector, the qwen2.5-coder, and the embedding model. Nothing to download.
AppCopyright=LYGO Sovereign License v3.0 - no resale, no rebranding, no modified redistribution. Model weights: Apache-2.0 (Google DeepMind Gemma 4 12B, Alibaba Qwen2.5-Coder, Nomic AI); licence text ships in models\.
VersionInfoVersion=1.3.1.0
VersionInfoDescription=LYGO Local Agent Console (PC LOCAL) 1.3.1 FULL - sealed build + gemma4-12b + coder + embeddings inside
DefaultDirName=C:\LYGO\LLM_CONSOLE
DefaultGroupName=LYGO Local Agent Console
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir=D:\
OutputBaseFilename=LYGO_LLM_CONSOLE_1.3.1_PC_SETUP_FULL
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#Payload}\LICENSE
InfoAfterFile=<USER>\AppData\Local\Temp\lygo_installer\INSTALL_NOTES_1.3.1_PC_FULL.txt
UninstallDisplayName={#AppTitle}
UsePreviousAppDir=yes
SetupLogging=yes
ShowLanguageDialog=no
MinVersion=10.0
; ~13 GB for the weights beside the console - say it before the wizard starts.
ExtraDiskSpaceRequired=13500000000

DiskSpanning=yes
DiskSliceSize=4294967295
SlicesPerDisk=1

[Files]
; EXCLUDES - posted PUBLIC: no operator chat archive, no bench logs (see lygo_pc_131.iss for the why)
Source: "{#Payload}\*"; DestDir: "{app}"; Excludes: "workspace\memory\conversations\*,workspace\memory\conversations\*\*,workspace\memory\conversations\*\*\*,workspace\memory\*.jsonl"; Flags: ignoreversion recursesubdirs createallsubdirs

; the brains, uncompressed, straight from the steward's vault
Source: "{#ModelVault}\gemma4-12b.gguf"; DestDir: "{app}\models"; Flags: ignoreversion nocompression
Source: "{#ModelVault}\gemma4-12b-mmproj.gguf"; DestDir: "{app}\models"; Flags: ignoreversion nocompression
Source: "{#ModelVault}\qwen2.5-coder-7b.gguf"; DestDir: "{app}\models"; Flags: ignoreversion nocompression
Source: "{#ModelVault}\nomic-embed-text-latest.gguf"; DestDir: "{app}\models"; Flags: ignoreversion nocompression

; the vault pointer + fetcher, so this machine can also pull anything added later
Source: "{#ModelPack}\models.lock.json"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelPack}\fetch_models.py"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelPack}\LICENSE-APACHE-2.0.txt"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelVault}\manifest.json"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelVault}\sidecars\*"; DestDir: "{app}\models\sidecars"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
Name: "{autoprograms}\LYGO Local Agent Console\LYGO Local Agent Console (PC LOCAL)"; Filename: "{app}\LYGO_LLM_CONSOLE.bat"; WorkingDir: "{app}"; Comment: "Boot the PC LOCAL console (ports 9641 / 11441) - brains included"
Name: "{autoprograms}\LYGO Local Agent Console\Stop the console"; Filename: "{app}\LYGO_LLM_CONSOLE_STOP.bat"; WorkingDir: "{app}"; Comment: "Stop the console and its engine"
Name: "{autoprograms}\LYGO Local Agent Console\README"; Filename: "{app}\README.md"
Name: "{autoprograms}\LYGO Local Agent Console\Uninstall"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LYGO Local Agent Console"; Filename: "{app}\LYGO_LLM_CONSOLE.bat"; WorkingDir: "{app}"; Comment: "Boot the PC LOCAL console (brains included)"

[Run]
Filename: "{cmd}"; Parameters: "/c attrib -R ""{app}\*"" /S /D"; Flags: runhidden; StatusMsg: "Making the installed build writeable ..."

Filename: "{cmd}"; Parameters: "/k python ""{app}\src\install.py"""; WorkingDir: "{app}"; Description: "Provision this machine (seed soul / identity / memory - no steward data is copied)"; Flags: postinstall nowait skipifsilent

Filename: "{app}\LYGO_LLM_CONSOLE.bat"; Description: "Start the console now (the brains are already in models\)"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec

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
              'down the full build and every model, but the console will not start until Python 3 is on PATH.' + #13#10 + #13#10 +
              'Continue with the install anyway?',
              mbConfirmation, MB_YESNO) = IDNO then
      Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    Log('LYGO PC LOCAL FULL ' + '{#Release}' + ' installed (weights inside) to ' + ExpandConstant('{app}'));
end;
