"""Public gateway tests.

The CORS/rate/portal checks are unit-level. The request-envelope checks are over a real socket,
because the status line and the headers ARE the answer — and because both defects they cover were
invisible to the router logic:

  * an over-limit body was refused **unread**, so a caller still writing it lost the answer to a
    reset instead of being told `too_large` (defect 27, same class as `/api/session` in the kernel);
  * a non-numeric `Content-Length` raised `ValueError` out of the handler, so the connection closed
    with no answer at all and the operator got a traceback on stderr.
"""
from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import public_gateway  # noqa: E402
from public_gateway import ALLOW_ORIGINS, Handler, _cors_ok, _rate  # noqa: E402

NL = chr(13) + chr(10)
_state: dict = {}


def setUpModule() -> None:  # noqa: N802
    _state["rate"] = public_gateway._rate
    public_gateway._rate = lambda ip: True  # the limiter is not what these tests are about
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    _state["httpd"] = httpd
    _state["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def tearDownModule() -> None:  # noqa: N802
    _state["httpd"].shutdown()
    _state["httpd"].server_close()
    public_gateway._rate = _state["rate"]


def post(body: bytes, declared: str) -> str:
    """Send a hand-built request and return the whole answer, headers and body."""
    head = ("POST /api/chat HTTP/1.1" + NL + "Host: 127.0.0.1" + NL
            + "Content-Length: " + declared + NL + NL).encode()
    raw = b""
    with socket.create_connection(("127.0.0.1", _state["port"]), timeout=10) as sock:
        sock.sendall(head + body)
        sock.settimeout(5)
        try:
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                raw += chunk
        except OSError:
            pass
    return raw.decode("latin-1", "replace")


def body_of(answer: str) -> dict:
    return json.loads(answer.split(NL + NL, 1)[1]) if NL + NL in answer else {}


class PublicGatewayTests(unittest.TestCase):
    def test_cors_allowlist(self):
        self.assertTrue(_cors_ok("https://chatagent.ca"))
        self.assertTrue(_cors_ok("https://eternalhaven.ca"))
        self.assertTrue(_cors_ok("https://foo.hf.space"))
        self.assertFalse(_cors_ok("https://evil.example"))

    def test_rate_limit(self):
        ip = "203.0.113.9"
        ok = 0
        for _ in range(40):
            if _rate(ip):
                ok += 1
        self.assertLessEqual(ok, 24)
        self.assertGreater(ok, 0)

    def test_web_portal_files(self):
        p = ROOT / "web_portal"
        self.assertTrue((p / "index.html").is_file())
        html = (p / "index.html").read_text(encoding="utf-8")
        self.assertIn("LYGO Free LLM Portal", html)
        self.assertIn("Hugging Face", html)
        self.assertNotIn("LYGO_SERVER", html)


class RefusedBodyTest(unittest.TestCase):
    """A caller whose request is refused must still receive the refusal."""

    def test_an_over_limit_body_is_answered_with_the_reason(self):
        body = b"x" * 60_000
        answer = post(body, str(len(body)))
        self.assertIn("413", answer.split(NL, 1)[0], answer[:200])
        self.assertEqual(body_of(answer).get("error"), "too_large", answer[:200])

    def test_the_caller_is_told_the_connection_is_closing(self):
        body = b"x" * 60_000
        answer = post(body, str(len(body)))
        self.assertIn("Connection: close", answer, "a caller that reuses this socket fails its next request on it")

    def test_an_over_limit_body_is_consumed(self):
        """The mechanism, at the unit both surfaces share: `http_body.drain` must read the body."""
        import http_body

        class Peer:
            def __init__(self, declared):
                self.headers = {"Content-Length": str(declared)}
                self.connection = None
                self.sent = 0
                self.remaining = declared

            class _Stream:
                def __init__(self, peer):
                    self.peer = peer

                def read(self, n=-1):
                    take = min(n if n and n > 0 else self.peer.remaining, self.peer.remaining)
                    self.peer.remaining -= take
                    self.peer.sent += take
                    return b"x" * take

            rfile = None

        peer = Peer(60_000)
        peer.rfile = Peer._Stream(peer)
        self.assertEqual(http_body.drain(peer, 60_000), 60_000, "a refused body must be consumed")

        huge = Peer(http_body.CAP + 9_000_000)
        huge.rfile = Peer._Stream(huge)
        self.assertLessEqual(http_body.drain(huge, huge.remaining), http_body.CAP,
                             "a caller declaring tens of megabytes must not cost us tens of megabytes")


class MalformedContentLengthTest(unittest.TestCase):
    """Both of these used to end the connection with no answer: `int()` raised out of the handler."""

    def test_a_non_numeric_length_is_a_400(self):
        answer = post(b"", "abc")
        self.assertTrue(answer, "the caller must get an answer, not a dropped connection")
        self.assertIn("400", answer.split(NL, 1)[0], answer[:200])
        self.assertEqual(body_of(answer).get("error"), "bad_request", answer[:200])

    def test_a_negative_length_is_a_400(self):
        answer = post(b"", "-5")
        self.assertTrue(answer, "the caller must get an answer, not a dropped connection")
        self.assertIn("400", answer.split(NL, 1)[0], answer[:200])


class PictureRouteTests(unittest.TestCase):
    """The pictures a browser page on chatagent.ca can ask THIS machine for.

    MEASURED on the steward's box 2026-09-23: one render is 12.5 s on a free card and 272-300 s on the
    CPU route the limb picks while the chat model holds the card. That is why POST answers 202 with a
    job handle: a five-minute request dies in any proxy, and the page can show the clock meanwhile.
    """

    PNG = bytes([137, 80, 78, 71, 13, 10, 26, 10]) + b"0" * 64   # a real PNG signature + filler

    def setUp(self):
        import paths
        import types

        self.paths = paths
        self.tmp = Path(tempfile.mkdtemp(prefix="lygo_gw_images_"))
        (self.tmp / "images").mkdir(parents=True, exist_ok=True)
        self.file = self.tmp / "images" / "gen-20260923-120000.png"
        self.file.write_bytes(self.PNG)
        self.renders: list[str] = []
        self.sizes: list[tuple] = []
        self.fail_with: dict = {}
        self.ready = True

        fake = types.ModuleType("media_tools")

        def image_generate(prompt: str, negative: str = "", width: int = 0, height: int = 0,
                           *a: object, **kw: object) -> dict:
            self.renders.append(prompt)
            self.sizes.append((width, height))
            if self.fail_with:
                return dict(self.fail_with)
            return {"ok": True, "path": str(self.file), "bytes": len(self.PNG),
                    "seconds": 12.5, "engine": "sd", "width": width or 1024, "height": height or 1024}

        fake.image_generate = image_generate  # type: ignore[attr-defined]
        fake.media_status = lambda: {"ok": True, "image": {  # type: ignore[attr-defined]
            "exe": "D:/sd/sd-cli.exe", "exe_on_disk": True, "cpu_exe": "D:/sd-cpu/sd-cli.exe",
            "ready": self.ready, "default": "sd_xl_turbo_1.0_fp16.safetensors"}}
        fake._route_for_a_render = lambda: ("", "")  # type: ignore[attr-defined]
        self.fake = fake

        self._real_mod = sys.modules.get("media_tools")
        sys.modules["media_tools"] = fake
        self._rate = public_gateway._rate_image
        public_gateway._rate_image = lambda ip: True
        self._patch = mock.patch.object(paths, "WORKSPACE", self.tmp)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        public_gateway._rate_image = self._rate
        if self._real_mod is not None:
            sys.modules["media_tools"] = self._real_mod
        else:
            sys.modules.pop("media_tools", None)

    # ── helpers ───────────────────────────────────────────────────────────────────────────────────
    def _req(self, method: str, path: str, body: bytes | None = None,
             headers: dict | None = None) -> tuple[int, dict, bytes]:
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", _state["port"], timeout=20)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            r = conn.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            conn.close()

    def _draw(self, prompt: str = "a lime green sports car on a neon street",
              origin: str | None = "https://chatagent.ca", wait: float = 15.0):
        hd = {"Content-Type": "application/json"}
        if origin:
            hd["Origin"] = origin
        st, heads, raw = self._req("POST", "/api/image", json.dumps({"prompt": prompt}).encode(), hd)
        job = json.loads(raw or b"{}")
        if st != 202:
            return st, heads, job, {}
        row: dict = {}
        end = time.time() + wait
        while time.time() < end:
            _, _, raw2 = self._req("GET", "/api/image?job=" + str(job.get("job")), headers=hd)
            row = json.loads(raw2 or b"{}")
            if row.get("status") in ("done", "failed"):
                break
            time.sleep(0.05)
        return st, heads, job, row

    # ── the route ─────────────────────────────────────────────────────────────────────────────────
    def test_the_portal_origin_can_start_a_picture_and_fetch_it(self):
        st, heads, job, row = self._draw()
        self.assertEqual(st, 202, job)
        self.assertTrue(job.get("job"), job)
        self.assertEqual(heads.get("Access-Control-Allow-Origin"), "https://chatagent.ca")
        self.assertTrue(row, "the job must be pollable")
        self.assertEqual(row.get("status"), "done", row)
        self.assertGreater(row.get("bytes"), 0)
        self.assertEqual(row.get("engine"), "sd")
        self.assertEqual(self.renders, ["a lime green sports car on a neon street"])
        st2, h2, raw = self._req("GET", str(row.get("url")), headers={"Origin": "https://chatagent.ca"})
        self.assertEqual(st2, 200, raw[:200])
        self.assertEqual(raw, self.PNG, "the bytes route must hand back the picture the limb wrote")
        self.assertEqual(h2.get("Content-Type"), "image/png")
        self.assertEqual(h2.get("Access-Control-Allow-Origin"), "https://chatagent.ca")

    def test_a_requested_size_reaches_the_limb(self):
        st, _, job, row = self._draw(prompt="a cat at 512")
        self.assertEqual(st, 202, job)
        self.assertEqual(job.get("size"), 1024, "no size asked for = the limb's own default")
        self.assertEqual(row.get("size"), 1024)
        self.assertEqual(row.get("drawn_w"), 1024, "the page shows the pixels the limb drew, not what was asked")
        self.assertEqual(self.sizes, [(0, 0)], "an unasked size must let the limb decide")
        hd = {"Content-Type": "application/json"}
        st2, _, raw = self._req("POST", "/api/image",
                                json.dumps({"prompt": "a cat at 512", "size": 512}).encode(), hd)
        self.assertEqual(st2, 202, raw[:200])
        self.assertEqual(json.loads(raw).get("size"), 512)
        row2 = {}
        for _ in range(40):
            _, _, raw2 = self._req("GET", "/api/image?job=" + json.loads(raw)["job"], headers=hd)
            row2 = json.loads(raw2)
            if row2.get("status") in ("done", "failed"):
                break
            time.sleep(0.05)
        self.assertIn((512, 512), self.sizes, self.sizes)
        self.assertEqual((row2.get("drawn_w"), row2.get("drawn_h")), (512, 512), row2)
        st3, _, raw3 = self._req("POST", "/api/image",
                                 json.dumps({"prompt": "a cat", "size": 99}).encode(), hd)
        self.assertEqual(st3, 400)
        self.assertEqual(json.loads(raw3).get("error"), "bad_size")
        self.assertNotIn((99, 99), self.sizes)

    def test_the_url_is_only_offered_when_there_really_is_a_file(self):
        self.fail_with = {"ok": False, "error": "image_failed", "seconds": 183.4,
                          "hint": "the picture engine could not get device memory"}
        _, _, _, row = self._draw()
        self.assertEqual(row.get("status"), "failed", row)
        self.assertEqual(row.get("error"), "image_failed")
        self.assertIn("device memory", row.get("hint"))
        self.assertNotIn("url", row, "no file, no url - the page must not be handed a path to nothing")

    def test_a_failed_render_does_not_block_the_next_one(self):
        self.fail_with = {"ok": False, "error": "image_failed"}
        _, _, _, row = self._draw()
        self.assertEqual(row.get("status"), "failed")
        self.fail_with = {}
        st, _, _, row2 = self._draw()
        self.assertEqual(st, 202)
        self.assertEqual(row2.get("status"), "done", "the single-flight lock must be released on failure")

    def test_a_name_the_gateway_did_not_write_is_refused(self):
        for bad in ("../../secret.png", "..%2Fsecret.png", "notes.txt", "gen-x.png", "", "."):
            st, _, _ = self._req("GET", "/api/image?file=" + bad)
            self.assertEqual(st, 404, bad)
        st, _, raw = self._req("GET", "/api/image?file=gen-20260923-120000.png")
        self.assertEqual(st, 200, raw[:200])

    def test_a_non_allowlisted_origin_is_refused_for_start_and_bytes(self):
        st, _, _, _ = self._draw(origin="https://evil.example")
        self.assertEqual(st, 403)
        st2, _, _ = self._req("GET", "/api/image?file=gen-20260923-120000.png",
                              headers={"Origin": "https://evil.example"})
        self.assertEqual(st2, 403)
        self.assertEqual(self.renders, [], "a refused caller must not have started a render")

    def test_an_empty_prompt_is_named(self):
        st, _, raw = self._req("POST", "/api/image", b"{}", {"Content-Type": "application/json"})
        self.assertEqual(st, 400)
        self.assertEqual(json.loads(raw).get("error"), "empty_prompt")
        self.assertEqual(self.renders, [])

    def test_a_quarantined_prompt_is_refused_before_any_render(self):
        real_gate = public_gateway.gate_prompt
        public_gateway.gate_prompt = lambda text: {"verdict": "QUARANTINE", "reason": "p0"}
        try:
            st, _, raw = self._req("POST", "/api/image", json.dumps({"prompt": "format c:"}).encode(),
                                   {"Content-Type": "application/json"})
        finally:
            public_gateway.gate_prompt = real_gate
        self.assertEqual(st, 451)
        self.assertEqual(self.renders, [], "the gate runs before the engine, not after")

    def test_one_render_at_a_time_and_the_busy_answer_names_the_job(self):
        held = public_gateway._image_lock.acquire(blocking=False)
        self.assertTrue(held)
        public_gateway._image_jobs["abc123"] = {"id": "abc123", "status": "running",
                                                "started": time.time() - 30, "prompt": "p"}
        try:
            st, _, raw = self._req("POST", "/api/image", json.dumps({"prompt": "a cat"}).encode(),
                                   {"Content-Type": "application/json"})
        finally:
            public_gateway._image_jobs.pop("abc123", None)
            public_gateway._image_lock.release()
        self.assertEqual(st, 429)
        body = json.loads(raw)
        self.assertEqual(body.get("error"), "busy")
        self.assertEqual(body.get("job"), "abc123")
        self.assertGreaterEqual(body.get("elapsed", 0), 29)
        self.assertEqual(self.renders, [])

    def test_a_refusal_reads_the_body_it_is_refusing(self):
        """A refusal that leaves the body unread resets the caller (WinError 10053, measured 2026-09-25).

        It showed up once in a full run and never alone (the pictures-off test passes 3/3 by itself), so
        the pin is on the rule: whatever this route answers, it reads what the caller sent first.
        """
        seen = []
        real = public_gateway.drain_body
        public_gateway.drain_body = lambda handler, declared: (seen.append(int(declared)),
                                                               real(handler, declared))[1]
        was = public_gateway.IMAGE_ENABLED
        public_gateway.IMAGE_ENABLED = False
        try:
            body = json.dumps({"prompt": "a cat"}).encode()
            st, _, _ = self._req("POST", "/api/image", body, {"Content-Type": "application/json"})
        finally:
            public_gateway.IMAGE_ENABLED = was
            public_gateway.drain_body = real
        self.assertEqual(st, 503)
        self.assertEqual(seen, [len(body)], "the refusal must read the body before it answers")

    def test_the_operator_can_turn_pictures_off(self):
        public_gateway.IMAGE_ENABLED = False
        try:
            st, _, raw = self._req("POST", "/api/image", json.dumps({"prompt": "a cat"}).encode(),
                                   {"Content-Type": "application/json"})
            _, _, health_raw = self._req("GET", "/health")
        finally:
            public_gateway.IMAGE_ENABLED = True
        self.assertEqual(st, 503)
        self.assertEqual(json.loads(raw).get("error"), "image_off")
        self.assertFalse(json.loads(health_raw).get("image", {}).get("enabled"))
        self.assertEqual(self.renders, [])

    def test_a_machine_with_no_engine_says_so_instead_of_pretending(self):
        self.ready = False
        st, _, raw = self._req("POST", "/api/image", json.dumps({"prompt": "a cat"}).encode(),
                               {"Content-Type": "application/json"})
        self.assertEqual(st, 503)
        self.assertEqual(json.loads(raw).get("error"), "no_image_engine")
        self.assertEqual(self.renders, [])

    def test_health_reports_the_picture_route_honestly(self):
        _, _, raw = self._req("GET", "/health")
        img = json.loads(raw).get("image") or {}
        self.assertTrue(img.get("enabled"))
        self.assertTrue(img.get("ready"))
        self.assertIn("sd-cli.exe", str(img.get("engine")))
        self.assertEqual(img.get("default"), "sd_xl_turbo_1.0_fp16.safetensors")
        self.assertIn("busy", img)

    def test_the_picture_limiter_is_tighter_than_the_chat_one(self):
        # `self._rate` is the real limiter this class saved in setUp; the picture budget is its own.
        public_gateway._rate_image = self._rate
        public_gateway._image_hits.clear()
        ip_ok = [public_gateway._rate_image("198.51.100.7") for _ in range(public_gateway.IMAGE_MAX_REQ + 1)]
        self.assertTrue(all(ip_ok[:-1]), ip_ok)
        self.assertFalse(ip_ok[-1], "the picture budget must run out")
        self.assertLess(public_gateway.IMAGE_MAX_REQ, public_gateway.MAX_REQ,
                        "a picture costs minutes of card or CPU; a chat reply costs seconds")
        self.assertTrue(public_gateway._rate("198.51.100.8"), "and the chat limiter is a different budget")

    def test_an_over_limit_prompt_body_is_refused_and_consumed(self):
        import socket

        head = ("POST /api/image HTTP/1.1" + NL + "Host: 127.0.0.1" + NL
                + "Content-Length: 20000" + NL + NL).encode()
        raw = b""
        with socket.create_connection(("127.0.0.1", _state["port"]), timeout=10) as sock:
            sock.sendall(head + b"x" * 20_000)
            sock.settimeout(5)
            try:
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    raw += chunk
            except OSError:
                pass
        answer = raw.decode("latin-1", "replace")
        self.assertIn("413", answer.split(NL, 1)[0], answer[:200])
        self.assertEqual(body_of(answer).get("error"), "too_large", answer[:200])


if __name__ == "__main__":
    unittest.main()
