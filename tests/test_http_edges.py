"""HTTP edge tests (2026-09-20 sweep).

Two things a client could hit that nothing covered:

  * OPTIONS was answered **501 with an HTML page**. `BaseHTTPRequestHandler` has no `do_OPTIONS`,
    so the stdlib's "Unsupported method" page came back - for a method the server does implement,
    to a client that asked the API a JSON question. A browser preflight and a curl capability
    probe both got markup where every other answer is JSON.
  * the token was looked up in the exact case the client sent it in (`token_from_request`), while
    header NAMES are case-insensitive (RFC 9110) and clients canonicalise them: urllib title-cases
    every part, HTTP/2 lowercases them all, a proxy may do either. The same valid token answered
    401 through `X-Lygo-Llm-Token` and 200 through `Authorization: Bearer`.

OPTIONS is tested over a real socket, because the status line and headers ARE the answer. The
token is tested at the function: a loopback request bypasses the gate by design, so a live test
here would prove nothing.
"""
import socket
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import auth  # noqa: E402
import server  # noqa: E402

_state: dict = {}


def setUpModule() -> None:
    _state["auth"] = server.AUTH_REQUIRED
    server.AUTH_REQUIRED = False  # the envelope is not what these tests are about
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    _state["httpd"] = httpd
    _state["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def tearDownModule() -> None:
    _state["httpd"].shutdown()
    _state["httpd"].server_close()
    server.AUTH_REQUIRED = _state["auth"]


def status_line(response: bytes) -> bytes:
    """`204 No Content` - the reason phrase without the version, which is the server's business."""
    return response.split(b"\r\n", 1)[0].split(b" ", 1)[1].strip()


def send(request: bytes, timeout: float = 15.0) -> bytes:
    """Send a hand-built request and return everything up to the end of the headers."""
    with socket.create_connection(("127.0.0.1", _state["port"]), timeout=timeout) as s:
        s.sendall(request)
        out = b""
        while b"\r\n\r\n" not in out:
            chunk = s.recv(65536)
            if not chunk:
                break
            out += chunk
        return out


class OptionsTests(unittest.TestCase):
    def test_options_is_answered_instead_of_501(self) -> None:
        head = send(b"OPTIONS /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        self.assertEqual(status_line(head), b"204 No Content", head[:120])
        self.assertIn(b"allow:", head.lower())
        self.assertIn(b"options", head.lower())

    def test_options_never_answers_html(self) -> None:
        head = send(b"OPTIONS /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        self.assertNotIn(b"<!doctype", head.lower())
        self.assertNotIn(b"<html", head.lower())

    def test_options_on_a_path_the_console_does_not_serve_is_a_json_404(self) -> None:
        head = send(b"OPTIONS /nope HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        self.assertEqual(status_line(head)[:3], b"404", head[:120])
        self.assertIn(b"application/json", head.lower(), "the API answers JSON, never a page of markup")

    def test_options_carries_no_cors_headers(self) -> None:
        """No origin may read this console from a page: loopback-bound and token-gated.

        The public web edition keeps its own allowlist (`public_gateway._cors_ok`); the local
        console must not acquire one by accident.
        """
        head = send(b"OPTIONS /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                    b"Origin: https://chatagent.ca\r\nConnection: close\r\n\r\n")
        self.assertNotIn(b"access-control-allow-origin", head.lower())


class TokenHeaderCaseTests(unittest.TestCase):
    def test_the_header_is_found_however_the_client_cased_it(self) -> None:
        for name in ("X-LYGO-LLM-Token", "x-lygo-llm-token", "X-Lygo-Llm-Token", "X-LYGO-LLM-TOKEN"):
            self.assertEqual(auth.token_from_request({name: "abc"}, {}), "abc", name)

    def test_the_other_places_a_token_may_ride_still_work(self) -> None:
        self.assertEqual(auth.token_from_request({"Authorization": "Bearer abc"}, {}), "abc")
        self.assertEqual(auth.token_from_request({"AUTHORIZATION": "bearer abc"}, {}), "abc")
        self.assertEqual(auth.token_from_request({}, {"t": ["abc"]}), "abc")
        self.assertEqual(auth.token_from_request({}, {"token": ["abc"]}), "abc")
        self.assertEqual(auth.token_from_request({"Cookie": auth.COOKIE_NAME + "=abc"}, {}), "abc")

    def test_no_token_is_still_no_token(self) -> None:
        self.assertIsNone(auth.token_from_request({}, {}))
        self.assertIsNone(auth.token_from_request({"Cookie": "other=1"}, {}))
        self.assertIsNone(auth.token_from_request({"Authorization": "Basic abc"}, {}))


if __name__ == "__main__":
    unittest.main()
