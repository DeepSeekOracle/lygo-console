"""The checker's judgement, held to account without a GPU.

check_one() needs an engine, but the labelling and the refusal to boot an impossible model are pure
logic and are exactly where a wrong answer misleads the operator about what the box can do.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import model_check  # noqa: E402


class CapabilityLabelTests(unittest.TestCase):
    def test_a_plain_chat_model_is_text(self):
        self.assertEqual(model_check.caps_for({"id": "gemma4-12b", "kind": "chat"}), ["text"])

    def test_a_projector_means_the_model_can_be_shown_a_picture(self):
        caps = model_check.caps_for({"id": "gemma4-12b", "kind": "chat", "mmproj": "mmproj-BF16.gguf"})
        self.assertIn("image-in", caps)
        self.assertIn("text", caps)

    def test_a_coder_name_is_a_claim_and_the_probe_is_the_evidence(self):
        caps = model_check.caps_for({"id": "qwen2.5-coder:14b", "kind": "chat"})
        self.assertIn("coder", caps)
        # The label is carried as a claim from the name; nothing may claim a MEASURED code skill
        # before the probe answers, which is why the measurement is recorded separately.
        self.assertEqual(model_check.caps_for({"id": "llama3.2:1b", "kind": "chat"}), ["text"])

    def test_an_embedding_model_is_not_advertised_as_a_chat_brain(self):
        caps = model_check.caps_for({"id": "nomic-embed-text:latest", "kind": "embed"})
        self.assertEqual(caps, ["embed"])

    def test_a_sound_model_is_labelled_sound(self):
        self.assertIn("sound", model_check.caps_for({"id": "whisper-large-v3", "kind": "chat"}))

    def test_an_image_generator_is_not_advertised_as_a_chat_brain(self):
        caps = model_check.caps_for({"id": "qwen-image-2.1", "kind": "image"})
        self.assertIn("image-out", caps)
        self.assertNotIn("text", caps)

    def test_labels_are_ordered_for_a_glance(self):
        caps = model_check.caps_for({"id": "qwen3-coder:30b", "kind": "chat", "mmproj": "x-mmproj.gguf"})
        self.assertEqual(caps, [c for c in model_check.CAP_ORDER if c in caps])


class BlobFreePngTests(unittest.TestCase):
    def test_the_vision_probe_sends_a_real_png(self):
        png = model_check._png_solid((255, 0, 0), 8)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"IHDR", png)
        self.assertIn(b"IDAT", png)


class FailureClassTests(unittest.TestCase):
    """The class decides the operator's next move, so it must not default to blaming the box."""

    def test_metadata_the_loader_cannot_read_is_an_engine_limit(self):
        cls, hint = model_check.classify_failure(
            "error loading model hyperparameters: key qwen35moe.rope.dimension_sections has wrong array length; expected 4, got 3")
        self.assertEqual(cls, "engine")
        self.assertIn("newer llama.cpp build", hint)

    def test_a_tensor_shape_mismatch_is_an_engine_limit(self):
        cls, _ = model_check.classify_failure("check_tensor_dims: tensor 'blk.1.ffn_down_exps.weight' has wrong shape")
        self.assertEqual(cls, "engine")

    def test_a_silent_timeout_is_a_rig_limit_and_says_so(self):
        cls, hint = model_check.classify_failure("", "no health after 180s")
        self.assertEqual(cls, "rig")
        self.assertIn("not an agent fault", hint)

    def test_nothing_is_ever_blamed_on_the_agent(self):
        for tail in ("", "out of memory", "wrong shape", "unknown architecture"):
            _cls, hint = model_check.classify_failure(tail)
            self.assertNotIn("agent fault", hint.split("not an agent fault")[0][-30:] or "")

    def test_the_fit_plan_settles_the_class_before_the_tail_is_read(self):
        """Defect #23: a class sniffed off the log tail flips between runs.

        `nemotron-3-super` read `engine` in one sweep and `rig` in another, because the class came from
        whatever the engine's last six lines happened to say. A model the fit plan has already judged
        too_big for this host is a rig limit, and that answer must not depend on the tail at all.
        """
        for tail in ("wrong array length", "", "out of memory", "unknown architecture", "no health"):
            cls, hint = model_check.classify_failure(tail, fit={"verdict": "too_big"})
            self.assertEqual(cls, "rig", f"too_big must decide first, tail={tail!r}")
            self.assertIn("cannot hold", hint)

    def test_a_fit_that_fits_does_not_overrule_the_tail(self):
        # The override is one-directional: a model that fits this host still gets the tail's answer,
        # or the engine-limit case (a newer llama.cpp build) would be lost.
        cls, _ = model_check.classify_failure("key x has wrong array length", fit={"verdict": "cpu_ok"})
        self.assertEqual(cls, "engine")


class ImpossibleBootTests(unittest.TestCase):
    def test_a_missing_file_is_never_booted(self):
        res = model_check.check_one({"id": "ghost", "path": str(Path("nope") / "ghost.gguf")})
        self.assertEqual(res["verdict"], "missing")

    def test_a_model_larger_than_the_host_is_labelled_not_booted(self):
        # 9 TB of weights against a 28 GB host: the arithmetic refuses before any spawn is attempted.
        # (Checked as the pure rule, because no unit test can create a 9 TB file - check_one reads the
        # real on-disk size, which is the fact and beats any size a record claims.)
        self.assertFalse(model_check.fits_here(9_000_000, 28_000))
        self.assertTrue(model_check.fits_here(7_000, 28_000))

    def test_an_unknown_size_never_refuses_a_boot(self):
        # Silence is not evidence: with either number unknown the boot itself is the only honest test.
        self.assertTrue(model_check.fits_here(0, 28_000))
        self.assertTrue(model_check.fits_here(9_000_000, 0))


if __name__ == "__main__":
    unittest.main()


class TestEvidenceIsNeverInvented(unittest.TestCase):
    """The checker's evidence reader: decode right, and never turn "not stated" into a zero.

    Measured 2026-09-20: b10988 writes its engine log UTF-8 while other builds write UTF-16, and reading
    the wrong way returns mojibake without raising - so a fallback keyed on exceptions never fired and a
    log-derived "why" could surface as garbage. And when the offload line is absent (this build at its
    default verbosity), the honest answer is unknown, not 0: a false zero is exactly the defect class that
    once filed ten models as "no GPU on this host" while an RTX 4060 Ti sat idle.
    """

    PORT = 11987

    def setUp(self):
        from model_check import LOG_DIR

        self.log = LOG_DIR / f"llama-server-{self.PORT}.log"
        self.log.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        try:
            self.log.unlink()
        except OSError:
            pass

    def test_decoder_reads_both_encodings(self):
        from model_check import _decode

        text = "srv  llama_server: model loaded\nload_tensors: offloading 33 repeating layers to GPU\n"
        for enc in ("utf-8", "utf-16", "utf-16-be"):
            got = _decode(text.encode(enc))
            self.assertIn("model loaded", got, enc)
            self.assertIn("offloading 33", got, enc)
            self.assertNotIn("\ufffd", got, enc)

    def test_offload_line_is_read_from_a_utf8_log(self):
        from model_check import gpu_evidence

        self.log.write_bytes(b"I srv load_model: loading model\n"
                             b"I load_tensors: offloading 33 repeating layers to GPU\n"
                             b"I srv llama_server: model loaded\n")
        ev = gpu_evidence(self.PORT)
        self.assertEqual(ev["gpu_layers_used"], 33)

    def test_absent_offload_line_is_unknown_not_zero(self):
        from model_check import gpu_evidence

        self.log.write_bytes(b"I srv load_model: loading model\nI srv llama_server: model loaded\n")
        ev = gpu_evidence(self.PORT)
        self.assertIsNone(ev["gpu_layers_used"], "an unstated layer count must not be recorded as 0")
        self.assertNotIn("devices_seen", ev)

    def test_evidence_names_the_build_that_answered(self):
        from model_check import gpu_evidence

        self.log.write_bytes(b"I srv llama_server: model loaded\n")
        ev = gpu_evidence(self.PORT)
        self.assertTrue(ev["gpu_backend"], "the answering build must be recorded")
        self.assertIsInstance(ev["gpu_device"], str)


class TestRatesAreSamplesNotNoise(unittest.TestCase):
    """A speed label must come from a sample that can carry it."""

    def test_two_token_answer_is_not_rated(self):
        from model_check import rate_from

        got = rate_from({"prompt_n": 31, "predicted_n": 2, "prompt_per_second": 116.1,
                         "predicted_per_second": 16.6})
        self.assertNotIn("gen_tps", got, "a 2-token answer cannot rate generation")
        self.assertIn("rate_note", got)
        self.assertEqual(got["predicted_n"], 2)
        self.assertEqual(got["prefill_tps"], 116.1, "a 31-token prompt can rate prefill")

    def test_a_real_sample_is_rated(self):
        from model_check import rate_from

        got = rate_from({"prompt_n": 46, "predicted_n": 128, "prompt_per_second": 280.0,
                         "predicted_per_second": 135.8})
        self.assertEqual(got["gen_tps"], 135.8)
        self.assertNotIn("rate_note", got)

    def test_empty_timings_give_nothing(self):
        from model_check import rate_from

        self.assertNotIn("gen_tps", rate_from({}))


class FitDecidesTheClassOnARealCheckTests(unittest.TestCase):
    """The wiring, not only the rule: check_one must hand the fit plan to the classifier."""

    def test_a_model_too_big_for_this_host_never_reads_engine(self):
        import lygo_engine
        import model_fit

        d = tempfile.TemporaryDirectory(prefix="lygo_fit_")
        self.addCleanup(d.cleanup)
        model = Path(d.name) / "nemotron-3-super.gguf"
        model.write_bytes(b"g" * 4096)

        def boom(rec, *, api_key, state, port=None):
            raise RuntimeError("llama-server exited 1")

        seen: list = []
        with patch.object(model_fit, "verdict",
                          return_value={"verdict": "too_big", "need_mib": 88000, "ram_usable_mib": 20000}), \
                patch.object(lygo_engine, "boot", side_effect=boom), \
                patch.object(model_check, "_log_tail", return_value="key x has wrong array length"):
            for _ in range(2):
                res = model_check.check_one({"id": "nemotron-3-super:latest",
                                             "path": str(model), "kind": "chat"})
                seen.append(res.get("fail_class"))
        self.assertEqual(seen, ["rig", "rig"], f"the class flipped between runs: {seen}")


class SweepIdentityTests(unittest.TestCase):
    """Defect #24: the checker swept both names of one weights file.

    `gemma4:12b` (a CAS blob whose filename carries the content hash) and `gemma4-12b` (the same 7.38 GB
    under a store path, hash in `sha256`) are ONE model. The sweep walked both, so the twin nobody
    measured kept a stale CPU-era rate (6.8 tok/s, no `gpu_backend`) beside the record measured at 17.0
    on the CUDA build - and the operator reads both labels in the choice box.
    """

    HASH = "1278394b" + "0" * 56

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lygo_sweep_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = self.root / "gemma4-12b.gguf"
        self.store.write_bytes(b"g" * 1024)
        self.blob = self.root / f"sha256-{self.HASH}"
        self.blob.write_bytes(b"g" * 1024)
        (self.root / "registry.json").write_text(json.dumps({
            "selected": "gemma4-12b",
            "models": [
                {"id": "gemma4:12b", "path": str(self.blob), "bytes": 1024, "source": "legacy_cas"},
                {"id": "gemma4-12b", "path": str(self.store), "bytes": 1024, "source": "gguf",
                 "sha256": self.HASH},
            ],
        }), encoding="utf-8")

    def test_one_weights_file_is_swept_once(self):
        seen: list = []

        def fake_check(rec, **kw):
            seen.append(rec.get("id"))
            return {"id": rec.get("id"), "verdict": "runs", "caps": ["text"], "path": rec.get("path")}

        with patch.object(model_check, "SAVE", self.root), \
                patch.object(model_check, "check_one", side_effect=fake_check), \
                patch.object(model_check, "_save_result", lambda r: None), \
                patch("engine.runner_for", return_value=None):
            out = model_check.run()
        self.assertEqual(len(seen), 1, f"one file, one record - the sweep visited {seen}")
        self.assertEqual(seen[0], "gemma4-12b", "the operator's own pick keeps its name")
        self.assertEqual(out.get("checked"), 1)
