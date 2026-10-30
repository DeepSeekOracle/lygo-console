"""LYGO Perf — host-adaptive launch profile for the engine.

One stick, many hosts. This module decides where the weights actually run: as many
layers as the host GPU can hold, on every CPU core the host offers, or honestly on
CPU alone when the engine build has no GPU backend at all.

Contract:
- the kit config is the operator's word. An integer ngl/threads is a PIN and is
  honored exactly as written, 0 included. The token "auto" (or a missing key) hands
  the decision to this module.
- a GPU is only claimed when it is proven: the engine build must ship a backend DLL
  and the engine's own --list-devices must name a device. No proof, no claim.
- every outcome is remembered per host + model in data/perf.json, so the next boot on
  that host starts where it last succeeded instead of spending its time again.

Placement changes speed only. Weights, precision and semantics never change here.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import atomicio
from paths import DATA, ENGINE_DIR

GIB = 1024**3
MIB = 1024**2
VRAM_RESERVE_MIB = 1024  # the desktop compositor and driver keep their room
FULL_LAYERS = 99  # llama.cpp's idiom for "all layers"
MIN_PARTIAL_LAYERS = 6  # below this the transfer costs more than it buys
THREAD_MIN, THREAD_MAX = 4, 16
COMPUTE_MIN_MIB = 192  # graph and scratch buffers llama.cpp needs while offloaded
COMPUTE_FRACTION = 0.04  # ...plus a slice of the weights, which is how they scale in practice
KV_UNKNOWN_MIB = 256  # what a model with no readable header is charged for its KV cache
# KV cache bytes per element, relative to f16. q8_0 halves the cache and q4_0 quarters it: that is
# what buys a larger context on a small GPU.
KV_TYPE_FACTOR = {"f16": 1.0, "bf16": 1.0, "q8_0": 0.5, "q4_0": 0.25, "q5_0": 0.3125, "q5_1": 0.3125}
DEVICE_TTL_S = 300
SLOW_ATTEMPT_S = 150  # a launch that burns this long is hung, not merely tight
HOST_LIMIT = 12
SIGNATURE = "Δ9Φ963-LYGO-PERF-HOST-ADAPTIVE-v1"
PERF_JSON = DATA / "perf.json"
DEVICE_BACKENDS = ("vulkan", "cuda", "sycl", "hip", "opencl", "metal", "kompute")

REMEDY = {
    "engine_cpu_only": 'engine/ has no GPU backend installed — scripts/fetch_engine.ps1 -Backend cuda (NVIDIA) or -Backend vulkan (any GPU) adds one; it runs only after a self-test passes on this host',
    "no_gpu_device": "the engine build has a GPU backend but reports no device — update the GPU driver",
    "no_vram_headroom": "the GPU has no free VRAM to spare — close other GPU applications",
    "vram_too_small_for_partial_offload": "the GPU is too small for this model — choose a smaller model",
    "pinned_by_config": 'config/console.json pins ngl — set it to "auto" to let the host decide',
    "remembered_from_this_host": "this host failed with more layers before — delete data/perf.json to retry",
    "gpu_disabled_by_config": 'config/console.json sets gpu — set it back to "auto" to let the host decide',
    "backend_crashed_on_this_host": "a GPU backend crashed this engine on this host — the stick stays on CPU here; the verdict is remembered, and a driver update or a fresh fetch clears it",
    "backend_failed_on_this_host": "this host already failed that GPU backend — delete data/perf.json to retry it",
    "backend_sees_no_device": "the backend loaded but named no device — check the GPU driver",
    "backend_activation_failed": "the backend could not be applied to engine/ — check free space and permissions",
    "backend_self_test_incomplete": "the backend did not finish a probe model load in time — it was left off",
    "backend_layer_error": "the backend layer itself failed — the kit ran the shipped CPU engine",
    "backend_not_proven": "no installed GPU backend has passed a self-test on this host yet",
}

_DEV_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def engine_path(where: Any = None) -> Path:
    """The engine directory in play: an explicit one, else whatever is active for this host."""
    if where:
        return Path(where)
    try:
        import paths

        return Path(paths.engine_dir())
    except Exception:
        return Path(ENGINE_DIR)


def engine_backends(where: Any = None) -> list[str]:
    """GPU backends this engine build actually ships — a file scan, not a hope."""
    d = engine_path(where)
    try:
        if not d.is_dir():
            return []
        names = [f.name.lower() for f in d.iterdir() if f.is_file()]
    except OSError:
        return []
    return sorted(b for b in DEVICE_BACKENDS if any(n.startswith(f"ggml-{b}") for n in names))


_DEVICE_RE = re.compile(
    r"^\s*(?P<id>[A-Za-z0-9_]+):\s*(?P<name>.+?)\s*\((?P<total>\d+)\s*MiB"
    r"(?:,\s*(?P<free>\d+)\s*MiB free)?\)\s*$"
)


def parse_devices(text: str) -> list[dict[str, Any]]:
    """Parse `llama-server --list-devices`: 'Vulkan0: NVIDIA GeForce RTX 4060 Ti (7949 MiB, 7181 MiB free)'."""
    out: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        m = _DEVICE_RE.match(line)
        if not m:
            continue
        total = int(m.group("total"))
        out.append(
            {
                "id": m.group("id"),
                "name": m.group("name").strip(),
                "total_mib": total,
                "free_mib": int(m.group("free")) if m.group("free") else total,
            }
        )
    return out


def engine_devices(exe: Any = None, *, timeout: int = 25, refresh: bool = False) -> list[dict[str, Any]]:
    """Ask the engine itself which devices it sees. Never raises: [] means none we can use.

    refresh=True is required right after applying a backend DLL: the cache key is the exe
    path and mtime, and neither changes when a DLL lands next to it, so the pre-activation
    "no devices" answer would otherwise outlive the activation and reject a good backend.
    """
    path = Path(exe) if exe else engine_path() / "llama-server.exe"
    try:
        if not path.is_file():
            return []
        key = f"{path}|{path.stat().st_mtime_ns}"
    except OSError:
        return []
    if refresh:
        _DEV_CACHE.pop(key, None)
    hit = _DEV_CACHE.get(key)
    if hit and (time.time() - hit[0]) < DEVICE_TTL_S:
        return hit[1]
    devs: list[dict[str, Any]] = []
    try:
        r = subprocess.run(
            [str(path), "--list-devices"],
            cwd=str(path.parent),
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        devs = parse_devices((r.stdout or "") + "\n" + (r.stderr or ""))
    except (OSError, subprocess.SubprocessError):
        devs = []
    _DEV_CACHE[key] = (time.time(), devs)
    return devs


def best_device(devs: Any) -> dict[str, Any] | None:
    """The device with the most free VRAM — where the layers would go."""
    items = [d for d in (devs or []) if isinstance(d, dict)]
    if not items:
        return None
    return max(items, key=lambda d: int(d.get("free_mib") or 0))


_VRAM_MEMO: dict[str, Any] = {}


def gpu_vram_mib(*, refresh: bool = False) -> tuple[int, int]:
    """(total, free) VRAM on the card ITSELF, from the driver - the rig's truth for planning.

    Deliberately NOT the engine's device list: the shipped base engine is CPU-only on purpose, so asking
    IT answers "(none)" on a box with a working card. Measured 2026-09-20: that reading filed ten models
    "no_gpu_device" on a machine with an idle RTX 4060 Ti, and a backend self-test inherited the same
    answer. "Can OUR engine use the GPU here" is a different question owned by `backends`, which awards it
    only after a real model load proves it on this host.
    """
    if not refresh and _VRAM_MEMO:
        at, pair = _VRAM_MEMO.get("v") or (0.0, (0, 0))
        if (time.time() - at) < 2.0:
            return pair
    best = (0, 0)
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        _VRAM_MEMO["v"] = (time.time(), best)
        return best
    for line in (r.stdout or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        try:
            tot, free = int(parts[0]), int(parts[1])
        except (IndexError, ValueError):
            continue
        if tot > best[0]:
            best = (tot, free)
    _VRAM_MEMO["v"] = (time.time(), best)
    return best


def gpu_free_mib() -> int:
    """Free VRAM on the card, from the driver. 0 when there is none. Never raises, never over-claims."""
    try:
        return int(gpu_vram_mib()[1])
    except Exception:  # noqa: BLE001
        return 0


def physical_cores() -> tuple[int, int]:
    """(fast_cores, all_physical_cores) for this host; (0, 0) when it cannot be known.

    On a hybrid part (Alder/Raptor Lake and later) the E-cores are slower per clock and packing
    threads onto them costs more than it buys: llama.cpp measured *faster* on the 6 P-cores than on
    16 of the 20 logical processors of the same chip. Windows exposes the split as an
    EfficiencyClass per physical core, the P-cores carrying the higher class.

    Off Windows, or on a uniform part, both numbers are the same physical count.
    """
    if os.name != "nt":
        return 0, 0
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        needed = ctypes.c_uint(0)
        # A FALSE return is how this API reports the buffer size it wants, not a failure.
        kernel32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(needed))
        if not needed.value:
            return 0, 0
        buf = ctypes.create_string_buffer(needed.value + 4096)
        size = ctypes.c_uint(needed.value + 4096)
        if not kernel32.GetLogicalProcessorInformationEx(0, buf, ctypes.byref(size)):
            return 0, 0
        raw = buf.raw
        classes: list[int] = []
        off = 0
        while off + 32 <= size.value:
            step = int.from_bytes(raw[off + 4 : off + 8], "little")
            if step == 0:
                break
            # PROCESSOR_RELATIONSHIP: Flags @8 (byte), EfficiencyClass @9 (byte).
            classes.append(int(raw[off + 9]))
            off += step
    except Exception:
        return 0, 0
    if not classes:
        return 0, 0
    return classes.count(max(classes)), len(classes)


def auto_threads() -> int:
    """Threads for llama-server: the fast physical cores, capped for sanity.

    Sized to the P-cores on a hybrid host and to the physical cores otherwise, never to the logical
    processor count: hyperthreads on an inference loop contend for the same execution ports.
    """
    fast, total = physical_cores()
    cores = fast or total
    if cores:
        return max(THREAD_MIN, min(THREAD_MAX, cores))
    n = os.cpu_count() or 4
    return max(THREAD_MIN, min(THREAD_MAX, n - 1 if n > THREAD_MIN else n))


def clamp_threads(threads: Any) -> int:
    """CPU threads for llama-server: a pin is honored, else the host's own cores."""
    if threads is None:
        return auto_threads()
    try:
        nth = int(threads)
    except (TypeError, ValueError):
        return auto_threads()
    return max(2, min(THREAD_MAX, nth or auto_threads()))


def sanitize_kv_type(value: object) -> str:
    """A KV cache type this kit will plan around, else 'f16' (the engine's own default)."""
    t = str(value or "").strip().lower()
    return t if t in KV_TYPE_FACTOR else "f16"


def kv_bytes_per_token(dims: object) -> int | None:
    """KV cache bytes for ONE token, read from the model's own GGUF header.

    n_layer * n_kv_head * (key_len + value_len) * 2 bytes. Grouped-query models have far fewer KV
    heads than attention heads, so this has to come from the header: a guess would mis-plan every
    host it was not guessed on. None means the header did not say, and the caller charges a flat
    allowance instead of pretending the cache is free.
    """
    if not isinstance(dims, dict) or not dims:
        return None

    def pick(*suffixes: str) -> int | None:
        # Suffix order is precedence, NOT dict order: "attention.head_count_kv" has to win over
        # "attention.head_count". A header that lists head_count first would otherwise be read as
        # a 7x larger cache (401 KiB/token instead of 56), and every GPU would plan as too small.
        for s in suffixes:
            for key, val in dims.items():
                if str(key).lower().endswith(s):
                    try:
                        return int(val)
                    except (TypeError, ValueError):
                        return None
        return None

    layers = pick(".block_count", ".n_layer")
    kv_heads = pick(".attention.head_count_kv", ".attention.head_count")
    if not layers or not kv_heads:
        return None
    key_len = pick(".attention.key_length") or 128
    val_len = pick(".attention.value_length") or key_len
    return int(layers) * int(kv_heads) * (int(key_len) + int(val_len)) * 2


def kv_cache_mib(per_token: int | None, ctx: int, kv_type: str = "f16") -> int:
    """KV cache size in MiB for a context. An unknown model gets the flat allowance."""
    if not per_token or not ctx:
        return KV_UNKNOWN_MIB
    factor = KV_TYPE_FACTOR.get(sanitize_kv_type(kv_type), 1.0)
    return max(1, int(int(per_token) * int(ctx) * factor / MIB))


def plan_ngl(
    model_bytes: int,
    free_mib: int,
    *,
    reserve_mib: int = VRAM_RESERVE_MIB,
    kv_mib: int = 0,
) -> tuple[int, str]:
    """How many of 99 layers fit in the free VRAM. Returns (ngl, reason).

    The KV cache is charged explicitly when the caller knows it (kv_mib, from the
    model's own header) and with a flat allowance when it does not. A plan that ignores
    the cache is how a confident "fits_vram" becomes an engine that dies allocating its
    context."""
    if free_mib <= 0:
        return 0, "no_gpu_device"
    avail = int(free_mib) - int(reserve_mib)
    if avail <= 0:
        return 0, "no_vram_headroom"
    if model_bytes <= 0:
        return FULL_LAYERS, "model_size_unknown_full_offload"
    weights = int(model_bytes / MIB)
    kv = int(kv_mib) if kv_mib else KV_UNKNOWN_MIB
    compute = max(COMPUTE_MIN_MIB, int(weights * COMPUTE_FRACTION))
    need = weights + kv + compute
    if need <= avail:
        return FULL_LAYERS, "fits_vram"
    ngl = min(FULL_LAYERS - 1, int(FULL_LAYERS * avail / need))
    if ngl < MIN_PARTIAL_LAYERS:
        return 0, "vram_too_small_for_partial_offload"
    return ngl, f"partial_offload_{ngl}_of_{FULL_LAYERS}"


def _legacy_ngl(hw: dict[str, Any]) -> int:
    """Pre-adaptive rule, kept for callers that cannot see a device list."""
    vram = int(hw.get("vram_bytes") or 0)
    if vram >= 4 * GIB:
        return FULL_LAYERS
    if vram >= 2 * GIB:
        return 20
    return 0


def host_id() -> str:
    """This PC, for backend verdicts: no model, no live memory — the machine itself."""
    try:
        return f"{platform.node()}|{os.cpu_count() or 0}"
    except Exception:
        return "unknown-host"


def fingerprint(hw: dict[str, Any], device: dict[str, Any] | None, model_id: str) -> str:
    """Host identity: a stick carried to another PC must not inherit this PC's verdict.

    RAM is rounded to whole GiB because 'available' memory moves every second. The device's reported
    *total* VRAM is deliberately NOT part of the key: it moves with the driver read for the same card
    (seen here as 7949 then 8187 MiB), and a key that moves orphans every earlier record - the memory
    then silently never applies, which is indistinguishable from having no memory at all.
    """
    # Key on installed RAM, never available RAM: available memory moves by gigabytes as browsers and
    # models come and go, and a key that moves renames the host - so every earlier verdict is orphaned
    # and the memory silently stops applying. Callers that know the total pass ram_total_bytes; the
    # rest fall back to ram_bytes.
    ram_gib = round(int(hw.get("ram_total_bytes") or hw.get("ram_bytes") or 0) / GIB)
    parts = [
        platform.node(),
        str(os.cpu_count() or 0),
        str(ram_gib),
        str((device or {}).get("name") or ""),
        str(model_id or ""),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _load_store() -> dict[str, Any]:
    try:
        data = json.loads(atomicio.read_text(PERF_JSON, errors="replace"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def host_record(fp: str) -> dict[str, Any] | None:
    rec = (_load_store().get("hosts") or {}).get(fp)
    return rec if isinstance(rec, dict) else None


def remember_host(fp: str, **fields: Any) -> dict[str, Any]:
    """Merge one host's launch outcome. Atomic write, bounded history, never raises."""
    store = _load_store()
    hosts = store.get("hosts")
    if not isinstance(hosts, dict):
        hosts = {}
    known = {k: v for k, v in (hosts.get(fp) or {}).items() if isinstance(v, (str, int, float, bool))}
    entry = {**known, **fields, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    hosts[fp] = entry
    if len(hosts) > HOST_LIMIT:
        for k, _ in sorted(hosts.items(), key=lambda kv: str(kv[1].get("at") or ""))[: len(hosts) - HOST_LIMIT]:
            hosts.pop(k, None)
    store["hosts"] = hosts
    store["signature"] = SIGNATURE
    try:
        PERF_JSON.parent.mkdir(parents=True, exist_ok=True)
        atomicio.atomic_write_text(PERF_JSON, json.dumps(store, indent=2, sort_keys=True) + "\n")
    except OSError:
        pass
    return entry


def _pin_int(value: Any) -> int | None:
    """An integer pin, or None. "auto", "", junk and a typo all mean "let the host decide"."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def resolve(
    *,
    lim: dict[str, Any],
    hw: dict[str, Any],
    model_bytes: int,
    model_id: str = "",
    kv_mib: int = 0,
) -> dict[str, Any]:
    """Probe facts + config pins + this host's memory -> the launch profile."""
    devs = hw.get("devices")
    known = devs is not None
    devices = [d for d in (devs or []) if isinstance(d, dict)]
    backends = [str(b) for b in (hw.get("backends") or [])] if known else []
    device = best_device(devices)
    vram_free_mib = int(device.get("free_mib") or 0) if device else int((hw.get("vram_bytes") or 0) / MIB)
    vram_total_mib = int(device.get("total_mib") or 0) if device else 0
    fp = fingerprint(hw, device, model_id)
    record = host_record(fp)

    pin_ngl = _pin_int(lim.get("ngl"))
    pin_threads = _pin_int(lim.get("threads"))
    fallback = False
    if pin_ngl is not None:
        ngl, reason, source = int(pin_ngl), "pinned_by_config", "pin"
    elif not known:
        ngl, reason, source = _legacy_ngl(hw), "vram_only_thresholds", "auto"
    elif not backends:
        ngl, reason, source = 0, str(hw.get("gpu_reason") or "engine_cpu_only"), "auto"
    elif device is None:
        ngl, reason, source = 0, "no_gpu_device", "auto"
    else:
        ngl, reason = plan_ngl(model_bytes, vram_free_mib, kv_mib=kv_mib)
        source = "auto"
    # A GPU is only planned when backends.py has proven one *on this host*: devices seen
    # by a candidate build that failed its self-test must not become layers.
    if int(ngl) > 0 and known and hw.get("gpu_ok") is False:
        ngl, source, reason = 0, "auto", str(hw.get("gpu_reason") or "backend_not_proven")
    # Only a PROVEN failure may lower the plan. A record written while the host simply ran on CPU
    # carries no evidence of a fault, and treating it as one pins the machine to CPU permanently: the
    # first CPU boot would outrank every later plan for that host+model, so a GPU gets planned,
    # measured, and then never used. Evidence - not the absence of a success - is what caps.
    if source == "auto" and isinstance(record, dict) and record.get("failed") is True:
        remembered = _pin_int(record.get("ngl"))
        if remembered is not None and remembered < ngl:
            ngl, source, fallback = remembered, "host_record", True
            reason = f"remembered_failure_on_this_host ({record.get('note') or 'a lower offload was all that ran'})"

    threads = int(pin_threads) if pin_threads is not None else auto_threads()
    mode = "cpu" if int(ngl) <= 0 else ("gpu_full" if int(ngl) >= FULL_LAYERS else "gpu_partial")
    return {
        "ngl": int(ngl),
        "threads": int(threads),
        "mode": mode,
        "source": source,
        "reason": reason,
        "threads_source": "pin" if pin_threads is not None else "auto",
        "device": (device or {}).get("name") or "",
        "device_id": (device or {}).get("id") or "",
        "vram_free_mib": vram_free_mib,
        "vram_total_mib": vram_total_mib,
        "backends": backends,
        "reserve_mib": VRAM_RESERVE_MIB,
        "kv_mib": int(kv_mib),
        "host": fp,
        "backend": str(hw.get("backend") or "cpu"),
        "engine_dir": str(hw.get("engine_dir") or ""),
        "gpu_ok": bool(hw.get("gpu_ok")),
        "fallback": fallback,
        "known_devices": known,
        "signature": SIGNATURE,
    }


def ladder(ngl: Any) -> list[int]:
    """Launch attempts, best first, CPU last: a GPU that cannot load must not kill the brain."""
    first = max(0, int(ngl or 0))
    out = [first]
    for cand in (first // 2, MIN_PARTIAL_LAYERS + 2, 0):
        if 0 <= cand < out[-1]:
            out.append(cand)
    return out


def report(state: dict[str, Any] | None = None) -> dict[str, Any]:
    """The /api/health view of the launch profile. Never raises: health must always answer."""
    try:
        st = state if isinstance(state, dict) else {}
        pl = st.get("lygo_engine") if isinstance(st.get("lygo_engine"), dict) else {}
        prof = pl.get("perf") if isinstance(pl.get("perf"), dict) else {}
        hw = pl.get("hardware") if isinstance(pl.get("hardware"), dict) else {}
        llama = pl.get("llama") if isinstance(pl.get("llama"), dict) else {}
        if not prof:
            prof = {
                "ngl": llama.get("ngl"),
                "threads": llama.get("threads") if llama.get("threads") is not None else auto_threads(),
                "mode": "unknown",
                "source": "unplanned",
                "reason": "engine_not_planned_yet",
                "device": "",
                "backends": engine_backends(),
                "host": "",
                "fallback": False,
                "vram_free_mib": 0,
                "vram_total_mib": 0,
            }
        mode = str(prof.get("mode") or "unknown")
        out: dict[str, Any] = {
            "mode": mode,
            "ngl": prof.get("ngl"),
            "threads": prof.get("threads"),
            "device": prof.get("device") or "",
            "vram_free_mib": prof.get("vram_free_mib") or 0,
            "vram_total_mib": prof.get("vram_total_mib") or 0,
            "ram_gib": hw.get("ram_gib") or 0,
            "cpu_threads": auto_threads(),
            "backends": prof.get("backends") or [],
            "source": prof.get("source"),
            "reason": prof.get("reason"),
            "fallback": bool(prof.get("fallback")),
            "host": prof.get("host") or "",
            "signature": SIGNATURE,
        }
        remedy = REMEDY.get(str(prof.get("reason") or ""))
        if remedy:
            out["remedy"] = remedy
        if st.get("perf_fallback"):
            out["launch_fallback"] = st["perf_fallback"]
        # Effective beats planned: after a fallback, health must report what is actually running.
        eff = prof.get("effective") if isinstance(prof.get("effective"), dict) else {}
        out["planned"] = {"mode": mode, "ngl": prof.get("ngl"), "threads": prof.get("threads")}
        out["kv_mib"] = prof.get("kv_mib") or 0
        out["backend"] = prof.get("backend") or ""
        out["engine_dir"] = prof.get("engine_dir") or ""
        if eff:
            out["effective"] = eff
            if eff.get("ngl") is not None:
                n = int(eff["ngl"] or 0)
                out["ngl"] = n
                mode = "cpu" if n <= 0 else ("gpu_full" if n >= FULL_LAYERS else "gpu_partial")
                out["mode"] = mode
            if eff.get("threads") is not None:
                out["threads"] = eff["threads"]
            for key in ("backend", "engine_dir"):
                if eff.get(key):
                    out[key] = eff[key]
        try:
            import backends as _backends

            out["backend_layer"] = _backends.report()
        except Exception:
            pass
        out["api_boost"] = (
            "CPU-only local inference is the slow path — an API brain (/api/brain) answers faster"
            if mode == "cpu"
            else ""
        )
        return out
    except Exception as exc:  # a broken perf record must not take health down with it
        return {"mode": "unknown", "error": f"{type(exc).__name__}: {exc}"}
