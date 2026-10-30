"""The roster card is a local read. Boot health must not walk skill trees."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import importlib.util  # noqa: E402

import roster  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "rosterinfo_backend",
    ROOT / "src" / "modules" / "lygo.rosterinfo" / "backend.py",
)
backend = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(backend)


class FakeReq:
    def __init__(self, q: str = "") -> None:
        self._q = q
        self.code = 0
        self.payload = None

    def get(self, key, default=None):
        if key == "q":
            return self._q
        return default

    def json(self, code, payload):
        self.code = code
        self.payload = payload


class RosterTests(unittest.TestCase):
    def test_health_does_not_scan(self) -> None:
        with mock.patch.object(roster, "snapshot", side_effect=AssertionError("scanned")):
            got = backend.health(None)
        self.assertTrue(got["ok"])
        self.assertIn("GET /api/roster", got["detail"])

    def test_unity_plugin_source_counts_as_unity(self) -> None:
        self.assertTrue(roster.is_unity("ui", r"C:\skills\ui\SKILL.md", "unity"))
        self.assertTrue(roster.is_unity("unity-cli", "", "extra"))
        self.assertFalse(roster.is_unity("filmcraft", r"D:\kit\skills\filmcraft\SKILL.md", "bundled"))

    def test_the_card_names_unity_and_limbs(self) -> None:
        snap = {
            "at": "2026-10-24T00:00:00",
            "skills": [
                {"slug": "unity-cli", "description": "editor", "source": "unity", "enabled": True, "unity": True},
                {"slug": "filmcraft", "description": "film", "source": "bundled", "enabled": True, "unity": False},
            ],
            "unity": [
                {"slug": "unity-cli", "description": "editor", "source": "unity", "enabled": True, "unity": True},
            ],
            "limbs": [
                {"name": "skill_read", "core": True, "description": "read a skill"},
            ],
            "roots": ["<USER>/.grok/skills"],
        }
        with mock.patch.object(roster, "snapshot", return_value=snap):
            with mock.patch.object(roster, "unity_plugin_roots", return_value=[Path("plugin")]):
                with mock.patch.object(roster, "grok_skills_root", return_value=Path("grok")):
                    full = roster.card("")
                    got = roster.card("unity")
        self.assertEqual(full["state"], "green")
        self.assertEqual(full["n_skills"], 2)
        self.assertEqual(full["n_unity"], 1)
        self.assertIn("skill_read", full["limb_names"])
        self.assertEqual(got["n_skills"], 2)
        self.assertEqual(got["skills"][0]["slug"], "unity-cli")
        self.assertNotIn("skill_read", got["limb_names"])
        self.assertTrue(any(row["k"] == "unity-cli" for group in got["groups"] for row in group["rows"]))

    def test_register_and_route(self) -> None:
        reg = backend.register(None)
        self.assertEqual(reg["routes"][0][1], "/api/roster")
        self.assertEqual(reg["limbs"], [])
        req = FakeReq("skill")
        snap = {
            "at": "2026-10-24T00:00:00",
            "skills": [],
            "unity": [],
            "limbs": [],
            "roots": [],
        }
        with mock.patch.object(roster, "snapshot", return_value=snap):
            with mock.patch.object(roster, "unity_plugin_roots", return_value=[]):
                with mock.patch.object(roster, "grok_skills_root", return_value=None):
                    backend.data(None, req)
        self.assertEqual(req.code, 200)
        self.assertEqual(req.payload["state"], "amber")
        self.assertTrue(req.payload["missing"])


if __name__ == "__main__":
    unittest.main()
