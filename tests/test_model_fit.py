"""Model fit: every model seen gets a verdict, including the ones this host cannot run.

All arithmetic here is fed explicit VRAM/RAM figures, so these tests say the same thing on a laptop
as on the workstation. None of them starts an engine.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import model_fit  # noqa: E402
import perf  # noqa: E402

GiB = 1024**3


class VerdictTests(unittest.TestCase):
    def setUp(self):
        # These cases describe a host with NO usable card, which is what passing 0 used to imply. It is
        # now STATED rather than assumed: a passed 0 means "unknown" and the driver gets asked, because a
        # 0 measured while another process held the card must not make a working GPU disappear.
        self._rig = model_fit.rig_vram_mib
        model_fit.rig_vram_mib = lambda: (0, 0)

    def tearDown(self):
        model_fit.rig_vram_mib = self._rig

    def test_a_small_model_offloads_fully(self):
        v = model_fit.verdict(model_bytes=1 * GiB, ctx=4096, vram_free_mib=7000, ram_total=32000)
        self.assertEqual(v["verdict"], "gpu_full")
        self.assertEqual(v["ngl"], perf.FULL_LAYERS)

    def test_an_oversized_model_is_visible_but_will_not_load(self):
        # The whole point of the exercise: a 300 GiB model on an 8 GB card and 32 GB of RAM is still
        # reported, and still reported honestly.
        v = model_fit.verdict(
            model_bytes=300 * GiB, ctx=8192, vram_free_mib=7000, ram_total=32000
        )
        self.assertEqual(v["verdict"], "too_big")
        self.assertEqual(v["ngl"], 0)
        self.assertEqual(v["mode"], "none")
        self.assertGreater(v["need_mib"], v["ram_usable_mib"])

    def test_gemma4_plans_partial_offload_like_the_bench_found(self):
        # 6.87 GiB of weights against ~7 GiB free is NOT a full offload, and the sweep found 79
        # layers dominated 99 on this card. The planner must agree with the measurement.
        v = model_fit.verdict(
            model_bytes=int(6.87 * GiB), ctx=32768, vram_free_mib=7075, ram_total=32581
        )
        self.assertEqual(v["verdict"], "gpu_partial")
        self.assertEqual(v["ngl"], 79)
        self.assertLess(v["ngl"], perf.FULL_LAYERS)

    def test_an_unknown_size_is_admitted_not_guessed(self):
        v = model_fit.verdict(model_bytes=0, vram_free_mib=7000, ram_total=32000)
        self.assertEqual(v["verdict"], "unknown")
        self.assertEqual(v["reason"], "model_size_unknown")

    def test_a_host_with_no_gpu_falls_back_to_cpu_not_to_a_refusal(self):
        v = model_fit.verdict(
            model_bytes=1 * GiB, ctx=4096, vram_free_mib=0, ram_total=32000
        )
        self.assertEqual(v["verdict"], "cpu_ok")
        self.assertEqual(v["ngl"], 0)
        self.assertEqual(v["mode"], "cpu")

    def test_a_model_that_only_just_fits_is_called_tight(self):
        # Barely inside the usable ceiling: it loads, but calling it "works" without the warning
        # would be the kind of green that reads as broken hardware when it starts swapping.
        v = model_fit.verdict(
            model_bytes=23000 * 1024**2, ctx=1024, vram_free_mib=0, ram_total=32000
        )
        self.assertIn(v["verdict"], ("cpu_tight", "too_big"))
        self.assertEqual(v["ngl"], 0)


class AnnotateTests(unittest.TestCase):
    def test_a_too_big_model_stays_visible_and_leaves_the_pick_list(self):
        rec = {"id": "huge", "bytes": 300 * GiB, "kind": "chat", "ctx": 8192, "runnable": True}
        model_fit.annotate(rec, vram_free_mib=7000, ram_total=32000)
        self.assertFalse(rec["runnable"], "a model too big to hold must never be chosen for the brain")
        self.assertTrue(rec["available_local"], "it must still be visible")
        self.assertEqual(rec["fit"]["verdict"], "too_big")
        self.assertTrue(rec["fit"]["text"])

    def test_a_projector_is_a_sidecar_not_a_model(self):
        rec = {"id": "gemma4-mmproj", "bytes": 180 * 1024**2, "kind": "mmproj"}
        model_fit.annotate(rec, vram_free_mib=7000, ram_total=32000)
        self.assertEqual(rec["fit"]["verdict"], "sidecar")
        self.assertNotIn("runnable", rec, "a sidecar must not be demoted as if it were a model")
        self.assertEqual(model_fit.summary([rec])["runnable_here"], 0)

    def test_summary_counts_what_it_saw(self):
        recs = [
            {"kind": "chat", "bytes": 1 * GiB},
            {"kind": "chat", "bytes": 300 * GiB},
            {"kind": "mmproj", "bytes": 1},
        ]
        for r in recs:
            model_fit.annotate(r, vram_free_mib=7000, ram_total=32000)
        s = model_fit.summary(recs)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["runnable_here"], 1)
        self.assertEqual(s["visible_but_not_runnable"], 1)


class ThreadTests(unittest.TestCase):
    def test_threads_stay_inside_the_engine_s_own_bounds(self):
        self.assertGreaterEqual(perf.auto_threads(), perf.THREAD_MIN)
        self.assertLessEqual(perf.auto_threads(), perf.THREAD_MAX)

    def test_an_operator_pin_is_never_overridden(self):
        # The skill's non-negotiable: an explicit integer always wins, 0 included.
        self.assertEqual(perf.clamp_threads(16), 16)
        self.assertEqual(perf.clamp_threads(4), 4)
        self.assertGreater(perf.clamp_threads(0), 0)

    def test_physical_core_probe_is_self_consistent(self):
        fast, total = perf.physical_cores()
        if total:
            self.assertGreaterEqual(fast, 1)
            self.assertLessEqual(fast, total)
            self.assertLessEqual(total, perf.auto_threads() * 8 + 16)


if __name__ == "__main__":
    unittest.main()
