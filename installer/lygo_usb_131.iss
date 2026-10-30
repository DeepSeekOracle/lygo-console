; ---------------------------------------------------------------------------
;  LYGO Local Agent Console - USB CLAW / stick build - installer, 1.3.1
;
;  Payload : the SEALED 1.3.1 stick build
;            <DRIVE_D>\LYGO_CANON\2026-09-22_build-1.3.1_USB_CLAW\USB_CLAW
;  Engine  : bundled (engine\llama-server.exe + impl DLLs + backends)
;  Python  : bundled (python\ inside the kit) - the stick needs no Python install
;  Models  : NOT inside this installer (a stick has no room for 7-12 GB of weights, and this one
;            has ~3 GB free). models\fetch_models.py pulls them from our own vault, sha256 verified,
;            into <install>\models - which config\console.json scans. It refuses any URL that is
;            not ours. On the studio PC the vault is already found at <MODELS>.
;
;  Default install target is built from where the installer itself runs:
;  run it from the stick (E:\) and it installs to <USB_ROOT>\LYGO_BUILDER_KEY\lygo_llm_console.
; ---------------------------------------------------------------------------

#define Payload "<DRIVE_D>\LYGO_CANON\2026-09-22_build-1.3.1_USB_CLAW\USB_CLAW"
#define ModelPack "<USER>\AppData\Local\Temp\lygo_installer\modelpack"
#define Release "1.3.1"
#define AppTitle "LYGO Local Agent Console (USB CLAW)"

[Setup]
AppId={{B41D6E88-3F52-4C09-8A7D-9E13C7B45602}
AppName={#AppTitle}
AppVersion={#Release}
AppVerName={#AppTitle} {#Release}
AppPublisher=LYGO / Δ9Φ963 · steward Justin Helmer (Lightfather)
AppComments=The stick build: own engine, own Python, own model CAS. One console, three systems: USB LOCAL / PC LOCAL / WEB PORTAL (API only). Sealed 1.3.1 - every turn records what it generated vs what it showed.
AppCopyright=LYGO Sovereign License v3.0 - no resale, no rebranding, no modified redistribution.
VersionInfoVersion=1.3.1.0
VersionInfoDescription=LYGO Local Agent Console (USB CLAW) 1.3.1 - sealed stick build, own engine and Python
DefaultDirName={src}\LYGO_BUILDER_KEY\lygo_llm_console
DefaultGroupName=LYGO Local Agent Console (USB CLAW)
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir=E:\
OutputBaseFilename=LYGO_LLM_CONSOLE_1.3.1_USB_SETUP
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#Payload}\LICENSE
InfoAfterFile=<USER>\AppData\Local\Temp\lygo_installer\INSTALL_NOTES_1.3.1_USB.txt
UninstallDisplayName={#AppTitle}
UsePreviousAppDir=yes
SetupLogging=yes
ShowLanguageDialog=no
MinVersion=10.0
ExtraDiskSpaceRequired=8000000000

[Files]
; EXCLUDES - posted PUBLIC: no operator chat archive, no bench logs (see lygo_pc_131.iss for the why)
Source: "{#Payload}\*"; DestDir: "{app}"; Excludes: "workspace\memory\conversations\*,workspace\memory\conversations\*\*,workspace\memory\conversations\*\*\*,workspace\memory\*.jsonl"; Flags: ignoreversion recursesubdirs createallsubdirs

; our own vault pointer + fetcher, into models\ - the stick's own scan root
Source: "{#ModelPack}\models.lock.json"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelPack}\fetch_models.py"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelPack}\LICENSE-APACHE-2.0.txt"; DestDir: "{app}\models"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\LYGO Agent Stick"; Filename: "{app}\LYGO_AGENT_STICK.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console (own engine, own Python, own model CAS)"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\LYGO Local Agent Console (stick ports)"; Filename: "{app}\LYGO_LLM_CONSOLE.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console on the PC pair of ports"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Stop the console"; Filename: "{app}\LYGO_AGENT_STICK_STOP.bat"; WorkingDir: "{app}"; Comment: "Stop the stick console and its engine"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Fetch the models"; Filename: "{app}\models\fetch_models.py"; WorkingDir: "{app}"; Comment: "Download the brains from our own vault (sha256 verified)"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\README"; Filename: "{app}\README.md"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Uninstall"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LYGO Agent Stick"; Filename: "{app}\LYGO_AGENT_STICK.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console (works while the stick is plugged in)"

[Run]
Filename: "{cmd}"; Parameters: "/c attrib -R ""{app}\*"" /S /D"; Flags: runhidden; StatusMsg: "Making the installed build writeable ..."

Filename: "{cmd}"; Parameters: "/k ""{app}\python\python.exe"" ""{app}\src\install.py"""; WorkingDir: "{app}"; Description: "Provision this stick (seed soul / identity / memory - no steward data is copied)"; Flags: postinstall nowait skipifsilent

Filename: "{cmd}"; Parameters: "/k ""{app}\python\python.exe"" ""{app}\models\fetch_models.py"" --profile coder --yes --dest ""{app}\models"""; WorkingDir: "{app}"; Description: "Fetch the coder now from our own vault (qwen2.5-coder 7B, 4.7 GB, sha256 verified)"; Flags: postinstall nowait skipifsilent unchecked

Filename: "{cmd}"; Parameters: "/k ""{app}\python\python.exe"" ""{app}\models\fetch_models.py"" --profile basic --yes --dest ""{app}\models"""; WorkingDir: "{app}"; Description: "Fetch the basic set now (gemma4-12b + projector + embeddings, 7.3 GB - needs the room)"; Flags: postinstall nowait skipifsilent unchecked

Filename: "{app}\LYGO_AGENT_STICK.bat"; Description: "Start the stick console now"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec

Filename: "{app}\README.md"; Description: "Open the README"; Flags: postinstall shellexec unchecked skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    Log('LYGO USB CLAW ' + '{#Release}' + ' installed from the sealed 1.3.1 build to ' + ExpandConstant('{app}'));
end;
