"""The defects the 2026-09-21 campaign found, held down as tests.

Each of these was measured on the running console or against the vendor's own endpoint; the numbers and
the raw rows are in docs/TEST_CAMPAIGN_2026-09-21.md and workspace/memory/test-campaign-*.jsonl.

  1. switching provider kept the previous provider's model, so every API turn came back 404
     ("The model `deepseek-chat` not found" from NVIDIA, Gemini and Groq alike);
  2. the local fallback then re-used that same foreign model, so the engine that was ready to answer
     could not be asked to - the operator was shown "API handoff" instead of an answer;
  3. a reasoning provider given a small output budget answered almost nothing ("GEM" for a request to
     say GEMINI OK) while a plain provider was fine;
  4. the volatile readout that rides the newest message came back inside the answer, so a reply of
     "LOCAL SIDE" was followed by the clock and the world line;
  5. a limb that failed was repeated by the model as the answer, so an internal Python message
     ("calc could not answer (invalid syntax ...)") was shown to the operator as if it were the reply.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve()
SRC = HERE.parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import chat_loop  # noqa: E402
import cloud_api  # noqa: E402


class SwitchingProviderAdoptsItsModelTests(unittest.TestCase):
    """1 + 2: the model must follow the provider, and the local fallback must not inherit it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name) / "api.json"
        self.addCleanup(lambda: None)
        self._path = cloud_api.API_PATH
        cloud_api.API_PATH = self.cfg
        self.addCleanup(lambda: setattr(cloud_api, "API_PATH", self._path))

    def test_switching_provider_adopts_that_providers_model(self):
        cloud_api.save({"provider": "deepseek", "key": "sk-" + "a" * 32})
        before = cloud_api.public_status()
        self.assertEqual(before["model"], cloud_api.PROVIDERS["deepseek"]["model"])
        st = cloud_api.save({"provider": "nvidia"})
        self.assertEqual(st["provider"], "nvidia")
        self.assertEqual(st["model"], cloud_api.PROVIDERS["nvidia"]["model"],
                         "the provider changed but the model stayed behind - this is the 404")

    def test_an_explicit_model_is_still_honoured(self):
        cloud_api.save({"provider": "deepseek", "key": "sk-" + "b" * 32})
        st = cloud_api.save({"provider": "nvidia", "model": "nvidia/my-own-choice"})
        self.assertEqual(st["model"], "nvidia/my-own-choice")

    def test_an_unrelated_save_does_not_disturb_the_model(self):
        cloud_api.save({"provider": "nvidia", "key": "nvapi-" + "c" * 60})
        st = cloud_api.save({"enabled": True})
        self.assertEqual(st["model"], cloud_api.PROVIDERS["nvidia"]["model"])

    def test_the_local_fallback_does_not_inherit_the_apis_model(self):
        api_payload = {"model": "this-model-does-not-exist-lygo", "max_tokens": 4096,
                       "messages": [{"role": "user", "content": "hi"}],
                       "chat_template_kwargs": {"thinking": False}}
        local = cloud_api.local_payload_from(api_payload, "qwen2.5-coder:7b")
        self.assertEqual(local["model"], "qwen2.5-coder:7b",
                         "the engine was handed the API's model - it refuses and the turn dies")
        self.assertNotIn("chat_template_kwargs", local, "an API-only field was sent to our own engine")
        self.assertEqual(local["messages"], api_payload["messages"], "the conversation must survive")


class ReasoningBudgetTests(unittest.TestCase):
    """3: a reasoning provider needs a floor under its output budget or it answers nothing."""

    def test_a_reasoning_provider_gets_a_floor(self):
        self.assertGreaterEqual(cloud_api.effective_max_tokens("gemini", 64), 512)
        self.assertGreaterEqual(cloud_api.effective_max_tokens("groq", 64), 512)

    def test_a_plain_provider_is_left_alone(self):
        self.assertEqual(cloud_api.effective_max_tokens("deepseek", 64), 64)

    def test_a_larger_ask_is_never_lowered(self):
        self.assertEqual(cloud_api.effective_max_tokens("gemini", 4096), 4096)


class TheAnswerIsCleanedTests(unittest.TestCase):
    """4 + 5: what the operator reads must be the answer, not our machinery."""

    def test_the_readout_is_stripped_from_an_answer(self):
        got = chat_loop.strip_readout(
            "LOCAL SIDE\n\nNOW UTC 2026-09-21T19:00:44+00:00 · local 2026-09-21T13:00:44-06:00 (Mountain)")
        self.assertEqual(got.strip(), "LOCAL SIDE")

    def test_a_genuine_answer_that_mentions_time_is_kept(self):
        for text in ["The current UTC time is 2026-09-21T19:00:48+00:00, which is early evening in Utah.",
                     "NOW UTC is the label this console prints above the clock."]:
            self.assertEqual(chat_loop.strip_readout(text), text)

    def test_a_limb_failure_is_recognised_as_not_an_answer(self):
        self.assertTrue(chat_loop.is_limb_failure("calc could not answer (invalid syntax (<unknown>, line 1))."))
        self.assertTrue(chat_loop.is_limb_failure("  web_search could not answer (timeout).  "))

    def test_a_real_answer_is_not_mistaken_for_a_limb_failure(self):
        for text in ["51", "The result is 51.", "calc could not answer that, so I worked it out: 51."]:
            self.assertFalse(chat_loop.is_limb_failure(text))


class TheConsoleDescribesItselfAccuratelyTests(unittest.TestCase):
    """The agent must be told the box it is actually running on.

    Measured 2026-09-21 against the running console: asked to describe its environment, BOTH brains said
    they served on 9641, while the console was bound to 9651. They were quoting config/console.json's
    default, because --port only ever reached the banner - never the facts the agent reads.
    """

    def test_live_facts_reports_the_port_the_console_is_bound_to(self):
        import os

        import banner

        old = os.environ.get("LYGO_CONSOLE_PORT")
        os.environ["LYGO_CONSOLE_PORT"] = "9651"
        try:
            self.assertEqual(banner.live_facts().get("console_port"), 9651)
        finally:
            if old is None:
                os.environ.pop("LYGO_CONSOLE_PORT", None)
            else:
                os.environ["LYGO_CONSOLE_PORT"] = old

    def test_the_runtime_block_the_agent_reads_carries_that_port(self):
        import os

        import runtime_facts

        old = os.environ.get("LYGO_CONSOLE_PORT")
        os.environ["LYGO_CONSOLE_PORT"] = "9651"
        try:
            block = runtime_facts.prompt_block("local")
        finally:
            if old is None:
                os.environ.pop("LYGO_CONSOLE_PORT", None)
            else:
                os.environ["LYGO_CONSOLE_PORT"] = old
        self.assertIn("http://127.0.0.1:9651/", block)

    def test_the_readout_is_labelled_as_not_the_question(self):
        import continuity

        tail = continuity.volatile_tail(with_clock=True)
        self.assertIn("NOT the question", tail)
        # the clock line still stands alone, so the cleanup can still recognise it
        self.assertRegex(tail, r"(?m)^NOW UTC \d{4}-\d{2}-\d{2}T")

    def test_that_label_never_reaches_the_operator(self):
        note = ("READOUT (context for this turn only - this is NOT the question and NOT an answer to "
                "repeat; use it only when the turn is actually about time or place):")
        text = (note + "\nNOW UTC 2026-09-21T19:00:44+00:00 · local 2026-09-21T13:00:44-06:00 · Monday."
                "\nREADOUT CHECK")
        self.assertEqual(chat_loop.strip_readout(text).strip(), "READOUT CHECK")



    def test_the_echoed_capability_line_is_stripped_too(self):
        """The model parroted the tail's capability sentence as an answer: "The world_pulse provides
        timestamps and weather updates for various cities, but it's important to ...". It is context."""
        echoed = ("world_pulse holds city clocks and weather (RESOURCE, not CANON), for turns about "
                  "time or place.\nThe answer is 4.")
        self.assertEqual(chat_loop.strip_readout(echoed).strip(), "The answer is 4.")

    def test_the_tail_keeps_both_halves(self):
        """The label without the capability line would leave clocks unreachable - the guard in
        tests/test_continuity.py is right about that, and tests/test_conversational_reply.py asserts it
        for the no-clock shape. Keep both."""
        import continuity

        with_clock = continuity.volatile_tail(with_clock=True)
        without = continuity.volatile_tail(with_clock=False)
        self.assertIn("NOW UTC", with_clock)
        self.assertIn("world_pulse", with_clock)
        self.assertNotIn("NOW UTC", without)
        self.assertIn("world_pulse holds", without)
        self.assertIn("NOT the question", without)



class AProducedPictureIsNamedInTheReplyTest(unittest.TestCase):
    """Row 76: the operator asked the API brain for a picture and was told "I'll render it now - first
    run loads the checkpoint, so give it a minute." The file WAS made (23.6s, a real PNG) - but the
    answer never said where it was. A produced artifact that the answer does not mention is an answer
    that hides its own result."""

    def test_a_produced_picture_is_named_with_its_path_and_size(self):
        traces = [{"name": "image_generate",
                   "result": {"ok": True, "path": "workspace/images/gen-20260921-130555.png",
                              "bytes": 957430, "seconds": 23.6, "engine": "sdxl-turbo"}}]
        out = chat_loop.surface_artifacts("I'll render it now - first run loads the checkpoint.", traces)
        self.assertIn("gen-20260921-130555.png", out)
        self.assertIn("957,430", out)
        self.assertIn("I'll render it now", out)  # the answer itself is kept

    def test_an_answer_that_already_names_the_file_is_left_alone(self):
        traces = [{"name": "image_generate",
                   "result": {"ok": True, "path": "workspace/images/gen-a.png", "bytes": 10}}]
        text = "Done - the picture is at workspace/images/gen-a.png"
        self.assertEqual(chat_loop.surface_artifacts(text, traces), text)

    def test_a_failed_limb_adds_nothing(self):
        traces = [{"name": "image_generate", "result": {"ok": False, "error": "vram", "path": ""}}]
        text = "It did not work."
        self.assertEqual(chat_loop.surface_artifacts(text, traces), text)

    def test_a_reader_limb_is_not_advertised_as_a_new_picture(self):
        traces = [{"name": "image_see",
                   "result": {"ok": True, "path": "workspace/images/input.png", "bytes": 10,
                              "text": "a cat"}}]
        text = "That is a cat."
        self.assertEqual(chat_loop.surface_artifacts(text, traces), text)

    def test_no_traces_is_a_no_op(self):
        self.assertEqual(chat_loop.surface_artifacts("plain answer", []), "plain answer")


class TheWalkRecordSaysWhichWalkItWasTest(unittest.TestCase):
    """Row 60: `chain_tried` showed one turn's refusals beside an answer another turn had produced, so
    the panel read as if this turn had failed over when it had not. The record is worth keeping - it
    just has to say when it was taken and that it is the last walk, not this turn."""

    def test_the_record_carries_the_time_it_was_taken(self):
        import cloud_api

        cloud_api._note_chain(["nvidia:500", "deepseek:200"], "deepseek")
        st = cloud_api.public_status()
        self.assertIn("chain_at", st)
        self.assertTrue(str(st["chain_at"]).strip(), "a walk record with no time is the defect")
        self.assertIn("deepseek:200", str(st.get("chain_tried")))

    def test_the_label_says_it_is_the_last_walk_not_this_turn(self):
        import cloud_api

        cloud_api._note_chain(["nvidia:500", "deepseek:200"], "deepseek")
        label = cloud_api.chain_label()
        self.assertIn("last cloud walk", label)
        self.assertIn("nvidia:500", label)

    def test_with_no_walk_recorded_the_label_says_so(self):
        import cloud_api

        self.assertIn("no cloud walk", cloud_api.chain_label({"chain_tried": []}).lower())



if __name__ == "__main__":
    unittest.main()
