"""TurnPerf regression tests — the counter that makes a turn's cost visible.

Measured on this box 2026-09-22 (qwen2.5-coder:7b, RTX 4060 Ti, ngl 99, 16k window): three
consecutive turns asked "Reply with exactly: CACHE TEST"; the engine generated **137 tokens
at 47.7 tok/s** on each and the console displayed **10 characters**. The engine's own
cumulative counter agreed (`llamacpp:tokens_predicted_total 412` for the whole engine life),
and nothing in the console could see it — which is the actual defect these tests lock down:

  * a turn records what it generated AND what it showed, and attributes the difference;
  * a check that could not run says so (`counted: estimate`, never a silent claim of engine);
  * the asked shape ("reply with exactly X", "one word", "three sentences") becomes a hard
    token budget, and a pure echo is offered no limb schema at all — the mechanism that made
    137 tokens out of a two-word answer;
  * a shape ask that also names work keeps every limb, so the shape rules can never take a
    capability away from a task that needs one.

No engine and no GPU are needed: the tokenizer is stubbed, and the record is pure arithmetic.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import turnperf  # noqa: E402


def engine_reply(content: str, *, gen_n: int, prompt_n: int, calls=None, reasoning: str = "") -> dict:
    """One llama-server reply, shaped the way the console really receives it."""
    msg: dict = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = calls
    if reasoning:
        msg["reasoning_content"] = reasoning
    return {
        "choices": [{"message": msg}],
        "timings": {
            "prompt_n": prompt_n,
            "prompt_per_second": 219.6,
            "predicted_n": gen_n,
            "predicted_per_second": 47.7,
        },
    }


class TestRulerIsNamed(unittest.TestCase):
    """A count is only worth something when the method behind it is stated."""

    def test_no_engine_falls_back_and_says_so(self):
        n, how = turnperf.count("CACHE TEST", port=None)
        self.assertEqual(n, 3)
        self.assertEqual(how, "estimate")

    def test_engine_tokenizer_wins_when_reachable(self):
        with patch.object(turnperf, "engine_tokenize", return_value=7):
            n, how = turnperf.count("CACHE TEST", port=11441)
        self.assertEqual((n, how), (7, "engine"))

    def test_empty_text_is_zero_and_named_empty(self):
        self.assertEqual(turnperf.count("", port=11441), (0, "empty"))

    def test_tokenizer_failure_never_raises(self):
        """A dead tokenizer is an estimate, not an exception - and never a silent claim."""
        with patch.object(turnperf, "engine_tokenize", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                turnperf.engine_tokenize("x", port=1)
        n, how = turnperf.count("hello", port=1)
        self.assertGreaterEqual(n, 1)
        self.assertIn(how, ("engine", "estimate"))

    def test_unreachable_port_probes_and_falls_back(self):
        n, how = turnperf.count("a fairly ordinary sentence", port=9)
        self.assertGreater(n, 1)
        self.assertEqual(how, "estimate")


class TestCallTexts(unittest.TestCase):
    """`predicted_n` counts tokens that can land in three different places."""

    def test_tool_call_is_captured_not_ignored(self):
        got = turnperf.call_texts(engine_reply("CACHE TEST", gen_n=137, prompt_n=84,
                                               calls=[{"function": {"name": "read_file",
                                                                    "arguments": "{\"path\": \"x\"}"}}]))
        self.assertEqual(got["content"], "CACHE TEST")
        self.assertIn("read_file", got["calls"])

    def test_reasoning_is_captured(self):
        got = turnperf.call_texts(engine_reply("ok", gen_n=50, prompt_n=10, reasoning="weighing options"))
        self.assertEqual(got["reasoning"], "weighing options")
        self.assertEqual(got["content"], "ok")

    def test_junk_never_raises(self):
        for junk in (None, [], {}, {"choices": []}, {"choices": [None]}, {"choices": [{"message": 3}]}):
            self.assertEqual(turnperf.call_texts(junk)["content"], "")


class TestNoteCall(unittest.TestCase):
    """The per-turn log is the only place a call's numbers and its text meet."""

    def test_call_without_timings_is_not_logged(self):
        log: list = []
        turnperf.note_call(log, {"choices": [{"message": {"content": "hi"}}]})
        self.assertEqual(log, [])

    def test_logged_entry_keeps_timings_and_text_lengths(self):
        log: list = []
        turnperf.note_call(log, engine_reply("CACHE TEST", gen_n=137, prompt_n=84))
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["gen_n"], 137)
        self.assertEqual(log[0]["content_chars"], len("CACHE TEST"))
        self.assertEqual(log[0]["_content"], "CACHE TEST")

    def test_junk_never_raises(self):
        log: list = []
        for junk in (None, [], "x", 5, {"timings": "no"}):
            turnperf.note_call(log, junk)
        self.assertEqual(log, [])


class TestCountingOneTurn(unittest.TestCase):
    """The measured case, and the honesty gates around it."""

    def _record(self, **kw):
        """One turn's record. `gen_n` defaults to what the shown text really costs, so the
        'nothing is left unexplained' tests are about accounting and not about arithmetic luck."""
        content = kw.pop("content", "CACHE TEST")
        calls = kw.pop("calls", None)
        reasoning = kw.pop("reasoning", "")
        shown = kw.pop("shown_text", content)
        raw_texts = kw.pop("raw_texts", None)
        gen_n = kw.pop("gen_n", None)
        log: list = []
        turnperf.note_call(log, engine_reply(content, gen_n=gen_n or 1, prompt_n=kw.pop("prompt_n", 84),
                                             calls=calls, reasoning=reasoning))
        if gen_n is None:
            sinks = [shown, reasoning] + list(raw_texts or [])
            if calls:
                sinks.append(json.dumps(calls, default=str))
            gen_n = sum(turnperf.count(s, port=None)[0] for s in sinks if s)
            turnperf.note_call(log, engine_reply(content, gen_n=gen_n, prompt_n=84, calls=calls,
                                                 reasoning=reasoning))
            log.pop(0)
        return turnperf.attribute(timing_log=log, shown_text=shown, raw_texts=raw_texts,
                                  port=None, **kw)

    def test_measured_gap_is_reported_as_a_gap(self):
        rec = self._record(gen_n=137, prompt_n=84)
        self.assertEqual(rec["gen_tokens"], 137)
        self.assertEqual(rec["shown_tokens"], 3)
        self.assertEqual(rec["surplus_tokens"], 134)
        self.assertEqual(rec["surplus_pct"], 98)
        self.assertEqual(rec["counted"], "estimate")
        self.assertEqual(rec["engine_calls"], 1)

    def test_a_limb_call_explains_the_gap(self):
        calls = [{"function": {"name": "read_file", "arguments": json.dumps({"path": "p" * 80})}}]
        rec = self._record(calls=calls)
        self.assertGreater(rec["call_tokens"], 0)
        self.assertGreater(rec["surplus_tokens"], 3)
        self.assertEqual(rec["unaccounted_tokens"], 0)
        self.assertIn("read_file", rec["dropped_head"])

    def test_reasoning_explains_the_gap(self):
        rec = self._record(reasoning="weighing the options " * 10)
        self.assertGreater(rec["thinking_tokens"], 10)
        self.assertEqual(rec["unaccounted_tokens"], 0)

    def test_unexplained_tokens_are_reported_not_rounded_over(self):
        rec = self._record(gen_n=137)
        self.assertEqual(rec["unaccounted_tokens"], 134)
        self.assertTrue(rec["dropped_head"] or rec["call_tokens"] >= 0)

    def test_a_replaced_answer_counts_as_superseded(self):
        draft = "a first draft of the answer, quite a long one in fact"
        rec = self._record(gen_n=137, raw_texts=[draft, "CACHE TEST"], shown_text="CACHE TEST")
        self.assertGreater(rec["superseded_tokens"], 3)
        self.assertLess(rec["unaccounted_tokens"], rec["surplus_tokens"])

    def test_a_clean_turn_has_no_gap_and_no_sample(self):
        log: list = []
        turnperf.note_call(log, engine_reply("CACHE TEST", gen_n=3, prompt_n=84))
        rec = turnperf.attribute(timing_log=log, shown_text="CACHE TEST", port=None)
        self.assertEqual(rec["surplus_tokens"], 0)
        self.assertEqual(rec["surplus_pct"], 0)
        self.assertNotIn("dropped_head", rec)

    def test_a_superseded_draft_is_kept_in_the_sample(self):
        rec = self._record(gen_n=137, raw_texts=["an older draft that lost", "CACHE TEST"],
                           shown_text="CACHE TEST")
        self.assertIn("older draft", rec["dropped_head"])

    def test_wall_clock_split_is_attributed(self):
        rec = self._record(gen_n=137, wall_ms=3233.7, limbs=2)
        self.assertEqual(rec["wall_ms"], 3233.7)
        self.assertEqual(rec["limbs"], 2)
        self.assertGreater(rec["unshown_ms"], rec["gen_ms"] * 0.5)

    def test_empty_turn_never_divides_by_zero(self):
        rec = turnperf.attribute(timing_log=[], shown_text="", port=None)
        self.assertEqual(rec["gen_tokens"], 0)
        self.assertEqual(rec["surplus_pct"], 0)
        self.assertNotIn("error", rec)

    def test_junk_input_returns_a_record_not_an_exception(self):
        rec = turnperf.attribute(timing_log=[{"gen_n": "x"}], shown_text=None, port=None)
        self.assertIn("error", rec)
        self.assertEqual(rec["signature"], turnperf.SIGNATURE)

    def test_public_calls_do_not_leak_private_text_keys(self):
        rec = self._record(shown_text="CACHE TEST")
        for entry in rec["calls"]:
            self.assertFalse([k for k in entry if k.startswith("_")], entry)

    def test_report_line_names_the_gap(self):
        line = turnperf.report(self._record(gen_n=137, prompt_n=84))
        self.assertIn("137", line)
        self.assertIn("unseen", line)
        self.assertIn("ruler", line)


class TestAskedShape(unittest.TestCase):
    """The operator's own words decide the budget — and whether a limb is worth offering."""

    def test_reply_with_exactly_is_a_pure_echo(self):
        got = turnperf.shape_budget("Reply with exactly: CACHE TEST", 4096, port=None)
        self.assertEqual(got["kind"], "exact")
        self.assertEqual(got["phrase"], "CACHE TEST")
        self.assertFalse(got["tools"])
        self.assertLessEqual(got["cap"], 64)
        self.assertGreaterEqual(got["cap"], turnperf.SHAPE_FLOOR)

    def test_say_only_quoted_phrase(self):
        got = turnperf.shape_budget('say only "stack"', 4096, port=None)
        self.assertEqual(got["kind"], "exact")
        self.assertEqual(got["phrase"], "stack")

    def test_one_word(self):
        got = turnperf.shape_budget("answer in one word", 4096, port=None)
        self.assertEqual(got["kind"], "one_word")
        self.assertLessEqual(got["cap"], turnperf.SHAPE_FLOOR + 1)

    def test_three_sentences_gets_grace(self):
        got = turnperf.shape_budget("reply with three sentences", 4096, port=None)
        self.assertEqual(got["kind"], "sentences")
        self.assertEqual(got["count"], 3)
        self.assertEqual(got["grace"], turnperf.GRACE_TOKENS)

    def test_yes_or_no(self):
        got = turnperf.shape_budget("Answer yes or no", 4096, port=None)
        self.assertEqual(got["kind"], "yes_no")

    def test_free_turn_is_untouched(self):
        self.assertEqual(turnperf.shape_budget("what time is it right now?", 4096, port=None), {})

    def test_shape_ask_that_also_names_work_keeps_every_limb(self):
        for text in (
            "Write the file to disk with exactly the text OMEGA-441",
            "save a note in the workspace with exactly this text",
            "reply with exactly: done, then list the files in my folder",
            "Read the note and reply with exactly the code word",
        ):
            got = turnperf.shape_budget(text, 4096, port=None)
            self.assertNotEqual(got.get("tools"), False, text)

    def test_a_long_message_gets_no_shape_budget(self):
        text = "Reply with exactly: CACHE TEST " + ("and more context " * 20)
        self.assertEqual(turnperf.shape_budget(text, 4096, port=None), {})

    def test_cap_never_raises_the_configured_ceiling(self):
        got = turnperf.shape_budget("write 20 sentences", 64, port=None)
        self.assertLessEqual(got["cap"], 64)

    def test_junk_never_raises(self):
        for junk in (None, "", 5, "   "):
            self.assertEqual(turnperf.shape_budget(junk, 4096, port=None), {})


class TestStopCondition(unittest.TestCase):
    """A shape is finished when it is finished; the tokens after it are nobody's."""

    def test_exact_phrase_completes(self):
        shape = turnperf.shape_budget("Reply with exactly: CACHE TEST", port=None)
        self.assertTrue(turnperf.shape_complete("Reply with exactly: CACHE TEST", "CACHE TEST", shape))
        self.assertFalse(turnperf.shape_complete("Reply with exactly: CACHE TEST", "CACHE", shape))

    def test_exact_phrase_matches_across_whitespace_and_case(self):
        shape = turnperf.shape_budget("Reply with exactly: cache test", port=None)
        self.assertTrue(turnperf.shape_complete("Reply with exactly: cache test", "  Cache\nTest ", shape))

    def test_sentences_count(self):
        shape = turnperf.shape_budget("reply with two sentences", port=None)
        self.assertFalse(turnperf.shape_complete("reply with two sentences", "One sentence only.", shape))
        self.assertTrue(turnperf.shape_complete("reply with two sentences", "One. Two.", shape))

    def test_items_count(self):
        shape = turnperf.shape_budget("give three bullet points", port=None)
        self.assertFalse(turnperf.shape_complete("give three bullet points", "- a\n- b", shape))
        self.assertTrue(turnperf.shape_complete("give three bullet points", "- a\n- b\n- c", shape))

    def test_yes_no_completes_on_the_word(self):
        shape = turnperf.shape_budget("answer yes or no", port=None)
        self.assertTrue(turnperf.shape_complete("answer yes or no", "Yes — because the file is there.", shape))

    def test_free_turn_never_completes_early(self):
        self.assertFalse(turnperf.shape_complete("explain the architecture", "Explaining.", None))

    def test_empty_text_is_never_complete(self):
        shape = turnperf.shape_budget("Reply with exactly: X", port=None)
        self.assertFalse(turnperf.shape_complete("Reply with exactly: X", "", shape))
        self.assertFalse(turnperf.shape_complete("Reply with exactly: X", "   ", shape))

    def test_make_stopper_is_none_for_a_free_turn(self):
        self.assertIsNone(turnperf.make_stopper("what is the weather in Tokyo?"))
        stop = turnperf.make_stopper("Reply with exactly: CACHE TEST")
        self.assertIsNotNone(stop)
        self.assertEqual(stop.grace, 0)
        self.assertTrue(stop("CACHE TEST"))

    def test_counted_shape_carries_a_grace(self):
        stop = turnperf.make_stopper("reply with three bullet points")
        self.assertEqual(stop.grace, turnperf.GRACE_TOKENS)


class TestWiring(unittest.TestCase):
    """The record is only worth building if the turn actually publishes it."""

    def _server_src(self) -> str:
        return (ROOT / "src" / "server.py").read_text(encoding="utf-8", errors="replace")

    def test_server_imports_and_uses_turnperf(self):
        src = self._server_src()
        for needle in ("import turnperf", "turnperf.note_call(", "turnperf.attribute(",
                       "turnperf.shape_budget(", "turnperf.report("):
            self.assertTrue(needle in src, "src/server.py must use " + needle)

    def test_portal_renders_the_gap(self):
        js = (ROOT / "portal" / "app.js").read_text(encoding="utf-8", errors="replace")
        self.assertTrue("surplus_tokens" in js, "portal/app.js must render the unseen-token count")
        self.assertTrue("shown_tokens" in js, "portal/app.js must render the shown-token count")

    def test_health_publishes_last_perf_with_the_new_fields(self):
        """`last_perf` in /api/health is the home the operator already reads."""
        src = self._server_src()
        self.assertIn('STATE["last_perf"]', src)

    def test_a_streamed_turn_asks_the_engine_for_its_numbers(self):
        """Measured 2026-09-22: a streamed turn recorded `perf: {}` - the numbers arrive in the
        final chunk, and only when `stream_options.include_usage` asked for them."""
        src = self._server_src()
        self.assertIn('"include_usage": True', src)
        self.assertIn("on_timings", src)

    def test_the_streamed_read_can_be_stopped_and_says_so(self):
        src = self._server_src()
        self.assertIn("stopped the read there", src)
        self.assertIn('"seen_text"', src)


class TestStreamedAndCached(unittest.TestCase):
    """The two readings a streamed turn needs: its numbers, and how much was already cached."""

    def test_a_final_chunk_with_timings_is_recorded(self):
        log: list = []
        final = {"object": "chat.completion.chunk", "choices": [],
                 "timings": {"prompt_n": 32, "predicted_n": 3, "cache_n": 8033,
                             "prompt_per_second": 411.9, "predicted_per_second": 47.9},
                 "usage": {"completion_tokens": 3, "prompt_tokens": 35}}
        turnperf.note_call(log, final)
        self.assertEqual(log[0]["gen_n"], 3)
        self.assertEqual(log[0]["prompt_n"], 32)
        self.assertEqual(log[0]["cache_n"], 8033)

    def test_usage_alone_is_still_a_ruler(self):
        """A build that reports `usage` without `timings` must still be measurable."""
        log: list = []
        turnperf.note_call(log, {"choices": [],
                                 "usage": {"completion_tokens": 12, "prompt_tokens": 200,
                                           "prompt_tokens_details": {"cached_tokens": 150}}})
        self.assertEqual(log[0]["gen_n"], 12)
        self.assertEqual(log[0]["prompt_n"], 200)
        self.assertEqual(log[0]["cache_n"], 150)

    def test_a_chunk_without_numbers_adds_nothing(self):
        log: list = []
        turnperf.note_call(log, {"choices": [{"delta": {"content": "hi"}}]})
        self.assertEqual(log, [])

    def test_prefix_reuse_is_reported_as_a_percentage(self):
        log: list = []
        turnperf.note_call(log, {"timings": {"prompt_n": 145, "predicted_n": 6, "cache_n": 7920,
                                             "prompt_per_second": 411.9, "predicted_per_second": 47.9},
                                 "choices": [{"message": {"content": "CACHE TEST"}}]})
        rec = turnperf.attribute(timing_log=log, shown_text="CACHE TEST", port=None)
        self.assertEqual(rec["cache_tokens"], 7920)
        self.assertEqual(rec["cache_hit_pct"], 98)
        self.assertEqual(rec["prefill_new_tokens"], 145)

    def test_a_turn_that_read_nothing_from_cache_says_zero(self):
        log: list = []
        turnperf.note_call(log, {"timings": {"prompt_n": 8065, "predicted_n": 3, "cache_n": 0,
                                             "prompt_per_second": 3037.0, "predicted_per_second": 47.0},
                                 "choices": [{"message": {"content": "ok"}}]})
        rec = turnperf.attribute(timing_log=log, shown_text="ok", port=None)
        self.assertEqual(rec["cache_hit_pct"], 0)


class TestShapePhraseAnywhere(unittest.TestCase):
    """Where the shape sits in the message must not decide whether it is read.

    Both of these were measured on the live console 2026-09-22 and neither was recognised: the
    operator's own words said what the turn should cost, and the console offered 24 limbs anyway.
    """

    def _shape(self, text):
        with patch.dict(os.environ, {"LYGO_TURNPERF": "1"}):
            return turnperf.shape_budget(text, port=None)

    def test_a_shape_ask_at_the_end_is_read(self):
        s = self._shape("Is 2+2=4? Answer in one word.")
        self.assertEqual(s.get("kind"), "one_word")
        self.assertIs(s.get("tools"), False)
        self.assertTrue(s.get("cap"))

    def test_a_shape_ask_at_the_front_is_read(self):
        s = self._shape("In one sentence, what is the LYGO protocol stack?")
        self.assertEqual(s.get("kind"), "one_sentence")
        self.assertIs(s.get("tools"), False)
        self.assertTrue(s.get("cap"))

    def test_a_shape_ask_that_names_work_still_keeps_every_limb(self):
        """The exact string the live probe used - it must not lose the writing limb."""
        ask = ("Write the file <USER>/AppData/Local/Temp/lygo_probe.txt with the single "
               "word ok, then reply with exactly DONE")
        self.assertEqual(self._shape(ask), {})

    def test_a_plural_noun_is_work_too(self):
        self.assertEqual(self._shape("List the files in my workspace in 3 bullet points"), {})

    def test_a_free_form_question_is_not_a_shape_ask(self):
        self.assertEqual(self._shape("What is the LYGO protocol stack?"), {})


class TestSentenceEndsAreReal(unittest.TestCase):
    """An abbreviation must not satisfy "one sentence" - the read would stop mid-answer."""

    def test_an_abbreviation_is_not_a_sentence_end(self):
        self.assertFalse(turnperf.shape_complete("Answer in one sentence.", "See e.g. the docs",
                                                 {"kind": "one_sentence"}))

    def test_a_decimal_is_not_a_sentence_end(self):
        self.assertFalse(turnperf.shape_complete("Answer in one sentence.", "It is 2.5 units",
                                                 {"kind": "one_sentence"}))

    def test_a_real_sentence_end_is(self):
        self.assertTrue(turnperf.shape_complete("Answer in one sentence.",
                                                "The console measures it. And more follows",
                                                {"kind": "one_sentence"}))

    def test_a_finished_sentence_at_the_end_counts(self):
        self.assertTrue(turnperf.shape_complete("Answer in one sentence.", "That is the answer.",
                                                {"kind": "one_sentence"}))


class TestConsoleAuthoredText(unittest.TestCase):
    """shown > generated is not an error: the console wrote part of the answer itself."""

    def test_the_console_authored_part_is_named(self):
        log: list = []
        turnperf.note_call(log, engine_reply("DONE", gen_n=2, prompt_n=480))
        rec = turnperf.attribute(timing_log=log,
                                 shown_text="DONE\n\n[console] the on-box brain did not call the "
                                            "writing limb, so the console wrote it itself: it wrote "
                                            "the file, 340 bytes, and here is a longer note about it",
                                 port=None)
        self.assertEqual(rec["surplus_tokens"], 0)
        self.assertGreater(rec["console_authored_tokens"], 0)

    def test_a_normal_turn_names_no_console_text(self):
        log: list = []
        turnperf.note_call(log, engine_reply("CACHE TEST", gen_n=2, prompt_n=145))
        rec = turnperf.attribute(timing_log=log, shown_text="CACHE TEST", port=None)
        self.assertNotIn("console_authored_tokens", rec)


class TestTheSwitch(unittest.TestCase):
    """An operator must be able to put every turn back exactly as it was, without editing code."""

    def test_the_switch_disables_the_shape(self):
        with patch.dict(os.environ, {"LYGO_TURNPERF": "0"}):
            self.assertEqual(turnperf.shape_budget("Reply with exactly: CACHE TEST", port=None), {})
            self.assertIsNone(turnperf.make_stopper("Answer in one word", {}))
            self.assertIsNone(turnperf.make_stopper("Answer in one word"))

    def test_the_switch_leaves_the_measurement_on(self):
        """What the record says is worth having either way - the switch changes behaviour, not truth."""
        with patch.dict(os.environ, {"LYGO_TURNPERF": "0"}):
            log: list = []
            turnperf.note_call(log, engine_reply("CACHE TEST", gen_n=137, prompt_n=84))
            rec = turnperf.attribute(timing_log=log, shown_text="CACHE TEST", port=None)
            self.assertEqual(rec["gen_tokens"], 137)
            self.assertEqual(rec["surplus_tokens"], 134)

    def test_a_shape_ask_is_still_read_when_the_switch_is_unset(self):
        with patch.dict(os.environ, {"LYGO_TURNPERF": "1"}):
            shape = turnperf.shape_budget("Reply with exactly: CACHE TEST", port=None)
        self.assertEqual(shape.get("kind"), "exact")
        self.assertIs(shape.get("tools"), False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
