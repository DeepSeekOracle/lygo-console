; ---------------------------------------------------------------------------
;  LYGO Local Agent Console - USB CLAW  -  full installer
;
;  Payload : the SEALED V1 build for the stick, copied out of the vault:
;            <DRIVE_D>\LYGO_CANON\2026-09-20_build-1.1.0_USB_CLAW-WORKING\USB_CLAW
;  Engine  : bundled (engine\llama-server.exe + impl DLLs + backends)
;  Python  : bundled (python\ inside the kit) - the stick needs no Python install
;
;  Default install target is built from where the installer itself is run:
;  run it from the stick (E:\) and it installs to <USB_ROOT>\LYGO_BUILDER_KEY\lygo_llm_console.
; ---------------------------------------------------------------------------

#define Payload "<DRIVE_D>\LYGO_CANON\2026-09-20_build-1.1.0_USB_CLAW-WORKING\USB_CLAW"
#define Release "1.1.0"
#define AppTitle "LYGO Local Agent Console (USB CLAW)"

[Setup]
AppId={{B41D6E88-3F52-4C09-8A7D-9E13C7B45602}
AppName={#AppTitle}
AppVersion={#Release}
AppVerName={#AppTitle} {#Release}
AppPublisher=LYGO / Δ9Φ963 · steward Justin Helmer (Lightfather)
AppComments=The stick build: own engine, own Python, own model CAS. One console, three systems: USB LOCAL / PC LOCAL / WEB PORTAL (API only).
AppCopyright=LYGO Sovereign License v3.0 - no resale, no rebranding, no modified redistribution.
VersionInfoVersion=1.1.0.0
VersionInfoDescription=LYGO Local Agent Console (USB CLAW) installer, built from the sealed V1 build
DefaultDirName={src}\LYGO_BUILDER_KEY\lygo_llm_console
DefaultGroupName=LYGO Local Agent Console (USB CLAW)
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir=E:\
OutputBaseFilename=LYGO_LLM_CONSOLE_V1_USB_SETUP
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#Payload}\LICENSE
InfoAfterFile=<USER>\AppData\Local\Temp\lygo_installer\INSTALL_NOTES_USB.txt
UninstallDisplayName={#AppTitle}
UsePreviousAppDir=yes
SetupLogging=yes
ShowLanguageDialog=no
MinVersion=10.0

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Our own vault pointer + fetcher, into models\ - the stick's own scan root.
; Weights are NOT inside this installer (a 7 GB model does not belong in a
; stick installer): fetch_models.py pulls them from the steward's own store,
; SHA-256 verified, and refuses any URL that is not ours.
Source: "<USER>\AppData\Local\Temp\lygo_installer\modelpack\models.lock.json"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "<USER>\AppData\Local\Temp\lygo_installer\modelpack\fetch_models.py"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "<USER>\AppData\Local\Temp\lygo_installer\modelpack\LICENSE-APACHE-2.0.txt"; DestDir: "{app}\models"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\LYGO Agent Stick"; Filename: "{app}\LYGO_AGENT_STICK.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console (own engine, own Python, own model CAS)"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\LYGO Local Agent Console (stick ports)"; Filename: "{app}\LYGO_LLM_CONSOLE.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console on the PC pair of ports"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Stop the console"; Filename: "{app}\LYGO_AGENT_STICK_STOP.bat"; WorkingDir: "{app}"; Comment: "Stop the stick console and its engine"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\README"; Filename: "{app}\README.md"
Name: "{autoprograms}\LYGO Local Agent Console (USB CLAW)\Uninstall"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LYGO Agent Stick"; Filename: "{app}\LYGO_AGENT_STICK.bat"; WorkingDir: "{app}"; Comment: "Boot the stick console (works while the stick is plugged in)"

[Run]
Filename: "{cmd}"; Parameters: "/c attrib -R ""{app}\*"" /S /D"; Flags: runhidden; StatusMsg: "Making the installed build writeable ..."

Filename: "{cmd}"; Parameters: "/k ""{app}\python\python.exe"" ""{app}\src\install.py"""; WorkingDir: "{app}"; Description: "Provision this stick (seed soul / identity / memory - no steward data is copied)"; Flags: postinstall nowait skipifsilent

Filename: "{app}\LYGO_AGENT_STICK.bat"; Description: "Start the stick console now"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec

Filename: "{cmd}"; Parameters: "/k ""{app}\python\python.exe"" ""{app}\models\fetch_models.py"" --profile basic --yes --dest ""{app}\models"""; WorkingDir: "{app}"; Description: "Download the basic models now from our own vault (gemma4-12b + projector + embeddings, 7.3 GB, SHA-256 verified)"; Flags: postinstall nowait skipifsilent unchecked

Filename: "{app}\README.md"; Description: "Open the README"; Flags: postinstall shellexec unchecked skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    Log('LYGO USB CLAW ' + '{#Release}' + ' installed from the sealed V1 build to ' + ExpandConstant('{app}'));
end;
