"""Dispatcher registry and protected boot rules."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import dispatcher  # noqa: E402
from tools import CORE_NAMES, dispatch  # noqa: E402


class DispatcherTests(unittest.TestCase):
    def test_registry_has_core_workers(self) -> None:
        with mock.patch.object(dispatcher, "_tcp", lambda *a, **k: False):
            rows = dispatcher.registry()
        ids = {r["id"] for r in rows}
        self.assertTrue({"hermes", "local", "advisor", "forge"} <= ids)

    def test_local_boot_does_not_spawn_when_engine_is_up(self) -> None:
        with mock.patch("swarm.engine_alive", lambda: True):
            got = dispatcher.boot("local")
        self.assertTrue(got["ok"])
        self.assertIn("already", got["note"])

    def test_forge_and_desktop_hermes_are_not_auto_killed(self) -> None:
        self.assertEqual(dispatcher.restart("forge")["error"], "protected")
        self.assertEqual(dispatcher.restart("desktop-hermes")["error"], "protected")
        self.assertEqual(dispatcher.boot("forge")["error"], "protected")
        self.assertEqual(dispatcher.boot("desktop-hermes")["error"], "protected")

    def test_desktop_hermes_is_connected_on_8080_when_dashboard_is_closed(self) -> None:
        def tcp(host, port, timeout=0.35):
            del host, timeout
            return port == 8080

        def get(url, timeout=0.7, headers=None):
            del timeout, headers
            if url.endswith("/health"):
                return 200, '{"status":"ok"}'
            if url.endswith("/v1/models"):
                return 200, json.dumps({"data": [{"id": "qwen3-4b-instruct"}]})
            return 0, ""

        with mock.patch.object(dispatcher, "_tcp", tcp), mock.patch.object(dispatcher, "_get", get):
            rows = dispatcher.registry()
        desk = next(r for r in rows if r["id"] == "desktop-hermes")
        self.assertEqual(desk["status"], "connected")
        self.assertEqual(desk["port"], 8080)
        self.assertIn("engine", desk["hint"])

    def test_limb_is_not_in_core_names(self) -> None:
        self.assertNotIn("dispatcher", CORE_NAMES)
        with mock.patch.object(dispatcher, "_tcp", lambda *a, **k: False):
            st = dispatch("dispatcher", {"action": "status"})
        self.assertTrue(st.get("ok"))
        self.assertIn("workers", st)
