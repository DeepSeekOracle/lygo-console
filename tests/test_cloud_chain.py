from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import cloud_api  # noqa: E402

OK_BODY = {"choices": [{"message": {"role": "assistant", "content": "answer-from-stub"}}], "usage": {}}


class _Stub(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible endpoint that answers with a scripted code."""

    codes: list[int] = [200]
    seen: list[dict] = []

    def log_message(self, *a):  # silence
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace")
        body = {}
        try:
            body = json.loads(raw or "{}")
        except json.JSONDecodeError:
            pass
        type(self).seen.append(
            {
                "port": self.server.server_port,
                "path": self.path,
                "auth": self.headers.get("Authorization") or "",
                "model": body.get("model"),
            }
        )
        code = type(self).codes[min(len(type(self).seen) - 1, len(type(self).codes) - 1)]
        payload = json.dumps(OK_BODY if code < 400 else {"error": {"message": "stub %d" % code}}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _serve(codes: list[int]):
    cls = type("Stub%s" % len(_Stub.__subclasses__()), (_Stub,), {"codes": list(codes), "seen": []})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, cls


class CloudChainTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._api = Path(self._tmp.name) / "api.json"
        self._patch = [
            ("API_PATH", cloud_api.API_PATH),
            ("_ds_url", cloud_api.PROVIDERS["deepseek"]["url"]),
            ("_gm_url", cloud_api.PROVIDERS["gemini"]["url"]),
        ]
        cloud_api.API_PATH = self._api
        self.srv_a, self.cls_a = _serve([402])
        self.srv_b, self.cls_b = _serve([200])
        cloud_api.PROVIDERS["deepseek"]["url"] = "http://127.0.0.1:%d/v1/chat/completions" % self.srv_a.server_port
        cloud_api.PROVIDERS["gemini"]["url"] = "http://127.0.0.1:%d/v1/chat/completions" % self.srv_b.server_port

    def tearDown(self):
        for srv in (self.srv_a, self.srv_b):
            srv.shutdown()
            srv.server_close()
        cloud_api.API_PATH = self._patch[0][1]
        cloud_api.PROVIDERS["deepseek"]["url"] = self._patch[1][1]
        cloud_api.PROVIDERS["gemini"]["url"] = self._patch[2][1]
        self._tmp.cleanup()

    def wire(self, **obj):
        self._api.write_text(json.dumps(obj), encoding="utf-8")

    def post(self):
        return cloud_api.chat({"messages": [{"role": "user", "content": "hi"}], "max_tokens": 8})

    # ---- the upgrade: a second key survives the first one dying -------------------------

    def test_402_on_primary_falls_through_to_second_key(self):
        self.wire(enabled=True, provider="deepseek", model="deepseek-chat", key="sk-first",
                  keys={"gemini": "AIza-second"}, fallbacks=["gemini"], degraded=False)
        code, body, _ = self.post()
        self.assertEqual(code, 200)
        self.assertIn(b"answer-from-stub", body)
        self.assertEqual(len(self.cls_a.seen), 1, "primary tried once")
        self.assertEqual(len(self.cls_b.seen), 1, "backup tried once")
        self.assertTrue(self.cls_b.seen[0]["auth"].endswith("AIza-second"), "backup used its OWN key")
        self.assertEqual(self.cls_b.seen[0]["model"], "gemini-flash-latest", "backup used its own model")
        st = cloud_api.public_status()
        self.assertEqual(st["keys_wired"], ["deepseek", "gemini"])
        self.assertEqual(st["key_count"], 2)
        self.assertEqual(st["last_provider"], "gemini")
        self.assertEqual(st["chain_tried"], ["deepseek:402", "gemini:200"])

    def test_all_keys_failing_returns_last_failure_for_handoff(self):
        self.srv_b.shutdown()
        self.srv_b.server_close()
        srv_c, cls_c = _serve([402])
        cloud_api.PROVIDERS["gemini"]["url"] = "http://127.0.0.1:%d/v1/chat/completions" % srv_c.server_port
        self.addCleanup(lambda: (srv_c.shutdown(), srv_c.server_close()))
        self.wire(enabled=True, provider="deepseek", model="deepseek-chat", key="sk-first",
                  keys={"gemini": "AIza-second"}, fallbacks=["gemini"])
        code, body, _ = self.post()
        self.assertEqual(code, 402, "the console must still see a 402 so it can hand off to local")
        self.assertIn("no_api_key", cloud_api.public_status()["secret"] + "no_api_key")
        st = cloud_api.public_status()
        self.assertEqual(st["chain_tried"], ["deepseek:402", "gemini:402"])
        self.assertTrue(st["enabled"], "keys are still wired; only the last call failed")

    def test_malformed_request_does_not_burn_the_second_key(self):
        self.srv_a.codes_override = None  # noqa: SLF001  (documentation only)
        self.cls_a.codes = [400]
        self.wire(enabled=True, provider="deepseek", model="deepseek-chat", key="sk-first",
                  keys={"gemini": "AIza-second"}, fallbacks=["gemini"])
        code, _body, _ = self.post()
        self.assertEqual(code, 400)
        self.assertEqual(len(self.cls_a.seen), 1)
        self.assertEqual(len(self.cls_b.seen), 0, "a 400 is not a reason to spend the backup key")

    # ---- nothing regressed for the single-key / keyless cases --------------------------

    def test_single_key_config_unchanged(self):
        self.cls_a.codes = [200]
        self.wire(enabled=True, provider="deepseek", model="deepseek-chat", key="sk-only")
        code, _body, _ = self.post()
        self.assertEqual(code, 200)
        self.assertEqual(len(self.cls_a.seen), 1)
        self.assertEqual(len(self.cls_b.seen), 0)
        st = cloud_api.public_status()
        self.assertEqual(st["keys_wired"], ["deepseek"])
        self.assertEqual(st["key_count"], 1)
        self.assertTrue(st["enabled"] and st["has_key"])

    def test_no_key_at_all_still_401_no_api_key(self):
        self.wire(enabled=True, provider="deepseek", model="deepseek-chat", key="")
        code, body, _ = self.post()
        self.assertEqual(code, 401)
        self.assertEqual(json.loads(body.decode()), {"error": "no_api_key"})
        st = cloud_api.public_status()
        self.assertFalse(st["enabled"])
        self.assertFalse(st["has_key"])
        self.assertEqual(st["key_count"], 0)

    def test_second_key_alone_can_carry_the_console(self):
        self.cls_a.codes = [200]
        self.wire(enabled=True, provider="deepseek", model="deepseek-chat", key="",
                  keys={"gemini": "AIza-second"}, fallbacks=["gemini"])
        self.assertTrue(cloud_api.enabled())
        code, _body, _ = self.post()
        self.assertEqual(code, 200)
        self.assertEqual(len(self.cls_b.seen), 1)
        self.assertEqual(len(self.cls_a.seen), 0)

    # ---- the secret rule: wired in, never echoed --------------------------------------

    def test_neither_key_is_ever_echoed(self):
        self.wire(enabled=True, provider="deepseek", model="deepseek-chat", key="sk-first",
                  keys={"gemini": "AIza-second"}, fallbacks=["gemini"])
        blob = json.dumps(cloud_api.public_status())
        self.assertNotIn("sk-first", blob)
        self.assertNotIn("AIza-second", blob)
        self.assertEqual(cloud_api.public_status()["secret"], "never_echoed")

    def test_save_merges_a_backup_key_and_can_unwire_one(self):
        self.wire(enabled=True, provider="deepseek", model="deepseek-chat", key="sk-first")
        cloud_api.save({"keys": {"gemini": "AIza-second"}, "fallbacks": ["gemini"]})
        self.assertEqual(cloud_api.public_status()["keys_wired"], ["deepseek", "gemini"])
        cloud_api.save({"keys": {"gemini": ""}})
        st = cloud_api.public_status()
        self.assertEqual(st["keys_wired"], ["deepseek"])
        self.assertEqual(cloud_api._load()["keys"], {})  # noqa: SLF001
        self.assertEqual(cloud_api._load()["key"], "sk-first", "clearing a backup must not clear the primary")  # noqa: SLF001

    def test_chain_is_ordered_primary_first_and_skips_keyless_providers(self):
        self.wire(enabled=True, provider="deepseek", model="deepseek-chat", key="sk-first",
                  keys={"gemini": "AIza-second", "groq": "gsk-third"}, fallbacks=["gemini", "groq", "xai"])
        names = [c["provider"] for c in cloud_api.chain_for()]
        self.assertEqual(names, ["deepseek", "gemini", "groq"], "xai has no key so it is skipped")
        self.assertEqual(cloud_api.public_status()["chain"][0], "DeepSeek")


if __name__ == "__main__":
    unittest.main()
