"""What this box can use RIGHT NOW - not what it said at boot.

A boot-time reading is a guess by the time a picture is asked for. Measured on this host 2026-09-21:
the console had gemma4-12b resident (10.9 GB, 79 layers offloaded) on an 8188 MiB card, and the picture
engine died asking for **644.05 MB device / 0.00 MB available** after loading a 6.9 GB checkpoint for
19.8 s. The number that decided that render was the free VRAM in the second it was asked for.

So this module measures, on demand and cheaply:
  * the card(s): total/used/free VRAM, utilisation, temperature, power draw  (nvidia-smi, one process)
  * who is holding it: pid + MiB per process  (nvidia-smi --query-compute-apps, then tasklist for names)
  * system RAM: exact, through GlobalMemoryStatusEx (no dependency, no parsing)
  * CPU: busy percentage and core count (psutil if present, else the Windows perf counter)

and answers the only question the limbs actually ask: *can this render have the card, or does it go to
the CPU?* (`picture_route`). Every reading is cached for `TTL_S` so a page polling a panel does not spawn
nvidia-smi per view, `force=True` is the way to say "measure again", and nothing here ever raises: an
unreadable counter is a blank with a reason, never an exception in the middle of a turn.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

TTL_S = 2.0
# MEASURED: stable-diffusion.cpp's own words when it died - "need 644.05 MB device / 132.05 MB budget,
# available 0.00 MB device". That is the number a route decision has to clear.
RENDER_NEED_MIB = 644
# Headroom kept on the card: a render that exactly fits its own requirement still has to survive the
# allocations around it, and a driver-level shortfall kills the process rather than the request.
RESERVE_MIB = 256
# MEASURED 2026-09-23, SDXL-Turbo at 1024x1024: the checkpoints weigh ~6.8 GB, so 644 MiB is a floor, not a
# budget. With 7113 MiB free the CUDA build drew a real picture in 12.5 s; with the chat model resident the
# same call has ~0 MiB free and dies at 19.8 s. A route decision therefore has to clear the CHECKPOINT plus
# this much room for the buffers around it - and picture_route() adds RESERVE_MIB on top, so the room a card
# must show is RENDER_HEADROOM_MIB + RESERVE_MIB (448 MiB here, against the 497 MiB the working render had).
RENDER_HEADROOM_MIB = 192

_GPU_Q = "name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw"
_APPS_Q = "pid,used_memory"

_CACHE: dict[str, Any] = {"gpu_ts": 0.0, "gpus": None, "apps_ts": 0.0, "apps": None,
                          "snap_ts": 0.0, "snap": None}

# pid -> (name, when). Names do not change; pids get reused. MEASURED 2026-09-21: resolving one name
# through tasklist costs SECONDS, and /api/health pays it per holder per poll - 4.766 s for the whole
# payload, against ~20 ms for every other probe in it. The Win32 call below is microseconds.
_NAMES: dict[int, tuple[str, float]] = {}
NAME_TTL_S = 300.0


def _nvidia_smi() -> str | None:
    """The binary, or None. Windows keeps it in System32 as well as the legacy NVSMI folder."""
    found = shutil.which("nvidia-smi")
    if found:
        return found
    for cand in (r"C:\Windows\System32\nvidia-smi.exe",
                 r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
        if Path(cand).is_file():
            return cand
    return None


def _run(argv: list[str], timeout: int = 8) -> tuple[int, str, str]:
    """One short process. Never raises - a missing tool is an answer, not a crash."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return 1, "", "timed out after %ss" % timeout
    except OSError as exc:
        return 1, "", str(exc)


def _mib(text: Any) -> int:
    """'7400 MiB' / '7400' / ' 7400 ' -> 7400. Unreadable -> 0, which reads as 'none'."""
    digits = "".join(ch for ch in str(text) if ch.isdigit())
    return int(digits) if digits else 0


def _fnum(text: Any) -> float | None:
    keep = "".join(ch for ch in str(text) if ch.isdigit() or ch == ".")
    try:
        return float(keep)
    except ValueError:
        return None


def _proc_name(pid: int) -> str:
    """A pid's image name. Win32 first (microseconds), tasklist only as a last resort (seconds).

    Measured on this host 2026-09-21: `tasklist /FI "PID eq N"` costs ~1-2 s per call, and the health
    route asks for one name per VRAM holder on every poll - that alone took /api/health from ~20 ms to
    4.766 s. QueryFullProcessImageNameW answers the same question without starting a process.
    """
    hit = _NAMES.get(pid)
    if hit and time.time() - hit[1] < NAME_TTL_S:
        return hit[0]
    name = ""
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            try:
                size = wintypes.DWORD(260)
                buf = ctypes.create_unicode_buffer(260)
                if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    name = Path(buf.value).name
            finally:
                k32.CloseHandle(handle)
    except Exception:
        name = ""
    if not name:                                                   # never a wrong answer, only a slower one
        code, out, _ = _run(["tasklist", "/FI", "PID eq %d" % pid, "/NH", "/FO", "CSV"], timeout=6)
        if code == 0 and out.strip():
            row = next(csv.reader(io.StringIO(out.strip().splitlines()[0])))
            name = (row[0] if row else "").strip()
    name = name or "pid %d" % pid
    _NAMES[pid] = (name, time.time())
    return name


def gpus(force: bool = False) -> list[dict[str, Any]]:
    """Every card, as measured now. Empty list = nothing measurable (and that is a valid answer)."""
    now = time.time()
    if not force and _CACHE["gpus"] is not None and now - _CACHE["gpu_ts"] < TTL_S:
        return _CACHE["gpus"]
    exe = _nvidia_smi()
    cards: list[dict[str, Any]] = []
    if exe:
        code, out, _ = _run([exe, "--query-gpu=" + _GPU_Q, "--format=csv,noheader,nounits"])
        if code == 0:
            for line in out.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 5 or not parts[0]:
                    continue
                total, used, free = _mib(parts[1]), _mib(parts[2]), _mib(parts[3])
                cards.append({
                    "name": parts[0], "total_mib": total, "used_mib": used, "free_mib": free,
                    "util_pct": int(_fnum(parts[4]) or 0),
                    "temp_c": int(_fnum(parts[5]) or 0) if len(parts) > 5 else None,
                    "power_w": _fnum(parts[6]) if len(parts) > 6 else None,
                    "used_pct": round(100.0 * used / total, 1) if total else None,
                })
    _CACHE.update({"gpus": cards, "gpu_ts": now})
    return cards


def compute_apps(force: bool = False) -> list[dict[str, Any]]:
    """The processes holding VRAM right now: pid, name, used MiB."""
    now = time.time()
    if not force and _CACHE["apps"] is not None and now - _CACHE["apps_ts"] < TTL_S:
        return _CACHE["apps"]
    exe = _nvidia_smi()
    apps: list[dict[str, Any]] = []
    if exe:
        code, out, _ = _run([exe, "--query-compute-apps=" + _APPS_Q, "--format=csv,noheader,nounits"])
        if code == 0:
            for line in out.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 2 or not parts[0].isdigit():
                    continue
                pid = int(parts[0])
                apps.append({"pid": pid, "name": _proc_name(pid), "used_mib": _mib(parts[1])})
    _CACHE.update({"apps": apps, "apps_ts": now})
    return apps


def ram(force: bool = False) -> dict[str, Any]:
    """Physical memory, exactly, through the kernel (no parsing, no dependency)."""
    try:
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = _MemStatus()
        st.dwLength = ctypes.sizeof(_MemStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            raise OSError("GlobalMemoryStatusEx refused")
        mib = 1024 * 1024
        total = int(st.ullTotalPhys // mib)
        avail = int(st.ullAvailPhys // mib)
        return {"total_mib": total, "available_mib": avail, "used_mib": total - avail,
                "used_pct": round(100.0 * (total - avail) / total, 1) if total else None,
                "source": "GlobalMemoryStatusEx"}
    except Exception as exc:                                     # pragma: no cover - platform guard
        return {"total_mib": 0, "available_mib": 0, "used_mib": 0, "used_pct": None,
                "source": "unreadable", "why": str(exc)[:200]}


def cpu(force: bool = False) -> dict[str, Any]:
    """Busy percentage and core count. `pct_busy` is None when nothing could read it.

    None rather than 0.0 on purpose: 0.0 reads as "idle" and would be a lie about a box under load.
    """
    cores = os.cpu_count() or 1
    try:
        import psutil                                            # optional, used when present

        return {"pct_busy": float(psutil.cpu_percent(interval=None)), "cores": cores,
                "source": "psutil"}
    except Exception:
        pass
    code, out, _ = _run(["powershell", "-NoProfile", "-Command",
                         "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage "
                         "-Average).Average"], timeout=12)
    pct = _fnum(out.strip().splitlines()[0]) if (code == 0 and out.strip()) else None
    if pct is None:
        return {"pct_busy": None, "cores": cores, "source": "unreadable"}
    return {"pct_busy": round(pct, 1), "cores": cores, "source": "Win32_Processor"}


def reset_cache() -> None:
    """Forget every cached reading. `force=True` on one call does this for that call; this is for a
    caller that wants the next reading fresh (a test, or a console that just changed what is resident)."""
    _CACHE.update({"gpu_ts": 0.0, "gpus": None, "apps_ts": 0.0, "apps": None,
                   "snap_ts": 0.0, "snap": None})


def free_vram_mib() -> int:
    """Free VRAM in the least-loaded card. 0 means 'none measurable', not 'nothing else is running'."""
    cards = gpus()
    return max((c["free_mib"] for c in cards), default=0)


def holder_note() -> str:
    """Who is holding the card, in words. Empty when nobody measurable is.

    MEASURED on this host 2026-09-21: nvidia-smi reports every process with a graphics context, and the
    desktop has ~23 of them at **0 MiB**. Picking the largest of those produced "dwm.exe (pid 1504) holds
    0 MiB of the card" - a sentence that reads as a fault while naming an innocent process. Only a holder
    with memory is a holder; when none has any, that is what the words say.
    """
    apps = [a for a in compute_apps() if a.get("used_mib", 0) > 0]
    if not apps:
        return ""
    top = max(apps, key=lambda a: a["used_mib"])
    return "%s (pid %d) holds %d MiB of the card" % (top["name"], top["pid"], top["used_mib"])


def snapshot(force: bool = False) -> dict[str, Any]:
    """Everything above, in one reading, cached for TTL_S."""
    now = time.time()
    if not force and _CACHE["snap"] is not None and now - _CACHE["snap_ts"] < TTL_S:
        return _CACHE["snap"]
    cards = gpus(force=force)
    apps = compute_apps(force=force)
    snap: dict[str, Any] = {
        "ts": now, "available": bool(cards),
        "why": "" if cards else ("nvidia-smi not found" if not _nvidia_smi() else "nvidia-smi read nothing"),
        "gpus": cards, "holders": apps, "ram": ram(), "cpu": cpu(),
    }
    _CACHE.update({"snap": snap, "snap_ts": now})
    return snap


def render_need_mib(checkpoint_bytes: int = 0) -> int:
    """Free VRAM a render really needs: the whole checkpoint plus room, never just the shortfall.

    `RENDER_NEED_MIB` is what the engine said it was STILL SHORT OF when it died - a floor, not a budget.
    A checkpoint we can measure is the honest answer; an unmeasurable one leaves that floor alone.
    """
    have = int(checkpoint_bytes or 0) // (1024 * 1024)
    return max(RENDER_NEED_MIB, have + RENDER_HEADROOM_MIB) if have else RENDER_NEED_MIB


def picture_route(need_mib: int = RENDER_NEED_MIB,
                  reserve_mib: int = RESERVE_MIB) -> tuple[str, str]:
    """('cuda'|'cpu', why) for a render needing `need_mib`.

    This is the decision the picture limb asks for BEFORE it spawns an engine: a doomed 20-second GPU
    attempt that ends in `available 0.00 MB device` is worse than going straight to the CPU, and it is
    also what the operator sees as "the picture tool is broken".
    """
    free = free_vram_mib()
    if free >= need_mib + reserve_mib:
        return "cuda", "the card has %d MiB free and this render needs %d MiB" % (free, need_mib)
    who = holder_note()
    why = "the card has %d MiB free but this render needs %d MiB" % (free, need_mib)
    if who:
        why += "; " + who
    elif not free:
        why += "; no card is measurable on this box"
    return "cpu", why
