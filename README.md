# LYGO LLM Console — v1.9.1 Public Beta

Sovereign local LLM runtime for Windows. Runs a GGUF model on your own machine behind a local HTTP
console, with tools, media limbs and a role-labelled portal — no account, no cloud round-trip, no
telemetry.

**This tag is the first Public Beta of the real working system.** It is what the steward runs day
to day. It is not hardened for hostile input (see [`DISCLAIMER.md`](DISCLAIMER.md)).

- **Prebuilt installer:** LYGO Skill Hub → Console 1.9.1 (no build required)
- **From source:** [`INSTALL.md`](INSTALL.md)
- **License:** LYGO Sovereign License v3.0 — **source-available, NOT open source**. No resale, no
  white-label, no publishing modified copies. Read [`LICENSE`](LICENSE), [`LICENSING.md`](LICENSING.md),
  [`TRADEMARKS.md`](TRADEMARKS.md), [`NOTICE`](NOTICE).

---

## What it does

One codebase, three role-labelled surfaces. `src/surface.py` is the source of truth for the labels:

| Surface | Where it runs | Mode |
|---|---|---|
| PC LOCAL | desktop kit | full console, local model |
| USB LOCAL / USB API | removable stick | portable, model optional |
| WEB PORTAL | a web page | **API only** — no UI from this repo |

Core pieces:

- **`src/server.py`** — the console HTTP service (port **9641**).
- **`engine/`** — llama.cpp binaries it drives (engine **11441**). *Not shipped in this repo.*
- **`models/`** — your GGUF files. *Not shipped in this repo.*
- **`src/media_tools.py`, `music_tools.py`, `video_harness.py`, `render_swap.py`** — media limbs
  (image, music, video) that park and wake the engine so an 8 GB card is not asked to do two
  heavy jobs at once.
- **`prompts/`, `skills/`, `portal/`, `web_portal/`, `verifiers/`, `scripts/`, `tools/`** — prompt
  packs, skills, the local portal, verifiers and maintenance scripts.
- **`installer/`** — Inno Setup sources for the PC installer.

## Requirements

- Windows 10/11 x64.
- Python **3.13** (`C:\Python313\python.exe` is the steward's interpreter; any 3.13 works).
- A llama.cpp build for your backend in `engine/` (Vulkan, CUDA or CPU).
- A GGUF model in `models/`. The steward's anchor is
  `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (2,382 MB), run at 64K context on an 8 GB card.
- Optional: NVIDIA bridge on 8082, general bridge on 8081, if you want API delegation.

## Quick start

```powershell
# 0. get the code
git clone <this repo> lygo_llm_console ; cd lygo_llm_console

# 1. put the engine and a model in place (neither ships here)
#    engine\  <- llama-server.exe + dlls
#    models\  <- your .gguf

# 2. start it
.\LYGO_LLM_CONSOLE.bat

# 3. prove it
Invoke-RestMethod http://127.0.0.1:9641/api/health
```

`LYGO_LLM_CONSOLE_STOP.bat` stops it. Full detail, including the installer build, is in
[`INSTALL.md`](INSTALL.md).

## What is deliberately not in this repository

These are runtime state, weights or third-party payloads. They are excluded by design — a clone is
source, not a working install:

| Excluded | Why |
|---|---|
| `data/` | 1.1 GB of runtime records and generated state |
| `save/`, `workspace/` | session and workspace state |
| `models/` | GGUF weights — licence and size vary per model |
| `engine/` | 2.3 GB of llama.cpp binaries; download or build your own |
| `utilities/filmcraft/` | 183 MB third-party payload |
| `config/api.json`, `config/local.json`, `config/admin.json` | **credentials and machine paths** |
| `*.bak-*` | timestamped backups |

`config/local.json.example` is included — copy it to `config/local.json` and fill in your own
values. Never commit the result.

## Verify a build

```powershell
# HTTP boot + health (no model needed)
Invoke-RestMethod http://127.0.0.1:9641/api/health

# test suite
C:\Python313\python.exe -m pytest tests -q
```

`/api/health` returning `brain=missing` is normal until `engine/` and a GGUF are both present. A
console that answers on 9641 with no brain is a working console with no model, not a broken build.

## Known limits — stated so nobody "fixes" them by guessing

- **One heavy engine at a time on an 8 GB card.** The media limbs park the engine rather than
  overlap it.
- **No model, no brain.** `brain=missing` is the honest state, not an error to suppress.
- **Nothing auto-publishes.** No git push, no social post, no chart write without a human.
- **Not multi-tenant.** It assumes one trusted user on one trusted machine.
- **Beta interfaces move.** Config keys and endpoints can change between betas.

## Documentation in this repo

| File | What it covers |
|---|---|
| [`INSTALL.md`](INSTALL.md) | install, build from source, installer, verification |
| [`WHITEPAPER.md`](WHITEPAPER.md) | the design and the why, at length |
| [`HARDENING.md`](HARDENING.md) | what was hardened and what was not |
| [`MODELS.md`](MODELS.md) | model choices and context budgets |
| [`STANDALONE.md`](STANDALONE.md) | running without the rest of the stack |
| [`CHANGELOG.md`](CHANGELOG.md) | what 1.9.1 changed |
| [`DISCLAIMER.md`](DISCLAIMER.md) | what you accept by running this |
| [`SECURITY.md`](SECURITY.md) | reporting, secret rules, default posture |

## License and brand

LYGO Sovereign License v3.0 (`Δ9Φ963-LICENSE-v3.0`) — **not** MIT, **not** OSI open source.

You may use it and build on it. You may **not** sell it, rebrand it, white-label it, publish
modified copies, or strip attribution, seals or signatures. Brand policy: [`TRADEMARKS.md`](TRADEMARKS.md).
Third-party credits: [`NOTICE`](NOTICE).

**Steward:** Justin Helmer — Lightfather · Excavationpro · DeepSeekOracle
