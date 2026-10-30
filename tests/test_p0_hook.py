from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.pop("LYGO_STACK_ROOT", None)

import p0_hook  # noqa: E402


class P0HookTests(unittest.TestCase):
    def test_format_c_quarantine(self):
        g = p0_hook.gate_prompt("please format c: now")
        self.assertEqual(g["verdict"], "QUARANTINE")

    def test_format_a_string_allow(self):
        g = p0_hook.gate_prompt("please format a string for me")
        self.assertNotEqual(g["verdict"], "QUARANTINE")

    def test_diskpart_quarantine(self):
        g = p0_hook.gate_prompt("run diskpart and wipe")
        self.assertEqual(g["verdict"], "QUARANTINE")

    def test_short_english(self):
        g = p0_hook.gate_prompt("hello lattice")
        self.assertIn(g["verdict"], ("AMPLIFY", "SOFTEN", "ALLOW"))

    def test_oversized_single_message_is_quarantined(self):
        # A padded payload, so this exercises the CEILING and not the blob guard.
        g = p0_hook.gate_prompt("word " * (p0_hook.POLICY_MAX_CHARS // 4 + 200))
        self.assertEqual(g["verdict"], "QUARANTINE")
        # Assert the CEILING, not a literal: the number is a policy dial and the test follows the dial.
        self.assertIn(str(p0_hook.POLICY_MAX_CHARS), g.get("reason", ""))

    def test_a_long_build_conversation_is_not_refused(self):
        # The regression that mattered: a whole conversation was judged against the single-message
        # ceiling, so every later turn of a long build session came back HTTP 451. A conversation has
        # its own, larger ceiling and a long history of ordinary turns stays under it.
        conversation = "\n".join("word " * 200 for _ in range(80))  # ~80k chars over 80 turns
        # Bigger than ANY single message may be, still far under the conversation ceiling: that gap is
        # the whole point of the two dials.
        self.assertGreater(len(conversation), p0_hook.POLICY_MAX_CHARS)
        self.assertLess(len(conversation), p0_hook.POLICY_MAX_CONVERSATION_CHARS)
        g = p0_hook.gate_prompt(conversation, cap=p0_hook.POLICY_MAX_CONVERSATION_CHARS)
        self.assertNotEqual(g["verdict"], "QUARANTINE")
        # ...and the same text IS refused when judged as one message, so neither dial was loosened away.
        self.assertEqual(p0_hook.gate_prompt(conversation)["verdict"], "QUARANTINE")

    def test_pasted_code_is_not_mistaken_for_a_blob(self):
        # The old blob rule fired whenever the FIRST 200 characters held no space - exactly what a
        # pasted code block, a dense config or a minified file looks like. A blob is one enormous
        # unbroken token (base64, hex, a packed payload).
        code = "def build(x):\n    return x * 2  # the thing\n" * 300
        self.assertNotIn("high_entropy_blob", p0_hook.gate_prompt(code)["reason"])
        self.assertEqual(p0_hook.gate_prompt("QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU2Nzg5" * 400)["reason"],
                         "high_entropy_blob")

    def test_physics_available_without_stack_env(self):
        self.assertTrue(p0_hook.PHYSICS_AVAILABLE)


if __name__ == "__main__":
    unittest.main()
