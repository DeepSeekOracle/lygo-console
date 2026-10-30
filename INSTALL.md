# Install, build and verify — LYGO LLM Console 1.9.1

Two routes: **install the prebuilt package** (fastest) or **build from this repository** (you get
the source you can audit and change).

---

## Route A — prebuilt installer

1. Get the 1.9.1 package from the **LYGO Skill Hub** (Console 1.9.1). The package is the PC build;
   the portable stick build is a separate artefact.
2. Verify the download hash against the one published next to it before running anything.
3. Run the installer as an ordinary user. It is an Inno Setup package
   (`LYGO_LLM_CONSOLE_1.9.1_PC_SETUP`), not a system-wide service.
4. Start it from the Start-menu entry or `LYGO_LLM_CONSOLE.bat`.
5. Prove it: `Invoke-RestMethod http://127.0.0.1:9641/api/health` → HTTP 200 with a JSON body.

The installer ships the application, not a model. You choose and supply the GGUF.

---

## Route B — from source

### Prerequisites

| Need | Detail |
|---|---|
| OS | Windows 10/11 x64 |
| Python | **3.13**. `C:\Python313\python.exe -V` must print 3.13.x |
| Engine | a llama.cpp build placed in `engine\` (Vulkan is what the steward runs) |
| Model | a `.gguf` in `models\` |
| Optional | Inno Setup 6 at `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe` to build the installer |
| Optional | PyInstaller 6.22.3 to build tool executables |

Python dependencies: install from the requirements file if present, otherwise the console will
report the missing module by name on first start — read the error, install that module, restart.

### Steps

```powershell
git clone <this repo> lygo_llm_console
cd lygo_llm_console

# engine and weights are not in git
#   engine\   <- llama-server.exe + ggml/llama dlls
#   models\   <- your .gguf

# configuration: start from the example, never from a real one
Copy-Item config\local.json.example config\local.json
notepad config\local.json          # absolute paths for THIS machine

# run
.\LYGO_LLM_CONSOLE.bat
```

The launcher resolves its own directory, so the clone can live anywhere — but any remaining
`<CONSOLE_DIR>` placeholders in `.bat` files or `config\*.json` must be replaced with real paths
for your machine. Placeholders exist because the public tree was scrubbed of the builder's layout;
see [`SECURITY.md`](SECURITY.md).

### Verify

```powershell
# 1. service is up
Invoke-RestMethod http://127.0.0.1:9641/api/health

# 2. brain state - "missing" means no engine/model yet, not a broken build
(Invoke-RestMethod http://127.0.0.1:9641/api/health).brain

# 3. tests
C:\Python313\python.exe -m pytest tests -q
```

### Build the PC installer

```powershell
# 1. stage a clean tree and point the .iss at it
#    installer\lygo_pc_191.iss  (Release 1.9.1, output LYGO_LLM_CONSOLE_1.9.1_PC_SETUP)
# 2. compile
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer\lygo_pc_191.iss
```

Check the `[Files]` `Source:` root in the `.iss` before compiling — it must point at your staged
tree, not at a builder path.

### Ports you must know

| Port | Service |
|---|---|
| 9641 | console HTTP |
| 11441 | local engine (llama.cpp server) |
| 8081 | general bridge |
| 8082 | NVIDIA bridge |

A port already in use is the most common start failure. `tools\resolve_ports.py` exists for that
reason; check it before assuming the code is broken.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Port 9641 in use | another console/daemon still running | `LYGO_LLM_CONSOLE_STOP.bat`, then check with `Get-NetTCPConnection -LocalPort 9641` |
| `brain=missing` | no engine, no GGUF, or wrong path in `local.json` | put the binary in `engine\`, the model in `models\`, re-check paths |
| Engine exports but no tokens | model larger than free VRAM | smaller quant, or lower context; one engine at a time on 8 GB |
| Windows popup storm | a bare `subprocess` call spawning a console window | the stack patches `Popen` with `CREATE_NO_WINDOW` via `win_quiet.py`; do not reintroduce a bare `subprocess.run` |
| Installer compiles but app won't start | `[Files]` source wrong, or `config/` staged with the builder's paths | re-check the staged tree and `local.json` |

## Uninstall

Stop the console, delete the install directory, and remove any desktop/Start-menu shortcut. No
service, no registry keys beyond the uninstaller entry, no scheduled tasks are created by this repo.
Models and `data/` live inside the install directory — keep or delete them deliberately.
