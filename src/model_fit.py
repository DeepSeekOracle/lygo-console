"""LYGO Model Fit — can *this* host actually run that model?

The scanner's job is to make models **visible**. This module's job is to say what this host would do
with each one. Those are different questions and the kit used to answer only the first: a GGUF that
parses was advertised as runnable, so a model far too large for the card and the RAM looked exactly
as available as one that fits.

Contract:
- **Every model seen gets a verdict, including the ones that cannot run.** "Available but too big"
  is a useful fact about this PC; hiding the model would make the console lie by omission.
- A verdict is arithmetic, never a load attempt: no engine is started, nothing is downloaded, and a
  verdict is never remembered as a host failure (that is `perf.json`'s job, on a real launch).
- Nothing here decides what the brain *runs*. It reports; `registry.pick` still chooses, and still
  refuses what does not fit in RAM.

Placement changes speed only. This module changes nothing about weights, precision or semantics.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import perf

MIB = 1024**2
GIB = 1024**3
RAM_RESERVE_FRACTION = 0.25  # the OS, the console and the browser keep their share
RAM_RESERVE_MIN_MIB = 2048
# A CPU-only load has to hold the weights *and* the cache in system RAM, and it still has to leave
# room for everything else running. Past this share of total RAM the honest answer is "not on this
# machine", not "slowly".
RAM_LOAD_SHARE = 0.75

VERDICT_TEXT = {
    "gpu_full": "runs fully on the GPU — the fast path",
    "gpu_partial": "runs partly on the GPU, the rest on CPU — usable, slower than full",
    "cpu_ok": "fits in RAM: runs on CPU alone — works, but slow",
    "cpu_tight": "only just fits in RAM — expect swapping and long waits",
    "too_big": "too large for this host's memory — visible, but it will not load here",
    "unknown": "size unknown — cannot be judged before a load is attempted",
}


def ram_total_mib() -> int:
    """Installed RAM in MiB, 0 when it cannot be read. Never raises."""
    if os.name == "nt":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return int(st.ullTotalPhys / MIB)
        except Exception:
            return 0
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / MIB)
    except (ValueError, OSError, AttributeError):
        return 0


def vram_free_mib() -> int:
    """Free VRAM on the best device this host can actually USE, 0 when there is none.

    Delegates to the engine's own device probe: a GPU is only claimed when the engine build ships a
    backend and names a device, so a host with a card but no working backend reads as 0 and is
    planned honestly for CPU. Never raises.
    """
    try:
        return int(perf.gpu_free_mib() or 0)
    except Exception:
        return 0


def ram_usable_mib(total_mib: int) -> int:
    """What a model may actually claim, after the OS and the running desktop take their share."""
    total = int(total_mib or 0)
    if total <= 0:
        return 0
    return max(0, total - max(RAM_RESERVE_MIN_MIB, int(total * RAM_RESERVE_FRACTION)))


def needs_mib(model_bytes: int, kv_mib: int, *, compute_fraction: float = perf.COMPUTE_FRACTION) -> int:
    """Weights + cache + the working buffers a load has to allocate alongside them."""
    weights = int(int(model_bytes or 0) / MIB)
    compute = max(perf.COMPUTE_MIN_MIB, int(weights * compute_fraction))
    return weights + int(kv_mib or 0) + compute


def rig_vram_mib() -> tuple[int, int]:
    """(total, free) VRAM on the card itself - the rig's truth for planning.

    One reader for the whole kit: `perf.gpu_vram_mib()` asks the driver. Asking our CPU-only base binary
    for a device list answered "(none)" here and filed ten models "no_gpu_device" on a box with an idle
    RTX 4060 Ti.
    """
    try:
        return perf.gpu_vram_mib()
    except Exception:  # noqa: BLE001
        return (0, 0)


def gpu_present() -> bool:
    """Is there a GPU on this host at all? A card that reports nothing FREE is busy, not absent."""
    return rig_vram_mib()[0] > 0


def verdict(
    *,
    model_bytes: int,
    ctx: int | None = None,
    dims: Any = None,
    vram_free_mib: int = 0,
    ram_total: int = 0,
    kv_type: str = "f16",
) -> dict[str, Any]:
    """The placement this host would get for one model, as arithmetic.

    Returns a dict with `verdict`, `ngl`, `mode`, `reason`, `need_mib`, `vram_free_mib` and
    `ram_usable_mib`. `verdict` is one of the VERDICT_TEXT keys; `reason` is perf's own string, so a
    reader can trace which rule fired.
    """
    nbytes = int(model_bytes or 0)
    if nbytes <= 0:
        return {
            "verdict": "unknown",
            "ngl": 0,
            "mode": "unknown",
            "reason": "model_size_unknown",
            "need_mib": 0,
            "vram_free_mib": int(vram_free_mib or 0),
            "ram_usable_mib": ram_usable_mib(ram_total),
        }
    per_token = perf.kv_bytes_per_token(dims)
    kv_mib = perf.kv_cache_mib(per_token, ctx, kv_type) if ctx else perf.KV_UNKNOWN_MIB
    need = needs_mib(nbytes, kv_mib)
    vram = int(vram_free_mib or 0)
    if vram <= 0:
        # A caller that passed 0 (or measured 0 while another process held the card) must not have that
        # transient recorded as "this host has no GPU": ask the DRIVER what the card has. Only if the
        # driver also reports nothing does "no_gpu_device" (or "a busy card") become the answer.
        vram = rig_vram_mib()[1]
    usable = ram_usable_mib(ram_total)

    if vram > 0:
        ngl, reason = perf.plan_ngl(nbytes, vram, kv_mib=kv_mib)
    else:
        # "no GPU device" and "the GPU is busy" are different answers with different next moves: the
        # first says this box will never run it fast, the second says try again when the card is free.
        # Measured 2026-09-20: ten models were filed "no_gpu_device" on a box whose RTX 4060 Ti was
        # held by a live engine at that moment (nvidia-smi read 0 MiB free of 8188).
        ngl, reason = 0, ("gpu_busy_at_check" if gpu_present() else "no_gpu_device")
    base = {
        "need_mib": need,
        "kv_mib": int(kv_mib),
        "vram_free_mib": vram,
        "ram_usable_mib": usable,
    }
    if ngl >= perf.FULL_LAYERS:
        return {**base, "verdict": "gpu_full", "ngl": ngl, "mode": "gpu_full", "reason": reason}
    if ngl > 0:
        return {**base, "verdict": "gpu_partial", "ngl": ngl, "mode": "gpu_partial", "reason": reason}
    # No layer on the GPU. The question becomes whether the host can hold it at all, and the honest
    # denominator is total RAM: a load that fits only by using every free byte is not a plan.
    if usable <= 0:
        return {**base, "verdict": "unknown", "ngl": 0, "mode": "cpu", "reason": "ram_unknown"}
    if need <= usable:
        tight = need > usable * RAM_LOAD_SHARE
        return {
            **base,
            "verdict": "cpu_tight" if tight else "cpu_ok",
            "ngl": 0,
            "mode": "cpu",
            "reason": reason,
        }
    return {**base, "verdict": "too_big", "ngl": 0, "mode": "none", "reason": reason}


def annotate(
    record: dict[str, Any],
    *,
    dims: Any = None,
    vram_free_mib: int = 0,
    ram_total: int = 0,
    kv_type: str = "f16",
) -> dict[str, Any]:
    """Add a `fit` block to a scanner record, in place, and return it.

    A projector is a sidecar, not a model: it carries no placement of its own and is never marked
    as unrunnable on its own account.
    """
    if not isinstance(record, dict):
        return record
    if record.get("kind") == "mmproj":
        record["fit"] = {
            "verdict": "sidecar",
            "ngl": 0,
            "mode": "n/a",
            "reason": "projector_belongs_to_a_model",
            "text": "projector for the model beside it",
        }
        return record
    fit = verdict(
        model_bytes=int(record.get("bytes") or 0),
        ctx=record.get("ctx"),
        dims=dims,
        vram_free_mib=vram_free_mib,
        ram_total=ram_total,
        kv_type=kv_type,
    )
    fit["text"] = VERDICT_TEXT.get(str(fit.get("verdict")), "")
    record["fit"] = fit
    # `runnable` keeps its old meaning (the file itself is loadable); the fit is what the picker and
    # the LLM tab read. A model too big to hold must never be chosen for the brain.
    if fit["verdict"] in ("too_big", "unknown"):
        record["runnable"] = False
        record["available_local"] = True
    else:
        record["available_local"] = True
    return record


def summary(records: Any) -> dict[str, Any]:
    """Counts by verdict, for a status read-out that has to stay honest about what it saw."""
    counts: dict[str, int] = {}
    total = 0
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        fit = rec.get("fit")
        if not isinstance(fit, dict):
            continue
        total += 1
        key = str(fit.get("verdict") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    runs = sum(counts.get(k, 0) for k in ("gpu_full", "gpu_partial", "cpu_ok"))
    return {
        "total": total,
        "by_verdict": counts,
        "runnable_here": runs,
        "visible_but_not_runnable": counts.get("too_big", 0) + counts.get("unknown", 0),
    }
