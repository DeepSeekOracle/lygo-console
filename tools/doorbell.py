#!/usr/bin/env python3
"""The doorbell - the one thing that can still start the console when the console is gone.

The steward, 2026-09-21: *"a good feature would be a button on the console that is basically the boot bat
thats on the desktop, built into the console ... right now the server is down and closed but the browser is
up showing health failed ... the boot LLM button only switches the llm, we need a separate stand alone
button that boots the server from the browser page just in case it goes down or needs a restart"*.

The page cannot do this by itself. It is served *by* the server it would be asking to start, and no browser
may spawn a process. So the page rings a doorbell instead: this listener, on the console's port minus one,
holding no state, whose whole job is to run `LYGO_LLM_CONSOLE.bat`.

It deliberately does NOT reimplement the launcher. Port resolution, the stale-holder sweep, the
"is this really ours" ownership check, the refusal to start a second console - all of that already lives in
the .bat and stays there, so one place decides how this copy starts.

Design rules:

* loopback only, never 0.0.0.0 - this starts a process;
* every boot needs the token in `data/.lygo_doorbell_token`, which the console hands to its own page (same
  origin, already authenticated). A page on some other site can post here but cannot guess it, and a foreign
  origin is never echoed back in the CORS reply either;
* no shell string is ever assembled from a request: the argv is fixed, the token is compared, nothing else
  from the wire reaches the process table;
* `LYGO_NO_BROWSER=1` on the boot, because the browser is already open on the page that rang - a second tab
  is noise;
* it outlives the console (`DETACHED_PROCESS`): a doorbell that dies with the thing it revives is pointless;
* it never raises into a caller, and it never prints or logs the token.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOST = "127.0.0.1"
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = "LYGO_LLM_CONSOLE.bat"
# The USB copy launches from its own .bat. Hardcoding one name would make the doorbell refuse to listen
# on the other copy - and that is the copy a hand would most need it on. First that exists wins.
LAUNCHER_CANDIDATES = ("LYGO_LLM_CONSOLE.bat", "LYGO_AGENT_STICK.bat", "LYGO_AGENT_STICK_STACK.bat",
                      "LYGO_AGENT_STICK_PC.bat", "LYGO_AGENT_STICK2.bat")
TOKEN_REL = Path("data") / ".lygo_doorbell_token"
LOG_REL = Path("save") / "logs" / "doorbell.log"
DEFAULT_CONSOLE_PORT = 9641
BOOT_COOLDOWN_S = 6.0

_LAST: dict = {}          # last boot per root, so one copy's history never answers for another's
_TOKEN_CACHE: dict = {}   # one read per root


# ---------------------------------------------------------------------------------------------------- ports


def console_port(root: Path | None = None) -> int:
    """This copy's console port, resolved the one documented way. Never raises: the doorbell must start even
    when nothing else in the kit can be imported, because not starting is the failure it exists for."""
    env = os.environ.get("LYGO_CONSOLE_PORT")
    try:
        n = int(str(env).strip())
        if 0 < n < 65536:
            return n
    except (TypeError, ValueError):
        pass
    try:
        tools = str((Path(root) if root else ROOT) / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import resolve_ports  # type: ignore

        return int(resolve_ports.resolve("LYGO_CONSOLE_PORT", "port", DEFAULT_CONSOLE_PORT)[0])
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_CONSOLE_PORT


console_port_of = console_port  # handle() has an argument of that name, so it calls this one explicitly


def default_port(console: int) -> int:
    """One below the console: 9641 -> 9640 here, and 9651 -> 9650 on the USB copy."""
    return int(console) - 1


def doorbell_port(root: Path | None = None) -> int:
    root = Path(root) if root else ROOT
    try:
        n = int(str(os.environ.get("LYGO_DOORBELL_PORT")).strip())
        if 0 < n < 65536:
            return n
    except (TypeError, ValueError):
        pass
    try:
        src = str(root / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from paths import console_cfg  # type: ignore

        n = int(str(console_cfg().get("doorbell_port") or "").strip())
        if 0 < n < 65536:
            return n
    except Exception:  # noqa: BLE001
        pass
    try:
        fp = root / "config" / "console.json"
        if fp.is_file():
            n = int(str(json.loads(fp.read_text(encoding="utf-8", errors="replace")).get("doorbell_port") or "").strip())
            if 0 < n < 65536:
                return n
    except Exception:  # noqa: BLE001
        pass
    return default_port(console_port(root))


def launcher_path(root: Path | None = None) -> Path:
    """Whichever launcher this copy actually carries. LYGO_LAUNCHER overrides it outright."""
    root = Path(root) if root else ROOT
    override = (os.environ.get("LYGO_LAUNCHER") or "").strip()
    if override:
        return root / override
    for name in LAUNCHER_CANDIDATES:
        if (root / name).exists():
            return root / name
    return root / LAUNCHER


def console_is_up(port: int, timeout: float = 0.4) -> bool:
    """Is anything listening where the console should be? One connect attempt, never a request."""
    try:
        with socket.create_connection((HOST, int(port)), timeout=timeout):
            return True
    except Exception:  # noqa: BLE001 - a refused port is the normal answer here
        return False


# ------------------------------------------------------------------------------------ token, log, detach


def token_file(root: Path | None = None) -> Path:
    return (Path(root) if root else ROOT) / TOKEN_REL


def token(root: Path | None = None, create: bool = True) -> str:
    """The shared secret, created once. The console reads the same file and hands it to its own page."""
    root = Path(root) if root else ROOT
    key = str(root)
    if key in _TOKEN_CACHE:
        return _TOKEN_CACHE[key]
    path = token_file(root)
    try:
        val = path.read_text(encoding="utf-8").strip()
        if len(val) >= 16:
            _TOKEN_CACHE[key] = val
            return val
    except Exception:  # noqa: BLE001
        pass
    if not create:
        return ""
    val = secrets.token_hex(16)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(val)
    except Exception:  # noqa: BLE001
        pass
    _TOKEN_CACHE[key] = val
    return val


def redact(text: str) -> str:
    """No request line reaches a file or a window with its query intact - the secret travels in that query."""
    return re.sub(r"([?&])token=[^\s&\"]*", r"\1token=[redacted]", str(text))


def log_path(root: Path | None = None) -> Path:
    return (Path(root) if root else ROOT) / LOG_REL


def log_line(root: Path, text: str) -> None:
    """Write down what happened, beside the console's own log, so a failure to start is readable afterwards.

    It went silent once and nobody could say why, which is the whole reason this exists. Every boot, ring and
    refusal is recorded here. The secret is stripped first - see redact() above.
    """
    try:
        fp = log_path(root)
        fp.parent.mkdir(parents=True, exist_ok=True)
        with open(fp, "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
    except Exception:  # noqa: BLE001 - a doorbell that cannot write a line is still a doorbell
        pass


def detach_argv() -> list:
    return [sys.executable, str(Path(__file__).resolve()), "--detached"]


def spawn_self(root: Path | None = None, spawn=None) -> dict:
    """Leave the window that started us.

    Started attached - which is how the launcher first did it - this process went away with its console window
    and the page's button then had nothing left to ring: the same trap as the dead server, one layer down. So
    the doorbell hands itself to the operating system, with no window of its own, and returns at once.
    """
    root = Path(root) if root else ROOT
    spawn = spawn or (lambda argv, **kw: subprocess.Popen(argv, **kw))
    try:
        fp = log_path(root)
        fp.parent.mkdir(parents=True, exist_ok=True)
        sink = open(fp, "a", encoding="utf-8")
    except Exception:  # noqa: BLE001
        sink = subprocess.DEVNULL
    try:
        proc = spawn(detach_argv(), cwd=str(root), stdin=subprocess.DEVNULL, stdout=sink,
                     stderr=subprocess.STDOUT, close_fds=True,
                     creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
        return {"detached": True, "pid": getattr(proc, "pid", None)}
    except Exception as exc:  # noqa: BLE001
        return {"detached": False, "why": "%s: %s" % (type(exc).__name__, exc)}


def ensure_running(root: Path | None = None, port: int | None = None, spawn=None) -> dict:
    """What the console calls on its way up, so its own button is never a dead end.

    Best effort and quiet: it reports what it did and never raises into a boot. Started only by the launcher,
    the doorbell would be missing whenever someone ran `python src/server.py` directly - and that is exactly
    the session in which the button would matter.
    """
    root = Path(root) if root else ROOT
    port = int(port if port is not None else doorbell_port(root))
    if console_is_up(port, timeout=0.35):
        return {"already": True, "port": port}
    made = spawn_self(root, spawn=spawn)
    made["port"] = port
    log_line(root, "ensure: nothing was listening on %d, detached=%s" % (port, made.get("detached")))
    return made


# ----------------------------------------------------------------------------------------------------- boot


def boot_argv(root: Path | None = None) -> list:
    """The launcher, in its own minimised window. A fixed argv: nothing from a request is ever in it."""
    return ["cmd", "/c", "start", "", "/min", str(launcher_path(root))]


def boot(root: Path | None = None, *, spawn=None, respect_cooldown: bool = False, by: str = "") -> dict:
    root = Path(root) if root else ROOT
    now = time.time()
    last = _LAST.get(str(root)) or {}
    if respect_cooldown and (now - float(last.get("at") or 0.0)) < BOOT_COOLDOWN_S:
        return {"launched": False, "why": "cooldown", "since": round(now - float(last["at"]), 1)}
    argv = boot_argv(root)
    env = dict(os.environ)
    env["LYGO_NO_BROWSER"] = "1"  # the browser is already open on the page that rang
    if str(root) != str(ROOT):
        env["LYGO_CONSOLE_ROOT"] = str(root)
    spawner = spawn or subprocess.Popen
    try:
        proc = spawner(argv, cwd=str(root), env=env, close_fds=True,
                       creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
    except Exception as exc:  # noqa: BLE001 - a doorbell may never die of a failed boot
        log_line(root, "boot failed: %s: %s" % (type(exc).__name__, exc))
        return {"launched": False, "why": "%s: %s" % (type(exc).__name__, exc)[:200]}
    pid = getattr(proc, "pid", None)
    _LAST[str(root)] = {"at": now, "pid": pid, "by": by}
    if spawn is None:  # a real boot: a test double must not post fake boots into a shipped log
        log_line(root, "boot: launcher pid %s (asked by %s)" % (pid, by or "someone"))
    return {"launched": True, "pid": pid, "argv": argv[2:]}


# -------------------------------------------------------------------------------------- state and replies


def _state(root: Path, console: int, probe=None) -> dict:
    probe = probe or console_is_up
    try:
        up = bool(probe(console))
    except Exception:  # noqa: BLE001
        up = False
    return {
        "doorbell": True,
        "console_port": int(console),
        "console_up": up,
        "launcher": LAUNCHER,
        "root": str(root),
        "last_boot": dict(_LAST.get(str(root)) or {}),
        "cooldown_s": BOOT_COOLDOWN_S,
    }


def _cors(origin: str | None, console: int) -> dict:
    """Only this copy's own page may read a reply. Anything else gets no allow header at all."""
    allowed = ("http://127.0.0.1:%d" % console, "http://localhost:%d" % console)
    headers = {
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "content-type",
        "Access-Control-Max-Age": "600",
    }
    if origin and origin in allowed:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"
    return headers


def _reply(status: int, headers: dict, payload) -> tuple:
    headers = dict(headers or {})
    if isinstance(payload, (dict, list)):
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    elif isinstance(payload, str):
        body = payload.encode("utf-8")
        headers["Content-Type"] = "text/html; charset=utf-8"
    else:
        body = payload or b""
    return status, headers, body


PAGE = """<!doctype html>
<meta charset="utf-8"><title>&#916;9&#934;963 &#183; LYGO doorbell</title>
<style>
 body{background:#101418;color:#c9d6df;font:16px/1.55 Consolas,monospace;padding:44px}
 h1{font-size:17px;color:#8ab4f8;margin:0 0 22px;letter-spacing:.06em}
 button{background:#1b2733;color:#e8f0fe;border:1px solid #4a6b8a;padding:10px 18px;font:inherit;cursor:pointer}
 button:hover{background:#24384a}button[disabled]{opacity:.55;cursor:default}
 #m{color:#7d8b96;margin-top:18px}.up{color:#7ee787}.down{color:#ffa657}
</style>
<h1>&#916;9&#934;963 &#183; LYGO doorbell</h1>
<p>The console is <span id="t">?</span>.</p>
<p><button id="b">Boot the console</button></p>
<p id="m">this page reloads itself when the console answers.</p>
<script>
var TOKEN = "%%TOKEN%%", CPORT = %%CONSOLE%%;
function paint(up){
  var t = document.getElementById("t");
  t.textContent = up ? "running" : "not running";
  t.className = up ? "up" : "down";
}
function check(){
  fetch("http://127.0.0.1:" + CPORT + "/state", {cache:"no-store"})
    .then(function(r){ return r.json(); })
    .then(function(j){ paint(!!j.console_up); })
    .catch(function(){ paint(false); });
}
document.getElementById("b").onclick = function(){
  var b = this; b.disabled = true;
  var t0 = Date.now();
  fetch("/boot?token=" + encodeURIComponent(TOKEN) + "&t=" + t0, {cache:"no-store"}).catch(function(){});
  (function poll(){
    fetch("http://127.0.0.1:" + CPORT + "/state", {cache:"no-store"})
      .then(function(r){ return r.json(); })
      .then(function(j){ if (j.console_up) { location.href = "http://127.0.0.1:" + CPORT + "/"; } })
      .catch(function(){});
    var n = Math.round((Date.now() - t0) / 1000);
    document.getElementById("m").textContent = "starting the console ... " + n + "s";
    if (n < 180) { setTimeout(poll, 1500); }
    else { b.disabled = false;
           document.getElementById("m").textContent =
             "nothing answered in 180s - the launcher window says why (save/logs/doorbell.log)"; }
  })();
};
check(); setInterval(check, 4000);
</script>
"""


def handle(method: str, path: str, body=None, *, root: Path | None = None, expected_token: str | None = None,
           spawn=None, probe=None, console_port: int | None = None, origin: str | None = None) -> tuple:
    """One request, answered. Kept pure so the gate can be tested without a socket or a real launcher."""
    root = Path(root) if root else ROOT
    parsed = urlparse(path or "/")
    route = parsed.path or "/"
    console = int(console_port if console_port is not None else console_port_of(root))
    headers = _cors(origin, console)
    method = (method or "GET").upper()

    if method == "OPTIONS":
        return _reply(204, headers, b"")

    if route == "/" and method == "GET":
        page = PAGE.replace("%%TOKEN%%", token(root)).replace("%%CONSOLE%%", str(console))
        return _reply(200, headers, page)

    if route == "/state" and method == "GET":
        return _reply(200, headers, _state(root, console, probe=probe))

    if route == "/boot":
        given = ""
        if isinstance(body, dict):
            given = str(body.get("token") or "")
        if not given:
            given = str((parse_qs(parsed.query).get("token") or [""])[0])
        if not expected_token or given != expected_token:
            log_line(root, "boot refused: no valid token, caller %s" % (origin or "unknown"))
            return _reply(403, headers, {"error": "bad or missing token", "hint": "the console page holds it"})
        out = boot(root, spawn=spawn, respect_cooldown=True, by=origin or "doorbell page")
        out["console_port"] = console
        return _reply(200, headers, out)

    return _reply(404, headers, {"error": "not found", "paths": ["/", "/state", "/boot"]})


class _Handler(BaseHTTPRequestHandler):
    server_version = "LYGODoorbell/1"
    sys_version = ""

    def log_message(self, fmt, *args):
        # BaseHTTPRequestHandler would print the request line verbatim, query and all - which is where the
        # secret travels. Redacted first, then written to the log; to a window only if asked.
        try:
            line = redact(fmt % args)
            log_line(getattr(self.server, "root", ROOT), line)
            if os.environ.get("LYGO_DOORBELL_VERBOSE"):
                print("[doorbell] " + line, flush=True)
        except Exception:  # noqa: BLE001
            pass

    def _run(self, method: str) -> None:
        parsed = urlparse(self.path)
        body = None
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:  # noqa: BLE001
            length = 0
        if length > 0:
            try:
                raw = self.rfile.read(min(length, 65536)).decode("utf-8", errors="replace")
                body = json.loads(raw) if raw.strip().startswith("{") else {"token": raw.strip()}
            except Exception:  # noqa: BLE001
                body = None
        root = getattr(self.server, "root", ROOT)
        try:
            status, headers, payload = handle(
                method, self.path, body, root=root, expected_token=token(root),
                console_port=getattr(self.server, "console", None),
                origin=self.headers.get("Origin"), probe=console_is_up,
            )
        except Exception as exc:  # noqa: BLE001 - a doorbell must not die of one odd request
            status, headers, payload = _reply(500, {}, {"error": "%s: %s" % (type(exc).__name__, exc)})
        try:
            self.send_response(status)
            for key, val in (headers or {}).items():
                self.send_header(key, val)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if payload:
                self.wfile.write(payload)
        except Exception:  # noqa: BLE001 - the caller went away; the doorbell stays
            pass

    def do_GET(self) -> None:  # noqa: N802
        self._run("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._run("POST")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._run("OPTIONS")


def serve(root: Path | None = None, port: int | None = None) -> int:
    """Bind loopback and answer until killed. One doorbell per copy: a second one exits quietly."""
    root = Path(root) if root else ROOT
    port = int(port if port is not None else doorbell_port(root))
    console = console_port_of(root)
    if not launcher_path(root).exists():
        print("[doorbell] no launcher at %s - refusing to listen" % launcher_path(root), flush=True)
        return 2
    if console_is_up(port, timeout=0.3):
        print("[doorbell] %s:%d is already answered - one doorbell per copy" % (HOST, port), flush=True)
        return 0
    try:
        httpd = ThreadingHTTPServer((HOST, port), _Handler)
    except OSError as exc:
        print("[doorbell] cannot listen on %s:%d (%s)" % (HOST, port, exc), flush=True)
        return 3
    httpd.daemon_threads = True
    httpd.root = root         # type: ignore[attr-defined]
    httpd.console = console   # type: ignore[attr-defined]
    known = token(root, create=True)
    print("[doorbell] up on http://%s:%d/  . console http://%s:%d/  . token file %s (%d chars, never printed)"
          % (HOST, port, HOST, console, TOKEN_REL, len(known)), flush=True)
    log_line(root, "up on %s:%d, console %d" % (HOST, port, console))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = ROOT
    port = None
    for i, a in enumerate(argv):
        if a in ("--port", "-p") and i + 1 < len(argv):
            try:
                port = int(argv[i + 1])
            except ValueError:
                port = None
        if a in ("--root", "-r") and i + 1 < len(argv):
            root = Path(argv[i + 1])
    if "--print-port" in argv:
        print(doorbell_port(root))
        return 0
    if "--detach" in argv:
        # what the launcher and the console use: leave a real one behind, say its pid, and go
        made = spawn_self(root)
        made["log"] = str(log_path(root))
        print(json.dumps(made, sort_keys=True))
        return 0 if made.get("detached") else 3
    return serve(root, port)


if __name__ == "__main__":
    raise SystemExit(main())
