from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from world_clock import CITIES, pulse  # noqa: E402


class WorldClockTests(unittest.TestCase):
    def test_pulse_stamps(self):
        p = pulse()
        self.assertTrue(p.get("ok"))
        self.assertTrue(p.get("utc_iso"))
        self.assertTrue(p.get("unix"))
        self.assertGreaterEqual(len(p.get("cities") or []), 5)
        names = {c["name"] for c in p["cities"]}
        self.assertIn("UTC", names)
        self.assertIn("Tokyo", names)
        self.assertEqual(len(CITIES), len(p["cities"]))


if __name__ == "__main__":
    unittest.main()
