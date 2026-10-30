; ---------------------------------------------------------------------------
;  LYGO Local Agent Console - USB CLAW - installer, 1.3.1, CODER INSIDE
;
;  The stick build plus the code brain in the installer itself:
;  qwen2.5-coder-7b.gguf (4.68 GB) rides in the .bin slices beside SETUP.EXE
;  (DiskSpanning - Windows cannot load a single Setup.exe larger than ~4.2 GB),
;  and lands in <install>\models, which config\console.json scans.
;
;  gemma4-12b (7.4 GB) is NOT here: with the coder inside, the two together would be 12 GB of
;  installer for a stick. Boot, then either fetch it from our own vault with models\fetch_models.py
;  --profile basic, or use LYGO_LLM_CONSOLE_1.3.1_PC_SETUP_FULL on the machine.
;
;  Output goes to D:\ by default: the stick itself has ~3 GB free and this build is ~5.2 GB.
;  Copy SETUP.EXE and its .bin beside it onto the stick if you want it there afterwards.
; ---------------------------------------------------------------------------

#define Payload "<DRIVE_D>\LYGO_CANON\2026-09-22_build-1.3.1_USB_CLAW\USB_CLAW"
#define ModelVault "<MODELS>"
#define ModelPack "<USER>\AppData\Local\Temp\lygo_installer\modelpack"
#define Release "1.3.1"
#define AppTitle "LYGO Local Agent Console (USB CLAW, coder included)"

[Setup]
AppId={{B41D6E88-3F52-4C09-8A7D-9E13C7B45602}
AppName={#AppTitle}
AppVersion={#Release}
AppVerName={#AppTitle} {#Release}
AppPublisher=LYGO / Δ9Φ963 · steward Justin Helmer (Lightfather)
AppComments=The stick build with its code brain inside: own engine, own Python, own model CAS, and the qwen2.5-coder 7B in the installer. Sealed 1.3.1 - every turn records what it generated vs what it showed.
AppCopyright=LYGO Sovereign License v3.0 - no resale, no rebranding, no modified redistribution. Model weights: Apache-2.0 (Alibaba Qwen2.5-Coder); licence text ships in models\.
VersionInfoVersion=1.3.1.0
VersionInfoDescription=LYGO Local Agent Console (USB CLAW) 1.3.1 with the coder inside
DefaultDirName={src}\LYGO_BUILDER_KEY\lygo_llm_console
DefaultGroupName=LYGO Local Agent Console (USB CLAW)
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir=D:\
OutputBaseFilename=LYGO_LLM_CONSOLE_1.3.1_USB_SETUP_CODER
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#Payload}\LICENSE
InfoAfterFile=<USER>\AppData\Local\Temp\lygo_installer\INSTALL_NOTES_1.3.1_USB_CODER.txt
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
; EXCLUDES - posted PUBLIC: no operator chat archive, no bench logs (see lygo_pc_131.iss for the why)
Source: "{#Payload}\*"; DestDir: "{app}"; Excludes: "workspace\memory\conversations\*,workspace\memory\conversations\*\*,workspace\memory\conversations\*\*\*,workspace\memory\*.jsonl"; Flags: ignoreversion recursesubdirs createallsubdirs

Source: "{#ModelVault}\qwen2.5-coder-7b.gguf"; DestDir: "{app}\models"; Flags: ignoreversion nocompression

Source: "{#ModelPack}\models.lock.json"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelPack}\fetch_models.py"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelPack}\LICENSE-APACHE-2.0.txt"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "{#ModelVault}\manifest.json"; DestDir: "{app}\models"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\LYGO Agent Stick"; Filename: "{app}\LYGO_AGENT_STICK.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console - the coder is already in models\"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\LYGO Local Agent Console (stick ports)"; Filename: "{app}\LYGO_LLM_CONSOLE.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console on the PC pair of ports"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Stop the console"; Filename: "{app}\LYGO_AGENT_STICK_STOP.bat"; WorkingDir: "{app}"; Comment: "Stop the stick console and its engine"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Fetch the rest"; Filename: "{app}\models\fetch_models.py"; WorkingDir: "{app}"; Comment: "Download gemma4 + the projector + embeddings from our own vault"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\README"; Filename: "{app}\README.md"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Uninstall"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LYGO Agent Stick"; Filename: "{app}\LYGO_AGENT_STICK.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console (coder included)"

[Run]
Filename: "{cmd}"; Parameters: "/c attrib -R ""{app}\*"" /S /D"; Flags: runhidden; StatusMsg: "Making the installed build writeable ..."

Filename: "{cmd}"; Parameters: "/k ""{app}\python\python.exe"" ""{app}\src\install.py"""; WorkingDir: "{app}"; Description: "Provision this stick (seed soul / identity / memory - no steward data is copied)"; Flags: postinstall nowait skipifsilent

Filename: "{app}\LYGO_AGENT_STICK.bat"; Description: "Start the stick console now (the coder is already in models\)"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec

Filename: "{cmd}"; Parameters: "/k ""{app}\python\python.exe"" ""{app}\models\fetch_models.py"" --profile basic --yes --dest ""{app}\models"""; WorkingDir: "{app}"; Description: "Also fetch gemma4-12b + its projector + embeddings (7.3 GB, from our own vault)"; Flags: postinstall nowait skipifsilent unchecked

Filename: "{app}\README.md"; Description: "Open the README"; Flags: postinstall shellexec unchecked skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    Log('LYGO USB CLAW (coder inside) ' + '{#Release}' + ' installed to ' + ExpandConstant('{app}'));
end;
