"""OpenAI-compatible bridge to the local OpenCode CLI, for the console's API menu.

The Forge Manager shells `opencode run` per job; this exposes the same free models over HTTP so the
console (and its Hermes brain, which expects a base URL) can boot them like any other API.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import cloud_api  # noqa: E402

HOST, PORT = "127.0.0.1", 18901
MODELS = getattr(cloud_api, "OPENCODE_MODELS", [])


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep the console log readable
        sys.stderr.write("[opencode-bridge] " + (fmt % args) + "\n")

    def _send(self, code, payload, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            data = {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "opencode"} for m in MODELS]}
            self._send(200, json.dumps(data).encode())
            return
        self._send(404, b'{"error":"not_found"}')

    def do_POST(self):
        if "/chat/completions" not in self.path:
            self._send(404, b'{"error":"not_found"}')
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, b'{"error":"bad_json"}')
            return
        model = str(body.get("model") or (MODELS[0] if MODELS else ""))
        code, data, ctype = cloud_api._opencode_chat(model, body, timeout=180)
        if code >= 400:
            sys.stderr.write("[opencode-bridge] %s FAILED %s model=%s msgs=%s\n" % (
                code, data[:400].decode("utf-8", "replace"), model, len(body.get("messages") or [])))
        self._send(code, data, ctype)


if __name__ == "__main__":
    print(f"opencode bridge on http://{HOST}:{PORT}/v1 with {len(MODELS)} models", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
