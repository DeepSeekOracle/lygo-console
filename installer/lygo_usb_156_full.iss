; ---------------------------------------------------------------------------
;  LYGO Local Agent Console - USB CLAW / stick build - FULL installer, 1.5.6
;
;  Payload : the SEALED 1.5.6 stick build  <DRIVE_D>\LYGO_CANON\2026-09-25_build-1.5.6_USB_CLAW\USB_CLAW
;            (own engine\, own python\, its own picture engine in tools\sd-cpu\)
;  PLUS    : the weights a stick can actually carry - the qwen2.5-coder and the embedding model
;            from the steward's vault (<MODELS> -> <install>\models) - and the SD 1.5 image
;            checkpoint (4.27 GB) with its CreativeML Open RAIL-M licence, because that is the one
;            checkpoint whose licence allows redistribution. gemma4-12b (7.3 GB) is NOT inside:
;            with it, this installer stops fitting on the media it exists for; fetch it later with
;            models\fetch_models.py if the target machine has room.
;
;  Windows cannot load a single Setup.exe larger than ~4.2 GB, so the payload rides in .bin slices
;  beside SETUP.EXE (DiskSpanning) and the installer reads them itself. Keep the files together.
;
;  Default install target is built from where the installer itself runs:
;  run it from the stick root (E:\) and it installs to <USB_ROOT>\LYGO_BUILDER_KEY\lygo_llm_console.
;  The target needs ~10 GB free for this variant; the plain LYGO_LLM_CONSOLE_1.5.6_USB_SETUP
;  (573 MB) is the one for a small stick - it fetches the brains instead of carrying them.
; ---------------------------------------------------------------------------

#define Payload "<DRIVE_D>\LYGO_CANON\2026-09-25_build-1.5.6_USB_CLAW\USB_CLAW"
#define ModelVault "<MODELS>"
#define SdVault "<MEDIA>\models\sd"
#define ModelPack "<USER>\AppData\Local\Temp\lygo_installer\modelpack"
#define Release "1.5.6"
#define AppTitle "LYGO Local Agent Console (USB CLAW, coder + picture inside)"

[Setup]
AppId={{B41D6E88-3F52-4C09-8A7D-9E13C7B45602}
AppName={#AppTitle}
AppVersion={#Release}
AppVerName={#AppTitle} {#Release}
AppPublisher=LYGO / Δ9Φ963 · steward Justin Helmer (Lightfather)
AppComments=The stick build with its weights inside: own engine, own Python, own picture engine, plus the qwen2.5-coder, the embedding model and the SD 1.5 image checkpoint - so a stick installed from this one draws a picture and writes code with nothing to fetch. One console, three systems: USB LOCAL / PC LOCAL / WEB PORTAL (API only). Sealed 1.5.6, which adds the music systems: the exact YuE-shaped lyric template is the box's default (one press restores it), the sheet is validated and converted without spending a render, and the agent can write a whole song into the box from a brief or at random from the engine's own tag vocabulary.
AppCopyright=LYGO Sovereign License v3.0 - no resale, no rebranding, no modified redistribution. Model weights stay their authors': Apache-2.0 (Alibaba Qwen2.5-Coder, Nomic AI) and CreativeML Open RAIL-M (SD 1.5) - both licence texts ship in models\.
VersionInfoVersion=1.5.6.0
VersionInfoDescription=LYGO Local Agent Console (USB CLAW) 1.5.6 FULL - sealed stick build + coder + embeddings + SD 1.5 checkpoint inside
DefaultDirName={src}\LYGO_BUILDER_KEY\lygo_llm_console
DefaultGroupName=LYGO Local Agent Console (USB CLAW)
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir=D:\
OutputBaseFilename=LYGO_LLM_CONSOLE_1.5.6_USB_SETUP_FULL
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#Payload}\LICENSE
InfoAfterFile=<EDRIVE>\lygo-protocol-stack\lygo_llm_console\installer\notes\INSTALL_NOTES_1.5.6_USB_FULL.txt
UninstallDisplayName={#AppTitle}
UsePreviousAppDir=yes
SetupLogging=yes
ShowLanguageDialog=no
MinVersion=10.0
; the kit + ~9 GB of weights
ExtraDiskSpaceRequired=15000000000

DiskSpanning=yes
DiskSliceSize=4294967295
SlicesPerDisk=1

[Files]
; EXCLUDES - posted PUBLIC: no operator chat archive, no bench logs, no operator media
Source: "{#Payload}\*"; DestDir: "{app}"; Excludes: "workspace\memory\conversations\*,workspace\notes\*,workspace\notes\*\*,workspace\notes\*\*,workspace\memory\conversations\*\*,workspace\memory\conversations\*\*\*,workspace\memory\*.jsonl,workspace\audio\*,workspace\audio\*\*,workspace\audio\*\*\*,workspace\images\*,workspace\images\*\*,workspace\images\*\*\*,workspace\uploads\*,workspace\uploads\*\*,workspace\uploads\*\*\*,workspace\rust\*,workspace\rust\*\*,workspace\rust\*\*\*,workspace\memory\*.md,workspace\*.wav,workspace\*.mp3,workspace\*.flac,workspace\*.pdb,workspace\*.png,workspace\*.jpg,workspace\*.jpeg,workspace\*.webp,workspace\*.html,workspace\*.jsonl,workspace\*.txt,workspace\*.log,workspace\*.db,workspace\*.sqlite,workspace\*.sqlite3"; Flags: ignoreversion recursesubdirs createallsubdirs

; the weights a stick can carry, uncompressed
Source: "{#ModelVault}\qwen2.5-coder-7b.gguf"; DestDir: "{app}\models"; Flags: ignoreversion nocompression
Source: "{#ModelVault}\nomic-embed-text-latest.gguf"; DestDir: "{app}\models"; Flags: ignoreversion nocompression

; the picture checkpoint + its licence (RAIL-M travels with the weights)
Source: "{#SdVault}\v1-5-pruned-emaonly.safetensors"; DestDir: "{app}\models\sd"; Flags: ignoreversion nocompression
Source: "{#ModelPack}\LICENSE-CREATIVEML-OPEN-RAIL-M.txt"; DestDir: "{app}\models\sd"; Flags: ignoreversion

; our own vault pointer + fetcher, into models\ - the stick's own scan root
Source: "{#ModelPack}\models.lock.json"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelPack}\fetch_models.py"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelPack}\LICENSE-APACHE-2.0.txt"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelVault}\manifest.json"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelVault}\sidecars\*"; DestDir: "{app}\models\sidecars"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\LYGO Agent Stick"; Filename: "{app}\LYGO_AGENT_STICK.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console (own engine, own Python, own picture engine, weights inside)"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\LYGO Local Agent Console (stick ports)"; Filename: "{app}\LYGO_LLM_CONSOLE.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console on the PC pair of ports"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Stop the console"; Filename: "{app}\LYGO_AGENT_STICK_STOP.bat"; WorkingDir: "{app}"; Comment: "Stop the stick console and its engine"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Fetch gemma4-12b"; Filename: "{app}\models\fetch_models.py"; WorkingDir: "{app}"; Comment: "Optional: the 12B brain, 7.3 GB, from our own vault (sha256 verified) - the coder and the checkpoint are already inside"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Fetch a faster checkpoint"; Filename: "{app}\tools\sd-cpu\FETCH_IMAGE_MODEL.bat"; WorkingDir: "{app}\tools\sd-cpu"; Comment: "Optional: SDXL Turbo (6.94 GB, much faster) - non-commercial licence, so you fetch it and the licence is shown"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Picture engine notes"; Filename: "{app}\tools\sd-cpu\README_IMAGE_ENGINE.txt"; Comment: "The picture engine that travels with this stick, and what is not bundled"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\README"; Filename: "{app}\README.md"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Uninstall"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LYGO Agent Stick"; Filename: "{app}\LYGO_AGENT_STICK.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console (works while the stick is plugged in)"

[Run]
; the sealed copy is read-only on purpose; the INSTALLED tree must be writeable (save/, logs)
Filename: "{cmd}"; Parameters: "/c attrib -R ""{app}\*"" /S /D"; Flags: runhidden; StatusMsg: "Making the installed build writeable ..."

; the stick carries its own Python, so the postinstall steps use THAT one, not the machine's
Filename: "{app}\python\python.exe"; Parameters: """{app}\src\install.py"""; WorkingDir: "{app}"; Description: "Provision this machine (seed soul / identity / memory - no steward data is copied)"; Flags: postinstall nowait skipifsilent

Filename: "{app}\LYGO_AGENT_STICK.bat"; Description: "Start the stick console now (coder, embeddings and the picture checkpoint are already in models\)"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec

Filename: "{app}\README.md"; Description: "Open the README"; Flags: postinstall shellexec unchecked skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    Log('LYGO USB CLAW FULL ' + '{#Release}' + ' installed (own engine, own Python, coder + embeddings + SD 1.5 checkpoint inside) to ' + ExpandConstant('{app}'));
end;
