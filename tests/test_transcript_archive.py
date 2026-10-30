"""The console keeps the whole conversation, forever, on disk - without involving the model.

Asked for in these words: "I want Conversations to flow unlimited ... CREATE A simple system that
SAVES the whole conversation window, all text into a folder inside the console ... this will be the
forever history of full conversations ... a program that doesnt bother the AI ... triggered after the
COMPLETE response ... so we dont duplicate saves we can simply save responses from AI and sent text
from the user".

So the contract these tests hold:

* a steward message is filed the moment it is sent, and the agent's answer the moment it is COMPLETE;
* the SAME text is never filed twice, however many times a turn is re-synced (the live console
  re-persists its whole session every turn - that is how history used to get lost and duplicated);
* the full text survives verbatim - no truncation, no summarising, newlines and fences intact;
* a wiped conversation starts a NEW dated file instead of appending to the old one;
* every block is labelled (who, when, which turn, which model) and indexed;
* nothing in here can raise into a turn: a broken archive must never break an answer.
"""

import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
SRC = HERE.parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import transcript_archive as ta  # noqa: E402

BIG = "\n".join(f"line {i} of the operator's long message - padding, code fences and all" for i in range(400))
BIG_ANSWER = "Here is the answer.\n\n```python\nprint('hello')\n```\n\nAnd more text.\n" * 40


class TheForeverHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # registered after the cleanup, so it runs FIRST (LIFO): a background writer must not still
        # be holding the temp folder when Windows tries to remove it
        self.addCleanup(ta.flush, 3)
        self.root = Path(self.tmp.name) / "memory" / "conversations"
        self._root = ta.ROOT
        ta.ROOT = self.root
        self.addCleanup(lambda: setattr(ta, "ROOT", self._root))
        ta.reset_for_tests()

    def _msgs(self, user, answer=None, first="hello there"):
        msgs = [{"role": "user", "content": first}]
        if answer is None:
            msgs.append({"role": "user", "content": user})
        else:
            msgs.append({"role": "user", "content": user})
            msgs.append({"role": "assistant", "content": answer})
        return msgs

    def _text(self):
        files = ta.files()
        self.assertTrue(files, "nothing was filed at all")
        return "\n".join(p.read_text(encoding="utf-8") for p in files)

    # --- the two triggers -------------------------------------------------

    def test_the_sent_text_is_filed_when_it_is_sent(self):
        ta.sync(self._msgs("first real question"), meta={"model": "qwen2.5-coder:7b"})
        ta.flush(timeout=5)
        body = self._text()
        self.assertIn("first real question", body)
        self.assertIn("STEWARD", body, "the steward's own words must be labelled as sent by the steward")

    def test_the_answer_is_filed_only_once_it_is_complete(self):
        ta.sync(self._msgs("what is the plan"), meta={"model": "qwen2.5-coder:7b"})
        ta.flush(timeout=5)
        before = self._text()
        self.assertNotIn("Here is the answer.", before, "an unfinished turn must not file an answer")
        ta.sync(self._msgs("what is the plan", BIG_ANSWER), meta={"model": "qwen2.5-coder:7b", "brain": "cloud"})
        ta.flush(timeout=5)
        after = self._text()
        self.assertIn("Here is the answer.", after)
        self.assertIn("LYGO", after, "the agent's answer must be labelled as the agent's")

    # --- never twice, never mangled --------------------------------------

    def test_re_syncing_the_same_turn_files_nothing_new(self):
        msgs = self._msgs("the same question", "the same answer")
        ta.sync(msgs, meta={})
        ta.sync(msgs, meta={})
        ta.sync(msgs, meta={})
        ta.flush(timeout=5)
        body = self._text()
        # the fixture opens with its own steward message, so count the message itself, not the label
        self.assertEqual(body.count("the same question"), 1, "the steward's message was filed more than once")
        self.assertEqual(body.count("the same answer"), 1, "the answer was filed more than once")
        self.assertEqual(body.count("sent by STEWARD"), 2, "the fixture has two steward messages")
        self.assertEqual(body.count("complete by LYGO"), 1, "one completed answer, once")

    def test_every_byte_of_a_long_message_survives(self):
        ta.sync(self._msgs(BIG, BIG_ANSWER), meta={"model": "local"})
        ta.flush(timeout=5)
        body = self._text()
        self.assertIn(BIG, body, "a long message was truncated or summarised")
        self.assertIn(BIG_ANSWER, body, "a long answer was truncated or summarised")

    def test_a_wiped_conversation_starts_a_new_file(self):
        ta.sync(self._msgs("old conversation question", "old answer"), meta={})
        ta.flush(timeout=5)
        first = ta.files()[-1]
        ta.sync([{"role": "user", "content": "brand new session opener"},
                 {"role": "assistant", "content": "brand new answer"}], meta={})
        ta.flush(timeout=5)
        files = ta.files()
        self.assertEqual(len(files), 2, "a new conversation must not be appended to the old file")
        self.assertNotEqual(first, files[-1])
        self.assertIn("brand new answer", files[-1].read_text(encoding="utf-8"))
        self.assertNotIn("brand new answer", first.read_text(encoding="utf-8"))

    # --- labelled, indexed, findable -------------------------------------

    def test_blocks_are_labelled_with_who_turn_and_time(self):
        ta.sync(self._msgs("label me"), meta={"model": "qwen2.5-coder:7b", "brain": "local"})
        ta.flush(timeout=5)
        body = self._text()
        for needle in ("STEWARD", "turn", "qwen2.5-coder:7b"):
            self.assertIn(needle, body, f"the block lost its label: {needle}")

    def test_there_is_an_index_the_agent_can_grep(self):
        ta.sync(self._msgs("indexable question", "indexable answer"), meta={})
        ta.flush(timeout=5)
        idx = ta.index_path()
        self.assertTrue(idx.is_file(), "no index file was written")
        text = idx.read_text(encoding="utf-8")
        self.assertIn("indexable question", text)

    def test_searching_finds_text_in_old_conversations(self):
        ta.sync(self._msgs("the needle is blorptwelve", "answered"), meta={})
        ta.flush(timeout=5)
        hits = ta.search("blorptwelve")
        self.assertTrue(hits, "search found nothing in a file it wrote itself")
        self.assertIn("blorptwelve", hits[-1]["text"])

    # --- it must never bother the turn ------------------------------------

    def test_filing_never_raises_and_stays_off_the_hot_path(self):
        ta.ROOT = Path(self.tmp.name) / "nope" / "conversations"
        (Path(self.tmp.name) / "nope").write_text("this is a file, not a folder", encoding="utf-8")
        t0 = time.time()
        ta.sync(self._msgs("x" * 200000, "y" * 200000), meta={})   # un-writable root, huge payload
        took = time.time() - t0
        self.assertLess(took, 0.5, f"filing blocked the turn for {took:.2f}s")
        ta.flush(timeout=5)  # must swallow the failure, not raise

    def test_filing_is_off_the_calling_thread(self):
        ta.sync(self._msgs("thread check", "thread answer"), meta={})
        # the writer is a background thread: the call returned long before the disk did
        self.assertIsNotNone(ta._writer(), "no background writer thread was started")


class TheWindowSlidesTests(unittest.TestCase):
    """The console trims an over-long conversation before it sends it. Filing must survive that."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(ta.flush, 3)
        self._root = ta.ROOT
        ta.ROOT = Path(self.tmp.name) / "memory" / "conversations"
        self.addCleanup(lambda: setattr(ta, "ROOT", self._root))
        ta.reset_for_tests()

    def _body(self):
        fs = ta.files()
        return "\n".join(p.read_text(encoding="utf-8") for p in fs), fs

    def test_a_trimmed_window_is_the_same_conversation(self):
        full = [{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"},
                {"role": "user", "content": "three"}, {"role": "assistant", "content": "four"}]
        ta.sync(full, meta={})
        ta.flush(5)
        before, files_before = self._body()
        # the console now sends only the tail of the same conversation
        ta.sync(full[-2:], meta={})
        ta.flush(5)
        after, files_after = self._body()
        self.assertEqual(len(files_after), 1, "the slid window was mistaken for a new conversation")
        self.assertEqual(before, after, "re-sending the tail wrote something new")

    def test_the_newest_message_of_a_slid_window_is_still_filed(self):
        ta.sync([{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"}], meta={})
        ta.flush(5)
        ta.sync([{"role": "assistant", "content": "two"},
                 {"role": "user", "content": "the newest question"},
                 {"role": "assistant", "content": "the newest answer"}], meta={})
        ta.flush(5)
        body, files = self._body()
        self.assertEqual(len(files), 1)
        self.assertIn("the newest answer", body)
        self.assertEqual(body.count("one"), 1, "an already-filed message was written again")

    def test_the_same_words_twice_in_one_conversation_are_one_record(self):
        ta.sync([{"role": "user", "content": "repeat me"},
                 {"role": "assistant", "content": "first reply"}], meta={})
        ta.sync([{"role": "user", "content": "repeat me"},
                 {"role": "assistant", "content": "first reply"},
                 {"role": "user", "content": "repeat me"},
                 {"role": "assistant", "content": "second reply"}], meta={})
        ta.flush(5)
        body, _ = self._body()
        self.assertEqual(body.count("repeat me"), 1, "identical words were filed twice")
        self.assertIn("second reply", body, "a genuinely new answer was dropped")

    def test_a_state_dict_without_a_cursor_does_not_break_the_turn(self):
        """The positional design kept a `cursor` key the content re-key does not write. A state dict
        without it raised KeyError('cursor') on every turn - measured 2026-09-21 in the console log,
        where the filing itself had already happened and only the return value broke. The cursor is
        now the honest count of what is filed, and nothing may raise."""
        from unittest import mock

        stale = {"seen": [], "file": None, "fingerprint": ""}
        msgs = [{"role": "user", "content": "a question after the upgrade"},
                {"role": "assistant", "content": "an answer after the upgrade"}]
        with mock.patch.object(ta, "_load_state", lambda: dict(stale)):
            out = ta.sync(msgs, meta={"model": "m", "brain": "ready", "provider": "local"})
        self.assertEqual(out.get("filed"), 2, "both messages must still be filed")
        self.assertEqual(out.get("cursor"), 2, "the cursor is how many messages are filed")
        # The writer owns the disk, so the bytes land after sync() returns: this test read the file
        # too early and passed only when the writer happened to win the race (seen 2026-09-21 in a
        # combined run). Wait for the writer - the console relies on that same asynchrony.
        ta.flush(5)
        body, _ = self._body()
        self.assertIn("a question after the upgrade", body)
        self.assertIn("an answer after the upgrade", body)


if __name__ == "__main__":
    unittest.main()


class TheAnswererIsWhoAnsweredTests(unittest.TestCase):
    """Measured on the stick, 2026-09-21: a turn the local 1.5B model answered was filed as
    `qwen2.5:1.5b · ready · answered by deepseek`. The provider on the completion line has to be the
    one that produced the text, so a local answer credits no cloud provider - the walk record
    belongs to a different brain."""

    def test_a_local_answer_never_credits_a_cloud_provider(self):
        stamp = ta._stamp("assistant", 1, {"model": "qwen2.5:1.5b", "brain": "local", "provider": "deepseek"})
        self.assertNotIn("answered by", stamp)
        self.assertIn("qwen2.5:1.5b", stamp)

    def test_a_status_string_is_not_a_brain_and_credits_nobody(self):
        stamp = ta._stamp("assistant", 1, {"model": "qwen2.5:1.5b", "brain": "ready", "provider": "deepseek"})
        self.assertNotIn("answered by", stamp)

    def test_a_cloud_answer_still_credits_the_provider_that_served_it(self):
        stamp = ta._stamp("assistant", 1, {"model": "deepseek-chat", "brain": "cloud", "provider": "nvidia"})
        self.assertIn("answered by nvidia", stamp)


class TheCompletionSitesNameTheAnsweringBrainTests(unittest.TestCase):
    """Both completion call sites must pass who answered, not a status, and gate the provider on it."""

    def test_both_sites_pass_the_answering_brain_and_gate_the_provider(self):
        src = (SRC / "server.py").read_text(encoding="utf-8")
        flat = " ".join(src.split())  # a wrapped call is still one call
        self.assertIn('"brain": active, "provider": _answered if active == "cloud" else ""', flat,
                      "the streaming/buffered completion site must name the brain that answered")
        self.assertIn('"brain": "cloud" if use_cloud else "local", "provider": _answered if use_cloud else ""',
                      flat, "the vision-refusal site must name the brain that answered")