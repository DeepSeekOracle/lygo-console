"""Boot every model the console can see, measure it, and label what it can actually do.

This is the checker and the balancer. Two jobs, one pass:

* CHECK - boot the model through the console's own engine module (lygo_engine.boot), so what is
  measured is what the shipping launch path does, then run a real turn against it. A model that
  cannot load here is recorded with the arithmetic that says why, not left for an operator to
  discover by waiting.
* BALANCE - label each model by what it can do - text, coder, image in, image out, sound, embed -
  from its own GGUF metadata plus what the probe actually demonstrated. Labels are evidence, never
  vibes: binary string-grepping is not a capability test.

The result lands in save/model_check.json (per model, appended as it goes, so a crash or a timeout
loses nothing) and is stamped onto the registry record so the picker and the LLM tab can show it.

One model at a time, on purpose: one GPU, one set of weights. Standalone - it refuses to run while
another engine holds the port, because a measurement taken beside someone else's engine is a lie.
"""
from __future__ import annotations

import re

import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The checker's own port, so its engine log is llama-server-11471.log and the console's own log (and
# therefore envwatch's health verdict) is untouched by a measurement run.
CHECK_PORT = int(os.environ.get("LYGO_CHECK_PORT") or 11481)  # NOT 11471: backends owns that for its self-test

import model_fit  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAVE = ROOT / "save"
OUT = SAVE / "model_check.json"
LOG_DIR = SAVE / "logs"

# Labels the picker and the LLM tab render. Keep these short and literal: they are read at a glance.
CAP_TEXT = "text"          # answers a chat turn
CAP_CODER = "coder"        # writes code (name/arch claim AND the code probe)
CAP_IMAGE_IN = "image-in"  # can look at an image (a projector is attached)
CAP_IMAGE_OUT = "image-out"  # generates images
CAP_SOUND = "sound"        # audio in or out (speech, music)
CAP_EMBED = "embed"        # vectors, not chat
CAP_TOOLS = "tools"        # the template carries tool calls

CAP_ORDER = [CAP_TEXT, CAP_CODER, CAP_IMAGE_IN, CAP_IMAGE_OUT, CAP_SOUND, CAP_EMBED, CAP_TOOLS]

_CODER_HINTS = ("coder", "code", "codestral", "starcoder", "devstral")
_IMAGE_HINTS = ("image", "diffusion", "flux", "sd3", "sdxl", "vision")
_SOUND_HINTS = ("whisper", "audio", "speech", "tts", "music", "voice", "piper", "wav")


def caps_for(rec: dict[str, Any], header: dict[str, Any] | None = None) -> list[str]:
    """The labels a record's own facts support, before any probe runs.

    Facts beat names, so the order is: header architecture/kind, then an attached projector, then the
    name. A chat model with a projector really can be shown a picture; a name containing "coder" is
    only a claim until the probe answers a code question, and the probe result is recorded separately
    in caps_evidence.
    """
    h = dict(header or {})
    name = " ".join(str(x or "") for x in (rec.get("id"), Path(str(rec.get("path") or "")).name)).lower()
    arch = str(h.get("architecture") or rec.get("architecture") or "").lower()
    kind = str(rec.get("kind") or h.get("kind") or "chat").lower()
    caps: list[str] = []

    if kind in ("embed", "embedding") or "embed" in name or arch in ("bert", "nomic-bert"):
        caps.append(CAP_EMBED)
    if kind == "image" or any(w in name for w in _IMAGE_HINTS) or arch in ("flux", "sdxl", "stable-diffusion"):
        caps.append(CAP_IMAGE_OUT)
    if any(w in name for w in _SOUND_HINTS) or arch in ("whisper", "wav2vec2", "musicgen"):
        caps.append(CAP_SOUND)

    if CAP_EMBED not in caps and CAP_IMAGE_OUT not in caps:
        caps.append(CAP_TEXT)
    if CAP_TEXT in caps and any(w in name for w in _CODER_HINTS):
        caps.append(CAP_CODER)
    if rec.get("mmproj") or h.get("mmproj"):
        caps.append(CAP_IMAGE_IN)
    tmpl = str(h.get("chat_template") or "").lower()
    if "tool" in tmpl or "function" in tmpl:
        caps.append(CAP_TOOLS)
    return [c for c in CAP_ORDER if c in caps]


def _png_solid(rgb: tuple[int, int, int], size: int = 32) -> bytes:
    """A tiny solid-colour PNG, stdlib only.

    The vision probe has to show the model a real picture. A solid colour is the smallest honest test:
    a model that can see says the colour, a model that cannot see either refuses or invents, and both
    answers are recorded rather than assumed.
    """
    r, g, b = rgb
    raw = b"".join(b"\x00" + bytes((r, g, b)) * size for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _post(url: str, payload: dict[str, Any], key: str, timeout: float) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def _wait_health(port: int, timeout_s: float) -> tuple[bool, float]:
    """llama-server's own /health. True once it answers, with how long it took.

    MEASURED 2026-09-25: `_wait_health(port, 0.1)` took 3,005 ms - a 30x overrun of its own budget -
    because each attempt used a flat `timeout=3` and the budget was checked only between attempts. An
    attempt is now bounded by what is left of the budget, and a port that answers without being ready
    (a non-200) no longer spins without sleeping.
    """
    t0 = time.time()
    while True:
        left = timeout_s - (time.time() - t0)
        if left <= 0:
            return False, time.time() - t0
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=min(1.0, left)) as r:
                if r.status == 200:
                    return True, time.time() - t0
        except Exception:
            pass
        time.sleep(min(1.0, max(0.0, timeout_s - (time.time() - t0))))


def _log_tail(port: int, lines: int = 6) -> str:
    """The engine's own last words, for the failures that only the log explains (OOM, abort, arch)."""
    txt = _read_log(port)
    if not txt:
        return ""
    keep = [ln.strip() for ln in txt.splitlines() if ln.strip()][-lines:]
    return " | ".join(keep)[:600]


# A failure the ENGINE BUILD owns, not the rig: the file is fine and the box may well have room, but our
# loader is older than the model's metadata. The operator's next move differs (a newer engine build, or a
# different quant of the same model), so the record must never file it under "out of memory". Measured on
# this box 2026-09-20, both failed in ~7 s and neither was about RAM:
#   qwen3.6:latest        qwen35moe.rope.dimension_sections has wrong array length; expected 4, got 3
#   nemotron-3-super      tensor blk.1.ffn_down_exps.weight has wrong shape; got a 4-D tensor
_ENGINE_LIMITS = (
    ("wrong array length", "the engine build cannot read this model's metadata - a newer llama.cpp build, "
                           "or a different quant of the same model, is the fix"),
    ("wrong shape", "the engine build cannot read this model's tensor layout - a newer llama.cpp build, "
                    "or a different quant of the same model, is the fix"),
    ("unknown architecture", "the engine build has no loader for this architecture - a newer llama.cpp "
                             "build is the fix"),
    ("unsupported", "the engine build does not support this model's format"),
)


def classify_failure(log_tail: str, why: str = "", *,
                     fit: dict[str, Any] | None = None) -> tuple[str, str]:
    """RIG or ENGINE. Never blame the operator's box for a loader that is simply older than the file.

    The fit plan answers FIRST when it has an answer (defect #23). The class used to be sniffed off the
    engine's log tail alone, so `nemotron-3-super` - 86 GB against a ~24 GB usable box - read `engine` in
    one sweep and `rig` in the next, because the tail differs run to run. Arithmetic does not. A `too_big`
    verdict is a rig limit whatever the last few log lines happen to say; a model that FITS is still left
    to the tail, where the engine-limit case (a newer llama.cpp build is the fix) really does live.
    """
    if str((fit or {}).get("verdict") or "") == "too_big":
        return "rig", ("this host cannot hold that file - a rig limit, whatever the engine's last lines "
                       "say (the fit plan decided first)")
    text = f"{log_tail or ''} {why or ''}".lower()
    for needle, hint in _ENGINE_LIMITS:
        if needle in text:
            return "engine", hint
    return "rig", ("this is a rig limit, not an agent fault - the console stays up and the operator can "
                   "pick another route")


_HASH_RUN = re.compile(r"[0-9A-Fa-f]{32,}")
# A rate needs a sample. Under this, the count is recorded and the rate is left unstated.
RATE_MIN_TOKENS = 16


def rate_from(timings: dict[str, Any], *, minimum: int = RATE_MIN_TOKENS) -> dict[str, Any]:
    """Turn the engine's own timings into rates only when the sample can carry one.

    Measured 2026-09-20: a two-token answer to a yes/no prompt recorded 16.6 tok/s for a model that
    really does 135.8 tok/s on this box - and that number is what a human reads in the choice box. Below
    the threshold the count is recorded and the rate stays unstated, so a label can say "too few tokens
    to rate" instead of something false.
    """
    counts: dict[str, Any] = {"prompt_n": timings.get("prompt_n"), "predicted_n": timings.get("predicted_n")}
    prompt_n = int(timings.get("prompt_n") or 0)
    out_n = int(timings.get("predicted_n") or 0)
    if timings.get("prompt_per_second") and prompt_n >= minimum:
        counts["prefill_tps"] = round(float(timings["prompt_per_second"]), 1)
    if timings.get("predicted_per_second") and out_n >= minimum:
        counts["gen_tps"] = round(float(timings["predicted_per_second"]), 1)
    if "gen_tps" not in counts and out_n:
        counts["rate_note"] = f"{out_n} generated tokens - too few to rate"
    return counts
# llama.cpp's own sentence when it really offloads: "load_tensors: offloading 33 repeating layers to GPU".
# Absent on a CPU-only load, which is what makes it evidence rather than intention.
_OFFLOAD_RE = re.compile(r"offload(?:ing|ed)\s+(\d+)(?:/\d+)?\s+(?:repeating\s+)?layers?\s+to\s+GPU", re.I)
_DEVICE_RE = re.compile(r"\b(CUDA|Vulkan|SYCL|ROCm|Metal)(\d+)\b", re.I)


_DEV_MEMO: dict[Any, list[str]] = {}


def _decode(raw: bytes) -> str:
    """Decode an engine log whichever way the build wrote it.

    Measured 2026-09-20: this llama-server (b10988) writes its log UTF-8, while other builds write
    UTF-16. Decoding the wrong way raises nothing - it returns mojibake that reads like a real but
    unreadable log line, so the old utf-16-then-utf-8 fallback never fired and "why" came out as
    garbage. Score both and take the printable one; the ratio makes the choice verifiable, not a guess.
    """
    best, score = "", -1.0
    for enc in ("utf-8", "utf-16", "utf-16-be"):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
        good = sum(1 for ch in text if 32 <= ord(ch) < 127 or ch in "\r\n\t")
        ratio = good / max(1, len(text))
        if ratio > score:
            best, score = text, ratio
    return best


def _read_log(port: int, cap: int = 400_000) -> str:
    """The engine's log for this port, whole (capped) and correctly decoded."""
    p = LOG_DIR / f"llama-server-{port}.log"
    if not p.is_file():
        return ""
    try:
        return _decode(p.read_bytes()[-cap:])
    except OSError:
        return ""


def _log_text(port: int, cap: int = 400_000) -> str:
    """The engine's log for this port, whole (capped): offload lines are written at load time."""
    return _read_log(port, cap)


def gpu_evidence(port: int) -> dict[str, Any]:
    """What this run really did with the GPU: which build answered, and what that build can see.

    Three facts, three sources, none of them guessed:
      * ``gpu_backend`` - the build that answered, by directory ("cpu" for the shipped engine)
      * ``gpu_device`` - the first device that build reports through its own ``--list-devices``
      * ``gpu_layers_used`` - the offload line llama.cpp prints when layers really go on the card, or
        ``None`` when this build's verbosity does not print one.

    ``None`` is not ``0``. A missing line means "not stated"; recording a false zero here is the same
    defect as the ``no_gpu_device`` mislabel that wrote off ten models on a box with a working card.
    """
    out: dict[str, Any] = {"gpu_backend": "", "gpu_device": "", "gpu_layers_used": None}
    try:
        import engine as _engine
        import perf as _perf

        exe = _engine.resolve_binary()
        if exe is not None:
            out["gpu_backend"] = "cpu" if Path(exe).parent == Path(_engine.ENGINE_DIR) else Path(exe).parent.name
            key = Path(exe).parent
            if key not in _DEV_MEMO:
                _DEV_MEMO[key] = [str(d.get("name") if isinstance(d, dict) else d)
                                  for d in (_perf.engine_devices(exe) or [])]
            devs = _DEV_MEMO[key]
            out["gpu_device"] = devs[0][:96] if devs else ""
    except Exception:  # noqa: BLE001 - evidence is never allowed to break a check
        pass
    layers = [int(m.group(1)) for m in _OFFLOAD_RE.finditer(_read_log(port))]
    if layers:
        out["gpu_layers_used"] = max(layers)
    return out


def redact(text: str) -> str:
    """No hash-shaped run leaves this module on its way to a panel.

    An engine log line can name a CAS blob (``sha256-<64 hex>``), and a panel carrying a 64-hex run is
    indistinguishable from a panel leaking a hash - which is exactly what the envwatch card forbids, and
    it was right to. Redact at the source so every surface downstream is safe by construction.
    """
    return _HASH_RUN.sub("[hash]", str(text or ""))


def own_words(log_tail: str) -> str:
    """The engine's own sentence about why it refused - the line that actually explains it.

    "boot raised RuntimeError: llama-server exited 1" is true and useless. The loader wrote the cause in
    its log; pick that line (a known engine limit first, then any error line) so the operator reads the
    reason rather than our exception wrapper.
    """
    lines = [ln.strip() for ln in str(log_tail or "").replace(" | ", "\n").splitlines() if ln.strip()]
    for ln in lines:
        low = ln.lower()
        if any(needle in low for needle, _ in _ENGINE_LIMITS):
            return redact(ln)[:240]
    for ln in lines:
        low = ln.lower()
        if any(w in low for w in ("error", "failed", "abort", "out of memory")):
            return redact(ln)[:240]
    return redact(lines[-1])[:240] if lines else ""


def fits_here(need_mib: int, usable_mib: int) -> bool:
    """The balancer's first rule, as arithmetic: a model bigger than this host can spare is VISIBLE
    but not runnable here, and no boot is attempted to prove it - waiting for an OOM teaches nothing
    that the sizes did not already say. An unknown size (0 on either side) is not a refusal: the boot
    is then the only honest test.
    """
    return not (usable_mib and need_mib and need_mib > usable_mib)


def check_one(rec: dict[str, Any], *, boot_timeout_s: float = 180.0,
              turn_timeout_s: float = 180.0, probe_ctx: int = 8192,
              keep_engine: bool = False) -> dict[str, Any]:
    """Boot one record, probe it, measure it, stop it. Returns the record that gets persisted."""
    import lygo_engine
    import engine

    path = Path(str(rec.get("path") or ""))
    out: dict[str, Any] = {
        "id": rec.get("id"),
        "path": str(path),
        "kind": rec.get("kind") or "chat",
        "aliases": list(rec.get("aliases") or []),
        "mmproj": rec.get("mmproj"),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        out["bytes"] = path.stat().st_size
    except OSError:
        out["bytes"] = int(rec.get("bytes") or 0)

    header: dict[str, Any] = {}
    try:
        from gguf_header import parse_gguf_header
        header = dict(parse_gguf_header(path) or {})
    except Exception as e:
        header = {"header_error": f"{type(e).__name__}: {e}"}
    out["architecture"] = header.get("architecture") or rec.get("architecture")
    out["caps"] = caps_for(rec, header)
    out["caps_evidence"] = {"from": "header+name"}

    if not path.is_file():
        out.update(verdict="missing", why="no file at that path")
        return out

    # BALANCE first: the arithmetic that decides whether a boot here can mean anything.
    try:
        fit = model_fit.verdict(
            model_bytes=int(out["bytes"]),
            ctx=probe_ctx,
            dims=header,
            vram_free_mib=model_fit.vram_free_mib(),
            ram_total=model_fit.ram_total_mib(),
            kv_type="q8_0",
        )
    except Exception as e:
        # The measurement must never be the thing that breaks: an unreadable header leaves the fit
        # unknown, and the boot itself is then the test.
        fit = {"verdict": "unknown", "reason": f"fit failed: {type(e).__name__}: {e}"}
    out["fit"] = {"verdict": fit.get("verdict"), "ngl": fit.get("ngl"), "mode": fit.get("mode"),
                  "reason": fit.get("reason"), "need_mib": fit.get("need_mib"),
                  "ram_usable_mib": fit.get("ram_usable_mib")}
    need = int(fit.get("need_mib") or 0)
    usable = int(fit.get("ram_usable_mib") or 0)
    out["predicted"] = "too_large" if not fits_here(need, usable) else "fits"
    if not fits_here(need, usable):
        # The steward's rule: attempt it anyway. A prediction is not a measurement, and a model that
        # cannot boot here must be WATCHED failing softly, named as a rig limit and routed around -
        # never quietly dropped from the list before anyone tried.
        out["predicted_why"] = (f"needs {need} MiB, this host can spare {usable} MiB "
                                f"- attempting anyway, on the operator's instruction")

    if out["kind"] in ("mmproj", "projector") or path.stem.lower().endswith(("-mmproj", "mmproj")):
        out.update(verdict="not_a_model", why="a projector, not a model")
        return out

    rec_boot = dict(rec)
    rec_boot["path"] = str(path)
    rec_boot["ctx"] = min(int(rec.get("ctx") or probe_ctx), probe_ctx)
    rec_boot["n_gpu_layers"] = None  # never an instruction: the launch plan decides the offload
    state: dict[str, Any] = {}
    key = "lygo-check-" + os.urandom(4).hex()
    _boot_t0 = time.time()
    try:
        lygo_engine.boot(rec_boot, api_key=key, state=state, port=CHECK_PORT)
    except Exception as e:
        # The engine's own last words matter most here: "exited 1" alone tells the operator nothing.
        # The log tail is what turns a bare failure into "this box cannot hold that file, here is why".
        _p = int(state.get("engine_port") or CHECK_PORT)
        _tail = _log_tail(_p)
        _cls, _hint = classify_failure(_tail, str(e), fit=out.get("fit"))
        _own = own_words(_tail)
        out.update(verdict="failed_soft", fail_class=_cls,
                   # Lead with the engine's own words: they name the cause. Our exception stays on the
                   # record as `raised`, so the reason and the wrapper are never confused.
                   why=(_own or f"boot raised {type(e).__name__}: {e}"),
                   raised=f"{type(e).__name__}: {e}", log_tail=redact(_tail), hint=_hint)
        return out
    port = int(state.get("engine_port") or 0)
    out["engine"] = state.get("engine")
    out["engine_port"] = port
    # The plan's offload lives under plan["llama"]["ngl"] (the engine's own arg builder reads
    # lp["ngl"]); reading plan["ngl"] recorded None on every model, so the balancer's decision was
    # invisible in the pane. Keep the fallbacks: a plan that changes shape must not blank a number.
    _plan = state.get("lygo_engine") or {}
    out["planned_ngl"] = ((_plan.get("llama") or {}).get("ngl")
                          if isinstance(_plan.get("llama"), dict) else None) or _plan.get("ngl")
    out["brain"] = state.get("brain")
    # Record what the engine did with the GPU while the model is loaded - the evidence a panel may quote.
    out.update(gpu_evidence(port))

    try:
        if port <= 0:
            out.update(verdict="failed_soft", fail_class="rig",
                       why=f"no engine port; brain={state.get('brain')} err={state.get('error')}",
                       hint="the launch plan refused or could not place this model - a rig limit, "
                            "not an agent fault")
            return out
        out["boot_s"] = round(time.time() - _boot_t0, 1)
        up, boot_s = _wait_health(port, boot_timeout_s)
        out["health_wait_s"] = round(boot_s, 1)
        if not up:
            out.update(verdict="failed_soft", fail_class="rig",
                       why=f"no health after {boot_timeout_s:.0f}s", log_tail=_log_tail(port),
                       hint="the engine either ran out of memory or never finished loading - a rig "
                            "limit, not an agent fault")
            return out

        base = f"http://127.0.0.1:{port}"
        probe: dict[str, Any] = {"ctx": rec_boot["ctx"], "max_tokens": 64}

        if CAP_EMBED in out["caps"]:
            st, js = _post(base + "/v1/embeddings", {"input": "lygo lattice"}, key, turn_timeout_s)
            vec = (js.get("data") or [{}])[0].get("embedding") if isinstance(js, dict) else None
            probe["embed_dim"] = len(vec) if isinstance(vec, list) else None
            probe["http"] = st
            out["verdict"] = "runs" if isinstance(vec, list) and vec else "failed"
            out["why"] = "embeddings endpoint answered" if vec else f"embeddings failed (http {st})"
            out["probe"] = probe
            return out

        # An ordinary turn, then a code question: the two things an operator does with a console.
        # The turn must also EARN its rate: "Reply with exactly: OK" (8 tokens max) can never rate
        # generation, and a rate read from two tokens is how 16.6 tok/s got attached to a model that does
        # 135.8. A fixed list needs no inventiveness from any model and yields a real sample.
        t0 = time.time()
        st, js = _post(base + "/v1/chat/completions", {
            "messages": [{"role": "user",
                          "content": "Count from 1 to 40, one number per line. Numbers only, no words."}],
            "max_tokens": 64, "temperature": 0, "stream": False,
        }, key, turn_timeout_s)
        probe["http"] = st
        probe["wall_s"] = round(time.time() - t0, 2)
        if st != 200:
            # The engine is UP and the turn still failed: that is a wiring answer, not a dead model, so
            # it is recorded as its own class instead of being called a rig limit.
            out.update(verdict="failed", fail_class="wiring", why=f"chat http {st}",
                       probe=probe, log_tail=_log_tail(port),
                       hint="the engine booted and answered health, so this is console wiring")
            return out
        choice = (js.get("choices") or [{}])[0]
        text = ((choice.get("message") or {}).get("content") or "").strip()
        probe["answer"] = text[:120]
        probe["answered"] = bool(text)
        timings = js.get("timings") or {}
        if timings:
            probe.update(rate_from(timings))

        st2, js2 = _post(base + "/v1/chat/completions", {
            "messages": [{"role": "user",
                          "content": "Write one line of Python that reverses a string s. Code only."}],
            "max_tokens": 48, "temperature": 0, "stream": False,
        }, key, turn_timeout_s)
        if st2 == 200:
            code = (((js2.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            probe["code_answer"] = code.strip()[:160]
            probe["code_ok"] = ("::-1" in code) or ("reversed(" in code)
            if probe["code_ok"] and CAP_CODER not in out["caps"]:
                out["caps"].append(CAP_CODER)
                out["caps"] = [c for c in CAP_ORDER if c in out["caps"]]
            out["caps_evidence"]["code_probe"] = "wrote a correct reversal" if probe["code_ok"] else "did not"

        if CAP_IMAGE_IN in out["caps"]:
            import base64
            img = base64.b64encode(_png_solid((255, 0, 0))).decode("ascii")
            st3, js3 = _post(base + "/v1/chat/completions", {
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "What colour fills this image? One word."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}},
                ]}],
                "max_tokens": 16, "temperature": 0, "stream": False,
            }, key, turn_timeout_s)
            seen = (((js3.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            probe["vision_http"] = st3
            probe["vision_answer"] = seen[:80]
            probe["vision_ok"] = "red" in seen.lower()
            out["caps_evidence"]["image_probe"] = ("said red" if probe["vision_ok"]
                                                  else f"answered {seen[:40]!r}" if seen else "no answer")
            if not probe["vision_ok"] or st3 != 200:
                out["caps"] = [c for c in out["caps"] if c != CAP_IMAGE_IN]
                out["caps_evidence"]["image_probe"] += " - label withdrawn"

        out["probe"] = probe
        out["verdict"] = "runs"
        rated = probe.get("gen_tps")
        out["why"] = (f"answered in {probe.get('wall_s')}s; {rated} tok/s generation" if rated
                      else f"answered in {probe.get('wall_s')}s ({probe.get('rate_note') or 'rate unstated'})")
        return out
    finally:
        if not keep_engine and port:
            try:
                engine.stop_port(port)
            except Exception:
                pass


def _records() -> list[dict[str, Any]]:
    p = SAVE / "registry.json"
    if not p.is_file():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    ms = d.get("models") if isinstance(d, dict) else d
    return list(ms or [])


def _save_result(result: dict[str, Any]) -> None:
    """Append as we go: a long run must never lose the models it already proved."""
    doc: dict[str, Any] = {"models": {}}
    if OUT.is_file():
        try:
            doc = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            doc = {"models": {}}
    doc.setdefault("models", {})[str(result.get("id"))] = result
    doc["host"] = {"ram_mib": model_fit.ram_total_mib(), "ram_usable_mib": model_fit.ram_usable_mib(model_fit.ram_total_mib()),
                   "vram_free_mib": model_fit.vram_free_mib(), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    SAVE.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    # Stamp the labels onto the registry so the picker and the LLM tab can show them without a probe.
    rp = SAVE / "registry.json"
    if not rp.is_file():
        return
    try:
        reg = json.loads(rp.read_text(encoding="utf-8"))
        models = reg.get("models") if isinstance(reg, dict) else reg
        ident = _file_identity_soft(result)
        for m in models or []:
            same = (str(m.get("id")) == str(result.get("id"))
                    or str(m.get("path")) == str(result.get("path"))
                    or (bool(ident) and _file_identity_soft(m) == ident))
            if same:
                m["caps"] = result.get("caps") or []
                m["checked"] = {"verdict": result.get("verdict"), "why": (result.get("why") or "")[:200],
                                "at": result.get("checked_at"),
                                "boot_s": result.get("boot_s"),
                                "gen_tps": (result.get("probe") or {}).get("gen_tps"),
                                "prefill_tps": (result.get("probe") or {}).get("prefill_tps")}
        # A label belongs to the FILE, so a twin naming the same weights is folded into the record that
        # was measured (its name survives in `also_known_as`) rather than keeping a stale rate of its own.
        # One file, one row, one rate - defect #24.
        if isinstance(reg, dict):
            try:
                import registry  # noqa: PLC0415

                reg["models"] = registry.dedupe_by_file(list(models or []),
                                                        str(reg.get("selected") or ""))
            except Exception:  # noqa: BLE001
                pass
        rp.write_text(json.dumps(reg, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        sys.stderr.write(f"[model-check] registry stamp failed: {type(e).__name__}: {e}\n")


def _file_identity_soft(rec: dict[str, Any]) -> str:
    """registry's file identity for a record, or '' when the registry cannot be asked."""
    try:
        import registry  # noqa: PLC0415

        return str(registry._file_identity(rec) or "")
    except Exception:  # noqa: BLE001
        return ""


def _selected_id() -> str:
    """The operator's own pick, read from the same registry the sweep takes its records out of."""
    try:
        d = json.loads((SAVE / "registry.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    return str((d or {}).get("selected") or "") if isinstance(d, dict) else ""


def _one_record_per_file(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One weights file is ONE model (defect #24).

    Measured 2026-09-20: `gemma4:12b` (a CAS blob whose filename carries the content hash) and
    `gemma4-12b` (the same 7.38 GB under a store path, hash in `sha256`) are one file, listed twice in
    the choice box. The sweep walked both because it deduped on the path STRING, so the twin that was
    never measured kept a stale CPU-era rate (6.8 tok/s, no `gpu_backend`) beside the record measured at
    17.0 on the CUDA build - one model, two labels, one of them false. `registry.dedupe_by_file` owns the
    identity (content hash first, path second), so the sweep asks it instead of inventing a second answer.
    """
    try:
        import registry  # noqa: PLC0415

        return registry.dedupe_by_file(recs, _selected_id())
    except Exception:  # noqa: BLE001 - a missing registry must not stop a sweep
        return recs


def run(ids: list[str] | None = None, *, limit: int | None = None, boot_timeout_s: float = 180.0) -> dict[str, Any]:
    """Check every registry model (or the named ones). Smallest first, so proof arrives early."""
    import engine

    busy = (engine.runner_for(CHECK_PORT)
            or engine.runner_for(int(os.environ.get("LYGO_ENGINE_PORT") or 11441)))
    if busy is not None:
        return {"error": "an engine is already running - close the console first, a measurement beside "
                         "someone else's engine is a lie", "port": getattr(busy, "port", None)}

    recs = _one_record_per_file([r for r in _records() if r.get("path")])
    seen: set[str] = set()
    todo: list[dict[str, Any]] = []
    for r in sorted(recs, key=lambda r: int(r.get("bytes") or 0)):
        p = str(r.get("path"))
        if p in seen:
            continue
        seen.add(p)
        todo.append(r)
    if ids:
        want = {i.lower() for i in ids}
        todo = [r for r in todo if str(r.get("id", "")).lower() in want]
    if limit:
        todo = todo[:limit]

    results = []
    for i, rec in enumerate(todo, 1):
        t0 = time.time()
        res = check_one(rec, boot_timeout_s=boot_timeout_s)
        res["took_s"] = round(time.time() - t0, 1)
        _save_result(res)
        results.append(res)
        print(f"[{i}/{len(todo)}] {res.get('id')}: {res.get('verdict')} "
              f"{'+'.join(res.get('caps') or [])} ({res.get('took_s')}s) {res.get('why')}", flush=True)
    return {"checked": len(results), "results": results}


if __name__ == "__main__":
    args = sys.argv[1:]
    want = [a for a in args if not a.startswith("-")]
    lim = None
    for a in args:
        if a.startswith("--limit="):
            lim = int(a.split("=", 1)[1])
    print(json.dumps(run(want or None, limit=lim), ensure_ascii=False, indent=2)[:4000])
