# LYGO Engine (hybrid)

One brain. Two runtimes. **VRAM + RAM + SSD as one placement hierarchy** — Colibri’s lesson, applied to everything we already onboard.

| Path | Runtime | When |
|------|---------|------|
| GGUF (own vault, incl. an imported Ollama CAS) | ggml-org **llama.cpp** (`engine/llama-server.exe`) | default agent loop |
| HF / Colibri dir (`config.json` + safetensors) | **Colibri** `coli serve` | GLM / DeepSeek V4.x / Kimi K3 / … |

**Boot LLM** does not ask you to pick a vendor. `lygo_engine.plan()` measures RAM, optional NVIDIA VRAM, and CPU threads, then:

- **llama.cpp:** mmap the GGUF from disk (do not require the whole file in RAM), auto `-ngl` if VRAM is free, threads = cores. Placement changes speed, not weights.
- **Colibri:** `PIN_GB` from ~55% of RAM, `CUDA_EXPERT_GB=auto` if VRAM ≥ 4 GiB. Experts stream from SSD.

Ports: llama `:11441` · Colibri `:11443` · portal `:9641`. Chat uses whichever the plan booted (`STATE.engine_port`).

`kernel_status` reports `lygo_engine` (probe + both launchers).

Colibri source (Apache-2.0): https://github.com/JustVugg/colibri — we do not vendor the C tree. Engine zip: `scripts/fetch_colibri.ps1`. Weights stay your download.

CANON is still dual ledgers / Haven Star Chart. This engine is RESOURCE.
