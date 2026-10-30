"""film_tools — the one place in the kit that knows what FilmCraft is and where it is.

FilmCraft is an open-source (MIT OR Apache-2.0) Premiere-style editor written in Rust. What makes it
wireable is that every menu item, button, slider and drag in it is a COMMAND with an id, typed
parameters and an enabled state - 693 of them in the pinned 0.6.0 - and four surfaces dispatch the
same commands:

* ``filmcraft.exe``            the editor itself, started with ``--control <port>``
* ``filmcraft-cli.exe``        the headless engine: inspect / exec / run / export / render / probe
* the control channel          JSON lines on 127.0.0.1 - drives the RUNNING editor
* ``filmcraft-cli mcp``        the same commands as MCP tools on stdio

This module owns the paths, the one-shot calls, the process records and the agent-facing action
table. It NEVER raises into a caller: every call answers a dict, and a thing that did not happen says
so by name instead of being reported as done. Nothing here starts a process unless somebody asked for
it - a boot, a web serve or an export are all named actions, never side effects of a read.

Records live in ``save/film/`` (this module's own small state: what it started, what it wrote), and
the film workspace lives in ``workspace/film/`` (projects, media, exports) - both of which a kit copy
carries with it, because both are relative to the kit folder.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from atomicio import atomic_write_text, read_text
from paths import KIT_ROOT, SAVE, WORKSPACE, console_cfg

SIGNATURE = "\u03949\u03a6963-LYGO-FILM-v1"
APP_NAME = "filmcraft"
RELEASE_FLOOR = "0.6.0"          # what PIN.json pins; a different install is reported, not refused
FILM_SAVE = SAVE / "film"
FILM_WORKSPACE = WORKSPACE / "film"
_COMMANDS_CACHE: dict[str, Any] = {"at": 0.0, "version": "", "rows": None}
_NO_WINDOW = 0x08000000
_DETACHED = 0x00000008 | 0x00000200


# --------------------------------------------------------------------------------------------
# where things are
# --------------------------------------------------------------------------------------------


def _cfg() -> dict[str, Any]:
    try:
        return console_cfg() or {}
    except Exception:
        return {}


def _path_from(raw: Any) -> Path | None:
    """A configured path, expanded. Relative entries resolve INSIDE the kit folder."""
    txt = str(raw or "").strip()
    if not txt:
        return None
    try:
        p = Path(os.path.expandvars(os.path.expanduser(txt)))
    except Exception:
        return None
    return p if p.is_absolute() else (KIT_ROOT / p)


def install_home() -> Path:
    """The FilmCraft folder on this machine: the config's own value, else the kit's own copy."""
    return _path_from(_cfg().get("filmcraft_dir")) or (KIT_ROOT / "utilities" / "filmcraft")


def platform_dir() -> str:
    if sys.platform.startswith("win"):
        return "win-x64"
    if sys.platform == "darwin":
        return "macos-universal"
    if os.uname().machine in ("aarch64", "arm64") if hasattr(os, "uname") else False:
        return "linux-aarch64"
    return "linux-x86_64"


def _exe(name: str) -> str:
    return name + (".exe" if sys.platform.startswith("win") else "")


def app_path() -> Path:
    """The desktop editor. Config value wins; else <filmcraft_dir>/<platform>/filmcraft(.exe)."""
    return _path_from(_cfg().get("filmcraft_app")) or (install_home() / platform_dir() / _exe("filmcraft"))


def cli_path() -> Path:
    """The headless engine + control-channel client + MCP server."""
    return _path_from(_cfg().get("filmcraft_cli")) or (install_home() / platform_dir() / _exe("filmcraft-cli"))


def web_root() -> Path:
    """The offline WebAssembly editor: index.html + filmcraft_web_bg.wasm + its glue."""
    return install_home() / "web"


def control_port() -> int:
    try:
        return int(_cfg().get("filmcraft_control_port") or 9642)
    except (TypeError, ValueError):
        return 9642


def web_port() -> int:
    try:
        return int(_cfg().get("filmcraft_web_port") or 9643)
    except (TypeError, ValueError):
        return 9643


def cli_timeout() -> int:
    try:
        return max(30, int(_cfg().get("filmcraft_timeout_s") or 900))
    except (TypeError, ValueError):
        return 900


def ensure_workspace() -> dict[str, str]:
    """Create the film workspace inside the kit. A path the console itself creates is its own."""
    out: dict[str, str] = {}
    for name in ("projects", "media", "exports", "renders"):
        d = FILM_WORKSPACE / name
        try:
            d.mkdir(parents=True, exist_ok=True)
            out[name] = str(d)
        except OSError as exc:
            out[name] = f"refused: {exc}"
    try:
        FILM_SAVE.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return out


def _ensure_parent(dest: Path) -> bool:
    """The engine writes the file, not us, so its folder has to exist before the call."""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False



def _record_path(name: str) -> Path:
    return FILM_SAVE / f"{name}.json"


def _record_read(name: str) -> dict[str, Any]:
    try:
        raw = read_text(_record_path(name))
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _record_write(name: str, data: dict[str, Any]) -> None:
    try:
        FILM_SAVE.mkdir(parents=True, exist_ok=True)
        atomic_write_text(_record_path(name), json.dumps(data, indent=1, sort_keys=True))
    except Exception:
        pass


# --------------------------------------------------------------------------------------------
# small facts
# --------------------------------------------------------------------------------------------


def _at() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _now() -> float:
    return time.time()


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def alive(pid: int) -> bool:
    """Is that pid still there? Claimed by the OS, not by our own record."""
    if not pid:
        return False
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                                 capture_output=True, text=True, timeout=8,
                                 creationflags=_NO_WINDOW)
            return str(int(pid)) in (out.stdout or "")
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _kill_tree(pid: int) -> bool:
    if not pid:
        return False
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                           capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW)
        else:
            import signal

            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
    except Exception:
        return False
    for _ in range(20):
        if not alive(pid):
            return True
        time.sleep(0.25)
    return not alive(pid)


def _run(argv: list[str], cwd: Path | None = None, timeout: int = 120) -> dict[str, Any]:
    """One-shot child, captured. A timeout kills the whole tree - a half-killed engine that keeps
    writing after the console gave up is worse than a refusal."""
    proc = None
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=(_NO_WINDOW if sys.platform.startswith("win") else 0),
            start_new_session=False if sys.platform.startswith("win") else True,
        )
        try:
            out, err = proc.communicate(timeout=timeout)
            return {"ok": proc.returncode == 0, "code": proc.returncode,
                    "stdout": out or "", "stderr": err or "", "timed_out": False}
        except subprocess.TimeoutExpired:
            _kill_tree(proc.pid)
            try:
                out, err = proc.communicate(timeout=5)
            except Exception:
                out, err = "", ""
            return {"ok": False, "code": None, "stdout": out or "", "stderr": err or "",
                    "timed_out": True, "why": f"the call did not finish inside {timeout}s"}
    except FileNotFoundError as exc:
        return {"ok": False, "code": None, "stdout": "", "stderr": "", "why": f"no such program: {exc}"}
    except Exception as exc:
        return {"ok": False, "code": None, "stdout": "", "stderr": "", "why": f"{type(exc).__name__}: {exc}"}


def _json_of(text: str) -> Any:
    txt = (text or "").strip()
    if not txt:
        return None
    try:
        return json.loads(txt)
    except Exception:
        return None


def installed() -> dict[str, Any]:
    """What is on disk here, as paths and booleans. Reports an OUTSIDE install honestly."""
    cfg = _cfg()
    home = install_home()
    cli = cli_path()
    app = app_path()
    web = web_root()
    return {
        "dir": str(home),
        "dir_from": "config filmcraft_dir" if str(cfg.get("filmcraft_dir") or "").strip() else "the kit's own utilities/filmcraft",
        "exists": home.is_dir(),
        "platform": platform_dir(),
        "app": str(app),
        "app_present": app.is_file(),
        "app_mb": round(app.stat().st_size / 1048576, 1) if app.is_file() else 0.0,
        "cli": str(cli),
        "cli_present": cli.is_file(),
        "cli_mb": round(cli.stat().st_size / 1048576, 1) if cli.is_file() else 0.0,
        "web": str(web),
        "web_present": (web / "index.html").is_file() and (web / "filmcraft_web_bg.wasm").is_file(),
        "web_mb": round((web / "filmcraft_web_bg.wasm").stat().st_size / 1048576, 1)
        if (web / "filmcraft_web_bg.wasm").is_file() else 0.0,
        "pinned": RELEASE_FLOOR,
        "fetch": "scripts/fetch_filmcraft.ps1",
    }


def version() -> str:
    """The CLI's own version string. Asked once per process - it is a fact about a file."""
    if _COMMANDS_CACHE.get("version"):
        return str(_COMMANDS_CACHE["version"])
    cli = cli_path()
    if not cli.is_file():
        return ""
    out = _run([str(cli), "--version"], timeout=30)
    m = re.search(r"filmcraft-cli\s+([0-9][^\s]*)", out.get("stdout") or "")
    ver = m.group(1) if m else ""
    if ver:
        _COMMANDS_CACHE["version"] = ver
    return ver


# --------------------------------------------------------------------------------------------
# the editor: boot, look, stop
# --------------------------------------------------------------------------------------------


def running(probe: bool = True) -> dict[str, Any]:
    """Is an editor answering? The PORT is the proof; the pid is only our own record of what we started.

    `probe=False` is the boot-check shape: it reports what the RECORD says and says nothing about
    whether anything is behind the port (`None`, not `False` - "not checked" is not "not there").
    A socket in the boot path is wrong twice over: connect() to a closed port on Windows costs the
    full timeout here (measured 0.35 s), and a boot must not wait on any port at all.
    """
    rec = _record_read("app")
    port = int(rec.get("port") or control_port())
    pid = int(rec.get("pid") or 0)
    answers = port_open(port) if probe else None
    return {
        "answers": answers,
        "probed": probe,
        "port": port,
        "pid": pid,
        "pid_alive": (alive(pid) if pid else False) if probe else None,
        "started_at": rec.get("at") or "",
        "started_by": rec.get("by") or ("this console" if pid else ""),
        "control": answers,
        "ours": bool(pid and alive(pid)) if probe else (None if not pid else bool(pid)),
    }


def control(method: str, params: dict[str, Any] | None = None, timeout: float = 20.0) -> dict[str, Any]:
    """One request, one reply, one connection - the protocol FilmCraft documents.

    The port has no authentication and only reads requests, so this speaks and closes. A refused or
    dead port is reported as such: a control call that never happened must not read as a result.
    """
    rec = running()
    if not rec.get("answers"):
        return {"ok": False, "error": "no_editor", "why":
                f"nothing is answering on 127.0.0.1:{rec.get('port')} - boot the editor first"}
    msg = json.dumps({"id": 1, "method": method, "params": params or {}}) + "\n"
    try:
        with socket.create_connection(("127.0.0.1", int(rec["port"])), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(msg.encode("utf-8"))
            f = s.makefile("rb")
            line = f.readline()
    except Exception as exc:
        return {"ok": False, "error": "control_failed", "why": f"{type(exc).__name__}: {exc}"}
    reply = _json_of((line or b"").decode("utf-8", "replace"))
    if not isinstance(reply, dict):
        return {"ok": False, "error": "unreadable_reply", "why": "the editor's reply was not a JSON object",
                "raw": (line or b"")[:400].decode("utf-8", "replace")}
    return reply


def boot(by: str = "console", port: int | None = None, wait_s: int = 45) -> dict[str, Any]:
    """Start the desktop editor with its control channel open. Named action, never a side effect."""
    app = app_path()
    if not app.is_file():
        return {"ok": False, "error": "not_installed", "why": f"no editor at {app}",
                "fix": "scripts/fetch_filmcraft.ps1 (or point filmcraft_app at an installed copy)"}
    now = running()
    if now.get("answers"):
        return {"ok": True, "already": True, "why": "an editor is already answering on its control channel",
                **now}
    FILM_SAVE.mkdir(parents=True, exist_ok=True)
    log = FILM_SAVE / "app.log"
    argv = [str(app)] + (["--control", str(int(port))] if port else ["--control", str(control_port())])
    used = int(port or control_port())
    try:
        fh = open(log, "ab")
        proc = subprocess.Popen(argv, cwd=str(app.parent), stdout=fh, stderr=fh, stdin=subprocess.DEVNULL,
                                creationflags=(_DETACHED if sys.platform.startswith("win") else 0))
    except Exception as exc:
        return {"ok": False, "error": "spawn_failed", "why": f"{type(exc).__name__}: {exc}", "argv": argv}
    _record_write("app", {"pid": proc.pid, "port": used, "at": _at(), "by": by,
                          "app": str(app), "log": str(log)})
    deadline = _now() + max(5, wait_s)
    while _now() < deadline:
        if port_open(used):
            return {"ok": True, "already": False, "pid": proc.pid, "port": used,
                    "why": "the editor is up and its control channel answers", "log": str(log)}
        if not alive(proc.pid):
            tail = ""
            try:
                tail = read_text(log)[-600:]
            except Exception:
                pass
            return {"ok": False, "error": "died_on_start", "pid": proc.pid, "port": used,
                    "why": "the editor exited before its control channel opened", "log": str(log),
                    "log_tail": tail,
                    "hint": "a machine with no display, or a missing runtime, says so in the log tail"}
        time.sleep(0.5)
    return {"ok": False, "error": "no_control_channel", "pid": proc.pid, "port": used,
            "why": f"the editor is running but nothing answered on 127.0.0.1:{used} within {wait_s}s",
            "log": str(log), "hint": "the editor stays up; its window may still be loading. Try `status` again."}


def stop(by: str = "console") -> dict[str, Any]:
    """Stop the editor THIS console started. An editor somebody else launched is left alone by name."""
    rec = _record_read("app")
    pid = int(rec.get("pid") or 0)
    if not pid:
        live = port_open(control_port())
        return {"ok": False, "error": "not_ours",
                "why": "this console did not start an editor, so it will not stop one"
                       + (" (something IS answering on the control port - that copy is not ours)" if live else "")}
    if not alive(pid):
        _record_write("app", {**rec, "stopped_at": _at(), "stopped_by": by})
        return {"ok": True, "already": True, "why": "the editor this console started is already gone", "pid": pid}
    killed = _kill_tree(pid)
    still = port_open(int(rec.get("port") or control_port()))
    _record_write("app", {**rec, "stopped_at": _at(), "stopped_by": by, "killed": killed})
    if killed and not still:
        return {"ok": True, "pid": pid, "why": "the editor is stopped and its control port is closed"}
    return {"ok": False, "error": "still_answering" if still else "still_alive", "pid": pid,
            "why": "the process did not go away - close its window by hand and try again"}


# --------------------------------------------------------------------------------------------
# the WebAssembly editor, served from the kit
# --------------------------------------------------------------------------------------------


def web_status(probe: bool = True) -> dict[str, Any]:
    """Is the browser editor being served? Same rule as `running()`: no socket in the boot path."""
    rec = _record_read("web")
    port = int(rec.get("port") or web_port())
    pid = int(rec.get("pid") or 0)
    serving = port_open(port) if probe else None
    return {
        "available": (web_root() / "index.html").is_file(),
        "serving": serving,
        "probed": probe,
        "port": port,
        "pid": pid,
        "pid_alive": (alive(pid) if pid else False) if probe else None,
        "url": f"http://127.0.0.1:{port}/" if serving else "",
        "root": str(web_root()),
        "ours": bool(pid and alive(pid)) if probe else (None if not pid else bool(pid)),
    }


def web_start(port: int | None = None) -> dict[str, Any]:
    """Serve the offline browser editor from the kit. Localhost only; the page uploads nothing."""
    root = web_root()
    if not (root / "index.html").is_file():
        return {"ok": False, "error": "no_web_payload", "why": f"no WebAssembly editor at {root}",
                "fix": "scripts/fetch_filmcraft.ps1 -Payload web"}
    now = web_status()
    if now.get("serving"):
        return {"ok": True, "already": True, "url": now["url"], "port": now["port"]}
    used = int(port or web_port())
    script = KIT_ROOT / "tools" / "film_web.py"
    if not script.is_file():
        return {"ok": False, "error": "helper_missing", "why": f"{script} is not in this kit"}
    log = FILM_SAVE / "web.log"
    FILM_SAVE.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, str(script), "--port", str(used), "--root", str(root)]
    try:
        fh = open(log, "ab")
        proc = subprocess.Popen(argv, cwd=str(KIT_ROOT), stdout=fh, stderr=fh, stdin=subprocess.DEVNULL,
                                creationflags=(_DETACHED if sys.platform.startswith("win") else 0))
    except Exception as exc:
        return {"ok": False, "error": "spawn_failed", "why": f"{type(exc).__name__}: {exc}", "argv": argv}
    _record_write("web", {"pid": proc.pid, "port": used, "at": _at(), "root": str(root), "log": str(log)})
    deadline = _now() + 20
    while _now() < deadline:
        if port_open(used):
            return {"ok": True, "already": False, "pid": proc.pid, "port": used,
                    "url": f"http://127.0.0.1:{used}/", "root": str(root)}
        if not alive(proc.pid):
            tail = ""
            try:
                tail = read_text(log)[-600:]
            except Exception:
                tail = ""
            return {"ok": False, "error": "died_on_start", "pid": proc.pid, "log": str(log), "log_tail": tail}
        time.sleep(0.25)
    return {"ok": False, "error": "no_answer", "pid": proc.pid, "port": used, "log": str(log),
            "why": f"the helper started but nothing answered on 127.0.0.1:{used}"}


def web_stop() -> dict[str, Any]:
    rec = _record_read("web")
    pid = int(rec.get("pid") or 0)
    if not pid:
        return {"ok": False, "error": "not_ours", "why": "this console is not serving the web editor"}
    if not alive(pid):
        _record_write("web", {**rec, "stopped_at": _at()})
        return {"ok": True, "already": True, "why": "the served copy was already gone", "pid": pid}
    _kill_tree(pid)
    still = port_open(int(rec.get("port") or web_port()))
    _record_write("web", {**rec, "stopped_at": _at()})
    return {"ok": not still, "pid": pid, "error": "still_serving" if still else "",
            "why": "the web editor is stopped and its port is closed" if not still
                   else "the port is still answering - close it by hand"}


# --------------------------------------------------------------------------------------------
# the headless engine
# --------------------------------------------------------------------------------------------


def cli(args: list[str], timeout: int | None = None, cwd: Path | None = None) -> dict[str, Any]:
    """Run the CLI once. Output is JSON; exit 0 success, 1 a failing command, 2 a usage error."""
    exe = cli_path()
    if not exe.is_file():
        return {"ok": False, "error": "not_installed", "why": f"no CLI at {exe}",
                "fix": "scripts/fetch_filmcraft.ps1"}
    out = _run([str(exe)] + [str(a) for a in args], cwd=cwd or exe.parent,
               timeout=int(timeout or cli_timeout()))
    if out.get("timed_out"):
        return {"ok": False, "error": "timeout", "why": out.get("why"), "argv": args}
    out["argv"] = args
    return out


def commands(refresh: bool = False) -> dict[str, Any]:
    """The command catalogue. Read from the CLI once and cached beside the kit's own records."""
    ver = version()
    if not refresh and _COMMANDS_CACHE.get("rows") and _COMMANDS_CACHE.get("version") == ver:
        return {"ok": True, "rows": _COMMANDS_CACHE["rows"], "from": "memory", "version": ver}
    cache = FILM_SAVE / "commands.json"
    if not refresh:
        try:
            d = json.loads(read_text(cache))
            if isinstance(d, dict) and d.get("rows") and d.get("version") == ver:
                _COMMANDS_CACHE.update({"rows": d["rows"], "version": ver, "at": _now()})
                return {"ok": True, "rows": d["rows"], "from": "cache", "version": ver,
                        "at": d.get("at") or ""}
        except Exception:
            pass
    out = cli(["commands", "--json"], timeout=120)
    if not out.get("ok"):
        return {"ok": False, "error": "commands_failed",
                "why": (out.get("stderr") or out.get("why") or "the CLI did not list its commands")[:400]}
    data = _json_of(out.get("stdout") or "")
    rows = data if isinstance(data, list) else (data or {}).get("commands") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return {"ok": False, "error": "unreadable_catalogue", "why": "the CLI answered something that is not a command list"}
    _COMMANDS_CACHE.update({"rows": rows, "version": ver, "at": _now()})
    try:
        FILM_SAVE.mkdir(parents=True, exist_ok=True)
        atomic_write_text(cache, json.dumps({"version": ver, "at": _at(), "rows": rows}))
    except Exception:
        pass
    return {"ok": True, "rows": rows, "from": "cli", "version": ver, "count": len(rows)}


def find_commands(query: str = "", limit: int = 40) -> dict[str, Any]:
    got = commands()
    if not got.get("ok"):
        return got
    q = (query or "").strip().lower()
    rows = got["rows"]
    if q:
        hits = []
        for r in rows:
            hay = " ".join(str(r.get(k) or "") for k in ("id", "label", "params"))
            hay += " " + " ".join(str(x) for x in (r.get("menu") or []))
            hay += " " + " ".join(str(x) for x in (r.get("shortcuts") or []))
            if q in hay.lower():
                hits.append(r)
    else:
        hits = list(rows)
    slim = [{"id": r.get("id"), "label": r.get("label"), "menu": "/".join(str(x) for x in (r.get("menu") or [])),
             "params": r.get("params") or "", "enabled": r.get("enabled"), "shortcut": r.get("shortcut") or ""}
            for r in hits[: max(1, min(int(limit or 40), 400))]]
    return {"ok": True, "count": len(hits), "shown": len(slim), "version": got.get("version"),
            "commands": slim}


def describe(command: str) -> dict[str, Any]:
    out = cli(["describe", str(command)], timeout=90)
    data = _json_of(out.get("stdout") or "")
    if isinstance(data, dict):
        return {"ok": True, "command": command, **data}
    return {"ok": False, "error": "unknown_command" if out.get("ok") else "describe_failed",
            "command": command, "why": (out.get("stderr") or out.get("stdout") or out.get("why") or "")[:400]}


def _project_args(project: Any, demo: bool) -> tuple[list[str], str]:
    """A project to act on: the kit's own workspace by default, or an absolute path."""
    if demo or not str(project or "").strip():
        return (["--demo"] if demo else []), "demo" if demo else "empty"
    p = Path(os.path.expandvars(os.path.expanduser(str(project))))
    if not p.is_absolute():
        p = FILM_WORKSPACE / "projects" / p
    if p.suffix.lower() != ".fcproj" and p.parent.is_dir():
        p = p.with_suffix(".fcproj")
    return ["--project", str(p)], str(p)


def inspect(project: Any = None, what: str = "both", demo: bool = False,
            consent: bool = False) -> dict[str, Any]:
    """The project tree, the active sequence, or both - headless, no window, nothing left open.

    A project outside this kit's own workspace needs consent even to be READ. Not because reading is
    dangerous in itself, but because pointing a 693-command media engine at an arbitrary path is a
    thing to do on purpose: the same guard covers every project-addressed action, so the answer is
    the same word (`consent_required`) whichever one the caller reached for.
    """
    kind = str(what or "both").strip().lower()
    if kind not in ("both", "project", "sequence"):
        return {"ok": False, "error": "bad_what", "why": "what must be project, sequence or both"}
    args, target = _project_args(project, demo)
    if _outside_workspace(target) and not consent:
        return _needs_consent(target, "reading")
    args += ["inspect"] + ([kind] if kind != "both" else [])
    out = cli(args)
    if not out.get("ok"):
        return {"ok": False, "error": "inspect_failed", "target": target,
                "why": (out.get("stderr") or out.get("why") or "")[:600]}
    data = _json_of(out.get("stdout") or "")
    if data is None:
        return {"ok": False, "error": "unreadable_answer", "target": target,
                "why": "the CLI printed something that is not JSON", "head": (out.get("stdout") or "")[:300]}
    return {"ok": True, "target": target, "what": kind, "document": data}


def _outside_workspace(target: Any) -> bool:
    """Is this project (or file) outside the kit's own film workspace? 'demo'/'empty' are not."""
    return str(target) not in ("demo", "empty") and not str(target).startswith(str(FILM_WORKSPACE))


def _needs_consent(target: Any, doing: str) -> dict[str, Any]:
    """The ONE refusal every project-addressed action shares, in the same words. A caller that gets
    a different word from two actions asking the same question has been told two different things."""
    return {"ok": False, "error": "consent_required", "target": str(target),
            "why": f"that project is not in this kit's own workspace, so {doing} it needs consent",
            "hint": "call again with consent=true, or copy the project into workspace/film/projects first"}


def _needs_consent_to_write(dest: Path, target: Any) -> dict[str, Any]:
    return {"ok": False, "error": "consent_required", "target": str(target), "file": str(dest),
            "why": "that file is outside this kit's own workspace, so writing it needs consent",
            "hint": "call again with consent=true, or give a bare name to land it in workspace/film"}


def run(steps: list[dict[str, Any]], project: Any = None, save: bool = False, demo: bool = False,
        consent: bool = False) -> dict[str, Any]:
    """Run engine commands in order on a project (headless). Editing somebody's project is gated."""
    args, target = _project_args(project, demo)
    if _outside_workspace(target) and not consent:
        # The guard is checked BEFORE the argument quibble, deliberately: a caller that reached for a
        # project of the operator's own is told that first, whichever way it went about it. Checking
        # steps first made the same call answer `no_steps` instead, which hides the real problem.
        return _needs_consent(target, "editing")
    if not isinstance(steps, list) or not steps:
        return {"ok": False, "error": "no_steps", "why": "run needs a non-empty list of {id, params}"}
    script = "\n".join(json.dumps({"id": str(s.get("id") or ""), "params": s.get("params") or {}})
                       for s in steps if s.get("id"))
    if not script.strip():
        return {"ok": False, "error": "no_steps", "why": "every step was missing an id"}
    FILM_SAVE.mkdir(parents=True, exist_ok=True)
    tmp = FILM_SAVE / "run.jsonl"
    try:
        atomic_write_text(tmp, script + "\n")
    except Exception as exc:
        return {"ok": False, "error": "write_failed", "why": str(exc)[:200]}
    argv = args + (["--save"] if save else []) + ["run", str(tmp)]
    out = cli(argv)
    lines = []
    for line in (out.get("stdout") or "").splitlines():
        d = _json_of(line)
        if isinstance(d, dict):
            lines.append(d)
    ok = bool(out.get("ok")) and all(l.get("ok") for l in lines) if lines else bool(out.get("ok"))
    return {"ok": ok, "target": target, "saved": bool(save), "steps": len(steps),
            "results": lines, "code": out.get("code"),
            "why": "" if ok else (out.get("stderr") or out.get("why") or "a command failed")[:400]}


def export(project: Any = None, out_name: str = "", preset: str = "", start: float | None = None,
           end: float | None = None, settings: dict[str, Any] | None = None,
           allow_offline: bool = False, demo: bool = False, timeout: int | None = None,
           consent: bool = False) -> dict[str, Any]:
    """Render the active sequence to a file. This is the slow one: minutes, not seconds."""
    if demo:
        args, target = ["--demo"], "demo"
    else:
        args, target = _project_args(project, False)
    if _outside_workspace(target) and not consent:
        return _needs_consent(target, "exporting")
    name = str(out_name or "").strip() or "export.mp4"
    dest = Path(os.path.expandvars(os.path.expanduser(name)))
    if not dest.is_absolute():
        dest = FILM_WORKSPACE / "exports" / dest
    if not dest.suffix:
        dest = dest.with_suffix(".mp4")
    if _outside_workspace(str(dest)) and not consent:
        # The output file is gated too. Without this, `out` was a way to write a render anywhere on
        # the disk with no consent at all - the project guard alone did not cover where it landed.
        return _needs_consent_to_write(dest, target)
    argv = args + ["export", str(dest)]
    if preset:
        argv += ["--preset", str(preset)]
    if start is not None:
        argv += ["--start", str(float(start))]
    if end is not None:
        argv += ["--end", str(float(end))]
    if settings:
        argv += ["--settings", json.dumps(settings)]
    if allow_offline:
        argv += ["--allow-offline"]
    if not _ensure_parent(dest):
        return {"ok": False, "error": "cannot_write_there", "target": target, "file": str(dest),
                "why": f"the folder for {dest} could not be created"}
    out = cli(argv, timeout=timeout)
    wrote = dest.is_file()
    return {"ok": bool(out.get("ok")) and wrote, "target": target, "file": str(dest),
            "mb": round(dest.stat().st_size / 1048576, 2) if wrote else 0.0,
            "wrote": wrote, "code": out.get("code"),
            "offline_media": [l.strip() for l in (out.get("stderr") or "").splitlines() if "missing" in l][:20],
            "why": "" if (out.get("ok") and wrote) else
                   (out.get("stderr") or out.get("why") or "the export did not write the file")[:600]}


def render_frame(project: Any = None, seconds: float = 0.0, out_name: str = "", scale: float | None = None,
                 demo: bool = False, consent: bool = False) -> dict[str, Any]:
    """One PNG of the program frame. Read-only on the project, but still gated by where it is."""
    if demo:
        args, target = ["--demo"], "demo"
    else:
        args, target = _project_args(project, False)
    if _outside_workspace(target) and not consent:
        return _needs_consent(target, "rendering")
    name = str(out_name or "").strip() or f"frame-{int(time.time())}.png"
    dest = Path(os.path.expandvars(os.path.expanduser(name)))
    if not dest.is_absolute():
        dest = FILM_WORKSPACE / "renders" / dest
    if dest.suffix.lower() != ".png":
        dest = dest.with_suffix(".png")
    if _outside_workspace(str(dest)) and not consent:
        return _needs_consent_to_write(dest, target)
    argv = args + ["render", "--seconds", str(float(seconds)), "--out", str(dest)]
    if scale:
        argv += ["--scale", str(float(scale))]
    if not _ensure_parent(dest):
        return {"ok": False, "error": "cannot_write_there", "target": target, "file": str(dest),
                "why": f"the folder for {dest} could not be created"}
    out = cli(argv)
    wrote = dest.is_file()
    return {"ok": bool(out.get("ok")) and wrote, "target": target, "file": str(dest),
            "mb": round(dest.stat().st_size / 1048576, 2) if wrote else 0.0,
            "why": "" if (out.get("ok") and wrote) else
                   (out.get("stderr") or out.get("why") or "no frame was written")[:400]}


def presets(query: str = "") -> dict[str, Any]:
    args = ["export", "--list-presets"] + ([str(query)] if str(query or "").strip() else [])
    out = cli(args, timeout=120)
    data = _json_of(out.get("stdout") or "")
    rows = data if isinstance(data, list) else (data or {}).get("presets") if isinstance(data, dict) else None
    if not out.get("ok") or rows is None:
        return {"ok": False, "error": "presets_failed",
                "why": (out.get("stderr") or out.get("why") or "")[:300]}
    return {"ok": True, "count": len(rows), "presets": rows[:80]}


# --------------------------------------------------------------------------------------------
# what is in the film workspace
# --------------------------------------------------------------------------------------------


def _files(root: Path, pattern: str, limit: int = 40) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.is_dir():
        return rows
    try:
        for p in sorted(root.glob(pattern), key=lambda q: q.stat().st_mtime, reverse=True)[:limit]:
            if p.is_file():
                rows.append({"name": p.name, "path": str(p), "mb": round(p.stat().st_size / 1048576, 2),
                             "at": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))})
    except OSError:
        pass
    return rows


def projects() -> list[dict[str, Any]]:
    rows = _files(FILM_WORKSPACE / "projects", "**/*.fcproj", 40)
    rows += _files(FILM_WORKSPACE / "media", "**/*.fcproj", 20)
    return rows


def exports() -> list[dict[str, Any]]:
    rows = []
    for pat in ("*.mp4", "*.mov", "*.mkv", "*.mxf", "*.wav", "*.png"):
        rows += _files(FILM_WORKSPACE / "exports", pat, 20)
    rows.sort(key=lambda r: r.get("at") or "", reverse=True)
    return rows[:40]


def renders() -> list[dict[str, Any]]:
    return _files(FILM_WORKSPACE / "renders", "*.png", 20)


# --------------------------------------------------------------------------------------------
# the whole fact set, for the modules
# --------------------------------------------------------------------------------------------


def state(with_commands: bool = False, cheap: bool = False) -> dict[str, Any]:
    """Everything the console can honestly say about FilmCraft right now. Reads only.

    `cheap=True` is the BOOT-CHECK shape: filesystem facts and directory scans, with no engine
    process started. It exists because a module's `health()` runs inside the host's `load()`, and
    spawning `filmcraft --version` (or asking for 693 commands) there cost this console 1.4 s per
    card - measured - for a fact nobody asked for at boot. The panel's own route (`data`) uses the
    full shape, where the operator is looking at it on purpose.
    """
    inst = installed()
    run_now = running(probe=not cheap)
    web = web_status(probe=not cheap)
    st: dict[str, Any] = {
        "signature": SIGNATURE,
        "at": _at(),
        "installed": inst,
        "version": "" if cheap else (version() if inst["cli_present"] else ""),
        "version_read": not cheap,
        "editor": run_now,
        "web": web,
        "projects": projects(),
        "exports": exports(),
        "renders": renders(),
        "workspace": str(FILM_WORKSPACE),
        "fetch": "scripts/fetch_filmcraft.ps1",
        "pinned": RELEASE_FLOOR,
    }
    if with_commands and not cheap:
        got = commands()
        st["commands"] = {"ok": got.get("ok"), "count": len(got.get("rows") or []),
                          "version": got.get("version") or "", "from": got.get("from") or ""}
    return st


# --------------------------------------------------------------------------------------------
# the agent-facing limb
# --------------------------------------------------------------------------------------------

ACTIONS = ("status", "open", "boot", "stop", "editor", "web", "web_stop", "inspect", "commands",
           "describe", "run", "export", "render", "presets", "doctor")

_ACTION_HELP = {
    "status": "what is installed, what is running, what is in the film workspace (no arguments)",
    "open": "start the desktop editor with its control channel open",
    "boot": "the console's own word for `open` - the button posts this, and it must mean the same thing",
    "stop": "stop the editor THIS console started",
    "editor": "ask the RUNNING editor something over its control channel (method=engine.commands, "
              "ui.elements, ui.screenshot, engine.execute ...)",
    "web": "serve the offline WebAssembly editor from the kit and answer with its URL",
    "web_stop": "stop the served web editor",
    "inspect": "the project tree / active sequence as JSON (project=..., what=project|sequence|both, demo=true)",
    "commands": "the command catalogue, filtered (query=..., limit=...)",
    "describe": "one command: menu, shortcut, parameters, enabled now (command=...)",
    "run": "run engine commands on a project (steps=[{id,params}], project=..., save=true)",
    "export": "render the active sequence to a file (project=..., out=..., preset=..., start=..., end=...)",
    "render": "one PNG of the program frame (project=..., seconds=..., out=...)",
    "presets": "the export presets this build has (query=...)",
    "doctor": "the install as the console checks it: paths, version, what is missing and what to run",
}


def film_limb(args: dict[str, Any] | None = None) -> dict[str, Any]:
    """The `film` limb. One action per call, one dict back, nothing raised."""
    a = dict(args or {})
    action = str(a.get("action") or a.get("do") or a.get("op") or "").strip().lower()
    if not action:
        return {"ok": False, "error": "no_action", "actions": list(ACTIONS), "help": _ACTION_HELP}
    if action in ("info", "state"):
        action = "status"
    if action == "serve":
        action = "web"
    if action == "df":
        action = "describe"
    if action == "boot":
        # The console's buttons post `boot` (the steward's own word for it); the limb's canonical
        # verb is `open`. Both land on the same function, so a click and an agent turn cannot start
        # two different things - which is the whole point of one harness behind both doors.
        action = "open"

    if action == "status":
        st = state(with_commands=False)
        return {"ok": True, "action": "status", **st}

    if action == "doctor":
        inst = installed()
        misses = []
        if not inst["app_present"]:
            misses.append(f"the desktop editor is not at {inst['app']}")
        if not inst["cli_present"]:
            misses.append(f"the headless CLI is not at {inst['cli']}")
        if not inst["web_present"]:
            misses.append(f"the WebAssembly editor is not at {inst['web']}")
        return {"ok": not misses, "action": "doctor", "installed": inst, "version": version(),
                "missing": misses,
                "fix": "" if not misses else "scripts/fetch_filmcraft.ps1",
                "why": "" if not misses else
                       "FilmCraft is fetched from its pinned release, never copied into git"}

    if action == "open":
        return {"action": "open", **boot(by=str(a.get("by") or "agent"))}

    if action == "stop":
        return {"action": "stop", **stop(by=str(a.get("by") or "agent"))}

    if action == "web":
        out = web_start()
        return {"action": "web", **out}

    if action == "web_stop":
        return {"action": "web_stop", **web_stop()}

    if action == "editor":
        method = str(a.get("method") or a.get("call") or "").strip()
        if not method:
            return {"ok": False, "error": "no_method", "action": "editor",
                    "methods": ["engine.commands", "engine.execute", "ui.inspect", "ui.elements",
                                "ui.click", "ui.screenshot", "ui.key", "ui.set"],
                    "help": _ACTION_HELP["editor"]}
        params = a.get("params") if isinstance(a.get("params"), dict) else {}
        if not params:
            for key in ("command", "id", "panel", "path", "prefix", "x", "y", "key"):
                if a.get(key) not in (None, ""):
                    params[key] = a[key]
        raw = control(method, params)
        return {"action": "editor", "method": method, "params": params,
                "ok": bool(raw.get("ok")), "result": raw.get("result"),
                "error": raw.get("error") or "", "why": raw.get("why") or ""}

    if action == "inspect":
        return {"action": "inspect", **inspect(project=a.get("project"), what=str(a.get("what") or "both"),
                                               demo=bool(a.get("demo")), consent=bool(a.get("consent")))}

    if action == "commands":
        return {"action": "commands", **find_commands(str(a.get("query") or a.get("filter") or ""),
                                                      int(a.get("limit") or 40))}

    if action == "describe":
        cmd = str(a.get("command") or a.get("id") or a.get("which") or "").strip()
        if not cmd:
            return {"ok": False, "error": "no_command", "action": "describe",
                    "help": "name the command, or use `commands` with a query first"}
        return {"action": "describe", **describe(cmd)}

    if action == "run":
        steps = a.get("steps") or a.get("commands")
        if isinstance(steps, str):
            steps = [{"id": steps}]
        if isinstance(steps, dict):
            steps = [steps]
        return {"action": "run", **run(steps or [], project=a.get("project"), save=bool(a.get("save")),
                                       demo=bool(a.get("demo")), consent=bool(a.get("consent")))}

    if action == "export":
        return {"action": "export", **export(project=a.get("project"), out_name=str(a.get("out") or ""),
                                             preset=str(a.get("preset") or ""),
                                             start=a.get("start"), end=a.get("end"),
                                             settings=a.get("settings") if isinstance(a.get("settings"), dict) else None,
                                             allow_offline=bool(a.get("allow_offline")),
                                             consent=bool(a.get("consent")),
                                             demo=bool(a.get("demo")))}

    if action == "render":
        return {"action": "render", **render_frame(project=a.get("project"),
                                                   seconds=float(a.get("seconds") or 0),
                                                   out_name=str(a.get("out") or ""),
                                                   scale=a.get("scale"), demo=bool(a.get("demo")),
                                                   consent=bool(a.get("consent")))}

    if action == "presets":
        return {"action": "presets", **presets(str(a.get("query") or ""))}

    return {"ok": False, "error": "unknown_action", "action": action, "actions": list(ACTIONS),
            "help": _ACTION_HELP}
