"""Media limbs: picture generation and voice generation, found by path, never by drive letter.

A machine with no generators must still pass this suite AND still answer honestly, so every test
here is hermetic: the ones that need a real engine SKIP when it is absent, and the refusal paths
run everywhere. A drive-specific assertion would fail the USB kit while the USB kit was right.

Rules under test:
- the three limbs are registered (schema + canonical keys + dispatch), so the model can see them;
- an empty argument is refused BY NAME, never raised inside a handler;
- a missing engine is named together with the config key that would fix it - never a crash, and
  never a bare "missing", which the operator reads as a broken tool;
- numeric arguments survive a model sending "4", 4.0, "" or "large";
- distilled checkpoints get distilled settings, because 20 steps at cfg 7 on a turbo model is
  both slow and wrong;
- media_status never raises, and reports readiness rather than assuming it.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import limbs  # noqa: E402
import media_tools as mt  # noqa: E402


def _spec(name: str) -> dict | None:
    for s in limbs.EXTRA_SCHEMA:
        fn = s.get("function") or {}
        if fn.get("name") == name:
            return fn
    return None


class RegistrationTests(unittest.TestCase):
    def test_the_three_limbs_are_offered_to_the_model(self):
        for name in ("image_generate", "sound_speak", "media_status"):
            self.assertIsNotNone(_spec(name), f"{name} is missing from EXTRA_SCHEMA")

    def test_required_args_match_what_the_limb_actually_reads(self):
        self.assertEqual(_spec("image_generate")["parameters"]["required"], ["prompt"])
        self.assertEqual(_spec("sound_speak")["parameters"]["required"], ["text"])
        self.assertNotIn("required", _spec("media_status")["parameters"])

    def test_canonical_keys_are_registered(self):
        self.assertEqual(limbs.CANON_KEYS.get("image_generate"), ("prompt",))
        self.assertEqual(limbs.CANON_KEYS.get("sound_speak"), ("text",))
        self.assertEqual(limbs.CANON_KEYS.get("media_status"), ())
        self.assertIn("prompt", limbs.ALIAS_POOL)

    def test_a_prompt_sent_as_text_still_lands(self):
        _, args = limbs.canonicalize("image_generate", {"text": "a blue door"})
        self.assertEqual(args.get("prompt"), "a blue door")

    def test_the_model_argument_says_which_generators_exist(self):
        # An argument with no description is a trap for a small model: it has no way to learn that a
        # second, slower, higher-quality generator is available by name.
        d = _spec("image_generate")["parameters"]["properties"]["model"].get("description", "")
        self.assertIn("media_status", d)
        self.assertIn("default", d.lower())
        self.assertIn("qwen-image-2.1", d)

    def test_generation_is_not_described_like_inspection(self):
        # image_info / image_see already existed. If these descriptions overlap, a small model
        # answers "draw a cat" by inspecting a cat it does not have.
        gi = _spec("image_generate")["description"].lower()
        self.assertIn("generation", gi)
        self.assertIn("image_info", gi)
        ss = _spec("sound_speak")["description"].lower()
        self.assertIn("speech", ss)
        self.assertIn("voicestudio", ss)
        ms = _spec("media_status")["description"].lower()
        self.assertIn("wired", ms)


class RefusalTests(unittest.TestCase):
    """No argument, no engine: the answer must NAME the problem rather than fail."""

    def test_empty_prompt_is_refused_by_name(self):
        r = mt.image_generate("")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "empty_prompt")

    def test_empty_text_is_refused_by_name(self):
        r = mt.sound_speak("")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "empty_text")

    def test_a_missing_engine_names_the_config_key_that_fixes_it(self):
        real = mt.media_root
        try:
            mt.media_root = lambda: Path(tempfile.mkdtemp(prefix="lygo_nomedia_"))
            r = mt.image_generate("anything at all")
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"], "no_image_engine")
            self.assertIn("sd_exe", r["hint"])
            with mock.patch.object(mt, "studio_voice_status",
                                   lambda: {"present": False, "up": False}):
                s = mt.sound_speak("anything at all")
            self.assertFalse(s["ok"])
            self.assertEqual(s["error"], "no_voice_engine")
            self.assertIn("piper_exe", s["hint"])
        finally:
            mt.media_root = real

    def test_dispatch_refuses_without_raising(self):
        r = limbs.extra("image_generate", {})
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "empty_prompt")
        r = limbs.extra("sound_speak", {})
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "empty_text")


class CoercionTests(unittest.TestCase):
    """A language model is not a typed argument list."""

    def test_bad_integers_never_raise(self):
        self.assertEqual(mt._inum("4", 20), 4)
        self.assertEqual(mt._inum(4.7, 20), 4)
        self.assertEqual(mt._inum("", 20), 20)
        self.assertEqual(mt._inum("large", 20), 20)
        self.assertEqual(mt._inum(None, 20), 20)

    def test_bad_floats_never_raise(self):
        self.assertAlmostEqual(mt._fnum("1.5", 7.0), 1.5)
        self.assertEqual(mt._fnum("nope", 7.0), 7.0)
        self.assertEqual(mt._fnum(None, 7.0), 7.0)


class CharacterStabilityTests(unittest.TestCase):
    """One-person scenes were cloning and morphing with an empty negative."""

    def test_a_one_person_prompt_gets_a_single_subject_cue(self):
        out = mt.stabilize_prompt("a woman in a bunny suit, cute")
        self.assertIn("single subject", out.lower())
        self.assertIn("two arms", out.lower())
        self.assertIn("bunny", out.lower())

    def test_a_group_prompt_is_left_alone(self):
        p = "a couple standing together in a park"
        self.assertEqual(mt.stabilize_prompt(p), p)

    def test_an_empty_negative_fills_anti_clone_terms(self):
        n = mt.stabilize_negative("")
        self.assertIn("duplicate", n)
        self.assertIn("morphing", n)
        self.assertIn("extra limbs", n)

    def test_an_explicit_negative_is_kept(self):
        self.assertEqual(mt.stabilize_negative("blurry"), "blurry")

    def test_a_person_prompt_uses_qwen_when_that_recipe_is_ready(self):
        rec = {
            "id": "qwen-image-2.1", "ready": True, "missing": [],
            "paths": {"diffusion_model": "x.gguf"},
        }
        with mock.patch.object(mt, "image_recipe", side_effect=lambda n: rec if n == "qwen-image-2.1" else None):
            with mock.patch.object(mt, "_image_from_recipe",
                                   return_value={"ok": True, "model": "qwen-image-2.1", "path": "x"}) as run:
                r = mt.image_generate("a woman in a steampunk corset")
        self.assertTrue(r["ok"])
        self.assertEqual(run.call_args[0][0]["id"], "qwen-image-2.1")

    def test_an_object_prompt_uses_qwen_when_that_recipe_is_ready(self):
        rec = {"id": "qwen-image-2.1", "ready": True, "missing": [], "paths": {"diffusion_model": "x.gguf"}}
        with mock.patch.object(mt, "image_recipe", side_effect=lambda n: rec if n == "qwen-image-2.1" else None):
            with mock.patch.object(mt, "_image_from_recipe",
                                   return_value={"ok": True, "model": "qwen-image-2.1", "path": "x"}) as run:
                r = mt.image_generate("a red apple on a wooden table")
        self.assertTrue(r["ok"])
        self.assertEqual(run.call_args[0][0]["id"], "qwen-image-2.1")

    def test_cpu_and_an_explicit_checkpoint_stay_off_qwen(self):
        rec = {"id": "qwen-image-2.1", "ready": True, "missing": [], "paths": {"diffusion_model": "x.gguf"}}
        with mock.patch.object(mt, "image_recipe", side_effect=lambda n: rec if n == "qwen-image-2.1" else None):
            with mock.patch.object(mt, "_image_from_recipe") as run:
                with mock.patch.object(mt, "sd_exe", return_value=None), mock.patch.object(
                    mt, "sd_cpu_exe", return_value=None
                ):
                    forced = mt.image_generate("a red apple on a wooden table", cpu=True)
                    named = mt.image_generate("a red apple on a wooden table", model=str(Path(__file__)))
        self.assertFalse(run.called)
        self.assertEqual(forced.get("error"), "no_image_engine")
        self.assertEqual(named.get("error"), "no_image_engine")

    def test_the_main_recipe_passes_a_fresh_seed(self):
        rec = {
            "id": "qwen-image-2.1", "ready": True, "missing": [],
            "paths": {"engine": str(Path(__file__)), "diffusion_model": "w.gguf"},
            "steps": 28, "cfg_scale": 2.5, "size": 1024, "align": 32,
            "extra": ["--offload-to-cpu"],
        }
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"] = argv
            dest = Path(argv[argv.index("-o") + 1])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"\x89PNG" + b"0" * 32)
            return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with mock.patch.object(mt.subprocess, "run", fake_run), \
                mock.patch.object(mt, "_out_dir", lambda kind: Path(tempfile.mkdtemp())):
            r = mt._image_from_recipe(rec, "a red barn at dusk", "", 0, 0, 0, 0, 30)
        self.assertTrue(r["ok"], r)
        self.assertIn("-s", seen["argv"])
        self.assertNotEqual(seen["argv"][seen["argv"].index("-s") + 1], "42")


class CheckpointDefaultsTests(unittest.TestCase):
    """The distilled-checkpoint rule: few steps, low guidance."""

    def test_distilled_checkpoints_are_recognised_by_name(self):
        for name in ("sd_xl_turbo_1.0_fp16.safetensors", "flux1-schnell.safetensors",
                     "sdxl_lightning_4step.safetensors", "lcm_lora_merged.safetensors"):
            self.assertTrue(mt._is_turbo(name), name)
        self.assertFalse(mt._is_turbo("v1-5-pruned-emaonly.safetensors"))

    def test_xl_checkpoints_are_sized_at_1024(self):
        self.assertTrue(mt._is_xl("sd_xl_turbo_1.0_fp16.safetensors"))
        self.assertFalse(mt._is_xl("v1-5-pruned-emaonly.safetensors"))


class StatusTests(unittest.TestCase):
    def test_status_never_raises_and_reports_readiness(self):
        s = mt.media_status()
        self.assertTrue(s["ok"])
        self.assertIn("ready", s["image"])
        self.assertIn("ready", s["sound"])
        self.assertIsInstance(mt.media_root(), Path)

    def test_status_reports_not_ready_when_the_root_is_empty(self):
        real = mt.media_root
        try:
            mt.media_root = lambda: Path(tempfile.mkdtemp(prefix="lygo_nomedia_"))
            with mock.patch.object(mt, "studio_voice_status",
                                   lambda: {"present": False, "up": False, "engine": "voicestudio"}):
                s = mt.media_status()
            self.assertFalse(s["image"]["ready"])
            self.assertFalse(s["sound"]["ready"])
            self.assertIn("studio", s["sound"])
        finally:
            mt.media_root = real


class StudioVoiceRouteTests(unittest.TestCase):
    """Chat speech uses VoiceStudio when that service is answering, Piper when it is not.

    VoiceStudio is a separate AGPL application. The limb talks to it over loopback HTTP through
    voice_harness and never imports its source. These tests fake the harness so a machine without
    the app, and a machine whose app is shut, stay green.
    """

    def test_a_live_studio_is_used_instead_of_piper(self):
        dest = Path(tempfile.mkdtemp(prefix="lygo_studio_")) / "voice-test.wav"
        dest.write_bytes(b"RIFF" + (b"\x00" * 40))
        spoken = []

        def fake_status():
            return {"present": True, "up": True, "base": "http://127.0.0.1:3900",
                    "engine": "voicestudio", "models": ["omnivoice"]}

        def fake_speak(text, voice="", length_scale=1.0):
            spoken.append({"text": text, "voice": voice, "length_scale": length_scale})
            return {"ok": True, "path": str(dest), "bytes": dest.stat().st_size,
                    "engine": "voicestudio", "voice": voice or "omnivoice",
                    "format": "wav", "audio_seconds": 1.2}

        with mock.patch.object(mt, "studio_voice_status", fake_status), \
                mock.patch.object(mt, "_speak_via_studio", fake_speak), \
                mock.patch.object(mt, "piper_exe", lambda: None):
            r = mt.sound_speak("This is LYGO Signal, live on the local dial.", voice="host")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["engine"], "voicestudio")
        self.assertEqual(spoken[0]["text"], "This is LYGO Signal, live on the local dial.")
        self.assertEqual(spoken[0]["voice"], "host")
        self.assertEqual(r["path"], str(dest))

    def test_piper_is_the_fallback_when_studio_is_down(self):
        with mock.patch.object(mt, "studio_voice_status",
                               lambda: {"present": True, "up": False}), \
                mock.patch.object(mt, "piper_exe", lambda: None):
            r = mt.sound_speak("anything at all")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "no_voice_engine")
        self.assertIn("piper_exe", r["hint"])


class CpuRouteArgvTests(unittest.TestCase):
    """The argv the picture limb actually builds, on both routes.

    MEASURED on this host 2026-09-23, SDXL-Turbo at 1024x1024 on an 8 GB card:
      CPU build, no tiling  -> exit 3221225786, log ends "decoding 1 latents", NO file, 183.4 s;
      CPU build, --vae-tiling -> "latent 1 decoded, taking 151.17s" in 49 tiles, 2.1 MB PNG, 272.0 s;
      CUDA build, no tiling -> the same prompt drawn in 12.5 s.
    So the flag belongs to the CPU route, and only there.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lygo_render_"))
        for rel in ("tools/sd-cpu/sd-cli.exe", "tools/sd/sd-cli.exe",
                    "models/sd/sd_xl_turbo_1.0_fp16.safetensors"):
            f = self.tmp / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x" * 128)
        self.seen: dict = {}

        def fake_run(argv, **kw):
            self.seen["argv"] = argv
            dest = Path(argv[argv.index("-o") + 1])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"\x89PNG" + b"0" * 64)
            return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        self.stack = [mock.patch.object(mt, "media_root", lambda: self.tmp),
                      mock.patch.object(mt, "_cfg", lambda: {}),
                      mock.patch.object(mt, "_out_dir", lambda kind: self.tmp / "out"),
                      mock.patch.object(mt.subprocess, "run", fake_run)]
        for p in self.stack:
            p.start()

    def tearDown(self):
        for p in reversed(self.stack):
            p.stop()

    def test_the_cpu_route_tiles_the_vae_decode(self):
        r = mt.image_generate("a lime green sports car on a neon street", cpu=True)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["engine"], "sd-cpu")
        self.assertIn("--vae-tiling", self.seen["argv"])
        self.assertTrue(Path(r["path"]).is_file())

    def test_the_card_route_is_left_exactly_as_it_was(self):
        r = mt.image_generate("a lime green sports car on a neon street")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["engine"], "sd")
        self.assertNotIn("--vae-tiling", self.seen["argv"])
        self.assertIn("-s", self.seen["argv"])
        self.assertNotEqual(self.seen["argv"][self.seen["argv"].index("-s") + 1], "42")

    def test_the_route_asks_the_card_for_the_checkpoint_not_the_shortfall(self):
        calls = {}

        def fake_route(need, **kw):
            calls["need"] = need
            return "cuda", "the card has room for this"

        with mock.patch.object(mt, "picture_route", fake_route):
            mt._route_for_a_render()
        size = mt.sd_model().stat().st_size
        self.assertEqual(calls["need"], mt.render_need_mib(size))
        self.assertGreaterEqual(calls["need"], mt.RENDER_NEED_MIB)


class DeclaredModelTests(unittest.TestCase):
    """A model with several weight files is declared as data, and a heavy one stays opt-in.

    Two rules are pinned here. A model whose weights are not on disk must be refused BY NAME with the
    missing files listed - not crash, not a bare "missing". And the fastest generator on the host stays
    the default: a model wired for a much larger machine must never silently take the default path,
    because on an 8 GB card it is slow at best and unloadable at worst.
    """

    def _recipe(self, **over) -> dict:
        rec = {
            "id": "test-flow",
            "paths": {},
            "missing": [],
            "ready": True,
            "default": False,
            "size": 1024,
            "steps": 40,
            "cfg_scale": 6.0,
            "align": 32,
            "extra": [],
        }
        rec.update(over)
        return rec

    def test_a_wired_but_undownloaded_model_says_exactly_that(self):
        r = mt._image_from_recipe(self._recipe(missing=["vae", "llm"]), "a cat", "", 0, 0, 0, 0.0, 30)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "image_weights_missing")
        self.assertEqual(r["model"], "test-flow")
        self.assertEqual(sorted(r["missing"]), ["llm", "vae"])
        self.assertIn("VAE", r["hint"])  # names the fix, never a bare "missing"

    def test_two_weight_files_in_one_entry_do_not_collide(self):
        rec = self._recipe(id="a", paths={"diffusion_model": "x.gguf"})
        self.assertEqual(mt._image_from_recipe(rec, "p", "", 0, 0, 0, 0.0, 30)["model"], "a")

    def test_only_a_declared_id_reaches_the_multi_weight_path(self):
        real = mt.image_recipes
        try:
            mt.image_recipes = lambda: [self._recipe()]
            self.assertIsNotNone(mt.image_recipe("test-flow"))
            for miss in ("", "   ", "sd_xl_turbo_1.0_fp16.safetensors",
                         "<MEDIA>/models/sd/other.gguf", "TEST-FLOW"):
                self.assertIsNone(mt.image_recipe(miss), miss)
        finally:
            mt.image_recipes = real

    def test_dimensions_are_snapped_to_the_architectures_own_tile(self):
        self.assertEqual(mt._align(1000, 32), 992)
        self.assertEqual(mt._align(1024, 32), 1024)
        self.assertEqual(mt._align(10, 32), 32)

    def test_status_names_the_default_and_lists_every_declared_model(self):
        img = mt.media_status()["image"]
        self.assertIn("default", img)
        self.assertIsInstance(img["models"], list)
        for m in img["models"]:
            self.assertIn("ready", m)
            self.assertIn("missing", m)
            self.assertIn("default", m)
        # Qwen-Image 2.1 is the draw when its weights are on disk. Turbo only when they are not.
        qwen = [m for m in img["models"] if m["id"] == "qwen-image-2.1" and m["ready"]]
        if qwen:
            self.assertEqual(img["default"], "qwen-image-2.1")
            self.assertTrue(qwen[0]["default"])
        else:
            self.assertFalse(any(m["default"] for m in img["models"]))

    def test_a_declared_model_absent_from_disk_is_refused_by_name(self):
        real = mt.media_root
        try:
            mt.media_root = lambda: Path(tempfile.mkdtemp(prefix="lygo_noweights_"))
            declared = mt.image_recipes()
            if not declared:
                self.skipTest("no image models declared in this edition")
            r = mt.image_generate("a lighthouse in fog", model=declared[0]["id"])
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"], "image_weights_missing")
            self.assertTrue(r["missing"])
        finally:
            mt.media_root = real

    def test_the_declared_model_keeps_its_own_engine_out_of_the_proven_one(self):
        declared = [r for r in mt.image_recipes() if r["id"] == "qwen-image-2.1"]
        if not declared:
            self.skipTest("qwen-image-2.1 is not declared in this edition's config")
        rec = declared[0]
        for key in ("diffusion_model", "vae", "llm"):
            self.assertIn(key, rec["paths"], f"{key} must be declared, even before it is downloaded")
        self.assertIn("qwen_image_2.1", rec["paths"]["diffusion_model"])
        self.assertIn("qwen_image_2.1", rec["paths"]["vae"])
        # ~9 GB of weights cannot live in an 8 GB card, so the entry must ask for CPU offload.
        self.assertIn("--offload-to-cpu", rec["extra"])
        # No second engine copy: the shipped build loads this model, proven by a real render.
        self.assertNotIn("engine", rec["paths"])


class ShippableWiringTests(unittest.TestCase):
    """We ship the wiring, never the weights.

    A declared model whose weights are not present must be able to point the operator at the vendor
    and state the terms - otherwise "the capability is wired" is indistinguishable from "the feature
    is missing", and the console silently under-reports what it can do.
    """

    def test_every_absent_weight_has_somewhere_to_get_it(self):
        real = mt.media_root
        try:
            mt.media_root = lambda: Path(tempfile.mkdtemp(prefix="lygo_noweights_"))
            declared = mt.image_recipes()
            if not declared:
                self.skipTest("no image models declared in this edition")
            rec = declared[0]
            self.assertFalse(rec["ready"])
            for key in rec["missing"]:
                self.assertIn(key, rec["sources"], f"{key} is absent and has no source URL")
                self.assertTrue(str(rec["sources"][key].get("url", "")).startswith("http"))
            r = mt.image_generate("a lighthouse in fog", model=rec["id"])
            self.assertEqual(r["error"], "image_weights_missing")
            self.assertTrue(r["sources"], "the refusal must point at where to get the weights")
            self.assertIn("missing", r["hint"])
        finally:
            mt.media_root = real

    def test_status_carries_sources_so_the_operator_can_act(self):
        for m in mt.media_status()["image"]["models"]:
            self.assertIn("sources", m)
            self.assertIn("license", m)

    def test_the_declared_model_records_its_terms(self):
        declared = [r for r in mt.image_recipes() if r["id"] == "qwen-image-2.1"]
        if not declared:
            self.skipTest("qwen-image-2.1 is not declared in this edition's config")
        rec = declared[0]
        self.assertIn("Research", rec["license"])
        self.assertIn("never", rec["license"].lower())
        # the redistribution status of each file is recorded, not assumed
        self.assertIn("apache-2.0", str(rec["sources"]["llm"].get("note", "")).lower())


class LiveEngineTests(unittest.TestCase):
    """Runs only where a generator is installed. Skips - never fails - elsewhere."""

    def test_a_picture_can_actually_be_produced(self):
        """A picture, or an honest account of a busy card - never a bare failure.

        The chat model holds an 8 GB card while the console is up, so a render that cannot get its
        644 MB is the normal state of a working machine, not a broken limb: measured 2026-09-21 with
        gemma4-12b resident (10.9 GB, 79 GPU layers), sd-cli loaded the checkpoint and died 19.8 s in
        with "cannot make enough memory available on CUDA0 ... available 0.00 MB device". What must
        never happen is this test failing for that reason alone: either the picture is on disk, or the
        limb names what holds the card and offers the route that does not need it. A wrong or missing
        explanation still fails, and so does an engine that is not there at all.
        """
        if not mt.media_status()["image"]["ready"]:
            self.skipTest("no image engine on this machine")
        # The empty-model default is Qwen-Image 2.1 and is covered without spawning it.
        # This smoke stays on the classic checkpoint so a suite run is one short draw.
        r = mt.image_generate("a red apple on a wooden table", model=str(mt.sd_model() or ""),
                              width=512, height=512, steps=4)
        if not r["ok"]:
            self.assertIn("hint", r, "a failed render must say why: " + str(r)[:300])
            self.assertIn("card", str(r["hint"]).lower(),
                          "it must name what is holding the card: " + str(r)[:300])
            self.assertTrue(r.get("retry_with_cpu"),
                            "the route that does not need the card must be offered: " + str(r)[:300])
            return
        self.assertGreater(r["bytes"], 10000)
        self.assertTrue(Path(r["path"]).is_file())
        Path(r["path"]).unlink(missing_ok=True)  # never leave test output in the operator's folder

    def test_a_voice_can_actually_be_produced(self):
        if not mt.media_status()["sound"]["ready"]:
            self.skipTest("no voice engine on this machine")
        r = mt.sound_speak("Testing the local voice.")
        self.assertTrue(r["ok"], r)
        self.assertGreater(r["audio_seconds"], 0.5)
        self.assertTrue(Path(r["path"]).is_file())
        Path(r["path"]).unlink(missing_ok=True)


class AFullCardIsExplainedTests(unittest.TestCase):
    """A full card is the NORMAL state on a working console - the chat model is the card.

    Measured on this host 2026-09-21 with gemma4-12b holding it (10.9 GB resident, 79 GPU layers): the
    picture engine loaded its 6.9 GB checkpoint and then died 19.8 s in, exit 1, no picture, with its own
    log saying

        [WARN] model_manager.cpp:1801 - model manager cannot make enough memory available on CUDA0:
        need 644.05 MB device / 132.05 MB budget, available 0.00 MB device / 1015.60 MB budget

    The limb reported `image_failed` with the raw tail: not the cause, and not the way out. The operator
    is left reading a CUDA warning, which is how "image creation is broken" is experienced. A picture
    that cannot be rendered because the card is busy must say so, name what is holding it, and offer the
    route that does not need the card at all (the CPU build is on this machine).
    """

    REAL_TAIL = [
        "[INFO   ] model_loader.cpp:1309 - loading tensors completed, taking 13.27s",
        "[WARN   ] model_manager.cpp:1801 - model manager cannot make enough memory available on CUDA0: "
        "need 644.05 MB device / 132.05 MB budget, available 0.00 MB device / 1015.60 MB budget",
    ]

    def test_the_engines_own_memory_warning_becomes_the_operators_next_move(self):
        hint = mt.memory_hint(self.REAL_TAIL)
        self.assertIn("card", hint.lower())
        self.assertIn("chat", hint.lower(), "it must name what is holding the card: " + hint)
        self.assertIn("cpu", hint.lower(), "the way out must be named: " + hint)

    def test_an_unrelated_failure_is_not_given_a_memory_story(self):
        self.assertEqual(mt.memory_hint(["[ERROR] invalid prompt: empty"]), "")

    def test_a_failed_render_carries_the_hint_and_the_cpu_route(self):
        class FakeDone:
            returncode = 1
            stdout = ""
            stderr = "\n".join(self.REAL_TAIL)

        with mock.patch.object(mt.subprocess, "run", return_value=FakeDone()), \
                mock.patch.object(mt, "picture_route", lambda need_mib=0, reserve_mib=0: ("cuda", "the card has 7111 MiB free")), \
                mock.patch.object(mt, "sd_exe", lambda: Path(__file__)), \
                mock.patch.object(mt, "sd_model", lambda: Path(__file__)), \
                mock.patch.object(mt, "sd_cpu_exe", lambda: Path(__file__)), \
                mock.patch.object(mt, "_out_dir", lambda kind: Path(tempfile.mkdtemp())):
            r = mt.image_generate("a red apple", model=str(Path(__file__)), width=512, height=512, steps=4)
        self.assertFalse(r["ok"])
        self.assertIn("hint", r, "a full card came back as a bare failure: " + str(r)[:200])
        # Contract corrected 2026-09-21: the limb no longer merely OFFERS the route that needs no card,
        # it takes it and returns that attempt's result. What must survive is the reason - the operator
        # still has to be told the card was held by the chat model when the CPU attempt is the one that
        # fails (ABusyCardStillDrawsTests holds the success side of this).
        self.assertTrue(r.get("fell_back_from"),
                        "the card's own reason must survive the CPU attempt: " + str(r)[:200])
        self.assertIn("cpu", str(r.get("fell_back_from", {}).get("reason", "")).lower(),
                      "and it must still name the way out: " + str(r)[:200])


class ABusyCardStillDrawsTests(unittest.TestCase):
    """A picture the operator can look at beats a correct report about VRAM.

    On this box the chat model holds the card on every working boot, so "the card is busy" is not an edge
    case - it is the normal state. Reporting it and stopping leaves "draw me a picture" broken in the only
    configuration this machine has, which is how the operator experiences it. The limb must take the route
    that needs no card (a CPU build is on disk here) and say which route it took.

    Measured 2026-09-21 through the limb itself: the GPU build loaded its 6.9 GB checkpoint, then died with
    `model manager cannot make enough memory available on CUDA0: need 644.05 MB ... available 0.00 MB`.
    """

    REAL_TAIL = AFullCardIsExplainedTests.REAL_TAIL

    class _Done:
        def __init__(self, rc, err=""):
            self.returncode, self.stdout, self.stderr = rc, "", err

    def _patched(self, gpu_fails_with, cpu_writes=True):
        """Return (result_of_call, gpu_calls, cpu_calls) with both routes faked."""
        gpu_path, cpu_path = Path(__file__), Path(mt.__file__)
        seen = {"gpu": [], "cpu": []}

        def fake_run(argv, **kw):
            if str(argv[0]) == str(cpu_path):
                seen["cpu"].append(argv)
                if cpu_writes:
                    dest = Path(argv[argv.index("-o") + 1])
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
                    return ABusyCardStillDrawsTests._Done(0)
                return ABusyCardStillDrawsTests._Done(1, "cpu build also unhappy")
            seen["gpu"].append(argv)
            return ABusyCardStillDrawsTests._Done(1, gpu_fails_with)

        with mock.patch.object(mt.subprocess, "run", fake_run), \
                mock.patch.object(mt, "sd_exe", lambda: gpu_path), \
                mock.patch.object(mt, "sd_model", lambda: gpu_path), \
                mock.patch.object(mt, "sd_cpu_exe", lambda: cpu_path), \
                mock.patch.object(mt, "image_recipe", lambda model: None), \
                mock.patch.object(mt, "picture_route", lambda need_mib=0, reserve_mib=0: ("cuda", "the card has 7111 MiB free")), \
                mock.patch.object(mt, "_out_dir", lambda kind: Path(tempfile.mkdtemp())):
            r = mt.image_generate("a red apple", width=512, height=512, steps=4)
        return r, seen["gpu"], seen["cpu"]

    def test_a_card_held_by_the_chat_model_still_produces_a_picture(self):
        r, gpu, cpu = self._patched("\n".join(self.REAL_TAIL))
        self.assertTrue(r.get("ok"), "a busy card must still draw, not just report: " + str(r)[:300])
        self.assertTrue(r.get("path") and Path(r["path"]).is_file())
        self.assertEqual(len(gpu), 1, "the card is tried once, then left alone")
        self.assertEqual(len(cpu), 1, "the route that needs no card is the way out: " + str(cpu))
        note = (r.get("note") or "").lower()
        self.assertIn("cpu", note, "it must say which route drew it: " + note)
        self.assertIn("card", note, "and why: " + note)

    def test_an_unrelated_failure_is_not_retried_on_the_cpu(self):
        r, gpu, cpu = self._patched("[ERROR] invalid prompt: empty")
        self.assertFalse(r.get("ok"))
        self.assertEqual(len(gpu), 1)
        self.assertEqual(cpu, [], "a bad prompt is not a busy card")
        self.assertNotIn("retry_with_cpu", r)

    def test_a_card_the_harness_knows_is_full_is_not_even_tried(self):
        """The harness answers before the engine is spawned: no 20-second doomed GPU attempt.

        Measured 2026-09-21: the GPU build loaded its 6.9 GB checkpoint, ran 19.8 s, then died asking for
        644.05 MB with 0.00 MB available. Asking hardware.picture_route first turns that into a note and a
        render on the CPU, which completed a real 1024x1024 picture in 172.1 s on this host.
        """
        gpu_path, cpu_path = Path(__file__), Path(mt.__file__)
        seen = {"gpu": [], "cpu": []}
        dest_box = {}

        class Done:
            def __init__(self, rc):
                self.returncode, self.stdout, self.stderr = rc, "", ""

        def fake_run(argv, **kw):
            if str(argv[0]) == str(cpu_path):
                seen["cpu"].append(argv)
                dest = Path(argv[argv.index("-o") + 1])
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
                dest_box["dest"] = dest
                return Done(0)
            seen["gpu"].append(argv)
            return Done(1)

        with mock.patch.object(mt.subprocess, "run", fake_run), \
                mock.patch.object(mt, "sd_exe", lambda: gpu_path), \
                mock.patch.object(mt, "sd_model", lambda: gpu_path), \
                mock.patch.object(mt, "sd_cpu_exe", lambda: cpu_path), \
                mock.patch.object(mt, "image_recipe", lambda model: None), \
                mock.patch.object(mt, "picture_route",
                                  lambda need_mib=0, reserve_mib=0: ("cpu", "the card has 132 MiB free "
                                                                     "but this render needs 644 MiB")), \
                mock.patch.object(mt, "_out_dir", lambda kind: Path(tempfile.mkdtemp())):
            r = mt.image_generate("a red apple", width=512, height=512, steps=4)
        self.assertTrue(r.get("ok"), "the CPU route must still draw: " + str(r)[:250])
        self.assertEqual(seen["gpu"], [], "a card known to be full must not be tried: " + str(seen))
        self.assertEqual(len(seen["cpu"]), 1)
        note = (r.get("note") or "").lower()
        self.assertIn("cpu", note)
        self.assertIn("132", note, "the live reading must be in the words the operator reads: " + note)


    def test_the_cpu_route_is_never_asked_to_retry_itself(self):
        cpu_path = Path(mt.__file__)
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return ABusyCardStillDrawsTests._Done(1, "\n".join(self.REAL_TAIL))

        with mock.patch.object(mt.subprocess, "run", fake_run), \
                mock.patch.object(mt, "sd_cpu_exe", lambda: cpu_path), \
                mock.patch.object(mt, "sd_model", lambda: cpu_path), \
                mock.patch.object(mt, "image_recipe", lambda model: None), \
                mock.patch.object(mt, "_out_dir", lambda kind: Path(tempfile.mkdtemp())):
            r = mt.image_generate("a red apple", cpu=True, width=512, height=512, steps=4)
        self.assertFalse(r.get("ok"))
        self.assertEqual(len(calls), 1, "cpu=True must not fall back to itself forever")


if __name__ == "__main__":
    unittest.main()
