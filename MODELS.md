# Generic boot model (legal to redistribute as a download instruction)

The SkillHub FULL zip is the **console only**. Weights are **not** inside the zip (too large; license stays with the model author).

## One generic GGUF we point to (Apache-2.0)

**Qwen2.5-1.5B-Instruct Q4_K_M** from the official Qwen org.

| | |
|--|--|
| File | `qwen2.5-1.5b-instruct-q4_k_m.gguf` |
| Size | ~1.04 GB |
| License | Apache License 2.0 (see the model card) |
| SHA-256 | `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e` |
| Download | https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf |
| Card | https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF |

Verify:

```
certutil -hashfile qwen2.5-1.5b-instruct-q4_k_m.gguf SHA256
```

Put the file in the console `models\` folder (create it). Boot `LYGO_LLM_CONSOLE.bat`, click **Scan drives**, select the Qwen 1.5B tag, **Boot LLM**.

This is enough for a KERNEL-level local agent loop (tools, search, SOUL/MEMORY). It is small; add larger GGUFs later if you have RAM.

## Embedding models (optional, for retrieval)

An embedding model returns **vectors**, not text. It is not a chat brain and cannot be abliterated -
it sits beside the brain on the embed port (`11442` console, `11452` stick), not in place of it.

**EmbeddingGemma 2 BF16** - Apache-2.0, 768-dim (MRL-truncatable to 512/256/128), 8,192-token window.

| | |
|--|--|
| File | `embeddinggemma-2-BF16.gguf` (a 310 MB `Q8_0` twin exists) |
| Size | ~532 MB |
| License | Apache License 2.0 (see the model card) |
| SHA-256 | `68bae29d62fb8c7d23e98d21fd4662753ddd636e6b62b8a70dfe059a9844f216` |
| Download | https://huggingface.co/ggml-org/embeddinggemma-2-GGUF/resolve/main/embeddinggemma-2-BF16.gguf |
| Card | https://ai.google.dev/gemma/docs/embeddinggemma/model_card_2 |

Needs engine **b11457** or newer: b10988 and b11074 both refuse it with
`unknown model architecture: 'gemma-embedding2'`. Once the engine is current, drop it in the vault
and scan - the console classifies it `kind: embed` on its own.

Two honest limits: the GGUF header reports a 262,144 context (a base-config artifact) while the model
card says 8,192, and the console clamps embed windows to `ctx_max` (16384 here) - so it launches at
twice its trained window. Embeddings for inputs under 8k are unaffected; lower `ctx_max` if you care.
And `/v1/embeddings` here is text-only: the `mmproj` sidecar loads but images/audio do not embed yet.

## Add more models (your downloads)

Drop any `*.gguf` into `models\`, or into the vault the machine declares with `LYGO_MODELS`
(`<MODELS>` on this PC). Scan again. Existing Ollama blobs can be imported once, read-only -
they are plain GGUF files and nothing here runs the daemon. You are responsible for each model's license (Llama, Gemma, etc.).

## Engine (not a model)

`engine\llama-server.exe` from ggml-org llama.cpp **b10988** Windows CPU zip. Not Ollama's nested copy.

Optional second engine: **Colibri** (`engine/colibri/coli.cmd`) — streams MoE experts from SSD. See `COLIBRI.md`. Scan a Hugging Face / Colibri model **directory** (not GGUF), then Boot. Slow without a fast disk. Do not auto-download 372 GB+ weights.

We do **not** ship Meta Llama weights with this pack.
