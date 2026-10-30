"""Backend store: a GPU is claimed only after it loads a real model on this host.

The three properties that matter, all provable without a GPU:

  1. a backend that fails is remembered for this host and never applied to engine/ —
     a broken GPU driver must not be able to take the console down with it (a real
     0xC0000005 from nvoglv64.dll did exactly that on 2026-09-18, at ngl 0 included);
  2. a backend that passes is applied and selected, and its verdict is reused instead
     of re-testing on every boot;
  3. with nothing installed, nothing proven, or gpu turned off in config, the kit runs
     the shipped engine and the plan says CPU out loud.

Probes and self-tests are faked here: a CI box with no GPU runs the same assertions.
"""
from __future__ import annotations

import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import backends  # noqa: E402
import paths  # noqa: E402
import perf  # noqa: E402

GIB = 1024**3


class BackendStoreTest(unittest.TestCase):
    """A fake kit laid out on disk: a shipped engine, a store, a data dir."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="lygo_backends_")
        # Release the fake kit explicitly. Left to the GC, every one of these emits
        # "Implicitly cleaning up <TemporaryDirectory ...>", which under
        # -W error::ResourceWarning lands as an unraisable exception on whichever test
        # happens to be running when the collector fires (defect D29).
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.engine = root / "engine"
        self.store = self.engine / "backends"
        self.data = root / "data"
        self.logs = root / "logs"
        for d in (self.engine, self.store, self.data, self.logs):
            d.mkdir(parents=True, exist_ok=True)
        for name in ("llama-server.exe", "llama.dll", "ggml.dll", "ggml-base.dll"):
            (self.engine / name).write_bytes(b"base engine " + name.encode())
        self.active = self.data / "perf_active.json"
        self._patches = [
            patch.object(backends, "ENGINE_DIR", self.engine),
            patch.object(backends, "BACKENDS_DIR", self.store),
            patch.object(backends, "DATA", self.data),
            patch.object(backends, "ACTIVE_JSON", self.active),
            patch.object(backends, "LOGS", self.logs),
            patch.object(paths, "ENGINE_DIR", self.engine),
            patch.object(paths, "ACTIVE_ENGINE_JSON", self.active),
            # the verdict store is perf.PERF_JSON: patching backends.DATA alone would
            # let a test write into the real kit store (it did, once)
            patch.object(perf, "PERF_JSON", self.data / "perf.json"),
            # This box's own card must not decide a store test. The busy-card policy has its own test
            # below; here it is held open ("") the way it is on an idle machine. Measured 2026-09-20: six
            # of these tests failed while a real checker sweep of ours held the GPU.
            patch.object(backends, "card_busy", lambda: ""),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        backends._MEMO.clear()
        self.addCleanup(backends._MEMO.clear)

    # -- fixtures ---------------------------------------------------------
    def add_overlay(self, name: str = "vulkan", payload: bytes = b"ggml-vulkan-dll") -> Path:
        d = self.store / name
        d.mkdir(parents=True, exist_ok=True)
        (d / f"ggml-{name}.dll").write_bytes(payload)
        (d / "backend.json").write_text(json.dumps({
            "name": name, "kind": "overlay", "tag": "b10988",
            "files": {f"ggml-{name}.dll": backends._sha256(d / f"ggml-{name}.dll")},
            "bytes": len(payload), "created": "2026-09-18T00:00:00Z",
        }), encoding="utf-8")
        return d

    def add_engine_build(self, name: str = "cuda") -> Path:
        d = self.store / name
        d.mkdir(parents=True, exist_ok=True)
        for f in ("llama-server.exe", "llama.dll", "ggml.dll", f"ggml-{name}.dll"):
            (d / f).write_bytes(b"cuda build " + f.encode())
        (d / "backend.json").write_text(json.dumps({
            "name": name, "kind": "engine", "tag": "b10988",
            "files": {}, "bytes": 4096, "created": "2026-09-18T00:00:00Z",
        }), encoding="utf-8")
        return d

    def verdict(self, name: str, verdict: str, host: str = "host-a") -> dict:
        info = backends.backend_info(name)
        return backends.remember_backend(host, name, verdict=verdict,
                                         key=backends.backend_key(name, info),
                                         detail="seeded", seconds=1.0)

    def model_file(self, size: int = 1024, tag: str = "small") -> Path:
        p = self.data / f"{tag}.gguf"
        p.write_bytes(b"g" * size)
        return p

    def fake_device_probe(self, devices=None):
        """Host-independent device probe: a GPU appears only once the overlay is applied.

        This mirrors the real bug it guards: perf caches device probes per exe path+mtime,
        and neither changes when a backend DLL lands next to the exe, so the post-activation
        probe must ask for a refresh or a good backend gets rejected as device-less.
        """
        devs = devices if devices is not None else [
            {"id": "Vulkan0", "name": "NVIDIA GeForce RTX 4060 Ti", "total_mib": 7949, "free_mib": 7181}
        ]
        seen = {"refresh": []}

        def probe(exe=None, *, refresh=False, timeout=25):
            seen["refresh"].append(bool(refresh))
            return list(devs) if refresh else []

        return probe, seen

    def patch_devices(self, devices=None):
        probe, seen = self.fake_device_probe(devices)
        p = patch.object(perf, "engine_devices", side_effect=probe)
        p.start()
        self.addCleanup(p.stop)
        return seen

    # -- a boot may fall back, but it may not condemn ----------------------
    # Measured 2026-09-20: a BOOT (not a self-test) wrote `bad` against the cuda build at 22:54 with
    # "launch_failed: RuntimeError: llama-server exited 1" while our own sweep was still releasing the
    # card. That verdict then withdrew the proven selection, data/perf_active.json disappeared, and the
    # console came up on the CPU engine with nothing saying so - and a verdict is remembered for 6 h.
    def test_a_boot_on_a_busy_card_records_no_verdict(self):
        self.add_engine_build("cuda")
        with patch.object(backends, "card_busy", lambda: "another llama-server is running"):
            may, why = backends.boot_condemnation("cuda", detail="RuntimeError: llama-server exited 1")
        self.assertFalse(may, why)
        self.assertIn("in use", why)
        self.assertIsNone(backends.backend_verdict("host-a", "cuda"))

    def test_a_faulted_boot_records_no_verdict(self):
        self.add_engine_build("cuda")
        may, why = backends.boot_condemnation("cuda",
                                              detail="RuntimeError: llama-server exited 3221225477")
        self.assertFalse(may, why)
        self.assertIn("fault", why)

    def test_a_boot_with_no_engine_words_records_no_verdict(self):
        self.add_engine_build("cuda")
        may, why = backends.boot_condemnation("cuda", detail="RuntimeError: llama-server exited 1")
        self.assertFalse(may, why)
        self.assertIn("words", why)

    def test_a_model_the_loader_cannot_read_does_not_condemn_the_backend(self):
        self.add_engine_build("cuda")
        may, why = backends.boot_condemnation(
            "cuda", detail="RuntimeError: llama-server exited 1",
            log_tail="error loading model hyperparameters: key qwen35moe.rope.dimension_sections "
                     "has wrong array length; expected 4, got 3")
        self.assertFalse(may, why)
        self.assertIn("model", why)

    def test_the_accelerator_refusing_to_come_up_still_condemns(self):
        # The rule keeps its teeth: when the build's own accelerator will not start, that IS a property
        # of the build on this host, and the verdict belongs in the store.
        self.add_engine_build("cuda")
        may, why = backends.boot_condemnation(
            "cuda", detail="RuntimeError: llama-server exited 1",
            log_tail="ggml_cuda_init: failed to initialize CUDA: no CUDA-capable device is detected")
        self.assertTrue(may, why)
        self.assertEqual(why, "")

    # -- store shape ------------------------------------------------------
    def test_store_kinds_are_detected(self):
        self.assertEqual(backends.backend_kind(self.add_overlay("vulkan")), "overlay")
        self.assertEqual(backends.backend_kind(self.add_engine_build("cuda")), "engine")
        junk = self.store / "junk"
        junk.mkdir()
        (junk / "notes.txt").write_text("not a backend", encoding="utf-8")
        self.assertEqual(backends.backend_kind(junk), "")
        self.assertEqual(sorted(backends.installed()), ["cuda", "vulkan"])

    def test_broken_manifest_is_not_reported_as_installed(self):
        d = self.add_overlay("vulkan")
        (d / "ggml-vulkan.dll").write_bytes(b"tampered")          # hash no longer matches
        info = backends.backend_info("vulkan", deep=True)
        self.assertFalse(info["verified"])
        self.assertNotIn("vulkan", backends.installed(deep=True))

    # -- nothing installed, nothing proven --------------------------------
    def test_no_backend_at_all_stays_on_cpu_and_launches_nothing(self):
        with patch.object(backends, "self_test", side_effect=AssertionError("must not launch")):
            out = backends.ensure(host="host-a", models=[], devices=[])
        self.assertEqual(out["backend"], "cpu")
        self.assertFalse(out["gpu_ok"])
        self.assertEqual(out["reason"], "engine_cpu_only")
        self.assertEqual(Path(out["engine_dir"]), self.engine)

    def test_half_applied_overlay_is_removed_when_nothing_is_installed(self):
        (self.engine / "ggml-vulkan.dll").write_bytes(b"leftover")
        with patch.object(backends, "self_test", side_effect=AssertionError("must not launch")):
            out = backends.ensure(host="host-a", models=[], devices=[])
        self.assertEqual(out["backend"], "cpu")
        self.assertFalse((self.engine / "ggml-vulkan.dll").exists())

    def test_gpu_disabled_by_config_never_tests_anything(self):
        self.add_overlay("vulkan")
        with patch.object(backends, "self_test", side_effect=AssertionError("must not launch")):
            out = backends.ensure(host="host-a", models=[], enabled=False)
        self.assertEqual(out["reason"], "gpu_disabled_by_config")
        self.assertFalse(out["gpu_ok"])
        self.assertFalse((self.engine / "ggml-vulkan.dll").exists())

    # -- policy: a moment is not a verdict ---------------------------------
    def age_verdict(self, name: str) -> None:
        """Push a remembered verdict past the retry window, without touching the code under test."""
        when = datetime.datetime.now() - datetime.timedelta(seconds=backends.VERDICT_RETRY_AFTER_S + 60)
        store = json.loads((self.data / "perf.json").read_text(encoding="utf-8"))

        def walk(node) -> bool:
            if isinstance(node, dict):
                if isinstance(node.get(name), dict) and "verdict" in node[name]:
                    node[name]["at"] = when.strftime("%Y-%m-%dT%H:%M:%S")
                    return True
                return any(walk(v) for v in node.values())
            return False

        self.assertTrue(walk(store), "the seeded verdict was not found in the store")
        (self.data / "perf.json").write_text(json.dumps(store, indent=2, sort_keys=True), encoding="utf-8")

    def test_a_busy_card_is_not_tested_and_remembers_no_verdict(self):
        """A card in use measures the moment, not the backend: skip the test and remember nothing."""
        self.add_overlay("vulkan")
        self.patch_devices()
        with patch.object(backends, "card_busy", lambda: "only 120 MiB of 8187 MiB VRAM free"), \
                patch.object(backends, "self_test", side_effect=AssertionError("must not launch")):
            out = backends.ensure(host="host-a", models=[{"path": str(self.model_file())}], devices=[],
                                  refresh=True)
        self.assertEqual(out["reason"], "gpu_busy_not_probed")
        self.assertEqual(out["backend"], "cpu")
        self.assertIn("VRAM free", out["detail"])
        self.assertFalse(backends.backend_verdict("host-a", "vulkan"), "a skip is not a verdict")
        self.assertFalse((self.engine / "ggml-vulkan.dll").exists(), "a skip leaves nothing applied")

    def test_a_bad_verdict_ages_out_and_is_retested(self):
        """A remembered verdict is evidence about a moment: past the window it stops blocking a test."""
        self.add_overlay("vulkan")
        self.patch_devices()
        self.verdict("vulkan", "bad")
        frozen = backends.ensure(host="host-a", models=[{"path": str(self.model_file())}], devices=[],
                                 refresh=True)
        self.assertEqual(frozen["reason"], "backend_failed_on_this_host")
        self.assertEqual(frozen["backend"], "cpu")
        self.age_verdict("vulkan")
        calls = []

        def fake_test(engine_dir, model_path, **kw):
            calls.append(str(engine_dir))
            return {"ok": True, "seconds": 1.0, "detail": "model_load_ok"}

        with patch.object(backends, "self_test", side_effect=fake_test):
            out = backends.ensure(host="host-a", models=[{"path": str(self.model_file())}], devices=[],
                                  refresh=True)
        self.assertTrue(calls, "a stale verdict must be retested")
        self.assertEqual(out["backend"], "vulkan")
        self.assertTrue(out["gpu_ok"])

    # -- a failed backend must not touch the engine -----------------------
    def test_failed_self_test_is_remembered_and_leaves_engine_untouched(self):
        self.add_overlay("vulkan")
        self.patch_devices()
        calls = []

        def fake_test(engine_dir, model_path, **kw):
            calls.append(str(engine_dir))
            # A clean exit carrying the loader's own words: that is what condemns a build.
            return {"ok": False, "rc": 1, "seconds": 0.4,
                    "detail": "launch_failed: llama-server exited 1 - failed to load model"}

        with patch.object(backends, "self_test", side_effect=fake_test):
            first = backends.ensure(host="host-a", models=[{"path": str(self.model_file())}], devices=[])
        self.assertEqual(first["backend"], "cpu")
        self.assertEqual(first["reason"], "backend_crashed_on_this_host")
        self.assertFalse((self.engine / "ggml-vulkan.dll").exists())
        self.assertEqual(len(calls), 1)
        verdict = backends.backend_verdict("host-a", "vulkan")
        self.assertEqual(verdict["verdict"], "bad")
        self.assertIn("failed to load model", verdict["detail"],
                      "the build is condemned by the loader's own words, not by a fault code")

        # Second boot on the same host: remembered, so no second crash.
        backends._MEMO.clear()
        with patch.object(backends, "self_test", side_effect=AssertionError("must not relaunch")):
            second = backends.ensure(host="host-a", models=[{"path": str(self.model_file())}], devices=[])
        self.assertEqual(second["backend"], "cpu")
        self.assertEqual(second["reason"], "backend_failed_on_this_host")

    def test_a_bad_verdict_never_leaves_its_overlay_applied(self):
        self.add_overlay("vulkan")
        (self.engine / "ggml-vulkan.dll").write_bytes(b"applied earlier")
        self.verdict("vulkan", "bad")
        with patch.object(backends, "self_test", side_effect=AssertionError("must not relaunch")):
            out = backends.ensure(host="host-a", models=[], devices=[])
        self.assertEqual(out["backend"], "cpu")
        self.assertFalse((self.engine / "ggml-vulkan.dll").exists())

    # -- a proven backend is applied and reused ---------------------------
    def test_a_faulted_launch_is_not_a_verdict(self):
        """0xC0000005 is the card being taken away mid-launch, not a property of the build."""
        self.add_overlay("vulkan")
        self.patch_devices()

        def faulted(engine_dir, model_path, **kw):
            return {"ok": False, "rc": 3221225477, "seconds": 0.4,
                    "detail": "launch_failed: RuntimeError: llama-server exited 3221225477"}

        with patch.object(backends, "self_test", side_effect=faulted):
            out = backends.ensure(host="host-a", models=[{"path": str(self.model_file())}], devices=[],
                                  refresh=True)
        self.assertEqual(out["reason"], "backend_faulted_not_probed")
        self.assertEqual(out["backend"], "cpu")
        self.assertFalse(backends.backend_verdict("host-a", "vulkan"), "a fault is not a verdict")
        self.assertFalse((self.engine / "ggml-vulkan.dll").exists(), "a faulted launch applies nothing")
        with patch.object(backends, "self_test", return_value={"ok": True, "rc": 0, "seconds": 1.0,
                                                              "detail": "model_load_ok"}):
            again = backends.ensure(host="host-a", models=[{"path": str(self.model_file())}], devices=[],
                                    refresh=True)
        self.assertEqual(again["backend"], "vulkan",
                         "nothing was remembered, so the next boot may try again instead of waiting six hours")

    def test_the_memo_survives_a_thread_change(self):
        """Threads change how a backend is exercised, not which backend this host proved."""
        self.add_overlay("vulkan")
        self.patch_devices()
        with patch.object(backends, "self_test", return_value={"ok": True, "rc": 0, "seconds": 1.0,
                                                              "detail": "model_load_ok"}):
            backends.ensure(host="host-a", models=[{"path": str(self.model_file())}], devices=[], threads=8)
        with patch.object(backends, "self_test", side_effect=AssertionError("a thread change must not re-test")):
            out = backends.ensure(host="host-a", models=[{"path": str(self.model_file())}], devices=[],
                                  threads=6)
        self.assertEqual(out["backend"], "vulkan")

    def test_passing_self_test_applies_the_overlay_and_selects_it(self):
        self.add_overlay("vulkan")
        seen = self.patch_devices()
        with patch.object(backends, "self_test", return_value={"ok": True, "rc": 0, "seconds": 2.1,
                                                              "detail": "model_load_ok"}):
            out = backends.ensure(host="host-a", models=[{"path": str(self.model_file())}],
                                  devices=[{"id": "Vulkan0", "name": "NVIDIA GeForce RTX 4060 Ti",
                                            "total_mib": 7949, "free_mib": 7181}])
        self.assertEqual(out["backend"], "vulkan")
        self.assertTrue(out["gpu_ok"])
        self.assertTrue((self.engine / "ggml-vulkan.dll").exists())
        self.assertEqual(Path(out["engine_dir"]), self.engine)
        self.assertEqual(Path(paths.engine_dir()), self.engine)
        self.assertEqual(seen["refresh"], [True])      # always post-activation, never the stale cache

    def test_a_proven_engine_build_becomes_the_engine_dir_without_retesting(self):
        cuda = self.add_engine_build("cuda")
        self.verdict("cuda", "ok")
        with patch.object(backends, "self_test", side_effect=AssertionError("verdict already fresh")):
            out = backends.ensure(host="host-a", models=[])
        self.assertEqual(out["backend"], "cuda")
        self.assertTrue(out["gpu_ok"])
        self.assertEqual(out["reason"], "backend_proven_on_this_host")
        self.assertEqual(Path(out["engine_dir"]), cuda)
        self.assertEqual(Path(paths.engine_dir()), cuda)              # engine.py will use it
        self.assertTrue((cuda / "llama-server.exe").is_file())

    def test_verdicts_do_not_leak_between_hosts(self):
        cuda = self.add_engine_build("cuda")
        self.patch_devices()
        self.verdict("cuda", "ok", host="host-a")
        with patch.object(backends, "self_test", return_value={"ok": True, "rc": 0, "seconds": 1.0,
                                                               "detail": "model_load_ok"}):
            out = backends.ensure(host="host-b", models=[{"path": str(self.model_file())}])
        self.assertEqual(out["backend"], "cuda")
        self.assertEqual(Path(out["engine_dir"]), cuda)               # tested, not inherited

    def test_refetch_changes_the_key_and_earns_a_new_test(self):
        self.add_engine_build("cuda")
        self.patch_devices()
        self.verdict("cuda", "bad")
        man = self.store / "cuda" / "backend.json"
        rec = json.loads(man.read_text(encoding="utf-8"))
        rec["created"] = "2026-09-18T01:00:00Z"                       # a re-fetch
        man.write_text(json.dumps(rec), encoding="utf-8")
        backends._MEMO.clear()
        with patch.object(backends, "self_test", return_value={"ok": True, "rc": 0, "seconds": 1.0,
                                                               "detail": "model_load_ok"}):
            out = backends.ensure(host="host-a", models=[{"path": str(self.model_file())}])
        self.assertEqual(out["backend"], "cuda")

    def test_leftover_overlay_without_a_store_entry_is_removed(self):
        (self.engine / "ggml-vulkan.dll").write_bytes(b"orphan")
        out = backends.deactivate("vulkan")            # store entry is gone on purpose
        self.assertTrue(out["deactivated"])
        self.assertFalse((self.engine / "ggml-vulkan.dll").exists())

    def test_a_backend_that_names_no_device_is_recorded_bad_and_not_applied(self):
        self.add_overlay("vulkan")
        self.patch_devices(devices=[])                 # activates, but the engine sees no GPU
        with patch.object(backends, "self_test", side_effect=AssertionError("no device, no load")):
            out = backends.ensure(host="host-a", models=[{"path": str(self.model_file())}])
        self.assertEqual(out["backend"], "cpu")
        self.assertEqual(out["reason"], "backend_sees_no_device")
        self.assertFalse((self.engine / "ggml-vulkan.dll").exists())

    def test_verdict_store_stays_inside_the_kit_data_dir(self):
        self.assertTrue(str(backends.verdict_store_path()).startswith(str(self.data)))
        backends.remember_backend("host-z", "cuda", verdict="ok", key="k", detail="guard")
        written = json.loads((self.data / "perf.json").read_text(encoding="utf-8"))
        self.assertEqual(written["backends"]["host-z"]["cuda"]["verdict"], "ok")

    def test_probe_models_skip_embedding_models(self):
        chat = self.model_file(1000, "chat")
        embed = self.model_file(10, "embed")           # smallest, but cannot prove a GPU
        out = backends.probe_models([
            {"path": str(embed), "bytes": 10, "id": "nomic-embed-text:latest"},
            {"path": str(chat), "bytes": 1000, "id": "qwen2.5:3b"},
        ])
        self.assertEqual(out, [chat])

    def test_one_crashing_model_does_not_condemn_a_backend(self):
        self.add_overlay("vulkan")
        self.patch_devices()
        good, bad = self.model_file(1000, "chatbig"), self.model_file(10, "chatsmall")
        tried = []

        def fake_test(engine_dir, model_path, **kw):
            tried.append(Path(model_path).name)
            if len(tried) == 1:
                # the embedding-model signature: fail-fast inside the model, not the driver
                return {"ok": False, "rc": 3221226505, "seconds": 3.5,
                        "detail": "0xC0000409 stack buffer overrun"}
            return {"ok": True, "rc": 0, "seconds": 9.0, "detail": "model_load_ok"}

        with patch.object(backends, "self_test", side_effect=fake_test):
            out = backends.ensure(host="host-a", models=[{"path": str(bad), "bytes": 10},
                                                         {"path": str(good), "bytes": 1000}])
        self.assertEqual(out["backend"], "vulkan")
        self.assertTrue(out["gpu_ok"])
        self.assertEqual(len(tried), 2)                # it kept going instead of giving up
        self.assertEqual(backends.backend_verdict("host-a", "vulkan")["verdict"], "ok")

    # -- self_test itself --------------------------------------------------
    def test_self_test_names_a_driver_crash_instead_of_guessing(self):
        class Dead:
            def __init__(self, rc):
                self.rc = rc

            def poll(self):
                return self.rc

            def kill(self):
                return None

            def wait(self, timeout=None):
                return self.rc

        exe = self.engine / "llama-server.exe"
        model = self.model_file()
        with patch.object(backends.subprocess, "Popen", return_value=Dead(3221225477)), \
             patch.object(backends, "_health_ok", return_value=False), \
             patch.object(backends, "_port_free", return_value=True):
            res = backends.self_test(self.engine, model, threads=4, timeout=3, backend="vulkan")
        self.assertFalse(res["ok"])
        self.assertEqual(res["rc"], 3221225477)
        self.assertIn("access violation", res["detail"].lower())
        self.assertTrue(Path(res["log"]).name.startswith("selftest-vulkan"))

    def test_self_test_refuses_to_guess_without_an_engine_or_a_model(self):
        self.assertFalse(backends.self_test(self.engine / "nowhere", self.model_file())["ok"])
        self.assertFalse(backends.self_test(self.engine, None)["ok"])

    # -- probe model choice ------------------------------------------------
    def test_probe_models_order_by_size_and_skip_giants(self):
        small, mid, giant, missing = self.model_file(10, "small"), self.model_file(1000, "mid"), \
            self.model_file(10, "giant"), self.data / "gone.gguf"
        out = backends.probe_models([
            {"path": str(mid), "bytes": 1000},
            {"path": str(giant), "bytes": 5 * GIB},
            {"path": str(small), "bytes": 10},
            {"path": str(missing), "bytes": 100},
            "junk",
            {"path": str(small), "bytes": 10},          # duplicate path
        ])
        self.assertEqual(out, [small, mid])

    # -- planning and health use the verdict -------------------------------
    def test_plan_forces_cpu_when_the_backend_layer_says_not_proven(self):
        hw = {"ram_bytes": 32 * GIB, "ram_gib": 32, "threads": 16, "llama_binary": True,
              "ssd_stream": True, "backends": [], "devices": [],
              "gpu_reason": "backend_crashed_on_this_host"}
        plan = perf.resolve(lim={"ngl": "auto", "threads": "auto", "source": "test"}, hw=hw,
                             model_bytes=2000 * 1024**2, model_id="qwen2.5:3b")
        self.assertEqual(plan["ngl"], 0)
        self.assertEqual(plan["mode"], "cpu")
        self.assertEqual(plan["reason"], "backend_crashed_on_this_host")
        self.assertIn("perf", json.dumps(plan).lower())

    def test_plan_refuses_to_claim_a_gpu_the_backend_layer_did_not_prove(self):
        hw = {"ram_bytes": 32 * GIB, "ram_gib": 32, "threads": 16, "llama_binary": True,
              "ssd_stream": True, "backends": ["vulkan"],
              "devices": [{"id": "Vulkan0", "name": "RTX 4060 Ti", "total_mib": 7949, "free_mib": 7181}],
              "vram_bytes": 8 * GIB, "gpu_ok": False, "gpu_reason": "backend_sees_no_device"}
        plan = perf.resolve(lim={"ngl": "auto", "threads": "auto", "source": "test"}, hw=hw,
                             model_bytes=2000 * 1024**2, model_id="qwen2.5:3b")
        self.assertEqual(plan["ngl"], 0)
        self.assertEqual(plan["reason"], "backend_sees_no_device")

    def test_health_reports_effective_not_planned_after_a_fallback(self):
        state = {"lygo_engine": {
            "selected": "qwen2.5:3b", "selected_source": "manual", "port": 9641,
            "perf": {"mode": "gpu_full", "ngl": 99, "threads": 16, "backend": "cuda",
                     "engine_dir": str(self.store / "cuda"), "gpu_ok": True,
                     "effective": {"ngl": 0, "threads": 12, "backend": "cuda",
                                   "mode": "cpu", "engine_dir": str(self.store / "cuda")}},
        }}
        out = perf.report(state)
        self.assertEqual(out["ngl"], 0)
        self.assertEqual(out["mode"], "cpu")
        self.assertEqual(out["threads"], 12)
        self.assertEqual(out["planned"]["ngl"], 99)
        self.assertEqual(out["effective"]["ngl"], 0)

    def test_backend_report_never_raises_with_a_broken_store(self):
        (self.store / "vulkan").mkdir()
        (self.store / "vulkan" / "backend.json").write_text("{not json", encoding="utf-8")
        (self.active).write_text("[]", encoding="utf-8")
        out = backends.report()
        self.assertIn("active", out)
        self.assertEqual(out["installed"], [])

    # -- paths fallback ----------------------------------------------------
    def test_stale_active_record_falls_back_to_the_shipped_engine(self):
        self.active.write_text("{broken", encoding="utf-8")
        self.assertEqual(Path(paths.engine_dir()), self.engine)
        self.active.write_text(json.dumps({"backend": "cuda", "engine_dir": str(self.data / "gone")}),
                               encoding="utf-8")
        self.assertEqual(Path(paths.engine_dir()), self.engine)
        good = self.add_engine_build("cuda")
        self.active.write_text(json.dumps({"backend": "cuda", "engine_dir": str(good)}), encoding="utf-8")
        self.assertEqual(Path(paths.engine_dir()), good)
        backends.clear_active()
        self.assertEqual(Path(paths.engine_dir()), self.engine)


if __name__ == "__main__":
    unittest.main()
