"""The LYGO RAG: recall over the console's own history, so the window stops being the limit."""

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


class RecallOverFiledHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(archive.flush, 3)
        self._root = archive.ROOT
        archive.ROOT = Path(self.tmp.name) / "memory" / "conversations"
        self.addCleanup(lambda: setattr(archive, "ROOT", self._root))
        archive.reset_for_tests()

    def _file(self, q, a, first=None):
        msgs = [{"role": "user", "content": first or q}, {"role": "assistant", "content": a}]
        archive.sync(msgs, meta={"model": "qwen2.5-coder:7b"})
        archive.flush(5)
        return msgs

    def test_it_indexes_what_the_archive_filed(self):
        self._file("how do I rebuild the engine?", "Run scripts/fetch_engine.ps1 and wait.")
        st = rag.build(force=True)
        self.assertEqual(st["conversations"], 1)
        self.assertEqual(st["blocks"], 2, "a filed turn is two blocks: the sent text and the answer")
        self.assertTrue(rag.index_path().is_file(), "no index file was written")

    def test_it_finds_the_conversation_that_holds_the_answer(self):
        self._file("what colour is the dock?", "The dock is charcoal.")
        self._file("how do I rotate the gemini key?", "Use the console's admin pane, key ring section.")
        self._file("what about the weather in Tokyo?", "Rain, probably.")
        rag.build(force=True)
        hits = rag.query("gemini key rotation", k=3)
        self.assertTrue(hits, "nothing recalled for a phrase the history contains")
        self.assertIn("gemini", hits[0]["text"].lower())
        # the passage found is the turn; recall hands over both halves of it, so the answer to the
        # question that matched comes with it
        got = rag.recall("gemini key rotation", budget_chars=1200)
        self.assertIn("admin pane", got, "recall returned the question without its answer")

    def test_ranking_prefers_the_block_that_matches_more_of_the_question(self):
        self._file("archive dock padding", "the dock is fixed now")
        self._file("unrelated chatter", "nothing to see")
        rag.build(force=True)
        hits = rag.query("archive dock padding")
        self.assertIn("dock", hits[0]["text"].lower(), "the best-matching conversation was not ranked first")
        self.assertIn("fixed", rag.recall("archive dock padding", budget_chars=800),
                      "recall did not bring back what was said about the dock")

    def test_recall_is_labelled_sized_to_budget(self):
        for i in range(12):
            self._file(f"question {i} about the archive and the engine",
                       "answer " + ("long detail " * 200))
        rag.build(force=True)
        got = rag.recall("what did we say about the archive", budget_chars=1200)
        self.assertTrue(got)
        self.assertLessEqual(len(got), 1200 + 60, "recall ignored its budget")
        self.assertIn("LYGO-RAG", got, "recall is not labelled")
        self.assertIn(".md", got, "a recalled passage must name the file it came from")

    def test_recall_shows_who_spoke_and_when(self):
        self._file("stamp check", "the answer to the stamp check")
        rag.build(force=True)
        got = rag.recall("stamp check", budget_chars=800)
        self.assertIn("STEWARD", got.upper())
        self.assertIn("complete by LYGO", got)

    def test_an_empty_history_is_not_an_error(self):
        rag.build(force=True)
        self.assertEqual(rag.query("anything at all"), [])
        self.assertEqual(rag.recall("anything at all"), "")
        self.assertEqual(rag.stats()["blocks"], 0)

    def test_building_twice_does_not_duplicate_and_picks_up_new_messages(self):
        self._file("first question", "first answer")
        rag.build(force=True)
        self.assertEqual(rag.stats()["blocks"], 2)
        rag.build(force=True)
        self.assertEqual(rag.stats()["blocks"], 2, "a rebuild duplicated blocks")
        self._file("second question", "second answer")
        self.assertEqual(rag.stats()["blocks"], 4, "a new message was not indexed")
        self.assertTrue(rag.query("second answer"), "the new message is not findable")


if __name__ == "__main__":
    unittest.main()
