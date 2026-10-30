"""A model this host cannot load must not be picked again — and must not poison other hosts.

The real trigger for this module: a PrismML ternary GGUF parses cleanly (so the scanner calls it
runnable) and then dies at load with wrong tensor offsets. Before this, the console retried that
same file on every boot, and never came up.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import model_verdicts  # noqa: E402
import registry  # noqa: E402


class ModelVerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = (model_verdicts.VERDICTS_JSON, model_verdicts._host)
        model_verdicts.VERDICTS_JSON = Path(self._tmp.name) / "model_verdicts.json"
        model_verdicts._host = lambda: "host-a"
        self.model = Path(self._tmp.name) / "broken.gguf"
        self.model.write_bytes(b"x" * 4096)
        self.models = [
            {"id": "qwen2.5:3b", "kind": "chat", "runnable": True, "bytes": 900, "path": str(self.model)},
            {"id": "broken:1b", "kind": "chat", "runnable": True, "bytes": 1000, "path": str(self.model)},
        ]

    def tearDown(self) -> None:
        model_verdicts.VERDICTS_JSON, model_verdicts._host = self._orig
        self._tmp.cleanup()

    def test_a_failed_model_is_remembered_and_never_picked_again(self) -> None:
        self.assertEqual(registry.pick_default(self.models), "qwen2.5:3b")
        model_verdicts.mark_bad("qwen2.5:3b", str(self.model), "engine_launch_failed")
        self.assertIn("qwen2.5:3b", model_verdicts.bad_ids())
        self.assertNotIn("qwen2.5:3b", registry.candidates(self.models))
        self.assertEqual(registry.candidates(self.models)[0], "broken:1b")
        self.assertTrue(model_verdicts.is_bad("qwen2.5:3b", str(self.model)))

    def test_the_verdict_is_per_host(self) -> None:
        model_verdicts.mark_bad("qwen2.5:3b", str(self.model), "engine_launch_failed")
        self.assertIn("qwen2.5:3b", model_verdicts.bad_ids())  # this host skips it
        self.assertEqual(len(registry.candidates(self.models)), 1)
        self.assertEqual(model_verdicts.bad_ids("host-b"), set(), "another PC must not inherit it")
        model_verdicts._host = lambda: "host-b"
        self.assertEqual(len(registry.candidates(self.models)), 2, "host-b still has both brains")

    def test_the_verdict_dies_when_the_file_changes(self) -> None:
        model_verdicts.mark_bad("qwen2.5:3b", str(self.model), "engine_launch_failed")
        self.assertEqual(model_verdicts.bad_ids(), {"qwen2.5:3b"})
        self.model.write_bytes(b"y" * 8192)  # re-copied / re-downloaded weights
        self.assertEqual(model_verdicts.bad_ids(), set(), "a new file earns a new trial")
        self.assertIn("qwen2.5:3b", registry.candidates(self.models))

    def test_clear_forgets_only_what_it_is_asked_to(self) -> None:
        model_verdicts.mark_bad("qwen2.5:3b", str(self.model), "why")
        model_verdicts.mark_bad("broken:1b", str(self.model), "why")
        model_verdicts.clear("qwen2.5:3b")
        self.assertEqual(model_verdicts.bad_ids(), {"broken:1b"})
        model_verdicts.clear()
        self.assertEqual(model_verdicts.bad_ids(), set())

    def test_a_corrupt_store_never_breaks_a_boot(self) -> None:
        model_verdicts.VERDICTS_JSON.write_text("{not json", encoding="utf-8")
        self.assertEqual(model_verdicts.bad_ids(), set())
        self.assertEqual(len(registry.candidates(self.models)), 2)

    def test_coder_brains_are_offered_before_bigger_general_models(self) -> None:
        models = self.models + [
            {"id": "qwen2.5-coder:7b", "kind": "chat", "runnable": True, "bytes": 4_470_000_000,
             "path": str(self.model)},
        ]
        self.assertEqual(registry.candidates(models)[0], "qwen2.5-coder:7b")
        self.assertEqual(registry.tool_rank({"id": "qwen2.5-coder:7b"}), registry.TOOL_RANK_CODER)
        self.assertGreater(
            registry.tool_rank({"id": "qwen2.5-coder:7b"}), registry.tool_rank({"id": "gemma2:9b"})
        )


if __name__ == "__main__":
    unittest.main()
