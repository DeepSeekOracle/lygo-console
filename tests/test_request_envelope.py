"""Request-envelope regression tests (2026-09-18 sweep).

Three ways one request could take a handler down or lie about what happened:

  * `Content-Length: -1` reached ``rfile.read(-1)``, which reads *until the peer closes* - so a
    handful of such requests wedged a handler thread each and the console stopped answering.
  * a non-numeric Content-Length raised ValueError out of the handler and answered 500 for what
    is really a client error.
  * an over-limit body was silently emptied, so the endpoint blamed the payload (400 no_input)
    instead of saying 413.

Raw sockets are used on purpose: http.client will not send these envelopes.
"""
import json
import re
import socket
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import server  # noqa: E402

_state: dict = {}


def setUpModule() -> None:
    _state["auth"] = server.AUTH_REQUIRED
    _state["token"] = server.TOKEN
    server.AUTH_REQUIRED = False  # loopback console: keep the token out of the envelope's way
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    _state["httpd"] = httpd
    _state["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def tearDownModule() -> None:
    _state["httpd"].shutdown()
    _state["httpd"].server_close()
    server.AUTH_REQUIRED = _state["auth"]
    server.TOKEN = _state["token"]


def raw(request: bytes, timeout: float = 15.0) -> bytes:
    """Send a hand-built request; return status line, headers and the whole declared body."""
    with socket.create_connection(("127.0.0.1", _state["port"]), timeout=timeout) as s:
        s.sendall(request)
        out = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            out += chunk
            head, sep, body = out.partition(b"\r\n\r\n")
            if sep:
                m = re.search(rb"content-length:\s*(\d+)", head, re.I)
                if not m or len(body) >= int(m.group(1)):
                    break
        return out


def status_of(response: bytes) -> int:
    return int(response.split(b" ")[1])


def get_json(path: str, timeout: float = 15.0) -> dict:
    """GET a route and wait for the whole declared body."""
    with socket.create_connection(("127.0.0.1", _state["port"]), timeout=timeout) as s:
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode())
        out = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            out += chunk
            head, _, rest = out.partition(b"\r\n\r\n")
            m = re.search(rb"content-length:\s*(\d+)", head, re.I)
            if m and len(rest) >= int(m.group(1)):
                break
        return json.loads(out.split(b"\r\n\r\n", 1)[1])


class RequestEnvelopeTest(unittest.TestCase):
    def test_negative_content_length_is_rejected_instead_of_blocking(self) -> None:
        # The point of the fix: an answer comes back at all. Before it, the handler sat inside
        # rfile.read(-1) until the peer closed, so a loop of these requests wedged the console.
        r = raw(b"POST /api/session HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: -1\r\n\r\n")
        self.assertEqual(status_of(r), 400, r[:160])
        self.assertIn(b"bad_request", r)

    def test_non_numeric_content_length_is_400_not_500(self) -> None:
        r = raw(b"POST /api/session HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: abc\r\n\r\n")
        self.assertEqual(status_of(r), 400, r[:160])
        self.assertNotIn(b"handler_failed", r)

    def test_oversized_body_is_413(self) -> None:
        r = raw(b"POST /api/session HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 900000\r\n\r\n")
        self.assertEqual(status_of(r), 413, r[:160])

    def test_request_handler_has_a_socket_timeout(self) -> None:
        # Without it, a peer that connects and stalls pins a thread for as long as it likes.
        self.assertIsNotNone(server.Handler.timeout, "Handler.timeout must bound a stalled peer")
        self.assertGreater(float(server.Handler.timeout), 0.0)

    def test_workspace_reports_the_true_entry_count(self) -> None:
        body = get_json("/api/workspace")
        # These keys are what let the portal say "showing first 120 of N" instead of presenting a
        # silently cut listing as if it were the whole folder.
        for key in ("total", "truncated", "limit"):
            self.assertIn(key, body, sorted(body))
        self.assertIsInstance(body["total"], int)
        self.assertEqual(body["truncated"], body["total"] > len(body["entries"]))


if __name__ == "__main__":
    unittest.main()
