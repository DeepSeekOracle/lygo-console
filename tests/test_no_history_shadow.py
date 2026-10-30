from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NoHistoryShadowTests(unittest.TestCase):
    def test_app_js_does_not_shadow_window_history(self):
        src = (ROOT / "portal" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("let history", src)
        self.assertNotIn("var history", src)
        self.assertIn("chatHistory", src)
        self.assertIn("window.history.replaceState", src)


if __name__ == "__main__":
    unittest.main()
