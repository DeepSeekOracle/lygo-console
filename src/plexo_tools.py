"""The console's download engine, and the bridge to a Plexo install.

Δ9Φ963-LYGO-PLEXO-HARNESS-v1

TWO THINGS LIVE HERE, and they are deliberately separate:

1. LYGO's own downloader (`fetch`). A file is split into blocks and every block is fetched on its own
   connection, in parallel, over HTTP range requests - the same public technique a download manager
   uses, implemented here in the standard library so the console needs no install, no Node, no
   Electron and no administrator rights. A server that ignores ranges gets an ordinary single stream
   instead, and says so. Progress is written beside the file as it goes, with the server's own `ETag`
   and `Last-Modified`, so a resume can REFUSE rather than splice two versions of a file together.

2. The bridge to Plexo (https://github.com/anmolkapil/plexo, MIT, (c) Anmol Kapil), the open-source
   desktop download manager this kit's own engine was written from the published behaviour of. Plexo
   runs a small loopback HTTP bridge for its browser extension - `GET /ping`, `POST /add`,
   `POST /confirm` on ports 19288-19290 - and this harness speaks it exactly as documented upstream
   (`src/shared/browserBridge.ts`), so an agent can hand a link to the operator's own Plexo and let
   HIS multi-network setup (Wi-Fi + a tethered phone on one download) do the work.

NO PLEXO SOURCE IS COPIED HERE. The bridge protocol is called, not reimplemented, and the engine is
this kit's own writing: Plexo is MIT-licensed, credited in full by `credit()`, and when its installers
are fetched the pinned copy carries its own LICENSE beside the binaries.

What this harness will NOT do:
  * it will not fetch a 246 MB installer as a side effect of anything (that is the operator's call,
    and `scripts/fetch_plexo.ps1` is the only door);
  * it will not mix two versions of a file on a resume (a changed ETag refuses by name);
  * it will not hand a link to Plexo without `consent=true`: Plexo's own model is a human confirm, and
    a download started on someone's metered connection is a side effect worth one word;
     SOVEREIGN, NOT DELEGATED: this engine also binds each stream to a chosen LOCAL ADDRESS, so one
   file can arrive over two network adapters at once (`bind=[...]`, `adapter_report`), spreads a
   file over several MIRRORS of the same bytes (`urls=[...]`, refused by name when they disagree),
   and takes a whole-job speed ceiling (`limit_mbps`). Bytes-per-adapter is reported afterwards,
   because "the second connection carried something" has to be a number, not a feeling.

* it will not claim a speed-up it did not measure - every job reports its own bytes, seconds and
    streams, and a job on a server without ranges reports `ranges: false` rather than a parallel win.

Δ9Φ963-BUILDER-USB-ENHANCED-v1
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import ssl
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import atomicio
import paths

SIGNATURE = "Δ9Φ963-LYGO-PLEXO-HARNESS-v1"

# --- what is pinned ------------------------------------------------------------------------------
UPSTREAM = "https://github.com/anmolkapil/plexo"
UPSTREAM_LICENCE = "MIT"
UPSTREAM_AUTHOR = "Anmol Kapil (https://github.com/anmolkapil)"
TUTORIAL = "https://getplexo.app"

# --- the bridge, exactly as upstream documents it (src/shared/browserBridge.ts) -------------------
BRIDGE_PORTS = (19288, 19289, 19290)
BRIDGE_API = 1
BRIDGE_PING_TIMEOUT_S = 1.5
BRIDGE_DEADLINE_S = 8.0
BRIDGE_MAX_URL = 8192

# --- our own engine -------------------------------------------------------------------------------
BLOCK_MB = 8.0
STREAM_CHOICES = (4, 8, 16, 32)
MAX_JOBS = 3
PROBE_TIMEOUT_S = 20.0

# Where `adapter_report` asks which public address it is seen from when check=true. A named
# constant, so the disclosure (a third party sees this machine's public address) sits one grep
# away from the call that does it.
PUBLIC_IP_URL = "https://api.ipify.org"
SIDECAR_SUFFIX = ".lygo-dl.json"
CHUNK = 256 * 1024

_NOWINDOW = 0x08000000 if sys.platform.startswith("win") else 0
_DETACHED = 0x00000008 if sys.platform.startswith("win") else 0

STATE_DIR = "plexo"
# Module-level, like every other harness in this kit: `paths` resolves the save root once (a test
# store override is set before import, so this still lands in the isolated store).
SAVE = paths.SAVE / STATE_DIR


def _at() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _cfg() -> dict[str, Any]:
    try:
        return paths.console_cfg() or {}
    except Exception:  # noqa: BLE001 - a harness must import on a tree with no config
        return {}


def _kit_root() -> Path:
    return Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------------------------
# the pinned upstream app: where it is, what it says about itself
# --------------------------------------------------------------------------------------------


def pin() -> dict[str, Any]:
    """The pinned Plexo release, from `utilities/plexo/PIN.json`. Read, never invented."""
    path = _kit_root() / "utilities" / "plexo" / "PIN.json"
    if not path.is_file():
        return {"present": False, "path": str(path), "release": "", "assets": [],
                "upstream": UPSTREAM, "licence": UPSTREAM_LICENCE}
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"present": False, "path": str(path), "error": f"PIN.json unreadable: {exc}",
                "release": "", "assets": [], "upstream": UPSTREAM, "licence": UPSTREAM_LICENCE}
    got["present"] = True
    got["path"] = str(path)
    got.setdefault("upstream", UPSTREAM)
    got.setdefault("licence", UPSTREAM_LICENCE)
    return got


def platform_dir() -> str:
    if sys.platform.startswith("win"):
        return "win-x64"
    if sys.platform == "darwin":
        return "macos-universal"
    return "linux-x86_64"


def install_home() -> Path:
    """Config `plexo_dir` wins; empty means the kit's own `utilities/plexo` (never a drive letter)."""
    val = str(_cfg().get("plexo_dir") or "").strip()
    if val:
        p = Path(val)
        return p if p.is_absolute() else (_kit_root() / p)
    return _kit_root() / "utilities" / "plexo"


def app_path() -> Path:
    """The Plexo executable. Config `plexo_app` wins, then the kit's own payload folder."""
    val = str(_cfg().get("plexo_app") or "").strip()
    if val:
        p = Path(val)
        return p if p.is_absolute() else (_kit_root() / p)
    home = install_home() / platform_dir()
    for name in ("plexo.exe", "Plexo.exe", "plexo"):
        if (home / name).is_file():
            return home / name
    return home / ("plexo.exe" if sys.platform.startswith("win") else "plexo")


def installed() -> dict[str, Any]:
    """What is on disk here, as paths and booleans - and which value answered."""
    cfg = _cfg()
    home = install_home()
    app = app_path()
    wasm = (home / platform_dir() / "plexo.exe").is_file()
    return {
        "dir": str(home),
        "dir_from": "config plexo_dir" if str(cfg.get("plexo_dir") or "").strip() else "the kit's own utilities/plexo",
        "exists": home.is_dir(),
        "platform": platform_dir(),
        "app": str(app),
        "app_present": app.is_file(),
        "app_mb": round(app.stat().st_size / 1048576, 1) if app.is_file() else 0.0,
        "app_from": "config plexo_app" if str(cfg.get("plexo_app") or "").strip() else f"the kit's own utilities/plexo/{platform_dir()}/plexo.exe",
        "installer_present": bool(list((home / "_dl").glob("*setup.exe"))) if (home / "_dl").is_dir() else False,
        "pin": pin().get("release") or "",
        "our_engine": True,
        "windows_payload_hint": wasm or None,
        "fetcher": "scripts/fetch_plexo.ps1",
    }


# --------------------------------------------------------------------------------------------
# the bridge: is Plexo answering, and can it take a link
# --------------------------------------------------------------------------------------------


def _http(method: str, path: str, port: int, *, body: dict[str, Any] | None = None,
          timeout: float = PROBE_TIMEOUT_S) -> tuple[int, Any]:
    url = f"http://127.0.0.1:{int(port)}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "lygo-console/plexo-bridge")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return int(resp.status), json.loads(raw.decode("utf-8", "replace") or "{}")
            except ValueError:
                return int(resp.status), {}
    except urllib.error.HTTPError as exc:
        try:
            return int(exc.code), json.loads((exc.read() or b"{}").decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return int(exc.code), {}
    except Exception:  # noqa: BLE001 - a closed port is a state, not a traceback
        return 0, {}


def bridge_ports() -> tuple[int, ...]:
    """The ports the bridge is asked on. Upstream's own list, unless this kit's config moves them."""
    raw = str(_cfg().get("plexo_bridge_ports") or "").strip()
    if raw:
        got = tuple(int(x) for x in re.findall(r"\d+", raw))
        if got:
            return got
    return tuple(BRIDGE_PORTS)


def bridge(ports: tuple[int, ...] | None = None) -> dict[str, Any]:
    """Ask every port until one answers AS PLEXO. The handshake is upstream's own: app + api version.

    Anything else holding the port counts as closed - which is the point of the handshake, and why a
    stale answer (a different `api`) is reported as `stale` rather than used.
    """
    tried: list[dict[str, Any]] = []
    for port in (ports or bridge_ports()):
        status, body = _http("GET", "/ping", port, timeout=BRIDGE_PING_TIMEOUT_S)
        app = str((body or {}).get("app") or "")
        got_api = (body or {}).get("api")
        row = {"port": port, "status": status, "app": app, "api": got_api,
               "version": str((body or {}).get("version") or "")}
        tried.append(row)
        if status == 200 and app == "plexo" and got_api == BRIDGE_API:
            return {"up": True, "state": "ok", "port": port, "version": row["version"],
                    "api": got_api, "tried": tried, "ports": list(ports or bridge_ports()),
                    "why": f"Plexo {row['version'] or '?'} answers on 127.0.0.1:{port} (api {got_api})"}
        if status == 200 and app == "plexo" and got_api != BRIDGE_API:
            return {"up": True, "state": "stale", "port": port, "version": row["version"],
                    "api": got_api, "tried": tried, "ports": list(ports or bridge_ports()),
                    "why": f"Plexo answers on 127.0.0.1:{port} but speaks bridge api {got_api}, not {BRIDGE_API}"}
    return {"up": False, "state": "closed", "port": 0, "version": "", "api": None, "tried": tried,
            "ports": list(ports or bridge_ports()),
            "why": f"no Plexo bridge answered on {', '.join(str(p) for p in (ports or bridge_ports()))}"}


def _url_ok(url: str) -> tuple[bool, str, str]:
    """Upstream's own rule: http(s) or magnet, no longer than 8192 characters."""
    text = (url or "").strip()
    if not text:
        return False, "empty_url", "no url was given"
    if len(text) > BRIDGE_MAX_URL:
        return False, "url_too_long", f"the url is {len(text)} characters; Plexo's bridge accepts up to {BRIDGE_MAX_URL}"
    if not re.match(r"^(https?://|magnet:\?)", text, re.I):
        return False, "bad_url", "a url has to start with http://, https:// or magnet:?"
    return True, "", ""


def bridge_add(url: str, file_name: str = "", page_url: str = "", timeout: float = BRIDGE_DEADLINE_S) -> dict[str, Any]:
    """`POST /add` - Plexo OFFERS to take it and answers a handoff token (upstream: 200 token / 422 kept)."""
    ok, code, why = _url_ok(url)
    if not ok:
        return {"ok": False, "error": code, "why": why}
    reach = bridge()
    if not reach.get("up") or reach.get("state") != "ok":
        return {"ok": False, "error": "bridge_" + str(reach.get("state") or "closed"), "why": reach.get("why"),
                "bridge": reach}
    body: dict[str, Any] = {"url": url.strip()}
    if file_name:
        body["fileName"] = str(file_name)
    if page_url:
        body["pageUrl"] = str(page_url)
    status, answer = _http("POST", "/add", int(reach["port"]), body=body, timeout=timeout)
    if status == 200 and str(answer.get("token") or ""):
        return {"ok": True, "token": str(answer["token"]), "port": int(reach["port"]),
                "version": reach.get("version") or "", "bridge": reach,
                "why": "Plexo offered to take it; /confirm makes it real"}
    if status == 422:
        return {"ok": False, "error": "kept", "why": "Plexo said no (422): the browser keeps this one",
                "bridge": reach}
    return {"ok": False, "error": "add_failed",
            "why": f"/add answered HTTP {status or 'no response'}", "bridge": reach}


def bridge_confirm(token: str, port: int) -> dict[str, Any]:
    """`POST /confirm` - without this Plexo shows nothing, and the offer expires."""
    if not token:
        return {"ok": False, "error": "no_token", "why": "there is no handoff token to confirm"}
    status, _ = _http("POST", "/confirm", int(port), body={"token": token}, timeout=5.0)
    if status == 200:
        return {"ok": True, "why": "confirmed: Plexo takes the download from here"}
    if status == 410:
        return {"ok": False, "error": "expired", "why": "the offer expired before it was confirmed (410)"}
    return {"ok": False, "error": "confirm_failed", "why": f"/confirm answered HTTP {status or 'no response'}"}


# --------------------------------------------------------------------------------------------
# the app itself: is it running, start it, stop only what we started
# --------------------------------------------------------------------------------------------


def _record_write(name: str, data: dict[str, Any]) -> None:
    SAVE.mkdir(parents=True, exist_ok=True)
    atomicio.atomic_write_text(SAVE / f"{name}.json", json.dumps(data, indent=2))


def _record_read(name: str) -> dict[str, Any]:
    path = SAVE / f"{name}.json"
    if not path.is_file():
        return {}
    try:
        got = json.loads(atomicio.read_text(path))
        return got if isinstance(got, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def alive(pid: int) -> bool:
    """Is that pid still there? Claimed by the OS, not by our own record."""
    if not pid:
        return False
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                                 capture_output=True, text=True, timeout=8,
                                 creationflags=_NOWINDOW)
            return str(int(pid)) in (out.stdout or "")
        os.kill(int(pid), 0)
        return True
    except Exception:  # noqa: BLE001
        return False


def running(probe: bool = True) -> dict[str, Any]:
    """Is the app up? The pid from OUR record is asked of the OS; the bridge is the live proof.

    `probe=False` is the boot-check shape: no socket, and `None` where it did not look.
    """
    rec = _record_read("app")
    pid = int(rec.get("pid") or 0)
    reach = bridge() if probe else {"up": None, "state": "not checked", "port": 0, "why": "not checked"}
    return {
        "pid": pid,
        "pid_alive": alive(pid) if (probe and pid) else (None if not probe else False),
        "started_at": rec.get("at") or "",
        "started_by": rec.get("by") or ("this console" if pid else ""),
        "what": rec.get("what") or "",
        "bridge": reach,
        "probed": probe,
    }


def open_app() -> dict[str, Any]:
    """Start the operator's Plexo, detached, and wait for its bridge to answer."""
    inst = installed()
    if not inst["app_present"]:
        return {"ok": False, "error": "not_installed", "why": inst["app"],
                "fix": f"run {inst['fetcher']} (or install Plexo from {TUTORIAL})"}
    reach = bridge()
    if reach.get("up") and reach.get("state") == "ok":
        return {"ok": True, "already": True, "port": reach["port"], "version": reach.get("version") or "",
                "why": reach.get("why")}
    try:
        proc = subprocess.Popen([str(inst["app"])], cwd=str(Path(inst["app"]).parent),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, creationflags=_NOWINDOW | _DETACHED,
                                close_fds=True)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "spawn_failed", "why": f"{type(exc).__name__}: {exc}"}
    deadline = time.monotonic() + 25.0
    while time.monotonic() < deadline:
        time.sleep(0.75)
        reach = bridge()
        if reach.get("up") and reach.get("state") == "ok":
            _record_write("app", {"pid": int(proc.pid), "at": _at(), "by": "this console",
                                  "what": str(inst["app"]), "port": reach["port"],
                                  "version": reach.get("version") or ""})
            return {"ok": True, "already": False, "pid": int(proc.pid), "port": reach["port"],
                    "version": reach.get("version") or "",
                    "why": f"started {Path(inst['app']).name}; its bridge answers on 127.0.0.1:{reach['port']}"}
        if proc.poll() is not None:
            return {"ok": False, "error": "died_on_start", "why": f"exited with code {proc.returncode} before its bridge answered"}
    return {"ok": False, "error": "bridge_timeout",
            "why": "Plexo was started but no bridge answered within 25 s (a first launch can be slow; its window may also be waiting for you)"}


def stop_app() -> dict[str, Any]:
    """Stop only what THIS console started - a window the operator opened belongs to the operator."""
    rec = _record_read("app")
    pid = int(rec.get("pid") or 0)
    if not pid:
        return {"ok": False, "error": "not_ours",
                "why": "this console did not start Plexo, so it will not stop it - a window you opened is yours"}
    if not alive(pid):
        return {"ok": False, "error": "already_gone", "why": f"pid {pid} is not running any more"}
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True,
                           timeout=20, creationflags=_NOWINDOW)
        else:
            os.kill(pid, 15)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "kill_failed", "why": f"{type(exc).__name__}: {exc}"}
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if not alive(pid):
            return {"ok": True, "pid": pid, "why": "stopped the Plexo this console started"}
        time.sleep(0.5)
    return {"ok": False, "error": "still_alive", "why": f"pid {pid} is still there after taskkill /T /F"}


# --------------------------------------------------------------------------------------------
# LYGO's own download engine: real range blocks, real parallel streams, real resume
# --------------------------------------------------------------------------------------------


def _headers(resp: Any) -> dict[str, str]:
    try:
        return {str(k).lower(): str(v) for k, v in resp.headers.items()}
    except Exception:  # noqa: BLE001
        return {}


def _filename_from_headers(head: dict[str, str], url: str) -> str:
    """The name the SERVER picked, RFC 5987 forms included - then the url's own last segment."""
    raw = head.get("content-disposition") or ""
    name = ""
    if raw:
        m = re.search(r"filename\*\s*=\s*([^;]+)", raw, re.I)
        if m:
            val = m.group(1).strip().strip('"')
            parts = val.split("''", 1)
            charset = (val.split("'", 1)[0] or "utf-8").strip() or "utf-8"
            enc = urllib.parse.unquote(parts[-1], encoding=charset, errors="replace")
            name = enc
        else:
            m = re.search(r'filename\s*=\s*"([^"]+)"', raw, re.I) or re.search(r"filename\s*=\s*([^;]+)", raw, re.I)
            if m:
                name = m.group(1).strip()
    if not name:
        tail = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
        name = urllib.parse.unquote(tail) if tail else ""
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", name).strip().strip(".")
    return name


# ---------------------------------------------------------------------------------------------
# THE SOVEREIGN HALF: several paths, our own sockets
# ---------------------------------------------------------------------------------------------
# Upstream's headline feature is one download over several network connections at once. That needs two
# things this kit now does itself, with no dependency on anybody's application:
#   1. EVERY local address this machine can send from (`local_addresses`), and a check that says whether
#      they really are separate paths or just one router behind two cables (`adapter_report`);
#   2. an HTTP(S) client whose socket is BOUND to a chosen local address (`_bound_get`) - the standard
#      library's urlopen cannot do that, which is the whole reason the capability used to be delegated.
# The block queue in Job then hands a block to a stream that leaves by a different adapter, so the file
# genuinely arrives over more than one path.

_TETHER_HINTS: tuple[tuple[str, str], ...] = (
    ("192.168.42.", "an Android USB tether"),
    ("172.20.10.", "an iPhone USB/hotspot link"),
    ("192.168.137.", "a Windows mobile-hotspot / ICS link"),
)

#: WHICH ENUMERATION FAILED AND WHY. An empty address list must never be silent: the first version of
#: this code caught every exception into `pass`, a missing `import socket` made all of them raise, and
#: the report read "no adapter other than loopback exists here" - a BROKEN CHECK PRESENTED AS A FINDING.
#: Whatever fails is recorded here, `adapter_report` prints it, and a caller can see the difference
#: between "this machine has one path" and "this check could not look".
_ADDR_ERRORS: dict[str, str] = {}


def _ip_kind(ip: str) -> str:
    if ip.startswith("127."):
        return "loopback"
    if ip.startswith("169.254."):
        return "link-local (no address was handed out)"
    if ip.startswith("10.") or ip.startswith("192.168.") or re.match(r"^172\.(1[6-9]|2\d|3[01])\.", ip):
        return "private"
    return "public"


def _tether_hint(ip: str) -> str:
    for prefix, what in _TETHER_HINTS:
        if ip.startswith(prefix):
            return what
    return ""


def local_addresses() -> list[dict[str, Any]]:
    """Every IPv4 address this host can send from, and how it was found. No socket is opened.

    Three sources, because no single one is complete: the host's own name resolves to every NIC on
    Windows, a UDP connect to a routable address reveals the DEFAULT route's address without sending a
    packet, and `socket.if_nameindex()` names the interfaces. Which ADDRESS belongs to which interface
    is not something the standard library answers, so this reports the addresses and the names side by
    side rather than inventing a mapping.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    _ADDR_ERRORS.clear()

    def add(ip: str, source: str) -> None:
        if not ip or ip in seen:
            return
        seen.add(ip)
        out.append({"ip": ip, "kind": _ip_kind(ip), "from": source,
                    "likely_tether": _tether_hint(ip)})

    # THE LOOPBACK IS ALWAYS THERE, so it is stated rather than enumerated: a fetch bound to it can
    # only reach a server on this machine, and refusing it as "not an address this machine holds"
    # (measured: exactly that refusal, on a box whose hostname resolves to one real address) was a
    # wrong answer about a fact that needs no network to know.
    add("127.0.0.1", "the loopback interface, which every machine has")

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(str(info[4][0]), "the host's own name")
    except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
        _ADDR_ERRORS["the host's own name"] = f"{type(exc).__name__}: {exc}"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("1.1.1.1", 53))
        add(str(s.getsockname()[0]), "the default route (nothing was sent)")
        s.close()
    except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
        _ADDR_ERRORS["the default route"] = f"{type(exc).__name__}: {exc}"
    if not out:
        _ADDR_ERRORS["result"] = ("no address was found by any method - either this machine has no "
                                  "network at all, or this Python cannot enumerate it; the reason each "
                                  "method gave is above, so an empty list is never a silent finding")
    return out


def interface_names() -> list[str]:
    try:
        return [name for _idx, name in socket.if_nameindex()]
    except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
        _ADDR_ERRORS["interface names"] = f"{type(exc).__name__}: {exc}"
        return []


# `import socket` was MISSING from this module when the adapter code first landed: every enumeration
# raised NameError into an `except: pass`, and the report said "no adapter other than loopback exists
# here" - a check that could not look, read as a machine with one path. The import is here, and the
# module's own test parses the source and refuses any `socket.`/`ssl.` use without its import.


def _public_ip_through(bind_ip: str, timeout: float) -> str:
    """Ask a public address-finder which address it sees, leaving through ONE adapter. Tiny, but a
    third party sees this machine's public address, which the answer says out loud."""
    resp = _bound_get(PUBLIC_IP_URL, {"User-Agent": "lygo-console/adapters"}, timeout, bind_ip)
    try:
        if resp.status != 200:
            return ""
        body = resp.read_all()
        return re.sub(r"[^0-9a-fA-F:.]", "", body.decode("ascii", "ignore"))[:64]
    finally:
        resp.close()


def adapter_report(check_public: bool = False, timeout: float = 6.0) -> dict[str, Any]:
    """What paths exist, and - when asked - whether they are really separate.

    This is the measurement upstream's own FAQ describes and cannot do for you: two adapters that share
    one router share one public address, so parallel streams gain NOTHING (Wi-Fi plus Ethernet into the
    same router). Two adapters that leave by different public addresses are genuinely separate paths,
    and binding streams to different ones is where the extra bandwidth comes from.
    """
    addrs = local_addresses()
    usable = [a for a in addrs if a["kind"] != "loopback"]
    for a in addrs:
        if a["kind"] == "loopback":
            a["note"] = "the loopback interface: traffic to this machine only, never a path to the internet"
    contacted = ""
    if check_public and usable:
        for a in usable:
            try:
                a["public_ip"] = _public_ip_through(a["ip"], timeout)
            except Exception as exc:  # noqa: BLE001 - an adapter that cannot reach out is a reading
                a["public_ip"] = ""
                a["public_note"] = f"{type(exc).__name__}: {exc}"
        contacted = PUBLIC_IP_URL
        pubs = {a.get("public_ip") for a in usable if a.get("public_ip")}
        if len(pubs) <= 1:
            verdict = (f"{len(usable)} adapter(s) leave through ONE public address"
                       + (f" ({sorted(pubs)[0]})" if pubs else "")
                       + ": they share one path, so parallel streams cannot add bandwidth. Wi-Fi plus"
                         " Ethernet into the same router is exactly this case.")
        else:
            verdict = (f"{len(pubs)} distinct public addresses among {len(usable)} adapter(s): separate "
                       f"paths, so binding streams to different adapters can add bandwidth.")
    elif check_public:
        verdict = "no adapter other than loopback exists here, so there is nothing to compare"
    else:
        verdict = ("the public address was NOT checked: check=true sends one small request through each"
                   f" adapter to {PUBLIC_IP_URL}, and a third party would see this machine's public address")
    return {
        "ok": True,
        "at": _at(),
        "addresses": addrs,
        "interfaces": interface_names(),
        "enumeration_errors": dict(_ADDR_ERRORS),
        "usable": len(usable),
        "checked_public": bool(check_public and usable),
        "contacted": contacted,
        "verdict": verdict,
        "how_to_use": ("pass bind=[\"<one address per stream>\"] to a fetch and it opens one bound socket"
                       " per stream; pass extra mirrors in urls=[...] and the block queue spreads the file"
                       " over every source as well"),
    }


class RateLimiter:
    """A shared token bucket: the WHOLE job's budget, not one stream's each.

    One bucket sized at one second of bytes. Every stream takes from it before writing, so a limit is a
    limit on the job - which is what an operator asking for 5 MB/s means.
    """

    def __init__(self, mbps: float) -> None:
        self.rate = max(0.0, float(mbps)) * 1048576
        # THE BUCKET STARTS EMPTY. A full bucket at t=0 lets a job push a whole second of bytes
        # instantly: measured, a 3 MiB file capped at 1 MB/s finished in 2.3 s and its own record read
        # 1.33 MB/s - a ceiling that looks broken. Empty from the first byte, the ceiling holds.
        self.tokens = 0.0
        self.at = time.monotonic()
        self.lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.rate > 0

    def take(self, n: int) -> None:
        if not self.enabled:
            return
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.rate, self.tokens + (now - self.at) * self.rate)
                self.at = now
                if self.tokens >= n:
                    self.tokens -= n
                    return
                deficit = (n - self.tokens) / self.rate
            time.sleep(min(max(deficit, 0.005), 0.25))


class BoundResponse:
    """A response read off a socket this kit opened and BOUND to a chosen local address."""

    def __init__(self, status: int, headers: dict[str, str], fp: Any, sock: Any) -> None:
        self.status = int(status)
        self.headers = headers
        self._fp = fp
        self._sock = sock
        self._left: int | None = None
        self._chunked = "chunked" in (headers.get("transfer-encoding") or "").lower()
        if not self._chunked:
            try:
                self._left = int(headers.get("content-length") or 0) or None
            except ValueError:
                self._left = None
        self._chunk_left = 0

    def __enter__(self) -> "BoundResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def read(self, n: int) -> bytes:
        if n <= 0:
            return b""
        if self._chunked:
            return self._read_chunked(n)
        if self._left is None:
            buf = self._fp.read(n)
            if not buf:
                self._left = 0
            return buf
        if self._left <= 0:
            return b""
        buf = self._fp.read(min(n, self._left))
        self._left -= len(buf)
        return buf

    def read_all(self) -> bytes:
        got = bytearray()
        while True:
            buf = self.read(CHUNK)
            if not buf:
                return bytes(got)
            got += buf

    def _read_chunked(self, n: int) -> bytes:
        """Chunked bodies: read a hex size line, that many bytes, then the CRLF. Upstream servers send
        Content-Length for range requests, but a proxy in front of one can chunk it - and a reader that
        silently returns short reads would look like a truncated download."""
        out = bytearray()
        while len(out) < n:
            if self._chunk_left <= 0:
                line = self._fp.readline(128)
                if not line:
                    break
                try:
                    self._chunk_left = int(line.split(b";")[0].strip() or b"0", 16)
                except ValueError:
                    break
                if self._chunk_left == 0:
                    break
            want = min(n - len(out), self._chunk_left)
            buf = self._fp.read(want)
            if not buf:
                break
            out += buf
            self._chunk_left -= len(buf)
            if self._chunk_left == 0:
                self._fp.readline(2)
        return bytes(out)

    def close(self) -> None:
        for handle in (self._fp, self._sock):
            try:
                handle.close()
            except Exception:  # noqa: BLE001
                pass


def _bound_get(url: str, headers: dict[str, str], timeout: float, bind_ip: str,
               redirects: int = 3) -> BoundResponse:
    """GET over a socket bound to `bind_ip` - the one thing urlopen will not do. http and https.

    Redirects are followed by hand (up to three), because the socket has to be rebuilt bound to the same
    adapter; a redirected request that quietly left by another adapter would defeat the whole point.
    """
    parts = urllib.parse.urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise RuntimeError(f"{scheme or 'no'} scheme is not http(s)")
    host = parts.hostname or ""
    if not host:
        raise RuntimeError("no host in this url")
    port = parts.port or (443 if scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    sock = socket.create_connection((host, port), timeout=timeout, source_address=(bind_ip, 0))
    try:
        if scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        lines = [f"GET {path} HTTP/1.1", f"Host: {parts.netloc}", "Accept-Encoding: identity",
                 "Connection: close"]
        for k, v in headers.items():
            lines.append(f"{k}: {v}")
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        fp = sock.makefile("rb")
        head_line = fp.readline(65536).decode("latin-1").strip()
        bits = head_line.split()
        status = int(bits[1]) if len(bits) > 1 and bits[1].isdigit() else 0
        head: dict[str, str] = {}
        while True:
            line = fp.readline(65536)
            if not line or line in (b"\r\n", b"\n"):
                break
            text = line.decode("latin-1").strip()
            if ":" in text:
                k, v = text.split(":", 1)
                head[k.strip().lower()] = v.strip()
        if status in (301, 302, 303, 307, 308) and redirects > 0 and head.get("location"):
            target = urllib.parse.urljoin(url, head["location"])
            for handle in (fp, sock):
                try:
                    handle.close()
                except Exception:  # noqa: BLE001
                    pass
            return _bound_get(target, headers, timeout, bind_ip, redirects - 1)
        return BoundResponse(status, head, fp, sock)
    except Exception:
        try:
            sock.close()
        except Exception:  # noqa: BLE001
            pass
        raise


def probe_many(urls: list[str], timeout: float = PROBE_TIMEOUT_S) -> dict[str, Any]:
    """Probe every mirror and say whether they are interchangeable. Mixing mirrors is only safe when
    they hold the SAME bytes - same size, same ETag - so this is the check that has to come first."""
    rows: list[dict[str, Any]] = []
    for url in urls:
        got = probe(url, timeout=timeout)
        got["url"] = url
        rows.append(got)
    good = [r for r in rows if r.get("ok")]
    sizes = {r.get("bytes") for r in good}
    etags = {r.get("etag") for r in good if r.get("etag")}
    same = len(good) == len(rows) and len(sizes) == 1 and len(etags) <= 1
    if not rows:
        verdict = "no urls were given"
    elif not good:
        verdict = "none of these mirrors answered"
    elif same:
        verdict = (f"{len(good)} mirror(s) agree on {sorted(sizes)[0]} bytes"
                   + (f" and ETag {sorted(etags)[0]}" if etags else " (none of them sent an ETag, so a"
                      " mixed-mirror resume cannot be checked - it will be refused rather than guessed)")
                   + ": safe to spread one download over all of them")
    else:
        differ = ", ".join(f"{r.get('url')}={r.get('bytes')}" for r in good)
        verdict = (f"THESE MIRRORS DO NOT HOLD THE SAME FILE ({differ}): a fetch will refuse to mix them,"
                   " because a file stitched from two versions is wrong in a way nobody can see")
    ranges = all(r.get("ranges") for r in good) if good else False
    return {"ok": bool(good), "at": _at(), "mirrors": rows, "agree": bool(same), "ranges": ranges,
            "bytes": sorted(sizes)[0] if len(sizes) == 1 else 0, "verdict": verdict}


def probe(url: str, timeout: float = PROBE_TIMEOUT_S) -> dict[str, Any]:
    """One byte, then read the answer: does this server do ranges, how big is the file, which version?

    Upstream's own first step (`Range: bytes=0-0`): a `206` proves ranges and carries the total size in
    `Content-Range`, and `ETag`/`Last-Modified` are what a resume has to match.
    """
    ok, code, why = _url_ok(url)
    if not ok:
        return {"ok": False, "error": code, "why": why}
    req = urllib.request.Request(url.strip(), method="GET")
    req.add_header("Range", "bytes=0-0")
    req.add_header("User-Agent", "lygo-console/downloader")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(resp.status)
            head = _headers(resp)
            resp.read(1)
    except urllib.error.HTTPError as exc:
        if int(exc.code) in (404, 403, 401, 410):
            return {"ok": False, "error": f"http_{int(exc.code)}",
                    "why": f"the server answered HTTP {int(exc.code)} for this url"}
        return {"ok": False, "error": "http_error", "why": f"the server answered HTTP {int(exc.code)}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "unreachable", "why": f"{type(exc).__name__}: {exc}"}

    size = 0
    ranges = status == 206
    content_range = head.get("content-range") or ""
    if ranges and "/" in content_range:
        tail = content_range.rsplit("/", 1)[-1].strip()
        if tail.isdigit():
            size = int(tail)
    if not size:
        try:
            size = int(head.get("content-length") or 0)
        except ValueError:
            size = 0
    name = _filename_from_headers(head, url)
    return {
        "ok": True, "url": url.strip(), "ranges": ranges, "status": status, "bytes": size,
        "content_type": head.get("content-type") or "", "name": name,
        "etag": head.get("etag") or "", "last_modified": head.get("last-modified") or "",
        "why": ("the server answers range requests, so blocks can run in parallel" if ranges
                else f"the server answered HTTP {status} without partial content - one stream it is"),
    }


def _sidecar_path(target: Path) -> Path:
    return target.with_name(target.name + SIDECAR_SUFFIX)


def _local_addr_of(resp: Any) -> str:
    """Best effort: the local address the OS chose for THIS connection.

    Read defensively - private attributes of an `http.client` response differ between Pythons, and if
    they are not there the caller reports "os-chosen" instead of inventing an address. Getting it right
    is what makes "did the second adapter carry anything" answerable without binding.
    """
    for chain in (("fp", "raw", "_sock"), ("fp", "raw"), ("_fp", "raw", "_sock")):
        obj: Any = resp
        try:
            for step in chain:
                obj = getattr(obj, step)
            addr = obj.getsockname()
            if addr and addr[0]:
                return str(addr[0])
        except Exception:  # noqa: BLE001
            continue
    return ""


class Job:
    """One download. Blocks are handed out from one queue, so the fastest stream takes the most."""

    def __init__(self, url: str, target: Path, meta: dict[str, Any], streams: int, block: int,
                 total: int, done: dict[int, int] | None = None,
                 sources: list[str] | None = None, bind: list[str] | None = None,
                 limit_mbps: float = 0.0) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.url = url
        # EVERY SOURCE THIS ONE FILE MAY COME FROM. One url is the normal case; several are mirrors of
        # the SAME bytes (`probe_many` checks size and ETag before a job exists), and the block queue
        # spreads the work across all of them. Block N comes from sources[N % len(sources)], so the
        # spread is deterministic and a block retried later goes back to the mirror that owns it.
        self.sources = [str(u) for u in (sources or [url]) if str(u).strip()] or [url]
        # PER-STREAM SOURCE ADDRESSES: stream i leaves by bind[i % len(bind)]. Empty means "let the OS
        # choose", which is what a single-adapter machine wants and what every earlier test exercised.
        self.bind = [str(b).strip() for b in (bind or []) if str(b).strip()]
        self.limit_mbps = max(0.0, float(limit_mbps or 0.0))
        self.limiter = RateLimiter(self.limit_mbps)
        # Bytes per source address, so "did the second adapter actually carry anything" is answered with
        # a number instead of a claim. Keyed by the bind address, or "os-chosen" when unbound.
        self.by_adapter: dict[str, int] = {}
        self.target = target
        self.meta = meta
        self.streams = max(1, min(int(streams), max(STREAM_CHOICES)))
        self.block = max(65536, int(block))
        self.total = int(total)
        # A server that ignores ranges has exactly ONE block - the whole file - and no Range header is
        # sent for it. Measured: with a per-block Range header against a 200-answering server the stream
        # failed by name, so a perfectly downloadable file read as a broken engine.
        if not meta.get("ranges"):
            self.block = max(65536, self.total or 1)
            self.streams = 1
        self.done = dict(done or {})
        # A block is CLAIMED before it is fetched. Without this every stream takes `pending[0]`, and a
        # one-block file is downloaded once per stream (measured: 8 streams pulled the same 3060-byte
        # file 8 times and reported 800% - the total was right and the work was done eight times).
        self.claimed: set[int] = set()
        self.received = int(sum(self.done.values()))
        self.started = time.monotonic()
        self.started_at = _at()
        self.state = "running"
        self.error = ""
        self.why = ""
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

    # -- reporting -----------------------------------------------------------------------------
    def public(self) -> dict[str, Any]:
        with self.lock:
            elapsed = max(0.001, time.monotonic() - self.started)
            pct = round(100.0 * self.received / self.total, 2) if self.total else 0.0
            return {
                "id": self.id, "state": self.state, "url": self.url, "path": str(self.target),
                "bytes": self.total, "received": self.received, "percent": pct,
                "streams": self.streams, "block_mb": round(self.block / 1048576, 2),
                "blocks_done": len(self.done),
                "blocks_total": (max(1, -(-self.total // self.block)) if self.total else 0),
                "ranges": bool(self.meta.get("ranges")), "started_at": self.started_at,
                "seconds": round(elapsed, 1),
                "mbps": round((self.received / 1048576) / elapsed, 2) if self.received else 0.0,
                "error": self.error, "why": self.why or self.meta.get("why", ""),
                "sidecar": str(_sidecar_path(self.target)),
                # The sovereign half, reported as it really was: how many sources this file is coming
                # from, which local addresses the streams left by, what each of them carried, and the
                # ceiling if the operator set one.
                "sources": list(self.sources),
                "mirrors": len(self.sources),
                "bind": list(self.bind),
                "by_adapter": {k: v for k, v in self.by_adapter.items()},
                "limit_mbps": self.limit_mbps,
            }

    def _save(self) -> None:
        payload = {"signature": SIGNATURE, "at": _at(), "url": self.url, "name": self.target.name,
                   "sources": list(self.sources), "bind": list(self.bind),
                   "total": self.total, "etag": self.meta.get("etag") or "",
                   "last_modified": self.meta.get("last_modified") or "",
                   "block": self.block, "streams": self.streams,
                   "done": {str(k): int(v) for k, v in sorted(self.done.items())},
                   "received": self.received, "state": self.state}
        try:
            atomicio.atomic_write_text(_sidecar_path(self.target), json.dumps(payload, indent=2))
        except Exception:  # noqa: BLE001 - progress is a courtesy; losing it must not kill the job
            pass

    # -- the work ------------------------------------------------------------------------------
    def _blocks(self) -> list[int]:
        return list(range(0, max(1, -(-self.total // self.block)))) if self.total else [0]

    def _pump(self, resp: Any, status: int, bind_ip: str, offset: int, index: int) -> None:
        """Write one block through, counting it against the adapter it came over and the job's ceiling."""
        if self.meta.get("ranges") and self.total and int(status) != 206:
            raise RuntimeError(f"the server answered HTTP {int(status)} for a range request")
        key = bind_ip or _local_addr_of(resp) or "os-chosen"
        with open(self.target, "r+b") as fh:
            fh.seek(offset)
            got = 0
            while True:
                if self.cancel.is_set():
                    return
                buf = resp.read(CHUNK)
                if not buf:
                    break
                # The ceiling is taken BEFORE the write, so a limit is a limit on bytes that really move.
                self.limiter.take(len(buf))
                fh.write(buf)
                got += len(buf)
                with self.lock:
                    self.received += len(buf)
                    self.by_adapter[key] = self.by_adapter.get(key, 0) + len(buf)
            if self.total:
                with self.lock:
                    self.done[index] = got
                    self._save()
            else:
                with self.lock:
                    self.received = got

    def _stream(self, slot: int = 0) -> None:
        """One worker: claim a block, fetch it from one of the mirrors, leaving by one adapter."""
        bind_ip = self.bind[slot % len(self.bind)] if self.bind else ""
        while not self.cancel.is_set():
            with self.lock:
                if self.state == "failed":
                    return
                pending = [i for i in self._blocks() if i not in self.done and i not in self.claimed]
                if not pending:
                    return
                index = pending[0]
                self.claimed.add(index)
            source = self.sources[index % len(self.sources)]
            offset = index * self.block
            end = min(offset + self.block, self.total) - 1
            if self.total and end < offset:
                with self.lock:
                    self.done[index] = 0
                continue
            headers = {"User-Agent": "lygo-console/downloader"}
            if self.meta.get("ranges"):
                headers["Range"] = f"bytes={offset}-{end}"
            try:
                if bind_ip:
                    # A socket bound to a chosen local address: urlopen cannot pick the outgoing
                    # address, and picking it is the entire point of running two adapters at once.
                    with _bound_get(source, headers, 60.0, bind_ip) as resp:
                        self._pump(resp, resp.status, bind_ip, offset, index)
                else:
                    req = urllib.request.Request(source, method="GET")
                    for key, val in headers.items():
                        req.add_header(key, val)
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        self._pump(resp, int(resp.status), "", offset, index)
            except Exception as exc:  # noqa: BLE001
                if self.cancel.is_set():
                    return
                with self.lock:
                    self.state = "failed"
                    self.error = "stream_failed"
                    self.why = f"a stream failed: {type(exc).__name__}: {exc}"
                    self._save()
                self.cancel.set()
                return

    def _run(self) -> None:
        try:
            self.target.parent.mkdir(parents=True, exist_ok=True)
            size = self.total or self.meta.get("bytes") or 0
            # RESUMING MUST NOT TRUNCATE. `open(..., "wb")` zeroes the file before the first byte of the
            # resume arrives, which silently threw away every block the sidecar said was already here
            # (measured: the resume "succeeded" into a file of the right SIZE and the wrong BYTES). The
            # part-file is opened for update and only extended/shortened to the expected length.
            if self.done and self.target.is_file():
                with open(self.target, "r+b") as fh:
                    if size:
                        fh.truncate(size)
            else:
                with open(self.target, "wb") as fh:
                    if size:
                        fh.truncate(size)
            threads = [threading.Thread(target=self._stream, args=(i,), daemon=True,
                                        name=f"lygo-dl-{self.id}-{i}")
                       for i in range(self.streams)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            with self.lock:
                if self.cancel.is_set() and self.state == "running":
                    self.state = "cancelled"
                    self.why = "cancelled; the part-downloaded file and its progress record are kept"
                elif self.state == "running":
                    self.received = int(sum(self.done.values()))
                    short = [i for i in self._blocks() if i not in self.done]
                    if short:
                        # Every stream returned without the blocks all being there. The file on disk is
                        # short and the sidecar says which blocks are missing - that is a failure with a
                        # resume, not a finished download.
                        self.state = "failed"
                        self.error = "incomplete"
                        self.why = (f"{len(short)} of {len(self._blocks())} blocks did not arrive "
                                    f"({self.received} of {self.total} bytes); re-run the same url to resume")
                        self._save()
                    else:
                        self.state = "done"
                        self.why = (f"{len(self.done)} blocks on {self.streams} stream(s)"
                                    + ("" if self.meta.get("ranges") else " (this server does not do ranges: one stream)"))
                        try:
                            _sidecar_path(self.target).unlink()
                        except Exception:  # noqa: BLE001
                            pass
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                if self.state == "running":
                    self.state = "failed"
                    self.error = "could_not_write"
                    self.why = f"{type(exc).__name__}: {exc} (is the destination writable?)"

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"lygo-dl-{self.id}")
        self.thread.start()


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


def downloads_root() -> Path:
    return paths.WORKSPACE / "downloads"


def _target_for(name: str, out: str = "") -> tuple[Path | None, dict[str, Any]]:
    """Where a file lands, and whether that needed consent. The workspace is free; outside is not."""
    root = downloads_root()
    if out:
        p = Path(out)
        target = p if p.is_absolute() else (root / p)
    else:
        if not name:
            return None, {"ok": False, "error": "no_name",
                          "why": "the server named no file and no name was given - pass name="}
        target = root / name
    try:
        resolved = target.resolve()
        root_resolved = root.resolve()
    except Exception:  # noqa: BLE001
        return None, {"ok": False, "error": "bad_out", "why": f"{out!r} is not a usable path"}
    inside = str(resolved).lower().startswith(str(root_resolved).lower())
    if not inside:
        return resolved, {"outside": True, "root": str(root_resolved)}
    return resolved, {"outside": False, "root": str(root_resolved)}


def fetch(url: str, name: str = "", out: str = "", streams: int = 8, consent: bool = False,
          resume: bool = True, urls: list[str] | None = None, bind: list[str] | None = None,
          limit_mbps: float = 0.0) -> dict[str, Any]:
    """Start a download and answer AT ONCE with its job record (a job is a worker, not a request).

    `urls` adds MIRRORS of the same file: every one is probed first and they must agree on size (and on
    ETag when they send one), or this REFUSES BY NAME - a file stitched from two versions is wrong in a
    way nobody can see. `bind` names the local source address each stream leaves by (one per stream,
    cycled): that is how two adapters carry one file at the same time. `limit_mbps` caps the whole job.
    """
    sources = list(dict.fromkeys([str(u).strip() for u in [url] + list(urls or []) if str(u).strip()]))
    if not sources:
        return {"ok": False, "error": "no_url", "why": "this call needs a url"}
    for one in sources:
        ok, code, why = _url_ok(one)
        if not ok:
            return {"ok": False, "error": code, "why": f"{one}: {why}"}
    bind_list = [str(b).strip() for b in (bind or []) if str(b).strip()]
    if bind_list:
        known = {a["ip"] for a in local_addresses()}
        unknown = [b for b in bind_list if b not in known]
        if unknown:
            return {"ok": False, "error": "bind_unknown_address",
                    "why": f"{', '.join(unknown)} is not an address this machine holds, so no socket can leave by it",
                    "addresses": sorted(known),
                    "hint": "plexo action=adapters lists the addresses that exist here"}
    with _JOBS_LOCK:
        live = [j for j in _JOBS.values() if j.state == "running"]
        if len(live) >= MAX_JOBS:
            return {"ok": False, "error": "too_many_jobs",
                    "why": f"{len(live)} downloads are already running; {MAX_JOBS} at once is the cap here",
                    "jobs": [j.id for j in live]}
    meta = probe(sources[0])
    mirrors: list[dict[str, Any]] = []
    if len(sources) > 1:
        many = probe_many(sources)
        mirrors = many.get("mirrors") or []
        if not many.get("agree"):
            return {"ok": False, "error": "mirror_mismatch", "why": many["verdict"], "mirrors": mirrors}
        meta = next((r for r in mirrors if r.get("ok")), meta)
    if not meta.get("ok"):
        return {"ok": False, **{k: meta[k] for k in ("error", "why") if k in meta}}
    target, spot = _target_for(name or meta.get("name") or "", out)
    if target is None:
        return {"ok": False, **spot}
    if spot.get("outside") and not consent:
        return {"ok": False, "error": "consent_required",
                "why": f"{target} is outside this kit's own {spot['root']}",
                "hint": "repeat with consent=true, or give a bare name to land under workspace/downloads"}

    done: dict[int, int] = {}
    if resume and _sidecar_path(target).is_file():
        try:
            side = json.loads(atomicio.read_text(_sidecar_path(target)))
        except Exception:  # noqa: BLE001
            side = {}
        same_version = (str(side.get("etag") or "") == str(meta.get("etag") or "")
                        and str(side.get("last_modified") or "") == str(meta.get("last_modified") or ""))
        same_size = int(side.get("total") or 0) == int(meta.get("bytes") or 0)
        if side and same_version and same_size:
            done = {int(k): int(v) for k, v in (side.get("done") or {}).items()}
        elif side:
            return {"ok": False, "error": "changed_on_server",
                    "why": "this file's ETag or size changed since the part-download - resuming would mix two versions",
                    "hint": f"delete {target} and its {SIDECAR_SUFFIX} record to start over"}

    streams_n = int(streams) if int(streams) in STREAM_CHOICES else (
        min(STREAM_CHOICES, key=lambda s: abs(s - int(streams))) if streams else 8)
    if not meta.get("ranges"):
        streams_n = 1
    block = int(BLOCK_MB * 1048576)
    blocks_total = max(1, -(-int(meta.get("bytes") or 0) // block))
    # More streams than blocks is nothing but duplicate requests for the same block.
    streams_n = max(1, min(streams_n, blocks_total))
    job = Job(sources[0], target, meta, streams_n, block, int(meta.get("bytes") or 0), done,
              sources=sources, bind=bind_list, limit_mbps=limit_mbps)
    job.meta["resumed_blocks"] = len(done)
    job.meta["mirrors"] = sources
    with _JOBS_LOCK:
        _JOBS[job.id] = job
        for jid in [k for k, v in _JOBS.items() if v.state != "running"]:
            if jid != job.id:
                _JOBS.pop(jid, None)
    job.start()
    size = job.total
    size_text = (f"{size / 1048576:.2f} MB" if size >= 1048576 else f"{size} bytes") if size else "unknown size"
    over = f"over {job.streams} stream(s)"
    if len(sources) > 1:
        over += f" across {len(sources)} mirrors"
    if bind_list:
        over += f", leaving by {len(bind_list)} adapter(s): {', '.join(bind_list)}"
    if limit_mbps:
        over += f", capped at {limit_mbps:g} MB/s"
    return {"ok": True, **job.public(),
            "why": (f"started: {size_text} {over}"
                    if meta.get("ranges") else "started: this server does not do ranges, so it is one stream"),
            "resumed": bool(done), "mirrors": sources, "mirror_report": mirrors}


def jobs() -> list[dict[str, Any]]:
    with _JOBS_LOCK:
        return [j.public() for j in _JOBS.values()]


def job_status(job_id: str) -> dict[str, Any]:
    with _JOBS_LOCK:
        found = _JOBS.get(job_id)
    if found is None:
        return {"ok": False, "error": "job_not_found",
                "why": f"no job {job_id!r} in this session (a job lives as long as this console does)",
                "jobs": [j["id"] for j in jobs()]}
    return {"ok": True, **found.public()}


def wait_for(job_id: str, seconds: float = 60.0) -> dict[str, Any]:
    """Poll a job for a bounded time - the convenience a small model needs, with a real deadline."""
    deadline = time.monotonic() + max(0.5, float(seconds))
    while True:
        got = job_status(job_id)
        if not got.get("ok"):
            return got
        if got.get("state") != "running" or time.monotonic() >= deadline:
            return got
        time.sleep(0.75)


def cancel(job_id: str) -> dict[str, Any]:
    with _JOBS_LOCK:
        found = _JOBS.get(job_id)
    if found is None:
        return {"ok": False, "error": "job_not_found", "why": f"no job {job_id!r} in this session"}
    if found.state != "running":
        return {"ok": False, "error": "not_running", "why": f"job {job_id} is already {found.state}"}
    found.cancel.set()
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and found.state == "running":
        time.sleep(0.25)
    found._save()
    return {"ok": True, **found.public(), "why": "cancelled; what arrived is kept with its progress record"}


def cancel_all() -> dict[str, Any]:
    """Stop every running job - the console's strip needs one button that means 'stop downloading'."""
    with _JOBS_LOCK:
        live = [j for j in _JOBS.values() if j.state == "running"]
    if not live:
        return {"ok": False, "error": "nothing_running", "why": "no download is running in this session"}
    stopped = [cancel(j.id) for j in live]
    return {"ok": True, "stopped": [s.get("id") for s in stopped], "count": len(stopped),
            "why": f"cancelled {len(stopped)} download(s); what arrived is kept, with its progress record"}


def verify(target: str) -> dict[str, Any]:
    """Read the file back: a claim about a download is a claim about bytes on disk."""
    p = Path(target)
    if not p.is_absolute():
        p = downloads_root() / target
    if not p.is_file():
        return {"ok": False, "error": "missing", "why": f"{p} is not on disk"}
    got = p.stat().st_size
    return {"ok": True, "path": str(p), "bytes": got, "mb": round(got / 1048576, 2)}


# --------------------------------------------------------------------------------------------
# the honest read-out
# --------------------------------------------------------------------------------------------


def credit() -> dict[str, Any]:
    """Who wrote what, and under which licence. The engine here is OURS; Plexo is the reference."""
    return {
        "signature": SIGNATURE,
        "engine": {
            "what": "LYGO's own parallel-range downloader (Python standard library only)",
            "licence": "part of this kit (LYGO Sovereign License v3.0)",
            "why": "no install, no Node, no Electron, no administrator rights, and it works on a stick",
        },
        "reference": {
            "what": "Plexo - multi-network download manager (Electron, WebTorrent)",
            "author": UPSTREAM_AUTHOR,
            "licence": UPSTREAM_LICENCE,
            "upstream": UPSTREAM,
            "downloads": TUTORIAL,
            "note": ("The bridge below calls Plexo's own documented loopback API "
                     "(/ping, /add, /confirm on 19288-19290). No Plexo source is copied into this kit; "
                     "the engine is written from the published technique, and a fetched Plexo payload "
                     "carries its own MIT licence beside its binaries."),
        },
        # WHAT THIS KIT NOW DOES ITSELF. This list is the honest answer to "is it a sovereign build?" -
        # every line here was measured on this machine by its own test before it was written down.
        "what_this_kit_does_now_itself": [
            "several NETWORK ADAPTERS on one download: streams bound to different local addresses "
            "(action=adapters, fetch bind=[...]), with bytes-per-adapter reported afterwards",
            "the check that says whether two adapters are really two paths or one router "
            "(action=adapters check=true asks each adapter its own public address)",
            "several MIRRORS of one file at once (fetch urls=[...]), with a size and ETag agreement "
            "check that refuses to stitch two versions together",
            "a whole-job speed ceiling (fetch limit_mbps=...), and a job queue with a hard cap",
            "resume that refuses when the server's file changed, and a progress record beside the file",
        ],
        "what_plexo_still_does_that_this_kit_does_not": [
            "BitTorrent: magnets, .torrent files, peers, seeding - LYGO's engine is HTTP(S) only",
            "a desktop GUI with per-download graphs, and a browser extension",
            "scheduled downloads (start this at 02:00) - this kit starts a job when it is asked to",
        ],
    }


def state(cheap: bool = False) -> dict[str, Any]:
    """Everything the console can honestly say about downloads right now. Reads only.

    `cheap=True` is the BOOT-CHECK shape: files, config and our own records, with no socket opened and
    no process started (a module's `health` runs inside the host's `load`).
    """
    inst = installed()
    pn = pin()
    run = running(probe=not cheap)
    live = jobs()
    return {
        "signature": SIGNATURE,
        "at": _at(),
        "installed": inst,
        "pin": {"release": pn.get("release") or "", "present": bool(pn.get("present")),
                "upstream": pn.get("upstream") or UPSTREAM, "licence": pn.get("licence") or UPSTREAM_LICENCE},
        "app": run,
        "bridge": run.get("bridge") if not cheap else {"up": None, "state": "not checked", "why": "not checked"},
        "engine": {"ours": True, "block_mb": BLOCK_MB, "streams": list(STREAM_CHOICES),
                   "max_jobs": MAX_JOBS, "root": str(downloads_root()),
                   "root_exists": downloads_root().is_dir(),
                   # The sovereign capabilities, read off the machine rather than asserted by a flag:
                   # `adapters` is what this host can actually send from, so a card can say "two paths"
                   # or "one path, two cables" without anyone guessing.
                   "adapters": [a["ip"] for a in local_addresses() if a["kind"] != "loopback"],
                   "multi_adapter": True, "mirrors": True, "rate_limit": True},
        "jobs": live,
        "root": str(downloads_root()),
        "fetcher": inst["fetcher"],
        "tutorial": TUTORIAL,
        "credit": credit()["reference"],
    }


def running_downloads() -> list[dict[str, Any]]:
    return [j for j in jobs() if j.get("state") == "running"]


# --------------------------------------------------------------------------------------------
# the agent-facing limb
# --------------------------------------------------------------------------------------------

ACTIONS = ("status", "jobs", "job", "wait", "cancel", "cancel_all", "fetch", "probe", "verify",
           "hand_off", "bridge", "open", "stop", "pin", "credit", "doctor", "adapters")
PANEL_ACTIONS = ("status", "jobs", "cancel_all", "bridge", "open", "stop", "doctor", "pin")


def doctor() -> dict[str, Any]:
    """What is ready here, what is not, and the one command that changes it."""
    inst = installed()
    reach = bridge()
    root = downloads_root()
    checks = [
        {"check": "our engine (this kit's own downloader)", "ok": True,
         "detail": f"standard library only; blocks of {BLOCK_MB:g} MB, streams {list(STREAM_CHOICES)}"},
        {"check": "workspace/downloads", "ok": root.is_dir() or os.access(root.parent, os.W_OK),
         "detail": str(root)},
        {"check": "Plexo app on this kit", "ok": bool(inst["app_present"]),
         "detail": inst["app"] if inst["app_present"] else f"not installed - run {inst['fetcher']}"},
        {"check": "Plexo bridge answering", "ok": bool(reach.get("up") and reach.get("state") == "ok"),
         "detail": reach.get("why")},
    ]
    return {"ok": all(c["ok"] for c in checks[:2]), "checks": checks, "at": _at(),
            "engine": "ours", "upstream": UPSTREAM, "licence": UPSTREAM_LICENCE,
            "note": "the first two checks are what THIS kit can do on its own; the Plexo checks are about "
                    "the operator's own install, and the console works without them"}


def plexo_limb(args: dict[str, Any]) -> dict[str, Any]:
    """One action per call, a dict back, nothing raised - the same door the console's buttons use."""
    action = str(args.get("action") or "status").strip().lower()
    url = str(args.get("url") or "").strip()
    job_id = str(args.get("job") or "").strip()

    if action in ("status", "doctor", "pin", "credit", "bridge"):
        return {"action": action, "status": "ok",
                "status_data": state(),
                "doctor_data": doctor() if action == "doctor" else None,
                "pin_data": pin() if action == "pin" else None,
                "credit_data": credit() if action == "credit" else None,
                "bridge_data": bridge() if action == "bridge" else None}
    if action == "jobs":
        return {"action": action, "status": "ok", "jobs": jobs(), "root": str(downloads_root())}
    if action == "job":
        return {"action": action, **job_status(job_id)}
    if action == "wait":
        return {"action": action, **wait_for(job_id, float(args.get("seconds") or 60.0))}
    if action == "cancel":
        return {"action": action, **cancel(job_id)}
    if action == "cancel_all":
        return {"action": action, **cancel_all()}
    if action == "verify":
        return {"action": action, **verify(str(args.get("path") or args.get("name") or ""))}
    if action == "fetch":
        return {"action": action, **fetch(url, name=str(args.get("name") or ""),
                                          out=str(args.get("out") or ""),
                                          streams=int(args.get("streams") or 8),
                                          consent=bool(args.get("consent")),
                                          resume=bool(args.get("resume", True)),
                                          urls=[str(u) for u in (args.get("urls") or []) if str(u).strip()],
                                          bind=[str(b) for b in (args.get("bind") or []) if str(b).strip()],
                                          limit_mbps=float(args.get("limit_mbps") or 0.0))}
    if action == "probe":
        wanted = [str(u) for u in (args.get("urls") or []) if str(u).strip()]
        if wanted:
            return {"action": action, **probe_many(wanted)}
        return {"action": action, **probe(url)}
    if action == "adapters":
        return {"action": action,
                **adapter_report(check_public=bool(args.get("check")),
                                 timeout=float(args.get("seconds") or 6.0))}
    if action in ("hand_off", "add"):
        if not bool(args.get("consent")):
            return {"action": action, "ok": False, "error": "consent_required",
                    "why": "handing a link to Plexo starts a download on the operator's own connection",
                    "hint": "repeat with consent=true once he has said yes (or use action=fetch to "
                            "download it here with LYGO's own engine)"}
        if not bridge().get("up") or bridge().get("state") != "ok":
            boot = open_app()
            if not boot.get("ok"):
                return {"action": action, "ok": False, "error": boot.get("error") or "bridge_closed",
                        "why": boot.get("why"), "bridge": bridge(),
                        "hint": f"install Plexo from {TUTORIAL}, or download here with action=fetch"}
        offer = bridge_add(url, file_name=str(args.get("name") or ""))
        if not offer.get("ok"):
            return {"action": action, **{k: offer[k] for k in ("error", "why") if k in offer}, "bridge": offer.get("bridge")}
        done = bridge_confirm(offer["token"], int(offer["port"]))
        return {"action": action, "url": url, "handed_to": "plexo", "port": offer.get("port"),
                "version": offer.get("version") or "", "ok": bool(done.get("ok")),
                "error": "" if done.get("ok") else done.get("error"),
                "why": done.get("why"), "note": "Plexo owns it now; its queue, limits and schedule apply"}
    if action == "open":
        return {"action": action, **open_app()}
    if action == "stop":
        return {"action": action, **stop_app()}
    return {"action": action, "ok": False, "error": "unknown_action",
            "why": f"{action!r} is not one of this limb's actions",
            "actions": list(ACTIONS)}


def _run(argv: list[str], timeout: int = 60) -> dict[str, Any]:
    """Present for the fetcher path and for tests that stub it; never used to hide a failure."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              creationflags=_NOWINDOW)
        return {"ok": done.returncode == 0, "code": done.returncode,
                "stdout": done.stdout or "", "stderr": done.stderr or ""}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": -1, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}


if __name__ == "__main__":  # a hand-run probe: python src/plexo_tools.py status|bridge|credit
    what = (sys.argv[1] if len(sys.argv) > 1 else "status").strip().lower()
    if what == "bridge":
        print(json.dumps(bridge(), indent=2))
    elif what == "credit":
        print(json.dumps(credit(), indent=2))
    elif what == "doctor":
        print(json.dumps(doctor(), indent=2))
    elif what == "pin":
        print(json.dumps(pin(), indent=2))
    else:
        print(json.dumps(state(), indent=2))
