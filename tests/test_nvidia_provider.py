"""The NVIDIA test key: a provider the kit ships, wired for the admin console.

`<DATA_VAULT>\\NVIDIA AGENT API TEST KEY.txt` holds an `nvapi-` key whose account answers on
`nvidia/nemotron-3-super-120b-a12b` - the model this kit's local engine cannot read (ledger L3).
Measured against the live endpoint: that model writes its reasoning into `content` unless the request
carries `chat_template_kwargs: {"thinking": false}`, and until now a provider could only send extra
HEADERS (`extra`), never extra body fields. So the kit gained a per-provider `body`, and these tests
hold it to two rules: it merges, and it can never override the model or the stream flag.

No real key is used here: the wired value is a clearly fake string.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve()
SRC = HERE.parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import cloud_api  # noqa: E402

FAKE_KEY = "nvapi-FAKE-only-for-tests-0123456789"


class TheKitShipsNvidiaTests(unittest.TestCase):
    def test_nvidia_is_a_provider_with_the_nim_endpoint(self):
        self.assertIn("nvidia", cloud_api.PROVIDERS)
        p = cloud_api.PROVIDERS["nvidia"]
        self.assertEqual(p["url"], "https://integrate.api.nvidia.com/v1/chat/completions")
        self.assertTrue(p.get("label"))
        self.assertIn(p["model"], p["models"], "the default model must be one of the listed ones")

    def test_the_default_model_is_the_one_measured_to_answer(self):
        p = cloud_api.PROVIDERS["nvidia"]
        self.assertEqual(p["model"], "nvidia/nemotron-3-super-120b-a12b")

    def test_thinking_is_off_in_the_provider_body(self):
        # measured: with thinking on, this model answers with its reasoning instead of the answer
        body = cloud_api.PROVIDERS["nvidia"].get("body") or {}
        self.assertEqual(((body.get("chat_template_kwargs") or {}).get("thinking")), False)


class AProviderBodyMergesButCannotOverrideTests(unittest.TestCase):
    def test_the_providers_body_fields_reach_the_request(self):
        seen = {}

        def fake_post(url, key, body, extra, timeout):
            seen.update(body)
            return 200, b'{"choices":[{"message":{"content":"ok"}}]}', "application/json"

        cur = {"provider": "nvidia", "model": "nvidia/nemotron-3-super-120b-a12b", "keys": {"nvidia": FAKE_KEY},
               "fallbacks": [], "enabled": True}
        with mock.patch.object(cloud_api, "_load", return_value=cur), \
                mock.patch.object(cloud_api, "_post", side_effect=fake_post), \
                mock.patch.object(cloud_api, "_note_success", lambda *a, **k: None):
            code, _, _ = cloud_api.chat({"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(code, 200)
        self.assertEqual(((seen.get("chat_template_kwargs") or {}).get("thinking")), False,
                         "the provider body must reach the request: " + json.dumps(seen)[:200])

    def test_a_provider_body_cannot_override_the_model_or_the_stream_flag(self):
        seen = {}

        def fake_post(url, key, body, extra, timeout):
            seen.update(body)
            return 200, b"{}", "application/json"

        cur = {"provider": "nvidia", "model": "nvidia/nemotron-3-super-120b-a12b", "keys": {"nvidia": FAKE_KEY},
               "fallbacks": [], "enabled": True}
        p = dict(cloud_api.PROVIDERS["nvidia"])
        p["body"] = {"model": "somebody-elses-model", "stream": True, "thinking": False}
        with mock.patch.object(cloud_api, "_load", return_value=cur), \
                mock.patch.dict(cloud_api.PROVIDERS, {"nvidia": p}), \
                mock.patch.object(cloud_api, "_post", side_effect=fake_post), \
                mock.patch.object(cloud_api, "_note_success", lambda *a, **k: None):
            cloud_api.chat({"messages": [], "model": "whatever"})
        self.assertEqual(seen.get("model"), "nvidia/nemotron-3-super-120b-a12b")
        self.assertIs(seen.get("stream"), False)
        self.assertIs(seen.get("thinking"), False, "fields the provider is allowed to add still land")


class AWiredNvidiaKeyJoinsTheChainTests(unittest.TestCase):
    def test_a_wired_nvidia_key_is_a_candidate_and_leaves_the_primary_alone(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / "api.json"
            with mock.patch.object(cloud_api, "API_PATH", tmp):
                cloud_api.save({"keys": {"nvidia": FAKE_KEY}})
                st = cloud_api.public_status()
                self.assertIn("nvidia", st["keys_wired"])
                self.assertEqual(st["provider"], cloud_api.DEFAULT_PROVIDER,
                                 "wiring a key must not silently change the primary provider")
                chain = cloud_api.chain_for()
                cand = [c for c in chain if c.get("provider") == "nvidia"]
                self.assertEqual(len(cand), 1, "the wired key must be a chain candidate")
                self.assertEqual(cand[0].get("url"), cloud_api.PROVIDERS["nvidia"]["url"],
                                 "a candidate without a url is only ever tried as bad_url")


if __name__ == "__main__":
    unittest.main()
