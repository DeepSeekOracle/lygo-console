#!/usr/bin/env python3
"""film_web - serve the offline FilmCraft WebAssembly editor out of this kit.

Why this file exists at all: the console's own HTTP server deliberately serves ONE html document,
assembled at "/" with the token and the system label spliced in, and its /static route refuses
subpaths and refuses to hand out any .html. That is the right rule for the console, and it means the
browser editor cannot live behind it. So the editor gets its own tiny static server, on its own
loopback port, started by the FilmCraft card's button and stopped by the same card - the same shape
as tools/doorbell.py, which exists for the same reason on the console's behalf.

What it serves is the `filmcraft-web-<version>.zip` payload from FilmCraft's own release: a static
site (index.html, wasm-bindgen glue, filmcraft_web_bg.wasm, an audio worklet) that runs the SAME
engine and the SAME UI as the desktop app, in the browser, with media that never leave the machine.
Designed by the release for any static host; hosted here by the kit.

What it deliberately does NOT do:

* no writes, no state, no upload: GET and HEAD only, everything else is refused by name;
* loopback only, never 0.0.0.0 - this is the operator's own machine;
* no path escapes: a request is joined to the root and the result must still be inside it;
* no directory listing and no content negotiation: a directory means index.html or 404;
* it never raises into a caller - a bad request is answered, not crashed on;
* it is not a general-purpose file server: the root must hold index.html and the payload, or it
  refuses to start, so a misconfigured port can never expose some other folder.

Usage (the console starts it this way, and so can you):

    python tools/film_web.py --port 9643 --root utilities/filmcraft/web
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import posixpath
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

MIME = {
    ".wasm": "application/wasm",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".json": "application/json",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
    ".md": "text/plain",
}
TEXTUAL = (".html", ".htm", ".js", ".mjs", ".css", ".json", ".txt", ".md", ".svg")
_status = {"requests": 0, "refused": 0, "bytes": 0}


def _ctype(path: Path) -> str:
    ext = path.suffix.lower()
    base = MIME.get(ext) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    if ext in TEXTUAL and "charset" not in base:
        return base + "; charset=utf-8"
    return base


def _cache(path: Path) -> str:
    """The payload's own rule (see its _headers / HOSTING.md): hashed assets forever, page never."""
    if path.name.lower() == "index.html":
        return "no-cache"
    if path.suffix.lower() in (".wasm", ".js", ".mjs"):
        return "public, max-age=31536000, immutable"
    return "no-cache"


class FilmHandler(BaseHTTPRequestHandler):
    server_version = "lygo-film_web"
    protocol_version = "HTTP/1.1"
    root: Path = Path(".")

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - the base class' name
        # One compact line per request: enough for the log beside the kit, never a wall of text.
        try:
            sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))
            sys.stdout.flush()
        except Exception:
            pass

    def _resolve(self, raw_path: str) -> Path | None:
        path = urlparse(raw_path).path
        path = unquote(path)
        if "\x00" in path or "\\" in path:
            return None
        clean = posixpath.normpath(path)
        if clean.startswith("/..") or clean == ".." or clean.startswith("../"):
            return None
        parts = [p for p in clean.split("/") if p not in ("", ".")]
        target = self.root.joinpath(*parts) if parts else self.root / "index.html"
        try:
            resolved = target.resolve()
        except OSError:
            return None
        try:
            if not str(resolved).startswith(str(self.root.resolve())):
                return None
        except OSError:
            return None
        if resolved.is_dir():
            resolved = resolved / "index.html"
        return resolved

    def _send_file(self, path: Path, head_only: bool) -> None:
        try:
            body = b"" if head_only else path.read_bytes()
            size = path.stat().st_size
        except OSError:
            self._refuse(404, "missing")
            return
        self.send_response(200)
        self.send_header("Content-Type", _ctype(path))
        self.send_header("Content-Length", str(size))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", _cache(path))
        # https://127.0.0.1 counts as a secure context, so WebGPU is available; the release needs no
        # cross-origin isolation (it uses no SharedArrayBuffer) - see its HOSTING.md.
        self.end_headers()
        if not head_only:
            try:
                self.wfile.write(body)
                _status["bytes"] += len(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _refuse(self, code: int, why: str) -> None:
        _status["refused"] += 1
        body = (why + "\n").encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve(self, head_only: bool) -> None:
        _status["requests"] += 1
        target = self._resolve(self.path)
        if target is None:
            self._refuse(403, "refused: that path is outside the folder this server was given")
            return
        if not target.is_file():
            self._refuse(404, "missing")
            return
        self._send_file(target, head_only)

    def do_GET(self) -> None:  # noqa: N802 - the base class' name
        self._serve(False)

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve(True)

    def do_POST(self) -> None:  # noqa: N802
        self._refuse(405, "refused: this server serves files, it does not accept anything")

    do_PUT = do_DELETE = do_PATCH = do_POST


def main() -> int:
    ap = argparse.ArgumentParser(description="Serve the kit's offline FilmCraft web editor on loopback.")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--quiet", action="store_true", help="no per-request lines")
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(f"film_web: no such folder: {root}", file=sys.stderr)
        return 2
    if not (root / "index.html").is_file():
        print(f"film_web: {root} holds no index.html, so there is no editor to serve here", file=sys.stderr)
        return 2
    if not (root / "filmcraft_web_bg.wasm").is_file():
        print(f"film_web: {root} holds no filmcraft_web_bg.wasm - the payload is incomplete", file=sys.stderr)
        return 2

    FilmHandler.root = root.resolve()
    if args.quiet:
        FilmHandler.log_message = lambda self, fmt, *a: None  # type: ignore[assignment]

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", int(args.port)), FilmHandler)
    except OSError as exc:
        print(f"film_web: cannot listen on 127.0.0.1:{args.port} - {exc}", file=sys.stderr)
        return 3
    httpd.daemon_threads = True

    def bye(signum, frame):  # noqa: ANN001 - signal handler
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, bye)
        except (ValueError, OSError):
            pass

    # The launcher reads this line to know the port is really up; it is not decoration.
    print(f"film_web serving {FilmHandler.root} on http://127.0.0.1:{args.port}/", flush=True)
    try:
        httpd.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"film_web: stopped by {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            httpd.server_close()
        except Exception:
            pass
    print(f"film_web stopped (served {_status['requests']} requests, refused {_status['refused']})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
