"""Retrieval quality: recall has to be worth trusting when nobody is watching it.

The console injects these passages into a turn on its own, so a bad ranking is not a cosmetic problem -
it spends the window on noise, and it does so exactly when the conversation is long enough that the
operator cannot check it. These tests pin the two ways that went wrong in live use:

* near-duplicate blocks filling the top-k (three copies of one message, nothing else)
* a common word in the query carrying a block that shares nothing else with it
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

_FILLER = ("the console runs the engine and files the archive on this machine, "
           "the console keeps the record and the engine answers the turn")


class RetrievalQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(archive.flush, 3)
        self._root = archive.ROOT
        archive.ROOT = Path(self.tmp.name) / "memory" / "conversations"
        self.addCleanup(lambda: setattr(archive, "ROOT", self._root))
        archive.reset_for_tests()

    def _file(self, q, a):
        archive.sync([{"role": "user", "content": q}, {"role": "assistant", "content": a}],
                     meta={"model": "qwen2.5-coder:7b"})
        archive.flush(5)

    def test_a_distinctive_term_outranks_a_word_that_is_everywhere(self):
        """'console' is in every block; the block that is actually about the doorbell must win."""
        for i in range(30):
            self._file(f"question {i} about the console", f"the console engine and the archive, turn {i} " + _FILLER)
        self._file("how does the doorbell ring work", "the doorbell listens on a loopback port one below the console")
        rag.build(force=True)

        hits = rag.query("doorbell loopback port", k=3)
        self.assertTrue(hits, "nothing came back for a phrase the filed history contains")
        self.assertIn("doorbell", hits[0]["text"].lower(),
                      f"the doorbell passage was not ranked first: {hits[0]['text'][:120]!r}")
        self.assertNotIn("question 7 about the console", hits[0]["text"],
                         "a block sharing only the common word outranked the distinctive one")

    def test_the_top_k_is_not_filled_with_near_duplicates(self):
        """Three copies of one message must not take all three slots."""
        for i in range(3):
            self._file("the picture attached was not looked at", "the picture attached was not looked at, no projector here")
        self._file("doorbell loopback ring token", "the doorbell rings the launcher with a token read from disk")
        rag.build(force=True)

        hits = rag.query("the picture attached was not looked at", k=3)
        self.assertGreaterEqual(len(hits), 2)
        texts = [h["text"].strip() for h in hits]
        self.assertEqual(len(texts), len(set(texts)),
                         f"the same passage was handed over more than once: {texts}")

    def test_a_real_hit_never_sorts_below_a_repeated_one_it_does_not_match(self):
        """Dedup must not drop the genuinely relevant block to make room for copies."""
        for i in range(5):
            self._file("engine fault report", "engine fault report, nothing new, the engine fault is old")
        self._file("rotate the gemini key", "open the admin pane and use the key ring to rotate the gemini key")
        rag.build(force=True)

        hits = rag.query("rotate the gemini key", k=3)
        self.assertIn("gemini", hits[0]["text"].lower(),
                      f"the passage that answers the question was buried: {[h['text'][:60] for h in hits]}")
        self.assertNotIn("gemini", hits[0]["text"].lower().replace("gemini", "", 1) + "engine fault",
                         "the filler outranked the answer")

    def test_recall_says_so_when_the_history_holds_nothing_relevant(self):
        """An empty answer is honest; a weak one spends the window on noise."""
        for i in range(8):
            self._file(f"question {i} about the archive", "the archive keeps the record, " + _FILLER)
        rag.build(force=True)

        self.assertEqual(rag.query("zebra unicycle thermodynamics", k=3), [],
                         "the index claimed to find a passage for words it does not hold")
        self.assertEqual(rag.recall("zebra unicycle thermodynamics", budget_chars=800), "",
                         "recall handed the turn a passage with no bearing on it")

    def test_a_filed_picture_is_never_handed_over_as_base64_text(self):
        """The archive files a turn verbatim, including an attached picture's data URL.

        Recall then puts passages into a live turn as TEXT. Handing over the bytes would paste tens of
        thousands of characters of base64 into the prompt - the very thing `vision.fit_turn` exists to
        prevent - and would be counted as tokens by the engine. Caught live by
        tests/test_vision_budget.py::test_no_base64_is_pasted_into_the_prompt_as_text.
        """
        blob = "data:image/png;base64," + ("iVBORw0KGgoAAAANSUhEUg" * 200)
        archive.sync([{"role": "user", "content": [
            {"type": "text", "text": "what is in this picture?"},
            {"type": "image_url", "image_url": {"url": blob}},
        ]}], meta={"model": "gemma4-12b"})
        archive.flush(5)
        self._file("a plain turn about the seal button", "the seal button closes the session")
        rag.build(force=True)

        hits = rag.query("what is in this picture", k=3)
        self.assertTrue(hits, "precondition: the filed turn is searchable")
        for h in hits:
            self.assertNotIn("base64,", h["text"].lower(),
                             "a recalled passage carried the picture's bytes as text")
        note = rag.recall("what is in this picture", budget_chars=1500)
        self.assertNotIn("base64,", note.lower(), "recall handed the turn raw image bytes")
        self.assertIn("image", note.lower(), "the picture was dropped without saying it was there")

    def test_a_strong_query_still_returns_its_passage(self):
        self._file("what does the seal button do", "the seal button closes the session and indexes the archive")
        rag.build(force=True)
        got = rag.recall("what does the seal button do", budget_chars=800)
        self.assertIn("seal", got.lower(), "a direct question about a filed turn came back empty")


if __name__ == "__main__":
    unittest.main(verbosity=2)
