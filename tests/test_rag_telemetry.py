"""The RAG has to be legible while it is working, or the operator cannot leave it alone.

The console injects recalled history into a turn on its own. When that happens invisibly, the only
signal the operator gets is a window percentage that looks like a fault - the conversation is eleven
times the budget, and nothing says the recall is why the turn still works. These tests pin the record
that makes the behaviour visible, and the rule that keeping it can never cost a turn.
"""

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
SRC = HERE.parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import transcript_archive as archive  # noqa: E402
import lygo_rag as rag  # noqa: E402
import compaction  # noqa: E402


class RecallIsRecordedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(archive.flush, 3)
        self._root = archive.ROOT
        archive.ROOT = Path(self.tmp.name) / "memory" / "conversations"
        self.addCleanup(lambda: setattr(archive, "ROOT", self._root))
        archive.reset_for_tests()
        self._save = compaction.SAVE
        compaction.SAVE = Path(self.tmp.name) / "save"
        self.addCleanup(lambda: setattr(compaction, "SAVE", self._save))

    def _file(self, q, a):
        archive.sync([{"role": "user", "content": q}, {"role": "assistant", "content": a}],
                     meta={"model": "qwen2.5-coder:7b"})
        archive.flush(5)

    def test_a_recall_leaves_a_record_of_what_it_handed_over(self):
        self._file("what does the seal button do", "the seal button closes the session and indexes the archive")
        rag.build(force=True)

        note = rag.recall("what does the seal button do", budget_chars=800)
        self.assertTrue(note, "precondition: this query does match the filed history")

        rec = rag.status().get("recall")
        self.assertIsInstance(rec, dict, "the RAG reports no record of its own recalls")
        self.assertEqual(rec.get("runs"), 1, "the recall that just happened was not counted")
        self.assertGreater(rec.get("chars") or 0, 0, "the record does not say how much was injected")
        self.assertEqual(rec.get("chars"), len(note), "the recorded size is not the size handed over")
        self.assertTrue(rec.get("last_at"), "the record has no timestamp")
        self.assertGreaterEqual(rec.get("hits") or 0, 1)
        self.assertIn("seal", str(rec.get("terms") or ""), "the record does not say what was asked")

    def test_a_recall_that_found_nothing_is_recorded_as_empty_not_as_a_success(self):
        self._file("question about the archive", "the archive keeps the record")
        rag.build(force=True)

        self.assertEqual(rag.recall("zebra unicycle thermodynamics", budget_chars=800), "",
                         "precondition: nothing in the history bears on that query")
        rec = rag.status()["recall"]
        self.assertEqual(rec.get("runs"), 1)
        self.assertEqual(rec.get("empty"), 1, "an empty recall was not recorded as empty")
        self.assertEqual(rec.get("chars"), 0)

    def test_recording_can_never_be_why_a_turn_loses_its_recall(self):
        """Like every other part of compaction: fail open, never fail the turn."""
        self._file("what does the seal button do", "the seal button closes the session and indexes the archive")
        rag.build(force=True)
        original = rag._record_recall
        self.addCleanup(lambda: setattr(rag, "_record_recall", original))

        def explode(*a, **k):
            raise RuntimeError("disk full")

        rag._record_recall = explode
        note = rag.recall("what does the seal button do", budget_chars=800)
        self.assertIn("seal", note.lower(), "a failed record cost the turn its recall")

    def test_the_console_status_carries_the_recall_record(self):
        self._file("what does the seal button do", "the seal button closes the session and indexes the archive")
        rag.build(force=True)
        rag.recall("what does the seal button do", budget_chars=800)

        st = compaction.status(ctx=32768, messages=[{"role": "user", "content": "hello"}])
        self.assertIn("recall", st, "the console's own status does not mention the RAG")
        self.assertEqual(st["recall"].get("runs"), 1)
        self.assertIn("window", st, "precondition: the window block is still published")


if __name__ == "__main__":
    unittest.main(verbosity=2)
