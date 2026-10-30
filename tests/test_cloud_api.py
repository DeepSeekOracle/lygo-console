from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloud_api import PROVIDERS, public_status, sanitize_key  # noqa: E402


class CloudApiTests(unittest.TestCase):
    def test_deepseek_provider(self):
        self.assertIn("deepseek", PROVIDERS)
        self.assertIn("api.deepseek.com", PROVIDERS["deepseek"]["url"])
        self.assertIn("gemini", PROVIDERS)
        self.assertIn("generativelanguage.googleapis.com", PROVIDERS["gemini"]["url"])

    def test_sanitize_strips_junk(self):
        k = sanitize_key('  "sk-test\u200b"  ')
        self.assertEqual(k, "sk-test")

    def test_public_status_never_has_key(self):
        st = public_status()
        self.assertNotIn("sk-", json_blob(st))
        self.assertIn("secret", st)
        self.assertEqual(st["secret"], "never_echoed")


def json_blob(obj) -> str:
    import json

    return json.dumps(obj)


if __name__ == "__main__":
    unittest.main()
