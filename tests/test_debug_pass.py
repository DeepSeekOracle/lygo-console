"""De-bug pass regressions: the fixed failure paths must stay fixed and must stay safe.

Three defects of ONE shape were found by auditing for that shape specifically: a name that is read
but never imported, sitting behind a broad `except` that turns the resulting NameError into a
silent wrong answer. The audit was worth running: the two lygo_engine defects had been invisible
to every test, because the tests patched the functions instead of driving them.

1. `lygo_engine.flash_attn_on()` read CONSOLE_JSON / LOCAL_JSON without importing them, so
   `"flash_attn": "on"` in console.json could never turn the flag on. The setting was dead and
   silently answered "off" forever.
2. `lygo_engine.backend_selection()`'s own safety net — the `except` that promises "a failure here
   must never stop a boot" — referenced ENGINE_DIR, so a backend-layer error raised NameError
   instead of answering "cpu". The fallback failed exactly when it was needed.
3. `lygo_engine.probe()` read ENGINE_DIR on the same path.

These guards are behavioural: each drives the real function down the path that used to raise.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import lygo_engine  # noqa: E402
import model_verdicts  # noqa: E402
import paths  # noqa: E402


class FlashAttnConfigIsWiredTests(unittest.TestCase):
    def test_config_paths_are_imported_not_free_names(self):
        # A free name here is a NameError swallowed by the function's own except: the setting dies.
        self.assertIs(lygo_engine.CONSOLE_JSON, paths.CONSOLE_JSON, "flash_attn_on() reads a name that is not the real config path")
        self.assertIs(lygo_engine.LOCAL_JSON, paths.LOCAL_JSON, "flash_attn_on() reads a name that is not the real local config path")

    def test_setting_on_reaches_the_flag(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "console.json"
            cfg.write_text(json.dumps({"flash_attn": "on"}), encoding="utf-8")
            with mock.patch.object(lygo_engine, "CONSOLE_JSON", cfg), mock.patch.object(
                lygo_engine, "LOCAL_JSON", Path(td) / "local.json"
            ):
                self.assertTrue(lygo_engine.flash_attn_on())

    def test_setting_off_and_broken_config_stay_false_without_raising(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "console.json"
            cfg.write_text(json.dumps({"flash_attn": "off"}), encoding="utf-8")
            with mock.patch.object(lygo_engine, "CONSOLE_JSON", cfg), mock.patch.object(
                lygo_engine, "LOCAL_JSON", Path(td) / "local.json"
            ):
                self.assertFalse(lygo_engine.flash_attn_on())
            cfg.write_text("{not json", encoding="utf-8")
            with mock.patch.object(lygo_engine, "CONSOLE_JSON", cfg), mock.patch.object(
                lygo_engine, "LOCAL_JSON", Path(td) / "local.json"
            ):
                self.assertFalse(lygo_engine.flash_attn_on())


class BackendLayerFailurePathTests(unittest.TestCase):
    """The CPU answer must survive the backend layer dying — that is the whole point of it."""

    def _boom(self):
        return mock.patch("backends.ensure", side_effect=RuntimeError("store is unreadable"))

    def test_backend_selection_answers_cpu_instead_of_raising(self):
        with self._boom():
            sel = lygo_engine.backend_selection()
        self.assertIsInstance(sel, dict)
        self.assertEqual(sel.get("backend"), "cpu")
        self.assertEqual(sel.get("reason"), "backend_layer_error")
        self.assertTrue(str(sel.get("engine_dir") or ""), "no engine_dir in the CPU answer")

    def test_probe_survives_a_backend_layer_error(self):
        with self._boom():
            hw = lygo_engine.probe()
        self.assertIsInstance(hw, dict)
        self.assertEqual(hw.get("backend"), "cpu")
        self.assertIn("ram_bytes", hw)
        self.assertIn("threads", hw)


class VerdictWriteTests(unittest.TestCase):
    def test_verdict_write_is_atomic_and_leaves_no_debris(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "model_verdicts.json"
            with mock.patch.object(model_verdicts, "VERDICTS_JSON", target):
                rec = model_verdicts.mark_bad("some-model", str(Path(td) / "gone.gguf"), "load failed")
                self.assertIsInstance(rec, dict)
                self.assertTrue(target.is_file(), "verdict was not written through the atomic writer")
                self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["hosts"][model_verdicts._host()]["some-model"]["why"], "load failed")
            leftovers = sorted(p.name for p in Path(td).glob("*.tmp"))
            self.assertEqual(leftovers, [], f"temp debris left behind: {leftovers}")


class LaunchFlagTests(unittest.TestCase):
    def test_the_invalid_no_mmap_flag_is_gone(self):
        """`--no-mmap` is not a flag in this build: passing it aborts the launch. -lm none is."""
        src = (ROOT / "src" / "engine.py").read_text(encoding="utf-8")
        self.assertNotIn('"--no-mmap"', src, "--no-mmap is not accepted by this llama.cpp build")
        self.assertIn('"-lm"', src, "the mmap=False path no longer maps to -lm none")


if __name__ == "__main__":
    unittest.main(verbosity=2)
