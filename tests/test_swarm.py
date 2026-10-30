"""Bot swarm: never boot llama; local helper only if 11441 is already up."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import swarm  # noqa: E402


class SwarmTests(unittest.TestCase):
    def test_status_lists_default_agents(self) -> None:
        st = swarm.status()
        ids = {a["id"] for a in st["agents"]}
        self.assertTrue({"helper", "advisor", "forge", "stream"} <= ids)
        self.assertIn("engine_alive", st)

    def test_ask_refuses_when_local_engine_is_down(self) -> None:
        with mock.patch.object(swarm, "live_engine", lambda: {"alive": False}):
            got = swarm.ask("helper", "ping")
        self.assertFalse(got["ok"])
        self.assertEqual(got["error"], "engine_not_booted")
        self.assertIn("GGUF", got["hint"])

    def test_ask_uses_booted_engine_when_alive(self) -> None:
        eng = {"alive": True, "port": 11441, "model": "AnyGGUF", "url": "http://127.0.0.1:11441/v1/chat/completions"}
        with mock.patch.object(swarm, "live_engine", lambda: eng):
            with mock.patch.object(swarm, "_complete", lambda *a, **k: {"ok": True, "reply": "PONG"}):
                got = swarm.ask("helper", "ping")
        self.assertTrue(got["ok"])
        self.assertEqual(got["reply"], "PONG")
        self.assertEqual(got["backend"], "local")

    def test_advise_falls_back_to_local_when_api_is_down(self) -> None:
        eng = {"alive": True, "port": 11441, "model": "AnyGGUF", "url": "http://127.0.0.1:11441/v1/chat/completions"}
        with mock.patch.object(swarm, "advisor_alive", lambda: False):
            with mock.patch.object(swarm, "live_engine", lambda: eng):
                with mock.patch.object(swarm, "_complete", lambda *a, **k: {"ok": True, "reply": "ok"}):
                    got = swarm.advise("second opinion")
        self.assertTrue(got["ok"])
        self.assertEqual(got["backend"], "local_fallback")

    def test_limbs_are_wired_without_widening_core_names(self) -> None:
        from tools import CORE_NAMES, dispatch

        self.assertNotIn("swarm_status", CORE_NAMES)
        with mock.patch.object(swarm, "engine_alive", lambda: False):
            with mock.patch.object(swarm, "advisor_alive", lambda: False):
                st = dispatch("swarm_status", {})
        self.assertTrue(st.get("ok"))
        self.assertIn("agents", st)
