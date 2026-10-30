"""Public LYGO inference gateway — chat and pictures, P0, rate-limited, no disks/shell.

Run on the always-on node (stream PC) behind Caddy/HTTPS. Never load admin.json.
Requires --i-consent to bind beyond loopback.

Pictures (`POST /api/image`, measured 2026-09-23) run the kit's own media_tools limb on THIS
machine, one at a time, 3 per address per 10 minutes, off with `LYGO_PUBLIC_IMAGE=0`. The portal at
chatagent.ca generates through this route because the console itself is loopback-bound and sends no
CORS headers on purpose; the gateway's origin allowlist is what the portal is already on.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

try:  # kit ports (a stick runs 9651/11451); never hard-code the desktop pair here
    from paths import DEFAULT_PORT, LLAMA_PORT  # noqa: E402
except Exception:  # standalone copy of this file
    DEFAULT_PORT, LLAMA_PORT = 9641, 11441

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from p0_hook import PHYSICS_AVAILABLE, gate_output_window, gate_prompt  # noqa: E402

try:  # the body-refusal policy is shared with the kernel (defect 27); this file also ships alone
    from http_body import drain as drain_body  # noqa: E402
except Exception:  # a copy without http_body: same job, same bounds, inline

    def drain_body(handler: Any, declared: int) -> int:
        want, got = min(int(declared or 0), 1 << 20), 0
        try:
            while got < want:
                chunk = handler.rfile.read(min(65_536, want - got))
                if not chunk:
                    break
                got += len(chunk)
        except (OSError, ValueError):
            pass
        return got

ALLOW_ORIGINS = (
    "https://chatagent.ca",
    "https://www.chatagent.ca",
    "https://eternalhaven.ca",
    "https://www.eternalhaven.ca",
    "https://excavationpro.ca",
    "https://www.excavationpro.ca",
    "https://deepseekoracle.com",
    "https://www.deepseekoracle.com",
    "https://asiancoastline.com",
    "https://bpmfinder.ca",
    "https://deepseekoracle.github.io",
    f"http://127.0.0.1:{DEFAULT_PORT}",
    f"http://localhost:{DEFAULT_PORT}",
    "http://127.0.0.1:8080",
    # A machine-specific origin (a LAN dev box, a staging host) does not belong in shipped source:
    # it is a permanent hole nobody remembers. Set LYGO_PUBLIC_ORIGINS="https://a,https://b".
) + tuple(
    o.strip().rstrip("/")
    for o in os.environ.get("LYGO_PUBLIC_ORIGINS", "").split(",")
    if o.strip()
)
PUBLIC_SYSTEM = (
    "You are the public LYGO LLM portal. Assist the human. Never replace them. "
    "CANON is dual ledgers / Haven Star Chart. This chat is RESOURCE. "
    "No shell, no disk, no passwords. P0: refuse OS wipe and fabricated receipts. "
    "Δ9 champions are optional lenses (ARKOS, LYRΔ, …) if the user invokes one. "
    "Donate: PayPal.me/ExcavationPro. Arcade: https://chatagent.ca/games/"
)
WINDOW = 600
MAX_REQ = 24
MAX_CHARS = 4000
MAX_MSGS = 10
_hits: dict[str, deque[float]] = defaultdict(deque)

# ── Picture generation, the public way ─────────────────────────────────────────────────────────────
# The steward's ask (2026-09-23): the API portal has to be able to make the same pictures the PC
# console makes. The static portal page cannot reach the console (loopback-bound, no CORS on
# purpose), so this gateway - which chatagent.ca is already allowlisted against - is the route:
# it calls the very same limb (media_tools.image_generate) on the operator's machine.
#
# MEASURED on the steward's box 2026-09-23, SDXL-Turbo 1024x1024: 12.5 s on a free card, and 272-300 s
# on the CPU route the limb picks while the chat model holds the card. A five-minute HTTP request is
# a request that dies in a proxy, so this is a JOB: POST starts it, the page polls, the bytes come
# from a URL. `LYGO_PUBLIC_IMAGE=0` turns the whole route off.
IMAGE_MAX_REQ = 3          # renders per IP per window (a picture costs the operator minutes of card or CPU)
IMAGE_WINDOW = 600
IMAGE_MAX_PROMPT = 700
IMAGE_JOB_KEEP = 40        # finished job rows kept for the page to poll (a row is small: no images in it)
IMAGE_JOB_TTL = 1800.0     # seconds a finished row stays readable
IMAGE_MAX_BYTES = 32 << 20
IMAGE_ENABLED = os.environ.get("LYGO_PUBLIC_IMAGE", "1").strip().lower() not in ("0", "false", "no", "off")
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
_image_hits: dict[str, deque[float]] = defaultdict(deque)
_image_jobs: dict[str, dict[str, Any]] = {}
_image_store_lock = threading.Lock()   # guards the job rows (nothing slow ever runs under it)
_image_lock = threading.Lock()   # ONE render at a time: the card, the engine and the CPU are shared

# A public endpoint may not answer behind a quieter gate than the console it fronts. The deep
# layer missing is a degraded mode, not a free pass: it is marked in every verdict, warned about
# once, and reported on /health. LYGO_PUBLIC_REQUIRE_PHYSICS=1 refuses instead of degrading.
STRICT_PHYSICS = os.environ.get("LYGO_PUBLIC_REQUIRE_PHYSICS", "").strip().lower() in ("1", "true", "yes")
_degraded_warned = False


def _warn_degraded_once() -> None:
    global _degraded_warned
    if not _degraded_warned:
        _degraded_warned = True
        sys.stderr.write(
            "[public-gateway] P0 physics layer unavailable: answering with the policy regex only. "
            "Set LYGO_PUBLIC_REQUIRE_PHYSICS=1 to refuse instead of degrade.\n"
        )
        sys.stderr.flush()


def _cors_ok(origin: str) -> bool:
    o = (origin or "").rstrip("/")
    if o in ALLOW_ORIGINS:
        return True
    if o.startswith("https://") and o.endswith(".hf.space"):
        return True
    return False


def _rate(ip: str) -> bool:
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > WINDOW:
        q.popleft()
    if len(q) >= MAX_REQ:
        return False
    q.append(now)
    return True


def _rate_image(ip: str) -> bool:
    """Its own, much tighter limiter: one render costs the operator minutes of GPU or CPU."""
    now = time.time()
    q = _image_hits[ip]
    while q and now - q[0] > IMAGE_WINDOW:
        q.popleft()
    if len(q) >= IMAGE_MAX_REQ:
        return False
    q.append(now)
    return True


def _image_prune() -> None:
    """Drop aged-out and surplus job rows. The caller holds `_image_store_lock`."""
    now = time.time()
    for jid, row in list(_image_jobs.items()):
        if row.get("status") != "running" and now - float(row.get("finished") or 0) > IMAGE_JOB_TTL:
            _image_jobs.pop(jid, None)
    while len(_image_jobs) > IMAGE_JOB_KEEP:
        oldest = min(_image_jobs, key=lambda k: float(_image_jobs[k].get("started") or 0))
        _image_jobs.pop(oldest, None)


def _image_public(row: dict[str, Any]) -> dict[str, Any]:
    """The row the page polls: status, clock, and (when there is one) the URL of the bytes."""
    out = {
        "ok": True,
        "job": str(row.get("id") or ""),
        "status": str(row.get("status") or "running"),
        "started": round(float(row.get("started") or 0), 3),
        "elapsed": round(time.time() - float(row.get("started") or time.time()), 1),
        "seconds": row.get("seconds") or 0.0,
        "bytes": int(row.get("bytes") or 0),
        "size": int(row.get("size") or 0),
        "drawn_w": int(row.get("drawn_w") or 0),
        "drawn_h": int(row.get("drawn_h") or 0),
        "engine": str(row.get("engine") or ""),
        "route": str(row.get("route") or ""),
        "prompt": str(row.get("prompt") or "")[:120],
        "error": str(row.get("error") or ""),
        "hint": str(row.get("hint") or ""),
    }
    if row.get("status") == "done" and row.get("file"):
        out["url"] = "/api/image?file=" + str(row["file"])
    return out


def _image_file_path(name: str) -> Path | None:
    """A generated picture by file name - never a path from the caller.

    The name is a bare file name (no separators, no ..), it must end in a picture extension, it must
    live in this kit's workspace/images, and it must really be there.
    """
    n = str(name or "").strip()
    if not n or "/" in n or "\\" in n or n.startswith(".") or len(n) > 96:
        return None
    if not n.lower().endswith(_IMAGE_EXT):
        return None
    try:
        from paths import WORKSPACE  # noqa: PLC0415 - kit paths, resolved at call time

        base = (WORKSPACE / "images").resolve()
        p = (base / n).resolve()
        if p.parent != base or not p.is_file() or p.stat().st_size > IMAGE_MAX_BYTES:
            return None
    except Exception:
        return None
    return p


def _image_health() -> dict[str, Any]:
    """What the picture route can say for itself, without ever raising into a public answer."""
    if not IMAGE_ENABLED:
        return {"enabled": False, "note": "disabled by LYGO_PUBLIC_IMAGE=0"}
    out: dict[str, Any] = {"enabled": True, "ready": False, "engine": "", "default": "", "route": "",
                           "why": "", "busy": _image_lock.locked(), "limits": {
                               "max_per_window": IMAGE_MAX_REQ, "window_s": IMAGE_WINDOW}}
    try:
        import media_tools  # noqa: PLC0415

        st = (media_tools.media_status() or {}).get("image") or {}
        out["ready"] = bool(st.get("ready"))
        out["engine"] = str(st.get("cpu_exe") if not st.get("exe_on_disk") else st.get("exe") or "")
        out["default"] = str(st.get("default") or "")
        route, why = media_tools._route_for_a_render()
        out["route"] = route or "cuda"
        out["why"] = why
    except Exception as e:  # a health line is not allowed to be the thing that breaks
        out["why"] = type(e).__name__
    return out


def _image_run(jid: str, prompt: str, size: int = 0) -> None:
    """The render itself, off the request thread. `_image_lock` is already held by the starter."""
    t0 = time.time()
    row = _image_jobs.get(jid) or {}
    try:
        import media_tools  # noqa: PLC0415

        # 0 = the limb's own default (1024² on the classic checkpoint). A caller may ask for less.
        res = media_tools.image_generate(prompt, "", size, size) if size else media_tools.image_generate(prompt)
    except Exception as e:  # noqa: BLE001 - a public caller gets a named failure, never a traceback
        sys.stderr.write(f"[public-gateway] image limb raised: {type(e).__name__}: {e}\n")
        sys.stderr.flush()
        res = {"ok": False, "error": "limb_raised", "hint": type(e).__name__}
    if not isinstance(res, dict):
        res = {"ok": False, "error": "limb_bad_result", "hint": type(res).__name__}
    path = str(res.get("path") or "")
    eng = str(res.get("engine") or "")
    row.update({
        "status": "done" if res.get("ok") else "failed",
        "seconds": round(float(res.get("seconds") or (time.time() - t0)), 1),
        "bytes": int(res.get("bytes") or 0),
        "engine": eng,
        # The route the limb actually used, from its own result where it says so, else from the build
        # name it ran - the page shows this next to the picture, so it may not be a guess.
        "route": str(res.get("drawn_on") or ("cpu" if eng.endswith("cpu") else ("cuda" if eng else ""))),
        "file": Path(path).name if path else "",
        # The pixels the limb actually drew, from the limb's own result - asked for 512, drawn 512.
        "drawn_w": int(res.get("width") or 0),
        "drawn_h": int(res.get("height") or 0),
        "error": str(res.get("error") or ""),
        "hint": str(res.get("hint") or res.get("log_tail") or "")[:600],
        "finished": time.time(),
    })
    with _image_store_lock:
        _image_jobs[jid] = row
    try:
        _image_lock.release()
    except RuntimeError:
        pass


def _image_running() -> dict[str, Any]:
    """The render in flight, if there is one - so a second caller can watch it instead of being told no."""
    with _image_store_lock:
        for row in _image_jobs.values():
            if row.get("status") == "running":
                return dict(row)
    return {}


def _openai_chat(url: str, model: str, messages: list[dict[str, Any]], key: str) -> str:
    payload = json.dumps({"model": model, "messages": messages, "max_tokens": 384, "stream": False}).encode()
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=payload, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    return str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


class Handler(BaseHTTPRequestHandler):
    # Our own engine is the backend, full stop. There is no daemon path here: a gateway that can
    # reach another service is a gateway that will be pointed at one, and this kit is standalone by
    # design - it boots its own llama-server on its own port.
    backend = "local"
    openai_url = f"http://127.0.0.1:{LLAMA_PORT}/v1/chat/completions"
    openai_key = ""
    model = ""   # empty = resolve from our own registry when the caller names no model
    server_version = "LYGO-PublicGateway/1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _origin(self) -> str:
        return (self.headers.get("Origin") or "").strip()

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        origin = self._origin()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        if getattr(self, "close_connection", False):
            # Say it, don't just do it (defect 27): a caller that read Content-Length would treat
            # this socket as reusable and fail its next request on it.
            self.send_header("Connection", "close")
        if _cors_ok(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            # Chrome's Private Network Access: a page served from a PUBLIC origin (chatagent.ca)
            # fetching a LOCAL one (the loopback origin of this kit's own console port) is a
            # public -> private request, and Chrome
            # refuses it unless the preflight says so in so many words. Without this header the whole
            # "use the web portal to drive your own engine" path dies as a bare `Failed to fetch`
            # with no CORS message anywhere - MEASURED 2026-09-24, page https://chatagent.ca/portal/,
            # fetch <the console's loopback origin>/api/health, while the same request from curl
            # answered 200. The port itself is written down in one place, paths.py.
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: Any) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"))

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/health", "/api/health"):
            self._json(
                200,
                {
                    "ok": True,
                    "public": True,
                    "full_lygo": False,
                    "tools": False,
                    "model": self.model,
                    "physics": PHYSICS_AVAILABLE,
                    "gate": "physics" if PHYSICS_AVAILABLE else "regex_only",
                    # The portal reads this to say, honestly, whether a picture can be made here right
                    # now and on which route - the same question the console answers for itself.
                    "image": _image_health(),
                    "portal": "https://chatagent.ca/portal/",
                    "note": "Public chat and pictures only. Full limbs require a local LYGO LLM Console.",
                },
            )
            return
        if path == "/api/image":
            self._get_image()
            return
        if path in ("/v1/models", "/api/models"):
            self._json(200, {"object": "list", "data": [{"id": self.model, "object": "model"}]})
            return
        self._json(404, {"error": "not_found"})

    def _get_image(self) -> None:
        """`?job=` the status the page polls · `?file=` the bytes of a picture this gateway wrote."""
        qs = parse_qs(urlparse(self.path).query)
        origin = self._origin()
        if origin and not _cors_ok(origin):
            self._json(403, {"error": "origin"})
            return
        jid = (qs.get("job") or [""])[0].strip()
        if jid:
            with _image_store_lock:
                row = _image_jobs.get(jid)
                row = dict(row) if row else None
            if not row:
                self._json(404, {"error": "no_such_job",
                                 "hint": "the gateway restarted or the job aged out - start another one"})
                return
            self._json(200, _image_public(row))
            return
        name = (qs.get("file") or [""])[0]
        p = _image_file_path(name)
        if p is None:
            self._json(404, {"error": "no_such_picture",
                             "hint": "file= must name a picture in this machine's workspace/images"})
            return
        ctype = {".png": "image/png", ".webp": "image/webp"}.get(p.suffix.lower(), "image/jpeg")
        self._send(200, p.read_bytes(), ctype)

    def _drop_body(self, declared: int) -> None:
        """Read and discard a body we are about to refuse.

        MEASURED 2026-09-25: a refusal that never read the body reset the caller (WinError 10053).
        """
        if declared < 0:
            self.close_connection = True
            return
        if declared > 16_000:
            self.close_connection = True
        if declared > 0:
            drain_body(self, declared)

    def _post_image(self) -> None:
        """Start one render. The answer is a job handle, because a picture can take minutes.

        MEASURED 2026-09-23: 12.5 s on a free card, 272-300 s on the CPU route, so the caller polls
        `GET /api/image?job=`.
        """
        declared = (self.headers.get("Content-Length") or "").strip()
        try:
            n = int(declared)
        except ValueError:
            n = -1
        origin = self._origin()
        if origin and not _cors_ok(origin):
            self._drop_body(n)
            self._json(403, {"error": "origin"})
            return
        if not IMAGE_ENABLED:
            self._drop_body(n)
            self._json(503, {"error": "image_off",
                             "hint": "the operator turned the picture route off (LYGO_PUBLIC_IMAGE=0)"})
            return
        ip = (self.client_address or ("", 0))[0]
        if not _rate_image(ip):
            self._drop_body(n)
            self._json(429, {"error": "rate_limited",
                             "hint": "%d picture(s) per %d s from one address" % (IMAGE_MAX_REQ, IMAGE_WINDOW)})
            return
        if n < 0:
            self.close_connection = True
            self._json(400, {"error": "bad_request", "detail": "Content-Length: " + declared[:32]})
            return
        if n > 16_000:
            drain_body(self, n)
            self.close_connection = True
            self._json(413, {"error": "too_large", "hint": "a prompt, nothing else"})
            return
        raw = self.rfile.read(n) if n else b"{}"
        try:
            obj = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "bad_json"})
            return
        prompt = str(obj.get("prompt") or obj.get("text") or "").strip()[:IMAGE_MAX_PROMPT]
        if not prompt:
            self._json(400, {"error": "empty_prompt", "hint": 'send {"prompt": "..."}'})
            return
        try:
            size = int(obj.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size and not (256 <= size <= 1536):
            self._json(400, {"error": "bad_size", "hint": "size is square pixels, 256-1536; omit it for the default"})
            return
        gate = gate_prompt(prompt)
        if gate.get("verdict") == "QUARANTINE":
            self._json(451, {"error": "quarantine", "gate": gate})
            return
        health = _image_health()
        if not health.get("ready"):
            self._json(503, {"error": "no_image_engine",
                             "hint": "this machine has no picture engine wired - see media_status",
                             "image": health})
            return
        if not _image_lock.acquire(blocking=False):
            run = _image_running()
            self._json(429, {"error": "busy",
                             "hint": "one picture at a time on this machine - watch this job, or ask again",
                             "job": str(run.get("id") or ""),
                             "elapsed": round(time.time() - float(run.get("started") or time.time()), 1)})
            return
        jid = secrets.token_hex(8)
        row = {"id": jid, "status": "running", "prompt": prompt[:120], "size": size or 1024, "started": time.time(),
               "seconds": 0.0, "bytes": 0, "engine": "", "route": str(health.get("route") or ""),
               "error": "", "hint": "", "file": ""}
        with _image_store_lock:
            _image_jobs[jid] = row
            _image_prune()
        threading.Thread(target=_image_run, args=(jid, prompt, size), daemon=True).start()
        # The estimate is the measurement, not a promise: 12.5 s on a free card, ~300 s on the CPU route.
        self._json(202, {
            "ok": True, "job": jid, "status": "running", "size": size or 1024,
            "route": row["route"], "engine": health.get("engine") or "",
            "eta_s": 20 if row["route"] != "cpu" else 300,
            "poll": "/api/image?job=" + jid,
            "url_when_done": "/api/image?file=<name>",
        })

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/image":
            self._post_image()
            return
        if path not in ("/v1/chat/completions", "/api/chat"):
            self._json(404, {"error": "not_found"})
            return
        origin = self._origin()
        if origin and not _cors_ok(origin):
            self._json(403, {"error": "origin"})
            return
        ip = (self.client_address or ("", 0))[0]
        if not _rate(ip):
            self._json(429, {"error": "rate_limited"})
            return
        declared = (self.headers.get("Content-Length") or "0").strip()
        try:
            n = int(declared)
        except ValueError:
            # `int()` used to raise out of the handler: the connection closed with no answer at all
            # and the operator got a traceback on stderr. A malformed envelope is the caller's
            # problem, and it gets an answer like any other.
            self.close_connection = True
            self._json(400, {"error": "bad_request", "detail": "Content-Length: " + declared[:32]})
            return
        if n < 0:
            self.close_connection = True
            self._json(400, {"error": "bad_request", "detail": "Content-Length: " + str(n)})
            return
        if n > 48_000:
            # Refuse the body *and consume it*: answering while the caller is still writing resets
            # the rest of that write, so the caller never reads this reason (defect 27).
            drain_body(self, n)
            self.close_connection = True
            self._json(413, {"error": "too_large"})
            return
        raw = self.rfile.read(n) if n else b"{}"
        try:
            obj = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "bad_json"})
            return
        msgs_in = obj.get("messages") if isinstance(obj.get("messages"), list) else []
        msgs: list[dict[str, str]] = [{"role": "system", "content": PUBLIC_SYSTEM}]
        for m in msgs_in[-MAX_MSGS:]:
            if not isinstance(m, dict):
                continue
            role = m.get("role") if m.get("role") in {"user", "assistant"} else "user"
            _c = m.get("content")
            if isinstance(_c, list):
                # An OpenAI-style content list can carry an image part whose url is a base64 blob.
                # `str()` of that list pastes the blob into the prompt (truncated, so not even a
                # usable URL) and hands it to the gate as prose. A public caller gets the words plus
                # a marker that a picture came with them - never the blob.
                bits = []
                for _p in _c:
                    if isinstance(_p, dict):
                        if _p.get("type") == "image_url" or _p.get("image_url"):
                            bits.append("[image]")
                        elif _p.get("text"):
                            bits.append(str(_p["text"]))
                content = " ".join(bits)[:MAX_CHARS]
            else:
                content = str(_c or "")[:MAX_CHARS]
            msgs.append({"role": role, "content": content})
        user = " ".join(m["content"] for m in msgs if m["role"] == "user")[-MAX_CHARS:]
        gate = gate_prompt(user)
        if gate.get("reason") == "p0_import_failed":
            # p0_hook fails CLOSED when its physics layer cannot be imported. This endpoint used
            # to override that closed gate with a fabricated {"verdict": "ALLOW"} labelled
            # "p0_regex_only" - a label for a regex that never ran. The public surface therefore
            # answered with no gating whatsoever. Run the real policy stage instead and say out
            # loud that only the shallow layer is active.
            gate = {**gate_output_window(user), "reason": "p0_regex_only", "degraded": True, "physics": None}
            _warn_degraded_once()
            if STRICT_PHYSICS:
                self._json(503, {"error": "gate_unavailable", "gate": gate})
                return
        if gate.get("verdict") == "QUARANTINE":
            self._json(451, {"error": "quarantine", "gate": gate})
            return
        model = str(obj.get("model") or self.model or _selected_model())
        try:
            # "local" and "openai" are the same route: our own engine, OpenAI-compatible.
            text = _openai_chat(self.openai_url, model, msgs, self.openai_key)
        except Exception as e:
            # This endpoint is public: upstream internals (host, port, stack frames) belong in the
            # operator's stderr, not in the caller's response.
            sys.stderr.write(f"[public-gateway] upstream failed: {type(e).__name__}: {e}\n")
            sys.stderr.flush()
            self._json(502, {"error": "upstream"})
            return
        # The whole reply, not its first 8 KB: _policy also QUARANTINEs a payload over its
        # POLICY_MAX_CHARS, so slicing the head let an oversized answer through unchecked.
        if gate_output_window(text).get("verdict") == "QUARANTINE":
            text = "[output quarantined]"
        if path == "/api/chat":
            self._json(200, {"text": text, "public": True, "gate": gate, "model": model})
            return
        self._json(
            200,
            {
                "id": "lygo-public",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "model": model,
            },
        )


def _selected_model() -> str:
    """The console's own default brain, so a public gateway starts on a model this machine has."""
    import json

    reg = Path(__file__).resolve().parents[1] / "save" / "registry.json"
    try:
        return str(json.loads(reg.read_text(encoding="utf-8")).get("selected") or "qwen2.5-coder:7b")
    except Exception:
        return "qwen2.5-coder:7b"


def main() -> int:
    ap = argparse.ArgumentParser(description="Public LYGO chat gateway (no admin, no shell).")
    ap.add_argument("--port", type=int, default=9642)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--i-consent", action="store_true")
    ap.add_argument("--lan", action="store_true")
    # Our own engine is the backend. There is no daemon option: the kit never needs one, and offering
    # it is how a standalone system starts to depend on something outside itself.
    ap.add_argument("--backend", choices=("local", "openai"), default="local")
    # LYGO_PUBLIC_OPENAI_URL lets the launcher point this gateway at any OpenAI-shaped backend - the
    # console's own free-brain adapter, for instance - without a flag or a config-key change: the
    # .bat has no --openai-url in its argument list by design, so an env var is the one seam that
    # works for every copy. Default stays the kit's own engine.
    ap.add_argument("--openai-url",
                    default=os.environ.get("LYGO_PUBLIC_OPENAI_URL")
                    or f"http://127.0.0.1:{LLAMA_PORT}/v1/chat/completions")
    ap.add_argument("--model", default=os.environ.get("LYGO_PUBLIC_MODEL") or _selected_model())
    args = ap.parse_args()
    bind = args.bind
    if args.lan:
        if not args.i_consent:
            print("Refusing LAN bind without --i-consent", file=sys.stderr)
            return 2
        bind = "0.0.0.0"
    Handler.backend = args.backend
    Handler.openai_url = args.openai_url
    Handler.model = args.model
    httpd = ThreadingHTTPServer((bind, args.port), Handler)
    pic = _image_health()
    if not pic.get("enabled"):
        pic_note = "off (LYGO_PUBLIC_IMAGE=0)"
    elif not pic.get("ready"):
        pic_note = "NO ENGINE WIRED (media_status image.ready=false) - pictures will answer 503"
    else:
        pic_note = "ready on %s · route %s" % (pic.get("engine") or "engine", pic.get("route") or "cuda")
    print(f"LYGO public gateway {bind}:{args.port} backend={args.backend} model={args.model}  (chat + pictures)")
    print(f"  pictures : {pic_note} · {IMAGE_MAX_REQ} per {IMAGE_WINDOW} s per address, one render at a time")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
