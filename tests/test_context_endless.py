"""The conversation has to flow past the window, and the detail has to come back by itself.

What this pins, in the operator's terms:

* a conversation that outgrows the window keeps going - nothing is refused, nothing is lost;
* a detail from earlier (or from an earlier session) is handed to the turn when the ask needs it,
  whether or not this turn happened to drop anything;
* what is injected still fits the window, because a recall that overflows the engine is worse than
  no recall at all;
* the index keeps itself current - nobody rebuilds it by hand;
* no match means silence, not noise.

Δ9Φ963-LYGO-CONTEXT-ENDLESS-TESTS-v1
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
import compaction  # noqa: E402
import lygo_rag as rag  # noqa: E402

FACT = "the vault passphrase rotation is handled by tools/rotate_keys.py on the first of the month"
ASK = "remind me how we rotate the vault passphrase"
FILLER = ("the console folded the record again and the engine answered as it normally does on this host, " * 3)


class EndlessConversationTests(unittest.TestCase):
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
        # the console runs this on a 32K window; below the system reserve a budget is degenerate and
        # 'does it fit' stops being a meaningful question, so the fixture matches the host
        self.ctx = 32768

    def _file(self, q, a):
        archive.sync([{"role": "user", "content": q}, {"role": "assistant", "content": a}],
                     meta={"model": "qwen2.5-coder:7b"})
        archive.flush(5)

    def _long_session(self, turns: int = 60):
        """A conversation the window cannot hold, with the fact filed early and then pushed out.

        Grown until the real trim actually sheds something rather than assuming a size fits: guessing a
        turn count is how this fixture first passed for the wrong reason on a 4K window and then failed
        on the 32K one the console really runs.
        """
        msgs = [{"role": "user", "content": ASK}, {"role": "assistant", "content": FACT}]
        self._file(ASK, FACT)
        i = 0
        while True:
            msgs += [{"role": "user", "content": f"chatter {i}: {FILLER}"},
                     {"role": "assistant", "content": f"answer {i}: {FILLER}"}]
            self._file(f"chatter {i}: {FILLER}", f"answer {i}: {FILLER}")
            i += 1
            kept, shed, _info = compaction.trim_messages(msgs, self.ctx)
            live_text = " ".join(str(m.get("content")) for m in kept)
            if i >= turns and shed and FACT not in live_text:
                return msgs  # grown until the fact has genuinely left the window
            if i > 600:
                self.fail("the fixture never outgrew the window")

    def test_a_conversation_past_the_window_keeps_flowing_and_keeps_its_details(self):
        msgs = self._long_session(turns=100)
        kept, dropped, info = compaction.trim_messages(msgs, self.ctx)
        self.assertTrue(dropped, "precondition: this conversation does not fit the window")
        self.assertLess(len(kept), len(msgs), "precondition: the window shed nothing")
        self.assertNotIn(FACT, " ".join(str(m.get("content")) for m in kept),
                         "precondition: the fact is no longer in the live window")

        note = compaction.recall_for(ASK, messages=msgs, ctx=self.ctx)
        self.assertIn("rotate_keys.py", note,
                      f"the turn was not handed the detail it asked for (note {len(note)} chars)")

    def test_what_is_injected_still_fits_the_window(self):
        msgs = self._long_session(turns=100)
        kept, dropped, info = compaction.trim_messages(msgs, self.ctx)
        note = compaction.recall_for(ASK, messages=msgs, ctx=self.ctx)
        self.assertTrue(note)

        turn = kept + [{"role": "user", "content": ASK + "\n\n" + note}]
        budget = compaction.window_budget(self.ctx)
        self.assertLessEqual(
            compaction.history_tokens(turn), budget["history_tokens"],
            f"the recap pushed the turn past the history budget: {compaction.history_tokens(turn)} > "
            f"{budget['history_tokens']}")

    def test_a_detail_comes_back_even_when_this_turn_dropped_nothing(self):
        """The whole point of an archive: a short turn can still need something filed long ago."""
        self._file(ASK, FACT)
        for i in range(3):
            self._file(f"chatter {i}", f"answer {i}")
        rag.build(force=True)
        small = [{"role": "user", "content": f"chatter {i}"} for i in range(3)]
        kept, dropped, _ = compaction.trim_messages(small, self.ctx)
        self.assertFalse(dropped, "precondition: this turn fits the window with room to spare")

        note = compaction.recall_for(ASK, messages=small, ctx=self.ctx)
        self.assertIn("rotate_keys.py", note,
                      "a filed detail was not offered because this turn happened to fit the window")

    def test_no_match_means_silence(self):
        for i in range(5):
            self._file(f"chatter {i}: {FILLER}", f"answer {i}")
        msgs = [{"role": "user", "content": "what is the airspeed of an unladen swallow"}]
        self.assertEqual(compaction.recall_for("what is the airspeed of an unladen swallow",
                                              messages=msgs, ctx=self.ctx), "",
                         "the turn was handed a passage with no bearing on it")

    def test_a_courtesy_does_not_pull_a_recap_into_the_turn(self):
        """Measured on the live index: 'thanks' scored 6.23 and injected 1,378 chars of history.

        A score floor cannot tell a question from a courtesy - both clear it. The ask has to match the
        filed passage on more than one of its words before the window is spent.
        """
        self._file("thanks", "you are welcome")
        self._file(ASK, FACT)
        rag.build(force=True)
        for courtesy in ("thanks", "hello", "ok"):
            self.assertEqual(compaction.recall_for(courtesy, ctx=self.ctx), "",
                             f"{courtesy!r} pulled filed history into the turn")

    def test_a_real_question_still_gets_its_passage(self):
        self._file(ASK, FACT)
        rag.build(force=True)
        self.assertIn("rotate_keys.py", compaction.recall_for(ASK, ctx=self.ctx),
                      "the term test silenced a genuine question")

    def test_the_index_keeps_itself_current_without_being_asked(self):
        self._file("first turn about the engine", "the engine is fetched by scripts/fetch_engine.ps1")
        rag.build(force=True)
        self._file(ASK, FACT)  # filed after the index was built - nothing rebuilds it by hand
        hits = rag.query("rotate the vault passphrase")
        self.assertTrue(hits, "a newly filed turn was not searchable until somebody rebuilt the index")
        self.assertTrue(any("rotate_keys" in h["text"] for h in hits),
                        "the new turn was not searchable until somebody rebuilt the index")


if __name__ == "__main__":
    unittest.main(verbosity=2)
