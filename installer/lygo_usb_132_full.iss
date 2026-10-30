; ---------------------------------------------------------------------------
;  LYGO Local Agent Console - USB CLAW - installer, 1.3.2, ALL IN ONE
;    (own engine + own Python + the code brain + the picture engine + a picture checkpoint)
;
;  Everything a stick needs to draw a picture AND do real tasking, with nothing to fetch:
;
;    Payload : the SEALED 1.3.2 stick build
;              <DRIVE_D>\LYGO_CANON\2026-09-24_build-1.3.2_USB_CLAW\USB_CLAW
;              ... which carries tools\sd-cpu\ = stable-diffusion.cpp CPU build (45 MB, no CUDA,
;              no admin) and the console code that finds it on any machine (media_root() falls
;              back to the kit root when the machine has no media root of its own).
;    Brains  : qwen2.5-coder-7b.gguf (4.68 GB) - the fast tasker/coder, from our own vault.
;    Pictures: v1-5-pruned-emaonly.safetensors (SD 1.5, 4.27 GB) into <install>\models\sd, with its
;              CreativeML Open RAIL-M licence beside it. RAIL-M is why THIS checkpoint is the one
;              carried: it allows redistribution with the licence attached, where SDXL Turbo does
;              not (Stability's non-commercial research licence) - options\sd-cpu\FETCH_IMAGE_MODEL.bat
;              will fetch Turbo for anyone who wants 4 steps instead of 20.
;
;  gemma4-12b (7.4 GB) is NOT here: with the coder and the checkpoint inside, everything together
;  would be a 17 GB installer for a stick. Boot, then either fetch it from our own vault with
;  models\fetch_models.py --profile basic, or use the PC FULL installer on the machine.
;
;  Windows cannot load a single Setup.exe larger than ~4.2 GB, so the weights ride in .bin slices
;  beside SETUP.EXE (DiskSpanning) and the installer reads them itself. Keep the files together.
;
;  Output goes to D:\ by default: the stick itself has ~3 GB free and this build is ~9.5 GB.
;  Copy SETUP.EXE and its .bin beside it onto the stick (or a bigger one) if you want it there.
; ---------------------------------------------------------------------------

#define Payload "<DRIVE_D>\LYGO_CANON\2026-09-24_build-1.3.2_USB_CLAW\USB_CLAW"
#define ModelVault "<MODELS>"
#define SdVault "<MEDIA>\models\sd"
#define ModelPack "<USER>\AppData\Local\Temp\lygo_installer\modelpack"
#define Release "1.3.2"
#define AppTitle "LYGO Local Agent Console (USB CLAW, everything included)"

[Setup]
AppId={{B41D6E88-3F52-4C09-8A7D-9E13C7B45602}
AppName={#AppTitle}
AppVersion={#Release}
AppVerName={#AppTitle} {#Release}
AppPublisher=LYGO / Δ9Φ963 · steward Justin Helmer (Lightfather)
AppComments=The stick build with everything inside: own engine, own Python, own model CAS, the picture engine (stable-diffusion.cpp) and both its brains - the qwen2.5-coder for tasking and the SD 1.5 checkpoint for pictures. Plug it into a machine, install, and it draws. Sealed 1.3.2.
AppCopyright=LYGO Sovereign License v3.0 - no resale, no rebranding, no modified redistribution. Model weights stay their authors': Apache-2.0 (Alibaba Qwen2.5-Coder) and CreativeML Open RAIL-M (SD 1.5) - both licence texts ship in models\.
VersionInfoVersion=1.3.2.0
VersionInfoDescription=LYGO Local Agent Console (USB CLAW) 1.3.2 ALL IN ONE - coder, picture engine and SD 1.5 checkpoint inside
DefaultDirName={src}\LYGO_BUILDER_KEY\lygo_llm_console
DefaultGroupName=LYGO Local Agent Console (USB CLAW)
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir=D:\
OutputBaseFilename=LYGO_LLM_CONSOLE_1.3.2_USB_SETUP_FULL
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#Payload}\LICENSE
InfoAfterFile=<USER>\AppData\Local\Temp\lygo_installer\INSTALL_NOTES_1.3.2_USB_FULL.txt
UninstallDisplayName={#AppTitle}
UsePreviousAppDir=yes
SetupLogging=yes
ShowLanguageDialog=no
MinVersion=10.0
ExtraDiskSpaceRequired=13000000000

DiskSpanning=yes
DiskSliceSize=4294967295
SlicesPerDisk=1

[Files]
; the whole sealed stick build - the picture engine rides in this wildcard (tools\sd-cpu\)
Source: "{#Payload}\*"; DestDir: "{app}"; Excludes: "workspace\memory\conversations\*,workspace\memory\conversations\*\*,workspace\memory\conversations\*\*\*,workspace\memory\*.jsonl"; Flags: ignoreversion recursesubdirs createallsubdirs

; the code brain
Source: "{#ModelVault}\qwen2.5-coder-7b.gguf"; DestDir: "{app}\models"; Flags: ignoreversion nocompression

; the picture checkpoint, into the folder the console actually looks in, licence beside it
Source: "{#SdVault}\v1-5-pruned-emaonly.safetensors"; DestDir: "{app}\models\sd"; Flags: ignoreversion nocompression
Source: "{#ModelPack}\LICENSE-CREATIVEML-OPEN-RAIL-M.txt"; DestDir: "{app}\models\sd"; Flags: ignoreversion

Source: "{#ModelPack}\models.lock.json"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelPack}\fetch_models.py"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelPack}\LICENSE-APACHE-2.0.txt"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelVault}\manifest.json"; DestDir: "{app}\models"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\LYGO Agent Stick"; Filename: "{app}\LYGO_AGENT_STICK.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console - the coder and the picture checkpoint are already in models\"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\LYGO Local Agent Console (stick ports)"; Filename: "{app}\LYGO_LLM_CONSOLE.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console on the PC pair of ports"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Stop the console"; Filename: "{app}\LYGO_AGENT_STICK_STOP.bat"; WorkingDir: "{app}"; Comment: "Stop the stick console and its engine"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Picture engine notes"; Filename: "{app}\tools\sd-cpu\README_IMAGE_ENGINE.txt"; Comment: "The bundled picture engine and the checkpoint that came with it (SD 1.5, RAIL-M)"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Fetch a faster checkpoint"; Filename: "{app}\tools\sd-cpu\FETCH_IMAGE_MODEL.bat"; WorkingDir: "{app}\tools\sd-cpu"; Comment: "Optional: SDXL Turbo (6.94 GB, 4 steps, much faster) - you fetch it, its licence is shown"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Fetch the other brains"; Filename: "{app}\models\fetch_models.py"; WorkingDir: "{app}"; Comment: "gemma4-12b and embeddings from our own vault (sha256 verified)"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\README"; Filename: "{app}\README.md"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Uninstall"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LYGO Agent Stick"; Filename: "{app}\LYGO_AGENT_STICK.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console (everything included)"

[Run]
Filename: "{cmd}"; Parameters: "/c attrib -R ""{app}\*"" /S /D"; Flags: runhidden; StatusMsg: "Making the installed build writeable ..."

Filename: "{app}\python\python.exe"; Parameters: """{app}\src\install.py"""; WorkingDir: "{app}"; Description: "Provision this machine (seed soul / identity / memory - no steward data is copied)"; Flags: postinstall nowait skipifsilent

Filename: "{app}\python\python.exe"; Parameters: """{app}\models\fetch_models.py"" --profile basic --yes --dest ""{app}\models"""; WorkingDir: "{app}"; Description: "Also fetch gemma4-12b now (7.3 GB - the big brain is the one thing not in this installer)"; Flags: postinstall nowait skipifsilent unchecked

Filename: "{app}\tools\sd-cpu\FETCH_IMAGE_MODEL.bat"; Description: "Also fetch SDXL Turbo now (6.94 GB, 4 steps instead of 20 - optional, SD 1.5 is already inside)"; WorkingDir: "{app}\tools\sd-cpu"; Flags: postinstall nowait skipifsilent unchecked

Filename: "{app}\LYGO_AGENT_STICK.bat"; Description: "Start the stick console now (the coder and the picture checkpoint are already in models\)"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec

Filename: "{app}\README.md"; Description: "Open the README"; Flags: postinstall shellexec unchecked skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    Log('LYGO USB CLAW ALL IN ONE ' + '{#Release}' + ' installed to ' + ExpandConstant('{app}') +
        ' (coder + picture engine + SD 1.5 checkpoint inside)');
end;
