"""LYGO Engine — hybrid brain.

One planner, two runtimes:
- llama.cpp GGUF: mmap SSD + RAM + optional GPU layers (onboard)
- Colibri MoE: VRAM + RAM + SSD expert streaming (JustVugg/colibri)

Placement only changes speed. Semantics stay the model. RESOURCE backends; CANON unchanged.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import perf
from engine import available_ram_bytes, clamp_ctx, ram_ok, resolve_binary, runner_for, spawn_runner, total_ram_bytes
from paths import (
    COLIBRI_PORT,
    CONSOLE_JSON,
    DEFAULT_PORT,
    ENGINE_DIR,
    LLAMA_PORT,
    LOCAL_JSON,
    console_limits,
)

GIB = 1024**3


def cpu_threads() -> int:
    """One thread policy for the whole kit — see perf.auto_threads()."""
    return perf.auto_threads()


def vram_free_bytes() -> int:
    nvsmi = shutil.which("nvidia-smi")
    if not nvsmi:
        return 0
    try:
        r = subprocess.run(
            [nvsmi, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=4,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        if r.returncode != 0:
            return 0
        total = 0
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                total += int(line) * 1024 * 1024
        return total
    except Exception:
        return 0


def backend_selection(*, refresh: bool = False) -> dict[str, Any]:
    """Which engine build this host may use, straight from the backend layer.

    backends.py installs nothing on its own: it looks at engine/backends/, remembers this
    host's verdict, and self-tests a candidate with a real model load before trusting it.
    A failure here must never stop a boot, so every error becomes a CPU answer.
    """
    try:
        import backends as be
        import registry

        models = [m for m in ((registry.load() or {}).get("models") or []) if isinstance(m, dict)]
        gpu_cfg = str(console_limits().get("gpu") or "auto")
        return be.ensure(models=models, threads=cpu_threads(), host=perf.host_id(),
                         enabled=gpu_cfg != "off", refresh=refresh)
    except Exception as exc:
        return {
            "backend": "cpu",
            "engine_dir": str(ENGINE_DIR),
            "kind": "base",
            "gpu_ok": False,
            "reason": "backend_layer_error",
            "detail": f"{type(exc).__name__}: {exc}",
        }


def probe() -> dict[str, Any]:
    ram = available_ram_bytes()
    vram = vram_free_bytes()
    sel = backend_selection()
    chosen = Path(str(sel.get("engine_dir") or ENGINE_DIR))
    return {
        "ram_bytes": ram,
        "ram_total_bytes": total_ram_bytes(),
        "ram_gib": round(ram / GIB, 2) if ram else 0,
        "vram_bytes": vram,
        "vram_gib": round(vram / GIB, 2) if vram else 0,
        "threads": cpu_threads(),
        "llama_binary": bool(resolve_binary()),
        "ssd_stream": True,
        "backends": perf.engine_backends(chosen),
        "devices": perf.engine_devices(chosen / "llama-server.exe"),
        "backend": str(sel.get("backend") or "cpu"),
        "engine_dir": str(chosen),
        "gpu_ok": bool(sel.get("gpu_ok")),
        "gpu_reason": str(sel.get("reason") or ""),
        "gpu_detail": str(sel.get("detail") or ""),
        "gpu_tested_now": bool(sel.get("tested")),
    }


def _is_colibri(rec: dict[str, Any]) -> bool:
    return rec.get("engine") == "colibri" or rec.get("kind") == "colibri"


def _is_moe(rec: dict[str, Any]) -> bool:
    blob = " ".join(
        str(rec.get(k) or "")
        for k in ("architecture", "id", "path", "note")
    ).lower()
    return any(x in blob for x in ("moe", "mixtral", "deepseek", "qwen2moe", "glm", "kimi", "olmoe"))


def flash_attn_on() -> bool:
    """console.json/local.json "flash_attn": "on" forces the flag; otherwise the measured default."""
    try:
        import json as _json

        cfg: dict[str, Any] = {}
        for p in (CONSOLE_JSON, LOCAL_JSON):
            if p.is_file():
                raw = _json.loads(p.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    cfg = {**cfg, **raw}
        return str(cfg.get("flash_attn") or "off").strip().lower() in ("on", "true", "1", "yes")
    except Exception:  # noqa: BLE001 - a preference must never stop a boot
        return False


def plan(rec: dict[str, Any]) -> dict[str, Any]:
    """VRAM / RAM / SSD placement. Does not silently change precision."""
    hw = probe()
    ram = int(hw["ram_bytes"] or 0)
    vram = int(hw["vram_bytes"] or 0)
    coli = _is_colibri(rec)
    backend = "colibri" if coli else "llama"
    model_path = Path(rec.get("path") or "")
    model_bytes = int(rec.get("bytes") or 0)
    if not model_bytes and model_path.is_file():
        try:
            model_bytes = model_path.stat().st_size
        except OSError:
            model_bytes = 0
    lim = console_limits()
    # Placement needs the KV cache, not just the weights: on this host the cache is a few hundred
    # MiB and on a larger context it is the difference between a full offload and a partial one.
    # The figures come from the model's own GGUF header (n_layer, n_kv_head, key/value length).
    kv_type = perf.sanitize_kv_type(lim.get("kv_type"))
    # The engine clamps a model's native context to ctx_max before it launches, so the cache has
    # to be sized at that same window. Sizing it at the native 32k while the engine runs 16k
    # over-charged the plan by 2x, which is how a GPU that would have fit gets demoted.
    ctx_planned = clamp_ctx(
        rec.get("ctx"),
        lim["ctx_max"],
        architecture=rec.get("architecture"),
        kind=rec.get("kind"),
        model_id=rec.get("id"),
    )
    dims: dict[str, Any] = {}
    try:
        if model_path.is_file():
            import gguf_header

            dims = gguf_header.parse_gguf_header(model_path).get("found") or {}
    except Exception:  # noqa: BLE001 - an unreadable header means "charge the flat allowance"
        dims = {}
    kv_mib = perf.kv_cache_mib(perf.kv_bytes_per_token(dims), ctx_planned, kv_type)
    prof = perf.resolve(
        lim=lim,
        hw=hw,
        model_bytes=model_bytes,
        model_id=str(rec.get("id") or model_path.stem),
        kv_mib=kv_mib,
    )
    ngl = int(prof["ngl"])
    threads = int(prof["threads"])
    pin_gib = max(2, int((ram * 0.55) / GIB)) if ram else 8
    cuda_expert = "auto" if vram >= 4 * GIB else "0"
    return {
        "backend": backend,
        "signature": "Δ9Φ963-LYGO-ENGINE-HYBRID-v1",
        "reason": "colibri_moe_stream" if coli else "llama_mmap_ssd_gpu",
        "moe": _is_moe(rec) or coli,
        "tiers": {
            "vram_gib": hw["vram_gib"],
            "ram_gib": hw["ram_gib"],
            "ssd_mmap": True,
            "policy": "placement_changes_speed_not_weights",
        },
        "llama": {
            "ngl": ngl,
            "threads": threads,
            "mmap": True,
            "mlock": False,
            # Honest flag, and now a measured one. This used to read "mode != cpu" - every GPU host
            # was told flash attention was on while spawn_runner never sent the flag. Measured then,
            # -fa on was SLOWER for prompt processing (2620 vs 3246 tok/s) and it shipped off.
            # Re-measured 2026-09-20 on the current build (0.4.1-dev 10988, -ngl 79 -t 6, q8_0 KV):
            # -fa on is
            # FASTER on both halves - prompt processing 154.1 vs 141.7 t/s, generation 15.15 vs 13.00 t/s
            # so config/console.json ships "on". Without a device to accelerate it stays off: with no
            # GPU the flag buys nothing, and a plan must not advertise a benefit this host cannot feel.
            "flash_attn": flash_attn_on() and bool(hw.get("devices")),
            # Every one of these is read back by boot() and really sent to llama-server; a knob
            # that only exists in the plan is a knob that lies about the running engine.
            "kv_type": kv_type,
            "kv_mib": kv_mib,
            "ctx": ctx_planned,
            "batch": int(lim.get("batch") or 0),
            "ubatch": int(lim.get("ubatch") or 0),
        },
        "perf": prof,
        "colibri": {
            "pin_gib": pin_gib,
            "cuda_expert_gb": cuda_expert,
            "port": COLIBRI_PORT,
        },
        "port": COLIBRI_PORT if coli else LLAMA_PORT,
        "hardware": hw,
        "limits": {
            "source": lim["source"],
            "ctx_default": lim["ctx_default"],
            "ctx_max": lim["ctx_max"],
        },
    }


def _boot_log_tail(port: int) -> str:
    """The engine's own last words for this boot. Imported lazily: lygo_engine is loaded by model_check."""
    try:
        import model_check  # noqa: PLC0415

        return model_check._log_tail(int(port))
    except Exception:  # noqa: BLE001 - evidence is never allowed to break a boot
        return ""


def _drop_backend(pl: dict[str, Any], why: str, *, tail: str = "") -> str:
    """Retire a GPU backend that just killed the engine on this boot. Returns '' if there was none.

    Whether this failure says anything about the BUILD is not this function's question to answer on its
    own: `backends.boot_condemnation` owns the rules (a busy card, a fault, a model our loader cannot
    read, and no engine words at all are all moments, not properties). Measured 2026-09-20 22:54 - this
    function wrote `bad`, deactivated the build and cleared the proven selection on a contended launch,
    so the next console came up on the CPU engine with nothing saying why. The fallback for THIS boot is
    unchanged: the console must come up.
    """
    name = str((pl.get("perf") or {}).get("backend") or "cpu")
    if name == "cpu":
        return ""
    try:
        import backends as be

        info = be.backend_info(name)
        may, refused = be.boot_condemnation(name, detail=why, log_tail=tail)
        (pl.get("perf") or {})["gpu_ok"] = False
        if not may:
            # No verdict, no deactivation, no cleared selection: the store is left exactly as it was, and
            # the next boot decides on the same evidence this one had.
            return (f"{name} failed at launch on this host ({why}); running the shipped engine for this "
                    f"boot - no verdict recorded ({refused})")
        be.remember_backend(perf.host_id(), name, verdict="bad",
                            key=be.backend_key(name, info), detail=f"launch_failed: {why}")
        be.deactivate(name, info)
        be.clear_active()
        return (f"{name} failed at launch on this host ({why}); backend dropped, "
                f"retrying on the shipped engine")
    except Exception as exc:
        return f"backend {name} looked broken but could not be retired: {type(exc).__name__}: {exc}"


def boot(rec: dict[str, Any], *, api_key: str, state: dict[str, Any],
         port: int | None = None) -> str:
    """Spawn the planned backend. Returns brain status string."""
    from colibri import resolve_coli, spawn_colibri

    p = Path(rec.get("path") or "")
    pl = plan(rec)
    state["lygo_engine"] = pl
    state.pop("perf_fallback", None)
    if pl["backend"] == "colibri":
        if resolve_coli() is None:
            state["brain"] = "missing_colibri"
            state["error"] = "coli launcher missing — scripts/fetch_colibri.ps1"
            return "missing_colibri"
        port = COLIBRI_PORT
        with __import__("engine").ENGINE_LOCK:
            existing = runner_for(port)
            if existing and existing.gguf == str(p):
                state["brain"] = "ready"
                state["engine"] = "lygo-colibri"
                state["engine_port"] = port
                return "ready"
            extra = {
                "PIN_GB": str(pl["colibri"]["pin_gib"]),
                "CUDA_EXPERT_GB": str(pl["colibri"]["cuda_expert_gb"]),
            }
            spawn_colibri(
                port=port,
                model_dir=p,
                alias=str(rec.get("id") or p.name),
                api_key=api_key,
                extra_env=extra,
            )
        state["brain"] = "ready"
        state["engine"] = "lygo-colibri"
        state["engine_port"] = port
        state["error"] = None
        state["selected"] = rec.get("id")
        return "ready"

    if resolve_binary() is None:
        state["brain"] = "missing"
        return "missing"
    perf_rec = pl["perf"]
    perf_rec.setdefault("effective", {})
    size = int(rec.get("bytes") or (p.stat().st_size if p.is_file() else 0))
    ram = int(pl["hardware"].get("ram_bytes") or 0)
    # mmap + SSD: do not require the whole GGUF to fit in RAM (Colibri lesson).
    if ram and ram < 2.5 * GIB:
        state["brain"] = "ram_refused"
        return "ram_refused"
    if size and size < ram * 0.45 and not ram_ok(size):
        # tiny host + small model still honor headroom
        if ram < 6 * GIB:
            state["brain"] = "ram_refused"
            return "ram_refused"
    # The port is a PARAMETER so a tool can measure models without writing into the console's own engine
    # log. The log is named by port (engine.py: LOGS/llama-server-<port>.log), and a checker's failed
    # model loads left "engine fault x119" in the operator's engine log - read by envwatch as a fault on a
    # console that was in perfect health. A measurement must never leave a mark on what it measures.
    port = int(port or LLAMA_PORT)
    lp = pl["llama"]
    with __import__("engine").ENGINE_LOCK:
        existing = runner_for(port)
        if existing and existing.gguf == str(p):
            # Already up from an earlier launch: report the plan and say so, rather than
            # claiming a profile this process never verified.
            perf_rec["effective"] = {"ngl": None, "threads": None,
                                     "note": "engine already running from an earlier launch"}
            state["brain"] = "ready"
            state["engine"] = "lygo-llama"
            state["engine_port"] = port
            return "ready"
        # Ask registry for the projector, not the raw field: a record scanned off a disk carries None
        # while the projector sits right beside the model file (see registry.mmproj_for).
        from registry import mmproj_for as _mmproj_for

        mm = _mmproj_for(rec)
        lim = console_limits()
        ctx = clamp_ctx(
            rec.get("ctx"),
            lim["ctx_max"],
            architecture=rec.get("architecture"),
            kind=rec.get("kind") or "chat",
            model_id=rec.get("id"),
        )
        # The plan decides the offload. The record's n_gpu_layers is a *record of what ran*, not an
        # instruction: our own scan writes 0 for every model it finds, so trusting it here pinned the
        # machine to CPU and the adaptive plan never reached the argv - /api/health reported ngl 79
        # while the engine was launched with -ngl 0. An operator pin belongs in config/console.json,
        # where perf.resolve honours it, and it is applied before this line runs.
        planned = int(lp["ngl"])
        planned_first = planned
        queue = perf.ladder(planned)
        started = time.monotonic()
        dropped_backend = False
        # The performance flags are the newest thing in this launch, so they are the first suspect
        # when it fails: degrade to the shipped defaults once, then start blaming the backend.
        conservative = False
        flag_note = ""
        tuned_flags = bool(lp.get("kv_type") or lp.get("batch") or lp.get("ubatch") or lp.get("flash_attn"))
        while queue:
            ngl_try = queue.pop(0)
            try:
                spawn_runner(
                    port=port,
                    gguf=p,
                    kind=rec.get("kind") or "chat",
                    mmproj=mm,
                    ctx=ctx,
                    ngl=int(ngl_try),
                    alias=rec.get("id") or p.stem,
                    api_key=api_key,
                    threads=int(lp["threads"]),
                    mmap=True,
                    flash_attn=bool(lp.get("flash_attn")),
                    kv_type=str(lp.get("kv_type") or ""),
                    batch=int(lp.get("batch") or 0),
                    ubatch=int(lp.get("ubatch") or 0),
                    skip_ram_gate=True,
                    ctx_max=lim["ctx_max"],
                    architecture=rec.get("architecture"),
                )
            except (RuntimeError, TimeoutError, OSError) as exc:
                if not queue and tuned_flags and not conservative:
                    conservative = True
                    lp["flash_attn"] = False
                    lp["kv_type"] = ""
                    lp["batch"] = 0
                    lp["ubatch"] = 0
                    queue = [int(ngl_try)]
                    flag_note = (
                        "tuned engine flags failed (" + type(exc).__name__ + ") - running the shipped defaults"
                    )
                    state["perf_fallback"] = flag_note + "; retrying"
                    continue
                if not queue:
                    # A backend that fails even at ngl 0 is the suspect, not the machine:
                    # drop it, remember the verdict and give the shipped engine one try.
                    why = ""
                    if not dropped_backend:
                        why = _drop_backend(pl, f"{type(exc).__name__}: {exc}", tail=_boot_log_tail(port))
                    if why:
                        dropped_backend = True
                        planned = 0
                        queue = [0]
                        state["perf_fallback"] = why
                        continue
                    state["error"] = f"engine_launch_failed: {type(exc).__name__}: {exc}"
                    raise
                # A launch that burned its whole readiness deadline is a hung engine, not a
                # tight fit: go straight to the CPU instead of spending another deadline.
                if (time.monotonic() - started) > perf.SLOW_ATTEMPT_S:
                    queue = [0]
                state["perf_fallback"] = (
                    f"ngl {ngl_try} failed ({type(exc).__name__}); retrying ngl {queue[0]}"
                )
                continue
            notes = [flag_note] if flag_note else []
            if int(ngl_try) != planned:
                notes.append(f"ngl {planned} failed on this host; running ngl {ngl_try}")
            if notes:
                state["perf_fallback"] = "; ".join(notes)
            _exe = resolve_binary()
            perf_rec["effective"] = {
                "ngl": int(ngl_try),
                "threads": int(lp["threads"]),
                "mode": "cpu" if int(ngl_try) <= 0 else (
                    "gpu_full" if int(ngl_try) >= perf.FULL_LAYERS else "gpu_partial"),
                "planned_ngl": int(planned_first),
                "backend": str(perf_rec.get("backend") or "cpu"),
                "engine_dir": str(_exe.parent) if _exe else "",
                "engine": str(_exe.name) if _exe else "",
                "flash_attn": bool(lp.get("flash_attn")),
                "kv_type": str(lp.get("kv_type") or ""),
                "batch": int(lp.get("batch") or 0),
                "ubatch": int(lp.get("ubatch") or 0),
            }
            perf.remember_host(
                pl["perf"]["host"],
                ngl=int(ngl_try),
                threads=int(lp["threads"]),
                model=str(rec.get("id") or p.stem),
                mode="cpu" if int(ngl_try) <= 0 else "gpu",
                # `failed` is the ONLY field allowed to lower a later plan, so it is set from evidence:
                # the engine refused the planned offload and had to come down. Everything else recorded
                # here describes what ran and must never outrank a fresh plan - a record written while
                # the host merely ran on CPU carries no fault, and treating it as one would pin the
                # machine to CPU for good.
                failed=bool(int(ngl_try) < int(planned)),
                note="" if int(ngl_try) == planned else f"planned {planned}, ran {int(ngl_try)} after a failed launch",
            )
            break
    state["brain"] = "ready"
    state["engine"] = "lygo-llama"
    state["engine_port"] = port
    state["error"] = None
    state["selected"] = rec.get("id")
    return "ready"


def status() -> dict[str, Any]:
    from colibri import resolve_coli

    return {
        "ok": True,
        "name": "LYGO Engine",
        "hybrid": True,
        "probe": probe(),
        "llama": bool(resolve_binary()),
        "colibri": bool(resolve_coli()),
        "ports": {"llama": LLAMA_PORT, "colibri": COLIBRI_PORT, "portal": DEFAULT_PORT},
    }
