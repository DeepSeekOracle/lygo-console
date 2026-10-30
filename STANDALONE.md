# STANDALONE — zero Ollama, our own backend

**Rule (steward, 2026-09-19):** the console runs **its own** services and **its own** model files.
Ollama is not required, not called, and not scanned implicitly. An existing Ollama *model store* is
plain GGUF bytes and may be imported **once, read-only**; after that the kit never needs it.

Signature: `Δ9Φ963-STANDALONE-NO-OLLAMA-v1`

## What actually runs

| piece | what it is | where |
|---|---|---|
| chat engine | our own `engine/llama-server.exe` (ggml-org llama.cpp **b10988**), `--jinja`, `--mmproj` when the model has a projector | `127.0.0.1:11441` (PC) / `:11451` (stick) |
| embed engine | same binary, embedding mode | `:11451` / `:11452` |
| console | our own HTTP studio + agent loop, 63 limbs | `:9641` (PC) / `:9651` (stick) |
| public gateway | our gateway, `--backend local` **by default** = our engine | `:9642` |
| model vault | GGUFs we own | PC `<MODELS>` (`LYGO_MODELS` env var) · stick `product\models\ollama` (its own CAS, 19 GB) |

`engine.ollama_port_open()` only *probes* `11434` as a diagnostic. `src/ollama_import.py` is the
read-only one-time importer — its contract is in its docstring: *"Never subprocess ollama."*
`tests/test_standalone_no_ollama.py` (11 tests) fails the build if any of that comes back.

## Which model, measured rather than guessed

`python scripts/bench_tools.py --models a,b` asks **24 operator requests over this console's own 63
schemas** and scores them with the console's own `chat_loop.extract_tool_calls` (native `tool_calls`
**and** fenced-JSON text calls, which is how a model with no tool tokens in its GGUF template answers).
Measured on the studio PC, 2026-09-19:

| model | right tool | valid args | mean | vision | owned by |
|---|---|---|---|---|---|
| llama3.1:8b | **23/24** | 23 | 0.6 s | no | stick CAS |
| qwen2.5-coder:7b | 22/24 | 22 | 0.7 s | no | PC vault |
| **gemma4:12b** | 21/24 | 21 | 4.8 s | **yes** | PC vault (+ its projector) |
| qwen2.5:3b | 21/24 | 21 | 0.8 s | no | stick CAS |
| gemma2:9b | 0/24 | 0 | 2.9 s | no | stick CAS |

**PC default = `gemma4:12b`**: the only model here that both calls the tools and *sees* an image, so
`check this photo …` answers from the same engine that answers everything else — zero extra VRAM, no
second process. The steward accepted "not fast, but stable and complete"; 4.8 s per tool call is the
price of one model that does everything. Set in `save/registry.json` (`selected`).

**USB stick** stays on **`llama3.1:8b`** (its own CAS, 23/24, fastest) — the stick has 3.6 GB free and
gemma4's 7.4 GB does not fit. When the stick runs on this PC it also sees `<MODELS>`, so a photo
question can still be answered by gemma4; on a foreign PC the stick is tools-only.

**`gemma2:9b` never calls a tool** — do not put it in a preference list.

## Moving a model into the vault

```
python scripts/import_to_vault.py --list                 # what is owned, what is outside
python scripts/import_to_vault.py --models llama3.1:8b   # copy + sha256-verify (+ its mmproj)
python scripts/import_to_vault.py --models gemma4:12b --register   # also repoint the registry
```
The registry is backed up (`save/registry.json.bak-vault-*`) before any repoint, and `source` becomes
`lygo_vault`. This machine's vault already owns `gemma4:12b` (+ projector), `qwen2.5-coder:7b`,
`nomic-embed-text`; the other 11 registry models still point at an Ollama folder and are migratable
with one command when the disk space is wanted.

## Limbs, honestly

63 tools call out of the engine: files/disks, session history, notes, skills, web + HTTPS fetch,
search, memory, image **read/scan** (`image_info`, `image_see` — real vision on our own engine),
pages, kernel/stack status.

**Image generation is not in this kit.** llama-server does text and vision; generating a picture needs
a diffusion backend, and bolting one onto the chat engine is the wrong shape. The stand-alone answer
that matches the rest of this system is a separate service reading GGUF weights — e.g.
**stable-diffusion.cpp** (one binary, no Python, no torch) on its own port, reached by a new limb.
That is a build for the steward to approve, not something to slip in behind a default.

## Proving it is standalone

1. `python -m pytest tests/test_standalone_no_ollama.py -q` → 11 passed.
2. Stop anything on 11434 (`netstat -ano | grep :11434`), then boot the console: it must reach
   `brain=ready`. The engine log must show `load_model: loading model '<MODELS>\…'`.
3. Ask a photo question and a `steward_map` question in the same session: both must answer.
