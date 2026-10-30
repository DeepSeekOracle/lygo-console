"""Local-default brain switching: API activation and automatic local handoff."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import brain_router  # noqa: E402
import cloud_api  # noqa: E402


class RouterTests(unittest.TestCase):
    def test_fallback_codes(self):
        for code in (0, 400, 401, 402, 403, 404, 408, 425, 429, 500, 502, 503, 504):
            self.assertTrue(brain_router.should_fallback(code), code)
        for code in (200, 201, 204):
            self.assertFalse(brain_router.should_fallback(code), code)

    def test_reason_names_the_failure(self):
        self.assertIn("tokens", brain_router.reason(402, "Insufficient Balance"))
        self.assertIn("rate", brain_router.reason(429, "").lower())
        self.assertIn("key", brain_router.reason(401, "").lower())
        self.assertIn("Insufficient Balance", brain_router.reason(402, "Insufficient Balance"))

    def test_mode_is_local_unless_api_is_on_and_healthy(self):
        self.assertEqual(brain_router.LOCAL, brain_router.mode_of({}))
        self.assertEqual(brain_router.LOCAL, brain_router.mode_of({"enabled": True, "has_key": False}))
        self.assertEqual(brain_router.API, brain_router.mode_of({"enabled": True, "has_key": True}))
        # a handoff puts the console back on local until the operator re-activates
        self.assertEqual(brain_router.LOCAL, brain_router.mode_of({"enabled": True, "has_key": True, "degraded": True}))

    def test_label_and_banner(self):
        self.assertEqual("DeepSeek/deepseek-chat", brain_router.label_of({"label": "DeepSeek", "model": "deepseek-chat"}))
        b = brain_router.banner("API out of tokens/credit", "DeepSeek/deepseek-chat", "qwen2.5:3b")
        self.assertIn("local", b.lower())
        self.assertIn("qwen2.5:3b", b)

    def test_handoff_info(self):
        h = brain_router.handoff_info(402, "Insufficient Balance", "DeepSeek/deepseek-chat")
        self.assertEqual(402, h["code"])
        self.assertFalse(h["skipped"])
        self.assertIn("DeepSeek/deepseek-chat", h["api"])
        self.assertTrue(brain_router.handoff_info(402, "x", "y", skipped=True)["skipped"])


class CloudStateTests(unittest.TestCase):
    """State machine on a throwaway config dir — never touches the operator's api.json."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._api_path, self._config = cloud_api.API_PATH, cloud_api.CONFIG
        cloud_api.CONFIG = Path(self.tmp.name)
        cloud_api.API_PATH = cloud_api.CONFIG / "api.json"

    def tearDown(self):
        cloud_api.API_PATH, cloud_api.CONFIG = self._api_path, self._config
        self.tmp.cleanup()

    def test_default_is_local_and_cloud_off(self):
        st = cloud_api.public_status()
        self.assertEqual("local", st["mode"])
        self.assertFalse(st["enabled"])
        self.assertFalse(st["has_key"])
        self.assertFalse(st["degraded"])
        self.assertEqual(0, st["handoffs"])
        self.assertFalse(cloud_api.active())

    def test_key_without_activation_stays_local(self):
        st = cloud_api.save({"key": "sk-throwaway-test", "provider": "deepseek", "model": "deepseek-chat"})
        self.assertTrue(st["has_key"])
        self.assertFalse(st["enabled"])
        self.assertEqual("local", st["mode"])

    def test_activate_then_handoff_then_reactivate(self):
        cloud_api.save({"key": "sk-throwaway-test", "enabled": True})
        self.assertEqual("api", cloud_api.public_status()["mode"])
        self.assertTrue(cloud_api.active())

        st = cloud_api.note_error(402, "API out of tokens/credit — Insufficient Balance")
        self.assertTrue(st["degraded"])
        self.assertEqual(402, st["last_code"])
        self.assertEqual("local", st["mode"])
        self.assertEqual(1, st["handoffs"])
        self.assertFalse(cloud_api.active())
        self.assertTrue(cloud_api.cooldown_active())
        self.assertFalse(cloud_api.cooldown_active(seconds=0.0))

        # the operator pressing API again lifts the handoff
        st = cloud_api.clear_error()
        self.assertFalse(st["degraded"])
        self.assertEqual("api", st["mode"])
        self.assertTrue(cloud_api.active())
        self.assertEqual(1, st["handoffs"])

    def test_switching_back_to_local_keeps_the_key(self):
        cloud_api.save({"key": "sk-throwaway-test", "enabled": True})
        st = cloud_api.save({"enabled": False})
        self.assertFalse(st["enabled"])
        self.assertTrue(st["has_key"])
        self.assertEqual("local", st["mode"])

    def test_note_error_survives_restart_and_stays_local(self):
        cloud_api.save({"key": "sk-throwaway-test", "enabled": True})
        cloud_api.note_error(429, "API rate limit reached")
        raw = json.loads(cloud_api.API_PATH.read_text(encoding="utf-8"))
        self.assertTrue(raw["degraded"])
        self.assertEqual(429, raw["last_code"])
        self.assertTrue(raw["key"], "key must stay saved")
        self.assertEqual("local", cloud_api.public_status()["mode"])

    def test_cooldown_expires(self):
        cloud_api.save({"key": "sk-throwaway-test", "enabled": True})
        cloud_api.note_error(503, "API unavailable")
        raw = json.loads(cloud_api.API_PATH.read_text(encoding="utf-8"))
        raw["last_at"] = time.time() - (cloud_api.COOLDOWN_S + 5)
        cloud_api.API_PATH.write_text(json.dumps(raw), encoding="utf-8")
        self.assertFalse(cloud_api.cooldown_active())
        # still degraded -> mode stays local until re-activated, but the console may retry the API
        self.assertEqual("local", cloud_api.public_status()["mode"])


if __name__ == "__main__":
    unittest.main()
