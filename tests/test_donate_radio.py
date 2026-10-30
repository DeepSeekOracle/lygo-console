from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DonateRadioTests(unittest.TestCase):
    def test_console_portal(self):
        html = (ROOT / "portal" / "index.html").read_text(encoding="utf-8")
        # the 15-minute donate popup layer was replaced by inline links in the footer hint
        self.assertIn("Donate", html)
        self.assertIn("paypal.com/paypalme/ExcavationPro", html)
        self.assertIn("patreon.com/Excavationpro", html)
        self.assertIn("radioEl", html)
        self.assertIn("chatagent.ca/sources/", html)
        self.assertIn("data-radio-vol", html)
        js = (ROOT / "portal" / "donate.js").read_text(encoding="utf-8")
        self.assertIn("15 * 60 * 1000", js)
        self.assertIn("opened", js)
        radio = (ROOT / "portal" / "radio.js").read_text(encoding="utf-8")
        self.assertIn("wantPlay: true", radio)
        self.assertIn("asiancoastline.com", radio)

    def test_web_portal(self):
        html = (ROOT / "web_portal" / "index.html").read_text(encoding="utf-8")
        self.assertIn("donateLayer", html)
        self.assertIn("radioEl", html)
        self.assertIn("LYGO TV", html)


if __name__ == "__main__":
    unittest.main()
