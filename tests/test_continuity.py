from __future__ import annotations

import sys
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from continuity import (  # noqa: E402
    append_memory,
    compose_system,
    ensure_identity,
    identity_path,
    memory_path,
    soul_path,
)


class ContinuityTests(unittest.TestCase):
    def test_seed_and_append(self):
        ensure_identity()
        self.assertTrue(soul_path().is_file())
        self.assertTrue(identity_path().is_file())
        self.assertTrue(memory_path().is_file())
        r = append_memory("leeches have three jaws")
        self.assertTrue(r.get("ok"))
        blob = memory_path().read_text(encoding="utf-8")
        self.assertIn("three jaws", blob)
        sys_txt = compose_system()
        self.assertIn("SOUL.md", sys_txt)
        self.assertIn("IDENTITY.md", sys_txt)
        self.assertIn("MEMORY.md", sys_txt)
        self.assertIn("light math", sys_txt.lower())
        self.assertIn("champion-arkos", memory_path().read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class PrefixCacheTests(unittest.TestCase):
    """A turn is [system][history][newest message] and the engine caches the longest common prefix.

    Measured on this box, one model, one flag set, two turns identical apart from the clock line 61 s
    apart (llama-server, q8_0 KV, -ngl 79, a ~7.6k-token history):

        clock inside the system block -> turn 2 re-prefilled 7,974 tokens in 146.1 s (54 tok/s)
        clock behind the history      -> turn 2 prefilled        17 tokens in   2.5 s

    So the clock is volatile and must ride the NEWEST message. These tests hold that shape in place:
    if the clock ever moves back inside the identity block, a turn stops being cacheable and the
    operator feels it as a dead console, not as a failing test.
    """

    def test_the_identity_block_is_byte_stable_and_carries_no_clock(self):
        from continuity import volatile_tail

        first = compose_system("local", with_clock=False)
        second = compose_system("local", with_clock=False)
        self.assertEqual(first, second, "an unstable identity block can never be cached")
        self.assertNotIn("NOW UTC", first)
        self.assertIn("NOW UTC", volatile_tail())
        # Other callers keep the clock in the block: the default did not change under them.
        self.assertIn("NOW UTC", compose_system("local"))

    def test_the_console_system_message_is_clock_free_and_the_tail_has_it(self):
        import server

        sysmsg = server.local_system_message("local")
        self.assertEqual("system", sysmsg["role"])
        self.assertNotIn("NOW UTC", sysmsg["content"])
        self.assertIn("NOW UTC", server.local_tail())

    def test_the_primer_sends_the_identical_opening_a_real_turn_sends(self):
        import server

        payload = server.prefix_prime_payload("gemma4-12b")
        self.assertEqual(server.local_system_message("local"), payload["messages"][0],
                         "primer and turn disagree: the primed prefix would stop matching")
        self.assertIn("NOW UTC", str(payload["messages"][-1]["content"]))
        self.assertTrue(payload["tools"], "a real turn sends the core schema")

    def test_a_streamed_turn_shows_every_token_and_keeps_the_text(self):
        import json as _json

        import openai_proxy
        import server

        frames: list[dict] = []

        def fake_stream(**_kwargs):
            for piece in ("The ", "lattice ", "works"):
                yield b"data: " + _json.dumps({"choices": [{"delta": {"content": piece}}]}).encode()
            yield b"data: [DONE]"

        with patch.object(openai_proxy, "llama_chat_stream", fake_stream):
            text = server._stream_local_turn({"model": "m", "messages": []}, frames.append)
        self.assertEqual("The lattice works", text)
        self.assertEqual(["The ", "lattice ", "works"], [f["delta"] for f in frames])
        self.assertTrue(all(f["type"] == "token" for f in frames))

    def test_a_stream_that_produces_nothing_falls_back_instead_of_breaking(self):
        import openai_proxy
        import server

        def silent(**_kwargs):
            return iter(())

        with patch.object(openai_proxy, "llama_chat_stream", silent):
            self.assertIsNone(server._stream_local_turn({"model": "m", "messages": []}, lambda f: None))


class TailNeverOrdersAToolTest(unittest.TestCase):
    """The tail is the one piece of prompt text that rides the operator's own message.

    An order there is obeyed on any input. Measured live 2026-09-21 00:18, local brain
    qwen2.5-coder-7b, one turn, the operator's whole text "heloo?": the engine emitted a world_pulse
    tool call and the console streamed city clocks and weather back as the answer. The primer sends
    the same sentence at every boot, so the engine was primed with the order as well.

    A capability is stated, never ordered - the tool schemas already carry what each tool is for.
    """

    def test_the_tail_states_the_capability_without_ordering_it(self):
        from continuity import volatile_tail

        tail = volatile_tail()
        self.assertIn("NOW UTC", tail, "the tail still has to carry the clock")
        self.assertIn("world_pulse", tail, "clocks/weather still have to be reachable from here")
        self.assertNotRegex(
            tail,
            r"\bCall\s+[a-z_]+",
            "an imperative glued to the operator's text is obeyed on any input, greeting included",
        )

    def test_the_clock_block_inside_the_identity_block_carries_no_order_either(self):
        txt = compose_system("local")
        self.assertIn("NOW UTC", txt)
        clock_block = txt[txt.index("NOW UTC"):]
        self.assertNotRegex(clock_block, r"\bCall\s+[a-z_]+")

    def test_the_primer_and_the_turn_still_send_one_identical_tail(self):
        """Changing the tail's wording is only safe while both callers keep calling one function."""
        import server

        self.assertIn("NOW UTC", server.local_tail())
        payload = server.prefix_prime_payload("gemma4-12b")
        self.assertIn("NOW UTC", str(payload["messages"][-1]["content"]))

    def test_the_gates_keep_the_last_word_when_the_finished_text_differs(self):
        """The page replaces its bubble on this frame; the streamed text is never the final word."""
        import server

        with open(server.__file__.replace(".pyc", ".py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn('{"type": "replace", "replace": assistant', source)
        self.assertIn("elif assistant != streamed:", source)
