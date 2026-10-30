; ---------------------------------------------------------------------------
;  LYGO Local Agent Console - USB CLAW / stick build - installer, 1.7.0
;
;  Payload : the stick tree at 1.7.0, staged and hashed as
;            <DRIVE_D>\LYGO_CANON\2026-10-18_build-1.7.0_USB_CLAW\USB_CLAW
;            (config\admin.json and config\local.json removed: the operator's map does not travel)
;  Engine  : bundled (engine\llama-server.exe + impl DLLs + backends)
;  Pictures: bundled - tools\sd-cpu\ is the CPU build of stable-diffusion.cpp (45 MB, 20 files, no
;            CUDA, no card, no admin). media_root() falls back to the kit root, so a stick install
;            finds its own picture engine on whatever machine it is plugged into. The CHECKPOINT is
;            not inside this one (a stick has no room): tools\sd-cpu\FETCH_IMAGE_MODEL.bat fetches it
;            once - SDXL Turbo 6.94 GB for speed, or SD 1.5 4.27 GB, which is the one whose licence
;            (CreativeML Open RAIL-M) allows redistribution and is therefore the one carried in
;            LYGO_LLM_CONSOLE_1.7.0_USB_SETUP_FULL.
;  Python  : bundled (python\ inside the kit) - the stick needs no Python install
;  Models  : NOT inside this installer (a stick has no room for 7-12 GB of weights, and this one
;            has ~3 GB free). models\fetch_models.py pulls them from our own vault, sha256 verified,
;            into <install>\models - which config\console.json scans. It refuses any URL that is
;            not ours. On the studio PC the vault is already found at <MODELS>.
;
;  Default install target is built from where the installer itself runs:
;  run it from the stick (E:\) and it installs to <USB_ROOT>\LYGO_BUILDER_KEY\lygo_llm_console.
; ---------------------------------------------------------------------------

#define Payload "<DRIVE_D>\LYGO_CANON\2026-10-18_build-1.7.0_USB_CLAW\USB_CLAW"
#define Release "1.7.0"
#define AppTitle "LYGO Local Agent Console (USB CLAW)"

[Setup]
AppId={{B41D6E88-3F52-4C09-8A7D-9E13C7B45602}
AppName={#AppTitle}
AppVersion={#Release}
AppVerName={#AppTitle} {#Release}
AppPublisher=LYGO / Δ9Φ963 · steward Justin Helmer (Lightfather)
AppComments=The console on a stick: its own Python, engine and picture engine, so a host needs nothing installed. Sealed 1.7.0 build. 1.7.0 adds the download pair (adapters bound to separate local addresses, mirrors inside one block queue), FilmCraft (film is the 41st core tool), the Internet Archive pair, and the tasking/context system, with the core cap moved to 44 slots by measurement. The operator's admin map does not travel: configdmin.json is excluded from this installer.
AppCopyright=LYGO Sovereign License v3.0 - no resale, no rebranding, no modified redistribution.
VersionInfoVersion=1.7.0.0
VersionInfoDescription=LYGO Local Agent Console (USB CLAW) 1.7.0 - portable sealed build, own Python + engine + picture engine, weights fetched from the model vault
DefaultDirName={src}\LYGO_BUILDER_KEY\lygo_llm_console
DefaultGroupName=LYGO Local Agent Console (USB CLAW)
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir=D:\
OutputBaseFilename=LYGO_LLM_CONSOLE_1.7.0_USB_SETUP
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#Payload}\LICENSE
InfoAfterFile=<EDRIVE>\lygo-protocol-stack\lygo_llm_console\installer\notes\INSTALL_NOTES_1.7.0_USB.txt
UninstallDisplayName={#AppTitle}
UsePreviousAppDir=yes
SetupLogging=yes
ShowLanguageDialog=no
MinVersion=10.0
; the kit itself, plus room for one picture checkpoint if you fetch it (7 GB) - the console boots without it
ExtraDiskSpaceRequired=15000000000

[Files]
; EXCLUDES - posted PUBLIC: no operator chat archive, no bench logs (see lygo_pc_132.iss for the why)
Source: "{#Payload}\*"; DestDir: "{app}"; Excludes: "config\admin.json,config\local.json,save\*,data\*,workspace\memory\conversations\*,workspace\notes\*,workspace\notes\*\*,workspace\notes\*\*,workspace\memory\conversations\*\*,workspace\memory\conversations\*\*\*,workspace\memory\*.jsonl,workspace\audio\*,workspace\audio\*\*,workspace\audio\*\*\*,workspace\images\*,workspace\images\*\*,workspace\images\*\*\*,workspace\uploads\*,workspace\uploads\*\*,workspace\uploads\*\*\*,workspace\rust\*,workspace\rust\*\*,workspace\rust\*\*\*,workspace\memory\*.md,workspace\*.wav,workspace\*.mp3,workspace\*.flac,workspace\*.pdb,workspace\*.png,workspace\*.jpg,workspace\*.jpeg,workspace\*.webp,workspace\*.html,workspace\*.jsonl,workspace\*.txt,workspace\*.log,workspace\*.db,workspace\*.sqlite,workspace\*.sqlite3"; Flags: ignoreversion recursesubdirs createallsubdirs

; our own vault pointer + fetcher, into models\ - the stick's own scan root

[Icons]
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\LYGO Agent Stick"; Filename: "{app}\LYGO_AGENT_STICK.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console (own engine, own Python, own model CAS, own picture engine)"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\LYGO Local Agent Console (stick ports)"; Filename: "{app}\LYGO_LLM_CONSOLE.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console on the PC pair of ports"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Stop the console"; Filename: "{app}\LYGO_AGENT_STICK_STOP.bat"; WorkingDir: "{app}"; Comment: "Stop the stick console and its engine"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Fetch the brains"; Filename: "{app}\models\fetch_models.py"; WorkingDir: "{app}"; Comment: "Download the brains from our own vault (sha256 verified)"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Fetch the picture checkpoint"; Filename: "{app}\tools\sd-cpu\FETCH_IMAGE_MODEL.bat"; WorkingDir: "{app}\tools\sd-cpu"; Comment: "One file, fetched once from its author, then image_generate works offline"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Picture engine notes"; Filename: "{app}\tools\sd-cpu\README_IMAGE_ENGINE.txt"; Comment: "The picture engine that travels with this stick, and what is not bundled"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\README"; Filename: "{app}\README.md"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Uninstall"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LYGO Agent Stick"; Filename: "{app}\LYGO_AGENT_STICK.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console (works while the stick is plugged in)"

[Run]
; the sealed copy is read-only on purpose; the INSTALLED tree must be writeable (save/, logs)
Filename: "{cmd}"; Parameters: "/c attrib -R ""{app}\*"" /S /D"; Flags: runhidden; StatusMsg: "Making the installed build writeable ..."

; the stick carries its own Python, so the postinstall steps use THAT one, not the machine's
Filename: "{app}\python\python.exe"; Parameters: """{app}\src\install.py"""; WorkingDir: "{app}"; Description: "Provision this machine (seed soul / identity / memory - no steward data is copied)"; Flags: postinstall nowait skipifsilent

Filename: "{app}\python\python.exe"; Parameters: """{app}\models\fetch_models.py"" --profile basic --yes --dest ""{app}\models"""; WorkingDir: "{app}"; Description: "Fetch the brains now from our own vault (gemma4-12b + projector, 7.3 GB, sha256 verified)"; Flags: postinstall nowait skipifsilent unchecked

Filename: "{app}\tools\sd-cpu\FETCH_IMAGE_MODEL.bat"; Description: "Fetch the picture checkpoint now (SDXL Turbo 6.94 GB, or run get_model.ps1 -Model sd15 for 4.27 GB)"; WorkingDir: "{app}\tools\sd-cpu"; Flags: postinstall nowait skipifsilent unchecked

Filename: "{app}\LYGO_AGENT_STICK.bat"; Description: "Start the stick console now"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec

Filename: "{app}\README.md"; Description: "Open the README"; Flags: postinstall shellexec unchecked skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    Log('LYGO USB CLAW ' + '{#Release}' + ' installed from the sealed 1.7.0 stick build to ' + ExpandConstant('{app}') +
        ' (own engine, own Python, picture engine inside; the checkpoint is one fetch)');
end;
