; ---------------------------------------------------------------------------
;  LYGO Local Agent Console - PC LOCAL  -  FULL installer (model inside)
;
;  Payload : the SEALED V1 build, copied out of the vault read-only copy:
;            <DRIVE_D>\LYGO_CANON\2026-09-20_build-1.1.0_PC_LOCAL-WORKING\PC_LOCAL
;  PLUS    : gemma4-12b + its mmproj projector, straight into <install>\models
;            - which config\console.json already lists as "scan_roots": ["./models"]
;            so the console boots it with no env var, no download, no internet.
;  Engine  : bundled (engine\llama-server.exe + impl DLLs + CUDA/CPU backends)
;  Builder : Inno Setup 6  (ISCC.exe)  ->  one EXE, no runtime dependency
;
;  The model files are stored with nocompression: they are already quantised,
;  so packing them would cost build time and save ~nothing.
; ---------------------------------------------------------------------------

#define Payload "<DRIVE_D>\LYGO_CANON\2026-09-20_build-1.1.0_PC_LOCAL-WORKING\PC_LOCAL"
#define ModelVault "<MODELS>"
#define ModelPack "<USER>\AppData\Local\Temp\lygo_installer\modelpack"
#define Release "1.1.0"
#define AppTitle "LYGO Local Agent Console (PC LOCAL, model included)"

[Setup]
AppId={{7C3A9E42-1B77-4A2E-9F31-2E5C0A6D1B10}
AppName={#AppTitle}
AppVersion={#Release}
AppVerName={#AppTitle} {#Release}
AppPublisher=LYGO / Δ9Φ963 · steward Justin Helmer (Lightfather)
AppComments=One console, three systems: USB LOCAL / PC LOCAL / WEB PORTAL (API only). Sealed V1 build, full engine, and the vision+audio model inside - it reads pictures and sound the moment it starts, on an ordinary machine.
AppCopyright=LYGO Sovereign License v3.0 - no resale, no rebranding, no modified redistribution. Model weights: Apache-2.0 (Google DeepMind, Gemma 4 12B); licence text ships in models\.
VersionInfoVersion=1.1.0.0
VersionInfoDescription=LYGO Local Agent Console (PC LOCAL) FULL installer - sealed V1 build + gemma4-12b inside
DefaultDirName=C:\LYGO\LLM_CONSOLE
DefaultGroupName=LYGO Local Agent Console
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir=D:\
OutputBaseFilename=LYGO_LLM_CONSOLE_V1_PC_SETUP_FULL
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#Payload}\LICENSE
InfoAfterFile=<USER>\AppData\Local\Temp\lygo_installer\INSTALL_NOTES_PC_FULL.txt
UninstallDisplayName={#AppTitle}
UsePreviousAppDir=yes
SetupLogging=yes
ShowLanguageDialog=no
MinVersion=10.0
; ~8 GB for the model beside the console - say it before the wizard starts.
ExtraDiskSpaceRequired=8000000000

; Windows cannot load a single Setup.exe larger than ~4.2 GB, so a 7.4 GB model
; cannot ride inside one file (Inno refuses: "Disk spanning must be enabled to
; create an installation larger than 4200000000 bytes"). Disk spanning keeps the
; real 12B brain in the installer: you run SETUP.EXE and it reads its .bin slice
; beside it. Same one-run install, two files on disk.
DiskSpanning=yes
DiskSliceSize=4294967295
SlicesPerDisk=1

[Files]
; the whole sealed build, folder shape included
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; the brain + its projector, uncompressed, straight from the steward's vault
Source: "{#ModelVault}\gemma4-12b.gguf"; DestDir: "{app}\models"; Flags: ignoreversion nocompression
Source: "{#ModelVault}\gemma4-12b-mmproj.gguf"; DestDir: "{app}\models"; Flags: ignoreversion nocompression

; the vault pointer + fetcher, so this machine can pull the extras (coder, embeddings)
Source: "{#ModelPack}\models.lock.json"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelPack}\fetch_models.py"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelPack}\LICENSE-APACHE-2.0.txt"; DestDir: "{app}\models"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\LYGO Local Agent Console\LYGO Local Agent Console (PC LOCAL)"; Filename: "{app}\LYGO_LLM_CONSOLE.bat"; WorkingDir: "{app}"; Comment: "Boot the PC LOCAL console (ports 9641 / 11441) - gemma4-12b included"
Name: "{autoprograms}\LYGO Local Agent Console\Stop the console"; Filename: "{app}\LYGO_LLM_CONSOLE_STOP.bat"; WorkingDir: "{app}"; Comment: "Stop the console and its engine"
Name: "{autoprograms}\LYGO Local Agent Console\README"; Filename: "{app}\README.md"
Name: "{autoprograms}\LYGO Local Agent Console\Uninstall"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LYGO Local Agent Console"; Filename: "{app}\LYGO_LLM_CONSOLE.bat"; WorkingDir: "{app}"; Comment: "Boot the PC LOCAL console (model included)"

[Run]
Filename: "{cmd}"; Parameters: "/c attrib -R ""{app}\*"" /S /D"; Flags: runhidden; StatusMsg: "Making the installed build writeable ..."

Filename: "{cmd}"; Parameters: "/k python ""{app}\src\install.py"""; WorkingDir: "{app}"; Description: "Provision this machine (seed soul / identity / memory - no steward data is copied)"; Flags: postinstall nowait skipifsilent

Filename: "{app}\LYGO_LLM_CONSOLE.bat"; Description: "Start the console now (gemma4-12b is already in models\)"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec

Filename: "{cmd}"; Parameters: "/k python ""{app}\models\fetch_models.py"" --profile full --yes"; WorkingDir: "{app}"; Description: "Also pull the code brain and embeddings from our own vault (qwen2.5-coder 7B + nomic-embed, 4.7 GB)"; Flags: postinstall nowait skipifsilent unchecked

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
              'The console boots through LYGO_LLM_CONSOLE.bat, which runs python. The install can ' +
              'continue and lay down the full build and the model, but the console will not start ' +
              'until Python 3 is on PATH.' + #13#10 + #13#10 +
              'Continue with the install anyway?',
              mbConfirmation, MB_YESNO) = IDNO then
      Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    Log('LYGO PC LOCAL FULL 1.1.0 installed from the sealed V1 build + gemma4-12b to ' + ExpandConstant('{app}'));
end;
