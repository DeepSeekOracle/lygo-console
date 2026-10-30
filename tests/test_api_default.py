"""House API policy: DeepSeek is the default provider AND the standing backup.

The console's brain is local-first; the cloud API is the option an operator switches on (or the
boost the stick uses when the host is weak). That option has one default and one failover order,
and these tests pin them.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import cloud_api  # noqa: E402


class ApiDefaultPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = cloud_api.API_PATH
        cloud_api.API_PATH = Path(self._tmp.name) / "api.json"

    def tearDown(self) -> None:
        cloud_api.API_PATH = self._orig
        self._tmp.cleanup()

    def wire(self, **obj) -> None:
        cloud_api.API_PATH.write_text(json.dumps(obj), encoding="utf-8")

    def test_deepseek_is_the_shipped_default(self) -> None:
        self.assertEqual(cloud_api.DEFAULT_PROVIDER, "deepseek")
        self.assertEqual(cloud_api.DEFAULT_MODEL, "deepseek-chat")
        self.assertEqual(next(iter(cloud_api.PROVIDERS)), "deepseek", "DeepSeek is the first provider")

    def test_blank_config_is_deepseek(self) -> None:
        cur = cloud_api._load()
        self.assertEqual(cur["provider"], "deepseek")
        self.assertEqual(cur["model"], "deepseek-chat")

    def test_unshipped_provider_name_falls_back_to_deepseek(self) -> None:
        self.wire(enabled=True, provider="not-a-provider", model="", key="sk-x")
        cur = cloud_api._load()
        self.assertEqual(cur["provider"], "deepseek")
        self.assertEqual(cur["model"], "deepseek-chat")
        chain = cloud_api.chain_for()
        self.assertEqual([c["provider"] for c in chain], ["deepseek"], "no entry posting to an empty URL")
        self.assertTrue(chain[0]["url"].startswith("https://api.deepseek.com"))

    def test_deepseek_leads_the_backup_order_behind_an_explicit_primary(self) -> None:
        self.wire(enabled=True, provider="groq", model="openai/gpt-oss-20b", key="gsk-primary",
                  keys={"deepseek": "sk-backup"})
        self.assertEqual([c["provider"] for c in cloud_api.chain_for()], ["groq", "deepseek"])

    def test_explicit_primary_still_outranks_deepseek_but_deepseek_beats_other_fallbacks(self) -> None:
        self.wire(enabled=True, provider="gemini", model="gemini-flash-latest", key="AIza-primary",
                  keys={"deepseek": "sk-backup", "openai": "sk-third"}, fallbacks=["openai"])
        self.assertEqual(
            [c["provider"] for c in cloud_api.chain_for()], ["gemini", "deepseek", "openai"]
        )

    def test_public_status_exposes_the_default_for_the_portal(self) -> None:
        st = cloud_api.public_status()
        self.assertEqual(st["default_provider"], "deepseek")
        self.assertTrue(st["is_default"])
        st2 = {"provider": "groq", "key": "gsk"}
        self.wire(enabled=True, **st2)
        self.assertFalse(cloud_api.public_status()["is_default"])

    def test_default_without_a_key_is_still_disabled_never_a_fake_call(self) -> None:
        self.wire(enabled=True, provider="deepseek", key="")
        self.assertEqual(cloud_api.chain_for(), [])
        self.assertFalse(cloud_api.enabled())
        st = cloud_api.public_status()
        self.assertFalse(st["has_key"])
        self.assertEqual(st["provider"], "deepseek")
