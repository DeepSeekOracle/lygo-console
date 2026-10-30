"""Switching the brain must not silently drop a saved key out of the failover chain.

Found live: with `provider: nvidia` set, `public_status()["keys_wired"]` came back
`["nvidia", "gemini", "groq"]` - **DeepSeek was gone**, even though its key was still in the config.
The legacy top-level `key` field was only offered to the candidate whose name equalled the primary,
so the moment the operator pointed the console at another provider the house default lost its key and
the chain quietly got shorter. A shorter chain is invisible until every remaining provider is down.

Two rules hold it now: the legacy key is recorded under the provider that saved it, and it stays
available to the house default when the primary is switched elsewhere.
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

LEGACY = "sk-deepseek-legacy-key-0123456789abcdef"
OTHER = "nvapi-FAKE-only-for-tests-0123456789"


class TheHouseDefaultKeepsItsKeyTests(unittest.TestCase):
    def _cfg(self, **over):
        cur = {"enabled": True, "provider": "nvidia", "model": "nvidia/nemotron-3-super-120b-a12b",
               "url": cloud_api.PROVIDERS["nvidia"]["url"], "key": LEGACY, "keys": {"nvidia": OTHER},
               "fallbacks": [], "chain_tried": [], "degraded": False, "last_error": "", "last_code": 0,
               "last_at": 0.0, "handoffs": 0}
        cur.update(over)
        return cur

    def test_switching_the_primary_keeps_the_house_default_in_the_chain(self):
        chain = cloud_api.chain_for(self._cfg())
        names = [c["provider"] for c in chain]
        self.assertIn("nvidia", names)
        self.assertIn(cloud_api.DEFAULT_PROVIDER, names,
                      "the house default lost its key when the primary moved: " + json.dumps(names))

    def test_the_defaults_candidate_carries_the_legacy_key_and_its_own_url(self):
        cand = [c for c in cloud_api.chain_for(self._cfg()) if c["provider"] == cloud_api.DEFAULT_PROVIDER][0]
        self.assertEqual(cand["key"], LEGACY)
        self.assertEqual(cand["url"], cloud_api.PROVIDERS[cloud_api.DEFAULT_PROVIDER]["url"],
                         "a fallback must post to its own provider, never the primary's url")

    def test_a_key_already_claimed_by_another_provider_is_not_reoffered_as_the_defaults(self):
        # if the same value is explicitly wired to another provider, do not hand it to the default too
        chain = cloud_api.chain_for(self._cfg(key=OTHER, keys={"nvidia": OTHER}))
        for c in chain:
            if c["provider"] == cloud_api.DEFAULT_PROVIDER:
                self.fail("a key owned by nvidia was re-offered as DeepSeek's: " + json.dumps(chain)[:200])

    def test_saving_a_key_records_it_under_its_own_provider(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / "api.json"
            with mock.patch.object(cloud_api, "API_PATH", tmp):
                cloud_api.save({"provider": "deepseek", "key": LEGACY})
                saved = json.loads(tmp.read_text(encoding="utf-8"))
                self.assertEqual(saved.get("keys", {}).get("deepseek"), LEGACY,
                                 "a saved key must be filed under its provider, not only the legacy field")


if __name__ == "__main__":
    unittest.main()
