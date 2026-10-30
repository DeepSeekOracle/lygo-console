from __future__ import annotations

# Every child process stays invisible: no console window over Justin's screen.
try:
    from win_quiet import install as _quiet_children
    _quiet_children()
except Exception:  # noqa: BLE001 - a missing guard must never stop the console
    pass

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sys as _sys

# A stick console is often cp437: a print() of a non-ASCII line (the Delta9Phi963 signature,
# a model answer) must degrade, never raise.
for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001 - not a real console (pipe, IDE, pytest capture)
        pass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# kit src on path
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:  # the module layer must never be able to cost the operator the console (WO-0001)
    from modules import host as module_host  # noqa: E402
except Exception as _module_err:  # noqa: BLE001
    module_host = None
    print(f"[modules] module layer unavailable: {type(_module_err).__name__}: {_module_err}", flush=True)

import http_body  # noqa: E402  (defect 27: how a body we refuse is consumed)
import version  # noqa: E402  (the release number lives in VERSION - see src/version.py)
from atomicio import atomic_write_text  # noqa: E402
from atomicio import read_text as read_text_locked  # noqa: E402
from auth import check, ensure_llama_key, ensure_token, token_from_request  # noqa: E402
import api_key as engine_key  # noqa: E402 - one persistent key for the engine API
from chat_loop import (  # noqa: E402
    extract_user_text,
    console_completes_the_write,
    has_image,
    host_prefetch,
    normalise_messages,
    prefetch_message,
    run_tools_round,
    trim_history,
    user_text_of,
)
from continuity import (  # noqa: E402
    compose_system,
    ensure_identity,
    identity_path,
    load_session,
    memory_path,
    new_session,
    save_session,
    soul_path,
)
from engine import (  # noqa: E402
    ENGINE_LOCK,
    foreign_daemon_port_open,
    ram_ok,
    resolve_binary,
    runner_for,
    spawn_runner,
    stop_port,
)
from p0_hook import (  # noqa: E402
    PHYSICS_AVAILABLE,
    POLICY_MAX_CONVERSATION_CHARS,
    SIGNATURE as P0_SIGNATURE,
    gate_output_window,
    gate_prompt,
)
try:  # the record module must never be able to cost the operator the console
    import compaction  # noqa: E402
    from compaction import (  # noqa: E402
        carry_over,
        live_ctx,
        safe_pre_turn,
        safe_record,
        safe_status,
        trim_messages,
    )

    COMPACTION_AVAILABLE = True
    COMPACTION_ERROR = ""
except Exception as _compact_err:  # noqa: BLE001  (a broken record module, not a broken console)
    import traceback as _tb

    COMPACTION_AVAILABLE = False
    COMPACTION_ERROR = f"{type(_compact_err).__name__}: {_compact_err}"
    _tb.print_exc()

    class _NoRecord:
        """Every read answers honestly instead of raising, so the routes stay 200 and say why."""

        BUILD_TAG = "unavailable"
        RECALL_K = 6
        ARCHIVE = INDEX = SESSIONS = ROLLUPS = CHECKPOINTS = None  # type: ignore[assignment]
        CARRY_CAP = 0

        def __getattr__(self, name: str):
            def _missing(*a, **k):
                return {"ok": False, "error": "compaction_unavailable", "detail": COMPACTION_ERROR}

            return _missing

    compaction = _NoRecord()  # type: ignore[assignment]

    def live_ctx() -> int:  # noqa: D103
        try:
            from paths import console_limits

            return int(console_limits().get("ctx_max") or 32768)
        except Exception:  # noqa: BLE001
            return 32768

    def carry_over(cap: int = 0) -> str:  # noqa: D103
        return ""

    def _no_record(*a, **k) -> dict:
        return {"ok": False, "error": "compaction_unavailable", "detail": COMPACTION_ERROR}

    safe_pre_turn = safe_record = safe_status = _no_record  # type: ignore[assignment]

    def trim_messages(messages: list, ctx: int | None = None, keep_turns: int | None = None):
        """Fall back to the console's own message window - the behaviour before the record existed."""
        kept = trim_history(messages)
        return kept, max(0, len(messages) - len(kept)), {"ok": False, "fallback": "trim_history"}
# How much recalled history a turn may carry when the window is full. What the window cannot
# hold is not lost - it is filed, and the passages that bear on the message come back with it.
RAG_RECALL_CHARS = 1800


try:  # the vault is a second, independent subsystem: its absence must not cost the console either
    import sessions  # noqa: E402
    from sessions import (  # noqa: E402
        safe_catalog,
        safe_open,
        safe_resume,
        safe_search,
        safe_stats,
        safe_vault_live,
    )

    SESSIONS_AVAILABLE = True
    SESSIONS_ERROR = ""
except Exception as _sessions_err:  # noqa: BLE001  (a broken vault module, not a broken console)
    SESSIONS_AVAILABLE = False
    SESSIONS_ERROR = f"{type(_sessions_err).__name__}: {_sessions_err}"

    class _NoVault:
        """Every session read answers honestly instead of raising."""

        BUILD_TAG = "unavailable"

        def __getattr__(self, name: str):
            def _missing(*a, **k):
                return {"ok": False, "error": "sessions_unavailable", "detail": SESSIONS_ERROR}

            return _missing

    sessions = _NoVault()  # type: ignore[assignment]

    def _no_vault(*a, **k) -> dict:
        return {"ok": False, "error": "sessions_unavailable", "detail": SESSIONS_ERROR}

    safe_catalog = safe_open = safe_resume = safe_search = safe_stats = safe_vault_live = _no_vault  # type: ignore[assignment]
from paths import (  # noqa: E402
    COLIBRI_PORT,
    CONSOLE_JSON,
    DEFAULT_PORT,
    EMBED_PORT,
    ENGINE_DIR,
    KIT_ROOT,
    LLAMA_PORT,
    LOCAL_JSON,
    PORTAL,
    WORKSPACE,
    ensure_dirs,
)
from receipts import prune_at_startup, write_receipt  # noqa: E402
from registry import get as reg_get  # noqa: E402
from registry import load as reg_load  # noqa: E402
from registry import upsert as reg_upsert  # noqa: E402
from scanner import discover, scan_roots  # noqa: E402
import model_fit  # noqa: E402
import turnperf  # noqa: E402  (what a turn cost, and which of its tokens were never shown)
from tools import TOOLS_SCHEMA, core_schema  # noqa: E402

TOKEN = ""
LLAMA_KEY = ""
BIND = "127.0.0.1"
AUTH_REQUIRED = False
MOCK_ONLY = False
# The release number lives in ONE file (VERSION); see src/version.py for why. A bump there
# moves the header, this health line and the runtime facts together.
BUILD = version.stamp()
STATE: dict[str, Any] = {"brain": "missing", "selected": None, "error": None, "scan_n": 0, "engine": "llama", "engine_port": LLAMA_PORT}


def brain_port() -> int:
    return int(STATE.get("engine_port") or LLAMA_PORT)


def _sync_hermes_rails() -> None:
    """Keep the isolated Hermes rails on the console's current model and API."""
    try:
        import hermes_brain
        import cloud_api

        st = cloud_api.public_status()
        route = cloud_api.hermes_route() if st.get("enabled") else {}
        hermes_brain.sync_harness(
            model=str(STATE.get("selected") or "lygo-local"),
            engine_port=brain_port(),
            console_port=int(DEFAULT_PORT),
            api_route=route or None,
        )
    except Exception as exc:  # noqa: BLE001 - rails must not take the console down
        print("[hermes] rails skip: " + type(exc).__name__ + ": " + str(exc)[:160], flush=True)


# A peer that opens a socket and stalls must not hold a handler thread until it decides to close.
try:
    REQUEST_TIMEOUT = float(os.environ.get("LYGO_REQUEST_TIMEOUT", "120"))
except ValueError:
    REQUEST_TIMEOUT = 120.0

# A body we refuse is consumed before the answer is written (defect 27, 2026-09-20). Refusing
# without reading leaves the caller's write in the socket: we answer and close, Windows resets the
# rest of that write, and the caller sees a network error instead of the reason — the portal's own
# paste path did. Bounded twice, because a declared length is the caller's word and not ours:
# discard at most DRAIN_CAP bytes, and wait at most DRAIN_TIMEOUT seconds for what has not arrived.
DRAIN_CAP = http_body.CAP
DRAIN_TIMEOUT = http_body.TIMEOUT
DRAIN_CHUNK = http_body.CHUNK

# host_prefetch fetches URLs out of the user's text before the model answers. On by default, but
# switchable: a fetched page is untrusted input landing beside the shell and python limbs.
HOST_PREFETCH = os.environ.get("LYGO_HOST_PREFETCH", "1").strip().lower() not in ("0", "false", "no")


# A browser that goes away mid-answer is not a server fault: these are the socket errors from
# writing to a socket the client already closed (tab closed, Stop pressed, page reloaded).
_CLIENT_GONE = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)


class _BadRequest(Exception):
    """A malformed request envelope (Content-Length, transfer encoding) -> 400, not a 500."""


class _BodyTooLarge(Exception):
    """Request body over the endpoint's limit: answers 413 instead of a misleading 400."""


# A half-written or hand-edited console.json used to raise out of load_console(): startup
# aborted before the server existed and every endpoint that reads the config answered 500.
# Bad config now costs the operator the config, never the console, and the reason is reported
# through /api/health instead of vanishing into a stderr traceback.
CONFIG_ERRORS: list[str] = []
_CONFIG_CACHE: dict[str, Any] = {"key": None, "cfg": {}}


def _note_config_error(msg: str) -> None:
    if msg not in CONFIG_ERRORS:
        CONFIG_ERRORS.append(msg)
    del CONFIG_ERRORS[5:]


def _config_key() -> tuple[Any, ...] | None:
    try:
        return tuple(
            (p.stat().st_mtime_ns, p.stat().st_size) if p.is_file() else None
            for p in (CONSOLE_JSON, LOCAL_JSON)
        )
    except OSError:
        return None


def load_console() -> dict[str, Any]:
    """config/console.json, optionally overlaid by config/local.json.

    Parsed once per file state: every POST used to re-read and re-parse both files.
    """
    key = _config_key()
    if key is not None and _CONFIG_CACHE.get("key") == key:
        return dict(_CONFIG_CACHE["cfg"])
    cfg: dict[str, Any] = {}
    for path in (CONSOLE_JSON, LOCAL_JSON):
        if not path.is_file():
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 - a broken config must not kill the console
            _note_config_error(f"{path.name}: {type(e).__name__}: {e}"[:200])
            continue
        if isinstance(loaded, dict):
            cfg = {**cfg, **loaded}
        else:
            _note_config_error(f"{path.name}: not a JSON object")
    if key is not None:
        _CONFIG_CACHE.update(key=key, cfg=cfg)
    return dict(cfg)


def default_scan_roots(cfg: dict[str, Any]) -> list[str]:
    raw = cfg.get("scan_roots")
    if not isinstance(raw, list) or not raw:
        raw = ["./models"]
    out: list[str] = []
    for r in raw:
        s = os.path.expandvars(os.path.expanduser(str(r)))
        p = Path(s)
        if not p.is_absolute():
            p = (KIT_ROOT / s).resolve()
        out.append(str(p))
    # This kit boots its own engine on its own GGUF vault. It never looks inside another daemon's
    # store: no home daemon store, no foreign daemon env var, no descent into a folder
    # named after one. A store is OURS (`cas` = a manifests/ + blobs/ pair we carry) or it is the
    # operator's deliberate mapping in config/console.json scan_roots. A dependency would be the
    # difference between a walking agent and an empty shell on a machine that has never run anything
    # else - which is the whole point of this kit.
    # Declared roots first, kit-relative second, raw drive letters last: a kit packaged onto
    # another drive must find its own models without a stale letter being the only candidate.
    extras = [KIT_ROOT / "models"]
    for env_name in ("LYGO_STACK_ROOT", "LYGO_BUILDER_KEY_ROOT"):
        declared = os.environ.get(env_name, "").strip()
        if declared:
            base = Path(os.path.expandvars(declared))
            extras.extend([base / "models", base / "product" / "models" / "cas"])
    extras.extend(
        [
            Path(r"<MODELS>"),
            Path(r"<DRIVE_D>\LYGO_MODEL_VAULT"),
            Path(r"<DRIVE_D>\LYGO_MODEL_VAULT\cas"),
            Path(r"U:\LYGO\models"),
            Path(r"F:\LYGO\models"),
            Path(r"<USB_ROOT>\LYGO_BUILDER_KEY\product\models\cas"),
        ]
    )
    usb = os.environ.get("LYGO_USB_ROOT", "").strip()
    if usb:
        extras.append(Path(usb) / "product" / "models" / "cas")
        extras.append(Path(usb) / "models")
    # LYGO_MODELS is the kit's own declaration of its model vault (the launchers set it; a machine
    # can set it too). A foreign daemon's variable is deliberately NOT read: a folder of GGUF bytes
    # is only ever read as files, but naming another service inside our own resolution path is how a
    # kit starts to believe it needs that service.
    for env_name in ("LYGO_MODELS",):
        declared = os.environ.get(env_name, "").strip()
        if declared:
            extras.append(Path(os.path.expandvars(declared)))
    # A CAS is a folder holding manifests/ + blobs/, and it usually sits one level down
    # (<usb>\models\cas), so descend from the declared parents too.
    for parent in list(extras):
        child = parent / "cas"
        if (child / "manifests").is_dir() and (child / "blobs").is_dir():
            extras.append(child)
    # Portable: console lives at <USB>/lygo_llm_console
    extras.append(KIT_ROOT.parent / "product" / "models" / "cas")
    for p in extras:
        try:
            s = str(p)
        except Exception:
            continue
        if p.exists() and s not in out:
            out.append(s)
    return out


def _next_model_rec(models: list[dict[str, Any]], exclude: list[str]) -> dict[str, Any] | None:
    """Next brain to try after one failed to load here: registry order, minus what we tried."""
    try:
        import registry as _reg

        for mid in _reg.candidates(models, prefer_ram=_reg.prefer_by_ram()):
            if mid in exclude:
                continue
            rec = next((m for m in models if m.get("id") == mid), None)
            if rec and rec.get("runnable") and rec.get("path"):
                return rec
    except Exception:  # noqa: BLE001 - the fallback picker must never be what fails
        return None
    return None


VISION_BLIND_NOTE = (
    "\u26a0 the picture attached to this message was not looked at: {model} has no vision projector on "
    "this machine, so only your text was read. Attach the picture with the FILE button to keep it in the "
    "workspace, or boot a model with vision."
)


def maybe_spawn(model_id: str | None) -> str:
    if MOCK_ONLY:
        STATE["brain"] = "mock"
        return "mock"
    # A render park OWNS the machine until the render finishes. Our own chat engine was stopped on
    # purpose, and STATE still reads "ready" (stop_port does not touch it), so without this guard a
    # second chat arriving mid-render would load the full chat model back on top of the render it
    # is waiting for - undoing the park silently. Wait it out instead. render_swap.parked_since()
    # reports 0 for a stale marker, so a crashed park can never wedge the console.
    try:
        import render_swap as _rs
        _wait_until = time.time() + 900
        while _rs.parked_since() and time.time() < _wait_until:
            time.sleep(1.0)
    except Exception:
        pass
    rec = reg_get(model_id) if model_id else None
    if rec is None:
        data = reg_load()
        rec = next((m for m in data.get("models") or [] if m.get("id") == data.get("selected")), None)
    if not rec or not rec.get("path") or not rec.get("runnable"):
        STATE["brain"] = "missing"
        return "missing"
    # A record can name a file this machine does not have - the stick's registry was measured carrying
    # eight such records, its only vision model among them. Failing here, naming the path it wanted, is
    # answerable; the engine's own "failed to load model" inside a log tail is not.
    from registry import reach as _reach

    _where = _reach(rec)
    if not _where["reachable"]:
        STATE["brain"] = "error"
        STATE["error"] = "model_not_on_this_machine: %s - %s" % (rec.get("id"), _where["why"])
        return "error"
    from lygo_engine import boot as lygo_boot

    # A model whose header parses but which this engine cannot actually load (wrong tensor
    # offsets, unsupported quant, half-copied file) used to be fatal: brain=error and a dark
    # console. Remember the verdict for THIS host and boot the next-best brain instead.
    tried: list[str] = []
    last_err = ""
    for attempt in range(3):
        try:
            if attempt == 0:
                STATE.pop("model_fallback", None)
            return lygo_boot(rec, api_key=LLAMA_KEY, state=STATE)
        except MemoryError as e:
            STATE["brain"] = "ram_refused"
            STATE["error"] = str(e)
            return "ram_refused"
        except Exception as e:  # noqa: BLE001 - never let one bad file take the console down
            last_err = f"{type(e).__name__}: {e}"
            mid = str(rec.get("id") or "")
            if mid:
                tried.append(mid)
            try:
                import model_verdicts

                model_verdicts.mark_bad(mid, str(rec.get("path") or ""), last_err)
            except Exception:  # noqa: BLE001
                pass
            nxt = _next_model_rec(reg_load().get("models") or [], exclude=tried)
            if not nxt:
                break
            rec = nxt
            STATE["model_fallback"] = (
                f"{tried[-1]} could not load here ({last_err}); trying {rec.get('id')}"
            )
    STATE["brain"] = "error"
    STATE["error"] = last_err
    return "error"


def local_system_message(brain: str = "local") -> dict[str, Any]:
    """The system message every local turn opens with - built in ONE place.

    The prefix primer below has to send the identical opening, so both callers come through here. If
    they ever drift apart the primer silently stops helping, which is the kind of defect that looks
    like "the engine got slow again" months later.
    """
    from continuity import compose_system

    # with_clock=False: the clock is volatile and now rides the NEWEST message instead (see
    # local_tail + continuity.volatile_tail). Inside this block it sat in front of the history and
    # invalidated the prompt cache for the whole turn, every turn.
    return {"role": "system", "content": compose_system(brain, carry=True, with_clock=False)}


def prefix_prime_payload(model: str) -> dict[str, Any]:
    """The opening a real local turn sends, built in ONE place for the prefix primer.

    The primer only works if its prompt has the same longest-common-prefix with a real turn as two
    real turns have with each other. Built inline, the two shapes could drift silently and the
    optimisation would just stop helping - a defect that looks like "the engine got slow again".
    """
    from tools import core_schema

    tail = local_tail()
    return {
        "model": model,
        "messages": [
            local_system_message("local"),
            {"role": "user", "content": "ready" + (("\n\n" + tail) if tail else "")},
        ],
        "max_tokens": 1,
        "stream": False,
        "tools": core_schema(),
    }


def local_tail(with_clock: bool = True) -> str:
    """The volatile block that rides the newest message of every local turn.

    `with_clock=False` is for a turn that asks nothing: the clock is material with nothing to do there,
    and the model recited it back instead of answering the greeting (continuity.volatile_tail has the
    measurement and the fix).
    """
    from continuity import volatile_tail

    return volatile_tail(with_clock=with_clock)


def _hardware_reading(*, local_caller: bool) -> dict[str, Any]:
    """Live card/RAM/CPU reading for /api/health. Cached two seconds inside hardware.py."""
    import hardware as _hw

    snap = _hw.snapshot()
    out: dict[str, Any] = {
        "available": snap.get("available"),
        "why": snap.get("why"),
        "gpus": snap.get("gpus") or [],
        "ram": snap.get("ram") or {},
        "cpu": snap.get("cpu") or {},
    }
    if local_caller:
        # nvidia-smi lists every process with a graphics context; this desktop has ~23, nearly all at
        # 0 MiB. The panel gets the ones actually holding memory, plus the count so the noise is visible
        # rather than printed as if it were load.
        holders = snap.get("holders") or []
        busy = [a for a in holders if a.get("used_mib", 0) > 0]
        out["holders"] = busy or holders[:5]
        out["holders_total"] = len(holders)
    return out


def health_payload(*, local_caller: bool, authenticated: bool) -> dict[str, Any]:
    """What /api/health answers. One failing probe blanks its own field; the route never 500s.

    Measured on this tree 2026-09-21, two boots: `GET /api/health -> 500` once while the engine was
    still loading (five polled 200s, one 500, then 200s again) and once as the very last line the
    console logged before the process went away. The operator's own console ended its log the same way
    at 00:16 that morning, which is why it read as "the console died". The body carries the exception
    and the handler sends it only to a loopback caller, so neither cause was ever readable - and it does
    not need to be. This route is what the operator reads state from, and every field below is one call
    away from raising on a box where a drive can be unplugged mid-read. A partial answer that names the
    probe it could not read is worth more than no answer at all.
    """
    degraded: list[dict[str, str]] = []

    def safe(field: str, fn, fallback):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - one probe may never take the whole route down
            degraded.append({"field": field, "error": f"{type(exc).__name__}: {exc}"[:200]})
            return fallback

    import backends as _backends
    import cloud_api as _cloud
    import paths as _paths
    import perf as _perf
    import registry as _registry
    import surface as _surface
    import version as _version
    from engine import available_ram_bytes

    ram = safe("ram_avail", available_ram_bytes, None)
    return {
        # What this box can use RIGHT NOW (src/hardware.py): the same reading the picture limb uses to
        # decide between the card and the CPU, so the operator can SEE the number that decided it.
        # Process names and pids only for a local caller, like every other path-shaped field here.
        "hardware": safe("hardware", lambda: _hardware_reading(local_caller=local_caller), {}),
        "ok": True,
        "build": BUILD,
        "release": safe("release", _version.release, ""),
        "release_tag": safe("release_tag", _version.tag, ""),
        "signature": "Δ9Φ963-LYGO-LLM-CONSOLE-v1",
        "authenticated": authenticated,
        "physics": PHYSICS_AVAILABLE,
        "brain": STATE.get("brain"),
        "selected": safe("selected", lambda: STATE.get("selected") or reg_load().get("selected"), None),
        "vision": safe("vision", _registry.selected_vision, None),
        # The reply cap this console is actually running (console.json -> local.json). The page reads it
        # and asks for it, instead of hardcoding its own number: it shipped 768, and that constant - not
        # this server - is what cut every long answer off mid-sentence.
        "max_tokens": safe("max_tokens", lambda: _paths.console_limits().get("max_tokens"), None),
        "error": STATE.get("error"),
        "model_fallback": STATE.get("model_fallback"),
        "scan_n": safe("scan_n", lambda: STATE.get("scan_n") or len(reg_load().get("models") or []), 0),
        "foreign_daemon_port_open": safe("foreign_daemon_port_open", foreign_daemon_port_open, False),
        "engine_present": safe("engine_present", lambda: bool(resolve_binary()), False),
        "engine_dir": safe(
            "engine_dir",
            lambda: str(_paths.engine_dir()) if local_caller else Path(_paths.engine_dir()).name,
            "",
        ),
        "backend_layer": safe("backend_layer", _backends.report, {}),
        "system": safe("system", lambda: _surface.report(local_ready=(STATE.get("brain") == "ready")), {}),
        "ram_avail": ram,
        "ram_auto": safe("ram_auto", _registry.prefer_by_ram, {}),
        "ram_fit": safe("ram_fit", lambda: _registry.ram_choice(reg_load().get("models") or [], ram), {}),
        "perf": safe("perf", lambda: _perf.report(STATE), {}),
        "selected_source": safe("selected_source", lambda: reg_load().get("selected_source"), None),
        "bind": BIND,
        "port": DEFAULT_PORT,
        "tools": safe("tools", lambda: [t["function"]["name"] for t in TOOLS_SCHEMA], []),
        "workspace": str(WORKSPACE) if local_caller else WORKSPACE.name,
        "config_errors": CONFIG_ERRORS,
        "cloud": safe("cloud", _cloud.public_status, {}),
        "hermes": safe("hermes", lambda: __import__("hermes_brain").status(), {}),
        "last_brain": STATE.get("last_brain"),
        "last_perf": STATE.get("last_perf"),
        "fallback": STATE.get("fallback"),
        # Turns where a claimed limb output had no limb behind it - this console says so in the reply
        # and counts it here, so the operator can see the detector working instead of trusting it.
        "fabrications_caught": int(STATE.get("fabrications_caught") or 0),
        "degraded": degraded,
    }


def _stream_local_turn(
    payload: dict[str, Any],
    emit,
    stop_when: Any = None,
    stop_info: dict[str, Any] | None = None,
    on_timings: Any = None,
) -> str | None:
    """Run one local turn as a stream, showing each token as the engine writes it.

    The old engine streamed by default; this console's local path did not. It waited for the whole reply and
    then emitted ONE token frame, so a 1,300-token answer at ~8 tok/s sat silent for ~160 s and looked
    like a dead engine - the operator's complaint that gemma4 replies take forever. The page has
    rendered every `delta` frame it is sent all along, so the fix belongs here, on the ground.

    `stop_when` is the turn's asked shape (see src/turnperf.py): once the operator's own requested
    shape is complete, further tokens are text nobody asked for, so the read stops and the connection
    closes - which is how the engine's own generation is cut short instead of being waited out. It is
    also how the answer the operator keeps stays exactly what the shape asked for: the text is
    truncated back to the completion point, and the caller's existing `replace` frame corrects a page
    that had already rendered the extra tokens. `stop_info` receives what was stopped, for the record.

    Returns the finished text, or None when the engine never produced a single token. The caller then
    makes the ordinary buffered call, so this can only make a turn faster - never break one.
    """
    import json as _json

    from openai_proxy import llama_chat_stream

    ask = dict(payload)
    ask["stream"] = True
    # The numbers for this turn arrive in the final chunk, and only if they are asked for. Without
    # this the console could measure nothing at all about a streamed turn - which is every turn the
    # operator actually watches (measured 2026-09-22: a streamed turn recorded `perf: {}`).
    ask["stream_options"] = {"include_usage": True}
    acc: list[str] = []
    grace = int(getattr(stop_when, "grace", 0) or 0) if stop_when else 0
    complete_at: int | None = None
    complete_text = ""
    seen = 0
    first_delta_at: float | None = None
    try:
        with ENGINE_LOCK:
            for raw in llama_chat_stream(api_key=LLAMA_KEY, payload=ask, port=brain_port()):
                line = (raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)).strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    piece = _json.loads(chunk)
                except _json.JSONDecodeError:
                    continue
                if on_timings is not None:
                    # The final chunk of a streamed turn carries the engine's own timings/usage;
                    # every chunk is offered and the recorder ignores the ones that carry none.
                    try:
                        on_timings(piece)
                    except Exception:  # noqa: BLE001 - telemetry may not break the read
                        pass
                for choice in piece.get("choices") or []:
                    delta = (choice.get("delta") or {}).get("content")
                    if delta:
                        if first_delta_at is None:
                            first_delta_at = time.time()
                        acc.append(delta)
                        seen += 1
                        try:
                            emit({"type": "token", "delta": delta})
                        except Exception:  # noqa: BLE001 - a closed page must not kill the turn
                            pass
                        if stop_when is not None:
                            if complete_at is None:
                                if stop_when("".join(acc)):
                                    complete_at = seen
                                    complete_text = "".join(acc)
                            elif (seen - complete_at) >= grace:
                                break
                if complete_at is not None and (seen - complete_at) >= grace:
                    break
    except Exception as e:  # noqa: BLE001
        print(f"stream fallback: {type(e).__name__}: {e}")
    text = "".join(acc)
    if complete_at is not None:
        # The shape asked for is delivered; the tokens after it were never requested. Keeping the
        # truncation here (not at the engine) is deliberate: the operator's own words set the shape,
        # so nothing the console invented is being imposed on the answer.
        if stop_info is not None:
            stop_info.update(
                {
                    "why": "shape_complete:" + str(getattr(stop_when, "kind", "") or "shape"),
                    "at": complete_at,
                    "saved": max(0, seen - complete_at),
                    "text": complete_text[:200],
                    # What the stream actually delivered, and how long it took - the two things a
                    # stopped turn can still be measured by, since its final chunk never arrives.
                    "seen_text": text,
                    "read_ms": round((time.time() - first_delta_at) * 1000.0, 1) if first_delta_at else None,
                }
            )
            print("[turnperf] the asked shape was complete after %d token(s) - stopped the read there, "
                  "%d token(s) the engine had already sent were not shown" % (complete_at, max(0, seen - complete_at)),
                  flush=True)
        text = complete_text
    elif stop_info is not None:
        stop_info["seen_text"] = text
        stop_info["read_ms"] = round((time.time() - first_delta_at) * 1000.0, 1) if first_delta_at else None
    return text or None


def warm_prefix(model_id: str | None = None) -> None:
    """Put the fixed opening of a local turn into the engine's prompt cache after a boot.

    A local turn sends local_system_message() + history + core_schema(). The fixed part is large: on
    this class of machine prefilling it costs ~40 s at ~130 tokens/s, and llama-server streams nothing
    until prefill finishes - so the operator's first ask after pressing Boot looked like a dead engine
    and the portal stopped the answer after 60 s of silence. One 1-token completion with the identical
    opening, sent as soon as the engine reports ready, makes that first real ask cost seconds instead
    of a minute.

    This is an optimisation and never a reason for a turn to fail: it takes no engine lock (a request
    that arrives while it runs simply queues behind it, exactly as it would have anyway) and any error
    is logged and dropped.
    """
    if MOCK_ONLY or STATE.get("brain") != "ready":
        return
    model = str(model_id or STATE.get("selected") or "")
    if not model or STATE.get("prefix_primed_for") == model:
        return

    def _run() -> None:
        try:
            from openai_proxy import llama_chat
            from tools import core_schema

            from openai_proxy import for_local_engine

            payload = for_local_engine(prefix_prime_payload(model))
            t0 = time.time()
            code, _body, _ = llama_chat(api_key=LLAMA_KEY, payload=payload, port=brain_port(), timeout=900.0)
            if STATE.get("brain") == "ready" and STATE.get("selected") == model:
                STATE["prefix_primed_for"] = model
            print(f"prefix primed for {model} in {time.time() - t0:.1f}s (code {code})")
        except Exception as e:  # noqa: BLE001 - an optimisation may never break a turn
            print(f"prefix prime skipped: {type(e).__name__}: {e}")

    threading.Thread(target=_run, daemon=True, name="lygo-prefix").start()


def boot_async(model_id: str | None) -> None:
    STATE["brain"] = "booting"
    STATE["error"] = None

    def _run() -> None:
        try:
            status = maybe_spawn(model_id)
            if status == "ready":
                warm_prefix(model_id)
        except Exception as e:
            STATE["brain"] = "error"
            STATE["error"] = str(e)

    threading.Thread(target=_run, daemon=True, name="lygo-llm-boot").start()


GATE_CHUNK = 8000


def gate_all(text: str) -> dict[str, Any]:
    """Gate the WHOLE text, not just its head.

    soul / identity / memory accept up to 90 KB but were only scanned as text[:8000]; anything
    appended past the first chunk reached disk unscanned. Every chunk is judged now, and the
    first QUARANTINE verdict wins.
    """
    if len(text) <= GATE_CHUNK:
        return gate_prompt(text)
    head: dict[str, Any] = {}
    for i in range(0, len(text), GATE_CHUNK):
        verdict = gate_prompt(text[i : i + GATE_CHUNK])
        if verdict.get("verdict") == "QUARANTINE":
            return verdict
        head = head or verdict
    return head


def _read_text_locked(path: Any, limit: int = 80_000) -> str:
    """Read a state file through the retrying reader - a writer may be mid-swap right now."""
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        return read_text_locked(p, errors="replace")[:limit]
    except OSError:
        return ""


class Handler(BaseHTTPRequestHandler):
    # StreamRequestHandler applies this to the socket, so one stalled peer cannot pin a thread.
    timeout = REQUEST_TIMEOUT
    server_version = "LYGO-LLM-Console/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        msg = fmt % args
        bits = msg.split(" ")
        if len(bits) >= 2 and "?" in bits[1]:
            bits[1] = bits[1].split("?", 1)[0]
            msg = " ".join(bits)
        sys.stderr.write("%s - %s\n" % (self.address_string(), msg))

    def _query(self) -> dict[str, list[str]]:
        return parse_qs(urlparse(self.path).query)

    def _headers_map(self) -> dict[str, str]:
        return {k: v for k, v in self.headers.items()}

    def _loopback(self) -> bool:
        """Is this request from this machine? Judged on the socket, never on the Host header.

        Trusting Host let any LAN client send "Host: localhost" and collect the loopback
        shortcut - and with the token spliced into the page it fetched, that was a full
        bypass of the token layer. A local reverse proxy does rewrite Host, so that case is
        explicit opt-in instead of a default.
        """
        ip = ((self.client_address or ("", 0))[0] or "").lower().replace("::ffff:", "")
        if ip in ("127.0.0.1", "::1", "localhost"):
            return True
        if os.environ.get("LYGO_TRUSTED_PROXY", "").strip().lower() not in ("1", "true", "yes"):
            return False
        host = (self.headers.get("Host") or "").split(":")[0].lower().strip("[]")
        return host in ("127.0.0.1", "localhost", "::1")

    def _ok_public(self) -> bool:
        path = urlparse(self.path).path
        if path in ("/", "/api/health", "/api/world", "/api/keys") or path.startswith("/static/"):
            return True
        return False

    def _auth(self) -> bool:
        path = urlparse(self.path).path
        headers = self._headers_map()
        query = self._query()
        provided_key = engine_key.from_headers(headers)
        # The OpenAI-compatible surface is the external engine API. Once a key exists,
        # /v1 requires that key (the local console token is still accepted for the UI).
        if path.startswith("/v1/") and engine_key.configured():
            if provided_key and engine_key.verify(provided_key):
                return True
            return check(token_from_request(headers, query), TOKEN)
        if provided_key and engine_key.verify(provided_key):
            return True
        if not AUTH_REQUIRED:
            return True
        if self._loopback():
            return True
        # The portal shell and the health bar stay reachable without a token - that is how a
        # remote operator gets in. Everything else needs one, and the shell hands the token out
        # only to a loopback caller (see _dispatch_GET).
        if path in ("/", "/api/health", "/api/world", "/api/keys") or path.startswith("/static/"):
            return True
        return check(token_from_request(headers, query), TOKEN)

    def _send(self, code: int, body: bytes, ctype: str = "application/json", extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        # Agent Manager embeds this loopback console on http://127.0.0.1:8790.
        self.send_header("Referrer-Policy", "no-referrer")
        if getattr(self, "close_connection", False):
            # Say it, don't just do it: a caller that read Content-Length would otherwise treat
            # this socket as reusable and fail its next request on it (defect 27).
            self.send_header("Connection", "close")
        _cport = int(getattr(self.server, "server_port", 0) or 0)
        # One request leaves this page for a port beside it: the Boot server button rings the doorbell, which by
        # construction listens one below whichever port this copy bound (tools/doorbell.py). Not named here, the
        # browser refuses that call with "Failed to fetch" while the doorbell sits there perfectly alive - which
        # is exactly how this was found. Derived, so the USB copy gets its own port and never the PC's.
        _dport = (_cport - 1) if _cport > 1 else 9639
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https:; media-src 'self' blob: https:; "
            f"connect-src 'self' https: http://127.0.0.1:{_dport} http://localhost:{_dport} http://127.0.0.1:8790 http://localhost:8790; "
            "frame-src https://www.paypal.com https://www.patreon.com http://127.0.0.1:8790 http://localhost:8790; "
            "frame-ancestors 'self' http://127.0.0.1:8790 http://localhost:8790",
        )
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)
        try:
            self.wfile.flush()
        except Exception:
            pass

    def _json(self, code: int, obj: Any) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"))

    def _drain(self, declared: int) -> int:
        """Consume a body this handler is refusing, so the caller can finish writing it.

        Refusing without reading is what turned a 413 into a network error on the caller's side
        (defect 27). The policy and its two bounds live in `http_body`, which the public gateway
        shares, so the two refusing surfaces cannot drift apart.
        """
        return http_body.drain(self, declared)

    def _read_body(self, limit: int) -> bytes:
        """Read the body, refusing a malformed or oversized Content-Length.

        Four ways this went wrong: `Content-Length: -1` reached rfile.read(-1), which reads until
        the peer closes, so a loop of such requests wedged a handler thread each; a non-numeric
        length raised ValueError out of the handler as a 500; an over-limit body was emptied,
        which made the endpoint report 400 no_input for a payload that was merely large; and an
        over-limit body was refused *unread*, so the caller's remaining write was reset instead of
        being answered (defect 27).
        """
        raw_len = (self.headers.get("Content-Length") or "0").strip()
        try:
            n = int(raw_len)
        except ValueError:
            raise _BadRequest(f"Content-Length: {raw_len[:32]!r}") from None
        if n < 0:
            raise _BadRequest(f"Content-Length: {n}")
        if n > limit:
            self._drain(n)
            raise _BodyTooLarge(f"{n} > {limit}")
        return self.rfile.read(n) if n else b""

    def _contain(self, exc: BaseException) -> None:
        if isinstance(exc, _CLIENT_GONE):
            # Nobody is listening, so there is nothing to answer - and a 500 would blame the
            # server for the browser's exit. One line, no traceback.
            try:
                sys.stderr.write("[client-gone] %s %s: %s\n" % (self.command, self.path, exc))
            except Exception:  # noqa: BLE001 - a note that fails must not hide the leave
                pass
            return

        """Answer 500 instead of dropping the connection when a handler raises.

        The cause also goes to stderr, which the console tees into save/logs/console-<date>.log
        (`_tee_logs`). It used to go nowhere: the access line said 500 and nothing anywhere said why,
        which is exactly how the health route's boot-window 500 (ledger row 40) stayed unnamed while
        being reproducible - it could only be reached by hammering /api/health through a boot, and each
        occurrence left no evidence behind it. Naming a failure must never add one, so this is the first
        thing the method does and the last thing it trusts.
        """
        try:
            where = "%s %s" % (self.command, self.path)
            print("[500] %s\n%s" % (
                where,
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            ), file=sys.stderr, flush=True)
        except Exception:  # noqa: BLE001 - a log that fails must not become the failure
            pass
        try:
            # Detail carries absolute paths and internals: right for the operator on this
            # machine, not for whoever else can reach a LAN-bound console.
            detail = f"{type(exc).__name__}: {exc}"[:300] if self._loopback() else type(exc).__name__
        except Exception:  # noqa: BLE001
            detail = "unprintable_error"
        try:
            self._json(500, {"error": "handler_failed", "detail": detail})
        except Exception:  # noqa: BLE001 - the socket may already be gone
            pass

    _KEYS_CACHE: dict = {"at": 0.0, "data": None}

    def api_keys_snapshot(self) -> dict:
        """Every configured API key pinged for real, cached 60 s. Read-only, no keys echoed."""
        import subprocess as _sp
        import time as _t

        now = _t.time()
        if self._KEYS_CACHE["data"] is not None and now - self._KEYS_CACHE["at"] < 60:
            return self._KEYS_CACHE["data"]
        out = {"at": _t.strftime("%Y-%m-%dT%H:%M:%S"), "providers": [], "error": ""}
        try:
            probe = r"<LYGO_DIR>\tools\key_probe.py"
            r = _sp.run([sys.executable, probe, "--json"], capture_output=True, text=True, timeout=45)
            out.update(json.loads(r.stdout or "{}"))
        except Exception as exc:  # noqa: BLE001 - a dead probe must not take the console down
            out["error"] = f"{type(exc).__name__}: {exc}"
        try:
            from paths import console_limits as _cl
            _ctx = (_cl() or {}).get("ctx_max")
        except Exception:
            _ctx = None
        out["engine"] = {"brain": STATE.get("brain"), "selected": STATE.get("selected"),
                         "ctx": _ctx}
        self._KEYS_CACHE.update({"at": now, "data": out})
        return out

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._dispatch_GET()
        except _BodyTooLarge as exc:
            # The refused body has already been consumed (see _drain) so the caller can finish its
            # write and read this answer; the connection closes after it.
            self.close_connection = True
            self._json(413, {"error": "too_large", "limit": str(exc)[:60]})
        except _BadRequest as exc:
            self.close_connection = True
            self._json(400, {"error": "bad_request", "detail": str(exc)[:60]})
        except Exception as exc:  # noqa: BLE001 - one bad request must not end the console
            self._contain(exc)

    def _dispatch_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path

        if path == "/api/doorbell":
            # Where the page's own "Boot server" button rings. This process is answering, so it can say
            # where the doorbell is and hand over the token the page will need the moment this process is
            # gone: tools/doorbell.py is the one thing that can still start the console when the console
            # is not there. Never invented - if the doorbell is not running, this says so plainly.
            bell = {"port": None, "url": "", "up": False, "token": "",
                    "launcher": "LYGO_LLM_CONSOLE.bat",
                    "why": "tools/doorbell.py has not run for this copy"}
            try:
                _tools = str(Path(__file__).resolve().parents[1] / "tools")
                if _tools not in sys.path:
                    sys.path.insert(0, _tools)
                import doorbell as _bell

                _dport = _bell.doorbell_port(_bell.ROOT)
                _dup = _bell.console_is_up(_dport, timeout=0.3)
                bell.update({"port": _dport, "url": f"http://127.0.0.1:{_dport}/", "up": _dup,
                             "token": _bell.token(_bell.ROOT, create=True),
                             "why": "" if _dup else
                             "the doorbell is not listening - run LYGO_LLM_CONSOLE.bat once to start it"})
            except Exception as _bell_exc:  # a console with no doorbell is still a console
                bell["why"] = f"{type(_bell_exc).__name__}: {_bell_exc}"[:160]
            _bbody = json.dumps(bell).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(_bbody)))
            self.end_headers()
            self.wfile.write(_bbody)
            return

        # One gate, not two: _auth() already exempts the shell, its assets, the health bar and the
        # world clock. This used to re-implement the same check with a slightly different list, so
        # the two could disagree about what was public. Behaviour is unchanged.
        if not self._auth():
            self._json(401, {"error": "unauthorized"})
            return
        # --- LYGO module host (Δ9Φ963-LYGO-MODULE-CORE-v1) ---------------------------------------
        # Modules own the routes they declare in their own module.json. The kernel routes below are
        # untouched: if the host answers the request this returns, if it does not, nothing changed.
        if path == "/api/engine-key":
            if not check(token_from_request(self._headers_map(), self._query()), TOKEN):
                self._json(403, {"error": "console_token_required"}); return
            self._json(200, engine_key.status()); return
        if module_host is not None and module_host.handle(self, "GET", path):
            return
        if path == "/" or path == "/index.html":
            html = (PORTAL / "index.html").read_text(encoding="utf-8")
            css = (PORTAL / "style.css").read_text(encoding="utf-8")
            js = (PORTAL / "app.js").read_text(encoding="utf-8")

            def _splice(src: str, pattern: str, block: str) -> str:
                m = re.search(pattern, src)
                if not m:
                    return src
                return src[: m.start()] + block + src[m.end() :]

            html = _splice(html, r'<link rel="stylesheet" href="/static/style\.css[^"]*">', "<style>\n" + css + "\n</style>")
            html = _splice(html, r'<script src="/static/app\.js[^"]*"></script>', "<script>\n" + js + "\n</script>")
            # The portal needs the token to talk to its own console, but this page is served
            # WITHOUT one. Hand the real token only to a loopback caller: a LAN client used to
            # fetch "/" and be handed the secret in the page source.
            html = html.replace("/*LYGO_TOKEN*/", json.dumps(TOKEN if self._loopback() else ""))
            _sys = __import__("surface").report(local_ready=(STATE.get("brain") == "ready"))
            html = html.replace("/*LYGO_SYSTEM*/", _sys["label"])
            extra: dict[str, str] = {}
            if AUTH_REQUIRED and check(token_from_request(self._headers_map(), self._query()), TOKEN):
                # The operator has already proven the token: keep it in a SameSite cookie so the
                # URL, the browser history and the printed launcher line need not carry it again.
                extra["Set-Cookie"] = f"lygo_token={TOKEN}; Path=/; SameSite=Strict; Max-Age=43200"
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", extra or None)
            return
        if path.startswith("/static/"):
            name = path[len("/static/") :].split("?")[0]
            if "/" in name or "\\" in name or not name:
                self._json(404, {"error": "missing"})
                return
            if name.lower().endswith((".html", ".htm")):
                # The only HTML this console serves is the one document assembled at "/" with the
                # token, system label and assets spliced in. Served raw, the template's
                # `window.LYGO_TOKEN = /*LYGO_TOKEN*/;` is a SyntaxError and the page dies silent.
                self._json(404, {"error": "use_shell_route"})
                return
            fp = PORTAL / name
            if not fp.is_file() or not str(fp.resolve()).startswith(str(PORTAL.resolve())):
                self._json(404, {"error": "missing"})
                return
            ctype = "text/plain"
            if name.endswith(".css"):
                ctype = "text/css"
            elif name.endswith(".js"):
                ctype = "application/javascript"
            self._send(200, fp.read_bytes(), ctype)
            return
        if path.startswith("/api/media/image/"):
            # Chat bubbles show generated PNGs. Only workspace/images, basename only.
            name = path[len("/api/media/image/") :]
            from media_tools import MEDIA_IMAGE_TYPES, workspace_image_path

            fp = workspace_image_path(name)
            if fp is None:
                self._json(404, {"error": "missing"})
                return
            ctype = MEDIA_IMAGE_TYPES.get(fp.suffix.lower()) or "application/octet-stream"
            self._send(200, fp.read_bytes(), ctype)
            return
        if path.startswith("/api/media/audio/"):
            # The song studio's players. Same rule as the pictures: workspace/songs, basename only.
            # A rendered song can take tens of minutes, so a browser reload must not be able to ask
            # this console to render anything - it only ever reads a file that is already on disk.
            # The name arrives percent-encoded (a browser encodes a space as %20), so it is decoded
            # here, at the HTTP layer that owns the encoding. Decoding first also keeps the traversal
            # refusal honest: "%2e%2e%2f" becomes "../" and is then refused by name, not joined.
            from urllib.parse import unquote

            name = unquote(path[len("/api/media/audio/") :])
            from music_tools import MEDIA_AUDIO_TYPES, workspace_song_path

            fp = workspace_song_path(name)
            if fp is None:
                self._json(404, {"error": "missing"})
                return
            ctype = MEDIA_AUDIO_TYPES.get(fp.suffix.lower()) or "application/octet-stream"
            self._send(200, fp.read_bytes(), ctype)
            return
        if path.startswith("/api/media/mesh/"):
            from urllib.parse import unquote

            from mesh_tools import workspace_mesh_path

            name = unquote(path[len("/api/media/mesh/") :])
            fp = workspace_mesh_path(name)
            if fp is None:
                self._json(404, {"error": "missing"})
                return
            ctype = "model/gltf-binary" if fp.suffix.lower() == ".glb" else "application/octet-stream"
            self._send(200, fp.read_bytes(), ctype)
            return
        if path == "/api/mesh":
            from mesh_tools import mesh_status

            self._json(200, mesh_status())
            return
        if path == "/api/world":
            from world_clock import pulse

            self._json(200, pulse())
            return
        if path == "/api/health":
            # The route the operator reads state from may degrade, never 500 - see health_payload, which
            # also carries the measurement (two boots logged a 500 here; the operator's console ended its
            # log on the same route).
            local_caller = self._loopback()
            try:
                auth_ok = bool(check(token_from_request(self._headers_map(), self._query()), TOKEN))
            except Exception:  # noqa: BLE001 - an odd token is no reason to lose the whole answer
                auth_ok = False
            try:
                answer = health_payload(local_caller=local_caller, authenticated=auth_ok)
            except Exception as exc:  # noqa: BLE001 - last resort: answer with what is certain
                answer = {
                    "ok": True,
                    "build": BUILD,
                    "degraded": [
                        {
                            "field": "health",
                            "error": (
                                f"{type(exc).__name__}: {exc}"[:200] if local_caller else type(exc).__name__
                            ),
                        }
                    ],
                }
            self._json(200, answer)
            return
        if path == "/api/cloud":
            if not self._auth():
                self._json(401, {"error": "unauthorized"})
                return
            self._json(200, __import__("cloud_api").public_status())
            return
        if path == "/api/keys":
            # Read-only provider liveness: status codes, latency, model counts. No token, no key
            # material - same trust level as /api/health.
            self._json(200, self.api_keys_snapshot())
            return
        if path == "/api/models" or path == "/v1/models":
            if not self._auth():
                self._json(401, {"error": "unauthorized"})
                return
            data = reg_load()
            # Hand back what each record IS on this machine, not just what it claims: reachable (the
            # files are here) and portable (they sit in storage this kit carries). Without this the
            # stick advertises models that live on the PC it was built on.
            from registry import reach as _reach

            data = {
                **data,
                "models": [{**m, "reach": _reach(m)} for m in data.get("models") or []],
            }
            if path.startswith("/v1"):
                self._json(
                    200,
                    {
                        "object": "list",
                        "data": [{"id": m.get("id"), "object": "model"} for m in data.get("models") or []],
                    },
                )
                return
            self._json(200, data)
            return
        if path == "/api/models/check":
            # The checker's own results with the rig report on top: what runs here, what is a rig limit
            # and why (src/model_check.py + src/model_route.py). Read-only, no query needed.
            if not self._auth():
                self._json(401, {"error": "unauthorized"})
                return
            import model_route

            self._json(200, model_route.rig_report(cloud=__import__("cloud_api").public_status()))
            return
        if path == "/api/models/route":
            # "Which model should take this turn?" - with no query it answers the general best route.
            if not self._auth():
                self._json(401, {"error": "unauthorized"})
                return
            import model_route

            cloud = __import__("cloud_api").public_status()
            self._json(200, {"best": model_route.best_for(None, cloud=cloud),
                             "routes": model_route.routes(cloud=cloud)})
            return
        if path == "/api/tools":
            self._json(200, {"tools": TOOLS_SCHEMA, "names": [t["function"]["name"] for t in TOOLS_SCHEMA]})
            return
        if path == "/api/workspace":
            from admin_map import read_roots
            from workspace_map import list_mounts

            qs = parse_qs(urlparse(self.path).query)
            target = (qs.get("path") or [""])[0].strip()
            root = Path(target) if target else WORKSPACE
            if target:
                try:
                    rp = root.resolve()
                    ok = any(str(rp).lower().startswith(str(r.resolve()).lower()) for r in read_roots())
                except OSError:
                    ok = False
                if not ok:
                    self._json(403, {"ok": False, "error": "denied"})
                    return
            ents = []
            total = 0
            if root.is_dir():
                kids = list(root.iterdir())
                total = len(kids)
                for child in kids[:120]:
                    ents.append({"name": child.name, "dir": child.is_dir(), "path": str(child)})
            mounts = list_mounts()
            # The 120-entry cap was silent: a large folder looked complete and an unreadable one
            # looked empty. Report the true count and whether it was cut.
            self._json(200, {"ok": True, "path": str(root), "entries": ents, "total": total, "truncated": total > len(ents), "limit": 120, "mounts": mounts.get("mounts"), "n_live": mounts.get("n_live")})
            return
        if path == "/api/memory":
            mem = WORKSPACE / "memory.jsonl"
            lines = []
            if mem.is_file():
                lines = mem.read_text(encoding="utf-8", errors="ignore").splitlines()[-40:]
            mp = memory_path()
            md = _read_text_locked(mp)
            self._json(200, {"ok": True, "notes": lines, "memory_md": md, "path": str(mp)})
            return
        if path == "/api/soul":
            p = soul_path()
            t = _read_text_locked(p)
            self._json(200, {"ok": True, "path": str(p), "text": t})
            return
        if path == "/api/identity":
            p = identity_path()
            t = _read_text_locked(p)
            self._json(200, {"ok": True, "path": str(p), "text": t})
            return
        if path == "/api/session":
            self._json(200, {"messages": load_session()})
            return
        if path == "/api/sessions":
            # The vault, read-only: ?stats=1 for the summary, ?sid=… to read one session back,
            # ?search=1&q=… to find one, otherwise the catalog.
            qs = parse_qs(urlparse(self.path).query)
            sid = (qs.get("sid") or [""])[0].strip()
            if sid:
                self._json(200, safe_open(sid, int((qs.get("chars") or ["24000"])[0] or 24000)))
                return
            if (qs.get("stats") or [""])[0].strip() in {"1", "true", "yes"}:
                self._json(200, safe_stats())
                return
            q = (qs.get("q") or [""])[0].strip()
            # `q` alone FILTERS the catalog (titles, ids, tags, notes) - that is what the panel's
            # Find box means. Reading inside the transcripts is the heavier search: ask for it.
            if q and (qs.get("search") or [""])[0].strip() in {"1", "true", "yes"}:
                self._json(200, safe_search(q, int((qs.get("k") or ["8"])[0] or 8)))
                return
            self._json(200, safe_catalog(
                int((qs.get("limit") or ["200"])[0] or 200),
                q,
                (qs.get("tag") or [""])[0].strip(),
                (qs.get("month") or [""])[0].strip(),
            ))
            return
        if path == "/api/compaction":
            # The live window report: what the engine will actually see this turn, how full it is,
            # and what has been folded/sealed behind it.
            self._json(200, safe_status(live_ctx()))
            return
        if path == "/api/archive":
            qs = parse_qs(urlparse(self.path).query)
            sid = (qs.get("sid") or [""])[0].strip()
            if sid:
                self._json(200, compaction.read_transcript(sid))
                return
            self._json(
                200,
                {
                    "ok": True,
                    "sessions": compaction.index_entries(limit=500),
                    "archive": str(compaction.ARCHIVE),
                    "index": str(compaction.INDEX),
                    "bytes": compaction._dir_bytes(compaction.ARCHIVE),
                },
            )
            return
        if path == "/api/skills":
            from skills_mod import clawhub_search, list_skills, skillhub_list

            qs = parse_qs(urlparse(self.path).query)
            src = (qs.get("src") or ["local"])[0].strip().lower()
            q = (qs.get("q") or [""])[0].strip()
            if src in {"hub", "skillhub", "full"}:
                ch = "full" if src == "full" else "all"
                self._json(200, skillhub_list(q, ch))
            elif q:
                self._json(200, clawhub_search(q))
            else:
                self._json(200, list_skills())
            return
        if path == "/api/notepad":
            from notepad import list_notes, read_note

            qs = parse_qs(urlparse(self.path).query)
            nid = (qs.get("id") or [""])[0].strip()
            if nid:
                self._json(200, read_note(nid))
            else:
                self._json(200, list_notes())
            return
        if path == "/api/receipts":
            if not check(token_from_request(self._headers_map(), self._query()), TOKEN):
                self._json(401, {"error": "unauthorized"})
                return
            from paths import RECEIPTS

            items = []
            if RECEIPTS.is_dir():
                for f in sorted(RECEIPTS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
                    try:
                        items.append(json.loads(f.read_text(encoding="utf-8")))
                    except Exception:
                        continue
            self._json(200, {"receipts": items})
            return
        self._json(404, {"error": "not_found"})

    def do_OPTIONS(self) -> None:  # noqa: N802
        """Answer a capability probe - until now OPTIONS fell through to the stdlib's 501 page.

        `BaseHTTPRequestHandler` has no `do_OPTIONS`, so every OPTIONS request was answered 501
        ("Unsupported method") with an HTML error page. Wrong twice: 501 means the server does not
        implement the method at all, and this console's API never answers HTML - a client parsing
        JSON got a page of markup. The answer carries no CORS headers, on purpose: the console is
        loopback-bound and token-gated, and an Access-Control-Allow-Origin here would let any page
        the operator visits read their local console. The public web edition keeps its own
        allowlist (see `public_gateway._cors_ok`).
        """
        path = urlparse(self.path).path
        served = (path in ("/", "/index.html") or path.startswith("/static/")
                  or path.startswith("/api/") or path.startswith("/v1/"))
        if not served:
            self._json(404, {"error": "not_found"})
            return
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._dispatch_POST()
        except _BodyTooLarge as exc:
            # The refused body has already been consumed (see _drain) so the caller can finish its
            # write and read this answer; the connection closes after it.
            self.close_connection = True
            self._json(413, {"error": "too_large", "limit": str(exc)[:60]})
        except _BadRequest as exc:
            self.close_connection = True
            self._json(400, {"error": "bad_request", "detail": str(exc)[:60]})
        except Exception as exc:  # noqa: BLE001 - one bad request must not end the console
            self._contain(exc)

    def _dispatch_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self._auth():
            self._json(401, {"error": "unauthorized"})
            return
        if path == "/api/engine-key":
            if not check(token_from_request(self._headers_map(), self._query()), TOKEN):
                self._json(403, {"error": "console_token_required"}); return
            try:
                body = self._read_body(64_000)
                obj = json.loads(body.decode("utf-8") or "{}") if body else {}
            except Exception:
                self._json(400, {"error": "bad_json"}); return
            action = str((obj or {}).get("action") or "create").lower()
            try:
                if action in ("create", "rotate"):
                    result = engine_key.create(str((obj or {}).get("label") or "LYGO Manager Console"))
                elif action == "revoke":
                    result = engine_key.revoke()
                else:
                    self._json(400, {"error": "action must be create, rotate, or revoke"}); return
                result["action"] = action
                self._json(200, result); return
            except Exception as exc:
                self._json(500, {"error": str(exc)[:300]}); return
        # --- LYGO module host (Δ9Φ963-LYGO-MODULE-CORE-v1) ---------------------------------------
        if module_host is not None and module_host.handle(self, "POST", path):
            return
        if path == "/api/scan":
            body = self._read_body(256_000)
            cfg = load_console()
            roots = default_scan_roots(cfg)
            obj: dict[str, Any] = {}
            if body:
                try:
                    parsed = json.loads(body.decode("utf-8"))
                    if isinstance(parsed, dict):
                        obj = parsed
                except json.JSONDecodeError:
                    obj = {}
            if obj.get("roots"):
                roots = [str(r) for r in obj["roots"]]
            # The wide walk over every fixed drive lives HERE, on a scan the operator asked for, and
            # deliberately not on the boot path: walking a whole disk at boot would only make a slow
            # boot slower, and boot only needs the roots the kit already knows about.
            if obj.get("all_drives") or obj.get("everywhere"):
                result = discover(
                    extra_roots=roots,
                    per_root_s=float(obj.get("per_root_s") or 20.0),
                )
            else:
                # Even the narrow scan carries a placement verdict, so the LLM panel can say which
                # models would actually run here.
                result = scan_roots(
                    roots,
                    annotate_fit=True,
                    vram_free_mib=model_fit.vram_free_mib(),
                    ram_total=model_fit.ram_total_mib(),
                )
            data = reg_upsert(result["models"])
            STATE["scan_n"] = len(data.get("models") or [])
            if data.get("selected"):
                STATE["selected"] = data.get("selected")
            self._json(200, {**result, "registry": data, "roots": result.get("roots") or roots})
            return
        if path == "/api/models/route":
            # "Which model should take THIS turn?" - the answer for a capability the operator names.
            if not self._auth():
                self._json(401, {"error": "unauthorized"})
                return
            body = self._read_body(16_000)
            try:
                q = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                q = {}
            import model_route

            cloud = __import__("cloud_api").public_status()
            cap = str((q or {}).get("cap") or "").strip() or None
            prefer = str((q or {}).get("prefer") or "").strip() or None
            self._json(200, {"cap": cap,
                             "best": model_route.best_for(cap, cloud=cloud, prefer=prefer),
                             "routes": model_route.routes(cap, cloud=cloud),
                             "report": model_route.rig_report(cloud=cloud)})
            return
        if path == "/api/select" or path == "/api/boot":
            body = self._read_body(16_000)
            try:
                obj = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                obj = {}
            mid = obj.get("id") or STATE.get("selected") or reg_load().get("selected")
            data = reg_load()
            data["selected"] = mid
            # An explicit switch is a human decision: pin it so the RAM-auto brain stops re-picking.
            data["selected_source"] = "manual" if obj.get("id") else data.get("selected_source", "auto")
            from registry import save as reg_save

            reg_save(data)
            STATE["selected"] = mid
            boot_async(mid)
            _sync_hermes_rails()
            # Fail SOFT in the answer, not only in the log. A model this host cannot hold is a RIG limit
            # with a way forward: the console stays up, the session survives, and the reply names what
            # else can take the turn (`agent_fault: false` is the line the operator reads).
            import model_route

            _cloud = __import__("cloud_api").public_status()
            _known = model_route.results().get(str(mid)) or {}
            _rig = None
            if _known and not str(_known.get("verdict") or "").startswith("runs"):
                _rig = model_route.soft_failure(str(mid), str(_known.get("why") or "this model did not run on this host"),
                                                cloud=_cloud)
            self._json(200, {"ok": True, "brain": STATE.get("brain"), "selected": mid,
                             "error": STATE.get("error"), "rig": _rig,
                             "route": model_route.best_for(None, cloud=_cloud, prefer=str(mid))})
            return
        if path == "/api/cloud":
            body = self._read_body(16_000)
            try:
                obj = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                obj = {}
            if isinstance(obj, dict) and (str(obj.get("action") or "") == "probe" or obj.get("probe")):
                # "is my API key alive?" - one call to the provider, recorded like any other attempt,
                # so a probe that fails and a turn that fails read the same on the panel.
                # Imported here, not by the name: `cloud_api` is a local of this method (a later import
                # in the same body binds it), so the first version raised UnboundLocalError and answered
                # 500 - caught by the traceback the 500 handler had just been taught to log.
                from cloud_api import probe as cloud_probe

                self._json(200, cloud_probe())
                return
            from cloud_api import save as cloud_save

            st_saved = cloud_save(obj if isinstance(obj, dict) else {})
            _sync_hermes_rails()
            self._json(200, st_saved)
            return
        if path == "/api/brain":
            # one clear switch: local (default, always complete) <-> api (cloud, engine as safety net)
            if not self._auth():
                self._json(401, {"error": "unauthorized"})
                return
            body = self._read_body(16_000)
            try:
                obj = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                obj = {}
            if not isinstance(obj, dict):
                obj = {}
            want = str(obj.get("mode") or "").strip().lower()
            if want not in ("local", "api"):
                self._json(400, {"error": "mode_must_be_local_or_api", "got": want})
                return
            import cloud_api

            if want == "api":
                patch: dict[str, Any] = {"enabled": True, "clear_error": True}
                for k in ("provider", "model", "url", "key"):
                    if obj.get(k):
                        patch[k] = obj[k]
                if isinstance(obj.get("keys"), dict):
                    # second (and further) provider keys: {"gemini": "AIza..."} wires the backup
                    patch["keys"] = obj["keys"]
                if isinstance(obj.get("fallbacks"), list):
                    patch["fallbacks"] = obj["fallbacks"]
                st = cloud_api.public_status()
                if not st.get("has_key") and not patch.get("key"):
                    self._json(
                        409,
                        {
                            "error": "no_api_key",
                            "hint": "paste the key in the API row then Save key (or send key with this call)",
                            "cloud": st,
                        },
                    )
                    return
                cloud_api.save(patch)
                # the API is an option — keep the local engine booted as the safety net
                if not MOCK_ONLY and STATE.get("brain") != "ready":
                    sel = STATE.get("selected") or reg_load().get("selected")
                    if sel:
                        boot_async(str(sel))
            else:
                cloud_api.save({"enabled": False})
            st = cloud_api.public_status()
            _sync_hermes_rails()
            self._json(
                200,
                {
                    "ok": True,
                    "mode": st.get("mode"),
                    "brain": STATE.get("brain"),
                    "selected": STATE.get("selected"),
                    "cloud": st,
                    "hermes": __import__("hermes_brain").status(),
                },
            )
            return
        if path == "/api/session":
            body = self._read_body(512_000)
            try:
                obj = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                obj = {}
            if obj.get("new"):
                new_session()
                # Where did the conversation that just ended go? Say it, instead of leaving the
                # operator to wonder whether pressing the button threw their chat away.
                filed = {}
                try:
                    filed = (compaction._load_state() or {}).get("last_vault") or {}
                except Exception:  # noqa: BLE001
                    filed = {}
                self._json(200, {"ok": True, "messages": [], "vault": filed})
                return
            msgs = obj.get("messages")
            if isinstance(msgs, list):
                save_session(msgs)
            self._json(200, {"ok": True, "messages": load_session()})
            return
        if path == "/api/sessions":
            # One route for the operator's Sessions panel and for the agent's own session limbs.
            body = self._read_body(512_000)
            try:
                obj = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                obj = {}
            action = str(obj.get("action") or "catalog").lower()
            if action == "catalog":
                self._json(200, safe_catalog(
                    int(obj.get("limit") or 200), str(obj.get("q") or ""),
                    str(obj.get("tag") or ""), str(obj.get("month") or "")))
                return
            if action == "stats":
                self._json(200, safe_stats())
                return
            if action == "open":
                self._json(200, safe_open(str(obj.get("sid") or ""), int(obj.get("chars") or 24000)))
                return
            if action == "vault":
                title = str(obj.get("title") or "").strip() or None
                sid = str(obj.get("sid") or "").strip() or None
                self._json(200, sessions.safe_vault(sid, title, "manual"))
                return
            if action in {"vault_live", "file"}:
                # File the conversation in progress without ending it - the manual save button.
                self._json(200, safe_vault_live("manual"))
                return
            if action == "adopt":
                self._json(200, sessions.safe_adopt(int(obj.get("limit") or 300)))
                return
            if action == "label":
                kw: dict = {}
                if obj.get("title") is not None:
                    kw["title"] = str(obj["title"])
                if obj.get("note") is not None:
                    kw["note"] = str(obj["note"])
                if obj.get("tags") is not None:
                    tags = obj["tags"]
                    kw["tags"] = (tags if isinstance(tags, list)
                                  else [p.strip() for p in str(tags).split(",") if p.strip()])
                if obj.get("pinned") is not None:
                    kw["pinned"] = bool(obj["pinned"])
                self._json(200, sessions.safe_label(str(obj.get("sid") or ""), **kw))
                return
            if action == "search":
                self._json(200, safe_search(str(obj.get("q") or ""), int(obj.get("k") or 8)))
                return
            if action == "resume":
                self._json(200, safe_resume(str(obj.get("sid") or ""), bool(obj.get("file_current", True))))
                return
            if action == "rebuild":
                self._json(200, sessions.safe_rebuild())
                return
            if action == "transcript":
                info = sessions.find_session(str(obj.get("sid") or ""))
                self._json(200, info or {"ok": False, "error": "not_found"})
                return
            self._json(400, {"ok": False, "error": "bad_action", "action": action,
                             "actions": ["catalog", "stats", "open", "vault", "vault_live", "adopt",
                                         "label", "search", "resume", "rebuild", "transcript"]})
            return
        if path == "/api/compaction":
            # One route, one switch: the portal's Save & Compact button, the auto-trigger, the
            # archive browser and the model's own recall limb all come through here.
            body = self._read_body(512_000)
            try:
                obj = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                obj = {}
            action = str(obj.get("action") or "status").lower()
            msgs = obj.get("messages") if isinstance(obj.get("messages"), list) else None
            if action == "status":
                self._json(200, safe_status(live_ctx(), msgs))
                return
            if action in {"save", "autosave"}:
                self._json(200, compaction.save_now("button" if action == "save" else "autosave"))
                return
            if action == "compact":
                out = compaction.compact(reason="manual", keep_turns=obj.get("keep_turns"))
                out["status"] = safe_status(live_ctx(), msgs)
                self._json(200, out)
                return
            if action in {"roll", "seal"}:
                out = compaction.seal(reason="manual")
                out["status"] = safe_status(live_ctx())
                self._json(200, out)
                return
            if action == "recall":
                q = str(obj.get("q") or obj.get("query") or "")
                if gate_prompt(q).get("verdict") == "QUARANTINE":
                    self._json(451, {"ok": False, "error": "quarantine"})
                    return
                out = compaction.recall(q, k=int(obj.get("k") or compaction.RECALL_K))
                out["text"] = compaction.recall_text(q, k=int(obj.get("k") or 3))
                self._json(200, out)
                return
            if action == "index":
                self._json(
                    200,
                    {
                        "ok": True,
                        "sessions": compaction.index_entries(limit=500),
                        "archive": str(compaction.ARCHIVE),
                        "bytes": compaction._dir_bytes(compaction.ARCHIVE),
                    },
                )
                return
            if action == "transcript":
                self._json(200, compaction.read_transcript(str(obj.get("sid") or "")))
                return
            if action == "bundle":
                self._json(200, compaction.bundle_archives(int(obj.get("older_than_days") or 30)))
                return
            self._json(400, {"ok": False, "error": "bad_action", "action": action,
                             "actions": ["status", "save", "compact", "roll", "recall", "index", "transcript", "bundle"]})
            return
        if path == "/api/skills":
            from skills_mod import add_root, clawhub_install, clawhub_inspect, clawhub_search, read_skill, set_enabled, skillhub_install, skillhub_list

            body = self._read_body(32_000)
            try:
                obj = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                obj = {}
            action = str(obj.get("action") or "").lower()
            slug = str(obj.get("slug") or obj.get("name") or "")
            if action == "enable":
                self._json(200, set_enabled(slug, True))
                return
            if action == "disable":
                self._json(200, set_enabled(slug, False))
                return
            if action == "read":
                self._json(200, read_skill(slug))
                return
            if action == "search":
                self._json(200, clawhub_search(str(obj.get("q") or "")))
                return
            if action in {"hub", "skillhub"}:
                self._json(200, skillhub_list(str(obj.get("q") or ""), str(obj.get("channel") or "all")))
                return
            if action == "inspect":
                self._json(200, clawhub_inspect(slug))
                return
            if action == "install":
                self._json(200, clawhub_install(slug))
                return
            if action in {"install_full", "skillhub_install"}:
                self._json(200, skillhub_install(slug, full=bool(obj.get("full") or action == "install_full")))
                return
            if action == "add_root":
                self._json(200, add_root(str(obj.get("path") or "")))
                return
            self._json(400, {"ok": False, "error": "bad_action"})
            return
        if path == "/api/soul":
            from continuity import soul_path

            body = self._read_body(90_000)
            try:
                obj = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                obj = {}
            text = str(obj.get("text") or "")
            if gate_all(text).get("verdict") == "QUARANTINE":
                self._json(451, {"ok": False, "error": "p0_blocked"})
                return
            p = soul_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(p, text)
            self._json(200, {"ok": True, "path": str(p), "bytes": len(text.encode("utf-8"))})
            return
        if path == "/api/identity":
            from continuity import identity_path

            body = self._read_body(90_000)
            try:
                obj = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                obj = {}
            text = str(obj.get("text") or "")
            if gate_all(text).get("verdict") == "QUARANTINE":
                self._json(451, {"ok": False, "error": "p0_blocked"})
                return
            p = identity_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(p, text)
            self._json(200, {"ok": True, "path": str(p), "bytes": len(text.encode("utf-8"))})
            return
        if path == "/api/memory":
            from continuity import memory_path

            body = self._read_body(90_000)
            try:
                obj = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                obj = {}
            text = str(obj.get("text") or "")
            if gate_all(text).get("verdict") == "QUARANTINE":
                self._json(451, {"ok": False, "error": "p0_blocked"})
                return
            p = memory_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(p, text)
            self._json(200, {"ok": True, "path": str(p), "bytes": len(text.encode("utf-8"))})
            return
        if path == "/api/workspace":
            from workspace_map import add_mount, list_mounts, remove_mount

            body = self._read_body(16_000)
            try:
                obj = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                obj = {}
            action = str(obj.get("action") or "").lower()
            if action == "add":
                self._json(
                    200,
                    add_mount(
                        str(obj.get("path") or ""),
                        read=bool(obj.get("read", True)),
                        write=bool(obj.get("write")),
                        search=bool(obj.get("search", True)),
                        label=str(obj.get("label") or ""),
                    ),
                )
                return
            if action == "remove":
                self._json(200, remove_mount(str(obj.get("path") or "")))
                return
            self._json(200, list_mounts())
            return
        if path == "/api/notepad":
            from notepad import delete_note, new_note, write_note

            body = self._read_body(300_000)
            try:
                obj = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                obj = {}
            action = str(obj.get("action") or "save").lower()
            if action == "delete":
                self._json(200, delete_note(str(obj.get("id") or "")))
                return
            if action == "new":
                self._json(200, new_note(str(obj.get("title") or "")))
                return
            if not (obj.get("id") or obj.get("text") or obj.get("content") or obj.get("title")):
                # A junk or empty body used to reach write_note(None, "", "") and mint a phantom
                # empty note, answered {"ok": true, ...} - a write the caller never asked for.
                self._json(400, {"error": "nothing_to_save",
                                 "hint": 'send {"id": "..."} or {"text": "..."}, or action:"new"'})
                return
            self._json(
                200,
                write_note(obj.get("id"), str(obj.get("title") or ""), str(obj.get("text") or obj.get("content") or "")),
            )
            return
        if path == "/api/mesh":
            body = self._read_body(64_000)
            try:
                obj = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                obj = {}
            action = str(obj.get("action") or "status").lower()
            if action == "status":
                from mesh_tools import mesh_status

                self._json(200, mesh_status())
                return
            if action == "generate":
                from mesh_tools import mesh_from_image

                self._json(200, mesh_from_image(
                    str(obj.get("path") or ""),
                    obj.get("resolution"),
                    obj.get("remove_bg", True),
                    obj.get("timeout"),
                    obj.get("foreground"),
                ))
                return
            if action == "decimate":
                from mesh_tools import mesh_decimate

                self._json(200, mesh_decimate(
                    str(obj.get("name") or ""),
                    obj.get("ratio"),
                    obj.get("timeout"),
                ))
                return
            self._json(400, {"ok": False, "error": "bad_action"})
            return
        if path == "/api/limb":
            body = self._read_body(64_000)
            try:
                obj = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                obj = {}
            from tools import dispatch as tool_dispatch

            name = str(obj.get("name") or "")
            args = obj.get("arguments") or obj.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            self._json(200, tool_dispatch(name, args))
            return
        if path == "/api/shutdown":
            stop_port(LLAMA_PORT)
            stop_port(EMBED_PORT)
            self._json(200, {"ok": True})
            threading.Thread(target=lambda: self.server.shutdown(), daemon=True).start()
            return
        if path == "/api/chat":
            self._api_chat()
            return
        if path == "/v1/chat/completions":
            self._v1_chat()
            return
        if path == "/v1/embeddings":
            self._json(501, {"error": "embed_runner_optional"})
            return
        if path == "/api/upload":
            self._api_upload()
            return
        self._json(404, {"error": "not_found"})

    def _api_upload(self) -> None:
        """Put an attached file where the agent's own limbs can read it.

        The portal cannot hand the engine an arbitrary path on the operator's disk, and the engine only
        ever sees the messages. So anything the browser cannot inline as text is written under
        workspace/uploads/ - a root read_file, list_dir and search_corpus already cover - and the chat
        message then carries that path with the instruction to open it. Refusing a file with no home is
        why a picked file used to go nowhere.
        """
        raw = self._read_body(32 * 1024 * 1024)
        if not raw:
            self._json(400, {"error": "empty", "detail": "no bytes in the body"})
            return
        from urllib.parse import unquote

        from paths import WORKSPACE

        name = unquote(str(self.headers.get("X-Lygo-Filename") or "attachment.bin"))
        name = name.replace("\\", "/").split("/")[-1].strip() or "attachment.bin"
        safe = "".join(ch for ch in name if ch.isalnum() or ch in "._- ()").strip() or "attachment.bin"
        dest_dir = WORKSPACE / "uploads"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / (time.strftime("%Y%m%d-%H%M%S") + "-" + safe)
        dest.write_bytes(raw)
        self._json(200, {
            "path": str(dest),
            "name": safe,
            "bytes": len(raw),
            "note": "saved in the workspace: the agent opens it with the read_file limb",
        })

    def _api_chat(self) -> None:
        # 12 MB, not 4: an attached photo arrives as base64 inside this JSON, and a phone photo encoded
        # is 1.3x its size - the old limit turned "attach a photo" into a silent 413 on the operator.
        # An over-limit body raises out of `_read_body` — 413 with the reason. The dead branch that
        # second-guessed it here was written when a refused body came back empty instead.
        raw = self._read_body(12 * 1024 * 1024)
        # The 120s socket cap is for a peer that connects and stalls. A chat turn
        # is allowed to run as long as Hermes runs, so the cap comes off once the
        # body has arrived.
        try:
            self.connection.settimeout(None)
        except OSError:
            pass
        try:
            obj = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "bad_json"})
            return
        # D13: `messages[]` is the contract; `prompt`/`message` are tolerated shorthands. A payload
        # that carries no user text at all is refused loudly (400 no_input) instead of being
        # answered with an empty turn — except image-only turns, which are legitimate.
        messages = normalise_messages(obj)
        if not messages and obj.get("messages"):
            self._json(400, {"error": "no_input", "hint": "messages[] entries must be objects with role/content"})
            return
        user = extract_user_text(messages)
        last_user = user_text_of(messages)
        if not (str(user).strip() or str(last_user).strip()) and not has_image(messages):
            self._json(400, {"error": "no_input", "hint": "send messages:[{\"role\":\"user\",\"content\":\"...\"}] (prompt or message also accepted)"})
            return
        # Two ceilings, because one number cannot serve both jobs (see p0_hook): the newest message is
        # judged as a payload, the whole conversation as a conversation. The old single 12,000-character
        # total refused every later turn of a long build session - measured on this box, a 30,222-char
        # conversation came back HTTP 451 while an 8,904-char one answered normally - which stopped the
        # work instead of protecting anything.
        gate = gate_prompt(str(last_user or user))
        if gate.get("verdict") != "QUARANTINE" and len(str(user or "")) > POLICY_MAX_CONVERSATION_CHARS:
            gate = {
                "verdict": "QUARANTINE",
                "reason": f"conversation_exceeds_{POLICY_MAX_CONVERSATION_CHARS}",
                "policy": "QUARANTINE",
                "physics": None,
                "signature": P0_SIGNATURE,
            }
        if gate.get("verdict") == "QUARANTINE":
            self._json(451, {"error": "quarantine", "gate": gate})
            return
        use_tools = bool(obj.get("tools", True))
        # A turn that asks for nothing is offered no limbs at all. Measured on this box 2026-09-21
        # (qwen2.5-coder:7b, this console's own local schema of 24 limbs): given the schema, a bare
        # "hi" came back as a world_pulse call on 3 of 3 turns and "heloo?" on 2 of 3 - the console
        # then ran the limb and answered the operator's greeting with a city-clock and weather report.
        # With the limbs withheld, 8 of 8 greetings were answered as greetings. The rule is strict and
        # lives in chat_loop.is_conversational: the WHOLE message has to be the greeting, so
        # "hi, what's the weather in Tokyo?" keeps every limb. The same flag decides the tail below -
        # withheld limbs alone still left the turn reciting the clock back (see the tail site).
        from chat_loop import is_conversational

        _conversational = is_conversational(last_user)
        if use_tools and _conversational:
            use_tools = False
        # A turn that asks for a SHAPE, not a task, is offered no limbs either - the same rule, one
        # step further. Measured on this box 2026-09-22 (qwen2.5-coder:7b, ngl 99, 16k window): asked
        # "Reply with exactly: CACHE TEST" three times, the engine generated **137 tokens at 47.7
        # tok/s** on each turn and the console displayed 10 characters - 2.87 s of a 3.2 s turn was
        # text nobody saw, and the engine's own counter agreed (llamacpp:tokens_predicted_total 412
        # for the whole engine life, three turns of 137). A request to repeat a phrase back is not a
        # request to run anything; the 24-limb schema and a 4096-token budget are what turned two
        # words into 137 tokens. See src/turnperf.py for the shape rules and their veto list.
        turn_shape = {}
        model = obj.get("model") or STATE.get("selected") or "lygo-local"
        # Reply length. The operator's config is the source of truth (console.json, then local.json
        # over it) - not this line and not the portal's own constant, which is how every long answer
        # came to be cut off at exactly 768 tokens while the config said 512 and this said 1024.
        # A request may ask for LESS than the configured cap; it may never raise it.
        from paths import console_limits

        _cap = int(console_limits().get("max_tokens") or 1024)
        max_tokens = max(64, min(int(obj.get("max_tokens") or _cap), _cap))
        # The asked SHAPE, read from the operator's own words, before anything is sent. Two effects,
        # both of them the operator's own request rather than an optimisation: a tighter reply cap
        # (never higher than the configured one), and - for an ask that is nothing but an echo - no
        # limb schema at all. The measurement behind this is in src/turnperf.py.
        turn_shape = turnperf.shape_budget(last_user, _cap, port=brain_port(), api_key=LLAMA_KEY)
        if turn_shape.get("cap"):
            _shaped = max(turnperf.SHAPE_FLOOR, min(int(turn_shape["cap"]), max_tokens))
            if _shaped < max_tokens:
                print("[turnperf] reply cap %d -> %d (%s)" % (max_tokens, _shaped, turn_shape.get("why")),
                      flush=True)
                max_tokens = _shaped
        if turn_shape.get("tools") is False and use_tools:
            use_tools = False
            print("[turnperf] shape-only ask (%s) - no limb schema for this turn"
                  % turn_shape.get("why"), flush=True)
        # The same reading of the operator's words, as a condition the streaming read can test after
        # every token: once the asked shape is complete, the rest of the generation is text nobody
        # asked for. None when the turn asks for no particular shape - then nothing here applies.
        turn_stop = turnperf.make_stopper(last_user, turn_shape)
        want_stream = bool(obj.get("stream", True))
        from cloud_api import chat as cloud_chat
        import brain_router
        import cloud_api

        # LOCAL is the default brain and always stays complete; the API is an option that hands
        # the turn back to the engine whenever it cannot answer (tokens, rate limit, outage).
        local_model = str(obj.get("model") or STATE.get("selected") or reg_load().get("selected") or "lygo-local")
        st_cloud = cloud_api.public_status()
        api_label = brain_router.label_of(st_cloud)
        want_api = obj.get("use_api")
        force_api = bool(obj.get("force_api"))
        has_key = bool(st_cloud.get("has_key"))
        if want_api is None:
            use_cloud = bool(st_cloud.get("enabled") and has_key)
        else:
            use_cloud = bool(want_api) and has_key
        handoff: dict[str, Any] | None = None
        if use_cloud and st_cloud.get("degraded") and not force_api and cloud_api.cooldown_active():
            # this API already handed off moments ago — answer locally, don't burn another call
            use_cloud = False
            handoff = brain_router.handoff_info(
                st_cloud.get("last_code") or 0, st_cloud.get("last_error") or "", api_label, skipped=True
            )
        if use_cloud:
            brain = "cloud"
            model = st_cloud.get("model") or model
        else:
            brain = maybe_spawn(local_model if reg_get(str(local_model)) else None)
        # Conversation compaction, before the prompt is built: stamp a checkpoint on its own cadence,
        # fold whatever has left the live window, and seal a session that has outgrown its caps. All
        # three are deterministic and bounded - a compaction must never be why a turn stalls.
        _ctx = live_ctx()
        pre_note = safe_pre_turn(messages, _ctx)
        _auto = (pre_note or {}).get("auto") if isinstance(pre_note, dict) else None
        if isinstance(_auto, dict) and _auto.get("compacted"):
            print("[compact] " + json.dumps({k: _auto.get(k) for k in
                  ("folded", "used", "compact_at", "pending_turns", "reason")}, default=str), flush=True)
        kept, dropped, trim_info = trim_messages(messages, _ctx)
        # Journal the operator's own turn BEFORE generating, so a crash or a stopped answer still
        # leaves the question in the record of truth.
        _last = messages[-1] if messages else None
        if isinstance(_last, dict) and _last.get("role") == "user":
            safe_record("user", _last.get("content"), {"turn": True})
        elif str(user).strip():
            safe_record("user", user, {"turn": True})
        # The forever history: the operator's own words are filed the moment they are sent, before any
        # answer exists. `sync` hands the text to a background writer and cannot raise, so a slow or
        # full disk costs this turn nothing - and it reads the whole conversation, so a message sent
        # while the console was down is filed by the next turn.
        try:
            from transcript_archive import sync as file_history

            file_history(messages, {"model": local_model, "brain": "cloud" if use_cloud else "local"})
        except Exception as _arch_exc:  # pragma: no cover - the turn must not care
            print(f"[archive] skipped: {_arch_exc!r}", flush=True)
        # Recall. `trim_messages` above reports what the window could not hold; that history is filed,
        # and the passages bearing on this message come back with the turn. Retrieval is local BM25 -
        # no model, no network - so it can only cost a few milliseconds.
        recall_note = ""
        try:
            # Autonomous, and bounded: asked for when the window shed history, and also when what is
            # being asked clearly matches something filed that this turn is not carrying. The recall
            # reserve in window_budget() is what keeps a recap from ever overflowing the engine.
            recall_note = compaction.recall_for(str(last_user or user or ""), messages=messages, ctx=_ctx)
            if recall_note:
                recall_note = ("This conversation is longer than the window can hold. The console filed the whole of it and recalled these passages for you:" + "\n\n" + recall_note)
        except Exception as _rag_exc:  # pragma: no cover
            print(f"[rag] recall skipped: {_rag_exc!r}", flush=True)
        msgs = [local_system_message("api" if use_cloud else "local")] + kept
        # The clock rides the NEWEST message, never the identity block. A turn is
        # [system][history][newest user]; llama-server reuses the longest common token prefix, so a
        # line that changes every minute decides how much of the turn is cached. Measured on this
        # box, one model, one flag set, two turns identical apart from the clock 61 s apart:
        #   clock in the system block -> turn 2 re-prefilled 7,974 tokens in 146.1 s (54 tok/s)
        #   clock behind the history  -> turn 2 prefilled        17 tokens in   2.5 s
        # The operator text was journaled above, so this changes only what the model reads.
        # A turn that asks nothing is NOT sent the clock, and IS told to answer as a person: measured on
        # this box 2026-09-21 (qwen2.5-coder:7b), a session holding a greeting the console had already
        # answered with the clock, 3 texts x 4 reps - shipped tail 7 of 12 answers were answers, with the
        # directive 12 of 12. "thanks" alone went from 0 of 4 to 4 of 4. Nothing here is stored or
        # journalled: the tail and the directive ride this request only.
        _tail = local_tail(with_clock=not _conversational)
        _ride = _tail
        if recall_note:
            # Ahead of the tail: the recalled history is context, the tail is the instruction to answer.
            _ride = recall_note + ("\n\n" + _ride if _ride else "")
        if _conversational:
            from chat_loop import CONVERSATIONAL_DIRECTIVE

            _ride = (_tail + "\n\n" + CONVERSATIONAL_DIRECTIVE) if _tail else CONVERSATIONAL_DIRECTIVE
        if _ride:
            if msgs and isinstance(msgs[-1], dict) and msgs[-1].get("role") == "user":
                _content = msgs[-1].get("content")
                if isinstance(_content, list):
                    # A turn with a picture attached arrives as a content LIST. `str()` of that list
                    # renders the parts as Python repr - the photo's whole base64 data URL, AS TEXT -
                    # so the image part was destroyed and a 200 KB JPEG became ~74,000 tokens of
                    # garbage. Measured 2026-09-21 on the operator's own console (gemma4-12b, projector
                    # loaded): the engine refused the turn with "request (133868 tokens) exceeds the
                    # available context size (32768 tokens)" and the operator saw an empty bubble.
                    # The tail is a PART; the picture stays a picture.
                    msgs[-1] = {**msgs[-1], "content": list(_content) + [{"type": "text", "text": _ride}]}
                else:
                    msgs[-1] = {**msgs[-1], "content": str(_content or "") + "\n\n" + _ride}
            else:
                msgs.append({"role": "user", "content": _ride})
        # The window gate: what the engine is about to be handed.
        # An image part used to be billed a flat 900 tokens (compaction.content_tokens), so a session
        # with pictures was sent anyway and the engine answered NOTHING - measured on this box
        # 2026-09-21, the failure the operator reported as "the engine sent no text for this turn":
        #     console: [turn] blank answer: ... cur_text=0 chars
        #     engine:  send_error: request (133868 tokens) exceeds the available context size (32768)
        # Every turn now passes this gate. The newest picture is shrunk until it fits, older pictures
        # are shed to `[image: ...]` text (their words are kept), and a turn that still cannot fit is
        # ANSWERED in words instead of being sent to an engine that will refuse it.
        def _start_sse() -> None:
            """Send the SSE headers exactly once.

            This dance used to live only in the streaming branch further down, so the over-window path
            wrote `data: {...}` as the FIRST bytes of the response - no status line, no headers - and a
            client that asked a question the window could not hold got `BadStatusLine` instead of the
            answer the console had already composed for it (measured 2026-09-22).
            """
            if getattr(self, "_sse_started", False):
                return
            self._sse_started = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()

        # Defined up here because the over-window path below emits through it: it used to sit after
        # the vision/over-window block, so a turn that exceeded the window - the one turn that has to
        # answer in words rather than reach the engine - died with
        # "UnboundLocalError: cannot access local variable 'emit_sse'" and reached the operator as
        # HTTP 500. Measured 2026-09-22: every turn 500'd while the session history was larger than
        # the engine window, which is the console's "not responding properly" in its worst shape.
        def emit_sse(event: dict[str, Any]) -> None:
            # Measured live: closing the tab mid-answer raised ConnectionAbortedError here
            # ([WinError 10053]) and the turn was logged as a 500. The client leaving is not a
            # server fault: note it once, then stop writing to a socket nobody is reading.
            if getattr(self, "_client_gone", False):
                return
            line = json.dumps(event) + "\n"
            try:
                self.wfile.write(b"data: " + line.encode("utf-8") + b"\n")
                self.wfile.flush()
            except _CLIENT_GONE as exc:
                self._client_gone = True
                try:
                    sys.stderr.write("[client-gone] %s %s: %s\n" % (self.command, self.path, exc))
                except Exception:  # noqa: BLE001
                    pass

        _vision_block = ""
        _vision_info: dict[str, Any] = {}
        try:
            import vision as _vision

            _vb = (trim_info.get("budget") or {}) if isinstance(trim_info, dict) else {}
            _plan = _vision.fit_turn(
                msgs[1:],
                window=int(_vb.get("ctx") or _ctx or 0) or 8192,
                system_tokens=int(_vb.get("system_reserve") or 0),
                reserve=int(_vb.get("answer_reserve") or 0) + int(_vb.get("safety_reserve") or 0),
            )
            if _plan["changed"]:
                msgs = [msgs[0]] + _plan["messages"]
            _vision_info = {"used": _plan["used_tokens"], "window": _plan["window"],
                            "shed": _plan["shed"], "shrunk": _plan["shrunk"],
                            "trimmed": _plan.get("trimmed") or 0, "over": _plan["over"]}
            if _plan["changed"] or _plan["over"]:
                print("[vision] " + json.dumps(_vision_info, default=str))
            if _plan["over"]:
                _vision_block = _plan["note"]
        except Exception as _exc:  # a gate may never become the failure it exists to prevent
            print("[vision] gate skipped: " + type(_exc).__name__ + ": " + str(_exc)[:200])
        if _vision_block:
            assistant = _vision_block
            try:
                _vrec = write_receipt(prompt=user, output=assistant, model=str(model), gate=gate,
                                      extra={"has_image": has_image(messages), "vision_refused": True})
                save_session(list(messages) + [{"role": "assistant", "content": assistant}])
                # The answer is filed only now, as a completed turn - never mid-stream, never twice:
                # the archive cursor decides what is new, not this call site.
                try:
                    from transcript_archive import sync as file_history
                
                    _answered = str(cloud_api.public_status().get("last_provider") or "")
                    file_history(list(messages) + [{"role": "assistant", "content": assistant}],
                                 {"model": model,
                                  "brain": "cloud" if use_cloud else "local",
                                  "provider": _answered if use_cloud else ""})
                except Exception as _arch_exc2:  # pragma: no cover
                    print(f"[archive] skipped: {_arch_exc2!r}", flush=True)

            except Exception:
                _vrec = {"id": "vision-refused"}
            safe_record("assistant", assistant, {"receipt": _vrec.get("id")})
            if want_stream:
                _start_sse()
                emit_sse({"type": "token", "delta": assistant, "verdict": gate.get("verdict")})
                emit_sse({"type": "done", "traces": [], "receipt": _vrec.get("id"),
                          "active": "local" if not use_cloud else "cloud", "brain": brain,
                          "vision": _vision_info})
            else:
                self._json(200, {"text": assistant, "gate": gate, "brain": brain,
                                 "active": "local" if not use_cloud else "cloud", "receipt": _vrec.get("id"),
                                 "traces": [], "vision": _vision_info})
            return

        # Hermes is the Manager brain for a local text turn. Image turns stay on the
        # vision path above. A missing launcher, or the test suite, falls through to
        # the console's own engine loop. The desktop Hermes profile is not used.
        try:
            import hermes_brain as _hermes_brain

            if _hermes_brain.wants_turn(has_image(messages), mock=bool(MOCK_ONLY)):
                def _hermes_wait() -> bool:
                    if want_stream:
                        _start_sse()
                        emit_sse({"type": "status", "brain": "hermes"})
                    return not getattr(self, "_client_gone", False)

                _api_route = cloud_api.hermes_route() if use_cloud else {}
                _h = _hermes_brain.answer_turn(
                    user_text=str(last_user or user or ""),
                    messages=messages,
                    model=str(local_model),
                    engine_port=brain_port(),
                    console_port=int(getattr(self.server, "server_port", 0) or 9641),
                    api_key=LLAMA_KEY,
                    on_wait=_hermes_wait,
                    api_route=_api_route or None,
                )
                if _h.get("handled"):
                    assistant = str(_h.get("text") or "")
                    brain = "hermes"
                    _h_model = str(_h.get("model") or local_model)
                    _h_via = str(_h.get("via") or "local")
                    try:
                        _hrec = write_receipt(
                            prompt=user, output=assistant, model=_h_model, gate=gate,
                            extra={"brain": "hermes", "via": _h_via, "has_image": False},
                        )
                        save_session(list(messages) + [{"role": "assistant", "content": assistant}])
                        try:
                            from transcript_archive import sync as file_history

                            file_history(
                                list(messages) + [{"role": "assistant", "content": assistant}],
                                {"model": _h_model, "brain": "hermes", "provider": _h_via},
                            )
                        except Exception as _arch_exc3:  # pragma: no cover
                            print(f"[archive] skipped: {_arch_exc3!r}", flush=True)
                    except Exception:
                        _hrec = {"id": "hermes"}
                    safe_record("assistant", assistant, {"receipt": _hrec.get("id"), "brain": "hermes"})
                    STATE["last_brain"] = "hermes"
                    if want_stream:
                        _start_sse()
                        emit_sse({"type": "brain", "active": _h_via, "brain": "hermes", "model": _h_model})
                        emit_sse({"type": "token", "delta": assistant, "verdict": gate.get("verdict")})
                        emit_sse({"type": "done", "traces": _h.get("traces") or [], "receipt": _hrec.get("id"),
                                  "active": _h_via, "brain": "hermes", "model": _h_model})
                    else:
                        self._json(200, {"text": assistant, "gate": gate, "brain": "hermes",
                                         "active": _h_via, "receipt": _hrec.get("id"),
                                         "traces": _h.get("traces") or [], "model": _h_model})
                    return
                print("[hermes] " + str(_h.get("reason") or "not handled") + "; local engine answers", flush=True)
        except Exception as _hexc:  # noqa: BLE001 - a brain hook must not take the console down
            print("[hermes] skipped: " + type(_hexc).__name__ + ": " + str(_hexc)[:200], flush=True)

        assistant = ""
        traces: list[Any] = []
        # Hoisted out of the `if use_tools:` block below: it used to be initialised there and read
        # unconditionally at the end of the turn, so `"tools": false` (the portal's unticked
        # "Agent limbs" box, and the documented test payload) raised UnboundLocalError and answered
        # HTTP 500 handler_failed on EVERY turn. Initialise every turn variable before any branch.
        honest_pending = ""
        # Set only when the turn was streamed, so the final frame never sends the text twice.
        streamed: str | None = None
        # Engine telemetry: llama.cpp reports its own tokens/s per call, so the console can show
        # the real generation speed of every turn instead of the operator having to benchmark it.
        # `turnperf.note_call` keeps the engine's numbers AND the text those tokens became, which is
        # what makes a generated-vs-shown gap countable at all. The log is per-turn, local to this
        # handler: module-level state would leak across turns and across request threads.
        timing_log: list[dict[str, Any]] = []
        turn_t0 = time.time()
        # Filled in by the streaming path when the asked shape was delivered early.
        stop_info: dict[str, Any] = {}

        def note_timings(parsed_obj: Any) -> None:
            """Keep the engine's own timing block. Never raises: telemetry must not break a turn."""
            turnperf.note_call(timing_log, parsed_obj)

        def perf_summary() -> dict[str, Any]:
            """What this turn's engine calls actually did - and how much of it the operator saw."""
            if not timing_log:
                return {}
            return turnperf.attribute(
                timing_log=timing_log,
                shown_text=assistant,
                wall_ms=(time.time() - turn_t0) * 1000.0,
                limbs=len(traces),
                shape=turn_shape,
                stopped=stop_info or None,
                port=brain_port(),
                api_key=LLAMA_KEY,
                default_cap=max_tokens,
            )
        if use_tools and last_user and HOST_PREFETCH:
            # host_prefetch pulls URLs out of the user's text and fetches them before the model
            # answers, so a pasted or forwarded link becomes untrusted content in the same context
            # as the shell and python limbs. On by default - it is how the small local models get
            # live context - but the operator can switch it off with LYGO_HOST_PREFETCH=0.
            pre = host_prefetch(last_user)
            if pre:
                traces.extend(pre)
                msgs.append({"role": "user", "content": prefetch_message(pre)})

        if want_stream:
            _start_sse()
            emit_sse(
                {
                    "type": "brain",
                    "active": "cloud" if use_cloud else "local",
                    "brain": brain,
                    "model": str(model),
                    "api": api_label,
                    "fallback": handoff,
                }
            )

        if brain != "ready" and brain != "cloud":
            # mock / missing
            assistant = (
                "LYGO LLM Console is up. Engine brain is "
                f"{brain}. P0 verdict {gate.get('verdict')}. "
                "Local: Scan drives, pick a model, Boot LLM (it stays the default). "
                "Or press API in the brain switch to run the chat on a cloud key. Same full limbs either way."
            )
            if handoff:
                assistant = (
                    brain_router.banner(handoff["why"], handoff.get("api") or "", "", skipped=bool(handoff.get("skipped")))
                    + "\n\n"
                    + assistant
                )
            if use_tools and "status" in user.lower():
                name, result = "kernel_status", __import__("tools").dispatch("kernel_status", {})
                traces.append({"name": name, "result": result})
            if want_stream:
                emit_sse({"type": "token", "delta": assistant, "verdict": gate.get("verdict")})
                emit_sse({"type": "done", "traces": traces})
                return
            rec = write_receipt(prompt=user, output=assistant, model=str(model), gate=gate, extra={"has_image": has_image(messages)})
            self._json(200, {"text": assistant, "gate": gate, "brain": brain, "receipt": rec["id"], "traces": traces})
            return

        from chat_loop import auto_limb, is_tool_call_echo, named_tool, sanitize_assistant, same_answer, tool_card, tool_prose
        from openai_proxy import llama_chat

        host_did_tools = bool(traces)
        if use_tools and host_did_tools:
            msgs.append(
                {
                    "role": "user",
                    "content": (
                        "HOST already ran the readout above. Write the operator-facing answer now, in your "
                        "own words. You may call at most ONE more limb if a specific path or URL is still "
                        "missing — otherwise answer."
                    ),
                }
            )
        payload = {
            "model": model,
            "messages": msgs,
            "max_tokens": max_tokens,
            "stream": False,
        }
        tool_schema = TOOLS_SCHEMA if use_cloud else core_schema()
        if use_tools:
            payload["tools"] = tool_schema
        if use_cloud:
            code, body, _ = cloud_chat(payload)
            if code >= 400 and payload.get("tools"):
                payload.pop("tools", None)
                code, body, _ = cloud_chat(payload)
            if code >= 400 and brain_router.should_fallback(code):
                # the API is not going to answer this turn — the local engine takes over
                try:
                    perr = json.loads(body.decode("utf-8"))
                    e0 = perr.get("error") if isinstance(perr, dict) else perr
                    msg0 = e0.get("message") if isinstance(e0, dict) else str(e0 or "")
                except Exception:
                    msg0 = body[:300].decode("utf-8", "replace")
                why = brain_router.reason(code, msg0)
                cloud_api.note_error(code, why)
                handoff = brain_router.handoff_info(code, msg0, api_label)
                STATE["fallback"] = handoff
                brain = maybe_spawn(local_model if reg_get(str(local_model)) else None)
                if brain == "ready":
                    tool_schema = core_schema()
                    payload.pop("tools", None)
                    # The engine must be asked with ITS model. Measured 2026-09-21: a fallback that
                    # carried the API's model was refused by our own engine too, so nothing answered
                    # and the operator was shown the "API handoff" notice instead of a reply.
                    payload = cloud_api.local_payload_from(payload, str(local_model))
                    with ENGINE_LOCK:
                        code, body, _ = llama_chat(api_key=LLAMA_KEY, payload=payload, port=brain_port())
                    model = local_model
                    use_cloud = False
                else:
                    brain = "cloud"
        else:
            # A plain local turn (no limb round, engine ready) streams: the operator watches the
            # answer being written instead of ~160 s of nothing and then the whole reply at once.
            # Any limb turn, or a stream that never produced a token, falls through to the
            # buffered call - so this can only make a turn faster, never break one.
            if want_stream and not use_tools and brain == "ready":
                streamed = _stream_local_turn(payload, emit_sse, stop_when=turn_stop,
                                              stop_info=stop_info, on_timings=note_timings)
            if streamed is None:
                with ENGINE_LOCK:
                    code, body, _ = llama_chat(api_key=LLAMA_KEY, payload=payload, port=brain_port())
            else:
                code = 200
                body = json.dumps({"choices": [{"message": {"content": streamed}}]}).encode("utf-8")
                # A streamed call's final chunk carries the timing block but no message text, so
                # attach what the stream delivered: without it a streamed turn has numbers with
                # nothing to attribute them to, and its surplus would read as unexplained.
                if timing_log and not timing_log[-1].get("_content"):
                    timing_log[-1]["_content"] = str(stop_info.get("seen_text") or streamed)
                    timing_log[-1].setdefault("source", "stream")
                if not timing_log:
                    # The read was stopped before the engine's final chunk arrived (that is the
                    # point of stopping), so this turn has no engine block. Record what is provable
                    # rather than nothing: the tokens that actually arrived - counted by the
                    # engine's own tokenizer - and the rate this console read them at. `source`
                    # says where the numbers came from, so nobody reads them as the engine's own.
                    _seen = str(stop_info.get("seen_text") or streamed)
                    _n = turnperf.count(_seen, port=brain_port(), api_key=LLAMA_KEY)[0]
                    _ms = stop_info.get("read_ms")
                    timing_log.append({
                        "prompt_n": 0,
                        "prompt_tok_s": None,
                        "gen_n": _n,
                        "gen_tok_s": round(1000.0 * _n / _ms, 1) if (_n and _ms) else None,
                        "source": "stream_text",
                        "_content": _seen,
                    })
        try:
            parsed = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            parsed = {}
        note_timings(parsed)
        if use_cloud and code >= 400:
            err = parsed.get("error") if isinstance(parsed, dict) else parsed
            msg_err = err.get("message") if isinstance(err, dict) else str(err or body[:400])
            assistant = "API error HTTP " + str(code) + ": " + str(msg_err)[:800]
            msg_obj = {}
        else:
            msg_obj = ((parsed.get("choices") or [{}])[0].get("message") or {})
            assistant = msg_obj.get("content") or ""
        ow = gate_output_window(assistant or "")
        if ow.get("verdict") == "QUARANTINE":
            assistant = "[output quarantined]"
        assistant = sanitize_assistant(assistant, traces) or assistant
        assistant, _write_receipt = console_completes_the_write(last_user, traces, assistant)
        if use_tools:
            # A host-prefetched turn still gets one extra step so the agent can follow up when the
            # readout did not answer the question; model-initiated turns get the full loop.
            follow = list(msgs)
            cur_msg = msg_obj
            cur_text = assistant
            for _step in range(1 if host_did_tools else 4):
                batch = run_tools_round(cur_text, cur_msg)
                if not batch:
                    break
                traces.extend(batch)
                follow.append({"role": "assistant", "content": cur_text, "tool_calls": cur_msg.get("tool_calls")})
                follow.append(
                    {
                        "role": "user",
                        "content": "Tool results (RESOURCE, not CANON):\n"
                        + json.dumps(batch, default=str)[:8000]
                        + "\n\nNow answer the operator's newest message in plain prose using these results."
                        " Do not emit another tool call, and do not repeat the call JSON.",
                    }
                )
                payload2 = {"model": model, "messages": follow, "max_tokens": max_tokens, "stream": False, "tools": tool_schema}
                if use_cloud:
                    _, body2, _ = cloud_chat(payload2)
                else:
                    # The round that answers the operator streams. This is the one they wait on with
                    # limbs ticked: the limb results are in `follow` and the model was just told to
                    # answer in prose rather than call another limb, so the text it writes here IS the
                    # answer. A round that still emits a tool call carries no content deltas, this
                    # returns nothing, and the buffered call below keeps the tool calls intact.
                    body2 = None
                    if want_stream and brain == "ready":
                        text2 = _stream_local_turn(payload2, emit_sse)
                        if text2 is not None:
                            streamed = text2
                            body2 = json.dumps({"choices": [{"message": {"content": text2}}]}).encode("utf-8")
                    if body2 is None:
                        with ENGINE_LOCK:
                            _, body2, _ = llama_chat(api_key=LLAMA_KEY, payload=payload2, port=brain_port())
                try:
                    p2 = json.loads(body2.decode("utf-8"))
                except json.JSONDecodeError:
                    break
                note_timings(p2)
                cur_msg = ((p2.get("choices") or [{}])[0].get("message") or {})
                cur_text = cur_msg.get("content") or ""
                assistant = cur_text or assistant
                if gate_output_window(assistant).get("verdict") == "QUARANTINE":
                    assistant = "[output quarantined]"
                    break
        if use_tools:
            # The operator's OWN newest text, not msgs[-1] — the host-readout instruction is
            # appended to msgs above, and taking that as the request silently disabled this whole
            # guarantee on every prefetched turn (list_dir, whoami, wayback, http_json, download_url).
            newest = str(user or "")
            want = named_tool(newest)
            if want and not any(t.get("name") == want for t in traces):
                # The operator asked for a limb BY NAME and the turn came back without calling it —
                # measured 2026-09-18, sometimes with an invented result ("agent_gauntlet.html
                # downloaded"). Ask once more with only that limb's card, run whatever it emits, and
                # if it still will not call, say so rather than let a guessed result stand.
                card = {"role": "user", "content": tool_card(want)}
                pf = {"model": model, "messages": list(msgs) + [card], "max_tokens": max_tokens, "stream": False, "tools": tool_schema}
                if use_cloud:
                    _, bodyf, _ = cloud_chat(pf)
                else:
                    with ENGINE_LOCK:
                        _, bodyf, _ = llama_chat(api_key=LLAMA_KEY, payload=pf, port=brain_port())
                try:
                    parsed_f = json.loads(bodyf.decode("utf-8"))
                except Exception:
                    parsed_f = {}
                note_timings(parsed_f)
                msgf = ((parsed_f.get("choices") or [{}])[0].get("message") or {})
                textf = str(msgf.get("content") or "")
                forced = run_tools_round(textf, msgf)
                got_named = [t for t in forced if t.get("name") == want]
                if got_named:
                    traces.extend(forced)
                    follow_f = list(msgs) + [
                        card,
                        {"role": "assistant", "content": textf, "tool_calls": msgf.get("tool_calls")},
                        {
                            "role": "user",
                            "content": "Tool results (RESOURCE, not CANON):\n"
                            + json.dumps(forced, default=str)[:8000]
                            + "\n\nNow answer the operator's newest message in plain prose using these results."
                            " Do not emit another tool call, and do not repeat the call JSON.",
                        },
                    ]
                    p_f = {"model": model, "messages": follow_f, "max_tokens": max_tokens, "stream": False}
                    if use_cloud:
                        _, body_f, _ = cloud_chat(p_f)
                    else:
                        with ENGINE_LOCK:
                            _, body_f, _ = llama_chat(api_key=LLAMA_KEY, payload=p_f, port=brain_port())
                    try:
                        parsed_ff = json.loads(body_f.decode("utf-8"))
                    except Exception:
                        parsed_ff = {}
                    note_timings(parsed_ff)
                    try:
                        t_f = str(((parsed_ff.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
                    except Exception:
                        t_f = ""
                    if t_f.strip():
                        assistant = t_f
                else:
                    auto = auto_limb(want, newest)
                    if auto is not None:
                        # The operator named the limb and the model would not call it. The HOST runs it
                        # (read-only limbs only — never a shell command, a python snippet or a file write)
                        # and hands back the readout, labelled as host-run.
                        res = __import__("tools").dispatch(want, auto)
                        tr = {"name": want, "arguments": auto, "result": res, "host": True}
                        traces.append(tr)
                        assistant = "Host ran the " + want + " limb you named: " + (tool_prose([tr]) or "no readout")
                    else:
                        honest_pending = want
        if use_tools and traces and not str(assistant or "").strip():
            # A limb ran and the turn still has no words for the operator. The engine can answer the
            # "now answer in plain prose, do not emit another tool call" instruction with a second tool
            # call, and the loop has no step left to narrate it - so the bubble arrived empty while the
            # console was holding the readout. Measured 2026-09-19 on the plainest possible ask ("what
            # time is it right now?"): finish=tool_calls, content '', call now({}), then a 25-token
            # follow-up call. Say what the limbs returned instead of saying nothing.
            own = [t for t in traces if not t.get("host")] or traces
            assistant = tool_prose(own) or ""
        if use_tools and traces and is_tool_call_echo(assistant, cur_msg):
            # The model emitted a call and then echoed it as its answer instead of narrating the
            # result: hand the operator the host readout rather than the raw call JSON.
            own = [t for t in traces if not t.get("host")] or traces
            echoed_prose = tool_prose(own)
            if echoed_prose:
                assistant = echoed_prose
        if use_tools and traces and re.search(
            r"\bunknown\b|\bcannot\b|\bcan't\b|^\s*(?:no result|nothing|n/a)\s*$", str(assistant or ""), re.I
        ) and len(str(assistant or "").strip()) < 40:
            # A limb DID run and the model still shrugged ("UNKNOWN"). Give the operator the readout
            # instead of a shrug — measured on skill_read, where the call and its text were fine.
            shrug_prose = tool_prose(traces)
            if shrug_prose:
                assistant = shrug_prose
        if not str(assistant or "").strip():
            # An empty bubble is the symptom that has cost the most time here, and the console keeps no
            # record of a turn's internals - only HTTP access lines - so every guess had to be inferred.
            # Leave a receipt: what came back, what ran, and what the model's message actually held.
            # Wrapped, and every name read through locals(): this runs on the failing path, which is
            # exactly where a name may be missing. A receipt may never become the failure itself - a
            # bare NameError here reached an operator as HTTP 500 handler_failed while printing nothing.
            try:
                print(
                    "[turn] blank answer: use_tools=%s host_did_tools=%s tools_ran=%s first_msg_keys=%s "
                    "cur_text=%d chars follow_msg_keys=%s"
                    % (
                        locals().get("use_tools"),
                        locals().get("host_did_tools"),
                        [t.get("name") for t in (locals().get("traces") or [])],
                        sorted(locals().get("msg_obj") or {}),
                        len(str(locals().get("cur_text") or "")),
                        sorted(locals().get("cur_msg") or {}),
                    ),
                    flush=True,
                )
            except Exception:  # noqa: BLE001 - logging may never take the console down
                pass
        assistant = sanitize_assistant(assistant, traces) or assistant
        prev_answer = ""
        for m in reversed(messages):
            if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"].strip():
                prev_answer = m["content"]
                break
        if prev_answer and assistant and same_answer(prev_answer, assistant):
            # The operator asked something new and got the old answer back — small models happily echo
            # whatever is in the session. One nudge; whatever comes back is kept.
            nudge = list(msgs) + [
                {"role": "assistant", "content": assistant},
                {
                    "role": "user",
                    "content": (
                        "That was your previous answer repeated. Answer only the newest operator message, in "
                        "different words, with different content. If nothing new applies, say what changed in "
                        "one line."
                    ),
                },
            ]
            p3 = {"model": model, "messages": nudge, "max_tokens": max_tokens, "stream": False}
            if use_cloud:
                _, body3, _ = cloud_chat(p3)
            else:
                with ENGINE_LOCK:
                    _, body3, _ = llama_chat(api_key=LLAMA_KEY, payload=p3, port=brain_port())
            try:
                p3j = json.loads(body3.decode("utf-8"))
            except json.JSONDecodeError:
                p3j = {}
            note_timings(p3j)
            try:
                fresh = (((p3j.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            except Exception:
                fresh = ""
            if fresh and not same_answer(prev_answer, fresh):
                assistant = sanitize_assistant(fresh, traces) or fresh
        if honest_pending:
            # Last word: a limb was named, never called, and is not one the host may run for the
            # operator — so say that plainly instead of letting a guessed answer stand.
            assistant = (
                "I was asked for the " + honest_pending + " limb and did not issue the call, so I have no "
                "result to report — nothing was invented to fill the gap. Ask again with \"use the "
                + honest_pending + " limb\", or tell me to answer without it."
            )
        active = "cloud" if use_cloud else "local"
        if handoff and assistant and active == "local" and not assistant.startswith("\u26a0"):
            assistant = brain_router.banner(handoff["why"], handoff.get("api") or "", str(model)) + "\n\n" + assistant
        if active == "local" and has_image(messages) and not __import__("registry").selected_vision():
            # Never silently ignore a picture: the operator attached it and is owed one honest sentence
            # instead of an answer that reads as though the photo had been considered.
            assistant = VISION_BLIND_NOTE.format(model=str(model)) + "\n\n" + assistant
        if not handoff:
            # a clean turn clears the last-handoff note on the health bar
            STATE["fallback"] = None
        STATE["last_brain"] = active
        rec = write_receipt(
            prompt=user,
            output=assistant,
            model=str(model),
            gate=gate,
            extra={"has_image": has_image(messages), "active_brain": active, "api_handoff": handoff},
        )
        try:
            save_session(list(messages) + [{"role": "assistant", "content": assistant}])
            # The answer is filed only now, as a completed turn - never mid-stream, never twice:
            # the archive cursor decides what is new, not this call site.
            try:
                from transcript_archive import sync as file_history
            
                _answered = str(cloud_api.public_status().get("last_provider") or "")
                file_history(list(messages) + [{"role": "assistant", "content": assistant}],
                             {"model": model, "brain": active, "provider": _answered if active == "cloud" else ""})
            except Exception as _arch_exc2:  # pragma: no cover
                print(f"[archive] skipped: {_arch_exc2!r}", flush=True)

        except Exception:
            pass
        # What the operator reads must be the answer, not our own machinery. Measured 2026-09-21:
        # asked to reply exactly "KEY CHECK", the local model answered the readout that rides the
        # newest message ("NOW UTC ... The world_pulse provides timestamps ...") and the operator was
        # handed the clock. The readout is stripped here - the one place every turn's text passes
        # through, both brains, stream and buffered alike - and a limb that failed never stands in for
        # an answer. Nothing else in the text is touched: a genuine answer about the time survives.
        try:
            from chat_loop import (strip_readout, is_limb_failure, limb_failure_name,
                                   surface_artifacts, verify_output_claims)

            _was = assistant
            # A produced picture the answer does not name is an answer that hides its own result.
            assistant = surface_artifacts(assistant, traces)
            assistant = strip_readout(assistant)
            # A claimed limb OUTPUT no limb produced is the same lie as a claimed file that does not
            # exist (measured: "here is the output of python_exec: 63" with only steward_map in the
            # traces). The answer is kept and the host check goes under it.
            _checked = verify_output_claims(assistant, traces)
            if _checked != assistant:
                assistant = _checked
                STATE["fabrications_caught"] = int(STATE.get("fabrications_caught") or 0) + 1
                print("[fabrication] a claimed limb output this turn could not support: %d caught so far"
                      % STATE["fabrications_caught"], flush=True)
            if is_limb_failure(assistant):
                assistant = ("The %s limb could not answer, so this reply does not use it."
                             % limb_failure_name(assistant))
            if assistant != _was:
                print("[answer] cleaned %d chars of readout out of the reply" % (len(_was) - len(assistant)),
                      flush=True)
            if not assistant.strip():
                assistant = ("I read the clock back to you instead of answering - ask again and I "
                             "will answer the question itself.")
        except Exception as _clean_exc:  # pragma: no cover - a turn must never fail on cosmetics
            print("[answer] cleanup skipped: %r" % (_clean_exc,), flush=True)
        # The record of truth: the engine's window may have dropped older turns, this journal has not.
        safe_record("assistant", assistant, {"receipt": rec.get("id")})
        # One performance record per turn, taken ONCE and taken LAST: after the sanitising, the
        # readout strip and the fabrication check have all had their word. Taken earlier it would
        # report text the operator never received as if it had been shown, which is the exact blind
        # spot this record exists to close (measured 2026-09-22: 137 tokens generated, 10 characters
        # shown, and nothing in the console could see the difference).
        _perf = perf_summary()
        if _perf:
            STATE["last_perf"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **_perf}
            _line = turnperf.report(_perf)
            if _line:
                print(_line, flush=True)
        _comp = safe_status(_ctx, list(messages) + [{"role": "assistant", "content": assistant}])
        if want_stream:
            if streamed is None:
                emit_sse({"type": "token", "delta": assistant, "verdict": gate.get("verdict")})
            elif assistant != streamed:
                # The text was already shown as it arrived. When the finished answer differs - a
                # sanitised tool-call echo, or the output-window quarantine - the gates are the
                # last word on what the operator keeps, and this frame is that word.
                emit_sse({"type": "replace", "replace": assistant, "verdict": gate.get("verdict")})
            emit_sse(
                {
                    "type": "done",
                    "traces": traces,
                    "receipt": rec["id"],
                    "active": active,
                    "brain": brain,
                    "fallback": handoff,
                    "perf": _perf,
                    "compaction": _comp,
                }
            )
            return
        self._json(
            200,
            {
                "text": assistant,
                "gate": gate,
                "brain": brain,
                "active": active,
                "fallback": handoff,
                "receipt": rec["id"],
                "traces": traces,
                "perf": _perf,
                "compaction": _comp,
                "history": {"kept": len(kept), "dropped": dropped, "of": len(messages),
                            "budget_tokens": trim_info["budget"]["history_tokens"],
                            "used_tokens": trim_info["used_tokens"], "pre": pre_note},
            },
        )

    def _v1_chat(self) -> None:
        raw = self._read_body(256_000)  # over the limit raises 413 with the reason; see _read_body
        try:
            obj = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "bad_json"})
            return
        messages = obj.get("messages") or []
        user = extract_user_text(messages)
        gate = gate_prompt(user)
        if gate.get("verdict") == "QUARANTINE":
            self._json(451, {"error": "quarantine", "gate": gate})
            return
        obj["max_tokens"] = int(obj.get("max_tokens") or 512)
        # do not execute Console tools; strip injection
        brain = maybe_spawn(obj.get("model") if reg_get(str(obj.get("model") or "")) else None)
        if brain != "ready":
            mock = {
                "id": "lygo-mock",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": f"brain={brain}; P0={gate.get('verdict')}"},
                        "finish_reason": "stop",
                    }
                ],
            }
            self._json(200, mock)
            return
        from openai_proxy import llama_chat

        with ENGINE_LOCK:
            code, body, ctype = llama_chat(api_key=LLAMA_KEY, payload={**obj, "stream": False}, port=brain_port())
        try:
            parsed = json.loads(body.decode("utf-8"))
            txt = (((parsed.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
            if gate_output_window(txt).get("verdict") == "QUARANTINE":
                parsed["choices"][0]["message"]["content"] = "[output quarantined]"
                body = json.dumps(parsed).encode("utf-8")
        except Exception:
            pass
        self._send(code, body, ctype or "application/json")


class _StickContainment(ThreadingHTTPServer):
    """Console server that contains a failure instead of dying on it.

    socketserver's default handle_error prints the traceback to stderr; on a stick console
    whose stream is cp437 that print can itself raise, which ends the whole console. Logging
    here can never raise, so one bad request costs one response, not the session.
    """

    def handle_error(self, ref: object, ca: object) -> None:  # noqa: N802
        try:
            import traceback  # noqa: PLC0415

            tb = "".join(traceback.format_exception(*_sys.exc_info()))[-4000:]
            _sys.stderr.write(f"[stick] request from {ca} failed:\n{tb}\n")
            _sys.stderr.flush()
        except Exception:  # noqa: BLE001 - logging must never raise
            pass


class _Tee:
    """Write to the window AND to a file, so a console that dies leaves a reason behind.

    This console has exited on its own twice with nothing on disk to explain it: stdout lived only in
    the launcher window, and when the window closed the reason closed with it. Everything printed now
    also lands in save/logs/console-<date>.log. A write failure here is never allowed to break a turn.
    """

    def __init__(self, stream, handle) -> None:
        self._stream = stream
        self._handle = handle

    def write(self, data: str) -> int:
        # The file side is flushed on every write on purpose: this log exists to explain a death, and a
        # buffered handle loses exactly the last lines that would - which is what happened when the
        # primer's own "prefix primed" line sat in the buffer while the engine log showed it had run.
        for target in (self._stream, self._handle):
            try:
                target.write(data)
                target.flush()
            except Exception:  # noqa: BLE001 - logging may never take the console down
                pass
        return len(data)

    def flush(self) -> None:
        for target in (self._stream, self._handle):
            try:
                target.flush()
            except Exception:  # noqa: BLE001
                pass

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def install_console_log() -> str:
    """Tee stdout/stderr into save/logs/console-<date>.log. Returns the path, or '' if it could not."""
    try:
        from paths import LOGS

        LOGS.mkdir(parents=True, exist_ok=True)
        path = LOGS / f"console-{time.strftime('%Y%m%d')}.log"
        handle = open(path, "a", encoding="utf-8", errors="replace")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        handle.write(f"\n===== console start {stamp} pid={os.getpid()} =====\n")
        handle.flush()
        sys.stdout = _Tee(sys.stdout, handle)
        sys.stderr = _Tee(sys.stderr, handle)
        return str(path)
    except Exception:  # noqa: BLE001 - a read-only stick still has to run
        return ""


def _vault_catchup() -> None:
    """File any session the vault has not seen yet, on the way up.

    The vault only filed on an explicit action - New session, or a button in its pane - so a session
    that ended when the window closed, or a whole day of them, sat unfiled until someone pressed
    something. That is the "it should update per session" this answers: a cheap, deterministic
    catch-up at boot, in its own thread, printing one line about what it did.
    """
    try:
        import sessions as _sess

        out = _sess.safe_adopt()
        filed = int(out.get("count") or 0)
        tests = int(out.get("test_only_count") or 0)
        print(f"[vault] catch-up: filed {filed} session(s)"
              + (f", skipped {tests} test-only journal(s)" if tests else ""), file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - a catch-up may never stop the console coming up
        print(f"[vault] catch-up skipped: {type(e).__name__}: {e}", file=sys.stderr)


def main() -> int:
    _log = install_console_log()
    global TOKEN, LLAMA_KEY, BIND, AUTH_REQUIRED, MOCK_ONLY
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="serve")
    # None means "whoever knows": console.json may declare the port for this copy of the kit.
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--lan", action="store_true")
    ap.add_argument("--i-consent", action="store_true", dest="i_consent")
    ap.add_argument("--gguf", default="")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--mock", action="store_true")
    args = ap.parse_args()
    ensure_dirs()
    # The Manager brain is an isolated Hermes home. Files only: no process, no desktop profile.
    try:
        import hermes_brain as _hermes_boot

        _hs = _hermes_boot.prepare()
        print("[hermes] " + str(_hs.get("line") or "home ready"), file=sys.stderr)
        _sync_hermes_rails()
    except Exception as _hs_e:  # noqa: BLE001 - the console still boots if the home cannot be written
        print(f"[hermes] not prepared: {_hs_e}", file=sys.stderr)
    # The page's own "Boot server" button rings tools/doorbell.py, and that has to already be running when
    # this process is gone - which is exactly when it is needed. So the console makes sure one exists on its
    # way up: best effort, never allowed to stop the console coming up, and LYGO_NO_DOORBELL=1 turns it off.
    if not os.environ.get("LYGO_NO_DOORBELL"):
        try:
            _tools = str(Path(__file__).resolve().parents[1] / "tools")
            if _tools not in sys.path:
                sys.path.insert(0, _tools)
            import doorbell as _bell

            _made = _bell.ensure_running(_bell.ROOT)
            print(f"[doorbell] {'already up' if _made.get('already') else 'started'} "
                  f"on port {_made.get('port')}", file=sys.stderr)
        except Exception as _bell_e:  # noqa: BLE001 - the button is a convenience; the console is the job
            print(f"[doorbell] not started: {_bell_e}", file=sys.stderr)
    # Retention is a boot-time concern: receipts/events/todo stores grow forever and every
    # turn used to pay a re-read of the whole history. Bounded, and it must never stop the
    # console from coming up, so a failure here is reported and swallowed.
    try:
        _ret = prune_at_startup()
        print(f"[stick] retention: receipts={_ret.get('receipts')} "
              f"events={_ret.get('events')} todos={_ret.get('todos')}", file=sys.stderr)
    except Exception as _ret_e:  # noqa: BLE001 - retention must not block boot
        print(f"[stick] retention prune skipped: {_ret_e}", file=sys.stderr)
    # The session vault catches up with anything it has not filed yet, in a thread: a big store takes
    # seconds to zip, and the console must not wait for it to start serving.
    threading.Thread(target=_vault_catchup, daemon=True, name="lygo-vault-catchup").start()
    try:
        from install import ensure_layout, seed_identity

        ensure_layout()
        seed_identity()
    except Exception:
        pass
    ensure_identity()
    TOKEN = ensure_token()
    LLAMA_KEY = ensure_llama_key()
    MOCK_ONLY = bool(args.mock)
    if args.lan:
        if not args.i_consent:
            print("LAN bind requires --i-consent", file=sys.stderr)
            return 2
        BIND = "0.0.0.0"
        AUTH_REQUIRED = True
    cfg = load_console()
    # console.json used to be dead config for the two keys that matter most for running a second
    # copy of this kit (its port pair and its bind). Honour them - and refuse a config that tries
    # to expose the console on the network without the explicit --lan --i-consent handshake.
    if args.port is None:
        try:
            args.port = int(cfg.get("port") or DEFAULT_PORT)
        except (TypeError, ValueError):
            print(f"console.json port is not a number, using {DEFAULT_PORT}", file=sys.stderr)
            args.port = DEFAULT_PORT
    if not args.lan:
        want_bind = str(cfg.get("bind") or "").strip()
        if want_bind and want_bind.lower() not in ("127.0.0.1", "localhost", "::1"):
            print(
                f"console.json asks to bind {want_bind}; refusing without --lan --i-consent",
                file=sys.stderr,
            )
            return 2
    if args.cmd != "serve":
        print("unknown cmd", args.cmd)
        return 2
    class _ConsoleServer(_StickContainment):
        # Windows maps SO_REUSEADDR (which http.server sets by default) to a *permissive* bind:
        # two consoles can share 9641 and the browser then talks to whichever one wins, while
        # their boots fight over 11441 — that is exactly "models vanished / boot hangs".
        # Exclusive-address makes the second start fail loudly instead of double-binding.
        allow_reuse_address = False

    def _port_owner_hint(port: int) -> str:
        try:
            out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=15).stdout or ""
        except Exception as e:  # pragma: no cover
            return f"unknown ({e})"
        rows = [ln.split() for ln in out.splitlines()]
        # netstat -ano columns: [Proto, Local, Foreign, State, PID]
        pids = sorted({r[4] for r in rows if len(r) >= 5 and f":{port}" in r[1] and r[3].upper() == "LISTENING"})
        names = []
        for pid in pids:
            nm = "?"
            try:
                t = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=15).stdout or ""
                if t.strip():
                    nm = t.split(",")[0].strip().strip('"')
            except Exception:
                pass
            names.append(f"PID {pid} ({nm})")
        return ", ".join(names) or "free"

    # The portal is spliced into one HTML document per request; a missing asset used to surface
    # as a 500 on every visit. Fail loudly here instead, before the port is taken.
    missing_assets = [n for n in ("index.html", "style.css", "app.js") if not (PORTAL / n).is_file()]
    if missing_assets:
        print(f"portal assets missing in {PORTAL}: {', '.join(missing_assets)}", file=sys.stderr)
        return 4
    # The port this console is actually bound to, published for anything that describes this box.
    # Measured 2026-09-21: the console ran on 9651, yet both agents told the operator 9641 -
    # config/console.json's default - because --port only ever reached the banner, never the facts
    # the agent reads. State the live port; the config value is a fallback, not the truth.
    os.environ["LYGO_CONSOLE_PORT"] = str(args.port)
    try:
        httpd = _ConsoleServer((BIND, args.port), Handler)
    except OSError as e:
        print("", file=sys.stderr)
        print(f"port {args.port} is already in use — {e}", file=sys.stderr)
        print("another LYGO console is still running (or its engine):", file=sys.stderr)
        for _p in (args.port, LLAMA_PORT, EMBED_PORT):
            print(f"  {_p} -> {_port_owner_hint(_p)}", file=sys.stderr)
        print("close that window, or run LYGO_LLM_CONSOLE_STOP.bat, then start again.", file=sys.stderr)
        return 3
    # The token matters only when auth is on; printing it otherwise puts a useless secret on screen.
    url = f"http://127.0.0.1:{args.port}/?v={BUILD}" + (f"&t={TOKEN}" if AUTH_REQUIRED else "")
    # The operator's branding: the logo and the live reading first, then the three plain lines that
    # follow (other surfaces and tests read those, so they are left exactly as they were).
    try:
        import banner as _banner

        _banner.print_banner(_banner.live_facts(url=url, kit=str(KIT_ROOT),
                                                console_port=args.port, engine_port=LLAMA_PORT))
        _banner.set_window_title("LYGO LLM CONSOLE  Δ9Φ963  port %s" % args.port)
    except Exception as exc:  # noqa: BLE001 - a banner must never be able to stop a boot
        print("[banner] degraded: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
    print(f"LYGO LLM Console {BUILD}  {url}")
    print(f"kit {KIT_ROOT}")
    if _log:
        print(f"console log {_log}")
    print(f"signature Δ9Φ963-LYGO-LLM-CONSOLE-v1  physics={PHYSICS_AVAILABLE}  bind={BIND}")

    def warmup() -> None:
      try:
        if args.gguf:
            p = Path(args.gguf)
            rec = {
                "id": p.stem,
                "path": str(p),
                "kind": "chat",
                "ctx": 4096,
                # Not an instruction: the launch plan decides the offload (see lygo_engine.boot).
                "n_gpu_layers": None,
                "runnable": p.is_file(),
                "source": "cli",
                "bytes": p.stat().st_size if p.is_file() else 0,
            }
            reg_upsert([rec], selected=p.stem)
            STATE["selected"] = p.stem
            STATE["scan_n"] = 1
        else:
            print("scanning models…")
            scanned = scan_roots(default_scan_roots(cfg))
            data = reg_upsert(scanned.get("models") or [])
            from registry import pick_default, prefer_by_ram, prefer_ids

            sel = data.get("selected")
            rec = next((m for m in (data.get("models") or []) if m.get("id") == sel), None)
            if not rec or not rec.get("runnable") or rec.get("kind") not in (None, "chat"):
                want_ram = prefer_by_ram()
                data["selected"] = pick_default(data.get("models") or [], prefer_ram=want_ram,
                                                 prefer=prefer_ids())
                data["selected_source"] = "ram" if want_ram else "auto"
                from registry import save as reg_save

                reg_save(data)
            STATE["selected"] = data.get("selected")
            STATE["scan_n"] = len(data.get("models") or [])
            print(f"scan models={STATE['scan_n']} truncated={scanned.get('scan_truncated')} selected={STATE.get('selected')}")
        if not MOCK_ONLY and STATE.get("selected"):
            print(f"booting {STATE.get('selected')} …")
            try:
                import banner as _banner

                _perf = STATE.get("perf") or {}
                _banner.print_engine_line(str(STATE.get("selected") or ""),
                                          backend=str(_perf.get("backend") or _perf.get("mode") or ""),
                                          ngl=_perf.get("ngl"), port=LLAMA_PORT)
            except Exception:  # noqa: BLE001 - branding must not be able to stop a boot
                pass
            boot_async(str(STATE.get("selected")))
      except Exception as e:
        STATE["brain"] = "error"
        STATE["error"] = f"warmup:{e}"
        print("warmup failed", e)

    def watchdog() -> None:
        from engine import runner_for

        while True:
            time.sleep(20)
            try:
                r = runner_for(LLAMA_PORT)
                if STATE.get("brain") == "ready" and r and r.proc.poll() is not None:
                    STATE["brain"] = "missing"
                    STATE["error"] = "llama_exited"
                    sel = STATE.get("selected")
                    if sel:
                        boot_async(str(sel))
            except Exception:
                pass

    threading.Thread(target=warmup, daemon=True, name="lygo-warmup").start()
    threading.Thread(target=watchdog, daemon=True, name="lygo-watchdog").start()
    if not args.no_browser:
        def open_when_ready() -> None:
            for _ in range(40):
                if STATE.get("scan_n"):
                    break
                time.sleep(0.25)
            time.sleep(0.3)
            try:
                # The doorbell starts this console because its page said health failed - the browser
                # is already open on that page, so a second tab is noise.
                if os.environ.get("LYGO_NO_BROWSER", "").strip().lower() not in ("1", "true", "yes"):
                    webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=open_when_ready, daemon=True, name="lygo-browser").start()
    try:
        from cloud_api import ensure_opencode_bridge

        ensure_opencode_bridge()
    except Exception as _bridge_exc:  # a missing bridge must not stop the console
        print("[bridge] not started:", type(_bridge_exc).__name__, _bridge_exc, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_port(LLAMA_PORT)
        stop_port(EMBED_PORT)
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
