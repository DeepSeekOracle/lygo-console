from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from chat_loop import host_prefetch  # noqa: E402
from continuity import compose_system  # noqa: E402
import notepad as notepad_mod  # noqa: E402
from notepad import delete_note, list_notes, read_note, write_note  # noqa: E402
from tools import dispatch  # noqa: E402


class NotepadTests(unittest.TestCase):
    def setUp(self) -> None:
        """Notes go to a throwaway notepad, never the operator's live save/notepad/.

        This test wrote real notes into the live index and deleted them again: two concurrent runs
        interleaved on one index.json, and a run that died mid-test left the steward a
        "unit-test-note" (defect D30).
        """
        self._td = tempfile.TemporaryDirectory(prefix="lygo_np_")
        self.addCleanup(self._td.cleanup)
        root = Path(self._td.name) / "notepad"
        for const, value in (("NOTEPAD_ROOT", root), ("NOTES_DIR", root / "notes"), ("INDEX_PATH", root / "index.json")):
            patcher = patch.object(notepad_mod, const, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        notepad_mod.ensure()   # seeds the scratch note the assertions expect

    def test_write_read_list(self):
        w = write_note("unit-test-note", "Unit", "paste me later")
        self.assertTrue(w.get("ok"))
        r = read_note("unit-test-note")
        self.assertTrue(r.get("ok"))
        self.assertIn("paste me later", r.get("text") or "")
        listed = list_notes()
        ids = [n["id"] for n in listed.get("notes") or []]
        self.assertIn("unit-test-note", ids)
        self.assertIn("scratch", ids)
        d = delete_note("unit-test-note")
        self.assertTrue(d.get("ok"))
        missing = read_note("unit-test-note")
        self.assertFalse(missing.get("ok"))

    def test_bad_id(self):
        r = write_note("../etc", "x", "nope")
        self.assertFalse(r.get("ok"))

    def test_tools_opt_in(self):
        write_note("tool-note", "Tool", "secret-draft-xyz")
        listed = dispatch("notepad_list", {})
        self.assertTrue(listed.get("ok"))
        got = dispatch("notepad_read", {"id": "tool-note"})
        self.assertIn("secret-draft-xyz", got.get("text") or "")
        sys_txt = compose_system()
        self.assertIn("notepad", sys_txt.lower())
        self.assertNotIn("secret-draft-xyz", sys_txt)
        traces = host_prefetch("please look at my notes")
        self.assertIn("notepad_list", [t.get("name") for t in traces])
        silent = host_prefetch("what time is it in Tokyo")
        self.assertNotIn("notepad_list", [t.get("name") for t in silent])
        delete_note("tool-note")

    def test_portal_has_notepad(self):
        html = (ROOT / "portal" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "portal" / "style.css").read_text(encoding="utf-8")
        js = (ROOT / "portal" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="notepad"', html)
        self.assertIn("np-body", html)
        self.assertIn(".notepad", css)
        self.assertIn("npSave", js)
        self.assertNotIn("let history", js)


if __name__ == "__main__":
    unittest.main()
