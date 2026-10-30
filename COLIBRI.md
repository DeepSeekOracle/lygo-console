# Colibri — MoE limb of LYGO Engine

LYGO Engine hybrid: see `LYGO_ENGINE.md`. Colibri is the SSD-streamed MoE backend, not a side product.

# Colibri engine (optional)

RESOURCE map to [JustVugg/colibri](https://github.com/JustVugg/colibri) (Apache-2.0).  
Treats **VRAM + RAM + SSD as one memory hierarchy** and streams MoE experts on demand. Not llama.cpp. Not a datacenter. Not fast on a modest PC — the point is it can *run*.

Supported families (upstream): GLM-5.2/5.3, GLM-5.3-Flash, Inkling, **Kimi K3**, **DeepSeek V4 Flash**, **DeepSeek V4.1 Flash**, Qwen3.8-Flash-Next, Qwen3.6, OLMoE.

## This console

1. Put the Colibri **launcher** in `engine/colibri/` (`coli.cmd` on Windows).  
   `powershell -File scripts\fetch_colibri.ps1`  
   Or unpack a [release](https://github.com/JustVugg/colibri/releases).
2. Put a **Colibri/HF model directory** (has `config.json` + safetensors) on a fast disk. Weights are huge (tens of GB to TB). We do **not** download them.
3. **Scan** in the console. The dir shows as `kind: colibri`. **Boot LLM**.
4. Chat goes to `127.0.0.1:11443` (`coli serve`). GGUF llama.cpp stays on `:11441`.

Do not ram-check the whole 372 GB file — Colibri only keeps dense weights resident.

## Honest limits

- Prefill of a long agent prompt on disk-streaming CPU can take a long time. Smoke-test with “hi” first.
- Public kit still defaults to small GGUF (Qwen 1.5B) for a usable agent loop.
- Hub never replace/delete CANON. Colibri is an inference backend, RESOURCE.

Upstream docs: https://github.com/JustVugg/colibri/blob/main/docs/quickstart.md
