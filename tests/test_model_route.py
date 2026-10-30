"""Routing and fail-soft: the two rules the console must never get wrong.

A wrong route answers a photo with a model that cannot see, and a wrong failure tells the operator
their agent is broken when their box is simply too small. Both are tested here on fixtures, no GPU.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import model_route  # noqa: E402

FIXTURE_REG = {"selected": "gemma4-12b", "models": [
    {"id": "gemma4-12b", "path": "<MODELS>/gemma4-12b.gguf", "kind": "chat", "bytes": 7_400_000_000,
     "mmproj": "<MODELS>/gemma4-12b-mmproj.gguf"},
    {"id": "qwen2.5-coder:7b", "path": "<MODELS>/qwen2.5-coder-7b.gguf", "kind": "chat", "bytes": 4_680_000_000},
    {"id": "nemotron-3-super:latest", "path": "I:/VAULT/cas/nemotron", "kind": "chat", "bytes": 86_830_000_000},
]}
FIXTURE_CHECK = {"host": {"at": "2026-09-20T21:00:00"}, "models": {
    "gemma4-12b": {"id": "gemma4-12b", "caps": ["text", "image-in"], "verdict": "runs", "boot_s": 45.7,
                   "fit": {"mode": "gpu_full", "ngl": 99}, "gpu_layers_used": 99, "devices_seen": 1,
                   "probe": {"gen_tps": 6.8}},
    "qwen2.5-coder:7b": {"id": "qwen2.5-coder:7b", "caps": ["text", "coder"], "verdict": "runs",
                         "probe": {"gen_tps": 11.9}},
    "nemotron-3-super:latest": {"id": "nemotron-3-super:latest", "caps": ["text"], "verdict": "failed_soft",
                                "fail_class": "rig", "why": "no health after 180s"},
}}


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "registry.json").write_text(json.dumps(FIXTURE_REG), encoding="utf-8")
        (d / "model_check.json").write_text(json.dumps(FIXTURE_CHECK), encoding="utf-8")
        self._save, self._check = model_route.SAVE, model_route.CHECK
        model_route.SAVE, model_route.CHECK = d, d / "model_check.json"

    def tearDown(self):
        model_route.SAVE, model_route.CHECK = self._save, self._check
        self.tmp.cleanup()

    def test_a_photo_is_routed_to_a_model_that_can_see(self):
        best = model_route.best_for("image-in")
        self.assertIsNotNone(best)
        self.assertEqual(best["id"], "gemma4-12b")

    def test_a_broken_model_is_never_chosen_but_stays_visible(self):
        ids = [r["id"] for r in model_route.routes()]
        self.assertIn("nemotron-3-super:latest", ids)
        picks = [r["id"] for r in model_route.routes() if r["runs"]]
        self.assertNotIn("nemotron-3-super:latest", picks)

    def test_the_fastest_proven_model_comes_first(self):
        self.assertEqual(model_route.routes()[0]["id"], "qwen2.5-coder:7b")

    def test_the_operators_own_pick_wins_when_it_can_take_the_turn(self):
        self.assertEqual(model_route.best_for("text", prefer="gemma4-12b")["id"], "gemma4-12b")

    def test_a_failed_boot_is_a_rig_limit_with_a_way_forward(self):
        payload = model_route.soft_failure("nemotron-3-super:latest", "needs 90000 MiB, host has 28000 MiB",
                                           cap="text")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["class"], "rig")
        self.assertFalse(payload["agent_fault"])
        self.assertIn("not a fault in the console", payload["message"])
        self.assertTrue(payload["alternatives"])

    def test_a_capability_that_exists_only_in_the_cloud_is_still_a_route(self):
        # The local lineup has no sound model at all; the console must still offer a route that can
        # take audio rather than saying the request is impossible.
        ids = [r["id"] for r in model_route.routes("sound-in")]
        self.assertIn("api:gemini", ids)

    def test_every_cloud_capability_carries_its_source(self):
        for r in model_route.routes():
            if r.get("route") == "cloud" and r.get("caps"):
                self.assertTrue(r.get("caps_source"), f"{r['id']} claims caps without a source")

    def test_an_unknown_provider_claims_nothing(self):
        custom = [r for r in model_route.routes() if r["id"] == "api:custom"][0]
        self.assertEqual(custom["caps"], [])
        self.assertIn("unknown", custom["why"].lower())

    def test_image_generation_is_our_own_service_not_a_chat_model(self):
        route = model_route.best_for("image-out")
        self.assertEqual(route["route"], "media")

    def test_the_gpu_proof_needs_layers_the_engine_really_offloaded(self):
        proof = model_route.gpu_proven()
        self.assertIsNotNone(proof)
        self.assertEqual(proof["id"], "gemma4-12b")
        self.assertEqual(proof["gpu_layers_used"], 99)
        self.assertEqual(proof["devices_seen"], 1)

    def test_a_plan_is_never_accepted_as_proof_the_gpu_carried_a_turn(self):
        # Measured on this box 2026-09-20: the arithmetic said gpu_full for eight models while the engine
        # in use was the CPU-only base build. A plan must never silence the "GPU not proven" amber.
        # In memory only - a test never edits the operator's record.
        orig = model_route.results
        model_route.results = lambda: {
            "m": {"id": "m", "verdict": "runs", "fit": {"mode": "gpu_full", "ngl": 99},
                  "probe": {"gen_tps": 40.0}}
        }
        try:
            self.assertIsNone(model_route.gpu_proven(), "a fit verdict is arithmetic, not evidence")
        finally:
            model_route.results = orig

    def test_a_model_that_never_ran_proves_nothing_about_the_gpu(self):
        # Swap the reader, not its cache: no dependency on how results() memoises.
        orig = model_route.results
        model_route.results = lambda: {}
        try:
            self.assertIsNone(model_route.gpu_proven())
        finally:
            model_route.results = orig

    def test_the_rig_report_says_what_runs_and_what_is_a_rig_limit(self):
        rep = model_route.rig_report()
        self.assertEqual(rep["models_total"], 3)
        self.assertEqual(rep["models_running"], 2)
        self.assertEqual([r["id"] for r in rep["rig_limits"]], ["nemotron-3-super:latest"])
        self.assertEqual(rep["wiring_faults"], [])
        self.assertEqual(rep["caps"].get("text"), 3)


if __name__ == "__main__":
    unittest.main()
