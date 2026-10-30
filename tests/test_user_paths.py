"""The operator's own folders, and receipts that have to be earned.

A live test asked the console to "create a note on the desktop"; it answered with a receipt for
workspace/desktop_note.txt and no file existed anywhere. These pin both halves: the folders are real
and reachable, and a file claim has to come back with a path that is actually on disk.
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import limbs  # noqa: E402
import tools  # noqa: E402
import user_paths  # noqa: E402






NOTES_DIR = Path(__file__).resolve().parents[1] / "workspace" / "notes"

DESKTOP_DIR = Path(r"<DESKTOP>")



class FoldersAreReal(unittest.TestCase):
    def test_the_operator_folders_resolve_to_directories_that_exist(self):
        found = user_paths.folder_map()
        self.assertTrue(found, "no user folder resolved at all")
        for name, path in found.items():
            self.assertTrue(Path(path).is_dir(), f"{name} resolved to {path}, which is not a directory")

    def test_what_an_operator_says_is_understood(self):
        for phrase, key in (("desktop", "desktop"), ("my documents", "documents"),
                            ("Downloads", "downloads"), ("home", "home")):
            got = user_paths.resolve(phrase)
            self.assertIsNotNone(got, f"{phrase!r} did not resolve")
            self.assertEqual(str(got), user_paths.folder_map().get(key), f"{phrase!r} resolved to the wrong place")

    def test_an_absolute_folder_is_taken_as_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(user_paths.resolve(tmp), Path(tmp))

    def test_a_place_that_does_not_exist_is_not_invented(self):
        self.assertIsNone(user_paths.resolve("the beach"))
        self.assertIsNone(user_paths.resolve(""))


class ReceiptsAreEarned(unittest.TestCase):
    def test_a_saved_note_comes_back_with_a_path_that_is_really_there(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = limbs.extra("save_note", {"name": "note.txt", "text": "hello from the console",
                                            "where": tmp, "consent": True})
            self.assertTrue(got.get("ok"), got)
            dest = Path(got["path"])
            self.assertTrue(dest.is_file(), "the limb reported a file that is not on disk")
            self.assertTrue(dest.is_absolute(), "a receipt must be an absolute path")
            on_disk = dest.read_bytes()
            self.assertEqual(on_disk, b"hello from the console")
            self.assertEqual(got["bytes"], len(on_disk))
            self.assertEqual(got["sha256_12"], hashlib.sha256(on_disk).hexdigest()[:12])
            self.assertTrue(got.get("verified"))

    def test_the_receipt_names_the_folder_it_really_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = limbs.extra("save_note", {"name": "deep/note.md", "text": "x", "where": tmp, "consent": True})
            self.assertTrue(got.get("ok"), got)
            self.assertEqual(Path(got["path"]).parent, Path(tmp).resolve())

    def test_writing_outside_the_workspace_needs_consent(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = limbs.extra("save_note", {"name": "nope.txt", "text": "x", "where": tmp})
            self.assertFalse(got.get("ok"))
            self.assertEqual(got.get("error"), "consent_required")
            self.assertFalse((Path(tmp) / "nope.txt").exists(), "it wrote without consent")

    def test_an_unknown_place_is_refused_with_the_real_map(self):
        got = limbs.extra("save_note", {"name": "x.txt", "text": "x", "where": "the beach", "consent": True})
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("error"), "unknown_place")
        self.assertIn("desktop", got.get("known") or {}, "the refusal must hand back the folders it does know")

    def test_the_workspace_default_needs_no_consent(self):
        got = limbs.extra("save_note", {"name": "scratch.txt", "text": "x"})
        self.assertTrue(got.get("ok"), got)
        self.assertIn("workspace", got["path"].lower().replace("\\", "/"))


class ItsVisibleToTheOnBoxBrain(unittest.TestCase):
    def test_the_limb_is_advertised_and_the_budget_holds(self):
        names = [t["function"]["name"] for t in tools.core_schema()]
        self.assertIn("save_note", names)
        self.assertLessEqual(len(names), 44)
        self.assertLess(len(str(tools.core_schema())), 26980)


class AClaimWithoutEvidenceDoesNotStand(unittest.TestCase):
    """Live 2026-09-21: a receipt for <USER>n\\Desktop\\test_note.txt, no file, no limb called."""

    def test_the_operators_own_sentence_is_recognised_as_a_file_request(self):
        import chat_loop

        self.assertTrue(chat_loop.FILE_HINT.search(
            "you have access to this PC - I need you to test your environment to see if you can make a "
            "file and save it.. Create a note on the desktop and save it"))
        self.assertTrue(chat_loop.FILE_HINT.search("save a note to my documents"))
        self.assertFalse(chat_loop.FILE_HINT.search("what is 17 times 23"))

    def test_a_claim_of_a_file_with_no_writer_is_corrected(self):
        import chat_loop

        fake = "I have created a note on the desktop.\\nReceipt: <USER>n\\Desktop\\test_note.txt"
        out = chat_loop.sanitize_assistant(fake, [])
        self.assertIn("[host check]", out)
        self.assertIn("Nothing was created", out)
        self.assertIn("test_note.txt", out, "the correction must name the path it could not find")

    def test_a_claim_with_nothing_backing_it_is_corrected(self):
        import chat_loop

        out = chat_loop.sanitize_assistant("I have saved the file for you.", [])
        self.assertIn("[host check]", out)
        self.assertIn("nothing was created", out.lower())

    def test_a_real_write_is_left_alone(self):
        import chat_loop

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "real.txt"
            dest.write_text("hi", encoding="utf-8")
            traces = [{"name": "save_note", "result": {"ok": True, "path": str(dest), "verified": True}}]
            text = f"I created the file at {dest}"
            self.assertEqual(chat_loop.sanitize_assistant(text, traces), text)

    def test_a_claim_naming_a_file_that_really_exists_is_confirmed(self):
        import chat_loop

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "there.txt"
            dest.write_text("hi", encoding="utf-8")
            out = chat_loop.sanitize_assistant(f"I have saved the note to {dest}", [])
            self.assertIn("really is on disk", out)
            self.assertIn(str(dest), out)

    def test_an_ordinary_answer_is_never_touched(self):
        import chat_loop

        self.assertEqual(chat_loop.sanitize_assistant("Seventeen times twenty three is 391.", []),
                         "Seventeen times twenty three is 391.")


    def test_backtick_junk_is_not_reported_as_a_path(self):
        import chat_loop

        out = chat_loop.sanitize_assistant("I have saved the note. Path: `t:**` and it is done.", [])
        self.assertIn("[host check]", out)
        tail = out.split("[host check]")[1] if "[host check]" in out else ""
        self.assertNotIn("t:**", tail)


class TheConsoleFinishesTheJob(unittest.TestCase):
    """The last rung: if the model will not call the limb, the console writes the file and says so."""

    def test_it_writes_the_file_when_no_limb_did(self):
        import chat_loop

        dest = NOTES_DIR / "ladder_t1.txt"
        if dest.exists():
            dest.unlink()
        reply, got = chat_loop.console_completes_the_write(
            "create a file called ladder_t1.txt in your workspace notes and save it",
            [], "I have created the file for you.")
        self.assertIsNotNone(got, "the console did not step in")
        self.assertTrue(Path(got["path"]).is_file(), "it reported a write it did not do")
        self.assertIn("[console]", reply)
        self.assertIn(got["path"], reply)
        self.assertIn("ladder_t1.txt", str(got["path"]))

    def test_the_written_note_is_honest_about_who_wrote_it(self):
        import chat_loop

        dest = NOTES_DIR / "ladder_t2.txt"
        if dest.exists():
            dest.unlink()
        _reply, got = chat_loop.console_completes_the_write(
            "save a note called ladder_t2.txt in the workspace", [], "done")
        body = Path(got["path"]).read_text(encoding="utf-8")
        self.assertIn("Written by the console", body)
        self.assertIn("did not call the writing limb", body)

    def test_a_real_limb_write_is_left_alone(self):
        import chat_loop

        traces = [{"name": "save_note", "result": {"ok": True, "path": "C:/x/y.txt", "verified": True}}]
        reply, got = chat_loop.console_completes_the_write(
            "create a file called y.txt on my desktop", traces, "I created it.")
        self.assertIsNone(got)
        self.assertEqual(reply, "I created it.")

    def test_an_ordinary_question_is_never_touched(self):
        import chat_loop

        reply, got = chat_loop.console_completes_the_write("what is 17 times 23?", [], "391")
        self.assertIsNone(got)
        self.assertEqual(reply, "391")

    def test_a_desktop_ask_resolves_to_the_real_desktop(self):
        import chat_loop

        dest = DESKTOP_DIR / "ladder_t5.txt"
        if dest.exists():
            dest.unlink()
        _reply, got = chat_loop.console_completes_the_write(
            "create a note called ladder_t5.txt on my desktop", [], "I have created it.")
        try:
            self.assertIsNotNone(got)
            self.assertEqual(Path(got["path"]).parent, DESKTOP_DIR)
            self.assertTrue(Path(got["path"]).is_file())
        finally:
            if dest.exists():
                dest.unlink()


class ThePortalShowsWhatAgentsAreDoing(unittest.TestCase):
    def test_portal_status_speaks_for_its_owners(self):
        got = limbs.extra("portal_status", {})
        self.assertTrue(got.get("ok"), got)
        self.assertIn("summary", got)
        self.assertIn("tasks", got)
        self.assertIn("keeper", got)
        self.assertIn("portal_status", [t["function"]["name"] for t in tools.core_schema()])


class TheHostWritesWhatTheOperatorAlreadySpecified(unittest.TestCase):
    """Gauntlet T4/T5/T7: the brain described files it never wrote. A request that states the
    name and the text needs no judgement, so the console runs it - as it already does for calc."""

    def test_the_sentence_is_understood(self):
        import chat_loop

        spec = chat_loop.host_write_request("create a file called host_t1.txt in your workspace "
                                            "notes with the exact text HOST-OK")
        self.assertEqual(spec["name"], "host_t1.txt")
        self.assertEqual(spec["text"], "HOST-OK")
        desk = chat_loop.host_write_request("create a note called host_t5.txt on my desktop with the text DESK-OK")
        self.assertEqual((desk["where"], desk["consent"]), ("desktop", True))
        self.assertIsNone(chat_loop.host_write_request("what is 17 times 23"))

    def test_the_prefetch_actually_writes_it(self):
        import chat_loop

        dest = NOTES_DIR / "host_t3.txt"
        if dest.exists():
            dest.unlink()
        traces = chat_loop.host_prefetch("create a file called host_t3.txt in your workspace notes "
                                         "with the exact text HOST-OK")
        names = [t["name"] for t in traces]
        self.assertIn("save_note", names, f"the host did not run the write: {names}")
        self.assertTrue(dest.is_file(), "no file on disk")
        self.assertEqual(dest.read_text(encoding="utf-8"), "HOST-OK")
        got = [t for t in traces if t["name"] == "save_note"][0]["result"]
        self.assertTrue(got.get("verified"), got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
