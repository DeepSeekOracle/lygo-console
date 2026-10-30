"""When the API key does not answer, the console must say so - with the provider's own code.

The operator's report: "the API system is not working ... the Agent does not respond using API key".
Measured on this box 2026-09-21, `config/api.json` held:

    "chain_tried": ["deepseek:401"], "last_code": 0, "last_error": "", "handoffs": 2

The walk that talks to the provider *knew* the answer was 401 and wrote the code into `chain_tried`.
`last_code` and `last_error` - the two fields the portal prints - were empty, so the page showed
"API handoff · " and stopped. The console was not hiding the reason from the operator on purpose; it
was dropping it on the way from the provider to the file, and no test noticed because the only field
anything asserted on was the one that survived.

Rules these tests hold to:

* the walk records the code and a plain-language reason itself, so no caller can lose them;
* the reason never contains key material (this file is on disk and printed in the page);
* a key that works says so, and a key that is refused says which kind of refusal it was - 401 is not
  429 and neither is 402, because the operator's next move is different in each case;
* asking "is my key alive?" is one call, and it records its outcome like any other attempt.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve()
SRC = HERE.parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import cloud_api  # noqa: E402  (path set above)
import server  # noqa: E402  (the handler, for the route tests)

KEY = "sk-" + "k" * 30  # never a real key; asserted absent from everything recorded


class TheReasonSurvivesTheWalkTests(unittest.TestCase):
    def setUp(self):
        self.state: dict = {"enabled": True, "provider": "deepseek", "model": "deepseek-chat",
                            "url": "https://api.deepseek.com/v1/chat/completions", "key": KEY,
                            "chain_tried": [], "degraded": False, "last_error": "", "last_code": 0}
        # The seam is atomic_write_text: what the walk records is what lands in self.state, so the
        # assertions read the same bytes the file would hold.
        def _capture(_path, text, *_a, **_k):
            self.state.update(json.loads(text))

        self.patches = [
            mock.patch.object(cloud_api, "_load", side_effect=lambda: json.loads(json.dumps(self.state))),
            mock.patch.object(cloud_api, "atomic_write_text", _capture),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def test_a_refused_key_leaves_its_code_and_a_reason(self):
        refusal = json.dumps({"error": {"message": "Authentication Fails, Your api key is invalid"}}).encode()

        with mock.patch.object(cloud_api, "_post", return_value=(401, refusal, "application/json")):
            code, _body, _ctype = cloud_api.chat({"messages": [{"role": "user", "content": "hi"}]})

        self.assertEqual(code, 401)
        self.assertEqual(self.state.get("last_code"), 401, "the provider's code was dropped again")
        reason = str(self.state.get("last_error") or "")
        self.assertTrue(reason, "the operator gets no reason for a refused key")
        self.assertIn("401", reason)
        self.assertIn("key", reason.lower())
        self.assertNotIn(KEY, reason, "the recorded reason must never carry key material")
        self.assertIn("deepseek:401", self.state.get("chain_tried") or [])

    def test_the_reason_tells_the_three_refusals_apart(self):
        for code, want in ((401, "reject"), (402, "credit"), (429, "limit")):
            with mock.patch.object(cloud_api, "_post", return_value=(code, b"{}", "application/json")):
                cloud_api.chat({"messages": [{"role": "user", "content": "hi"}]})
            reason = str(self.state.get("last_error") or "").lower()
            self.assertIn(want, reason, "HTTP %s reads as %r" % (code, reason))

    def test_a_key_that_answers_leaves_no_failure_behind_it(self):
        ok = json.dumps({"choices": [{"message": {"content": "pong"}}]}).encode()

        with mock.patch.object(cloud_api, "_post", return_value=(200, ok, "application/json")):
            code, _body, _ctype = cloud_api.chat({"messages": [{"role": "user", "content": "hi"}]})

        self.assertEqual(code, 200)
        self.assertNotEqual(self.state.get("last_code"), 401)
        self.assertEqual(self.state.get("last_error"), "")


class AskingWhetherTheKeyIsAliveTests(unittest.TestCase):
    def setUp(self):
        self.state: dict = {"enabled": True, "provider": "deepseek", "model": "deepseek-chat",
                            "url": "https://api.deepseek.com/v1/chat/completions", "key": KEY,
                            "chain_tried": [], "degraded": False, "last_error": "", "last_code": 0}
        # The seam is atomic_write_text: what the walk records is what lands in self.state, so the
        # assertions read the same bytes the file would hold.
        def _capture(_path, text, *_a, **_k):
            self.state.update(json.loads(text))

        self.patches = [
            mock.patch.object(cloud_api, "_load", side_effect=lambda: json.loads(json.dumps(self.state))),
            mock.patch.object(cloud_api, "atomic_write_text", _capture),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def test_a_working_key_probes_ok_and_says_which_provider_answered(self):
        ok = json.dumps({"choices": [{"message": {"content": "pong"}}]}).encode()

        with mock.patch.object(cloud_api, "_post", return_value=(200, ok, "application/json")):
            out = cloud_api.probe()

        self.assertTrue(out["ok"], out)
        self.assertEqual(out["code"], 200)
        self.assertEqual(out["provider"], "deepseek")
        self.assertIn("seconds", out)
        self.assertNotIn(KEY, json.dumps(out))

    def test_a_refused_key_probes_not_ok_with_the_code_and_the_reason(self):
        refusal = json.dumps({"error": {"message": "invalid api key"}}).encode()

        with mock.patch.object(cloud_api, "_post", return_value=(401, refusal, "application/json")):
            out = cloud_api.probe()

        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], 401)
        self.assertIn("401", out["why"])
        self.assertEqual(self.state.get("last_code"), 401, "a probe must be recorded like a turn")
        self.assertTrue(self.state.get("degraded"))

    def test_a_probe_with_no_key_says_so_instead_of_calling_anyone(self):
        self.state["key"] = ""
        with mock.patch.object(cloud_api, "_post", side_effect=AssertionError("must not call the provider")):
            out = cloud_api.probe()
        self.assertFalse(out["ok"])
        self.assertIn("key", str(out.get("why") or "").lower())


class TheProbeIsReachableTests(unittest.TestCase):
    """Through a real handler, because reading the source is not evidence.

    The first version of this test grepped server.py for the call and passed - while the call raised
    UnboundLocalError (`cloud_api` is a local of the dispatcher, bound by a later import) and answered
    500. The traceback the 500 handler now logs is what named it. This spins the actual handler.
    """

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _post(self, path, obj):
        import socket

        body = json.dumps(obj).encode()
        req = (f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
               f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode("ascii") + body
        with socket.create_connection(("127.0.0.1", self.port), timeout=60) as s:
            s.sendall(req)
            out = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                out += chunk
        head, _, rest = out.partition(b"\r\n\r\n")
        return int(head.split(b" ")[1]), rest.decode("utf-8", "replace")

    def test_the_probe_is_answered_on_the_consoles_own_route(self):
        import cloud_api as ca

        answer = {"ok": True, "code": 200, "provider": "deepseek", "model": "deepseek-chat",
                  "seconds": 1.1, "why": "the key answered"}
        with mock.patch.object(ca, "probe", return_value=answer):
            status, body = self._post("/api/cloud", {"action": "probe"})
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(json.loads(body), answer)

    def test_a_refused_key_comes_back_through_the_route_with_its_code(self):
        import cloud_api as ca

        answer = {"ok": False, "code": 401, "provider": "deepseek", "model": "deepseek-chat",
                  "seconds": 0.4, "why": "the provider rejected the API key (401)"}
        with mock.patch.object(ca, "probe", return_value=answer):
            status, body = self._post("/api/cloud", {"action": "probe"})
        self.assertEqual(status, 200, body[:300])
        got = json.loads(body)
        self.assertFalse(got["ok"])
        self.assertEqual(got["code"], 401)
        self.assertIn("401", got["why"])

    def test_the_ordinary_save_still_works(self):
        import cloud_api as ca

        with mock.patch.object(ca, "save", return_value={"ok": True, "saved": True}):
            status, body = self._post("/api/cloud", {"enabled": True})
        self.assertEqual(status, 200, body[:300])
        self.assertTrue(json.loads(body).get("ok"))


if __name__ == "__main__":
    unittest.main()
