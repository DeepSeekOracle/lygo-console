"""voice_harness.py - the console's side of the arm's-length bridge to VoiceStudio.

Δ9Φ963-LYGO-VOICE-HARNESS-v1

WHAT THIS IS
    The harness the console module needs to drive a *separate* voice application. VoiceStudio
    (github.com/debpalash/VoiceStudio) provides voice cloning, voice design, dubbing, dictation,
    transcription and audiobooks in 646 languages, fully locally, and it exposes all of it as a
    local OpenAI-compatible speech platform on http://127.0.0.1:3900/v1.

    This harness finds that application, says honestly whether it is installed and running, starts
    and stops ITS backend, and calls it over plain HTTP. Nothing else.

WHY IT IS A BRIDGE AND NOT AN EMBED (READ THIS BEFORE CHANGING ANYTHING)
    VoiceStudio is licensed **AGPL-3.0-only**. The LYGO console is licensed under the **LYGO
    Sovereign License v3.0**, which forbids relicensing ("not MIT, not Apache, not GPL") and forbids
    publishing a modified copy of the core. AGPL-3.0 section 5(c) does the opposite: it requires the
    entire combined work to be licensed AGPL-3.0 to anyone who receives a copy. Those two cannot be
    satisfied at once, so VoiceStudio source may NOT be merged into, vendored into, or linked into
    this kit. Doing so would make the console an unshippable derivative.

    What the two licenses DO allow, and what this file implements, is two separate programs talking
    over a documented local interface at arm's length:

        the operator installs VoiceStudio themselves, from the upstream repository, as their own
        AGPL copy (the vendor's own installer already provisions it into a user-owned runtime root);
        the console speaks to it over loopback HTTP and never contains a line of its code.

    Rules this file must keep, so the boundary stays real rather than rhetorical:
      1. NO VoiceStudio source, string constants or data files are copied into this kit. This module
         speaks HTTP and reads two JSON files the vendor's shell wrote.
      2. NOTHING in the VoiceStudio tree is ever written to. It is the operator's application.
      3. A service this harness did NOT start is REUSED, never killed. The vendor's own desktop shell
         may own that process, and killing it would take the operator's app down with it.
      4. The AGPL obligations belong to whoever distributes VoiceStudio. This kit distributes a
         pointer to the upstream repository and a bridge to a copy the operator installed.

WHAT IT ASKS OF THE OPERATOR'S INSTALL (all read-only)
    %APPDATA%\\VoiceStudio\\runtime-location.json   {"root": "<abs path>", "owned": true}
                                                    - written by the vendor's own shell; it says where
                                                      the runtime (and its .venv) actually lives, and it
                                                      is the only reason this harness can start the
                                                      backend at all
    <root>\\project\\.venv\\Scripts\\python.exe      - the runtime interpreter the vendor provisioned
    <root>\\project\\.runtime-ready                  - the vendor's own "dependencies are complete"
                                                      marker; its ABSENCE means "still installing",
                                                      which is a state, not a fault
    %LOCALAPPDATA%\\Programs\\VoiceStudio\\          - the installed shell, for the "is it installed"
                                                      answer. Only the path is read.

THE ONE THING THAT MAKES THIS POSSIBLE
    The vendor does not run its backend from inside its own install directory. It stages a copy of
    the backend plus its own .venv into a user-owned runtime root and runs uvicorn out of there. That
    copy is the operator's, and it is reachable without touching the application bundle at all.

THE ENV THE BACKEND NEEDS (lifted from the vendor's own shell, reproduced faithfully)
    OMNIVOICE_PORT = the service port      PYTHONUNBUFFERED=1   PYTHONUTF8=1
    OMNIVOICE_DESKTOP_CONTAINED=1          TORCHDYNAMO_DISABLE=1
    HF_HUB_DISABLE_SYMLINKS=1              HF_HUB_DISABLE_SYMLINKS_WARNING=1
    FOR_DISABLE_CONSOLE_CTRL_HANDLER=1     PYTHONHOME / PYTHONPATH deleted
    argv: <project>\\.venv\\Scripts\\python.exe -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port <port>
    cwd:  <project>
    Readiness is /api/health answering 200 - the same route the vendor's shell polls.

HONESTY RULES IT INHERITS FROM THE KIT
    * A check that could not run is a NAMED GAP, never a zero and never a green.
    * It never claims the service is up because a process exists; it probes.
    * It never claims a file was written without reading the bytes back.
    * It never reports the operator's machine as broken because a declared-optional path is absent.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SIGNATURE = "Δ9Φ963-LYGO-VOICE-HARNESS-v1"

#: The upstream project this harness bridges to. Published, never vendored.
UPSTREAM = "https://github.com/debpalash/VoiceStudio"
UPSTREAM_LICENSE = "AGPL-3.0-only"
BRIDGE_REASON = (
    "VoiceStudio is AGPL-3.0-only and this console is LYGO Sovereign License v3.0, which forbids "
    "relicensing to GPL and forbids publishing a modified core. The two cannot be combined into one "
    "shippable work, so the console bridges to a copy the operator installs instead of embedding it."
)

#: The vendor's own default. Using it means an already-running VoiceStudio is REUSED rather than
#: fought over, and it collides with nothing this kit owns (9641 console / 11441 engine / 9642 gateway).
DEFAULT_PORT = 3900

#: The vendor's own readiness poll cadence and startup budget, reproduced.
READY_POLL_S = 0.5
DEFAULT_BUDGET_S = 300
PROBE_TIMEOUT_S = 1.5

#: Live VoiceStudio 0.5.6 answers GET /health 200. An earlier asar dump listed /api/health; keep
#: that as a fallback so an older backend still counts as up.
HEALTH_PATHS = ("/health", "/api/health")

#: Longest clip this harness will inline as a data URL for the panel to play in place. Beyond this
#: the file is reported by absolute path and the panel says so, rather than shipping megabytes
#: through a JSON route.
INLINE_LIMIT_BYTES = 512 * 1024

FORMATS = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "flac": "audio/flac",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "pcm": "audio/pcm",
}


# --- where things are --------------------------------------------------------------------------


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name) or ""
    return Path(raw) if raw else None


def user_data_dir() -> Path | None:
    """The vendor shell's userData directory: where it records where it put the runtime."""
    base = _env_path("APPDATA")
    return (base / "VoiceStudio") if base else None


def install_root() -> Path | None:
    """The installed application bundle, per-user. Read for its path only; never written to."""
    base = _env_path("LOCALAPPDATA")
    return (base / "Programs" / "VoiceStudio") if base else None


def install_state() -> dict[str, Any]:
    """Is VoiceStudio installed here, and what does the install actually contain?"""
    root = install_root()
    out: dict[str, Any] = {
        "root": str(root) if root else "",
        "present": False,
        "exe": "",
        "exe_bytes": 0,
        "backend_main": "",
        "resources_present": False,
    }
    if root is None:
        out["reason"] = "LOCALAPPDATA is not set, so the per-user install root cannot be located"
        return out
    exe = root / "VoiceStudio.exe"
    resources = root / "resources"
    main = resources / "backend" / "main.py"
    out["exe"] = str(exe)
    out["resources_present"] = resources.is_dir()
    out["backend_main"] = str(main) if main.is_file() else ""
    try:
        if exe.is_file():
            out["present"] = True
            out["exe_bytes"] = int(exe.stat().st_size)
    except OSError as exc:
        out["reason"] = f"the install could not be read: {exc}"
    if not out["present"] and "reason" not in out:
        out["reason"] = f"no VoiceStudio.exe under {root}"
    return out


def location_record() -> dict[str, Any]:
    """What the vendor's shell recorded about where it put the runtime.

    This is the single fact that makes starting the backend possible without guessing, so its
    absence is reported as what it is: the application has not completed a first run yet.
    """
    ud = user_data_dir()
    out: dict[str, Any] = {"file": "", "root": "", "owned": False, "read": False, "reason": ""}
    if ud is None:
        out["reason"] = "APPDATA is not set, so the runtime location file cannot be located"
        return out
    path = ud / "runtime-location.json"
    out["file"] = str(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        out["reason"] = (
            "the vendor shell has not recorded a runtime location yet - it writes this on its first "
            "run, so VoiceStudio has not been launched on this machine"
        )
        return out
    except OSError as exc:
        out["reason"] = f"the runtime location file could not be read: {exc}"
        return out
    try:
        data = json.loads(raw or "{}")
    except ValueError as exc:
        out["reason"] = f"the runtime location file is not valid JSON: {exc}"
        return out
    root = str(data.get("root") or "")
    if not root or not os.path.isabs(root):
        out["reason"] = f"the runtime location file names no absolute root (got {root!r})"
        return out
    out["root"] = root
    out["owned"] = data.get("owned") is True
    out["read"] = True
    return out


def runtime_state() -> dict[str, Any]:
    """The staged runtime: the project directory, its interpreter, and the vendor's ready marker."""
    loc = location_record()
    out: dict[str, Any] = {
        "location": loc,
        "root": loc["root"],
        "project": "",
        "python": "",
        "python_present": False,
        "installing": False,
        "ready_marker": False,
        "deps_ready": False,
        "reason": loc["reason"],
    }
    if not loc["read"]:
        return out
    project = Path(loc["root"]) / "project"
    python = project / ".venv" / "Scripts" / "python.exe"
    out["project"] = str(project)
    out["python"] = str(python)
    out["installing"] = (project / ".runtime-installing").is_file()
    out["ready_marker"] = (project / ".runtime-ready").is_file()
    try:
        out["python_present"] = python.is_file()
    except OSError as exc:
        out["reason"] = f"the runtime interpreter could not be read: {exc}"
        return out
    # The vendor's own rule: the interpreter plus pyvenv.cfg is a runtime that can run. The
    # .runtime-ready marker is the stronger claim (dependencies complete), and it is optional.
    out["deps_ready"] = out["python_present"] and (project / ".venv" / "pyvenv.cfg").is_file()
    if not out["deps_ready"] and not out["reason"]:
        if out["installing"]:
            out["reason"] = (
                "the runtime is still being provisioned by the vendor shell - a state, not a fault"
            )
        else:
            out["reason"] = (
                "no runtime interpreter under the recorded root - VoiceStudio must be launched once "
                "so it can stage its own environment"
            )
    return out


# --- config ------------------------------------------------------------------------------------


def _cfg() -> dict[str, Any]:
    """The kit's own config, so an operator can point the bridge somewhere and say so."""
    try:
        import paths

        cfg = paths.console_cfg() or {}
    except Exception:  # noqa: BLE001 - a config that will not load is not a crash here
        cfg = {}
    return cfg.get("voice") if isinstance(cfg.get("voice"), dict) else {}


def service_port() -> int:
    """The port the bridge talks to. Default 3900 - the vendor's own - read but never claimed blindly."""
    raw = _cfg().get("service_port") or os.environ.get("LYGO_VOICE_PORT") or DEFAULT_PORT
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def service_base() -> str:
    return f"http://127.0.0.1:{service_port()}"


def timeout_s() -> float:
    try:
        return float(_cfg().get("timeout_s") or 120)
    except (TypeError, ValueError):
        return 120.0


# --- HTTP, stdlib only -------------------------------------------------------------------------


def _http(
    method: str,
    path: str,
    *,
    data: bytes | None = None,
    ctype: str = "application/json",
    timeout: float | None = None,
) -> tuple[int, bytes, str]:
    """One request to the local service. Returns (status, body, error).

    A transport failure comes back as status 0 with a named error - never as an exception the card
    would have to guess about, and never as a success.
    """
    url = service_base() + path
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", ctype)
    # VoiceStudio ignores the key unless OMNIVOICE_API_KEY is set; send one so a configured service
    # works without a second code path.
    req.add_header("Authorization", "Bearer not-needed-locally")
    try:
        with urllib.request.urlopen(req, timeout=float(timeout or PROBE_TIMEOUT_S)) as resp:
            return int(resp.status), resp.read(), ""
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001
            body = b""
        return int(exc.code), body, ""
    except Exception as exc:  # noqa: BLE001 - a refused connection is an answer, not a crash
        return 0, b"", f"{type(exc).__name__}: {exc}"


def _json_body(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8", "replace") or "null")
    except ValueError:
        return None


# --- the service -------------------------------------------------------------------------------


def probe(timeout: float | None = None) -> dict[str, Any]:
    """Is the local speech service answering, and what does it say about itself?

    Up is only ever claimed from a 200 on /health or /api/health. Live 0.5.6 serves /health;
    /api/health is kept as a fallback for an older backend. Everything else on this card is
    enrichment that may be absent without the answer becoming a lie.
    """
    out: dict[str, Any] = {
        "port": service_port(),
        "base": service_base(),
        "up": False,
        "health": None,
        "discovery": None,
        "models": [],
        "voices": [],
        "version": "",
        "error": "",
        "checks_run": 0,
        "checks_failed": [],
        "health_path": "",
    }
    code, body, err, path_used = 0, b"", "", ""
    for path_used in HEALTH_PATHS:
        code, body, err = _http("GET", path_used, timeout=timeout)
        out["checks_run"] += 1
        if code == 200:
            break
    if code != 200:
        out["error"] = err or f"{path_used} answered {code}"
        out["checks_failed"].append("health")
        return out
    out["up"] = True
    out["health_path"] = path_used
    out["health"] = _json_body(body)

    code, body, err = _http("GET", "/.well-known/voicestudio-speech", timeout=timeout)
    out["checks_run"] += 1
    if code == 200:
        out["discovery"] = _json_body(body)
    else:
        out["checks_failed"].append("discovery")

    code, body, _err = _http("GET", "/v1/models", timeout=timeout)
    out["checks_run"] += 1
    if code == 200:
        data = _json_body(body) or {}
        items = data.get("data") if isinstance(data, dict) else None
        if isinstance(items, list):
            out["models"] = [str(m.get("id") or "") for m in items if isinstance(m, dict)]
    else:
        out["checks_failed"].append("models")

    out["version"] = _version_of(out)
    return out


def _version_of(probed: dict[str, Any]) -> str:
    """The service's own version, read from whichever of its answers carries one."""
    for key in ("health", "discovery"):
        blob = probed.get(key)
        if isinstance(blob, dict):
            for field in ("version", "app_version", "release"):
                if blob.get(field):
                    return str(blob[field])
    return ""


def voices() -> list[dict[str, Any]]:
    """The voices the service offers: engine presets and the operator's own saved clones."""
    code, body, _err = _http("GET", "/v1/audio/voices", timeout=timeout_s())
    if code != 200:
        return []
    data = _json_body(body) or {}
    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, str):
            out.append({"id": item})
    return out


def backend_argv(port: int | None = None) -> dict[str, Any]:
    """The exact command the vendor's own shell runs, rebuilt from the runtime it staged."""
    rt = runtime_state()
    if not rt["deps_ready"]:
        return {"ok": False, "error": "runtime_not_ready", "why": rt["reason"], "argv": [], "cwd": ""}
    python = Path(rt["python"])
    argv = [
        str(python),
        "-m",
        "uvicorn",
        "main:app",
        "--app-dir",
        "backend",
        "--host",
        "127.0.0.1",
        "--port",
        str(int(port or service_port())),
    ]
    return {"ok": True, "error": "", "why": "", "argv": argv, "cwd": rt["project"]}


def backend_env(port: int | None = None) -> dict[str, str]:
    """The environment the vendor's shell gives the backend, reproduced faithfully.

    PYTHONHOME and PYTHONPATH are deleted on purpose: an inherited one makes the staged venv resolve
    the wrong standard library, which reads as a broken install.
    """
    env = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH"):
        env.pop(key, None)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    env["OMNIVOICE_DESKTOP_CONTAINED"] = "1"
    env["OMNIVOICE_PORT"] = str(int(port or service_port()))
    uv = uv_path()
    if uv:
        env["OMNIVOICE_BUNDLED_UV"] = str(uv)
    if sys.platform == "win32":
        env["TORCHDYNAMO_DISABLE"] = "1"
        env["HF_HUB_DISABLE_SYMLINKS"] = "1"
        env["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        env["FOR_DISABLE_CONSOLE_CTRL_HANDLER"] = "1"
    # The chat model and VoiceStudio share one graphics card. Below this much free
    # memory the speech model cannot load there, so the service is pinned to the CPU.
    try:
        import perf

        free = int(perf.gpu_free_mib())
    except Exception:  # noqa: BLE001
        free = -1
    if 0 <= free < 2500:
        env["OMNIVOICE_DEVICE"] = "cpu"
    return env


def uv_path() -> Path | None:
    """The vendor's bundled uv, if it is there. Only ever passed through as an env var."""
    root = install_root()
    if root is None:
        return None
    cand = root / "resources" / "tools" / ("uv.exe" if sys.platform == "win32" else "uv")
    try:
        return cand if cand.is_file() else None
    except OSError:
        return None


def port_free(port: int | None = None) -> bool:
    """Is the service port free? A bind test, not a guess from a process list."""
    port = int(port or service_port())
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


# --- launching and stopping --------------------------------------------------------------------


# The process this console started. Kept alive so its stdin pipe stays open: VoiceStudio's
# contained mode exits the moment that pipe closes (it reads a closed pipe as "the desktop died").
_held_backend: subprocess.Popen[bytes] | None = None


def launch_record_path() -> Path:
    """Where the harness records the pid of the backend it started.

    Under the kit's save/ tree, so it is this kit's own state and never the vendor's.
    """
    try:
        import paths

        return Path(paths.SAVE) / "voice" / "backend.json"
    except Exception:  # noqa: BLE001
        return Path(__file__).resolve().parents[1] / "save" / "voice" / "backend.json"


def launch_record() -> dict[str, Any]:
    try:
        return json.loads(launch_record_path().read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _write_launch_record(data: dict[str, Any]) -> None:
    path = launch_record_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        import atomicio

        atomicio.atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
    except Exception:  # noqa: BLE001 - a record that will not write must not stop the launch
        pass


def _alive(pid: Any) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        proc = subprocess.Popen(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"] if sys.platform == "win32" else ["ps", "-p", str(pid)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        out = proc.communicate(timeout=10)[0] or ""
    except Exception:  # noqa: BLE001
        return False
    if sys.platform == "win32":
        return f" {pid} " in out
    return str(pid) in out


def launch(budget_s: int | None = None, wait: bool = True) -> dict[str, Any]:
    """Start VoiceStudio's backend, exactly the way its own shell starts it.

    NEVER starts a second one: if the service already answers, that service is reused and said to be
    reused. The vendor's shell or another operator session may own it, and a second uvicorn on the
    same port is refused by the backend itself (it exits 78).
    """
    port = service_port()
    already = probe(timeout=PROBE_TIMEOUT_S)
    if already["up"]:
        return {
            "ok": True,
            "started": False,
            "reused": True,
            "port": port,
            "reason": "the local speech service was already answering - reused, not started",
            "probe": already,
        }

    built = backend_argv(port)
    if not built["ok"]:
        return {
            "ok": False,
            "started": False,
            "reused": False,
            "port": port,
            "error": built["error"],
            "why": built["why"],
            "hint": "launch VoiceStudio once so it stages its runtime, then try again",
        }

    if not port_free(port):
        return {
            "ok": False,
            "started": False,
            "reused": False,
            "port": port,
            "error": "port_in_use",
            "why": f"port {port} is taken but /api/health did not answer - another program owns it",
        }

    log_dir = launch_record_path().parent
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    log = log_dir / f"backend-{port}.log"
    try:
        handle = open(log, "ab", buffering=0)
    except OSError:
        handle = subprocess.DEVNULL  # type: ignore[assignment]
    try:
        # stdin must be a pipe this process keeps open. DEVNULL is an immediate EOF, and
        # VoiceStudio's contained-mode watchdog treats that EOF as the desktop closing.
        proc = subprocess.Popen(
            built["argv"],
            cwd=built["cwd"],
            env=backend_env(port),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
        global _held_backend
        _held_backend = proc
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "started": False,
            "reused": False,
            "port": port,
            "error": "spawn_failed",
            "why": str(exc)[:300],
            "argv": built["argv"],
        }
    _write_launch_record({"pid": int(proc.pid), "port": port, "cwd": built["cwd"],
                          "log": str(log), "started": time.strftime("%Y-%m-%dT%H:%M:%S")})
    out: dict[str, Any] = {
        "ok": True,
        "started": True,
        "reused": False,
        "pid": int(proc.pid),
        "port": port,
        "log": str(log),
        "waiting_for": "/api/health",
    }
    if not wait:
        return out

    budget = float(budget_s if budget_s is not None else DEFAULT_BUDGET_S)
    deadline = time.time() + budget
    while time.time() < deadline:
        if proc.poll() is not None:
            out.update({
                "ok": False,
                "error": "exited_early",
                "why": f"the backend exited with code {proc.returncode} before it was ready",
                "tail": _tail(log),
            })
            _write_launch_record({"pid": 0, "port": port, "reason": "exited_early"})
            return out
        if probe(timeout=PROBE_TIMEOUT_S)["up"]:
            out["ready_s"] = round(budget - (deadline - time.time()), 1)
            out["probe"] = probe(timeout=PROBE_TIMEOUT_S)
            return out
        time.sleep(READY_POLL_S)
    out.update({
        "ok": False,
        "error": "startup_timeout",
        "why": f"no answer on /api/health within {int(budget)} s; the backend may still be loading models",
        "tail": _tail(log),
    })
    return out


def _tail(path: Path, lines: int = 12) -> str:
    """The last lines of a log, for a failure that has to name a cause."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def stop(force: bool = False) -> dict[str, Any]:
    """Stop the backend, but ONLY one this harness started.

    A service this kit did not start belongs to the operator's VoiceStudio window. Killing it would
    take their application down, so a pid this harness never recorded is refused by name.
    """
    rec = launch_record()
    pid = rec.get("pid")
    if not pid or not _alive(pid):
        return {
            "ok": False,
            "stopped": False,
            "error": "not_ours",
            "why": (
                "no live backend started by this console was recorded, so nothing was stopped - a "
                "service started by VoiceStudio's own window is the operator's, and this console "
                "will not kill it"
            ),
        }
    global _held_backend
    held = _held_backend
    _held_backend = None
    if held is not None and held.stdin is not None:
        try:
            held.stdin.close()
        except OSError:
            pass
    cmd = ["taskkill", "/PID", str(int(pid))] + (["/F"] if force else [])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stopped": False, "error": "kill_failed", "why": str(exc)[:300], "pid": int(pid)}
    time.sleep(1.0)
    gone = not _alive(pid)
    if gone:
        _write_launch_record({"pid": 0, "port": rec.get("port"), "stopped": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return {
        "ok": gone,
        "stopped": gone,
        "pid": int(pid),
        "rc": proc.returncode,
        "why": "the backend this console started is gone" if gone
               else "the process is still alive - a forced stop may be needed",
    }


# --- speaking and transcribing ------------------------------------------------------------------


def voice_dir() -> Path:
    """Where spoken clips land: this kit's own workspace tree, never the application's."""
    try:
        import paths

        return Path(paths.WORKSPACE) / "audio" / "voice"
    except Exception:  # noqa: BLE001
        return Path(__file__).resolve().parents[1] / "workspace" / "audio" / "voice"


def speak(
    text: str,
    *,
    voice: str = "",
    model: str = "",
    fmt: str = "mp3",
    speed: float | None = None,
    instructions: str = "",
) -> dict[str, Any]:
    """Ask VoiceStudio to say `text`, then read the bytes back off disk and prove what landed.

    The body is the OpenAI-compatible shape the project documents for `/v1/audio/speech`:
    `model` = an installed engine id, `voice` = a saved profile id or an engine preset, and
    `instructions` = the engine's style direction.
    """
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "empty_text", "why": "there is nothing to say"}

    fmt = str(fmt or "mp3").lower()
    if fmt not in FORMATS:
        return {"ok": False, "error": "unknown_format", "why": f"{fmt!r} is not one of {', '.join(FORMATS)}"}

    if not probe(timeout=PROBE_TIMEOUT_S)["up"]:
        return {
            "ok": False,
            "error": "service_down",
            "why": f"nothing is answering on {service_base()}/api/health - start the service first",
        }

    payload: dict[str, Any] = {"input": text, "response_format": fmt}
    if model:
        payload["model"] = str(model)
    if voice:
        payload["voice"] = str(voice)
    if speed is not None:
        try:
            payload["speed"] = float(speed)
        except (TypeError, ValueError):
            pass
    if instructions:
        payload["instructions"] = str(instructions)

    body = json.dumps(payload).encode("utf-8")
    code, raw, err = _http("POST", "/v1/audio/speech", data=body, timeout=timeout_s())
    if code != 200 or not raw:
        return {
            "ok": False,
            "error": "speech_refused" if code else "speech_unreachable",
            "http": code,
            "why": err or _error_message(raw) or f"/v1/audio/speech answered {code}",
            "sent": {k: v for k, v in payload.items() if k != "input"},
            "chars": len(text),
        }

    directory = voice_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        name = f"voice-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.{fmt}"
        target = directory / name
        target.write_bytes(raw)
    except OSError as exc:
        return {"ok": False, "error": "write_failed", "why": f"the clip could not be written: {exc}",
                "bytes_received": len(raw)}

    # Read the write back: the receipt is measured, never assumed.
    try:
        on_disk = target.read_bytes()
    except OSError as exc:
        return {"ok": False, "error": "readback_failed", "why": str(exc)[:200], "path": str(target)}

    digest = hashlib.sha256(on_disk).hexdigest()
    verified = bool(on_disk) and len(on_disk) == len(raw)
    out: dict[str, Any] = {
        "ok": verified,
        "path": str(target),
        "name": name,
        "bytes": len(on_disk),
        "sha256": digest,
        "verified": verified,
        "format": fmt,
        "content_type": FORMATS[fmt],
        "chars": len(text),
        "voice": str(voice),
        "model": str(model),
    }
    if not verified:
        out["error"] = "short_write"
        out["why"] = f"{len(on_disk)} bytes on disk, {len(raw)} received"
    return out


def inline_clip(result: dict[str, Any]) -> str:
    """A data URL for the panel, when the clip is small enough to be honest to inline."""
    size = int(result.get("bytes") or 0)
    if not result.get("ok") or size <= 0 or size > INLINE_LIMIT_BYTES:
        return ""
    try:
        import base64

        raw = Path(result["path"]).read_bytes()
    except OSError:
        return ""
    ctype = str(result.get("content_type") or "audio/mpeg")
    return f"data:{ctype};base64,{base64.b64encode(raw).decode('ascii')}"


def transcribe(path: str, *, language: str = "", model: str = "") -> dict[str, Any]:
    """Transcribe a file through VoiceStudio's OpenAI-compatible transcription route.

    Multipart is hand-built: this kit is stdlib-only and a dependency for one route is not a trade
    worth making.
    """
    src = Path(str(path or ""))
    try:
        if not src.is_file():
            return {"ok": False, "error": "no_such_file", "why": f"{src} is not a file"}
        raw = src.read_bytes()
    except OSError as exc:
        return {"ok": False, "error": "unreadable_file", "why": str(exc)[:200]}

    if not probe(timeout=PROBE_TIMEOUT_S)["up"]:
        return {"ok": False, "error": "service_down",
                "why": f"nothing is answering on {service_base()}/api/health"}

    boundary = f"----LYGOVoice{os.getpid()}{int(time.time())}"
    parts: list[bytes] = []
    for name, value in (("model", model or "whisper-1"), ("language", language), ("response_format", "json")):
        if not value:
            continue
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{src.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode("utf-8")
    )
    parts.append(raw)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)

    code, resp, err = _http(
        "POST", "/v1/audio/transcriptions", data=body,
        ctype=f"multipart/form-data; boundary={boundary}", timeout=timeout_s(),
    )
    if code != 200:
        return {"ok": False, "error": "transcription_refused" if code else "transcription_unreachable",
                "http": code, "why": err or _error_message(resp) or f"answered {code}", "file": str(src)}
    data = _json_body(resp)
    text = ""
    if isinstance(data, dict):
        text = str(data.get("text") or "")
    elif isinstance(data, str):
        text = data
    return {"ok": True, "text": text, "chars": len(text), "file": str(src),
            "bytes": len(raw), "language": language or "auto"}


def _error_message(raw: bytes) -> str:
    """OpenAI-shaped errors nest their message under `error`; VoiceStudio also sends `detail`."""
    data = _json_body(raw)
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str) and err:
            return err
        if data.get("detail"):
            return str(data["detail"])
    return ""


# --- one read for the card ---------------------------------------------------------------------


def state(deep: bool = False, with_voices: bool = False) -> dict[str, Any]:
    """Everything the cards need, in one read, with every gap named.

    `deep` adds the runtime and launch bookkeeping; `with_voices` asks the service for its voice
    list. Both are off by default because a panel polls this route on a timer and neither the
    interpreter nor the voice list changes second to second.
    """
    install = install_state()
    probed = probe()
    out: dict[str, Any] = {
        "signature": SIGNATURE,
        "read_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "upstream": UPSTREAM,
        "license": UPSTREAM_LICENSE,
        "bridge_reason": BRIDGE_REASON,
        "install": install,
        "service": probed,
        "port": service_port(),
        "gaps": [],
    }
    if not install["present"]:
        out["gaps"].append(
            "VoiceStudio is not installed under %LOCALAPPDATA%\\Programs\\VoiceStudio - the console "
            "cannot drive an application that is not here"
        )
    if probed["checks_failed"]:
        out["gaps"].append(
            "the service answered /api/health but not: " + ", ".join(probed["checks_failed"])
        )
    if not probed["up"]:
        out["gaps"].append(f"the local speech service is not answering on {service_base()}")
    if deep:
        runtime = runtime_state()
        out["runtime"] = runtime
        out["launch"] = launch_record()
        out["port_free"] = port_free()
        if not runtime["deps_ready"] and runtime["reason"]:
            out["gaps"].append(f"the staged runtime is not ready: {runtime['reason']}")
    if with_voices:
        out["voices"] = voices()
    return out
