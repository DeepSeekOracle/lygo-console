"""The evidence rules behind a fail-soft console: the reason must be true, and proof must be proof.

Both were wrong on this box on 2026-09-20 and both were found by reading the recorded reasons rather
than the code: ten models were filed "no_gpu_device" on a machine with an idle RTX 4060 Ti (the reading
came from our CPU-only base binary), and a fit verdict - arithmetic - was about to be accepted as proof
that the GPU had carried a turn (it had not; both GPU backends sat unproven).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import model_check  # noqa: E402
import model_fit  # noqa: E402


class RigTruthTests(unittest.TestCase):
    def test_the_rig_reading_is_a_pair_of_numbers(self):
        total, free = model_fit.rig_vram_mib()
        self.assertIsInstance(total, int)
        self.assertIsInstance(free, int)
        self.assertGreaterEqual(total, free)
        self.assertGreaterEqual(free, 0)

    def test_a_box_with_no_card_says_no_gpu_device(self):
        orig = model_fit.rig_vram_mib
        model_fit.rig_vram_mib = lambda: (0, 0)
        try:
            fit = model_fit.verdict(model_bytes=20 * 1024**3, ctx=4096, ram_total=32581, vram_free_mib=0)
            self.assertEqual(fit["reason"], "no_gpu_device")
            self.assertEqual(fit["ngl"], 0)
        finally:
            model_fit.rig_vram_mib = orig

    def test_a_card_that_reports_nothing_free_is_busy_not_absent(self):
        orig = model_fit.rig_vram_mib
        model_fit.rig_vram_mib = lambda: (8188, 0)
        try:
            fit = model_fit.verdict(model_bytes=20 * 1024**3, ctx=4096, ram_total=32581, vram_free_mib=0)
            self.assertEqual(fit["reason"], "gpu_busy_at_check",
                             "a busy card and an absent card need different next moves")
        finally:
            model_fit.rig_vram_mib = orig

    def test_a_stale_zero_does_not_hide_a_working_card(self):
        # The caller measured 0 while another process held the card. The driver is asked instead, and the
        # balancer plans a real offload rather than writing the model off.
        orig = model_fit.rig_vram_mib
        model_fit.rig_vram_mib = lambda: (8188, 7157)
        try:
            fit = model_fit.verdict(model_bytes=20 * 1024**3, ctx=4096, ram_total=32581, vram_free_mib=0)
            self.assertGreater(fit["ngl"], 0)
            self.assertEqual(fit["mode"], "gpu_partial")
        finally:
            model_fit.rig_vram_mib = orig


class EngineWordsTests(unittest.TestCase):
    def test_the_reason_quotes_the_loader_not_our_wrapper(self):
        tail = ("main: server is listening | srv load_model: failed to load model | "
                "key qwen35moe.rope.dimension_sections has wrong array length; expected 4, got 3")
        self.assertIn("wrong array length", model_check.own_words(tail))
        cls, _hint = model_check.classify_failure(tail, "RuntimeError: llama-server exited 1")
        self.assertEqual(cls, "engine", "our loader being older than the file is not the rig's fault")

    def test_a_hash_shaped_run_never_leaves_the_module(self):
        blob = "srv load_model: failed to open D:\\V\\cas\\blobs\\sha256-" + "a1b2c3d4" * 8
        out = model_check.redact(blob)
        self.assertNotRegex(out, r"[0-9A-Fa-f]{32,}")
        self.assertIn("[hash]", out)

    def test_the_recorded_reason_is_the_engine_sentence(self):
        tail = "load_tensors: error: tensor 'blk.1.ffn_down_exps.weight' has wrong shape; expected 1, got 2"
        self.assertIn("wrong shape", model_check.own_words(tail))


if __name__ == "__main__":
    unittest.main()
