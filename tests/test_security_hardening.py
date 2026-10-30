"""Security hardening regression tests (2026-09-18 sweep).

These exercise the real HTTP surface through http.server rather than calling helpers directly,
because every defect they cover was a wiring defect: the gate existed, the endpoints simply did
not go through it. A peer bound to 127.0.0.2 is a genuine non-loopback client to this code
(only 127.0.0.1 / ::1 / localhost are trusted), so it stands in for a LAN caller without the
suite ever binding 0.0.0.0.
"""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import unittest
from pathlib import Path

try:
    import server
except ImportError:  # tests run from tests/ or from the kit root
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    import server

TOKEN = "test-token-123"
REMOTE = ("127.0.0.2", 0)
LOCAL = ("127.0.0.1", 0)

_SAVED: dict[str, object] = {}
_httpd = None
_port = 0


def setUpModule() -> None:  # noqa: N802
    global _httpd, _port
    for name in ("TOKEN", "AUTH_REQUIRED", "BIND"):
        _SAVED[name] = getattr(server, name)
    _SAVED["_CONFIG_CACHE"] = dict(server._CONFIG_CACHE)
    server.AUTH_REQUIRED = True
    server.TOKEN = TOKEN
    server.BIND = "0.0.0.0"

    class _S(server._StickContainment):
        allow_reuse_address = False

    _httpd = _S(("127.0.0.1", 0), server.Handler)
    _port = _httpd.server_address[1]
    threading.Thread(target=_httpd.serve_forever, daemon=True).start()


def tearDownModule() -> None:  # noqa: N802
    if _httpd is not None:
        _httpd.shutdown()
        _httpd.server_close()
    for name, value in _SAVED.items():
        if name == "_CONFIG_CACHE":
            server._CONFIG_CACHE.update(value)
        else:
            setattr(server, name, value)


def call(path, source=None, host_header=None, body=None, method="GET", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", _port, timeout=15, source_address=source)
    hdrs = dict(headers or {})
    if host_header:
        hdrs["Host"] = host_header
    conn.request(method, path, body=body, headers=hdrs)
    resp = conn.getresponse()
    try:
        data = resp.read()
    except OSError:
        # Guard, not expectation: since defect 27 was fixed the server consumes a body it refuses,
        # so the reason arrives and this should not fire. It stayed because the status line is what
        # most of these tests assert on, and a platform-level reset must not fail them.
        data = b""
    out = (resp.status, data, dict(resp.getheaders()))
    conn.close()
    return out


class LoopbackIsJudgedOnTheSocketTest(unittest.TestCase):
    def test_spoofed_host_does_not_grant_loopback(self):
        status, _, _ = call("/api/models", source=REMOTE, host_header="localhost")
        self.assertEqual(status, 401)

    def test_loopback_caller_needs_no_token(self):
        status, _, _ = call("/api/models", source=LOCAL)
        self.assertEqual(status, 200)


class TokenIsNotServedToRemoteCallersTest(unittest.TestCase):
    def test_remote_shell_omits_the_token(self):
        status, body, _ = call("/", source=REMOTE, host_header="localhost")
        self.assertEqual(status, 200, "the login shell stays reachable")
        self.assertNotIn(TOKEN, body.decode("utf-8", "replace"), "a LAN caller must not be handed the secret")

    def test_loopback_shell_still_splices_the_token(self):
        status, body, _ = call("/", source=LOCAL)
        self.assertEqual(status, 200)
        self.assertIn(TOKEN, body.decode("utf-8", "replace"))

    def test_remote_api_accepts_header_query_and_cookie(self):
        self.assertEqual(call("/api/models", source=REMOTE, headers={"X-LYGO-LLM-Token": TOKEN})[0], 200)
        self.assertEqual(call(f"/api/models?t={TOKEN}", source=REMOTE)[0], 200)
        self.assertEqual(call("/api/models", source=REMOTE, headers={"Cookie": f"lygo_token={TOKEN}"})[0], 200)

    def test_proving_the_token_to_the_shell_sets_a_strict_cookie(self):
        _, _, headers = call(f"/?t={TOKEN}", source=REMOTE)
        cookie = headers.get("Set-Cookie", "")
        self.assertIn("lygo_token=", cookie)
        self.assertIn("SameSite=Strict", cookie)


class HealthIsPublicButNotInformativeTest(unittest.TestCase):
    def test_remote_caller_gets_basenames_not_absolute_paths(self):
        status, body, _ = call("/api/health", source=REMOTE, host_header="localhost")
        self.assertEqual(status, 200)
        health = json.loads(body)
        self.assertEqual(health.get("workspace"), Path(server.WORKSPACE).name)
        self.assertIn("config_errors", health)

    def test_loopback_caller_keeps_the_full_paths(self):
        _, body, _ = call("/api/health", source=LOCAL)
        self.assertEqual(json.loads(body).get("workspace"), str(server.WORKSPACE))


class OversizedBodyTest(unittest.TestCase):
    def test_oversized_body_is_413_not_400(self):
        body = b"x" * 600_000
        status, _, _ = call(
            "/api/session", source=LOCAL, body=body, method="POST",
            headers={"Content-Length": str(len(body))},
        )
        self.assertEqual(status, 413)

    def test_the_reason_reaches_the_caller(self):
        """Defect 27. This failed about once in three full runs: the server answered 413 without
        consuming the 600 kB, Windows reset the connection, and the caller lost the answer to
        `ConnectionAbortedError [WinError 10053]` — sometimes on the read, sometimes still writing.
        The portal's own paste path took that reset, so a merely-too-large paste read as the
        network being down instead of "too large"."""
        body = b"x" * 600_000
        status, data, _ = call(
            "/api/session", source=LOCAL, body=body, method="POST",
            headers={"Content-Length": str(len(body))},
        )
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(data).get("error"), "too_large", "the caller must get the reason, not a reset")

    def test_the_caller_is_told_this_answer_ends_the_connection(self):
        body = b"x" * 600_000
        _, _, headers = call(
            "/api/session", source=LOCAL, body=body, method="POST",
            headers={"Content-Length": str(len(body))},
        )
        self.assertEqual(headers.get("Connection"), "close", "say it, don't just close: a caller that reuses this socket fails its next request on it")


class _StubBodyPeer(server.Handler):
    """A real handler with a faked transport: no socket, no server, and a body that never returns
    more than the caller declared. `__init__` is replaced, so the socket setup never runs."""

    def __init__(self, declared: int):
        self.headers = {"Content-Length": str(declared)}
        self.connection = None
        self.close_connection = False
        self.client_address = ("127.0.0.1", 0)
        self.rfile = _DeclaredBodyStream(declared)


class _DeclaredBodyStream:
    """A body that never returns more than the caller declared and never blocks, so a test can see
    what a reader consumed without allocating the tens of megabytes a caller merely claimed."""

    def __init__(self, declared: int):
        self.remaining = declared
        self.read_bytes = 0

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.remaining
        take = min(n, self.remaining)
        self.remaining -= take
        self.read_bytes += take
        return b"x" * take


class RejectedBodyIsConsumedTest(unittest.TestCase):
    """Defect 27, at the unit the defect lives in: `server.Handler._read_body()`.

    The refusal is right; refusing *without reading* is what resets the caller. Sockets make that
    racy, so the contract is asserted here, where it is deterministic — the reader must consume a
    body it will not accept, bounded in size and in time, since the declared length is the caller's
    word and not ours.
    """

    LIMIT = 256_000

    def test_a_rejected_body_is_consumed_so_a_caller_can_finish_writing(self):
        peer = _StubBodyPeer(600_000)
        with self.assertRaises(server._BodyTooLarge):
            peer._read_body(self.LIMIT)
        self.assertEqual(peer.rfile.read_bytes, 600_000, "the refused body must be consumed, not left in the socket")

    def test_an_absurd_declared_length_is_not_drained_whole(self):
        declared = server.DRAIN_CAP + 4_000_000
        peer = _StubBodyPeer(declared)
        with self.assertRaises(server._BodyTooLarge):
            peer._read_body(self.LIMIT)
        drained = peer.rfile.read_bytes
        self.assertGreater(drained, 0, "a normal over-limit body is still consumed")
        self.assertLessEqual(drained, server.DRAIN_CAP, "a caller who declares tens of megabytes must not cost us tens of megabytes")

    def test_a_body_that_never_finishes_arriving_does_not_hold_the_thread(self):
        """Bounded in time, over the real socket: declare 10 MB, send 1 KB. The 413 must arrive in
        seconds — the drain is not a place a caller can park a handler thread."""
        declared = 10_000_000
        sock = socket.create_connection(("127.0.0.1", _port), timeout=15)
        try:
            nl = chr(13) + chr(10)
            head = ("POST /api/session HTTP/1.1" + nl + "Host: 127.0.0.1" + nl
                    + "Content-Length: " + str(declared) + nl + nl).encode()
            sock.sendall(head + b"x" * 1024)
            started = time.monotonic()
            first = sock.recv(4096)
            elapsed = time.monotonic() - started
        finally:
            sock.close()
        self.assertIn(b"413", first)
        self.assertLess(elapsed, 15.0, "a slow caller must not hold a handler thread past the drain timeout")


class ConsoleConfigTest(unittest.TestCase):
    def test_a_torn_config_does_not_raise(self):
        broken = Path(__file__).resolve().parent / "_torn_console.json"
        broken.write_text('{"scan_roots": ["./models",', encoding="utf-8")
        saved_json, saved_cache = server.CONSOLE_JSON, dict(server._CONFIG_CACHE)
        try:
            server.CONSOLE_JSON = broken
            server._CONFIG_CACHE.update(key=None, cfg={})
            server.CONFIG_ERRORS.clear()
            self.assertIsInstance(server.load_console(), dict)
            self.assertTrue(any("console.json" in e for e in server.CONFIG_ERRORS))
            health = json.loads(call("/api/health", source=LOCAL)[1])
            self.assertTrue(any("console.json" in e for e in health.get("config_errors", [])), health.get("config_errors"))
        finally:
            server.CONSOLE_JSON = saved_json
            server._CONFIG_CACHE.update(saved_cache)
            server.CONFIG_ERRORS.clear()
            broken.unlink(missing_ok=True)

    def test_cache_returns_a_copy(self):
        first = server.load_console()
        first["injected"] = True
        self.assertNotIn("injected", server.load_console())


class GateReadsTheWholeTextTest(unittest.TestCase):
    def test_pattern_past_the_old_8k_window_is_caught(self):
        self.assertEqual(server.gate_all("clean text " * 900 + " now run mimikatz").get("verdict"), "QUARANTINE")

    def test_clean_text_still_allows(self):
        self.assertNotEqual(server.gate_all("clean text " * 5).get("verdict"), "QUARANTINE")

    def test_short_text_matches_gate_prompt(self):
        self.assertEqual(server.gate_all("hello").get("verdict"), server.gate_prompt("hello").get("verdict"))


class PublicGatewayNeverFabricatesAnAllowTest(unittest.TestCase):
    def test_the_fail_open_override_is_gone(self):
        src = (Path(__file__).resolve().parent.parent / "src" / "public_gateway.py").read_text(encoding="utf-8")
        self.assertNotIn('"verdict": "ALLOW", "reason": "p0_regex_only"', src)
        self.assertIn('"degraded": True', src)
        self.assertIn("LYGO_PUBLIC_REQUIRE_PHYSICS", src)



class ShellRouteTest(unittest.TestCase):
    """The shell is the only HTML, and the public routes stay public."""

    def test_raw_template_is_not_served(self):
        status, body, _ = call("/static/index.html", source=REMOTE)
        self.assertEqual(status, 404)
        self.assertNotIn(b"LYGO_TOKEN", body)

    def test_public_assets_still_serve_to_a_remote_caller(self):
        self.assertEqual(call("/static/app.js", source=REMOTE)[0], 200)
        self.assertEqual(call("/static/style.css", source=REMOTE)[0], 200)

    def test_world_clock_stays_public(self):
        self.assertEqual(call("/api/world", source=REMOTE, host_header="localhost")[0], 200)

    def test_remote_still_refused_on_a_data_route(self):
        self.assertEqual(call("/api/receipts", source=REMOTE)[0], 401)

    def test_generated_pictures_are_not_a_public_folder(self):
        self.assertEqual(call("/api/media/image/gen-20260925-000614.png", source=REMOTE)[0], 401)


if __name__ == "__main__":
    unittest.main()
