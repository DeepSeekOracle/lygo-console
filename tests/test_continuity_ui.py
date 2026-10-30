from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ContinuityUiTests(unittest.TestCase):
    def test_not_mashed(self):
        js = (ROOT / "portal" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "portal" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('slice(0, 400) + "\\n---\\n"', js)
        self.assertIn("soul-edit", html)
        self.assertIn("id-edit", html)
        self.assertIn("mem-edit", html)
        self.assertIn('data-cont="soul"', html)
        self.assertIn('data-cont="id"', html)
        self.assertIn('data-cont="mem"', html)
        self.assertIn("/api/identity", js)


if __name__ == "__main__":
    unittest.main()
