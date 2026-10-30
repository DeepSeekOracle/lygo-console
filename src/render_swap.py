"""Let a render borrow the machine from the chat model - then hand it straight back.

THE CONSTRAINT THAT SHAPES THIS FILE: the console runs a limb BEFORE the model writes its
reply. So the brain has to be awake again before a limb returns. A render that borrowed the
machine and did not hand it back would kill the very turn that asked for the picture. Hence
park -> run -> wake, all synchronous, with the wake in a `finally`.

TWO DIFFERENT SHORTAGES, ONE FIX:
- a PICTURE is short of the CARD. The chat model holds ~6.3 GB of an 8 GB card, so
  `picture_route()` correctly sends every render to the CPU - minutes for a picture the CUDA
  build does in ~13 s. Almost no RAM is involved.
- a VIDEO is short of RAM. It needs ~13.5 GB of weights and this box shares 32 GB with the
  OS; loading the 6.7 GB text encoder with ~3 GB free fails inside ComfyUI as
  `HostBuffer.read_file_slice failed`. The card is not the binding constraint there.
Parking the chat model frees BOTH (~6.6 GB RAM and ~6.3 GB VRAM), so one mechanism serves
both, and the caller says which shortage it is measuring via `ram_floor_mib`.

WHY THIS TALKS HTTP INSTEAD OF IMPORTING `server`:
the console is launched as `python src/server.py serve`, so its live state lives in the module
named `__main__`. `import server` inside a limb builds a SECOND, empty copy of the module -
its STATE reads `brain: "missing"`, and a check against it silently refuses to park. The
running server is reachable over its own port, so this module parks through the shared
`engine` singleton and wakes through the console's `/api/select` route. Never import `server`
from here.

Every path is defensive on purpose: a probe that fails must never park the brain, and a park
that cannot make room must put the brain back immediately.
"""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from contextlib import contextmanager
from typing import Any

from hardware import picture_route, ram, render_need_mib
from paths import LLAMA_PORT

try:
    from paths import DEFAULT_PORT as CONSOLE_PORT
except Exception:                                    # pragma: no cover - older paths.py
    CONSOLE_PORT = 9641

try:
    from paths import WORKSPACE as _WORKSPACE
except Exception:                                    # pragma: no cover
    _WORKSPACE = None

DRAIN_TRIES = 24          # 12 s for the card/memory to come back: the engine's VRAM is released
                          # within ~10 s of the stop, so a longer drain only lengthens a park
                          # that is going to fail anyway.
WAKE_TRIES = 420          # 3.5 min for the brain to come back up
WAKE_ATTEMPTS = 4          # /api/select retries before a wake is declared failed
PARK_MAX_S = 1800          # a park marker older than this is a crashed park, not an active one
# States that mean "this boot is over and it did not work". The poll must stop on these instead
# of burning all 420 tries and returning as if nothing happened.
_TERMINAL_BRAIN = frozenset(("error", "ram_refused", "missing", "missing_colibri"))
# One borrow at a time, process-wide. Two limbs borrowing at once is how a second render gets a
# None token, then the FIRST render's wake boots the chat model on top of the second render.
_BORROW_LOCK = threading.Lock()


def need_for(path: Any) -> int:
    """Free VRAM a picture needs, sized from its checkpoint when we can measure it."""
    try:
        if path is not None and path.is_file():
            return render_need_mib(path.stat().st_size)
    except Exception:
        pass
    return render_need_mib(0)


def need_ram_for(weight_mib: list[int], headroom_mib: int = 1024) -> int:
    """Free RAM a multi-weight render needs: the SUM of its weights plus headroom.

    SUM, not the largest file. An engine like ComfyUI loads the unet and then the text encoder
    in the SAME pass - the requirement is cumulative, and MEASURED 2026-10-09 the 6.7 GB text
    encoder failed to load after the 5.4 GB unet was already resident
    (`HostBuffer.read_file_slice failed` inside CLIPTextEncode, its log showing the earlier
    nodes already `executed`).

    The headroom must stay SMALL. MEASURED 2026-10-09: with 2048 the floor was 14965 MiB while
    this box settles at 14420 MiB once the brain is parked - the render missed the floor by 545
    MiB, so the park "failed", the brain was restored, and the render ran anyway with the brain
    competing for RAM. It took 16+ min against a 4.5 min baseline: thrashing, not rendering.
    A floor that is missed by a hair is worse than no floor at all, because failing it does not
    stop the render - it only takes the RAM away.
    """
    total = 0
    for m in weight_mib or []:
        try:
            total += int(m or 0)
        except Exception:
            continue
    return (total + headroom_mib) if total else 0


def _available_ram_mib() -> int:
    try:
        return int((ram() or {}).get("available_mib") or 0)
    except Exception:
        return 0


def _safe_vram() -> int:
    """Free VRAM for the log only - -1 rather than raising, so logging never changes behaviour."""
    try:
        from hardware import free_vram_mib
        return int(free_vram_mib() or 0)
    except Exception:
        return -1


def prefer_speed() -> bool:
    """True when the operator has asked for speed ahead of quality (`"image_prefer": "speed"`).

    The default is QUALITY, by the operator's explicit call: rendering the declared checkpoint
    is the point of the console, and buying seconds by substituting a smaller checkpoint trades
    away the output. On an 8 GB card with the desktop holding 0.9-1.6 GB, SDXL (6616 MiB) often
    does not fit even with the brain parked - so quality mode accepts a CPU render of minutes
    rather than quietly shipping a 512x512 picture. Speed mode uses the largest checkpoint that
    fits instead (`best_checkpoint`). Set config/console.json "image_prefer" to change it.
    """
    try:
        from media_tools import _cfg
        v = str((_cfg() or {}).get("image_prefer") or "quality").strip().lower()
        return v in ("speed", "fast", "quick")
    except Exception:
        return False


def checkpoints() -> list:
    """Declared *.safetensors checkpoints under <media_root>/models/sd, SMALLEST first."""
    try:
        from media_tools import media_root
        d = media_root() / "models" / "sd"
        if not d.is_dir():
            return []
        found = []
        for p in d.glob("*.safetensors"):
            try:
                found.append((int(p.stat().st_size), p))
            except OSError:
                continue
        found.sort()
        return [p for _, p in found]
    except Exception:
        return []


def fits_on_card(path: Any) -> bool:
    """True when the card could take this checkpoint AS THINGS STAND now."""
    try:
        if path is None:
            return False
        return picture_route(render_need_mib(path.stat().st_size))[0] == "cuda"
    except Exception:
        return False


def best_checkpoint(default: Any) -> Any:
    """`default` if the card can take it, else the LARGEST declared checkpoint that fits.

    MEASURED 2026-10-09: with the chat model parked this box had 6600 MiB free against an SDXL
    checkpoint of 6616 MiB - 16 MiB short, so `picture_route` correctly refused and the render
    went to the CPU for 284 s instead of the card's 25 s. The weights, not the resolution, are
    what will not fit, so the only way onto the card is a smaller checkpoint. SD 1.5 is on disk
    and fits. Returns `default` when nothing fits, so the caller's own CPU fallback still runs.
    """
    try:
        if fits_on_card(default):
            return default
    except Exception:
        pass
    for p in reversed(checkpoints()):        # largest first
        try:
            if fits_on_card(p):
                return p
        except Exception:
            continue
    return default


def _dbg(msg: str) -> None:
    """Append one line to workspace/logs/render_swap.log.

    This subsystem reads live sensors and then moves the operator's chat model, and its failure
    mode is SILENT: the render simply goes to the CPU and takes 10x longer with no error. A
    trace is the only way to see why it declined, so it always writes one.
    """
    try:
        if _WORKSPACE is None:
            return
        d = _WORKSPACE / "logs"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "render_swap.log"
        try:
            if p.stat().st_size > 200_000:
                # ROTATE, never truncate. Wiping in place drops exactly the line you would need if
                # the process dies in that window - and this log is the only trace of a silent CPU
                # render, so losing it is losing the diagnosis. Keep one previous generation.
                p.replace(p.with_name(p.name + ".1"))
        except OSError:
            pass
        with p.open("a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def _api(path: str, payload: dict | None = None, timeout: float = 8.0) -> dict:
    url = "http://127.0.0.1:%d%s" % (CONSOLE_PORT, path)
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def _port_open(port: int) -> bool:
    s = socket.socket()
    s.settimeout(1.0)
    try:
        s.connect(("127.0.0.1", int(port)))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _room_now(need_mib: int, ram_floor_mib: int) -> bool:
    """True when this render could start right now."""
    if need_mib:
        try:
            if picture_route(need_mib)[0] != "cuda":
                return False
        except Exception:
            return False
    if ram_floor_mib:
        got = _available_ram_mib()
        if got <= 0:
            # 0 is this sensor's FAILURE return, not "the machine is empty". Treating it as
            # headroom reports "room already", declines every park, and sends the render to fight
            # the chat model for RAM - silently, which is this module's whole failure mode.
            return False
        if got < ram_floor_mib:
            return False
    return True


def _wait_for_room(need_mib: int, ram_floor_mib: int, tries: int = DRAIN_TRIES) -> bool:
    for _ in range(tries):
        try:
            if _room_now(need_mib, ram_floor_mib):
                return True
        except Exception:
            return False
        time.sleep(0.5)
    return False


def _marker_path():
    try:
        if _WORKSPACE is None:
            return None
        return _WORKSPACE / "render_swap.park"
    except Exception:
        return None


def parked_since() -> float:
    """Epoch seconds a render park began, or 0 when none is active.

    The console calls this from its OWN spawn path. While the brain is parked, `STATE["brain"]`
    still reads "ready" (`stop_port` does not touch it), so any other chat that arrives calls
    `maybe_spawn` and loads the full chat model back onto the machine the render is using - the
    park is silently undone mid-render. The two modules share no memory, so an mtime marker is the
    cheapest truthful signal. A marker older than PARK_MAX_S is a crashed park, not an active one.
    """
    p = _marker_path()
    if p is None:
        return 0.0
    try:
        t = float(p.read_text(encoding="utf-8").strip() or 0)
    except Exception:
        return 0.0
    return t if t and (time.time() - t) < PARK_MAX_S else 0.0


def _set_park_marker(on: bool) -> None:
    p = _marker_path()
    if p is None:
        return
    try:
        if on:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("%.0f" % time.time(), encoding="utf-8")
        else:
            p.unlink(missing_ok=True)
    except Exception:
        pass


def park_for_render(need_mib: int = 0, ram_floor_mib: int = 0) -> dict[str, Any] | None:
    """Stop or shrink the chat engine when it is what stands between a render and the machine.

    Returns a token for wake(), or None when there is nothing to do - the machine is already
    usable, no chat engine of ours is serving, or parking did not make room after all.
    """
    try:
        if _room_now(need_mib, ram_floor_mib):
            _dbg("decline: room already (need=%d ram_floor=%d free_vram=%d ram=%d)"
                 % (need_mib, ram_floor_mib, _safe_vram(), _available_ram_mib()))
            return None                      # already usable: do not disturb the brain
    except Exception as exc:
        _dbg("decline: sensor error %s" % (type(exc).__name__,))
        return None                          # a sensor that fails must never decide

    if not _port_open(LLAMA_PORT):
        _dbg("decline: nothing listening on %d" % LLAMA_PORT)
        return None                          # nothing of ours is holding the machine

    try:
        mid = str(_api("/api/health", timeout=5).get("selected") or "")
    except Exception as exc:
        _dbg("decline: health unreadable %s" % (type(exc).__name__,))
        return None
    if not mid:
        _dbg("decline: no selected model")
        return None

    _dbg("park attempt: need=%d ram_floor=%d free_vram=%d ram=%d mid=%s"
         % (need_mib, ram_floor_mib, _safe_vram(), _available_ram_mib(), mid))

    # Assign the token BEFORE the stop. If this is interrupted between stopping the engine and
    # returning (a KeyboardInterrupt during the drain sleep), `borrowed_card`'s `finally` must
    # still have something to wake - otherwise the brain stays down for good.
    token: dict[str, Any] = {"mid": mid}

    # Stop it outright. This is the ONLY route, and it is the one that frees the CARD as well as
    # the RAM. A "shrink to a smaller model" park was tried and REMOVED: it frees RAM but leaves a
    # small model resident on the card, and MEASURED 2026-10-10 that stole enough VRAM to push
    # ComfyUI into CPU offload (a clip that takes 4.5 min was still running at 13+ min). Worse, it
    # had to go through `/api/select`, which persists the tiny model as the operator's manual pin
    # (`selected_source: "manual"`), so a console killed while parked booted the wrong model - a
    # park must never edit what the operator chose.
    try:
        from engine import stop_port       # shared singleton: NOT __main__
        stop_port(LLAMA_PORT)
        token["parked_with"] = "stopped"
        _dbg("stopped engine on %d" % LLAMA_PORT)
    except Exception as exc:
        _dbg("park decline: stop_port failed (%s)" % (type(exc).__name__,))
        return None
    if _wait_for_room(need_mib, ram_floor_mib):
        _dbg("parked by stopping (free_vram=%d ram=%d)"
             % (_safe_vram(), _available_ram_mib()))
        return token

    # We moved the brain and did not reach the floor. STAY PARKED and hand the token back: the
    # render runs regardless of this threshold (a failed park does not cancel the render), so
    # restoring the brain here would only take the RAM back and leave the render thrashing -
    # MEASURED 2026-10-09, a video that should take 4.5 min spent 16+ min that way. The caller's
    # `finally` wakes the brain when the render is done.
    _dbg("floor not reached after parking (free_vram=%d ram=%d need=%d floor=%d) - staying parked"
         % (_safe_vram(), _available_ram_mib(), need_mib, ram_floor_mib))
    token["parked_with"] = "stopped-short"
    return token


def _engine_serving() -> bool:
    """True when the chat engine's OWN port answers.

    This is the only proof the brain is really back. `STATE["brain"] == "ready"` is not proof: the
    console keeps that value from before the park (`stop_port` never touches it), and any other
    boot flips it too. The engine port is the thing the follow-up turn will actually call, so reach
    for that instead of trusting a status string.
    """
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/health" % LLAMA_PORT, timeout=4) as r:
            return 200 <= int(getattr(r, "status", 200)) < 300
    except Exception:
        return False


def wake(token: dict[str, Any] | None, wait: bool = True) -> bool:
    """Bring the chat brain back and PROVE it is serving. True only when it really is.

    The console runs a limb BEFORE the model writes its reply, so this is not cleanup - it is what
    makes the turn that asked for the render survivable. Returning on a failed `/api/select` (the
    previous behaviour) left a dead engine while the console still reported `brain: ready`, so the
    turn's own follow-up call hit a closed port and died with nothing in any log. So: retry the
    select until a boot actually starts, fail fast on a terminal state instead of polling out all
    420 tries, and only report success when the engine port answers AND the selection is ours.
    """
    if not token:
        return True
    mid = str(token.get("mid") or "")
    if not mid:
        return True

    started = False
    for attempt in range(WAKE_ATTEMPTS):
        try:
            _api("/api/select", {"id": mid}, timeout=20)
            started = True
            break
        except Exception as exc:
            _dbg("wake: select attempt %d/%d failed (%s)"
                 % (attempt + 1, WAKE_ATTEMPTS, type(exc).__name__))
            time.sleep(1.5)
    if not started and not _engine_serving():
        _dbg("WAKE FAILED: no boot started for %s after %d attempts" % (mid, WAKE_ATTEMPTS))
        return False
    if not wait:
        return True

    brain = ""
    for _ in range(WAKE_TRIES):
        try:
            h = _api("/api/health", timeout=5)
            brain = str(h.get("brain") or "")
            if brain in _TERMINAL_BRAIN:
                _dbg("WAKE FAILED: brain=%s for %s" % (brain, mid))
                return False
            if brain == "ready" and str(h.get("selected") or "") == mid and _engine_serving():
                _dbg("wake ok: %s serving" % mid)
                return True
        except Exception:
            pass
        time.sleep(0.5)
    _dbg("WAKE TIMEOUT after %d tries: brain=%s wanted=%s" % (WAKE_TRIES, brain, mid))
    return False


@contextmanager
def borrowed_card(need_mib: int = 0, ram_floor_mib: int = 0):
    """Park the chat brain only if that is what stands between this render and the machine.

    Yields True when the machine really was borrowed, False when nothing was parked. The borrow is
    serialised process-wide: two limbs borrowing at once is how one render gets a None token and the
    other's wake boots the chat model on top of it.
    """
    token = None
    with _BORROW_LOCK:
        try:
            token = park_for_render(need_mib, ram_floor_mib)
            if token:
                _set_park_marker(True)     # tell the server's spawn path to keep its hands off
            yield bool(token)
        finally:
            _set_park_marker(False)
            wake(token)
