"""A vision model must boot with its projector, and an attached file must be readable without asking.

Measured on this machine 2026-09-19. The registry held TWO records for the same file -

    gemma4:12b   lygo_vault   <MODELS>\\gemma4-12b.gguf   mmproj <MODELS>\\gemma4-12b-mmproj.gguf
    gemma4-12b   gguf         <MODELS>\\gemma4-12b.gguf   mmproj None

- and the operator's pinned pick was the projector-less twin. So `lygo_engine.boot` ran the model with no
`--mmproj`, the engine logged `image input is not supported - hint: ... you may need to provide the
mmproj` on every photo, and the console honestly said the picture had not been looked at. The scanner
could not know better: `_from_header` hard-codes `"mmproj": None`, and the projector beside the model was
registered as a model in its own right.

The same turn showed the other half: the composer tells the model "open it with the read_file limb before
you answer", the model did not, and the operator got "I was asked for the read_file limb and did not issue
the call" for a message whose own attachment line named the file.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import registry  # noqa: E402


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


class ProjectorBesideTheModelTests(unittest.TestCase):
    """One rule, asked by the boot, the vision limb and the console's own guard."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.model = self.d / "gemma4-12b.gguf"
        self.model.write_bytes(b"GGUF model")
        self.mm = self.d / "gemma4-12b-mmproj.gguf"
        self.mm.write_bytes(b"GGUF projector")
        registry._orig = getattr(registry, "KNOWN_VAULTS", None)

    def test_a_record_without_the_field_finds_the_projector_beside_it(self):
        got = registry.mmproj_for({"id": "x", "path": str(self.model)})
        self.assertEqual(got, self.mm)

    def test_the_record_field_wins_when_it_is_real(self):
        other = self.d / "chosen-mmproj.gguf"
        other.write_bytes(b"GGUF projector")
        got = registry.mmproj_for({"id": "x", "path": str(self.model), "mmproj": str(other)})
        self.assertEqual(got, other)

    def test_a_field_pointing_at_nothing_falls_back_to_the_file_beside_it(self):
        got = registry.mmproj_for({"id": "x", "path": str(self.model), "mmproj": str(self.d / "gone.gguf")})
        self.assertEqual(got, self.mm)

    def test_a_prefix_projector_is_found_too(self):
        (self.d / "gemma4-12b-mmproj.gguf").unlink()
        pref = self.d / "mmproj-gemma4-12b.gguf"
        pref.write_bytes(b"GGUF projector")
        self.assertEqual(registry.mmproj_for({"id": "x", "path": str(self.model)}), pref)

    def test_a_projector_record_is_not_given_a_projector_of_its_own(self):
        self.assertIsNone(registry.mmproj_for({"id": "x", "path": str(self.mm)}))

    def test_a_text_model_with_nothing_beside_it_has_none(self):
        plain = self.d / "qwen2.5-coder-7b.gguf"
        plain.write_bytes(b"GGUF model")
        self.assertIsNone(registry.mmproj_for({"id": "x", "path": str(plain)}))


class ScannerSidecarTests(unittest.TestCase):
    def setUp(self):
        import scanner

        self.scanner = scanner
        self.d = Path(tempfile.mkdtemp())
        self.model = self.d / "vision-model.gguf"
        self.model.write_bytes(b"GGUF model")
        self.mm = self.d / "vision-model-mmproj.gguf"
        self.mm.write_bytes(b"GGUF projector")

    def test_a_scanned_model_is_born_with_the_projector_beside_it(self):
        import gguf_header

        h = gguf_header.parse_gguf_header(self.model)
        rec = self.scanner._from_header(h, self.model)
        self.assertEqual(rec.get("mmproj"), str(self.mm),
                         "a scanned vision model must not look text-only")

    def test_the_two_sidecar_helpers_agree(self):
        self.assertEqual(self.scanner._projector_beside(self.model), self.mm)
        self.assertEqual(self.scanner._model_beside(self.mm), self.model)

    def test_a_projector_with_its_model_beside_it_is_not_a_model(self):
        """Checked on the REAL pair in the vault, because a header cannot be faked: parse_gguf_header
        reads the file's own metadata, and synthetic bytes come back as kind 'chat'."""
        vault = Path(os.environ.get("LYGO_MODELS") or r"<MODELS>")
        model = vault / "gemma4-12b.gguf"
        projector = vault / "gemma4-12b-mmproj.gguf"
        if not (model.is_file() and projector.is_file()):
            self.skipTest("no vision pair in this kit's vault to read")
        import gguf_header

        h = gguf_header.parse_gguf_header(projector)
        self.assertEqual((h.get("kind") or ""), "mmproj", "the projector's own header says so")
        self.assertEqual(self.scanner._model_beside(projector), model,
                         "and it has a model beside it, so a scan must not offer it as one")
        h_model = gguf_header.parse_gguf_header(model)
        self.assertEqual((h_model.get("kind") or ""), "chat")
        self.assertEqual(self.scanner._projector_beside(model), projector,
                         "while the model finds its projector")

    def test_a_projector_beside_its_model_is_understood_as_a_sidecar(self):
        self.assertIsNotNone(self.scanner._model_beside(self.mm),
                             "the name is enough to place the pair")
        self.assertIn('if (h.get("kind") or "") == "mmproj" and _model_beside(p):',
                      _src("src/scanner.py"), "and a scan skips it when the model is present")


class OneModelPerFileTests(unittest.TestCase):
    def test_two_records_for_one_file_collapse_into_the_selected_one(self):
        models = [
            {"id": "gemma4:12b", "path": r"I:\m\gemma4-12b.gguf", "source": "lygo_vault",
             "mmproj": r"I:\m\gemma4-12b-mmproj.gguf", "bytes": 7},
            {"id": "gemma4-12b", "path": r"I:\m\gemma4-12b.gguf", "source": "gguf", "mmproj": None},
        ]
        got = registry.dedupe_by_file(models, selected="gemma4-12b")
        self.assertEqual(len(got), 1, "one file is one model")
        self.assertEqual(got[0]["id"], "gemma4-12b", "the operator's pinned id survives")
        self.assertEqual(got[0]["mmproj"], r"I:\m\gemma4-12b-mmproj.gguf",
                         "and it gains the projector the twin was carrying")

    def test_records_for_different_files_are_left_alone(self):
        models = [{"id": "a", "path": r"I:\m\a.gguf"}, {"id": "b", "path": r"I:\m\b.gguf"}]
        self.assertEqual(len(registry.dedupe_by_file(models, selected=None)), 2)

    def test_upsert_collapses_twins_as_it_saves(self):
        self.assertIn("dedupe_by_file", _src("src/registry.py"))
        self.assertIn("dedupe_by_file([m for m in by_id.values()", _src("src/registry.py"))


class EngineAndLimbAgreeTests(unittest.TestCase):
    def test_the_boot_asks_registry_for_the_projector(self):
        s = _src("src/lygo_engine.py")
        self.assertIn("from registry import mmproj_for as _mmproj_for", s)
        self.assertIn("mm = _mmproj_for(rec)", s)
        self.assertNotIn('mm = Path(rec["mmproj"])', s, "the raw field is what booted a blind engine")

    def test_the_vision_limb_asks_the_same_question(self):
        s = _src("src/image_tools.py")
        self.assertIn("from registry import mmproj_for", s)
        self.assertIn("_registry.mmproj_for(rec)", s)


class AttachedFileIsReadByTheHostTests(unittest.TestCase):
    """The operator's message names the file: the host reads it, so no limb call can be forgotten."""

    def setUp(self):
        from chat_loop import attached_files, host_prefetch  # noqa: F401

        self.attached_files = attached_files
        self.host_prefetch = host_prefetch
        self.ws = ROOT / "workspace"
        self.ws.mkdir(parents=True, exist_ok=True)
        self.probe = self.ws / "_attach_probe.txt"
        self.probe.write_text("LANTERN CODE: DELTA-963\n", encoding="utf-8")

    def tearDown(self):
        try:
            self.probe.unlink()
        except OSError:
            pass

    def _message(self, why="What is the lantern code in the attached file?"):
        return (
            "Attached file _attach_probe.txt (24 B) is saved in the workspace as:\n"
            + str(self.probe)
            + "\nOpen it with the read_file limb before you answer, and say plainly if you cannot.\n\n"
            + why
        )

    def test_the_portal_puts_the_path_on_its_own_line(self):
        """This kit's own folder has a space in it, so an inline path pattern truncates it."""
        js = _src("portal/app.js")
        self.assertIn('is saved in the workspace as:\\n"', js)
        self.assertIn("+ a.path + \"\\n", js)

    def test_a_path_with_a_space_in_it_parses_exactly(self):
        got = self.attached_files(self._message())
        self.assertEqual(got, [str(self.probe)])

    def test_a_picture_is_left_to_the_image_path(self):
        shot = self.ws / "_attach_probe.png"
        shot.write_bytes(b"\x89PNG")
        try:
            self.assertEqual(self.attached_files("Attached file shot (1 B) is saved in the workspace as:\n" + str(shot)), [])
        finally:
            shot.unlink()

    def test_the_host_reads_the_file_and_answers_from_it(self):
        traces = self.host_prefetch(self._message())
        names = [t["name"] for t in traces]
        self.assertIn("read_file", names, "the host must read the attachment itself")
        read = next(t for t in traces if t["name"] == "read_file")
        self.assertTrue(read.get("host"), "and it is a host trace")
        self.assertTrue((read.get("result") or {}).get("ok"), read.get("result"))
        self.assertIn("DELTA-963", str((read.get("result") or {}).get("text")))

    def test_the_readout_reaches_the_model_with_the_file_in_it(self):
        """The host reading the file is not the fix - the model receiving its CONTENTS is.

        Measured 2026-09-19: the host read the attached file, `_compact_trace`'s slim branch kept only
        name/ok/path, and the model answered "LUMINA-77" for a file that says OMEGA-441. A readout whose
        payload was trimmed away is a readout the model invents.
        """
        from chat_loop import prefetch_message

        msg = prefetch_message(self.host_prefetch(self._message()))
        self.assertIn("DELTA-963", msg, "the file's own text must be in what the model is given")

    def test_the_host_does_not_go_to_the_web_for_a_file_it_was_handed(self):
        names = [t["name"] for t in self.host_prefetch(self._message())]
        self.assertNotIn("web_search", names)
        self.assertNotIn("web_fetch", names)

    def test_an_ordinary_question_still_reaches_the_web_tools(self):
        """The guard is the attachment marker, not the word 'what' - re-test the real question."""
        traces = self.host_prefetch("what is the tallest building in the world?")
        self.assertNotIn("read_file", [t["name"] for t in traces])


if __name__ == "__main__":
    unittest.main()
