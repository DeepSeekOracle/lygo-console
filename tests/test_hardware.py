"""Live hardware sensing: what this box can use RIGHT NOW.

A reading taken at boot is a guess by the time a picture is asked for. Measured on this host 2026-09-21:
the console booted gemma4-12b (10.9 GB resident, 79 GPU layers offloaded) onto a card with 8188 MiB, and
the picture engine then died asking for 644.05 MB with `available 0.00 MB device`. The number that
mattered was never a boot-time total - it was the free VRAM in the second the render was asked for.

These tests pin the parsing and the decision, not the hardware: every nvidia-smi reading is injected.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import hardware as hw  # noqa: E402

# The real line from this host (nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu --format=csv,noheader)
REAL_GPU_LINE = "NVIDIA GeForce RTX 4060 Ti, 8188 MiB, 839 MiB, 7111 MiB, 0 %"
# What it says while the chat model holds the card (measured during the failing render)
BUSY_GPU_LINE = "NVIDIA GeForce RTX 4060 Ti, 8188 MiB, 8056 MiB, 132 MiB, 96 %"
# SHAPE fixture, not a reading this box produced: measured 2026-09-21 with gemma4-12b resident, this
# driver (Windows/WDDM) reported **0 MiB** for llama-server as well - per-process VRAM is not accounted
# here. Free VRAM from the GPU query IS reliable (87 MiB measured with the engine up, 7111 MiB without).
# So holder_note() names a holder only when one has memory, and says nothing otherwise.
REAL_APP_LINE = "117204, 7400 MiB"


class HardwareTestCase(unittest.TestCase):
    """No test may inherit another's reading: the cache is module-level and its TTL is two seconds."""

    def setUp(self):
        hw.reset_cache()


class GpuParsingTests(HardwareTestCase):
    def test_the_real_line_parses_into_numbers(self):
        with mock.patch.object(hw, "_run", lambda argv, **kw: (0, REAL_GPU_LINE + "\n", "")):
            g = hw.gpus(force=True)
        self.assertEqual(len(g), 1)
        self.assertIn("4060 Ti", g[0]["name"])
        self.assertEqual(g[0]["total_mib"], 8188)
        self.assertEqual(g[0]["used_mib"], 839)
        self.assertEqual(g[0]["free_mib"], 7111)
        self.assertEqual(g[0]["util_pct"], 0)

    def test_a_card_that_is_busy_reads_as_busy(self):
        with mock.patch.object(hw, "_run", lambda argv, **kw: (0, BUSY_GPU_LINE + "\n", "")):
            self.assertEqual(hw.free_vram_mib(), 132)

    def test_no_measurable_card_is_a_blank_that_still_decides(self):
        with mock.patch.object(hw, "_run", lambda argv, **kw:
                               (1, "", "'nvidia-smi' is not recognized as an internal or external command")):
            self.assertEqual(hw.free_vram_mib(), 0)
            route, why = hw.picture_route(644)
            self.assertEqual(route, "cpu", "no card means the CPU route, not a crash")
            self.assertTrue(why, "and it must say why: " + why)

    def test_a_reading_that_raises_never_takes_the_caller_down(self):
        # Patched at subprocess (below _run), because _run IS the guard: a raise inside it would be
        # testing the mock, not the module.
        def boom(*a, **kw):
            raise OSError("nvidia-smi missing")

        with mock.patch.object(hw.subprocess, "run", boom):
            snap = hw.snapshot(force=True)
        self.assertIsInstance(snap, dict)
        self.assertFalse(snap["available"])
        self.assertEqual(hw.free_vram_mib(), 0)


class TheDecisionTests(HardwareTestCase):
    def test_a_free_card_gets_the_gpu_route(self):
        with mock.patch.object(hw, "_run", lambda argv, **kw: (0, REAL_GPU_LINE + "\n", "")):
            route, why = hw.picture_route(644)
        self.assertEqual(route, "cuda")
        self.assertIn("7111", why)

    def test_a_full_card_goes_to_the_cpu_and_names_the_holder(self):
        def run(argv, **kw):
            if "compute-apps" in " ".join(argv):
                return 0, REAL_APP_LINE + "\n", ""
            return 0, BUSY_GPU_LINE + "\n", ""

        with mock.patch.object(hw, "_run", run), \
                mock.patch.object(hw, "_proc_name", lambda pid: "llama-server"):
            route, why = hw.picture_route(644)
        self.assertEqual(route, "cpu")
        self.assertIn("644", why, "the reason must carry the number the render needed: " + why)
        with mock.patch.object(hw, "_run", run), \
                mock.patch.object(hw, "_proc_name", lambda pid: "llama-server"):
            note = hw.holder_note()
        self.assertIn("llama-server", note)
        self.assertIn("7400", note)


class ZeroMibHoldersTests(HardwareTestCase):
    """Measured on this host 2026-09-21: nvidia-smi lists 23 graphics contexts for this card (dwm,
    explorer, chrome, msedge, NVIDIA Overlay ...), nearly all reporting **0 MiB**. The first version of
    holder_note() picked the largest and said "dwm.exe (pid 1504) holds 0 MiB of the card" - a sentence
    that reads as a fault and names an innocent process. Only a holder with memory counts.
    """

    NOISY = ("1504, 0 MiB\n31000, 0 MiB\n117204, 7400 MiB\n")
    ALL_ZERO = "1504, 0 MiB\n31000, 0 MiB\n"

    def _run(self, apps):
        def run(argv, **kw):
            if "compute-apps" in " ".join(argv):
                return 0, apps, ""
            return 0, BUSY_GPU_LINE + "\n", ""

        return run

    def test_the_named_holder_is_the_one_with_memory(self):
        with mock.patch.object(hw, "_run", self._run(self.NOISY)), \
                mock.patch.object(hw, "_proc_name", lambda pid: "llama-server" if pid == 117204 else "dwm.exe"):
            note = hw.holder_note()
        self.assertIn("llama-server", note)
        self.assertIn("7400", note)
        self.assertNotIn("holds 0 MiB", note, "a process holding nothing is not the holder: " + note)

    def test_a_card_held_by_nothing_measurable_says_so_instead_of_blaming_dwm(self):
        with mock.patch.object(hw, "_run", self._run(self.ALL_ZERO)), \
                mock.patch.object(hw, "_proc_name", lambda pid: "dwm.exe"):
            note = hw.holder_note()
            route, why = hw.picture_route(644)
        self.assertNotIn("0 MiB", note)
        self.assertNotIn("dwm", why)
        self.assertEqual(route, "cpu")


class CachingTests(HardwareTestCase):
    def test_two_readings_inside_the_ttl_ask_the_card_once(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return 0, REAL_GPU_LINE + "\n", ""

        with mock.patch.object(hw, "_run", run):
            hw.snapshot(force=True)
            n = len(calls)
            hw.snapshot()
            hw.snapshot()
        self.assertEqual(len(calls), n, "a page polling the panel must not spawn nvidia-smi per view")

    def test_force_is_the_way_to_ask_again(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return 0, REAL_GPU_LINE + "\n", ""

        with mock.patch.object(hw, "_run", run):
            hw.snapshot(force=True)
            hw.snapshot(force=True)
        self.assertGreater(len(calls), 1)


class ThisBoxTests(HardwareTestCase):
    """The real readings, run on the host doing the asking: never a fabricated number."""

    def test_ram_is_measured_for_real(self):
        r = hw.ram()
        self.assertGreater(r["total_mib"], 0)
        self.assertGreaterEqual(r["available_mib"], 0)
        self.assertLessEqual(r["available_mib"], r["total_mib"])

    def test_cpu_is_measured_for_real(self):
        c = hw.cpu()
        self.assertGreaterEqual(c["cores"], 1)
        self.assertGreaterEqual(c["pct_busy"], 0.0)
        self.assertLessEqual(c["pct_busy"], 100.0)


class RenderNeedTests(HardwareTestCase):
    """The number a render has to clear is the CHECKPOINT, not the shortfall a dead attempt reported.

    MEASURED on this host 2026-09-23 with the 6.9 GB SDXL-Turbo checkpoint: 7113 MiB free drew a real
    1024x1024 picture on the CUDA build in 12.5 s, and with the chat model resident there is ~0 MiB free.
    A decision that only asked for 644 MiB sent a render to a card that could not hold the weights (the
    CUDA attempt died at 19.8 s, exit 1, "cannot make enough memory available on CUDA0").
    """

    CHECKPOINT = 6938081905  # the shipped sd_xl_turbo_1.0_fp16.safetensors, read off disk

    def test_the_shortfall_is_a_floor_not_a_budget(self):
        self.assertEqual(hw.render_need_mib(0), hw.RENDER_NEED_MIB)
        self.assertEqual(hw.render_need_mib(None), hw.RENDER_NEED_MIB)
        self.assertEqual(hw.render_need_mib(4096), hw.RENDER_NEED_MIB, "a tiny file must not lower it")

    def test_a_real_checkpoint_measures_the_need(self):
        need = hw.render_need_mib(self.CHECKPOINT)
        self.assertEqual(need, self.CHECKPOINT // (1024 * 1024) + hw.RENDER_HEADROOM_MIB)
        self.assertGreater(need, 6000)
        self.assertGreater(need, hw.RENDER_NEED_MIB)

    def test_a_card_the_chat_model_holds_is_not_asked_to_draw(self):
        with mock.patch.object(hw, "free_vram_mib", lambda: 132), \
                mock.patch.object(hw, "holder_note", lambda: ""):
            route, why = hw.picture_route(hw.render_need_mib(self.CHECKPOINT))
        self.assertEqual(route, "cpu")
        self.assertIn("132", why)

    def test_a_card_that_can_hold_the_checkpoint_still_gets_the_render(self):
        with mock.patch.object(hw, "free_vram_mib", lambda: 7113), \
                mock.patch.object(hw, "holder_note", lambda: ""):
            route, why = hw.picture_route(hw.render_need_mib(self.CHECKPOINT))
        self.assertEqual(route, "cuda")
        self.assertIn("7113", why)


if __name__ == "__main__":
    unittest.main()
