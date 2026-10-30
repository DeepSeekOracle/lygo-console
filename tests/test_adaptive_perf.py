"""Host-adaptive performance: the stick must run as fast as the host it is plugged into.

Four things are proven here:
  1. the auto-plan places layers in the VRAM that is actually free, and never claims a
     GPU it cannot see;
  2. the config stays the operator's word — an integer ngl/threads is a pin, "auto" is not;
  3. a launch that fails on this host is remembered for this host, and only for this host;
  4. the boot ladder degrades to CPU instead of taking the brain down with the GPU.

Every test here is host-independent: probes are faked, so a CI box with no GPU and a
GamePC with one both run the same assertions.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # the stick/PC profile helper
from _stick_profile import on_a_stick, stick_why  # noqa: E402

import engine  # noqa: E402
import lygo_engine  # noqa: E402
import paths  # noqa: E402
import perf  # noqa: E402

MIB = 1024**2
GIB = 1024**3


def gpu_host(vram_mib=7181, name="NVIDIA GeForce RTX 4060 Ti", backends=("vulkan",), ram_gib=32, threads=None):
    """A probe result from a host with a proven, ready GPU."""
    return {
        "ram_bytes": ram_gib * GIB,
        "ram_gib": ram_gib,
        "vram_bytes": vram_mib * MIB,
        "vram_gib": round(vram_mib / 1024, 2),
        "threads": threads if threads is not None else perf.auto_threads(),
        "llama_binary": True,
        "ssd_stream": True,
        "backends": list(backends),
        "devices": [{"id": "Vulkan0", "name": name, "total_mib": 7949, "free_mib": vram_mib}] if backends else [],
    }


class DeviceParsingTest(unittest.TestCase):
    def test_parses_a_real_list_devices_line(self):
        devs = perf.parse_devices("Available devices:\n  Vulkan0: NVIDIA GeForce RTX 4060 Ti (7949 MiB, 7181 MiB free)\n")
        self.assertEqual(1, len(devs))
        self.assertEqual("Vulkan0", devs[0]["id"])
        self.assertEqual("NVIDIA GeForce RTX 4060 Ti", devs[0]["name"])
        self.assertEqual(7949, devs[0]["total_mib"])
        self.assertEqual(7181, devs[0]["free_mib"])

    def test_an_empty_or_noisy_list_devices_is_just_an_empty_list(self):
        self.assertEqual([], perf.parse_devices(""))
        self.assertEqual([], perf.parse_devices("Available devices:\n"))
        self.assertEqual([], perf.parse_devices("error: unable to load vulkan-1.dll"))

    def test_total_without_a_free_figure_is_treated_as_free(self):
        devs = perf.parse_devices("  Metal0: Apple M2 (10922 MiB)")
        self.assertEqual(10922, devs[0]["free_mib"])

    def test_best_device_is_the_one_with_the_most_free_vram(self):
        devs = [
            {"id": "Vulkan0", "name": "iGPU", "total_mib": 2048, "free_mib": 900},
            {"id": "Vulkan1", "name": "dGPU", "total_mib": 8192, "free_mib": 7000},
        ]
        self.assertEqual("dGPU", perf.best_device(devs)["name"])
        self.assertIsNone(perf.best_device([]))
        self.assertIsNone(perf.best_device(None))


class LayerPlanningTest(unittest.TestCase):
    def test_a_model_that_fits_is_fully_offloaded(self):
        ngl, why = perf.plan_ngl(2 * GIB, 7181)
        self.assertEqual(99, ngl)
        self.assertEqual("fits_vram", why)

    def test_a_big_model_on_a_small_gpu_gets_a_partial_split(self):
        ngl, why = perf.plan_ngl(24 * GIB, 6144)
        self.assertTrue(0 < ngl < 99, ngl)
        self.assertTrue(why.startswith("partial_offload_"), why)

    def test_no_spare_vram_leaves_the_model_on_the_cpu(self):
        self.assertEqual((0, "no_vram_headroom"), perf.plan_ngl(2 * GIB, perf.VRAM_RESERVE_MIB))
        self.assertEqual((0, "no_vram_headroom"), perf.plan_ngl(2 * GIB, perf.VRAM_RESERVE_MIB - 1))

    def test_a_sliver_of_vram_is_not_worth_a_partial_split(self):
        ngl, why = perf.plan_ngl(60 * GIB, 1800)
        self.assertEqual(0, ngl)
        self.assertEqual("vram_too_small_for_partial_offload", why)

    def test_an_unknown_model_size_offloads_everything(self):
        self.assertEqual((99, "model_size_unknown_full_offload"), perf.plan_ngl(0, 8000))

    def test_no_device_at_all_is_cpu(self):
        self.assertEqual((0, "no_gpu_device"), perf.plan_ngl(2 * GIB, 0))


class ProfileResolutionTest(unittest.TestCase):
    def setUp(self):
        self._perf_json = perf.PERF_JSON
        self._td = tempfile.TemporaryDirectory()
        perf.PERF_JSON = Path(self._td.name) / "perf.json"  # no host memory leaks between tests

    def tearDown(self):
        perf.PERF_JSON = self._perf_json
        self._td.cleanup()

    def _lim(self, **kw):
        base = {"ngl": None, "threads": None, "ctx_default": 4096, "ctx_max": 8192, "source": "console.json"}
        return {**base, **kw}

    def test_a_proven_device_gets_full_offload(self):
        prof = perf.resolve(lim=self._lim(), hw=gpu_host(), model_bytes=2 * GIB, model_id="qwen2.5:3b")
        self.assertEqual(99, prof["ngl"])
        self.assertEqual("gpu_full", prof["mode"])
        self.assertEqual("NVIDIA GeForce RTX 4060 Ti", prof["device"])
        self.assertEqual(["vulkan"], prof["backends"])
        self.assertFalse(prof["fallback"])

    def test_an_engine_build_without_a_backend_stays_on_the_cpu(self):
        hw = gpu_host(backends=())
        hw["devices"] = [{"id": "Vulkan0", "name": "RTX 4060 Ti", "total_mib": 7949, "free_mib": 7181}]
        prof = perf.resolve(lim=self._lim(), hw=hw, model_bytes=2 * GIB, model_id="qwen2.5:3b")
        self.assertEqual(0, prof["ngl"])
        self.assertEqual("cpu", prof["mode"])
        self.assertEqual("engine_cpu_only", prof["reason"])

    def test_a_backend_with_no_device_stays_on_the_cpu(self):
        hw = gpu_host(backends=("vulkan",))
        hw["devices"] = []  # backend DLL loaded, but it names no device
        hw["vram_bytes"] = 8 * GIB  # a VRAM number alone is not proof of a usable device
        prof = perf.resolve(lim=self._lim(), hw=hw, model_bytes=0, model_id="m")
        self.assertEqual(0, prof["ngl"])
        self.assertEqual("no_gpu_device", prof["reason"])

    def test_config_pins_beat_a_proven_gpu(self):
        prof = perf.resolve(lim=self._lim(ngl=0, threads=4), hw=gpu_host(), model_bytes=2 * GIB, model_id="m")
        self.assertEqual(0, prof["ngl"])
        self.assertEqual(4, prof["threads"])
        self.assertEqual("pin", prof["source"])
        self.assertEqual("cpu", prof["mode"])

    def test_threads_come_from_the_host_when_unpinned(self):
        prof = perf.resolve(lim=self._lim(), hw=gpu_host(), model_bytes=0, model_id="m")
        self.assertEqual(perf.auto_threads(), prof["threads"])
        self.assertEqual("auto", prof["threads_source"])

    def test_a_probe_that_cannot_see_devices_keeps_the_old_vram_rule(self):
        for vram_gib, expect in ((8, 99), (3, 20), (1, 0)):
            hw = {"ram_bytes": 32 * GIB, "ram_gib": 32, "vram_bytes": vram_gib * GIB, "threads": 8}
            prof = perf.resolve(lim=self._lim(), hw=hw, model_bytes=2 * GIB, model_id="m")
            self.assertEqual(expect, prof["ngl"], vram_gib)
            self.assertFalse(prof["known_devices"])


class HostMemoryTest(ProfileResolutionTest):
    def test_a_failed_launch_on_this_host_is_remembered_and_respected(self):
        hw = gpu_host()
        fp = perf.fingerprint(hw, perf.best_device(hw["devices"]), "qwen2.5:3b")
        perf.remember_host(fp, ngl=0, failed=True, note="llama-server exited 1")
        prof = perf.resolve(lim=self._lim(), hw=hw, model_bytes=2 * GIB, model_id="qwen2.5:3b")
        self.assertEqual(0, prof["ngl"])
        self.assertEqual("host_record", prof["source"])
        self.assertTrue(prof["fallback"])
        self.assertIn("remembered_failure_on_this_host", prof["reason"])

    def test_a_record_with_no_evidence_of_a_fault_must_not_cap_the_plan(self):
        # The regression this guards: a host recorded while it merely ran on CPU has no failure to
        # report. Treating that as one pins the machine to CPU for good - the GPU is planned, measured
        # and then never used - so only a record that says it failed may lower a plan.
        hw = gpu_host()
        fp = perf.fingerprint(hw, perf.best_device(hw["devices"]), "qwen2.5:3b")
        perf.remember_host(fp, ngl=0, mode="cpu", threads=16, note="")
        prof = perf.resolve(lim=self._lim(), hw=hw, model_bytes=2 * GIB, model_id="qwen2.5:3b")
        self.assertEqual(99, prof["ngl"])
        self.assertEqual("auto", prof["source"])

    def test_a_later_success_clears_a_remembered_failure(self):
        hw = gpu_host()
        fp = perf.fingerprint(hw, perf.best_device(hw["devices"]), "qwen2.5:3b")
        perf.remember_host(fp, ngl=0, failed=True, note="died")
        perf.remember_host(fp, ngl=99, failed=False, note="")
        prof = perf.resolve(lim=self._lim(), hw=hw, model_bytes=2 * GIB, model_id="qwen2.5:3b")
        self.assertEqual(99, prof["ngl"])
        self.assertEqual("auto", prof["source"])

    def test_the_host_key_does_not_move_when_the_reported_vram_total_does(self):
        # The same card reports 7949 then 8187 MiB depending on the driver read. A key that moves with
        # it orphans every earlier record, so the host memory silently never applies - which is
        # indistinguishable from having no memory at all.
        hw = gpu_host()
        a = perf.fingerprint(hw, {"name": "NVIDIA GeForce RTX 4060 Ti", "total_mib": 7949}, "gemma4-12b")
        b = perf.fingerprint(hw, {"name": "NVIDIA GeForce RTX 4060 Ti", "total_mib": 8187}, "gemma4-12b")
        self.assertEqual(a, b)

    def test_the_host_key_ignores_available_ram_and_follows_installed_ram(self):
        # Available memory swings by gigabytes between sessions; installed RAM does not. If the key
        # follows the former, opening a browser renames this host and no verdict ever applies again.
        dev = {"name": "NVIDIA GeForce RTX 4060 Ti", "total_mib": 8187}
        a = perf.fingerprint({"ram_bytes": 12 * GIB, "ram_total_bytes": 32 * GIB}, dev, "gemma4-12b")
        b = perf.fingerprint({"ram_bytes": 26 * GIB, "ram_total_bytes": 32 * GIB}, dev, "gemma4-12b")
        c = perf.fingerprint({"ram_bytes": 26 * GIB, "ram_total_bytes": 64 * GIB}, dev, "gemma4-12b")
        self.assertEqual(a, b)
        self.assertNotEqual(b, c)

    def test_a_remembered_success_is_not_a_downgrade(self):
        hw = gpu_host()
        fp = perf.fingerprint(hw, perf.best_device(hw["devices"]), "qwen2.5:3b")
        perf.remember_host(fp, ngl=99, note="ready")
        prof = perf.resolve(lim=self._lim(), hw=hw, model_bytes=2 * GIB, model_id="qwen2.5:3b")
        self.assertEqual(99, prof["ngl"])
        self.assertEqual("auto", prof["source"])

    def test_the_same_verdict_is_not_applied_to_a_different_model(self):
        hw = gpu_host()
        fp = perf.fingerprint(hw, perf.best_device(hw["devices"]), "qwen2.5:3b")
        perf.remember_host(fp, ngl=0, note="died")
        other = perf.resolve(lim=self._lim(), hw=hw, model_bytes=2 * GIB, model_id="llama3.1:8b")
        self.assertEqual(99, other["ngl"])

    def test_a_pin_is_never_overridden_by_host_memory(self):
        hw = gpu_host()
        fp = perf.fingerprint(hw, perf.best_device(hw["devices"]), "m")
        perf.remember_host(fp, ngl=0, note="died")
        prof = perf.resolve(lim=self._lim(ngl=99), hw=hw, model_bytes=2 * GIB, model_id="m")
        self.assertEqual(99, prof["ngl"])
        self.assertEqual("pin", prof["source"])

    def test_the_record_survives_a_corrupt_file(self):
        perf.PERF_JSON.write_text("{not json", encoding="utf-8")
        self.assertIsNone(perf.host_record("deadbeef"))
        perf.remember_host("deadbeef", ngl=4)
        self.assertEqual(4, perf.host_record("deadbeef")["ngl"])
        self.assertEqual("Δ9Φ963-LYGO-PERF-HOST-ADAPTIVE-v1", json.loads(perf.PERF_JSON.read_text())["signature"])

    def test_the_record_is_bounded(self):
        for i in range(perf.HOST_LIMIT + 5):
            perf.remember_host(f"host{i:02d}", ngl=i)
        self.assertLessEqual(len(json.loads(perf.PERF_JSON.read_text())["hosts"]), perf.HOST_LIMIT)


class ConfigTokenTest(unittest.TestCase):
    def setUp(self):
        self._cache = paths._CFG_CACHE

    def tearDown(self):
        paths._CFG_CACHE = self._cache

    def test_auto_is_not_a_pin(self):
        paths._CFG_CACHE = {"ngl": "auto", "threads": "auto"}
        lim = paths.console_limits()
        self.assertIsNone(lim["ngl"])
        self.assertIsNone(lim["threads"])

    def test_explicit_numbers_stay_pins_zero_included(self):
        paths._CFG_CACHE = {"ngl": 0, "threads": 4}
        lim = paths.console_limits()
        self.assertEqual(0, lim["ngl"])
        self.assertEqual(4, lim["threads"])

    def test_numeric_strings_are_still_numbers(self):
        paths._CFG_CACHE = {"ngl": "99", "threads": "8"}
        lim = paths.console_limits()
        self.assertEqual(99, lim["ngl"])
        self.assertEqual(8, lim["threads"])

    def test_the_shipped_config_asks_for_a_host_adaptive_launch(self):
        """The SHIPPED file must ask for a host-adaptive launch - a per-machine pin is the operator's.

        `paths.console_limits()` (through `console_cfg()`) returns the MERGED view: console.json with
        config/local.json overlaid. Asserting on that cannot tell "the shipped default drifted" from
        "this machine's operator pinned a number on purpose" - the failure message blamed
        `config/console.json` for a 6 that file does not contain (measured 2026-09-27, defect 151).
        It is also per-machine in the wrong way: a pin that is right on an i5-13600KF makes the suite
        red on a laptop, while the whole point of shipping "auto" is that one kit behaves properly on
        both. So the shipped file is read directly, and whatever this host actually launches with is
        reported rather than asserted on.
        """
        import json

        shipped_path = paths.CONFIG / "console.json"
        shipped = json.loads(shipped_path.read_text(encoding="utf-8"))
        for key in ("ngl", "threads"):
            value = str(shipped.get(key, "")).strip().lower()
            self.assertEqual(
                value,
                "auto",
                f"config/console.json must SHIP {key}: auto, so every copy of this kit adapts to its own "
                f"host - the operator's integer pin belongs in the per-machine config/local.json",
            )

        # A pin is legitimate operator intent (paths.py: "an integer ngl/threads is a pin and is honored
        # even at 0"). It must come from local.json, never from the file the kit carries.
        lim = paths.console_limits()
        local_path = paths.CONFIG / "local.json"
        local = json.loads(local_path.read_text(encoding="utf-8")) if local_path.is_file() else {}
        for key in ("ngl", "threads"):
            if isinstance(lim[key], int):
                self.assertIn(
                    key,
                    local,
                    f"{key}={lim[key]} on this host but config/local.json does not declare it: a pin must be "
                    f"visible where it was made, not wired in somewhere the operator cannot see",
                )

    def setUp(self):
        self._cache = paths._CFG_CACHE

    def tearDown(self):
        paths._CFG_CACHE = self._cache

    def test_auto_uses_the_fast_physical_cores_not_the_logical_count(self):
        # This host advertises 20 logical processors but carries 6 performance cores. The sweep
        # measured the 6-core pin FASTER than 16 threads on the same chip, so oversubscribing every
        # logical processor is the bug, not the goal.
        with patch("perf.physical_cores", return_value=(6, 14)):
            self.assertEqual(6, perf.auto_threads())
        with patch("perf.physical_cores", return_value=(8, 8)):
            self.assertEqual(8, perf.auto_threads())
        with patch("perf.physical_cores", return_value=(2, 2)):
            self.assertEqual(4, perf.auto_threads())

    def test_auto_falls_back_to_logical_processors_when_topology_is_unreadable(self):
        # A host whose core split cannot be read must still get a sane answer, never a crash and
        # never zero threads.
        with patch("perf.physical_cores", return_value=(0, 0)):
            with patch("perf.os.cpu_count", return_value=20):
                self.assertEqual(16, perf.auto_threads())
            with patch("perf.os.cpu_count", return_value=8):
                self.assertEqual(7, perf.auto_threads())
            with patch("perf.os.cpu_count", return_value=2):
                self.assertEqual(4, perf.auto_threads())
            with patch("perf.os.cpu_count", return_value=None):
                self.assertEqual(4, perf.auto_threads())

    def test_clamp_honors_a_pin_and_caps_absurd_values(self):
        self.assertEqual(4, perf.clamp_threads(4))
        self.assertEqual(16, perf.clamp_threads(64))
        self.assertEqual(2, perf.clamp_threads(1))

    def test_clamp_falls_back_to_the_host_only_when_config_is_silent(self):
        paths._CFG_CACHE = {}
        with patch("perf.physical_cores", return_value=(0, 0)):
            with patch("perf.os.cpu_count", return_value=20):
                self.assertEqual(16, perf.clamp_threads(None))
                self.assertEqual(16, engine.clamp_threads(None))
        paths._CFG_CACHE = {"threads": 4}
        self.assertEqual(4, engine.clamp_threads(None))

    def test_lygo_engine_keeps_one_thread_policy(self):
        with patch("perf.os.cpu_count", return_value=12):
            self.assertEqual(perf.auto_threads(), lygo_engine.cpu_threads())


class LadderTest(unittest.TestCase):
    def test_the_ladder_descends_and_always_ends_on_the_cpu(self):
        self.assertEqual([99, 49, 8, 0], perf.ladder(99))
        self.assertEqual([20, 10, 8, 0], perf.ladder(20))
        self.assertEqual([0], perf.ladder(0))
        self.assertEqual([0], perf.ladder(-5))
        for start in (99, 50, 12, 7, 1):
            rungs = perf.ladder(start)
            self.assertEqual(0, rungs[-1], start)
            self.assertEqual(sorted(set(rungs), reverse=True), rungs, start)


class BootLadderTest(unittest.TestCase):
    """The GPU must be allowed to fail without taking the brain down."""

    def setUp(self):
        self._perf_json = perf.PERF_JSON
        self._td = tempfile.TemporaryDirectory()
        perf.PERF_JSON = Path(self._td.name) / "perf.json"
        self._cache = paths._CFG_CACHE
        paths._CFG_CACHE = {}
        self.rec = {"id": "qwen2.5:3b", "path": "C:/nope/qwen.gguf", "bytes": 2 * GIB, "kind": "chat"}

    def tearDown(self):
        perf.PERF_JSON = self._perf_json
        paths._CFG_CACHE = self._cache
        self._td.cleanup()

    def _boot(self, side_effect, state=None, slow=False):
        state = state if state is not None else {}
        calls: list[int] = []

        def fake_spawn(**kw):
            calls.append(kw["ngl"])
            if side_effect(len(calls)):
                raise RuntimeError("llama-server exited 1")
            return object()

        patches = [
            patch("lygo_engine.probe", return_value=gpu_host()),
            patch("lygo_engine.resolve_binary", return_value=Path("llama-server.exe")),
            patch("lygo_engine.runner_for", return_value=None),
            patch("lygo_engine.spawn_runner", side_effect=fake_spawn),
            patch("engine.stop_port", return_value=None),
            patch("perf.SLOW_ATTEMPT_S", -1.0 if slow else 3600.0),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        status = lygo_engine.boot(self.rec, api_key="k", state=state)
        return status, calls, state

    def test_a_gpu_launch_that_dies_retries_a_smaller_split(self):
        status, calls, state = self._boot(lambda n: n == 1)
        self.assertEqual("ready", status)
        self.assertEqual([99, 49], calls)
        self.assertIn("perf_fallback", state)

    def test_a_hung_gpu_launch_goes_straight_to_the_cpu(self):
        status, calls, state = self._boot(lambda n: n == 1, slow=True)
        self.assertEqual("ready", status)
        self.assertEqual([99, 0], calls)

    def test_a_launch_that_never_succeeds_reports_instead_of_hanging(self):
        with self.assertRaises(RuntimeError):
            self._boot(lambda n: True)

    def test_a_working_gpu_launch_is_remembered_for_this_host(self):
        status, calls, state = self._boot(lambda n: False)
        self.assertEqual("ready", status)
        self.assertEqual([99], calls)
        hosts = json.loads(perf.PERF_JSON.read_text())["hosts"]
        self.assertEqual(99, list(hosts.values())[0]["ngl"])
        self.assertNotIn("perf_fallback", state)

    def test_a_scanned_record_cannot_outrank_the_adaptive_plan(self):
        # The bug this guards: the scan writes n_gpu_layers 0 for every model it finds, and the launch
        # path read that as an operator instruction. /api/health reported ngl 79 while the engine was
        # actually started with -ngl 0, so the GPU sat idle and every machine looked CPU-only.
        self.rec = {**self.rec, "n_gpu_layers": 0}
        status, calls, state = self._boot(lambda n: False)
        self.assertEqual("ready", status)
        self.assertEqual([99], calls)
        self.assertEqual(99, state["lygo_engine"]["perf"]["effective"]["ngl"])
        self.assertEqual("gpu_full", state["lygo_engine"]["perf"]["effective"]["mode"])


class ReportTest(unittest.TestCase):
    def _state(self, reason="fits_vram", mode="gpu_full", ngl=99):
        return {
            "lygo_engine": {
                "perf": {
                    "mode": mode,
                    "ngl": ngl,
                    "threads": 16,
                    "device": "NVIDIA GeForce RTX 4060 Ti",
                    "vram_free_mib": 7181,
                    "vram_total_mib": 7949,
                    "backends": ["vulkan"],
                    "source": "auto",
                    "reason": reason,
                    "fallback": False,
                    "host": "abc123",
                },
                "hardware": {"ram_gib": 32},
            }
        }

    def test_a_gpu_host_reports_the_device(self):
        rep = perf.report(self._state())
        self.assertEqual("gpu_full", rep["mode"])
        self.assertEqual(99, rep["ngl"])
        self.assertEqual("NVIDIA GeForce RTX 4060 Ti", rep["device"])
        self.assertEqual("", rep["api_boost"])

    def test_a_cpu_only_build_names_the_remedy_and_offers_the_api(self):
        rep = perf.report(self._state(reason="engine_cpu_only", mode="cpu", ngl=0))
        self.assertIn("fetch_engine.ps1 -Backend", rep["remedy"])   # the way out is named
        self.assertIn("api_boost", rep)
        self.assertTrue(rep["api_boost"])

    def test_a_launch_fallback_is_surfaced(self):
        st = self._state()
        st["perf_fallback"] = "ngl 99 failed: RuntimeError; next 0"
        self.assertEqual(st["perf_fallback"], perf.report(st)["launch_fallback"])

    def test_report_never_raises_on_nonsense_state(self):
        for bad in (None, {}, {"lygo_engine": "nope"}, {"lygo_engine": {"perf": []}}, {"lygo_engine": {}}):
            rep = perf.report(bad)
            self.assertIsInstance(rep, dict)
            self.assertIn("mode", rep)

    def test_report_before_the_engine_is_planned_still_answers(self):
        rep = perf.report({})
        self.assertEqual("unknown", rep["mode"])
        self.assertEqual("engine_not_planned_yet", rep["reason"])


class PlanWiringTest(unittest.TestCase):
    """plan() must hand the adaptive profile to the launcher, not recompute it."""

    def setUp(self):
        self._perf_json = perf.PERF_JSON
        self._td = tempfile.TemporaryDirectory()
        perf.PERF_JSON = Path(self._td.name) / "perf.json"
        self._cache = paths._CFG_CACHE

    def tearDown(self):
        perf.PERF_JSON = self._perf_json
        paths._CFG_CACHE = self._cache
        self._td.cleanup()

    @unittest.skipIf(on_a_stick(), "PC-build assertion: this reads the shipped PC config (config/ is synced to the stick by hand, by design) or the checkout above the kit")
    @unittest.skipIf(on_a_stick(), "PC-build assertion: this reads the shipped PC config (config/ is synced to the stick by hand, by design) or the checkout above the kit")
    def test_plan_exposes_the_profile_and_the_gpu_threads(self):
        with patch("lygo_engine.probe", return_value=gpu_host()), patch.object(paths, "_CFG_CACHE", {}):
            pl = lygo_engine.plan({"id": "qwen2.5:3b", "path": "x.gguf", "bytes": 2 * GIB, "kind": "chat"})
        self.assertEqual(99, pl["llama"]["ngl"])
        self.assertEqual(perf.auto_threads(), pl["llama"]["threads"])
        self.assertEqual("gpu_full", pl["perf"]["mode"])
        # Measured, not assumed from the GPU being present: -fa on is faster on both halves on the
        # current build (prompt eval 154.1 vs 141.7 t/s, generation 15.15 vs 13.00 t/s), so the
        # shipped config says on, the plan passes it through, and spawn_runner really sends it.
        self.assertTrue(pl["llama"]["flash_attn"])
        self.assertEqual("vulkan", pl["perf"]["backends"][0])

    def test_plan_on_a_backendless_build_is_honest_and_info_only(self):
        hw = gpu_host(backends=())
        hw["devices"] = []
        hw["vram_bytes"] = 8 * GIB
        with patch("lygo_engine.probe", return_value=hw), patch.object(paths, "_CFG_CACHE", {}):
            pl = lygo_engine.plan({"id": "qwen2.5:3b", "path": "x.gguf", "bytes": 2 * GIB, "kind": "chat"})
        self.assertEqual(0, pl["llama"]["ngl"])
        self.assertFalse(pl["llama"]["flash_attn"])
        self.assertEqual("engine_cpu_only", pl["perf"]["reason"])
        self.assertIn("remedy", perf.report({"lygo_engine": pl}))

    def test_plan_uses_a_real_file_size_when_the_record_omits_bytes(self):
        with tempfile.NamedTemporaryFile(suffix=".gguf") as fh:
            fh.write(b"0" * 4096)
            fh.flush()
            with patch("lygo_engine.probe", return_value=gpu_host()), patch.object(paths, "_CFG_CACHE", {}):
                pl = lygo_engine.plan({"id": "tiny", "path": fh.name, "kind": "chat"})
        self.assertEqual("fits_vram", pl["perf"]["reason"], "a 4 KB model always fits")


    @unittest.skipIf(on_a_stick(), "PC-build assertion: this reads the shipped PC config (config/ is synced to the stick by hand, by design) or the checkout above the kit")
    @unittest.skipIf(on_a_stick(), "PC-build assertion: this reads the shipped PC config (config/ is synced to the stick by hand, by design) or the checkout above the kit")
    def test_flash_attention_follows_the_configured_default(self):
        """The shipped default is measured, and an operator pin still wins.

        This test used to assert the opposite default, on a measurement taken before the current
        engine: "-fa on measured SLOWER for prompt eval on this host's CUDA path (2620 vs 3246
        tok/s), so it is opt-in". On 0.4.1-dev build 10988 that reading is gone - with -ngl 79, -t 6
        and q8_0 KV on one 680-token prompt, prompt eval is 154.1 vs 141.7 t/s and generation is
        15.15 vs 13.00 t/s, with a faster load - so config/console.json ships "on" and the plan must
        pass that through. A pin of "off" in the config is still honoured.
        """
        with patch("lygo_engine.probe", return_value=gpu_host()), patch.object(paths, "_CFG_CACHE", {}):
            pl = lygo_engine.plan(
                {"id": "qwen2.5:3b", "path": "x.gguf", "bytes": 2 * GIB, "kind": "chat"}
            )
            self.assertTrue(pl["llama"]["flash_attn"], "the shipped default is on")
            with patch.object(lygo_engine, "flash_attn_on", return_value=False):
                pl_off = lygo_engine.plan(
                    {"id": "qwen2.5:3b", "path": "x.gguf", "bytes": 2 * GIB, "kind": "chat"}
                )
        self.assertFalse(pl_off["llama"]["flash_attn"], "an operator pin of off still wins")
        with (
            patch("lygo_engine.flash_attn_on", lambda: True),
            patch("lygo_engine.probe", return_value=gpu_host()),
            patch.object(paths, "_CFG_CACHE", {}),
        ):
            pl2 = lygo_engine.plan(
                {"id": "qwen2.5:3b", "path": "x.gguf", "bytes": 2 * GIB, "kind": "chat"}
            )
        self.assertTrue(pl2["llama"]["flash_attn"], "console.json may opt in")


if __name__ == "__main__":
    unittest.main()
