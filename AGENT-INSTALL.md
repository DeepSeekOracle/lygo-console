# AGENT INSTALL — lygo-console 1.9.1

**Audience: the agent, not the human.** A human *can* install this by hand; an agent does it
properly, because the wiring is the hard part. Read this whole file, then execute it in order.

Target: Windows 10/11 x64, an agent with shell access and the ability to run an installer silently.

---

## 0. What you are installing

Three apps that share one loop:

| App | Role | After install |
|---|---|---|
| Console | local model + 40 tools | `http://127.0.0.1:9641` |
| Browser | reads / archives the web | API `:9337`, CDP `:9333` |
| Forge | board + dispatcher + skills | `http://127.0.0.1:8796` |

Ports in use when all three are up: **9641, 11441, 8081, 8082** (console + engine + bridges),
**9337, 9333** (browser), **8796** (forge). Nothing binds a public interface. Everything is loopback.

## 1. Preflight

```powershell
[Environment]::OSVersion.Version.Build            # >= 19041
(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB   # expect >= 8
Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM   # GPU optional
foreach ($p in 9641,11441,8081,8082,9337,9333,8796) {
  if (Get-NetTCPConnection -LocalPort $p -State Listen -EA SilentlyContinue) { "IN USE: $p" }
}
```

If a port is already listening, something is installed. Stop here and report instead of installing
over a live system.

## 2. Install the Console (the model ships inside)

The installer is 3.88 GB split across two assets plus a combine script:

```powershell
New-Item -ItemType Directory -Force C:\lygo-install | Out-Null
Set-Location C:\lygo-install
# download these three from the release into this folder:
#   LYGO_LLM_CONSOLE_1.9.1_PC_SETUP.exe.part1
#   LYGO_LLM_CONSOLE_1.9.1_PC_SETUP.exe.part2
#   COMBINE-INSTALLER.bat
.\COMBINE-INSTALLER.bat                    # rebuilds the single installer
Get-FileHash .\LYGO_LLM_CONSOLE_1.9.1_PC_SETUP.exe -Algorithm SHA256   # must match SHA256SUMS.txt
Start-Process .\LYGO_LLM_CONSOLE_1.9.1_PC_SETUP.exe -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait
```

Default install root: `C:\LYGO\LLM_CONSOLE`.

## 3. Verify the console — do not skip

```powershell
Start-Sleep 25                                        # engine loads the bundled model
$h = Invoke-RestMethod http://127.0.0.1:9641/api/health
$h.ok; $h.brain; $h.engine_present; $h.selected
```

Pass conditions: `ok = True`, `brain = ready`, `engine_present = True`, `selected` names a model.
`brain = missing` means the engine has not finished loading — wait and re-check before touching
anything else. Do **not** report success on an HTTP 200 alone; the JSON is the evidence.

## 4. Install the Browser

```powershell
Start-Process .\LYGO_BROWSER_1.9.1_SETUP.exe -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES' -Wait
# verifies:
Invoke-RestMethod http://127.0.0.1:9337/api/health
```

The installer registers the browser as a **candidate**. Windows will not let any process set the
default browser — do not attempt to force `UserChoice`, and do not report it as a failure.

## 5. Install the Forge

```powershell
Expand-Archive .\LYGO_FORGE_1.9.1_PUBLIC_BETA.zip -DestinationPath C:\LYGO\FORGE
Start-Process C:\LYGO\FORGE\LYGOForge.exe
Start-Sleep 8
Invoke-RestMethod http://127.0.0.1:8796/api/status
```

## 6. Finish the wiring — this is the actual job

The packaged build ships with the dev-specific wiring removed. Your task is to restore it **on this
machine**, then prove each step:

1. **Model route.** Point the Forge's dispatcher at the console's engine:
   `http://127.0.0.1:11441/v1`. Confirm `local_ready: true` in `/api/status`.
2. **Browser link.** Give the console and the Forge the browser's endpoints (`9337` API, `9333` CDP)
   so agents can open and read pages rather than fetching raw HTML.
3. **Dispatcher.** Create one project folder you own, put one task on the board, and run it end to
   end. A dispatcher that has never dispatched is unproven.
4. **Networks and swarms.** Only after 1–3 pass. Configure loopback-only, never a public bind.
5. **Secrets.** Provider keys go in local config, never in a repo, never in a log, never in a report.
   Redact as `[REDACTED]` if you must quote anything.

## 7. Report

```
CONSOLE   ok=<> brain=<> model=<> :9641
BROWSER   api=<> cdp=<> :9337/:9333
FORGE     local_ready=<> port=8796
WIRING    model-route=<pass/fail> browser-link=<pass/fail> dispatch=<pass/fail> swarm=<not attempted>
OWED      what you could not verify, and why
```

Report what actually answered, not what you configured. "Configured" is not evidence.

## Guardrails

- Nothing here publishes, posts or pushes. Do not add a remote, do not upload.
- No force-kills: closing the browser by killing it loses cookies and the vault session.
- If a step fails twice, stop and report; do not improvise a fix on a live install.
- The human may run this by hand — that is supported — but the wiring in §6 is where a human
  usually stops, and where you are expected to finish.
