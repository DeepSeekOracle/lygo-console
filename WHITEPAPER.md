# LYGO LLM Local Console — As-Built Whitepaper (v2)

**Build `v1.1-20260917api2`** · signature **`Δ9Φ963-LYGO-LLM-CONSOLE-v1`** · status **BETA 1 — stable working prototype**
Local admin studio (`http://127.0.0.1:9641`) + public web portal (`https://chatagent.ca/portal/`) + public inference gateway (`:9642`)
USB stick deployment (**LYGO CLAW**) — self-contained console `:9651` + stick-brain engine `:11451`, started by `LYGO_AGENT_STICK.bat`, needing nothing from the host but the python already on the machine (see §2.3 and §10.8)

---

## 0. Document control

| Field | Value |
|---|---|
| Title | LYGO LLM Local Console — As-Built Whitepaper |
| Version | **v2 (as-built)** |
| Build covered | `v1.1-20260917api2` |
| Kit signature | `Δ9Φ963-LYGO-LLM-CONSOLE-v1` |
| Date | 2026-09-17 |
| Status | **BETA 1 — stable, running, verified green** |
| Supersedes | `LYGO_LLM_CONSOLE_v1.md` (2026-09-15, 1,042-line *design* document — pre-build intent, not as-built) |
| Companion to | `LYGO_USB_AND_CLAW_MASTER_WHITEPAPER.md`, `LYGO_TURBO_MODELS_WHITEPAPER.md`, `RECOVERY/USB_LLM_CONSOLE.md` |
| Steward / publisher | Justin Helmer (ExcavationPro · "Lightfather") — **human is the only publisher** |
| Purpose | (1) archive what was built and proven; (2) serve as the **builder template**: folder map, function index, recipes, conventions, guardrails, roadmap — so any human or AI agent can continue the build without re-deriving it |

### 0.1 Where this document lives

| Copy | Path | Why |
|---|---|---|
| Canonical | `<EDRIVE>\lygo-protocol-stack\docs\whitepapers\LYGO_LLM_CONSOLE_v2_AS_BUILT.md` | stack archive, next to `LYGO_LLM_CONSOLE_v1.md` |
| Travels with the kit | `<EDRIVE>\lygo-protocol-stack\lygo_llm_console\WHITEPAPER.md` | builder reads it where the code is |
| USB recovery (docs) | `<USB_ROOT>\LYGO_BUILDER_KEY\docs\LYGO_LLM_CONSOLE_v2_AS_BUILT.md` | USB recovery / archival copy |
| USB recovery (RECOVERY) | `<USB_ROOT>\LYGO_BUILDER_KEY\RECOVERY\LYGO_LLM_CONSOLE_v2_AS_BUILT.md` | recovery-folder copy beside `USB_LLM_CONSOLE.md` |
| USB kit copy | `<USB_ROOT>\LYGO_BUILDER_KEY\lygo_llm_console\WHITEPAPER_v2_AS_BUILT.md` | white paper sits with the stale USB master so a recovery boot has it offline |
| Machine-readable twin | `…/lygo_llm_console/BUILD_MANIFEST.json` (+ USB copy) | agents read the manifest, humans read this |
| Stick copies (offline-complete) | `<USB_ROOT>\LYGO_BUILDER_KEY\docs\BUILD_MANIFEST.json`, `<USB_ROOT>\LYGO_BUILDER_KEY\RECOVERY\BUILD_MANIFEST.json`, `<USB_ROOT>\LYGO_BUILDER_KEY\lygo_llm_console\WHITEPAPER_v2_AS_BUILT.md` | a recovery boot on a stranger's PC still has the manifest and the whitepaper |

### 0.2 Credential policy (binding)

- **No key, token, or password appears in this whitepaper or in `BUILD_MANIFEST.json`.** Credential-bearing files are referenced by path only; values are written `[REDACTED]`.
- Credential-bearing files (never copy to USB, never paste into a prompt, never commit): `config/api.json`, `.env`, `save/registry.json` (if it carries paths), and anything under the steward key root.
- `config/api.json` holds the cloud key → treated as `[REDACTED]` everywhere in this document.
- The local brain needs **no** credentials at all. That is the point of the default.

### 0.3 How to re-verify every claim in this document

Everything below is reproducible on the steward's machine in three commands. Claims that were executed are marked **[VERIFIED]** with the evidence; anything inferred but not executed is marked **[PROVISIONAL]**.

```bat
:: 1) Test suite (108 tests)
cd /d "<EDRIVE>\lygo-protocol-stack\lygo_llm_console\tests"
C:\Python313\python.exe -m unittest discover -s . -p "test_*.py"

:: 2) Console health (live facts)
curl -s http://127.0.0.1:9641/api/health

:: 3) One live turn, local brain, no tools (self-knowledge check)
curl -s -X POST http://127.0.0.1:9641/api/chat -H "Content-Type: application/json" ^
  -d "{\"messages\":[{\"role\":\"user\",\"content\":\"Which model and which engine are answering this turn? One short line.\"}],\"tools\":false,\"stream\":false}"
```

---

## 1. Executive summary

The LYGO LLM Console is a **self-contained, offline-first agent console** that runs an LLM agent on the steward's own machine, with a real agent loop (identity, memory, skills, 63 callable limbs, receipts, output policy gate) and **no cloud dependency by default**. It boots its own llama.cpp engine on its own GGUF vault (an existing Ollama model store can be imported once, read-only, and is then not needed), exposes a browser studio on loopback, and can optionally hand a turn to a cloud API — which is an **additive** option, never a requirement: if the API runs out of tokens or goes down, the console answers from the local engine and says so.

Since the v1 design document, the console has been **built, wired, driven and measured**. This whitepaper is that record.

### 1.1 What is proven today **[VERIFIED]**

| Fact | Value | Evidence |
|---|---|---|
| Test suite | **372 tests, OK, 33.4 s, exit 0** | `unittest discover` 2026-09-18, re-run after the performance & stability pass (`Ran 372 tests in 33.426s`); `pytest -q` agrees: 372 passed |
| Source | **39 Python modules · 12,840 lines** | `tools/make_index.py`, 2026-09-18 — Appendix B is that tool's output |
| Tests | **44 test modules · 5,191 lines** | `tools/make_index.py`, 2026-09-18 |
| Limbs (callable tools) | **56 wired** | `/api/health` → `tools[]`, enumerated 1–56 |
| Local models discoverable | **15** | `/api/health` → `scan_n: 15` |
| Live engine | `llama-server` PID 28448 · `qwen2.5-coder:7b` · `-c 16384 -ngl 99 -t 16 -np 1 --jinja --metrics -ctk q8_0 -ctv q8_0` | process command line + health; `kv_mib: 448` |
| Console process | PID 12664, started 18:03:22; newest source patch 17:59:04 → **the running process serves current code** | process start time vs. file mtimes |
| Local studio | `127.0.0.1:9641` → health `ok`, `brain: ready`, `engine_present: true` | `/api/health` |
| Composed system prompt | **15,950 chars**, self-capped at 16,300 (of a ~60k-char engine window) | `compose_system()` measured in-process; `continuity.PROMPT_CEILING` |
| Self-knowledge turn | “**The qwen2.5-coder:7b model is answering this turn.**” | live `/api/chat`, `tools:false`, 2026-09-18 — 18 tokens at 52.1 tok/s |
| Self-knowledge turn (tools on) | “**This turn is answered by the qwen2.5-coder:7b model using the llama.cpp engine.**” | live `/api/chat`, `tools:true`, 2026-09-18 — 3,684-token prompt at 2,846 tok/s, 24 tokens at 51.1 tok/s |
| Public portal page | `https://chatagent.ca/portal/` → HTTP 200, `text/html` | live HTTPS fetch |
| Public hosted backend | **`hosted_base` is empty** → no LYGO-hosted inference live yet | live `portal.json` |

### 1.2 The Beta-1 delta (what this round actually added)

1. **Local-default brain switch** with an explicit API opt-in, and a real failure handoff (402/429/outage → local engine answers, banner + receipt + health show the handoff).
2. **The model can no longer lie about itself.** Runtime facts (build, model, engine, brain mode, standby) are injected into the system prompt *for the brain that is actually answering that turn* — prompt and routing now read the same source.
3. **Agent I/O repaired at the source**, not patched in the prompt: the canned host readout that used to overwrite the model's answer is gone; boilerplate lines are stripped instead of the whole answer being thrown away; placeholders are rewritten in place; a repeated answer is regenerated once.
4. **Memory that actually reaches the model** — head+tail memory block, duplicate-note collapse, append-time duplicate refusal (a real flood of 12 identical notes was found and fixed).
5. **Context budget discipline** — history trimming, per-section prompt caps, core-limbs-first catalog, sized against a measured 8,192-token engine window.
6. **Seat alignment** — a LYRA-Δ9 Architect seat wired through the skills/champions layer, with the guardrail “never publish for the steward; never report a receipt you did not get.”
7. **Public portal surface documented and gate-hardened** — CORS allowlist, P0 on input and output, 24 req / 10 min / IP, 4,000-char and 10-message caps, no tools, no admin tree.
8. **Full verification suite** (108 tests) including a regression test that fails if a local-brain prompt ever names the cloud model.

### 1.3 What “BETA 1” means here (and what it does not)

**It means:** the console runs, answers, remembers, routes brains correctly, survives cloud failure by design, and passes its suite on this machine; the codebase is coherent, documented, and extension-ready.

**It does not mean:** production hardening for public multi-user traffic, or that every capability has been driven end-to-end in the browser. Known gaps are enumerated honestly in **§13**, and the near-term work is sequenced in **§14** — one step at a time, each with an acceptance test.

---

## 2. Product surfaces

The project is one kit with **two faces** and **three processes**.

| # | Surface | Address | Audience | Compute | Tools/limbs |
|---|---|---|---|---|---|
| 1 | **Local studio (admin console)** | `http://127.0.0.1:9641` (loopback only) | the steward | local engine by default; API optional | **full: 63 limbs**, disks, notepad, skills, scans |
| 2 | **Public portal** | `https://chatagent.ca/portal/` | any visitor | 4 modes: LYGO hosted → HF token → visitor's local console → custom URL | chat only (local mode: visitor's own limbs) |
| 3 | **Public inference gateway** | `:9642` (behind HTTPS/Caddy; `--lan --i-consent`) | the hosted portal mode | Stream-PC engine | **chat only** — no disks, no shell, no `admin.json` |

### 2.1 Ports and processes

| Port | Process | Role | Notes |
|---|---|---|---|
| `9641` | `src/server.py` (studio) | agent API + studio UI + `/api/health` | binds **127.0.0.1** only; token-guarded (`src/auth.py`), loopback/public-path exemptions |
| `11441` | `llama-server` (`engine.py` / `lygo_engine.py`) | local inference (OpenAI-compatible) | `--jinja --metrics --alias qwen2.5-coder:7b`, `-c 16384 -ctk q8_0 -ctv q8_0` |
| `11442` | embed runner (optional) | embeddings | `/v1/embeddings` returns `501 embed_runner_optional` when absent — currently **not listening** |
| `9642` | `src/public_gateway.py` | public chat gateway | starts only on demand with `--lan --i-consent`; not part of boot |

### 2.2 Boot and shutdown

- **Start:** `LYGO_LLM_CONSOLE.bat` (kit) — resolves python, starts the runner, waits for `:11441` to answer, starts the studio on `:9641`, opens the browser. `<DESKTOP>\LYGO_LLM_CONSOLE.bat` is a thin shim that launches the kit BAT.
- **Stop:** `LYGO_LLM_CONSOLE_STOP.bat`, or `POST /api/shutdown` (stops runner + embed, then shuts the studio down).
- **First run on a clean machine:** `INSTALL.bat` → `src/install.py` (seeds identity from `prompts/`, checks python, fetches the engine, never copies `admin.json` or steward vaults).

### 2.3 The USB stick deployment — LYGO CLAW

The same kit ships on the builder stick as a **self-contained console**: nothing on the host is
required except Windows and the stick (and the `C:\Python313` python the kit already documents),
because the engine binary, every weight, and all state live on the stick.

| Port | Process | Role | Notes |
|---|---|---|---|
| `9651` | `src/server.py` (stick console) | agent API + studio UI + `/api/health` | binds **127.0.0.1**; token-guarded (`data/.lygo_llm_token` — path only, value **[REDACTED]**) |
| `11451` | `engine/llama-server.exe` (stick brain) | local inference (OpenAI-compatible) | `-c 8192 -ngl 0 -t 4 -np 1 --jinja --metrics --alias qwen2.5:3b`, `--api-key [REDACTED]` |
| `11452` | embed runner (optional) | embeddings | not booted by the stick launcher; `/v1/embeddings` → `501` |

- **Start:** `<USB_ROOT>\LYGO_BUILDER_KEY\lygo_llm_console\LYGO_AGENT_STICK.bat` — this **is** the launcher (not a shim): sweep stale ports → start the engine → wait for `:11451` → start the console on `:9651` → open the browser.
- **Weights:** the stick's own CAS, `<USB_ROOT>\LYGO_BUILDER_KEY\product\models\ollama\blobs` — 6 manifests (`gemma2:9b`, `llama3.1:8b`, `llama3.2:1b`, `nomic-embed-text:latest`, `qwen2.5:1.5b`, `qwen2.5:3b`), ≈14.7 GB. The engine is pointed at the **stick blob**, never a host `.ollama` path, so the kit behaves identically on a machine that has never run Ollama.
- **Port policy:** the stick must never claim a port the host may already own — `8080` (studio web apps) and `11434` (Ollama's own default) are **foreign**; the stick uses `9651 / 11451 / 11452`, and three tests in `tests/test_usb_portability.py` fail the build if a kit default ever drifts onto a foreign port. `--lan` stays forbidden without `--i-consent`.
- **RAM auto:** `/api/health` reports `ram_auto: true`; the console sizes the engine from the host's available RAM (observed `ram_avail` ≈ 18.6 GB on the build machine) instead of a hard-coded guess.
- **Config-honoured launch:** the engine argv comes from the stick's `config/console.json` (`ngl`, `threads`, `ctx`). The stick ships `ngl: 0, threads: 4` → **CPU-only**, so behaviour is identical on every host, paid for at ~80 s per cold turn. Flip those two numbers for a GPU host; nothing else changes.
- **Durability:** `save/` (notepad index, continuity, skills state, workspace map, receipts) is written through `src/atomicio.py` — unique temp per write, `fsync`, per-path lock, bounded retry, and a Windows-aware last-resort in-place write whose handle shares read+write+delete. Reads of those files go through `atomicio.read_text()`, which waits out a transient OS denial instead of answering the user with a `500`. See D15–D18.
- **Containment:** `src/server.py` installs `_StickContainment` (module-level, therefore testable): `handle_error` never raises, `do_GET`/`do_POST` answer `500` instead of letting an exception end the process, and stdout/stderr are reconfigured with `errors="replace"` so a cp437 console can never be killed by a box-drawing character.

---

## 3. Architecture

### 3.1 Process and data-flow map

```
                        ┌──────────────────────────── steward's PC ────────────────────────────┐
                        │                                                                      │
  browser ──HTTP──▶ 127.0.0.1:9641  ── src/server.py ────────────────┐                        │
   (studio UI)         │  Handler.do_GET / do_POST / _api_chat        │                        │
                        │      │                                      │                        │
                        │      ├─ auth.py ......... token / loopback  │                        │
                        │      ├─ p0_hook.gate_prompt ....... IN gate │                        │
                        │      ├─ continuity.compose_system(brain) ───┤ system prompt          │
                        │      │      └─ runtime_facts.prompt_block ──┤  (live facts)          │
                        │      ├─ chat_loop.trim_history() ───────────┤ history window         │
                        │      ├─ chat_loop.host_prefetch() ──────────┤ 63 limbs (pre-ran)     │
                        │      │                                      │                        │
                        │      ├── brain_router / cloud_api ──────────┼──▶ cloud API  (opt-in) │
                        │      │        └─ 402/429/outage ────────────┼──▶ handoff → local     │
                        │      └── engine.maybe_spawn / runner ───────┼──▶ 127.0.0.1:11441     │
                        │                                             │      llama-server      │
                        │      ├─ p0_hook.gate_output_window ...... OUT gate                    │
                        │      ├─ chat_loop.sanitize_assistant()                                              │
                        │      ├─ receipts.write_receipt() .......... receipt                   │
                        │      └─ continuity.save_session() ......... session + memory          │
                        │                                                     │                 │
                        │  workspace/  SOUL.md IDENTITY.md MEMORY.md  save/ ◀──┘                 │
                        └──────────────────────────────────────────────────────────────────────┘
                                          │  (only if operator chooses)
  visitor ──HTTPS──▶ chatagent.ca/portal/ ─┴─▶ [LYGO hosted] :9642 public_gateway.py ──▶ engine
                                        └──▶ [HF token] router.huggingface.co  (visitor's account)
                                        └──▶ [visitor's console] http://127.0.0.1:9641 (their PC)
                                        └──▶ [custom URL]  visitor's OpenAI-compatible HTTPS
```

### 3.2 One console turn, step by step (`server.py::_api_chat`)

| # | Step | Code | Failure behavior |
|---|---|---|---|
| 1 | Read body (≤4 MB) | `_read_body` | `413 too_large` |
| 2 | Parse JSON | — | `400 bad_json` |
| 3 | Collect user text (`messages[]` or `prompt`) | `chat_loop.extract_user_text` | — |
| 4 | P0 input gate | `p0_hook.gate_prompt` | `451 quarantine` |
| 5 | Decide brain: `use_api` / `force_api` / saved mode + `has_key` | `brain_router`, `cloud_api.public_status` | no key → local |
| 6 | Recent-handoff cooldown | `cloud_api.cooldown_active` | skip API, answer local, record handoff |
| 7 | Compose system prompt **for that brain** | `continuity.compose_system("api"\|"local")` | — |
| 8 | Trim history into the engine window | `chat_loop.trim_history` | oldest turns dropped, system prompt always survives |
| 9 | Host prefetch (URL/search/map/notes/self-check) | `chat_loop.host_prefetch` | traces attached, never fatal |
| 10 | Answer: cloud **or** spawn/route local runner | `cloud_api.chat` \| `engine.maybe_spawn` | 402/429/timeout → `brain_router.handoff_info` → local answers, banner prepended |
| 11 | Tool rounds (model-initiated; extra follow-up step when the host pre-ran tools) | `chat_loop.run_tools_round`, `tools.dispatch` | tool error → message back to model, turn continues |
| 12 | P0 output gate | `p0_hook.gate_output_window` | QUARANTINE aborts the stream |
| 13 | Answer hygiene | `chat_loop.sanitize_assistant` / `strip_boiler` / `same_answer` | boiler lines dropped; one regeneration on repeat; **never** a canned replacement |
| 14 | Receipt + session save | `receipts.write_receipt`, `continuity.save_session` | receipt id returned in the payload |

### 3.3 Brain routing state machine

```
        ┌──────────────────────── LOCAL (default, always complete) ────────────────────────┐
        │  no key needed · engine auto-spawned · full 63 limbs · offline                   │
        └───────────────▲───────────────────────────────────────────▲──────────────────────┘
                        │ press LOCAL                               │ handoff (auto)
                        │                                           │ 402 · 429 · outage
        ┌───────────────┴──────────────┐   press API   ┌────────────┴──────────────────────┐
        │  API armed (key saved+on)    │──────────────▶│  CLOUD turn: deepseek-chat        │
        │  label: “API key saved, off” │◀──────────────│  local stays booted as standby    │
        └──────────────────────────────┘   press LOCAL └───────────────────────────────────┘
```

Operator-visible state is always published in three places: the **bubble footer** (`via-local` / `via API`), the **health payload** (`cloud.mode`, `cloud.enabled`, `cloud.standby`, `last_brain`, `fallback`, `handoff`), and the **system prompt** the answering model receives.

---

## 4. Runtime topology and files at rest

| Location | What it is | Live? |
|---|---|---|
| `<EDRIVE>\lygo-protocol-stack\lygo_llm_console` | **the live kit** — what the desktop BAT launches | ✅ |
| `<EDRIVE>\lygo-protocol-stack\docs\whitepapers` | stack archive (v1 + this v2) | — |
| `<USB_ROOT>\LYGO_BUILDER_KEY` | USB recovery root: `docs/`, `RECOVERY/`, `restore/`, `verify/`, `stack/`, `product/models/`, `lygo_llm_console/` | archival |
| `<USB_ROOT>\LYGO_BUILDER_KEY\lygo_llm_console` | **stale USB master** (build `v1.1-20260916m`, 47 tests, embedded py3.12) — no brain switch, no LYRA seat | ❌ stale |
| `<USB_ROOT>\LYGO_BUILDER_KEY\product\models\ollama\blobs\sha256-5ee4f07…` | the GGUF blob the live engine booted (`qwen2.5:3b`) | ✅ read-only |
| `<EDRIVE>\LYRA_CORE` | LYRA core the LYRA-Δ9 seat derives from | — |
| `…\lygo_llm_console\workspace\` | `SOUL.md`, `IDENTITY.md`, `MEMORY.md`, session files — the agent's continuity | ✅ |
| `…\lygo_llm_console\save\` | registry, notebooks, logs, pids, receipts | ✅ |
| `…\lygo_llm_console\config\` | `console.json`, `local.json`, `api.json` `[REDACTED]`, `admin.json` (not public git) | ✅ |

**Sync direction between `I:` and `E:` has not been decided by the steward.** Until it is, treat `<USB_ROOT>\LYGO_BUILDER_KEY\lygo_llm_console` as a stale snapshot and copy **documentation** (this whitepaper) to the USB, not code.
---

## 5. The brain system — LOCAL default, API opt-in, failure handoff

### 5.1 Design rule

> **The local engine is the complete default. The cloud API is an additive option. Nothing that matters may depend on the API being up.**

Consequences that are enforced in code:

- No key is required to boot, chat, run limbs, remember, or use skills.
- Saving or clearing a key **must not** silently switch the brain (`cloud_api.save` does not flip the mode).
- A cloud failure of any kind (insufficient balance 402, rate limit 429, timeout, transport error) **hands the turn to the local engine**, and the answer says so.
- The local engine stays **booted as standby** while the API is armed, so a handoff costs no model-load time.

### 5.2 Modes and labels

| Saved state | Effective brain | Operator label |
|---|---|---|
| no key | local | `LOCAL · qwen2.5:3b · no API key saved` |
| key saved, API off | local | `LOCAL · qwen2.5:3b · API key saved, off` |
| key saved, API on | cloud | `API · DeepSeek/deepseek-chat · local standby qwen2.5:3b` |
| API failed ≤ cooldown | local | `⚠ API handoff — {reason} … the local engine answered instead (qwen2.5:3b)` |

### 5.3 Handoff contract (the additive guarantee) **[VERIFIED]**

Reproduced end-to-end with a stub that returns HTTP 402:

- Bubble footer: `via-local`
- `last_class`: `bubble assistant via-local takeover`
- Banner shown to the operator: `⚠ API handoff — API out of tokens/credit — Insufficient Balance. … The local engine answered instead (qwen2.5:3b)…`
- Status line: `API handoff HTTP 402 · … local answered (1 handoffs). Press API to try the API again.`
- `cooldown_active()` keeps further turns local so a dead API is not hammered; pressing **API** clears the handoff and retries.

### 5.4 Key functions

| Function | File | Contract |
|---|---|---|
| `public_status()` | `src/cloud_api.py` | Non-secret view of the cloud state: `ok, enabled, has_key, mode, label, provider, model, standby, degraded, last_code, last_error` |
| `active()` | `src/cloud_api.py` | API usable *right now*: key saved **and** switched on **and** not in handoff |
| `note_error()` / `clear_error()` / `cooldown_active()` | `src/cloud_api.py` | Record a failure, clear it on operator action, and suppress retries inside the cooldown |
| `should_fallback()` / `reason()` | `src/brain_router.py` | Whether a cloud failure must be handed to local, and the short human reason |
| `mode_of()` | `src/brain_router.py` | Persisted mode: API only while a key is saved, switched on, and not in handoff |
| `handoff_info()` / `banner()` | `src/brain_router.py` | The record for UI/receipt/health, and the line prepended to the local answer |
| `answering(brain)` | `src/runtime_facts.py` | **Who generates the tokens this turn** — the brain switch decides, the model never guesses |
| `compose_system(brain)` | `src/continuity.py` | Builds the system prompt from the answering brain, not from a guess |

### 5.5 Console API surface (studio, `:9641`)

| Route | Method | Purpose |
|---|---|---|
| `/` and `/portal/*` | GET | studio UI, static assets |
| `/api/health` | GET | live facts: build, engine, models, limbs, brain, cloud, ram, scan |
| `/api/chat` | POST | **the agent turn** — `{messages[]|prompt, tools?, model?, max_tokens?, stream?, use_api?, force_api?}` |
| `/v1/chat/completions` | POST | OpenAI-compatible face of the same agent |
| `/v1/embeddings` | POST | `501 embed_runner_optional` unless the embed runner is up |
| `/api/brain` | POST | **the brain switch** — arm/disarm API vs local |
| `/api/tool` | POST | direct limb call `{name, arguments}` |
| `/api/models`, `/api/scan` | GET/POST | discover / rescan local models |
| `/api/shutdown` | POST | stop runner + embed + studio |

### 5.6 Public gateway surface (`:9642`, chat only)

| Route | Method | Limits |
|---|---|---|
| `/`, `/health`, `/api/health` | GET | — |
| `/v1/models`, `/api/models` | GET | — |
| `/v1/chat/completions`, `/api/chat` | POST | 24 req / 10 min / IP · ≤48 KB body · ≤10 messages · ≤4,000 chars · P0 in **and** out · CORS allowlist of LYGO domains · **no tools, no disks, no shell, no `admin.json`** |

---

### 5.7 House defaults — the default brain and the default API (binding) **[VERIFIED 2026-09-18]**

Two defaults ship with the kit, in code, not in a habit:

| Slot | Default | Where the rule lives | What it means |
|---|---|---|---|
| **Main brain (local)** | a tool-strong **coder** model — `qwen2.5-coder:7b` on the stick CAS, then coder 3b, then qwen/llama general | `registry.PREFER_IDS`, `registry.tool_rank()` | The console is an agent. Among the models a host can actually hold, the best *tool caller* wins; size is only a tiebreak inside a capability tier. A 9B general chat model no longer outranks a 7B coder. |
| **Backup API (cloud)** | **DeepSeek** — `deepseek-chat` | `cloud_api.DEFAULT_PROVIDER`, `cloud_api.DEFAULT_MODEL` | A blank/hand-edited/junk `config/api.json` normalises to DeepSeek instead of a chain entry posting to an empty URL. DeepSeek also **leads the failover order** behind an explicit primary: `groq, deepseek, gemini` is the order even if nobody wrote it down. |

`GET /api/health` publishes the policy so a human or an agent can check it without reading source:
`cloud.provider`, `cloud.default_provider`, `cloud.is_default`.

**Rule:** the API is the *backup*, never the default mode. `mode` stays `local` until a human or a
tool switches it (`POST /api/brain {"mode":"api"}`), and the local engine stays booted as standby.

## 6. The agent layer

An LLM call is not an agent. What makes this an agent is the ring of deterministic host code around the model — and the discipline that **host code supports the model's answer instead of replacing it**.

```
identity ─ memory ─ skills ─ limbs ─ gates ─ receipts ─ sessions
   │         │        │        │       │        │          │
SOUL.md  MEMORY.md  SKILL.md  56 fns  P0      audit      continuity
IDENTITY.md          champions       in/out   node       across turns
```

### 6.1 Prompt composition (exact order)

`continuity.compose_system(brain)` assembles, in order:

| Order | Block | Source | Cap |
|---|---|---|---|
| 1 | Alignment / protocol | `prompts/LYGO_ALIGN.txt` (`align.load_align`) | as-is |
| 2 | Soul | `workspace/SOUL.md` | 2,400 chars |
| 3 | Identity | `workspace/IDENTITY.md` | 1,400 chars |
| 4 | **Runtime facts** | `runtime_facts.prompt_block(brain)` | — |
| 5 | Limb catalog (core limbs first) | `runtime_facts.limb_catalog()` | 2,200 chars |
| 6 | Skills catalog | `skills_mod.prompt_catalog()` | 1,500 chars |
| 7 | Memory block | `continuity.read_memory_block()` — **head + tail, de-duplicated** | 3,200 chars |
| 8 | Session history | `chat_loop.trim_history()` | 9,000 chars |

**Runtime facts** is the block that ends the "which model are you?" problem. It states, from live state: console build, answering model, engine + endpoint, brain mode, standby engine, workspace, scan count, limb count, and how whoami/kernel_status report it.

### 6.2 Memory that reaches the model

The failure mode found in the field: `MEMORY.md` grows at the **bottom**, so a head-only read never shows what `remember` just wrote. Fixes:

| Function | File | Behavior |
|---|---|---|
| `_split_note()` | `continuity.py` | Parses `- (2026-09-17 17:05) note text` → `(stamp, note)` |
| `dedupe_notes()` | `continuity.py` | Collapses repeated `remember` lines to one, newest stamp, marked `(repeated 12x)` — **runs before the cap**, so a flood cannot hide curated sections |
| `read_memory_block(cap)` | `continuity.py` | Head (protocol) + tail (newest notes), de-duplicated, tail share 0.50 |
| `append_memory()` | `continuity.py` | Refuses an identical note already present → `{"ok": true, "duplicate": true}` (root-cause fix, not just cleanup) |

### 6.3 Limbs (56 callable tools)

The tool catalog is passed to the model **every turn** (tools are never withheld), and the catalog is also rendered into the prompt as name + intent so a small model knows what it can call. Groups:

| Group | Examples |
|---|---|
| Identity / self | `whoami`, `kernel_status`, `self_check`, `soul_read`, `identity_read`, `steward_map` |
| Workspace / files | `workspace_map`, `list_dir`, `read_file`, `write_file`, `edit_file`, `find_files`, `glob_files`, `scan`-backed discovery |
| Memory | `remember`, `memory_read`, `memory_append`, `memory_recall`, `notepad_*` |
| Skills / hub | `skill_list`, `skill_read`, `skill_enable`, `skill_disable`, `clawhub_search`, `clawhub_install`, `skillhub_list`, `skillhub_install` |
| Web (Witness/RESOURCE, never CANON) | `web_search`, `web_fetch`, `jina_fetch`, `http_json`, `wayback`, `arxiv_search`, `hn_search`, `github_search`, `weather`, `geocode`, `world_pulse` |
| Compute / utility | `shell`, `python_exec`, `calc`, `hash_text`, `now`, `todo_add`, `todo_list` |
| Safety / ops | `p0_gate`, `stack_health`, `credential_where` (points at credential files — **returns no values**) |
| Media | `image_info`, `image_save`, `image_list`, `page_thumbnail`, `download_url` |
| Sessions | `sessions_list` |

### 6.4 Skills and champions

`src/skills_mod.py` (802 lines — the largest module) implements OpenClaw-compatible skills: `SKILL.md` parsing, extra roots, enable/disable state, a prompt catalog, and two install paths (`clawhub_*`, `skillhub_*`) that download and extract skill zips.

`match_invoked()` resolves **which seat the operator named** — the most specific name wins — and drives the champion seats defined in the `CHAMPIONS` table. This build added the **`champion-lyra-architect` seat (LYRA-Δ9)**, derived from the LYRA core, carrying the guardrail:

> **Never publish for the steward. Never report a receipt you did not get.**

### 6.5 Gates, receipts, and continuity

| Concern | Implementation | Rule |
|---|---|---|
| Input policy | `p0_hook.gate_prompt` | `QUARANTINE` → HTTP 451, turn refused, reason recorded |
| Output policy | `p0_hook.gate_output_window` | regex policy only (no physics); `QUARANTINE` aborts the stream |
| Audit | `receipts.write_receipt` / `create_node` | every turn returns a receipt id |
| Continuity | `continuity.load_session` / `save_session` / `new_session` | sessions persist; `/portal` “New chat” starts clean |
| Steward map | `admin_map.brief_text` | publishes **paths and org links**, never credential values |

### 6.6 Answer hygiene (the anti-parrot rules)

| Function | Rule it enforces |
|---|---|
| `sanitize_assistant` | Cleans the model's answer. **It is never replaced by a canned string any more.** |
| `strip_boiler` / `BOILER_LEAD` | Drops template lines the prompt forbids, keeps everything the model actually said |
| `_scrub_placeholders` | Rewrites an invented placeholder URL **in place** instead of discarding the answer |
| `same_answer` | Detects the model repeating its previous answer; triggers exactly one regeneration |
| `fallback_from_traces` | Last-resort host readout, **only** for a turn where the model returned nothing usable, and prose-only (no raw dict reprs) |
| `_human_skills` | `{'n': 18, 'enabled': 6}` → a human phrase |

---

## 7. Context budget and performance envelope

| Quantity | Value | Note |
|---|---|---|
| Engine context window | **16,384 tokens** | `-c 16384` with `-ctk q8_0 -ctv q8_0` on the live runner (was 8,192) |
| Rough char equivalent | ~60,000 chars | ~3.7 chars/token at this mix |
| Composed system prompt | **15,950 chars** | measured in-process **[VERIFIED]** |
| History window | 9,000 chars | `HISTORY_CHARS` + `trim_history()` |
| Headroom after prompt + history | ~34,000–40,000 chars | left for tool traces + the answer (the console's own ceiling, not the window, is the binding limit) |
| Prompt ceiling | **16,300 chars** (`PROMPT_CEILING`) | the composed prompt never crosses this; the window behind it is headroom |
| Section caps | soul 2,400 · ident 1,400 · memory 3,200 · limb catalog 2,200 · skills catalog 1,500 · compact JSON 3,000 | each guards its own block |

**Why this matters:** on an 8k window, an unbounded prompt silently evicts the system prompt from the top of the context — which is exactly how an agent “forgets who it is”. Every cap above exists because that failure was observed, and `test_prompt_fits_the_engine_window` keeps it from regressing.

**Small-model reality:** the default local brain is `qwen2.5:3b`. It is fast and honest when the prompt is tight and the facts are injected; it degrades (parrots, drifts, invents tool behavior) when asked to infer its own runtime or to hold long context. The design response is deliberate: **inject facts, pre-run limbs, cap context, and keep the prompt truthful** — not to ask a 3B model to remember what the host already knows.
---


### 7.1 Host-adaptive performance and GPU backends **[VERIFIED 2026-09-18]**

The stick ships a CPU-only engine on purpose: it has to boot on every PC. Speed comes from
reading the host and **proving** what it can do, never from assuming.

* **Plan** (`src/perf.py`): threads = one per core, minus one for the console (16 here; the
  shipped config pinned 4). Layers = as many as the free VRAM holds; an integer in
  `config/console.json` is still the operator's pin, `"auto"` lets the host decide.
* **Backends** (`src/backends.py`): optional GPU engine builds live in `engine/backends/<name>/`
  and are applied or selected only after a **self-test** loads a real model with them on this
  host, in their own process. A crash costs one attempt, never the console. Verdicts are
  remembered per host and keyed by the backend's own files (`data/perf.json`).
* **Health tells the truth**: `/api/health -> perf` carries `mode`, `ngl`, `threads`, `backend`,
  `engine_dir`, the `planned` profile, and `effective` after any fallback — reported as what is
  actually running, not what was hoped for.

Measured on this host (RTX 4060 Ti 8 GB, 20 cores):

| Configuration | Prompt eval | Generation | 3.3k-token prompt |
|---|---|---|---|
| CPU, shipped pin (`ngl 0 -t 4`) | 80 tok/s | 15.1 tok/s | 58 s |
| CPU, auto threads (`-t 16`) | 133 tok/s | 18.9 tok/s | 35 s |
| CUDA, 99 layers (self-tested) | **4,300 tok/s** | **101.9 tok/s** | **1.16 s** |

The live stick console after this change: boot to `brain=ready` in 8.1 s, one real chat turn
6.6 s, `/api/health` reporting `mode=gpu_full, ngl=99, threads=16, backend=cuda` with
`backend_layer.active=cuda` and `applied_overlays=[]`. The API brain (`/api/brain`) is
unchanged and still the boost when a host is too small for the model it wants.

**The Vulkan lesson (defect D16):** a llama.cpp Vulkan build crashes on this host *inside the
NVIDIA driver* (`nvoglv64.dll`, `0xC0000005`) at **any** `-ngl`, 0 included, on both the
pinned b10988 and the newest b11037 — because llama.cpp loads every backend DLL it finds next
to the exe. So a GPU DLL in `engine/` can take the whole kit down even in CPU mode. Backends
therefore live in the store, activation is earned, and a bad verdict is remembered.

| Goal | Command |
|---|---|
| See what is installed | `scripts\fetch_engine.ps1 -List` |
| Add a backend | `scripts\fetch_engine.ps1 -Backend cuda` (NVIDIA, ~574 MB) · `-Backend vulkan` (any GPU, 30 MB) |
| Inspect / prove / retire one | `python src\backends.py status` · `test cuda` · `drop cuda` |
| Turn GPU off | `config/console.json` → `"gpu": "off"` |
| Pin threads or layers | `"threads": 12`, `"ngl": 20` — an integer is always a pin |
| Re-test after a driver update | delete `data/perf.json` (verdicts only), or re-run the fetch script |

### 7.2 Launch-flag tuning — measured, not assumed **[VERIFIED 2026-09-18]**

The engine accepts many knobs; on this host only one of the candidates helped and one candidate
turned out not to exist. Same 7B coder, same 1,059-token prompt, CUDA `-ngl 99 -t 16`:

| Variant | Prompt eval | Generation | Load |
|---|---|---|---|
| **baseline (shipped)** | **3,245.7 tok/s** | **55.4 tok/s** | 12.2 s |
| `-fa on` (flash attention) | 2,620.6 tok/s | 55.2 tok/s | 14.0 s |
| `-b 4096 -ub 1024` | 2,750.1 tok/s | 55.0 tok/s | 12.5 s |
| `--no-mmap` | *launch aborted: invalid argument* | — | — |

Consequences, all of them in code:

* **Flash attention is opt-in** (`config/console.json` → `"flash_attn"`). Every GPU plan used to
  advertise `flash_attn: true` while `engine.spawn_runner()` never passed the flag — the plan was
  a lie and the setting was unreachable (defect D20). Now the plan tells the truth and the flag is
  plumbed through, default **off** because it measured slower here.
* **No batch-size tuning.** The defaults win; shipping a regression with a nicer-looking argv is
  still shipping a regression.
* **`--no-mmap` does not exist in this build** — `-lm/--load-mode {auto,none,mmap,mlock,mmap+mlock,dio}`
  replaced it. The old flag is mapped to `-lm none` so a future caller passing `mmap=False` cannot
  kill a launch (defect D23).

### 7.3 Generation is bandwidth-locked; the cache was the only lever **[VERIFIED 2026-09-18]**

Measuring first, then buying, is what this section is for. Three interleaved prompts through the
console on the default brain (`qwen2.5-coder:7b`, Q4_K_M, CUDA `-ngl 99 -t 16`), plus one real
gated turn, gave 56.8 / 56.6 / 56.0 tok/s — **mean 56.5**. A direct conversation with the engine
measured 56.2, so the console's own overhead on generation is unmeasurable here, and the number is
~92% of what this card can do at all: 288 GB/s of bandwidth over a 4.68 GB model is a **61 tok/s**
ceiling, and generation is memory-bound, not compute-bound.

That is why the flag hunt below produced almost nothing — and why the pass stopped hunting:

| Variant (fresh engine each, same prompts) | Generation | Code prompt | Prefill | Verdict |
|---|---|---|---|---|
| baseline, ctx 8,192 | 56.7 | 56.8 | 3,550.8 | — |
| `-fa on` (flash attention) | 56.7 | 56.5 | 3,474.2 | rejected, −2% prefill |
| `-b 4096 -ub 1024` | 56.5 | 56.0 | 3,590.5 | noise |
| `--spec-type ngram-simple` | 56.5 | 56.7 | 3,566.2 | no effect |
| `--spec-type ngram-map-k4v` | 56.9 | 56.5 | 3,556.9 | no effect |
| **draft model `qwen2.5:1.5b`** | 54.7 | **78.5** | 2,794.7 | **+38% on code** — deferred (see below) |
| ctx 16,384 + `q8_0` KV + fa | 55.1 | 54.8 | 3,428.7 | rejected |
| **ctx 16,384 + `q8_0` KV** | 54.6 | 54.9 | 3,447.5 | **adopted** |

**What was adopted: twice the context at the same VRAM.** A `q8_0` KV cache costs half of f16 per
token, so 16,384 tokens of context fits in the *same 448 MiB* that 8,192 tokens of f16 was using —
measured live, twice, at `kv_mib: 448`. The cost is ~1% of generation rate (56.5 → 56.0 through the
console), and what it buys is the failure mode the engine log had been showing: a **6,342-token
truncation** on real prompts. `PROMPT_CEILING` is deliberately *not* raised with the window — the
window is headroom for history, tool traces and the answer, not a licence to write longer prompts.

**What was deferred, with numbers.** Draft-model speculative decoding is the one real find: a
`qwen2.5:1.5b` draft took code generation from 56.5 to **78.5 tok/s**. It is not wired because it
costs −21% prefill, +10 s load, ~1 GB of VRAM, and a second model to ship, pin and validate per
host. A number in the record is worth more than a half-validated feature.

**Telemetry, so this is re-checkable without a lab:** every `/api/chat` reply and SSE `done` event
now carries `perf` (`gen_tok_s`, `prompt_tok_s`, `gen_tokens`, `engine_calls`) straight from
llama.cpp's own `timings`; `/api/health` carries `last_perf`; `portal/app.js` shows it on the
context line and as the answer's tooltip; and `scripts/bench_toks.py` reproduces the whole table
read-only against a live console, printing the config beside every number.

## 8. Repository map — every folder, and what you edit there

Live kit root: **`<EDRIVE>\lygo-protocol-stack\lygo_llm_console\`**

| Folder / file | Purpose | Edit this when you want to… |
|---|---|---|
| `src/` | **all runtime code** — 33 modules, 7,525 lines | change behavior (see §8.1 and Appendix B) |
| `tests/` | 24 test modules, 1,370 lines, 108 tests | add a test for any new behavior (`test_*.py`, `unittest`) |
| `tools/` | doc/analysis tooling — `make_index.py` regenerates Appendix B of the whitepaper with `ast` | refresh the module/function index after changing `src/` |
| `portal/` | **admin studio UI** (`index.html`, `app.js` 34.7 KB, `style.css`, `radio.js`, `donate.js`, logos) | change the local studio UI (chat, brain buttons, panels) |
| `web_portal/` | **public portal UI** (`index.html` 23 KB, `app.js` 33.3 KB, `tools.js` 27.5 KB, `portal.json`, `manifest.json`, `lygo-skills.json`, `SOUL/IDENTITY/MEMORY.md`, social cards) | change the public page at chatagent.ca/portal |
| `prompts/` | seed alignment + identity: `LYGO_ALIGN.txt`, `SOUL.md`, `IDENTITY.md`, `MEMORY.md`, `BRAIN.md`, `MAP.md`, `LINKS.md`, `POEM.txt`, `CONTINUITY_SEED.json` | change what a **fresh install** believes |
| `workspace/` | **the live agent's** `SOUL.md`, `IDENTITY.md`, `MEMORY.md`, sessions | change what **this** agent believes (runtime, not seed) |
| `config/` | `console.json` (ports/scan roots), `local.json` (extra model roots), `api.json` `[REDACTED]`, `admin.json` (steward map — not public git), `local.json.example` | change ports, model roots, admin unlock map |
| `save/` | runtime state: registry, notebooks, pids, logs, receipts (`registry.json` is credential-adjacent) | inspect/repair runtime state |
| `data/` | static data + `data/hf-space-lygo-portal/` (the static HF Space mirror) | ship a static portal mirror |
| `models/` | locally-imported model files (GGUF) | drop a model in place |
| `engine/` | resolved engine binaries (`engine.py` resolves; `binary_forbidden` blocks disallowed binaries) | swap/upgrade the engine |
| `skills/` | operator-facing skill slots (`README.md`; skills themselves live under `save/skills` + extra roots) | add a kit-local skill |
| `scripts/` | `fetch_engine.ps1`, `fetch_colibri.ps1` | change how engines are fetched |
| `*.bat` | `LYGO_LLM_CONSOLE.bat` (start), `…_STOP.bat` (stop), `INSTALL.bat` (first-run), `PUBLIC_GATEWAY.bat` (public chat gateway) | change boot/install behavior |
| `*.md` | `README.md` (orientation), `PUBLIC_PORTAL.md` (portal contract), `HARDENING.md`, `MODELS.md`, `SKILLS.md`, `LYGO_ENGINE.md`, `COLIBRI.md`, `FULL_LYGO.md`, `READ_DISCLAIMER_FIRST.md` | documentation |
| `WHITEPAPER.md` | **this document** | keep the record current with each release |

### 8.1 The `src/` modules, grouped by role

| Role | Modules (lines) |
|---|---|
| **Console spine** | `server.py` (1217) · `paths.py` (63) · `registry.py` (62) · `auth.py` (63) |
| **Agent I/O** | `chat_loop.py` (478) · `continuity.py` (266) · `runtime_facts.py` (209) · `receipts.py` (49) · `p3_note.py` (45) |
| **Brains & engines** | `brain_router.py` (119) · `cloud_api.py` (265) · `engine.py` (298) · `lygo_engine.py` (216) · `colibri.py` (167) · `openai_proxy.py` (54) · `gguf_header.py` (180) · `ollama_import.py` (116) |
| **Capability layer** | `tools.py` (418) · `limbs.py` (400) · `web_tools.py` (424) · `image_tools.py` (78) · `notepad.py` (159) · `world_clock.py` (157) · `skills_mod.py` (802) |
| **Safety & governance** | `p0_hook.py` (151) · `align.py` (26) · `stack_health.py` (41) · `admin_map.py` (204) · `workspace_map.py` (220) · `scanner.py` (109) |
| **Public / install** | `public_gateway.py` (256) · `install.py` (207) |

**Reading order for a new builder (or an AI agent):** `README.md` → `PUBLIC_PORTAL.md` → this §8 → `src/server.py::_api_chat` → `src/chat_loop.py` → `src/continuity.py` + `src/runtime_facts.py` → `src/brain_router.py` + `src/cloud_api.py` → the module you intend to change → its test.

---

## 9. The public web portal — `https://chatagent.ca/portal/`

### 9.1 What it is

One static studio page (`web_portal/`) that gives any visitor a LYGO agent without an install. The visitor picks **who pays for the compute**:

| Mode | Compute | Limbs | Status **[VERIFIED 2026-09-17]** |
|---|---|---|---|
| **LYGO hosted** | Stream PC `public_gateway.py` behind HTTPS (`portal.json.hosted_base`) | chat + P0 + rate limit only | **NOT LIVE — `hosted_base` is empty** |
| **Hugging Face** | visitor's own HF account, token stays **in their browser** (`router.huggingface.co`, default `Qwen/Qwen2.5-1.5B-Instruct`) | chat only | wired; depends on visitor token |
| **My local console** | visitor's PC at `http://127.0.0.1:9641` | **full LYGO limbs** | wired; HTTPS pages often cannot call `http://localhost` (mixed content) → the visitor should use the local studio window |
| **Custom URL** | visitor's OpenAI-compatible HTTPS (Space, tunnel, llama-server) | chat (full limbs only if it is *their* console) | wired |

Page health: `https://chatagent.ca/portal/` → **HTTP 200, `text/html`**. PWA manifest present (`manifest.json`: standalone, dark `#0c0a08`, SVG+JPG icons, `start_url`/`scope`/`id` bound to the portal path).

### 9.2 The gateway (this is the "portal API version")

`src/public_gateway.py` (256 lines) — a second, deliberately crippled server. It is **not** the admin console and must never be:

- **Backends:** `--backend ollama` (local/Ollama-compatible) or `--backend openai` (any OpenAI-compatible endpoint). Default model `qwen2.5:3b` (`LYGO_PUBLIC_MODEL`).
- **Inference contract:** `/v1/chat/completions` and `/api/chat` — chat only.
- **Hard limits:** window 600 s, **24 requests / IP**, `MAX_CHARS = 4,000`, `MAX_MSGS = 10`, body ≤ 48 KB.
- **Policy:** `PUBLIC_SYSTEM` prompt + P0 gate on **input and output** (`p0_regex_only` verdict recorded).
- **Network:** CORS `ALLOW_ORIGINS` allowlist of LYGO domains; `X-Frame-Options: SAMEORIGIN`; launched only with `--lan --i-consent`, normally on `:9642` behind Caddy (`handle_path /llm/* → reverse_proxy 127.0.0.1:9642`).
- **Absent by design:** tools, disks, shell, `admin.json`, steward map, notepad (the public page's notepad is `localStorage` only).

### 9.3 Deployment recipe (hosted mode)

```bat
:: Stream PC — public chat gateway, LAN-facing, explicit consent
python -u src\public_gateway.py --backend ollama --model qwen2.5:3b --lan --i-consent --port 9642
```

1. Reverse-proxy it under HTTPS (Caddy block in `PUBLIC_PORTAL.md`).
2. Set `web_portal/portal.json` → `"hosted_base": "https://YOUR.PUBLIC.HOST/llm"`.
3. Republish `web_portal/` to `chatagent.ca/portal/`.
4. Do **not** bind `:9641` to the internet. Ever.

### 9.4 Gap analysis — making the portal the best it can be

| # | Gap | Impact | Fix | Effort |
|---|---|---|---|---|
| P1 | `hosted_base` empty → hosted mode is dead on the live site | visitors without an HF token or local console have **no working mode** | run the gateway on the Stream PC + Caddy + set `hosted_base` | S/M |
| P2 | HTTPS page → `http://127.0.0.1:9641` is mixed content | "My local console" mode fails silently in a hosted page | detect and explain; offer a copyable one-liner + the local BAT window | S |
| P3 | No visible quota/usage counter for a visitor | rate-limit rejections look like breakage | surface remaining quota + a friendly 429 message | S |
| P4 | Public gateway has no per-day cap or ban list | a hostile visitor can burn the daily budget within the window limit | add per-day cap + temporary IP block, log to `save/` | S/M |
| P5 | Portal and studio UIs are two code trees (`portal/app.js` 34.7 KB, `web_portal/app.js` 33.3 KB) | every UI fix is done twice | extract the shared chat/brain component into one JS module consumed by both | M |
| P6 | No published model choice for visitors | visitors cannot trade speed for quality | expose a whitelist (`qwen2.5:3b`, `llama3.2:1b`, …) with a default | S |
| P7 | `web_portal/lygo-skills.json` (5.6 KB) is the only public skill story | visitors cannot see the skill engine | render the public skill catalog as a browsable page | S |
| P8 | Streaming + abort on the public path untested under load | long answers may time out on flaky mobile links | add SSE keepalive + client abort; load-test | M |

**Rule for all portal work:** the portal may be improved freely, but it must never gain access to the steward's disks, keys, or admin tree. Public is echo; local is the console.

---

## 10. Verification record (2026-09-17)

### 10.1 Test suite **[VERIFIED]**

```
cd "<EDRIVE>\lygo-protocol-stack\lygo_llm_console\tests"
C:\Python313\python.exe -m unittest discover -s . -p "test_*.py"

Ran 102 tests in 13.031s      :: before the D13 fix
OK            (exit code 0)

Ran 108 tests in 13.446s      :: re-run after the D13 fix
OK            (exit code 0)
```

Coverage by module (24 files): admin train, **agent I/O**, **brain switch**, cloud API, colibri, continuity, continuity UI, donate radio, engine, lygo engine, no-history-shadow, notepad, openai proxy, P0 hook, public gateway, public install, registry, scanner, skills, tool battery, tools, web tools, workspace map, world clock.

### 10.2 Live console facts **[VERIFIED]** — `GET /api/health`

| Field | Observed value |
|---|---|
| `build` | `v1.1-20260917api2` |
| `signature` | `Δ9Φ963-LYGO-LLM-CONSOLE-v1` |
| `ok` / `brain` | `true` / `ready` |
| `selected` | `qwen2.5:3b` |
| `engine_present` | `true` |
| `bind` | `127.0.0.1:9641` |
| `scan_n` | `14` |
| `tools` | **56 entries, enumerated** |
| `ram_avail` | ~14.9 GB |
| `physics` | `true` |
| `cloud` | `{mode: local, enabled: false, has_key: true, provider: deepseek, model: deepseek-chat, standby: qwen2.5:3b, degraded: false}` |
| `last_brain` | `local` |
| `fallback` | `null` |
| `handoff` | `null` |

### 10.3 Live engine process **[VERIFIED]**

```
llama-server.exe  PID 26752
model  <USB_ROOT>\LYGO_BUILDER_KEY\product\models\ollama\blobs\sha256-5ee4f07…
flags  -c 8192  -ngl 99  -t 16  -np 1  --jinja  --metrics  --alias qwen2.5:3b
route  127.0.0.1:11441
```

### 10.4 Live agent turns **[VERIFIED]** — `POST /api/chat`

| Probe | Request | Answer text | Traces | Receipt |
|---|---|---|---|---|
| A | “Which model and which engine are answering this turn?” · `tools:false` | **“Model: qwen2.5:3b, Engine: llama.cpp”** | 0 | `65e51bb3-…` |
| B | Same question · tools on | **“The model answering this turn is qwen2.5:3b and the engine is llama.cpp:11441.”** | 0 | `3f654bfa-…` |
| C | `whoami` · tools on | real steward/org links (GitHub, Hugging Face, `chatagent.ca`), noting the host had already injected `steward_map` | 1 (`steward_map`) | `3b9a693b-…` |

Probes A and B are the end-to-end proof of the §6.1 runtime-facts fix: **the local 3B model now names itself correctly, unprompted and without a host readout**, and `active: "local"` in the payload matches the footer the operator sees.

### 10.4b Cloud (API) path after the prompt refactor **[VERIFIED 2026-09-17]**

`wp_cloud_verify.py` drove the whole brain state machine against the running console:

| Step | Endpoint | Observed |
|---|---|---|
| health before | `GET /api/health` | `build v1.1-20260917api2`, `ok true`, `brain ready`, `selected qwen2.5:3b`, `last_brain local`, `cloud.mode local`, `enabled false`, `has_key true`, `degraded false` |
| arm the API | `POST /api/brain {"mode":"api"}` | `{"ok":true,"mode":"api",…,"enabled":true}` — provider `deepseek`, model `deepseek-chat`, local engine kept booted as standby |
| **API turn** | `POST /api/chat` (tools off) | **“deepseek-chat via the DeepSeek API — engine `https://api.deepseek.com`, with llama.cpp (`:11441`, qwen2.5:3b) booted as local standby.”** · `active: "cloud"` · `traces: []` · receipt `b32073d8-6d2a-4cc8-8aa0-d2525928f9b7` |
| back to local | `POST /api/brain {"mode":"local"}` | `{"ok":true,"mode":"local",…,"enabled":false}` — key retained, cloud off |
| **LOCAL turn** | `POST /api/chat` (same question) | **“Model: qwen2.5:3b · Engine: llama.cpp”** · `active: "local"` · receipt `c4e34f39-2cfe-4f00-8e5d-96cfcc1777d6` |
| health after | `GET /api/health` | `mode local`, `last_brain local`, `fallback null`, `handoff null` — left in the safe default |

Both brains now name themselves correctly, the API path advertises its local standby, and switching back leaves the key saved but inactive. This closes the last open §10.7 row for the cloud path.

### 10.5 Prompt/routing consistency **[VERIFIED]**

`compose_system()` measured in-process: **15,950 chars**, and a grep of that prompt for the cloud model name returns nothing while `mode: local` — the invariant asserted by the regression test in `tests/test_agent_io.py`.

### 10.6 Hosted portal **[VERIFIED]**

```
https://chatagent.ca/portal/     → HTTP 200  text/html
https://chatagent.ca/portal/portal.json → hosted_base: ""   (hosted inference not live)
```

### 10.7 Not yet verified (honest list)

| Item | Why it matters | How to verify |
|---|---|---|
| The 402 handoff after the prompt changes | proves the additive guarantee still holds post-refactor | stub a 402 again (`Temp/stub402.py`) and re-run |
| Embeddings | `:11442` is not listening; `/v1/embeddings` → `501` | boot the embed runner and re-probe |
| Public gateway under real traffic | rate limit, streaming, P0 on the public path | start `:9642`, drive 25+ requests from two origins |
| Browser session stability | one browser session died mid-run (`no close frame received or sent`) | re-drive the portal with a fresh session |
| Stick engine on a GPU host | the stick ships `ngl: 0, threads: 4` (CPU-only, ~80 s per cold turn) | set `ngl`/`threads` in the stick's `config/console.json`, re-boot, re-run `%LOCALAPPDATA%\Temp\lygo_usbclaw_final3.py` |
| Stick embeddings (`:11452`) | `nomic-embed-text:latest` is in the stick CAS but the embed runner is not booted | boot it from the stick CAS and re-probe `/v1/embeddings` |
### 10.8 USB stick acceptance — the whole stick, end to end **[VERIFIED 2026-09-17]**

Loaded through the real double-click launcher, then driven through the HTTP surface while a real
chat was generating on the stick brain. Receipt: `%LOCALAPPDATA%\Temp\lygo_usbclaw_acceptance_final3.json`
(script: `%LOCALAPPDATA%\Temp\lygo_usbclaw_final3.py`).

| Check | Observed |
|---|---|
| `GET :9651/api/health` | `ok=true, brain=ready, engine_present=true, selected=qwen2.5:3b, selected_source=manual, port=9651, bind=127.0.0.1, ram_auto=true, scan_n=15, tools=56` |
| Engine process (read live from its PID) | `llama-server.exe -m <USB_ROOT>\LYGO_BUILDER_KEY\lygo_llm_console\..\product\models\ollama\blobs\sha256-5ee4f07c… --host 127.0.0.1 --port 11451 -c 8192 -ngl 0 -t 4 -np 1 --jinja --metrics --alias qwen2.5:3b --api-key [REDACTED]` |
| Config honoured | `-ngl 0 -t 4 -c 8192` matches the stick's `config/console.json`; no host model path; no secret in the recorded argv |
| Concurrent reads — 6 threads `GET /api/notepad` | **622 requests, 0 failures** |
| Concurrent writes — 2 threads `POST /api/notepad` (`new` then `save`) | **132 requests, 0 failures** |
| Chat during that load | `51 plus 46 is 97.` in **84.2 s** — receipt `a6f19e9e-866d-4ef4-b352-cf5df88190cd` |
| **Second run, same harness, independent** | **1,650 reads + 368 writes, 0 failures**; chat `The answer to 51 plus 46 is 97.` in **81.4 s** — receipt `e89f89cd-9f52-4196-8151-3b51147241bc` |
| Both load runs combined | **2,772 requests (2,272 reads + 500 writes), zero non-200 responses** |
| Console after the load | `brain=ready`, index parses, **no leftover `.tmp` files**, both listeners still up |
| Stick hygiene | 31 test notes created → 31 deleted; **0** `ACCEPT` notes left behind |
| Suite, both trees, 3 runs each | `Ran 154 tests` — **OK** every run (kit 21.5 / 21.8 / 22.1 s · stick 23.7 / 32.0 / 32.8 s) |
| Proof the write defect is real (pre-fix pattern) | the old fixed `.json.tmp` pattern fails **7 of 8 threads**; the shipped `atomicio` helper fails **0** |

Suite command, from each kit root (`-s .` inside `tests/` reports *Start directory is not
importable* on this kit, because `tests/` deliberately has no `__init__.py`):

```
C:\Python313\python.exe -m unittest discover -s tests
```

One honest note about the harness: an earlier load test of mine fired 12 no-delay hot loops
(~98k requests) and 404'd on `/api/notes` (the real route is `/api/notepad`) — those failures
belonged to the harness, not the kit. The harness quoted above refreshes the way a person does
(one request per 200 ms per reader) and is the only load evidence used here.

---


### 10.9 Performance and GPU backends **[VERIFIED 2026-09-18]**

| Claim | Evidence |
|---|---|
| Tests | `Ran 227 tests` · `OK` — live tree **and** stick tree, byte-identical `src/`, `tests/`, `scripts/`, `config/` |
| The kit picks the GPU itself | `/api/health` on the stick: `backend=cuda`, `ngl=99`, `threads=16`, `engine_dir=...\engine\backends\cuda`, `reason=fits_vram`, `effective == planned` |
| The engine really ran on the GPU | the running engine answered a 3,314-token prompt at **4,300 tok/s** prompt eval and **101.9 tok/s** generation (CPU 4-thread baseline in the same session: ~82 / 14.5) |
| A broken GPU driver cannot break boot | a Vulkan build crashing in `nvoglv64.dll` was caught by the self-test, **not** applied, and the console came up on the shipped engine (verdict recorded as `bad`, `applied_overlays=[]`) |
| Nothing unproven sits in `engine/` | `fetch_engine.ps1 -List`: `applied to engine\: none (CPU)` while both `cuda` and `vulkan` sit in `engine/backends/` |
| Backends are tag-consistent | both built from the kit's own pin `b10988`, manifests carry file sha256, sizes and the source tag |

### 10.10 Dial-in and harden pass **[VERIFIED 2026-09-18]**

* **Default brain staged on the stick:** `qwen2.5-coder:7b` copied into the canonical CAS
  (`product/models/ollama`) — 4,466.1 MB, blob size exact, **sha256 verified** against the digest.
  The CAS now holds 7 manifests.
* **3.03 GiB reclaimed:** the top-level `models/ollama` CAS held 10 blobs that were byte-identical
  to the canonical CAS (same sha256 digests, same sizes). Duplicates removed, manifests archived;
  no unique byte was deleted.
* **Tests:** 247 cases, OK on both trees (`live` 26.4 s). The pass added `tests/test_api_default.py`,
  `tests/test_model_verdicts.py`, `tests/test_usb_selfcontained.py` and moved the RAM-auto
  expectations to the tool-rank policy.
* **Self-containment re-proved by simulation:** with `%USERPROFILE%` pointed at an empty folder
  (a PC that has never run ollama) the console still resolves the stick's own CAS and its models.
* **Harden pass receipt:** `%TEMP%\lygo_harden_pass_receipt.json` — suites, secret scan, ports,
  engine purity, backend manifests, house policy, hygiene and a five-boot matrix.

## 11. Defect ledger — what broke, why, and the rule that prevents it

Every entry is a real failure observed in this build. The "rule" column is the generalizable lesson — this is the most valuable part of the document for whoever builds next.

| # | Defect | Root cause | Fix | Rule |
|---|---|---|---|---|
| D1 | The console answered with a canned block (“I don't have a tool that reports my own backing weights…”) instead of the model's answer | `chat_loop.fallback_from_traces()` built a fixed template and `sanitize_assistant()` **replaced** the model's draft with it | template demoted to last-resort (model returned nothing usable) and made prose-only; `sanitize_assistant` no longer substitutes | **Host code supports the model's answer; it never authors the answer.** |
| D2 | Raw Python leaked into user-visible prose: `skills={'n': 18, 'enabled': 6}` | dict repr interpolated straight into text | `_human_skills()` renders a human phrase | Anything a human reads is formatted for a human. |
| D3 | The model parroted the same answer turn after turn | the canned template got written into session history and the 3B model echoed it | `same_answer()` detects a repeat → exactly one regeneration; `strip_boiler` removes the forbidden template lines; New chat clears poisoned history | **Poisoned history is poison.** Fix the source, then start a clean session. |
| D4 | `remember` writes never appeared in the model's prompt | `MEMORY.md` grows at the **bottom**; the prompt read only the head | `read_memory_block()` = head (protocol) + tail (newest notes); `_read_cap_tail()` | For an append-only memory file, read **both ends**. |
| D5 | A single note appeared **12 times** in `MEMORY.md`, crowding curated sections out of the window | no dedupe; repeated test runs appended the same note | `dedupe_notes()` collapses to newest stamp with `(repeated 12x)`, **before** the cap; `append_memory()` refuses identical notes | Dedupe **before** truncating, and refuse writes at the source. |
| D6 | The system prompt risked being evicted from an 8k-token window | unbounded prompt sections + unbounded history | `HISTORY_CHARS = 9000`, `trim_history()`, per-section caps, core-limbs-first catalog, `test_prompt_fits_the_engine_window` | **Budget the window explicitly and test the budget.** |
| D7 | The model could not say which weights or engine served the turn | no runtime facts in the prompt; the model was asked to infer what the host knew | `runtime_facts.prompt_block()`; `whoami` / `kernel_status` return live model/engine/build/brain/limbs | **Never make a small model guess a fact the host knows.** Inject it. |
| D8 | A **local** turn claimed “Model answering this turn: deepseek-chat via DeepSeek API” | routing honoured the per-request brain but `compose_system()` read only the **saved** mode → prompt and routing disagreed | brain threaded through the whole chain: `server.py` → `compose_system(brain)` → `prompt_block(brain)` → `answering(brain)`; regression test asserts a local prompt contains no `deepseek-chat` | **One source of truth per fact.** If two code paths decide the same thing, they must read the same input. |
| D9 | A good answer was discarded because one URL looked like a placeholder | blanket rejection of any answer containing a placeholder pattern | `_scrub_placeholders()` rewrites in place; only boiler-only answers are dropped | Repair in place before you discard. |
| D10 | Console showed no local models; the boot hung while PowerShell waited on the model | boot BAT raced the engine; stale console instances held the port | boot settle (`ping -n 2`) → sweep stale ports → verify `:11441` answering; `_ConsoleServer.allow_reuse_address = False` | **Verify readiness, don't assume it.** Never share a port with a stale process. |
| D11 | `curl :11441/props` → `401 Invalid API Key`; engine context unknown | engine API key guard | read `n_ctx` from `save/logs` instead of the live props endpoint | A mis-guarded endpoint is not evidence; find another witness. |
| D12 | A browser tool session died mid-run (`RuntimeError: no close frame received or sent`) | long-lived agent browser session instability | re-drive with a fresh session; keep probes short and batched | Long browser sessions are fragile — checkpoint your evidence as you go. |
| D13 | `POST /api/chat` with an unknown top-level key (`message`) **answered anyway** | the handler read only `messages[]` / `prompt`; anything else was ignored, so the model was fed a system-prompt-only turn and the user saw an unrelated reply | **FIXED 2026-09-17** — `chat_loop.normalise_messages()` accepts `messages[]` plus the `prompt`/`message`/`input`/`text` shorthands and drops non-object entries; a payload with no user text now returns `400 {"error":"no_input"}`, while image-only and content-list turns stay legal. Live proof: `{"foo":"bar"}` → 400 · `{"messages":[1,2]}` → 400 · `{"message":"Which model is answering this turn?"}` → 200 `qwen2.5:3b` · `{"prompt":"Say OK only."}` → 200 `OK`. 6 tests added (`ChatInputTests`), suite 108 OK | Validate input contracts loudly; never answer a request you did not understand. |
| D14 | Duplicate `OPENAI_API_KEY` in the agent host `.env`; one test leaks an `unclosed socket` ResourceWarning | environment/config hygiene | cosmetic; fold into the next cleanup pass | Keep the environment as clean as the code. |
| D15 | The stick console **died mid-chat**: `PermissionError: [WinError 32] … 'save\notepad\index.json.tmp' -> 'index.json'`, raised inside a request handler | all four persistence modules wrote through **one fixed temp name** (`.json.tmp`), so two request threads collided and `os.replace` threw | `src/atomicio.py` (unique temp per write + `fsync` + retry) wired into `notepad.py`, `continuity.py`, `skills_mod.py`, `workspace_map.py`; `_StickContainment` answers `500` instead of dying; console stdout/stderr `errors="replace"` | **A temp name two threads can both choose is a shared mutable resource.** Give every write its own scratch file. |
| D16 | Same index under live load: **4× HTTP 500 `WinError 5`** on the swap (console survived) | `os.replace` needs delete access on the target, and CPython's `open()` never requests `FILE_SHARE_DELETE` — while any reader holds the file the swap can **never** win | per-path lock, skip the rewrite entirely when the notes are unchanged, bounded retry, then a last-resort in-place write whose handle shares read+write+delete | **If a reader can veto your atomic swap, do not depend on the swap.** Keep a bounded second way that still lands the data. |
| D17 | Readers hit `PermissionError: [Errno 13]` on `index.json` — 2–4 times per suite run, 0 in a control run of 292 reads with no writer | the OS denies `open()` for the instant a swap touches the directory entry; no writer-side sharing flag can prevent it | `atomicio.read_text()` retries a transient denial for ~0.5 s on **every** state-file read (notepad index, skills state, workspace map, note/skill bodies); `FileNotFoundError` still raises immediately and is never retried | **Retry the side of the race you did not cause.** A user's refresh must not become a `500` because a writer was mid-swap. |
| D18 | A stick kit reusing the studio's ports would collide with whatever the host already runs (`8080` studio web apps, `11434` Ollama's own default) | kit defaults were inherited from the desktop build | stick contract `9651 / 11451 / 11452`, enforced by three tests in `tests/test_usb_portability.py` | **A portable kit must not assume a port is free** — claim one nobody else uses, and test the claim. |
| D19 | The stick's own `MODEL_MANIFEST.json` contradicted itself: `primary.name: llama3.1:8b` while `ollama_pull: … qwen2.5:3b` | two fields described one fact and were edited at different times | data-fixed to `qwen2.5:3b` (backup kept beside it); the live `selected` in `/api/health` is the authority | **A manifest that contradicts itself is believed by whoever reads the wrong field.** One fact, one field. |

---

| **D16** | A GPU backend DLL placed in `engine/` took the engine down on a host whose Vulkan driver is broken — `0xC0000005` inside `nvoglv64.dll`, at *any* `-ngl` including 0, CPU mode included (llama.cpp loads every `ggml-*.dll` it finds next to the exe). | Optional GPU builds live in `engine/backends/<name>/` and are copied into `engine/` **only** after a self-test has loaded a real model with them on this host; the verdict is remembered per host in `data/perf.json`. |
| **D17** | The backend self-test chose the smallest model in the CAS, which was an embedding model; loading it at `-ngl 99` with a chat alias dies with `0xC0000409`, and that crash was recorded as a per-host verdict — one bad probe model could have disabled a working GPU for good. | Probe with chat models only (`PROBE_SKIP_TOKENS`) and try every probe model before condemning a backend. |
| **D18** | Device probes were cached per exe path + mtime, and neither changes when a backend DLL lands beside the exe — so the pre-activation "no devices" answer survived activation and a good backend was rejected as device-less. | `perf.engine_devices(..., refresh=True)` after applying a backend; the regression test asserts the post-activation probe is always the fresh one. |
| **D19** | The verdict store is `perf.PERF_JSON`, but the tests patched `backends.DATA` — so the suite wrote `host-a`/`host-b` verdicts into the real kit store. | Tests patch the path they actually write through, plus a guard test asserting the store stays inside the patched data dir. |
| D20 | Every GPU plan advertised `flash_attn: true` while `spawn_runner()` never passed a flash-attn flag: the setting was unreachable and the health output was false | `engine.spawn_runner(flash_attn=…)` + `console.json "flash_attn"`, default **off** because `-fa on` measured slower (2,620 vs 3,246 tok/s) | A plan field is a promise. If it is not passed to the process, it must not appear in health |
| D21 | A GGUF whose header parses but which the engine cannot load (PrismML ternary offsets, wrong quant type, half-copied file) was **fatal**: `boot()` raised `engine_launch_failed` and the console stayed dark with `brain=error` | `src/model_verdicts.py` verdicts keyed to host + file (size+mtime) + `maybe_spawn` fallback to the next-best brain, surfaced as `model_fallback` in health | The scanner's "runnable" is a header opinion. Only a real load proves a model, and one such proof must not be repeated forever |
| D22 | The launcher declares `LYGO_MODELS`/`OLLAMA_MODELS`, but the console's scan roots ignored them — on a PC with no `%USERPROFILE%\.ollama\models` the console saw **zero** models while gigabytes of brains sat on the stick | `server.default_scan_roots()` trusts the declared roots and descends into a CAS parent (`<usb>\models` → `\ollama`) | A portable agent must read the paths its own launcher set, not the host's habits |
| D23 | `mmap=False` emitted `--no-mmap`, a flag this engine build no longer accepts: any caller would get an aborted launch, not a slower one | mapped to `-lm none`, the current spelling | Flags expire. Verify a flag against `--help` of the binary you actually ship |

| D24 | `POST /api/chat {"tools": false}` → **500 `handler_failed`**, `UnboundLocalError: cannot access local variable 'honest_pending'` — and `false` is what the portal sends when its "Agent limbs" box is unticked | `honest_pending`/`traces` were bound **inside** `if use_tools:` and read unguarded further down | hoisted above the branch; three tests drive the real handler over HTTP with a mocked engine; live: `tools:true`, `tools:false` and omitted all answer 200 | **A variable bound in one branch and read outside it is a 500 waiting for the other payload.** Test every documented payload shape, not the happy one. |
| D25 | `plan_ngl()` costed a model's weights but **nothing for the KV cache**, so a plan could promise layers the card cannot hold | the cache was treated as free | cache sized from the model's own GGUF header (`n_layer × n_kv_head × (key_len + value_len) × 2` = 57,344 B/token for this 7B) at the context the engine will actually run; a flat 256 MiB allowance only when the dims cannot be read | **A GPU plan that ignores the cache is arithmetic that lies.** Size it from the artifact, per model. |
| D26 | The same sizing used the model's **native 32,768** ctx while the engine runs clamped to 16,384 — a 2× over-charge (896 MiB) that could have demoted a GPU which fits | two places decided the context; only one of them clamped | sizing goes through the same `clamp_ctx()` the launch uses → 448 MiB, matching the card | **If two code paths decide the same quantity, they must share the function.** |
| D27 | `kv_mib: 256` — a flat fallback for *every* model: the GGUF header scan ended on `attention.head_count` before reaching `attention.head_count_kv`, so GQA models looked dimless | the break condition named the wrong key | break tightened to `head_count_kv`; a model with no such key is correctly read as MHA (head count *is* the KV count) — and that wrong expectation was itself caught by a test | **Parsing stops where you tell it to stop.** Verify a "missing" field is really missing before you code a fallback for it. |
| D28 | `data/.llama_api_key` held the literal string `secret-key`, and `ensure_llama_key()` returned a stored value forever: the engine's only guard on loopback was a published placeholder | the generator only ran when the file was *absent* — one shipped placeholder made it permanent | placeholders and sub-16-char values are treated as absent and replaced with a generated per-install key; the live engine's `--api-key` now matches the rotated file (43 chars) | **A placeholder that is read is indistinguishable from a real secret.** Refuse to serve defaults, not just avoid writing them. |
| D29 | `pytest -W error::ResourceWarning` was **not** green, and which test failed moved between runs: `372 passed, 24 warnings` one run, `1 failed, 371 passed` the next | the suite leaked resources — `test_openai_proxy` called `httpd.shutdown()` with no `server_close()`, and `test_backends.setUp` created a `TemporaryDirectory` per test (24 tests) that only the GC reclaimed, so an unraisable `Implicitly cleaning up <TemporaryDirectory …>` landed on whatever test was running when the collector fired | both released explicitly (`server_close()`; `addCleanup(self.tmp.cleanup)`); the strict run is now **372 passed, 0 warnings** across three consecutive runs | **A leak stops being cosmetic once warnings are errors: the test that fails is whichever one the GC interrupted, so the report names an innocent test.** Close what you open — especially in `setUp`. |
| D30 | Two suite runs at once failed **different** tests each time (`test_agent_io`, then `test_workspace_map`, then `test_notepad` once the first two were fixed), and the live kit had quietly accumulated **239 test rows in `workspace/memory.jsonl`** plus 120 dead temp paths in `save/workspace_map.json` | four tests wrote the operator's real state and tidied up by hand afterwards: two appended to the real `MEMORY.md` and rewrote the old text in a `finally`, one added and removed a mount in the live map, one wrote real notes into the live notepad. Concurrent runs interleaved on one file, and a killed run left the junk behind for good | each now runs against a throwaway state dir (`patch.object(continuity, "WORKSPACE", …)`, `workspace_map.MAP_PATH`, `notepad.NOTEPAD_ROOT/NOTES_DIR/INDEX_PATH`, released with `addCleanup`); **two concurrent strict suites both pass 372**, and the live files are identical before and after a full run | **A test must never write the operator's real state — tidying up in `finally` is not a transaction.** Isolate the path in `setUp`, and treat "two runs at once" as a supported case, not an accident |

## 12. Builder template — how to extend this kit

### 12.1 Ground rules for any builder (human or AI agent)

1. **Read before you write.** §8 is the map; Appendix B is the function index. Do not guess a symbol's name.
2. **One source of truth per fact.** If two paths decide the same thing, thread the value — never duplicate the decision (D8).
3. **The host never authors the answer.** Inject facts, pre-run limbs, repair in place; always let the model speak (D1, D7, D9).
4. **Back up before you edit.** Copy the file (or the whole `save/`/`config/` subtree) first. Never destroy steward data.
5. **Never commit or copy credential-bearing files** (`config/api.json`, `.env`, `save/registry.json`). Write `[REDACTED]` in any output.
6. **Never publish for the steward.** No push to GitHub/HF/ClawHub/social, no deployment, no live Star Chart write — that is a human decision.
7. **Never report a receipt you did not get.** If a tool call did not return success, say so.
8. **Extend the test suite with the change.** A behavior change without a test is not done.
9. **Keep the local brain complete.** Any API feature must degrade to local, never the reverse.
10. **Match the local style:** type hints, small functions, docstrings that state the *contract* (“who generates the tokens this turn…”), module docstrings that state the *prohibition* (“Never echo secrets.”).

### 12.2 Definition of done (every change)

```
[ ] code change is minimal and in the module whose role matches §8.1
[ ] backup of every file touched
[ ] a test in tests/ exercises the new behavior (and fails without the change)
[ ] cd tests && python -m unittest discover -s . -p "test_*.py"  →  OK, exit 0, count >= 102
[ ] /api/health still reports ok, brain ready, engine_present true, 63 limbs, scan_n 14
[ ] one live /api/chat turn proves the operator-visible behavior
[ ] docs updated: README.md (if orientation changed) and WHITEPAPER.md (this record)
[ ] no credential in any file, log, prompt, or answer
```

### 12.3 Recipe: add a limb (a callable tool)

1. Implement it in the module that owns the domain (`limbs.py` for extras, `web_tools.py` for web, `tools.py` core).
2. Add it to the schema so the model can see it: `tools.core_schema()` (and the catalog that feeds `runtime_facts.limb_catalog`, cap 2,200 chars — **core limbs first**).
3. Route it in `tools.dispatch()`; keep the argument shape `{name, arguments}`.
4. Deny by default: any filesystem limb passes `_denied` / `_under` checks; the public gateway gets **no** limbs.
5. Add a test in `tests/test_tool_battery.py` or `tests/test_tools.py`.
6. Expect `tools[]` in `/api/health` to grow by one — the whitepaper's “56” becomes 57; update §1.1.

### 12.4 Recipe: add a skill or a champion seat

1. Write `SKILL.md` (front-matter + body) in a skills root (`save/skills`, or an extra root registered via `add_root`).
2. `skills_mod._parse_skill_md` must parse it — check `catalog()` lists it and `prompt_catalog()` includes it within 1,500 chars.
3. For a **seat** (a named character/role the operator can summon), add an entry to `CHAMPIONS` and make `match_invoked()` resolve it; the most specific name wins.
4. Seat guardrails go in the seat text itself (e.g. LYRA-Δ9: *never publish for the steward; never report a receipt you did not get*).
5. Test in `tests/test_skills.py`.

### 12.5 Recipe: add a route to the studio console

1. Add the branch in `server.py::Handler.do_GET` / `do_POST` (keep the existing `_auth` → `_read_body` → validate → act order).
2. Use `self._json(code, obj)` for responses; cap the request body (4 MB like `/api/chat`, 413 over it).
3. Decide auth: loopback-public (`_ok_public`) or token (`_auth`). Default = token.
4. If it changes the agent's mind about the world, update `runtime_facts` so the model sees it too.
5. Test it in the suite (see `tests/test_agent_io.py` for the in-process server pattern).

### 12.6 Recipe: add a brain provider

1. Extend `cloud_api` (or add a sibling module) with `chat()` + a status view — **never** return the key.
2. Every provider needs: `enabled`, `has_key`, `model`, `label`, `degraded`, `last_code`, `last_error` so the operator surface stays uniform.
3. Wire failure reasons through `brain_router.should_fallback()` / `reason()` so handoff stays automatic.
4. Keep the provider additive: local must still answer with the provider absent, disabled, or out of credit.
5. Test in `tests/test_brain_switch.py` and `tests/test_cloud_api.py`.

### 12.7 Recipe: change the portal

1. Edit `web_portal/` (public) or `portal/` (studio) — remember they are currently two trees (gap P5).
2. If it is a new backend capability, it belongs in `public_gateway.py` **with** a rate limit, a size cap, and P0 on input and output.
3. Never add tools, disks, shell, or `admin.json` reach to the public path.
4. Re-test: `tests/test_public_gateway.py`, then a live request against `:9642`.

### 12.8 Prompt-composition checklist (before you touch any prompt text)

```
[ ] does this fact change per turn?         → inject it via runtime_facts, not prose
[ ] does it change per brain?               → thread the brain argument (D8)
[ ] is it longer than its cap?              → raise the cap or shorten the block; never let it grow unbounded
[ ] does it tell the model what to claim?   → it must state *facts*, not claims about itself
[ ] does the engine window still fit?       → run test_prompt_fits_the_engine_window
[ ] does it survive trim_history()?         → the system prompt must never be trimmed away
```

### 12.9 Handing this kit to another AI agent

Give the agent, in this order:

1. This whitepaper (§8 map, §12 recipes, §11 rules).
2. `BUILD_MANIFEST.json` (machine-readable ports, routes, modules, tests, invariants).
3. The verification commands in §0.3 — **require it to run them before it changes anything**, so it starts from a green baseline.
4. The task, expressed as one roadmap step from §14 with its acceptance test.

Then hold it to §12.2. An agent that cannot show the suite green and a live turn did not finish the job.
---

## 13. Known limitations and risks (nothing hidden)

| # | Limitation | Severity | Mitigation today | Real fix |
|---|---|---|---|---|
| L1 | Default brain `qwen2.5:3b` is small: it can drift on long or abstract asks | M | facts injected, limbs pre-run, context capped, prompt truthful | ship a per-turn model chooser (14 models are already visible to the console) |
| L2 | Hosted portal mode is not live (`hosted_base` empty) | **H** | visitors can use HF / their own console / custom URL | §14 step 1 |
| L3 | Studio UI and public UI are duplicated trees | M | both work | shared component (gap P5) |
| L4 | Image-only turns are accepted (`has_image` is true) but the default local engine `qwen2.5:3b` has no vision — such a turn degrades to text handling | M | send an image-only payload: it answers without seeing the picture | route image turns to a vision-capable engine, or answer `503 no_vision` instead of pretending |
| L5 | Embeddings endpoint is `501` (embed runner not booted) | L | chat works without it | boot the embed runner when a feature needs vectors |
| L6 | USB master at `<USB_ROOT>\LYGO_BUILDER_KEY\lygo_llm_console` is stale (build `v1.1-20260916m`, 47 tests, py3.12, no brain switch, no LYRA seat) | **H** | this whitepaper + docs are copied to the USB | decide sync direction (`I:→E:` or `E:→I:`) and resync the kit |
| L7 | Engine context is 16,384 tokens (the model itself offers 32,768), and the console's own ceiling is 16,300 chars — a document that must reach the engine whole still will not fit | M | memory/notes are the long-term store; caps keep the window honest | the window is no longer the binding limit; raise `PROMPT_CEILING` and the per-section caps together, then re-run the budget test |
| L8 | A long-lived studio window can keep serving pre-fix code after a `src/` edit (D10) | M | compare the console PID start time with the newest `src/*.py` mtime, then restart | have `/api/health` report a `stale` flag when `src/` is newer than the process |
| L9 | Public gateway has no per-day quota or ban list | M | 24 req/10 min/IP, 4,000 chars, P0 both ways | gap P4 |
| L10 | One test leaks a socket `ResourceWarning`; duplicate `OPENAI_API_KEY` in host `.env` | L | harmless | cleanup pass |
| L11 | `config/local.json` still lists two `U:` roots that do not exist on this machine | L | scanner skips missing roots | remove or remap |
| L12 | Secrets handling depends on discipline: `config/api.json` holds the cloud key in clear text on disk | M | gitignored, never copied to USB, `[REDACTED]` in all docs, `credential_where` returns paths only | OS-level secret store or encryption at rest |

---

## 14. Roadmap — one step at a time

Each step is independently shippable, has an acceptance test, and leaves the console in the state the previous step proved.

| Step | Goal | Acceptance test |
|---|---|---|
| **1** | **Make the hosted portal real**: run `public_gateway.py :9642` on the Stream PC, reverse-proxy under HTTPS, set `hosted_base` | `curl` the public hosted `/v1/models` through HTTPS → model list; one visitor-mode answer from `https://chatagent.ca/portal/` |
| **2** | Harden the public path: per-day quota, IP block, quota counter in the UI, friendly 429 | 25 requests from one IP → 429 with the friendly message; a second IP unaffected |
| **3** | ✅ **cloud path re-verified 2026-09-17** (§10.4b: receipts `b32073d8…` cloud / `c4e34f39…` local, re-run again after the D13 fix). The 402 handoff re-run is **deferred to step 11 on purpose**: the only way to point the API brain at a stub today is to edit the credential file `config/api.json`, which this build forbids | answer names `deepseek-chat` ✅ · LOCAL answer names `qwen2.5:3b` ✅ · 402 → local answers with the handoff banner + receipt (proven live in §10.4) ✅ |
| **4** | Ship the model chooser (local side + public whitelist) | switch model in the studio → next turn answers with the new model named in the footer and in the prompt |
| **5** | De-duplicate the two UIs into one shared chat/brain component | studio and portal both render from the shared module; suite still green |
| **6** | ✅ **DONE 2026-09-17** — D13 fixed: `chat_loop.normalise_messages()` honours `messages[]`/`prompt`/`message`, and a payload with no user text returns `400 no_input` (§11 D13) | `{"foo":"bar"}` → 400 `no_input` ✅ · `{"message":"…"}` → 200 `qwen2.5:3b` ✅ · 108 tests OK ✅ |
| **7** | **Sync the kit to the USB** in the direction the steward chooses, and stamp the build on both | `E:\…\lygo_llm_console` reports the same build + test count as `I:` |
| **8** | Long-context pass: raise `-c`, re-budget the prompt, re-run the budget test | suite green with a larger window; measured prompt+history still fits |
| **9** | Host/engine cleanup: remove stale `U:` roots, fix the duplicate env key, silence the socket warning | suite green with zero warnings; health unchanged |
| **10** | Teach the console to publish nothing and prove it: a self-check that reports which files are credential-bearing and refuses to export them | `self_check` run in the portal shows the credential file list with `[REDACTED]` values and a refusal to export |
| **11** | Add a credential-safe cloud endpoint override (env var or a non-secret settings key) so the 402 handoff and any provider change can be tested without touching `config/api.json` | with the override set to a 402 stub, the turn answers locally with the handoff banner + receipt, and `config/api.json` is never read, printed or written |

**Sequencing rule:** do not start step *n+1* before step *n*'s acceptance test passes and the suite is green. Working product on a stable base — one step at a time.

---

## Appendix A — Configuration and identity files

### A.1 `config/console.json` (ports and scan roots)

| Key | Value (live) | Meaning |
|---|---|---|
| `port` | `9641` | studio console |
| `llama_port` | `11441` | local inference runner |
| `embed_port` | `11442` | optional embeddings runner |
| `scan_roots` | `["./models", "%USERPROFILE%/.ollama/models"]` | where models are discovered |
| `ctx_max` | `16384` | engine context cap; the KV cache is sized from the model's own GGUF header at this clamped value |
| `kv_type` | `q8_0` | KV cache quantisation — 2× context at the same VRAM (448 MiB, what 8,192 + f16 cost) |

### A.2 `config/local.json` — extra model roots

Live value contains two `U:/LYGO/...` roots that do not exist on this machine (see L11). Edit this file to point at any additional model directories; `config/local.json.example` shows the shape.

### A.3 `config/api.json` — the cloud key **[REDACTED]**

Holds the provider key. **Never copied to the USB, never printed, never committed, never placed in a prompt.** `cloud_api.sanitize_key()` exists so no path can echo it; `credential_where` returns the *path*, not the value.

### A.4 `config/admin.json` — the steward map (not public git)

`src/admin_map.py` loads it **only if the file exists**. It supplies:
- `read_roots` / `write_roots` / `search_roots` — the operator's mounts
- `links` — org URLs (GitHub, Hugging Face, lattice, portal, skill hub)
- `credential_pointers` — **paths** to credential files, never values
- `drives`, `paths_of()`, `brief()` / `brief_text()` — the human-readable map injected as `steward_map`
- `is_placeholder_url()` — guards against invented URLs reaching an answer

Overlaid at runtime by `workspace_map.py` (operator-editable mounts, with `_blocked_write` and a revoked set).

### A.5 Identity and continuity

| File | Role | Cap in the prompt |
|---|---|---|
| `prompts/LYGO_ALIGN.txt` | alignment / protocol text (`align.load_align`) | as-is |
| `workspace/SOUL.md` | the agent's soul | 2,400 chars |
| `workspace/IDENTITY.md` | who the agent is | 1,400 chars |
| `workspace/MEMORY.md` | curated long-term memory + append-only notes | 3,200 chars (head + tail, deduped) |
| `prompts/*` | **seed** versions copied on a fresh install (`install.seed_identity`) | — |
| `save/` sessions | per-session transcripts (`continuity.load_session` / `save_session`) | trimmed to 9,000 chars |

### A.6 Build identity (printed by the kit)

```
signature : Δ9Φ963-LYGO-LLM-CONSOLE-v1
build     : v1.1-20260917api2
```

---

## Appendix B — Complete module and function index

> **Regenerate me after any code change.** `tools/make_index.py` (shipped in the kit) walks `src/` and `tests/` with `ast` and prints this index. This snapshot predates the D13 fix, which added `chat_loop.normalise_messages()` and `chat_loop.user_text_of()` plus the 6 `ChatInputTests` cases — re-run the tool to refresh the counts.

*Auto-generated from the live source tree on 2026-09-17 by AST scan (see §0.3 for the command). Format: module — lines/bytes — module docstring, then each top-level function with its contract docstring.*

### `src/` — 39 python modules, 12840 lines total

**`src/__init__.py`** — 4 lines / 103 bytes — LYGO LLM Console — Δ9Φ963-LYGO-LLM-CONSOLE-v1

**`src/admin_map.py`** — 421 lines / 15651 bytes — Admin unlock map. Loaded only if config/admin.json exists (not in public git).
  - `invalidate`
  - `load`
  - `is_admin`
  - `_usb_root` — The builder-key root when one resolves, else '' - usb_root_status() carries the reason.
  - `_chatagent_candidates`
  - `chatagent_root_status` — Resolve the chatagent tree and say which candidate won or why each was skipped.
  - `chatagent_root` — The chatagent tree, or a named-not-found path when this host has none.
  - `_usb_record`
  - `usb_resolution_log` — Every candidate the last builder-key root resolution considered, in order.
  - `_usb_marker` — A reason string when root really is the builder-key tree, else ''.
  - `_usb_candidates`
  - `_warn_usb` — One hard stderr warning per distinct reason - never a silent wrong-tree fallback.
  - `usb_root_status` — Resolve the LYGO_BUILDER_KEY tree and say exactly what happened.
  - `path_warnings` — Unresolved-root warnings for status surfaces.
  - `_expand_path`
  - `paths_of`
  - `read_roots`
  - `write_roots`
  - `search_roots`
  - `links`
  - `credential_pointers`
  - `drives` — Drive roles. Config wins; the fallback describes roles without pinning another host's
  - `is_placeholder_url`
  - `brief`
  - `brief_text`

**`src/align.py`** — 26 lines / 930 bytes
  - `load_align`

**`src/atomicio.py`** — 155 lines / 6157 bytes — Atomic file writes that survive a stick.
  - `_lock_for` — One lock per target path: two writes to the same file must not race the swap.
  - `_transient` — WinError 32/33 (in use / lock violation) are worth waiting out; nothing else is.
  - `_write_in_place` — Non-atomic rewrite that still lets concurrent readers open the target.
  - `read_text` — Read text, waiting out a transient lock another handle holds on the file.
  - `atomic_write_text` — Write text to path atomically, safe for concurrent writers and locked targets.
  - `_write_locked`

**`src/auth.py`** — 98 lines / 3061 bytes
  - `_chmod600`
  - `ensure_token`
  - `ensure_llama_key` — The engine's API key: generated per install, stored 0600, reused once it is a real key.
  - `check`
  - `token_from_request`

**`src/backends.py`** — 736 lines / 29072 bytes — LYGO Backends — which engine build actually runs on this host.
  - `base_engine_dir`
  - `backend_dirs` — Installed backend directories, name -> path. Unreadable store means no backends.
  - `backend_kind` — 'engine' when the dir is a complete engine build, 'overlay' when it is DLLs for one.
  - `_sha256`
  - `_manifest`
  - `backend_files` — The files this backend owns — overlay: its backend DLLs; engine: its executables.
  - `backend_info` — Everything the planner needs about one backend: kind, files, size, integrity.
  - `backend_key` — Verdict identity: changes when the backend's files, size or tag change.
  - `installed`
  - `host_looks_nvidia` — Cheap vendor hint for ordering: the NVIDIA tools ship with the driver.
  - `order_candidates` — Which backend to try first here: CUDA on an NVIDIA device, else the usual order.
  - `_active_read`
  - `active_record` — The backend currently applied to the engine dir, as last written by ensure().
  - `set_active`
  - `clear_active`
  - `activate` — Make a backend usable. Overlay: copy its DLLs into engine/. Engine: nothing to copy.
  - `deactivate` — Undo activate() for an overlay: remove exactly the files the backend owns.
  - `applied_overlays` — Backends with files sitting in engine/ — by store manifest and by file pattern.
  - `verdict_store_path`
  - `backend_verdict`
  - `remember_backend` — Record one backend's verdict for one host. Atomic, bounded, never raises.
  - `_health_ok`
  - `_port_free`
  - `self_test` — Load a real model with this backend and see whether the process survives it.
  - `probe_models` — Smallest-first model candidates a self-test may use, skipping giants.
  - `engine_dir_for` — Where llama-server.exe lives when this backend is active.
  - `ensure` — Decide the engine dir and backend for this host, proving GPU use before claiming it.
  - `_cli` — python src/backends.py [status|list|test|drop|apply] — for receipts and debugging.
  - `report` — Health view of the backend layer. Never raises.

**`src/brain_router.py`** — 119 lines / 3738 bytes — Local-first brain routing for the LYGO LLM Console.
  - `_code`
  - `should_fallback` — True when a cloud failure should be handed to the local engine.
  - `reason` — Short operator-facing 'why the API did not answer'.
  - `mode_of` — Persisted brain mode: API only while a key is saved, switched on, and not in handoff.
  - `label_of`
  - `handoff_info` — Record for the UI/receipt/health payload describing one local takeover.
  - `banner` — Line prepended to the answer that the local engine produced instead of the API.

**`src/chat_loop.py`** — 806 lines / 32956 bytes
  - `math_expr` — The arithmetic inside a plain question, in a form `calc` can evaluate.
  - `math_only` — True when the whole message is a bare arithmetic question and nothing else.
  - `extract_user_text`
  - `normalise_messages` — Accept every payload shape a caller might send and return a real messages[] list.
  - `user_text_of` — Text of the newest user turn that actually carries text (else "").
  - `has_image`
  - `_args`
  - `extract_tool_calls`
  - `extract_urls`
  - `_flat` — One-line rendering of a tool result value (never a dict repr — a small model parrots those).
  - `tool_prose` — Readable summary of the newest tool result — the answer of last resort when the model echoed
  - `is_tool_call_echo` — True when the whole reply is a tool call (or a dump of one) with no prose around it.
  - `tool_names` — Every limb name plus its aliases (read -> read_file).
  - `named_tool` — The limb the operator asked for by name ("use the weather tool"), else "".
  - `_operator_args` — Args taken only from the operator's words. None = not enough there; answer honestly.
  - `auto_limb` — Args to run `name` on the host when the model would not call it. None = do not run it.
  - `tool_card` — One-limb instruction with the exact schema — the retry when a named limb was not called.
  - `host_prefetch` — 3B models talk about tools instead of calling them. Host runs URL/search/map first.
  - `_compact_trace`
  - `prefetch_message` — What the model sees when the host already ran the limbs for this turn.
  - `_human_skills` — `{'n': 18, 'enabled': 6}` is a dict repr; humans (and the transcript) want a phrase.
  - `fallback_from_traces` — Last-resort host readout — ONLY for a turn where the model returned nothing usable.
  - `_scrub_placeholders` — Rewrite an invented placeholder in place instead of throwing the whole answer away.
  - `strip_boiler` — Drop the template lines the prompt forbids, keep everything the model actually said.
  - `_is_only_boiler`
  - `sanitize_assistant` — Clean the model's answer. It never gets replaced by a canned string any more.
  - `same_answer` — True when the model just repeated its previous answer instead of answering the new turn.
  - `trim_history` — Keep the newest turns inside the engine's window so the system prompt always survives.
  - `run_tools_round`

**`src/cloud_api.py`** — 422 lines / 16595 bytes — Cloud API brain for the admin console. Keys stay in gitignored config/api.json. Never echo secrets.
  - `sanitize_key`
  - `_blank`
  - `_load`
  - `save`
  - `public_status`
  - `chain_for` — Ordered candidates: the primary (provider + key) first, then every keyed fallback.
  - `enabled`
  - `_post`
  - `_note_chain` — Record which keys were attempted (codes only — never key material).
  - `_note_success`
  - `chat` — One API turn, walked down the wired key chain.
  - `active` — API usable *right now*: key saved, switched on, and not in a handoff.
  - `note_error` — Record that an API turn failed; the console hands the next turns to local until cleared.
  - `clear_error` — Operator re-activation: forget the handoff so the API is tried again.
  - `cooldown_active` — True while a recent handoff should keep the console from calling the API again.

**`src/colibri.py`** — 167 lines / 5493 bytes — Optional Colibri engine (JustVugg/colibri). MoE experts stream from SSD. Not llama.cpp.
  - `resolve_coli`
  - `looks_like_colibri_model`
  - `model_card`
  - `spawn_colibri`
  - `status`

**`src/continuity.py`** — 287 lines / 9727 bytes — SOUL.md + IDENTITY.md + MEMORY.md + session history so the agent can grow and reference.
  - `soul_path`
  - `identity_path`
  - `memory_path`
  - `ensure_identity`
  - `_read_cap` — Prefer the head (protocol). Tail logs are not required every turn.
  - `_read_cap_tail` — Head (protocol) + tail (what `remember` just wrote).
  - `_split_note` — `- (2026-09-17 17:05) note text` → (stamp, note), else None.
  - `dedupe_notes` — Collapse repeated `remember` lines, keeping the newest stamp.
  - `read_memory_block` — MEMORY.md for the prompt: head (protocol) + tail (newest notes), de-duplicated first.
  - `_fits` — Shrink the MEMORY.md block - never SOUL/IDENTITY - until the prompt fits the engine window.
  - `compose_system`
  - `append_memory`
  - `load_session`
  - `save_session`
  - `new_session`

**`src/engine.py`** — 597 lines / 20960 bytes
  - `binary_forbidden`
  - `resolve_binary`
  - `class MEMORYSTATUSEX`
  - `available_ram_bytes`
  - `ram_ok`
  - `_clean_env`
  - `_assign_job`
  - `class Runner`
  - `_port_lock`
  - `_log_keep_bytes` — Newest bytes of each server log to keep; LYGO_ENGINE_LOG_MAX_BYTES=0 disables rotation.
  - `_open_engine_log` — Open this port's server log for append, dropping all but the newest LOG_KEEP bytes.
  - `_close_log` — Close a server log handle exactly once: on Windows the open handle locks the log file.
  - `_log_note` — Best-effort one-liner into save/logs/engine.log - why the engine refused to do something.
  - `kill_tree` — Kill pid and everything it spawned (taskkill /T /F; job objects only cover our own jobs).
  - `process_image_name` — Full image path of a live pid, or None when it cannot be queried (gone, or access denied).
  - `pid_is_our_engine` — True only when pid is alive AND its image really is our llama-server.exe.
  - `kill_pid` — Kill a pid recorded in the pid file, but only after proving it is really our engine.
  - `kill_recorded_pids` — Stop every engine pid in engine.pid.json, skipping any pid we cannot verify.
  - `stop_runner`
  - `stop_port`
  - `clamp_ctx` — Context window: model-native (else the config default), capped by the config ctx_max.
  - `clamp_threads` — CPU threads: a pin when given, else every core the host offers; always 2..16.
  - `clean_kv_type` — A KV cache type the engine actually accepts, else '' (= let llama.cpp decide).
  - `spawn_runner`
  - `_health`
  - `_write_pids` — Mirror the live runners into engine.pid.json, atomically.
  - `ollama_port_open`
  - `runner_for`

**`src/gguf_header.py`** — 199 lines / 6177 bytes
  - `class Truncated`
  - `class Cursor` —  | methods: __init__, need, u32, u64, string
  - `_skip_value`
  - `parse_gguf_header`
  - `write_tiny_gguf` — GGUF v3, tensor_count=0, kv general.name=tiny, general.architecture=llama.

**`src/image_tools.py`** — 78 lines / 2507 bytes — Image limbs: save, inspect, page thumbnail. No extra pip.
  - `_in_ws`
  - `image_info`
  - `image_save`
  - `image_list`
  - `page_thumbnail`

**`src/install.py`** — 212 lines / 7571 bytes — Public first-run for LYGO LLM Console. Never copies admin.json or steward vaults.
  - `is_admin_tree`
  - `_write_if_missing`
  - `seed_identity` — Copy public prompts into workspace. Skip if this is the steward admin tree unless force_public.
  - `ensure_layout`
  - `write_first_run`
  - `python_ok`
  - `engine_present`
  - `fetch_engine`
  - `report`
  - `main`

**`src/limbs.py`** — 582 lines / 31422 bytes — Extra agent limbs. Names avoid forbidden source tokens in tools.py.
  - `_safe_arith`
  - `canonicalize` — Fill a limb's canonical argument names from the aliases a small model reaches for.
  - `_win_env`
  - `_ws`
  - `_kill_tree` — Kill a pid and everything it spawned.
  - `_run_capture` — Run argv, capturing text output; on timeout kill the whole process TREE.
  - `extra`

**`src/lygo_engine.py`** — 432 lines / 17551 bytes — LYGO Engine — hybrid brain.
  - `cpu_threads` — One thread policy for the whole kit — see perf.auto_threads().
  - `vram_free_bytes`
  - `backend_selection` — Which engine build this host may use, straight from the backend layer.
  - `probe`
  - `_is_colibri`
  - `_is_moe`
  - `flash_attn_on` — console.json/local.json "flash_attn": "on" forces the flag; otherwise the measured default.
  - `plan` — VRAM / RAM / SSD placement. Does not silently change precision.
  - `_drop_backend` — Retire a GPU backend that just killed the engine on this host. Returns '' if there was none.
  - `boot` — Spawn the planned backend. Returns brain status string.
  - `status`

**`src/model_verdicts.py`** — 122 lines / 4233 bytes — Models THIS host has already proved it cannot load.
  - `_host`
  - `_fingerprint`
  - `_load`
  - `_save` — Write through the kit's atomic writer.
  - `entries`
  - `_still_valid`
  - `bad_ids` — Ids this host proved unloadable, where the verdict still describes the file on disk.
  - `is_bad`
  - `mark_bad`
  - `clear` — Forget one model's verdict, or every verdict for this host.

**`src/notepad.py`** — 166 lines / 5689 bytes — Standalone console notepad. Files live under save/notepad (kit-local, not MEMORY.md).
  - `ensure`
  - `_ok_id`
  - `_note_path`
  - `_load_index`
  - `_save_index`
  - `_rebuild_index`
  - `list_notes`
  - `read_note`
  - `write_note`
  - `delete_note`
  - `new_note`

**`src/ollama_import.py`** — 116 lines / 4560 bytes
  - `_blob_path`
  - `_display_id`
  - `import_cas_tree` — Read-only Ollama CAS. Never subprocess ollama.

**`src/openai_proxy.py`** — 54 lines / 1608 bytes
  - `llama_chat`
  - `llama_chat_stream`

**`src/p0_hook.py`** — 151 lines / 4237 bytes
  - `_try_import`
  - `_init`
  - `_policy`
  - `gate_output_window` — Policy regex only (no physics). QUARANTINE aborts the stream.
  - `gate_prompt`

**`src/p3_note.py`** — 45 lines / 1083 bytes
  - `vortex_signature`

**`src/paths.py`** — 323 lines / 12062 bytes
  - `under_workspace` — Resolve a limb-supplied path against the workspace.
  - `engine_dir` — The engine directory to launch: an activated GPU backend build, else engine/.
  - `_port_from_env` — Ports are env-overridable so a USB stick can run *beside* a desktop console.
  - `console_cfg` — Merged config/console.json + config/local.json (local wins). Cached, never raises.
  - `_cfg_int` — Config integer. An explicit number is a pin (0 included); "auto"/absent means
  - `_cfg_str` — Config text value, lowercased. Absent or junk means the default.
  - `console_limits` — Engine launch limits from the kit config.
  - `ensure_dirs`
  - `_record`
  - `resolution_log` — Every candidate the last stack-root resolution considered, in order, with its verdict.
  - `_stack_marker` — A reason string when root really is the protocol stack, else ''.
  - `_as_path`
  - `_stack_candidates`
  - `stack_root_status` — Resolve the protocol-stack root and say exactly what happened.
  - `stack_root` — The protocol-stack root. Never None - stack_root_status() carries the reason.

**`src/perf.py`** — 505 lines / 21240 bytes — LYGO Perf — host-adaptive launch profile for the engine.
  - `engine_path` — The engine directory in play: an explicit one, else whatever is active for this host.
  - `engine_backends` — GPU backends this engine build actually ships — a file scan, not a hope.
  - `parse_devices` — Parse `llama-server --list-devices`: 'Vulkan0: NVIDIA GeForce RTX 4060 Ti (7949 MiB, 7181 MiB free)'.
  - `engine_devices` — Ask the engine itself which devices it sees. Never raises: [] means none we can use.
  - `best_device` — The device with the most free VRAM — where the layers would go.
  - `gpu_free_mib` — Free VRAM on the best device, 0 when there is none. Never raises, never over-claims.
  - `auto_threads` — Every core the host offers, one left for the console, capped for sanity.
  - `clamp_threads` — CPU threads for llama-server: a pin is honored, else the host's own cores.
  - `sanitize_kv_type` — A KV cache type this kit will plan around, else 'f16' (the engine's own default).
  - `kv_bytes_per_token` — KV cache bytes for ONE token, read from the model's own GGUF header.
  - `kv_cache_mib` — KV cache size in MiB for a context. An unknown model gets the flat allowance.
  - `plan_ngl` — How many of 99 layers fit in the free VRAM. Returns (ngl, reason).
  - `_legacy_ngl` — Pre-adaptive rule, kept for callers that cannot see a device list.
  - `host_id` — This PC, for backend verdicts: no model, no live memory — the machine itself.
  - `fingerprint` — Host identity: a stick carried to another PC must not inherit this PC's verdict.
  - `_load_store`
  - `host_record`
  - `remember_host` — Merge one host's launch outcome. Atomic write, bounded history, never raises.
  - `_pin_int` — An integer pin, or None. "auto", "", junk and a typo all mean "let the host decide".
  - `resolve` — Probe facts + config pins + this host's memory -> the launch profile.
  - `ladder` — Launch attempts, best first, CPU last: a GPU that cannot load must not kill the brain.
  - `report` — The /api/health view of the launch profile. Never raises: health must always answer.

**`src/public_gateway.py`** — 299 lines / 12723 bytes — Public LYGO inference gateway — chat only, P0, rate-limited, no disks/shell.
  - `_warn_degraded_once`
  - `_cors_ok`
  - `_rate`
  - `_ollama_chat`
  - `_openai_chat`
  - `class Handler` —  | methods: log_message, _origin, _send, _json, do_OPTIONS, do_GET, do_POST
  - `main`

**`src/receipts.py`** — 187 lines / 7194 bytes
  - `_events_path`
  - `_todos_path`
  - `_keep_from_env` — Positive int from the environment, else *default* (garbage is ignored, not fatal).
  - `_prune_receipts` — Delete all but the newest *keep* receipt files, oldest first.
  - `_prune_lines` — Keep only the newest *keep* lines of a jsonl store, rewritten atomically.
  - `prune_state` — Bound the on-stick stores; returns counts/sizes so a caller can report them.
  - `prune_at_startup` — Explicit startup prune (counts/sizes returned); safe to call before the first turn.
  - `_maybe_prune` — Periodic prune after a write: throttled, and never allowed to fail a turn.
  - `create_node`
  - `write_receipt`

**`src/registry.py`** — 388 lines / 15166 bytes
  - `registry_backup_path` — registry.json.bak beside the live file (computed live: tests patch REGISTRY_PATH).
  - `load_status` — What the last load() did — public so callers can surface a recovery.
  - `_log` — Loud, dependency-free: stderr plus a line in save/logs/registry.log.
  - `tool_rank` — How well this model does agentic TOOL CALLING — the console's actual job.
  - `_bad_on_this_host` — Ids this host already proved it cannot load. A missing memory module is never fatal.
  - `_chats`
  - `ranked` — Every usable brain, best first: PREFER_IDS order, then tool rank, then size.
  - `candidates` — Ordered ids to try as the brain: the default first, then every fallback.
  - `avail_ram_bytes` — Available physical RAM, or 0 when it cannot be read (0 = 'unknown', never a refusal).
  - `vram_free_mib` — This host's free VRAM (0 when there is none). Lazy + fail-safe: registry stays GPU-agnostic.
  - `_ram_floor_bytes` — Half of INSTALLED RAM — a deterministic floor under the momentary-free measurement.
  - `prefer_by_ram`
  - `ram_choice` — Best tool-capable brain this host can actually RUN WELL; None when RAM/sizes are unknown.
  - `_fits` — Would this model fit here? Unknown sizes/RAM return True — never move a human's pin blind.
  - `pick_default` — Deterministic default (PREFER_IDS, else smallest). RAM-auto is opt-in via `prefer_ram`.
  - `_present` — Only advertise a model whose weights are really on this machine.
  - `_read_registry` — Parsed registry at *path*, or None when the file is absent, torn or not an object.
  - `load` — Read registry.json, falling back to registry.json.bak instead of losing every model.
  - `save`
  - `upsert`
  - `get`

**`src/repair_paths.py`** — 472 lines / 19510 bytes — Portable path repair for the LYGO LLM Console - the --repair-paths entry point.
  - `_split`
  - `_join`
  - `_is_abs`
  - `_drive_of`
  - `_norm`
  - `_under_root`
  - `_tail`
  - `_looks_pathish`
  - `_is_path_key`
  - `_is_secret_key`
  - `_display`
  - `_stamp`
  - `_current_roots` — Longest root first, so a workspace path tokenises as {workspace}, not {kit}.
  - `_infer_origin` — Guess the drive/folders the config was written for, when it does not say.
  - `_collect_strings`
  - `_ctx_for`
  - `_host_owned` — True for paths that belong to the machine rather than to the kit.
  - `_candidate_exists` — True when a rewritten root would actually resolve on this host.
  - `rewrite_value` — (new_value | None, kind, note) for one config string.
  - `_transform`
  - `_stamp_policy` — Record where this config's paths were written, so a later copy can still relocate them.
  - `plan` — Load every config file and return [(path, mutated_data, ctx, rows), ...] without writing.
  - `print_plan`
  - `apply` — Back up then atomically rewrite every file that has changes. Returns what was written.
  - `repair_paths` — Programmatic entry point. Without consent it prints the plan and refuses to write.
  - `main`

**`src/runtime_facts.py`** — 222 lines / 8047 bytes — Live console + model facts.
  - `_server_module`
  - `_state`
  - `console_build`
  - `selected_model` — The weights actually answering this turn: live STATE first, then the registry.
  - `engine_name`
  - `limb_names`
  - `brain_mode`
  - `cloud_info`
  - `answering` — Who generates the tokens this turn — the brain switch decides, the model never guesses.
  - `facts`
  - `system_roles` — The three systems and what each one is, in the compact form the prompt window can carry.
  - `prompt_block`
  - `limb_catalog` — Name + intent for every wired limb, so the agent knows what it can actually call.

**`src/scanner.py`** — 109 lines / 3735 bytes
  - `_expand`
  - `scan_roots`
  - `_from_header`

**`src/server.py`** — 1695 lines / 75628 bytes
  - `brain_port`
  - `class _BadRequest` — A malformed request envelope (Content-Length, transfer encoding) -> 400, not a 500.
  - `class _BodyTooLarge` — Request body over the endpoint's limit: answers 413 instead of a misleading 400.
  - `_note_config_error`
  - `_config_key`
  - `load_console` — config/console.json, optionally overlaid by config/local.json.
  - `default_scan_roots`
  - `_next_model_rec` — Next brain to try after one failed to load here: registry order, minus what we tried.
  - `maybe_spawn`
  - `boot_async`
  - `gate_all` — Gate the WHOLE text, not just its head.
  - `_read_text_locked` — Read a state file through the retrying reader - a writer may be mid-swap right now.
  - `class Handler` —  | methods: log_message, _query, _headers_map, _loopback, _ok_public, _auth, _send, _json, _read_body, _contain, do_GET, _dispatch_GET
  - `class _StickContainment` — Console server that contains a failure instead of dying on it. | methods: handle_error
  - `main`

**`src/skills_mod.py`** — 870 lines / 34684 bytes — OpenClaw-compatible skills for LYGO LLM Console.
  - `core_root` — Live LYRA core root, resolved at call time — never a frozen drive letter.
  - `_skill_md`
  - `seed_bundled`
  - `ensure`
  - `load_state`
  - `save_state`
  - `_parse_skill_md`
  - `_walk_skills`
  - `extra_roots`
  - `catalog`
  - `list_skills`
  - `read_skill`
  - `set_enabled`
  - `add_root`
  - `prompt_catalog`
  - `match_invoked` — Which seats/skills the operator named. The most specific name wins.
  - `_http_json`
  - `clawhub_search`
  - `clawhub_inspect`
  - `_download`
  - `_safe_member` — Relative target for one zip member, or None when the member must be skipped.
  - `_commit_staged` — Move a fully-extracted skill tree into place, replacing any previous copy.
  - `_extract_skill_zip`
  - `_load_catalogs`
  - `skillhub_list`
  - `skillhub_install`
  - `clawhub_install`

**`src/stack_health.py`** — 41 lines / 1330 bytes
  - `run_stack_health`

**`src/surface.py`** — 157 lines / 6589 bytes — One console, three systems - named, and measured rather than assumed.
  - `drive_type` — Windows volume type for a drive letter ("E:"), 0 when it cannot be read.
  - `media` — Return ("usb"|"pc", why). Declaration wins, then the volume type, then the launchers.
  - `here` — The system answering now: a LOCAL one while a local engine is ready, else the API-only portal.
  - `report` — The label block: which system this is, what answers, and all three with one flagged.
  - `lines` — Human-readable labels for a banner or a status screen: who this is, and all three.

**`src/tools.py`** — 569 lines / 25831 bytes
  - `core_schema`
  - `_strip_extended` — Drop a Win32 extended-length / device prefix so the comparison sees the real target.
  - `_long_path` — Ask Windows for the long form of a path, so an 8.3 short name (LYGOSE~1) cannot
  - `real_path` — The final on-disk target a write would hit.
  - `_components`
  - `write_allow_roots` — The write allowlist: workspace, save/receipts plus the configured read/write roots.
  - `_refuse`
  - `write_target` — Resolve and authorise a write target: (real_path, None) or (real_path, refusal).
  - `_denied` — True when the RESOLVED path names a denied location.
  - `_under`
  - `_self_check`
  - `_find_files`
  - `dispatch`
  - `parse_fence_tool`

**`src/web_tools.py`** — 632 lines / 23973 bytes — HTTPS search + fetch for LYGO LLM Console. Witness/RESOURCE only — not CANON.
  - `_blocked`
  - `_get`
  - `x_status` — (handle, post_id) when url is an x.com / twitter.com post URL, else None.
  - `_x_lines`
  - `x_post` — Read one X/Twitter post through a mirror API. RESOURCE, never CANON.
  - `class _DDG` —  | methods: __init__, handle_starttag, handle_endtag, handle_data
  - `_keywords`
  - `wikipedia_extract`
  - `wikipedia_search`
  - `duckduckgo_search`
  - `_json_get`
  - `hn_search`
  - `github_search`
  - `stackexchange_search`
  - `reddit_search`
  - `wikidata_search`
  - `searx_search`
  - `jina_fetch`
  - `web_search`
  - `_wall` — The marker that makes this body a wall instead of content, else None.
  - `_strip_html`
  - `web_fetch`

**`src/workspace_map.py`** — 219 lines / 6939 bytes — Operator-editable folder/drive mounts. Overlay on admin.json roots.
  - `_load`
  - `_save`
  - `_norm`
  - `_blocked_write`
  - `_pinned_paths`
  - `extra_paths`
  - `revoked_set`
  - `add_mount`
  - `remove_mount`
  - `list_mounts`

**`src/world_clock.py`** — 157 lines / 5065 bytes — World time + weather pulse. RESOURCE (Open-Meteo), not CANON.
  - `_wx_label`
  - `_fetch_weather`
  - `pulse_stamps`
  - `pulse`


### `tests/` — test modules

- `tests/test_adaptive_perf.py` — 467 lines — Host-adaptive performance: the stick must run as fast as the host it is plugged into.
- `tests/test_admin_train.py` — 64 lines
- `tests/test_agent_io.py` — 283 lines — Agent input/output: the model speaks for itself, knows its runtime, and can see its limbs.
- `tests/test_api_default.py` — 80 lines — House API policy: DeepSeek is the default provider AND the standing backup.
- `tests/test_backends.py` — 413 lines — Backend store: a GPU is claimed only after it loads a real model on this host.
- `tests/test_brain_switch.py` — 130 lines — Local-default brain switching: API activation and automatic local handoff.
- `tests/test_cloud_api.py` — 39 lines
- `tests/test_cloud_chain.py` — 198 lines
- `tests/test_colibri.py` — 44 lines
- `tests/test_continuity.py` — 40 lines
- `tests/test_continuity_ui.py` — 25 lines
- `tests/test_debug_pass.py` — 111 lines — De-bug pass regressions: the fixed failure paths must stay fixed and must stay safe.
- `tests/test_donate_radio.py` — 35 lines
- `tests/test_engine.py` — 25 lines
- `tests/test_lygo_engine.py` — 71 lines
- `tests/test_math_routing.py` — 86 lines — Arithmetic must not be routed to the web tools: host guard + model-call redirect.
- `tests/test_model_verdicts.py` — 88 lines — A model this host cannot load must not be picked again — and must not poison other hosts.
- `tests/test_no_history_shadow.py` — 20 lines
- `tests/test_notepad.py` — 64 lines
- `tests/test_openai_proxy.py` — 49 lines
- `tests/test_p0_hook.py` — 43 lines
- `tests/test_port_isolation.py` — 95 lines — Port isolation between copies of the kit.
- `tests/test_ports_env.py` — 67 lines — Port env overrides — the USB CLAW runs beside a desktop console, never on top of it.
- `tests/test_public_gateway.py` — 40 lines
- `tests/test_public_install.py` — 55 lines
- `tests/test_registry.py` — 53 lines
- `tests/test_registry_ram.py` — 200 lines — RAM-auto brain: the console picks the biggest model THIS host can hold.
- `tests/test_repair_guards.py` — 105 lines — Guards on the config-path repair.
- `tests/test_request_envelope.py` — 125 lines — Request-envelope regression tests (2026-09-18 sweep).
- `tests/test_scanner.py` — 81 lines
- `tests/test_security_hardening.py` — 203 lines — Security hardening regression tests (2026-09-18 sweep).
- `tests/test_shipped_engine_purity.py` — 109 lines — The shipped engine/ must stay CPU-pure, and every GPU dll must live in engine/backends/.
- `tests/test_skills.py` — 66 lines
- `tests/test_surface_labels.py` — 213 lines — The three systems must stay named, distinct, and read from the machine.
- `tests/test_tool_battery.py` — 45 lines
- `tests/test_tool_routing.py` — 53 lines — Tool routing on the shipped local brain: the fix must stay fixed.
- `tests/test_tools.py` — 41 lines
- `tests/test_tuning.py` — 578 lines — Performance-pass regression tests (2026-09-18 tuning sweep).
- `tests/test_usb_atomic_writes.py` — 250 lines — USB CLAW: persistence and request handling must survive a stick.
- `tests/test_usb_portability.py` — 170 lines — USB CLAW portability contract.
- `tests/test_usb_selfcontained.py` — 71 lines — The stick must find its OWN model store on a PC that has never run ollama.
- `tests/test_web_tools.py` — 131 lines
- `tests/test_workspace_map.py` — 37 lines
- `tests/test_world_clock.py` — 28 lines

tests total lines: 5191

### `portal/`

- `portal/app.js` — 60230 bytes
- `portal/donate.js` — 2907 bytes
- `portal/index.html` — 15945 bytes
- `portal/logo.jpg` — 154829 bytes
- `portal/logo.svg` — 620 bytes
- `portal/radio.js` — 10025 bytes
- `portal/style.css` — 23069 bytes

### `web_portal/`

- `web_portal/IDENTITY.md` — 1600 bytes
- `web_portal/MEMORY.md` — 9742 bytes
- `web_portal/README.md` — 102 bytes
- `web_portal/SOUL.md` — 2326 bytes
- `web_portal/app.js` — 33321 bytes
- `web_portal/donate.js` — 2129 bytes
- `web_portal/index.html` — 23853 bytes
- `web_portal/logo.jpg` — 154829 bytes
- `web_portal/logo.svg` — 620 bytes
- `web_portal/lygo-skills.json` — 5568 bytes
- `web_portal/manifest.json` — 590 bytes
- `web_portal/og.jpg` — 146483 bytes
- `web_portal/portal.json` — 427 bytes
- `web_portal/radio.js` — 5719 bytes
- `web_portal/style.css` — 12710 bytes
- `web_portal/tools.js` — 27504 bytes
- `web_portal/x-card.jpg` — 146483 bytes

### `config/`

- `config/admin.json` — 4018 bytes
- `config/api.json` — 428 bytes
- `config/console.json` — 3654 bytes
- `config/local.json` — 242 bytes
- `config/local.json.example` — 1552 bytes

### `prompts/`

- `prompts/BRAIN.md` — 618 bytes
- `prompts/CONTINUITY_SEED.json` — 2451 bytes
- `prompts/IDENTITY.md` — 1088 bytes
- `prompts/LINKS.md` — 868 bytes
- `prompts/LYGO_ALIGN.txt` — 2749 bytes
- `prompts/MAP.md` — 585 bytes
- `prompts/MEMORY.md` — 2566 bytes
- `prompts/POEM.txt` — 782 bytes
- `prompts/SOUL.md` — 1375 bytes

### `scripts/`

- `scripts/bench_toks.py` — 9564 bytes — tok/s benchmark for this console - read-only, re-runnable, prints the config with the numbers.
- `scripts/fetch_colibri.ps1` — 1135 bytes
- `scripts/fetch_engine.ps1` — 9343 bytes

### `skills/`

- `skills/README.md` — 260 bytes

### kit root files

- `.gitignore` — 220 bytes
- `.pytest_cache/` (dir)
- `BUILD_MANIFEST.json` — 20097 bytes
- `COLIBRI.md` — 1618 bytes
- `FULL_LYGO.md` — 557 bytes
- `HARDENING.md` — 1630 bytes
- `INSTALL.bat` — 1335 bytes
- `LYGO_ENGINE.md` — 1259 bytes
- `LYGO_LLM_CONSOLE.bat` — 13136 bytes
- `LYGO_LLM_CONSOLE_STOP.bat` — 10308 bytes
- `MODELS.md` — 1762 bytes
- `PUBLIC_GATEWAY.bat` — 4661 bytes
- `PUBLIC_PORTAL.md` — 2668 bytes
- `README.md` — 5851 bytes
- `READ_DISCLAIMER_FIRST.md` — 429 bytes
- `SKILLS.md` — 1568 bytes
- `WHITEPAPER.md` — 108293 bytes
- `config/` (dir)
- `data/` (dir)
- `docs_addendum_v23.md` — 0 bytes
- `engine/` (dir)
- `models/` (dir)
- `portal/` (dir)
- `prompts/` (dir)
- `save/` (dir)
- `scripts/` (dir)
- `skills/` (dir)
- `src/` (dir)
- `tests/` (dir)
- `tools/` (dir)
- `web_portal/` (dir)
- `workspace/` (dir)

## Appendix C — Glossary

| Term | Meaning in this kit |
|---|---|
| **Brain** | whichever model generates the tokens for a turn: the local engine or the cloud API |
| **Brain switch** | the operator control that arms the API or returns to local (`/api/brain`, `#brain-local` / `#brain-api`) |
| **Handoff** | an automatic, recorded transfer of a turn to the local engine because the API failed (402/429/outage) |
| **Standby** | the local engine kept booted while the API is armed, so a handoff is instant |
| **Limb** | a callable tool the agent can invoke (56 wired) |
| **Champion / seat** | a named role the operator can summon (e.g. LYRA-Δ9 Architect), resolved by `match_invoked()` |
| **Runtime facts** | the injected block that tells the model its own build, model, engine, brain and standby |
| **Prefetch** | the host pre-running the obvious limbs for a turn so a small model gets facts instead of guessing |
| **Trace** | the record of what the host ran for a turn (returned in the payload and shown in the studio) |
| **Receipt** | the audit node written for a turn; the id is returned with the answer |
| **P0 gate** | the policy gate on input and output; `QUARANTINE` refuses a turn, `ALLOW` records a verdict |
| **Continuity** | soul + identity + memory + session, composed into every turn |
| **Witness / RESOURCE** | web and third-party data: usable evidence, **never** CANON |
| **Kit** | this directory — a self-contained, portable console |
| **Steward** | Justin Helmer (ExcavationPro / Lightfather) — the human operator and the only publisher |

---

## Appendix D — File and path index

### D.1 Canonical paths

| Purpose | Path |
|---|---|
| Live kit | `<EDRIVE>\lygo-protocol-stack\lygo_llm_console\` |
| Console entry (kit) | `…\lygo_llm_console\LYGO_LLM_CONSOLE.bat` |
| Console entry (desktop shim) | `<DESKTOP>\LYGO_LLM_CONSOLE.bat` |
| Stop | `…\lygo_llm_console\LYGO_LLM_CONSOLE_STOP.bat` |
| First-run install | `…\lygo_llm_console\INSTALL.bat` → `src\install.py` |
| Public gateway launcher | `…\lygo_llm_console\PUBLIC_GATEWAY.bat` → `src\public_gateway.py` |
| Whitepaper v1 (design) | `<EDRIVE>\lygo-protocol-stack\docs\whitepapers\LYGO_LLM_CONSOLE_v1.md` |
| **Whitepaper v2 (this)** | `<EDRIVE>\lygo-protocol-stack\docs\whitepapers\LYGO_LLM_CONSOLE_v2_AS_BUILT.md` |
| Whitepaper (with the kit) | `…\lygo_llm_console\WHITEPAPER.md` |
| Machine-readable manifest | `…\lygo_llm_console\BUILD_MANIFEST.json` |
| USB docs | `<USB_ROOT>\LYGO_BUILDER_KEY\docs\` |
| USB recovery | `<USB_ROOT>\LYGO_BUILDER_KEY\RECOVERY\` |
| USB stale kit | `<USB_ROOT>\LYGO_BUILDER_KEY\lygo_llm_console\` (see L6) |
| Model blobs (Ollama CAS, read-only) | `<USB_ROOT>\LYGO_BUILDER_KEY\product\models\ollama\blobs\` |
| LYRA core | `<EDRIVE>\LYRA_CORE` |
| Studio logs / pids / receipts | `…\lygo_llm_console\save\` |

### D.2 Commands worth memorizing

```bat
:: tests
cd /d "<EDRIVE>\lygo-protocol-stack\lygo_llm_console\tests"
C:\Python313\python.exe -m unittest discover -s . -p "test_*.py"

:: health
curl -s http://127.0.0.1:9641/api/health

:: brain switch (arm API)
curl -s -X POST http://127.0.0.1:9641/api/brain -H "Content-Type: application/json" -d "{\"mode\":\"api\"}"

:: brain switch (return to local)
curl -s -X POST http://127.0.0.1:9641/api/brain -H "Content-Type: application/json" -d "{\"mode\":\"local\"}"

:: one agent turn, local brain, no tools
curl -s -X POST http://127.0.0.1:9641/api/chat -H "Content-Type: application/json" ^
  -d "{\"messages\":[{\"role\":\"user\",\"content\":\"whoami\"}],\"stream\":false}"

:: public gateway (only with consent, never :9641)
python -u src\public_gateway.py --backend ollama --model qwen2.5:3b --lan --i-consent --port 9642
```

---


<!-- LYGO_ADDENDUM: 2026-09-18 dial-in pass -->

---

# Addendum — dial-in pass, 2026-09-18 (v2.2)

*Written by the build agent at the steward's instruction, from measurements taken on the two real
systems. Where a number appears, it was read out of a log or a served endpoint; where something is
still open, it says so.*

## A1. Why this addendum exists

The steward's report on the previous build: *"LYGO Local LLM console boots from bat on desktop
cleanly … boots with a working interface, i tested response and it was fast … USB LYGO CLAW boots
clean, opens portal and works clean … basic functions seem fine — i have not run tool testing with
either system."* That is a passing boot and a passing chat turn, and an untested tool layer. This pass
therefore (a) hardened the USB claw for the host it gets plugged into, (b) fixed the tool-routing
defect that stops "basic functions" from being *usefully* basic, (c) proved the three-system role
labels on the served surfaces, and (d) repaired three defects this work itself introduced before
closing the chat.

## A2. Host-adaptive performance (dial-in)

Both launchers bring the local brain up in ~15–19 s on this host and report their own mode, so the
same stick behaves differently on a better machine instead of being pinned to the worst case:

| Surface | Ports (console / engine) | Measured boot | Mode reported |
|---|---|---|---|
| USB claw (E:, REMOVABLE) | 9651 / 11451 | 19.0 s | `gpu_full ngl=99 threads=16` |
| PC console (I:, FIXED) | 9641 / 11441 | 16.9 s | `gpu_full ngl=99 threads=16` |

The shipped `engine/` stays CPU-only and is locked that way by `tests/test_shipped_engine_purity.py`;
GPU backends live in the backend store and are only selected after a real self-test, so a stick moved
to a machine without a usable GPU still boots on the proven path. Default brain is `qwen2.5-coder:7b`
(chosen for VRAM fit, not rank); the always-default backup API is **DeepSeek**, with failover order
`groq, deepseek, gemini`; the API boosts the local brain when the host cannot carry the work.

## A3. Tool routing — the arithmetic defect, and its fix

**Symptom.** Arithmetic questions were answered by the *web tools*, not by `calc`: the first bench
measured **5/5 mis-routed**, with the model reporting a web-derived number (including one run that
answered `401`). Root cause was host-side, not model-side: `host_prefetch()` in `src/chat_loop.py`
matches the literal words **"what is"** (`SEARCH_HINT`) and fired `web_search` + `web_fetch` *before*
the model ever saw the question.

**Fix.** An arithmetic gate in front of the pre-dispatch path — `math_expr()` / `math_only()` — a host
`calc` prefetch that returns before any web call, `calc`'s `value` kept in the slim trace, and a
redirect in `run_tools_round()` for a model-issued web call whose query is a bare arithmetic
expression. A second iteration was needed and is itself the lesson: after the first fix the bench read
**1/4**, because `"What is 17 * 23? Use the calc tool."` left the words *"Use the calc tool"* in the
remainder and a strict whole-message recogniser rejected it. `MATH_INSTR` now strips a trailing
instruction ("use/with/via the calc tool") and trailing politeness ("please", "thanks"), which is
exactly the phrasing a tool prompt invites.

**Result (re-measured after the second fix, both consoles):**

| Probe | USB claw (`:9651`) | PC console (`:9641`) |
|---|---|---|
| `What is 17 * 23? Use the calc tool.` | `calc` → **391** | `calc` → **391** |
| `What is 17 * 23?` | `calc` → **391** | `calc` → **391** |
| `Compute 144 / 12 with the calc tool.` | `calc` → **12.0** | `calc` → **12.0** |
| `What is 9 * 9?` | `calc` → **81** | `calc` → **81** |
| control: `Who wrote the novel Moby-Dick?` | no tool, correct | no tool, correct |

**Arithmetic routed to the web: 0/4 (was 5/5). Arithmetic answered wrong: 0/4.** Turn latency
1.6–2.6 s per probe. Zero console log lines mentioning an error. Locked by
`tests/test_math_routing.py`, which mocks `dispatch` and never touches the network.
Instrument: `%LOCALAPPDATA%\Temp\lygo_routing_bench.py` (`LYGO_BENCH_KIT` / `LYGO_BENCH_PORT`).

## A4. Context budget — now structural, not a number

`compose_system()` measured 16,551 chars on the stick and 16,328 on the desktop with *identical code*,
because the `=== MEMORY.md ===` block is mutable user data. A fixed total therefore cannot hold. The
budget is now enforced against the measured cost of everything else: memory is capped
(`MEMORY_PROMPT_CAP 2800`) and `_fits()` shrinks **memory only** — never SOUL or IDENTITY — to keep the
composition under `PROMPT_CEILING 16300`, with the test asserting below the 16,500 hard limit.

Measured after the fix: **15,867 chars (PC console) / 16,090 chars (USB claw)**. Context window is
8,192 tokens; the largest composed prompt observed this pass was **7,638 tokens** (desktop) and 6,152
(stick).

## A5. The three systems, labelled by role — and proven on the page, not just in JSON

The steward's definition: *"USB LOCAL/API + PC LOCAL/API + online Internet API only Web portal … 3
separate systems that all work as one."* `src/surface.py` is the single source of truth, with two
renditions of one truth: `ROLES` for human-readable surfaces and `ROLES_BRIEF` for the model prompt
(keyed by `ORDER`'s ids — `USB_LOCAL`, `PC_LOCAL`, `API_ONLY`).

| System | Role, as served | How identity is decided |
|---|---|---|
| **USB LOCAL** | stand-alone agent: everything onboard the stick — plug it in and go, mobile | `GetDriveTypeW(E:) = REMOVABLE` |
| **PC LOCAL** | full admin console on this PC — this is the build that becomes the public version | `GetDriveTypeW(I:) = FIXED` |
| **WEB PORTAL (API ONLY)** | online API-only agent portal — already built, anyone can use it from a web page | no local engine: it *is* the API surface |

Proof run: both consoles booted and read back — stick reported `SYSTEM: USB LOCAL (id=usb_local)` and
the PC console `SYSTEM: PC LOCAL (id=pc_local)`, each listing all three systems with `here` on itself;
all role wording present in the `/api/health` payload **and** in the 56,955-char served page.
Verdict: `ROLE_VERIFY: PASS`. The web portal itself is published from the chatagent git repo at
`https://chatagent.ca/portal/`; the label edit is in the deployed `portal/index.html` and goes public
when the steward pushes.

## A6. Verification record (2026-09-18)

| Check | Result |
|---|---|
| Suite, PC console tree | **`Ran 296 tests` — OK** (~28 s) |
| Suite, USB claw tree | **`Ran 296 tests` — OK** (~34 s) |
| Changed sources across the two trees | byte-identical (hash-compared after every edit) |
| Full-tree Python compile sweep | clean, 0 failures |
| `ROLE_VERIFY` (both consoles, served pages) | **PASS** |
| Routing bench, both consoles | **0/4 mis-routed, 0/4 wrong** |
| Public SKU export | CLEAN — 195 files / 198 entries / 22.9 MB |
| Sealed self-test of shipped engine | CPU-only, unchanged |

Collection history within the day is worth recording because it is how the defects below were caught:
238 → 267 → 289 → **296**. A count that *drops* is a collection failure, not a smaller test suite.

## A7. Defect ledger additions (all three introduced by this pass, all repaired)

1. **`src/runtime_facts.py:173` — unterminated f-string.** A prompt-fit edit wrapped the System line
   and dropped its terminator. Hard `SyntaxError`, which collapsed collection from 267 to 238 tests in
   *both* trees. Rule: an edit that breaks a module shows up as a **collection count drop** — read
   "Ran N tests" before any verdict line.
2. **`src/surface.py` — `ROLES_BRIEF` keys written bare** (`usb_local:` instead of `USB_LOCAL`), a
   `NameError` at import that took out nine items (six `test_agent_io`, `test_whoami`,
   `test_dispatch_core`, the label test). Rule: a new dict in a module that is imported everywhere gets
   an import check in the same commit, not a suite run later.
3. **Prompt ceiling was structurally unfixable by trimming** — the real composed prompt was 16,543
   chars, 43 over, and an earlier "12,991" reading was a *partial* measurement that had to be
   withdrawn. Rule: measure the whole composed prompt from the function that serves it; never sum
   section estimates.
4. **Launcher behaviour worth remembering (not a defect):** a launcher silently refuses to start while
   a stale listener holds its port. A verifier that reported "no health" for both consoles had started
   *zero* engines — the kits' `save/logs/llama-server-*.log` mtimes proved it. The working verifier
   clears the console and engine ports first and prints port state every 20 s while waiting.

## A8. Open items — deliberately not closed

- **Publishing is the steward's call.** Nothing in this pass was committed or pushed; the role labels
  reach `https://chatagent.ca/portal/` when the steward pushes the chatagent repo. Public lattice
  updates wait for the end of the build phase, by explicit instruction.
- **`config/api.json` holds a plaintext API key in both trees, including the USB claw.** Value never
  printed or logged. Decision pending: rotate, remove, or keep it out of the export. Until then, treat
  the stick as carrying a credential.
- **Context ceiling 8,192 tokens** with the largest desktop prompt at 7,638 — thin headroom. Raising it
  is a VRAM trade-off and the steward's decision.
- **14B-over-7B branch of the VRAM rule is untested** on a 24 GB GPU; CUDA behaviour is verified on one
  host only. 27B PrismML Ternary Bonsai remains unsupported by design.
- **A `D4` fallback swap persists the recovered model as a `manual` pin**, which makes a recovered boot
  look like a human choice. Worth a deliberate decision later.

## A9. Artifacts from this pass

Fix scripts (each anchored, count-guarded, `.bak`-backed, idempotent):
`lygo_math_routing_fix.py` (host gate + redirect + new test), `lygo_math_instr_fix.py` (instruction
tolerance), `lygo_math_polite_fix.py` (politeness tolerance), `lygo_fix_runtime_facts.py`,
`lygo_fix_roles_brief.py`, `lygo_prompt_budget_fix.py`. Instruments: `lygo_routing_bench.py`,
`lygo_role_verify2.py`. All under `%LOCALAPPDATA%\Temp\`; backups under
`%LOCALAPPDATA%\Temp\lygo_kit_debris\`. Logs of every run quoted above were kept in the same
directory.

---

*End of addendum v2.2 — dial-in pass. Build `v1.1-20260917api2` · signature `Δ9Φ963-LYGO-LLM-CONSOLE-v1`.*
*Steward: Justin Helmer (ExcavationPro / Lightfather). Agents build, verify, report; the human publishes.*

## Appendix E — Document history and maintenance

| Version | Date | Change |
|---|---|---|
| v1 | 2026-09-15 | Design document (1,042 lines): intent, layout, security posture — pre-build |
| v2 | 2026-09-17 | **As-built record**: 227-test green suite, agent I/O + brain switching + context budget + LYRA-Δ9 seat, defect ledger with rules, builder template, portal gap analysis, verified roadmap |

| **v2.1 (this)** | **2026-09-18** | **Host-adaptive performance**: CPU threads and GPU layers follow the host, GPU builds live in `engine/backends/` and are applied only after a self-test proves them on that PC; measured 4,300 tok/s prompt eval and 101.9 tok/s generation on CUDA where the shipped pin did ~82 / 14.5; four new defects (D16–D19) with rules |

| 2026-09-18 (dial-in + harden) | DeepSeek fixed as the always-default backup API (`DEFAULT_PROVIDER`, normalised configs, failover ordering); the default main brain became a tool-strong coder (`PREFER_IDS` + `tool_rank`), `qwen2.5-coder:7b` staged and sha256-verified on the stick CAS (7 manifests); 3.03 GiB of duplicate blobs reclaimed; launch flags tuned by measurement (baseline wins, flash-attn opt-in); unloadable-model verdicts make a bad brain survivable (D21); scan roots honour the launcher (D22); 247 tests OK on both trees; harden pass receipt written |

| **v2.2 (this)** | **2026-09-18** | **Dial-in + tool routing + three-system role labels**: arithmetic questions now reach `calc` on both consoles (0/4 mis-routed, was 5/5); composed prompt held under a structural budget (15,867 / 16,090 chars vs 16,500 limit); USB LOCAL / PC LOCAL / WEB PORTAL roles proven on the served pages; 296-test green suite in both trees. |
**Maintenance rule:** this document is part of the build. When a roadmap step lands, update §1.1 (numbers), §10 (verification record) and §11 (defect ledger, move the fix out of §13/§14) **in the same change**, and re-copy it to the three archive locations in §0.1. A stale whitepaper is worse than none.

---

*End of whitepaper v2 — as-built. Build `v1.1-20260917api2` · signature `Δ9Φ963-LYGO-LLM-CONSOLE-v1`.*
*Steward: Justin Helmer (ExcavationPro / Lightfather). Agents build, verify, and report; the human publishes.*

## Defaults, dialled in (2026-09-18)

**Policy, in one place each.** `DEFAULT_PROVIDER`/`DEFAULT_MODEL` in `src/cloud_api.py` make
**DeepSeek the default API and the standing backup**: a blank config, or a provider name this build
does not ship, resolves to `deepseek` / `deepseek-chat`, and DeepSeek also leads the failover order
behind an explicit primary (so a second key only needs pasting in, never re-ordering). `PREFER_IDS`
+ `tool_rank()` in `src/registry.py` make the **default brain a coder**: tool-calling models are
ranked above general chat, so a 9B general model no longer outranks a 7B coder for agent work.

**The default brain must fit the machine, not just the RAM.** Two measured rules, both from booting
rather than reading:

* *VRAM before rank.* A rank-3 coder that cannot be offloaded loses to a rank-3 coder that can.
  Observed: an 8.5 GB 14B coder on an 8 GB GPU fell back to the CPU at a partial 60/99 layers,
  turning a tool turn into 101s; the 5 GiB 7B coder on the same host runs
  `mode=gpu_full ngl=99` and answered a tool prompt in **2.54s**.
* *A deterministic RAM budget.* The gate uses `max(measured available, half of installed RAM)`, so
  the chosen brain cannot swing because something else was running a minute earlier (observed: a
  32 GB host reading 3 GiB free picked its smallest model).

**Bench, shipped engine, 1,059-token prompt, same model:** baseline 3,245.7 tok/s prompt-eval /
55.4 tok/s generation; `-fa on` 2,620.6 / 55.2 (**slower** — so flash attention is opt-in via
`console.json`, and the plan no longer advertises it by default); `-b 4096 -ub 1024` 2,750.1 / 55.0
(slower); `--no-mmap` **invalid argument** in this build — it is `-lm/--load-mode` now.

**Harden pass 2: 15/15** (`lygo_harden2_receipt.json`) — shipped `engine/` CPU-pure on both trees,
GPU DLLs only in `engine/backends/`, backend manifests parse, no foreign port bound, and the boot
matrix: normal / stale `perf_active.json` / GPU off / backend store removed / **a brain that cannot
load is swapped instead of killing the console** (a crafted GGUF that parses but will not load was
replaced live and remembered in `data/model_verdicts.json`).

**Stick contents.** CAS now holds 7 manifests including `qwen2.5-coder:7b` (4,466.1 MB, sha256
verified against source); the redundant top-level CAS was reclaimed (3.03 GiB of byte-identical
blobs, manifests archived to `I:\LYGO_STICK_ARCHIVE`); free space 3.57 GiB. Suites: **255 tests OK
on both trees**, byte-identical (`src/`, `tests/`, `config/`).

**Not done / limits.** The 27B PrismML Ternary Bonsai that prompted this pass needs its own
llama.cpp fork (`tensor 'output_norm.weight' has offset …` — it will not load on stock) and does not
fit this stick; context stays at the conservative 8192; the CUDA path is verified on one host.

---

## Addendum — performance & stability pass, 2026-09-18 (v2.3)

This pass started as "measure the default brain", turned into a flag hunt, and ended as a
stability pass with two real defects fixed in the console's own code. Baseline first, always: the
shipped configuration was **56.5 tok/s generation** on the default brain, which §7.3 now records as
the practical ceiling of the card rather than a starting point to beat.

### What changed in the kit

1. **`/api/chat` no longer 500s on `tools: false`** (defect D24) — the payload the portal sends
   when its Agent-limbs box is unticked, and the one the API docs told testers to send.
2. **Per-turn tok/s telemetry** — `perf` on every reply and on the SSE `done` event, `last_perf`
   in `/api/health`, and the number shown in the studio (context line + answer tooltip).
3. **KV-aware GPU planning** (D25, D26, D27) — the cache is costed from the model's own header, at
   the context the engine will actually run.
4. **New knobs** `kv_type`, `batch`, `ubatch` through one config reader, sanitised and clamped,
   part of the launch signature so changing one forces the restart it needs.
5. **Launch safety net** — if tuned flags fail to load, the runner degrades to the shipped defaults
   *before* a GPU backend is retired, and says so in health.
6. **The engine key is no longer a placeholder** (D28) — generated per install, 43 chars, and the
   only thing standing between `llama-server` and anything else that can reach loopback.
7. **`scripts/bench_toks.py` ships with the kit** — read-only, re-runnable, config printed beside
   every number.

### Verification

* `Ran 372 tests in 33.426s` — **OK** (`unittest discover`, the documented command); `pytest -q`
  agrees at 372 passed. The suite was 255 before this pass; `tests/test_tuning.py` (39 cases)
  covers the KV arithmetic, the plan knobs, GGUF dimensions, the `/api/chat` tools flag, key
  rotation and the portal wiring.
* `pytest -q -W error::ResourceWarning` — **372 passed, 0 warnings, three runs in a row** (defect D29).
* Two strict suites **run simultaneously** — both 372 passed, and a full run leaves `workspace/MEMORY.md`, `workspace/memory.jsonl` and `save/workspace_map.json` untouched (defect D30: four tests used to write the operator's real state; 239 test rows and 120 dead temp paths were pruned out of it with backups kept).
* Live engine argv, read from the process table: `-c 16384 -ngl 99 -t 16 -np 1 --jinja --metrics
  --alias qwen2.5-coder:7b --api-key [REDACTED] -ctk q8_0 -ctv q8_0`, with the runner's key matching
  `data/.llama_api_key`.
* `/api/health` → `brain: ready`, `mode: local`, `ngl: 99`, `threads: 16`, `backend: cuda`,
  `kv_mib: 448`, `scan_n: 15`.
* After-bench on the final config (record: `save/logs/bench_toks_20260918_175653.json`): generation
  55.6 / 56.2 / 56.1 = **mean 56.0 tok/s**; depth 52.4 gen / 3,130.7 prefill; a real gated turn
  5,582 prompt tokens, 1.96 s wall, 46.0 tok/s; `tools=false` → HTTP 200.
* Two self-knowledge turns on the new default brain, tools on and off, named
  **qwen2.5-coder:7b / llama.cpp** correctly (§1.1).

### Deliberately not done

* **Flash attention stays off** and batch size stays default — measured slower or neutral here, and
  shipping a regression with a nicer-looking argv is still shipping a regression.
* **The draft model is not wired** (see §7.3) — the numbers are in the record instead.
* **`PROMPT_CEILING` is not raised** with the window. The engine window grew; the prompt budget did
  not, because the headroom belongs to history, traces and the answer.
* **The stick keeps its portable profile** — `9651/11451`, `-ngl 0`, `-t 4`, ctx 8,192. It boots on
  hosts nobody has measured, and a conservative default is the point of a portable kit. What it
  *does* inherit in this sync is the fixed code, including the KV-aware planner and the safety net.

### The USB, and one honest caveat

The steward chose the sync direction for roadmap #7: **live → stick**, the live tree being the one
verified green. The stick's prior state was backed up first, and credential-bearing files
(`config/api.json`, `.env`, `save/registry.json`, `config/admin.json`, `data/.llama_api_key`,
`data/.lygo_llm_token`) were never copied. Two copies of one kit drift; that is why the record
lives in `BUILD_MANIFEST.json` and the copies live in four places, all byte-identical after this
pass.
