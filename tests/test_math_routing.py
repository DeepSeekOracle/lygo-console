"""Arithmetic must not be routed to the web tools: host guard + model-call redirect.

Measured before the fix (qwen2.5-coder:7b, ctx 8192): "What is 17 * 23?" matched SEARCH_HINT on the
words "what is", so host_prefetch ran web_search + a web_fetch of the top hit before the model ran,
and the brain answered with a search-page number (53, then 401). Correcting the schema order in
tools.py changed nothing, because the mis-route happens before the model is called. These tests lock
the host-side guard; the live turn is measured separately by the routing bench.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import chat_loop  # noqa: E402


class MathRecogniserTests(unittest.TestCase):
    def test_bare_arithmetic_is_recognised(self):
        for text, want in (
            ("What is 17 * 23?", "17 * 23"),
            ("What is 17 * 23? Use the calc tool.", "17 * 23"),
            ("Compute 144 / 12 with the calc tool.", "144 / 12"),
            ("17 times 23 please", "17 * 23"),
            ("what is 17*23", "17*23"),
            ("17 times 23", "17 * 23"),
            ("Compute 144 / 12", "144 / 12"),
            ("what is 9 \u00d7 9", "9 * 9"),
            ("2+2=?", "2+2"),
            ("Calculate 1,000 + 500", "1000 + 500"),
            ("4 ^ 3", "4 ** 3"),
            ("how much is 12 divided by 4", "12 / 4"),
        ):
            self.assertTrue(chat_loop.math_only(text), text)
            self.assertEqual(chat_loop.math_expr(text), want)

    def test_questions_that_are_not_arithmetic_are_left_alone(self):
        for text in (
            "Who wrote the novel Moby-Dick?",
            "what is the capital of France",
            "search the web for lygo protocol stack",
            "how many drives are on this PC",
            "what time is it in Tokyo",
            "17 apples and 23 oranges",
            "read <USER>/notes.txt",
            "what is the ISS",
        ):
            self.assertFalse(chat_loop.math_only(text), text)


class HostPrefetchTests(unittest.TestCase):
    def test_arithmetic_prefetches_calc_and_not_the_web(self):
        traces = chat_loop.host_prefetch("What is 17 * 23?")
        self.assertEqual([t["name"] for t in traces], ["calc"])
        self.assertEqual(traces[0]["result"]["value"], 391)
        self.assertTrue(traces[0].get("host"))

    def test_the_sum_is_answered_not_merely_searched(self):
        traces = chat_loop.host_prefetch("what is 9 * 9")
        self.assertEqual(traces[0]["result"]["value"], 81)

    def test_the_factual_control_still_goes_to_the_web(self):
        # A question that needs the web must not be captured by the arithmetic gate. It may run the
        # real web tools, so only the routing decision is asserted, not the fetch.
        self.assertFalse(chat_loop.math_only("Who wrote the novel Moby-Dick?"))


class ModelCallRedirectTests(unittest.TestCase):
    def _msg(self, name, args):
        return {"tool_calls": [{"function": {"name": name, "arguments": json.dumps(args)}}]}

    def test_a_web_search_for_arithmetic_is_redirected_to_calc(self):
        with mock.patch.object(chat_loop, "dispatch", return_value={"ok": True, "value": 391}) as d:
            traces = chat_loop.run_tools_round("", self._msg("web_search", {"q": "what is 17 * 23"}))
        self.assertEqual(traces[0]["name"], "calc")
        self.assertEqual(d.call_args[0][0], "calc")
        self.assertEqual(d.call_args[0][1], {"expr": "17 * 23"})

    def test_a_real_search_is_not_redirected(self):
        with mock.patch.object(chat_loop, "dispatch", return_value={"ok": True, "hits": []}) as d:
            traces = chat_loop.run_tools_round("", self._msg("web_search", {"q": "lygo protocol stack"}))
        self.assertEqual(traces[0]["name"], "web_search")
        self.assertEqual(d.call_args[0][0], "web_search")
