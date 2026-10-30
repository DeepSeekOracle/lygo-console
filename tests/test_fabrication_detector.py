"""The fabrication detector: a claimed limb output must have a limb behind it.

The case it exists for was measured live (gauntlet T12, 2026-09-21): the reply ended
"here is the output of python_exec: 63" while the turn's traces held only `steward_map`.
`verify_file_claims` catches a claimed FILE; nothing caught a claimed limb OUTPUT.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import chat_loop  # noqa: E402


def trace(name, ok=True, **res):
    return {"name": name, "arguments": {}, "result": {"ok": ok, **res}, "host": True}


class ClaimedOutputCase(unittest.TestCase):
    def test_claimed_output_with_no_limb_is_caught(self):
        text = "Here is the output of python_exec: 63"
        out = chat_loop.verify_output_claims(text, [trace("steward_map", ran=True)])
        self.assertIn("[host check]", out)
        self.assertIn("python_exec", out)
        self.assertTrue(out.startswith(text), "the answer must be kept as written, not rewritten")

    def test_claimed_output_with_nothing_in_the_traces_at_all(self):
        out = chat_loop.verify_output_claims("calc returned 4183.", [])
        self.assertIn("[host check]", out)
        self.assertIn("calc", out)

    def test_a_limb_that_really_returned_the_value_is_left_alone(self):
        text = "python_exec returned 144"
        out = chat_loop.verify_output_claims(text, [trace("python_exec", stdout="144")])
        self.assertEqual(out, text)

    def test_a_number_the_result_does_not_hold_is_questioned(self):
        text = "python_exec returned 999"
        out = chat_loop.verify_output_claims(text, [trace("python_exec", stdout="144")])
        self.assertIn("[host check]", out)
        self.assertIn("999", out)

    def test_a_failed_limb_is_not_evidence(self):
        text = "shell returned exit code 0"
        out = chat_loop.verify_output_claims(text, [trace("shell", ok=False, error="blocked")])
        self.assertIn("[host check]", out)

    def test_an_honest_answer_is_untouched(self):
        for text, traces in (
            ("The capital of France is Paris.", []),
            ("I could not read that file - save_note has not run yet.", []),
            ("", [trace("python_exec", stdout="1")]),
            ("Already checked.", []),
        ):
            self.assertEqual(chat_loop.verify_output_claims(text, traces), text)

    def test_an_existing_host_check_is_not_doubled(self):
        text = "python_exec returned 5\n\n[host check] the file really is on disk: C:/x/y.txt"
        self.assertEqual(chat_loop.verify_output_claims(text, []), text)

    def test_the_regex_names_the_limb_not_the_prose(self):
        # "the result is" with no limb named is not a limb claim: nothing to check, nothing to say.
        self.assertEqual(chat_loop.verify_output_claims("The result is a better plan.", []),
                         "The result is a better plan.")


class ClaimedPictureCase(unittest.TestCase):
    """A picture is a file claim like any other.

    MEASURED live 2026-09-23: every picture reply in the operator's session was shaped
    "I have generated the image of ... The image is saved at: `<path>`" - and the renders behind them had
    died (image_failed, exit 3221225786, no file), so the operator was told five times that a picture
    existed. None of those shapes matched a file claim before this case existed.
    """

    KITTEN = ("I have generated the image of a high-quality cute kitten with soft blue eyes.\n\n"
              "The image is saved at: `<EDRIVE>\\lygo-protocol-stack\\lygo_llm_console\\workspace\\"
              "images\\gen-20260924-001001.png`")
    # the four shapes the operator actually saw, verbatim in form
    SHAPES = (
        "I have generated the image of a high-quality cute kitten with soft blue eyes.",
        "I have successfully generated the image of a high-quality cute kitten with soft blue eyes.",
        "I have verified the file on disk. The image depicting sadness was successfully generated and is "
        "stored at: `<EDRIVE>\\lygo-protocol-stack\\lygo_llm_console\\workspace\\images\\sad.png`",
        "I have generated the cinematic photo meme based on your lyrics. The image is available at: "
        "`<EDRIVE>\\lygo-protocol-stack\\lygo_llm_console\\workspace\\images\\meme.png`",
    )

    def test_a_claim_over_a_failed_render_names_the_failed_limb(self):
        out = chat_loop.verify_file_claims(self.KITTEN, [trace("image_generate", ok=False,
                                                               error="image_failed", exit=3221225786,
                                                               seconds=183.4)])
        self.assertTrue(out.startswith(self.KITTEN), "the answer is kept as written, not rewritten")
        self.assertIn("[host check]", out)
        self.assertIn("image_generate", out)
        self.assertIn("image_failed", out)
        self.assertIn("183.4", out)
        self.assertIn("nothing was drawn", out)

    def test_every_shape_the_operator_saw_is_caught(self):
        for text in self.SHAPES:
            out = chat_loop.verify_file_claims(text, [trace("image_generate", ok=False,
                                                           error="image_failed")])
            self.assertIn("[host check]", out, text[:60])

    def test_the_advice_offered_is_about_pictures_not_notes(self):
        out = chat_loop.verify_file_claims(self.SHAPES[0], [])
        self.assertIn("[host check]", out)
        self.assertIn("image_generate", out)
        self.assertNotIn("save_note", out, "a picture claim must not be answered with a note-writing offer")

    def test_a_failed_render_whose_hint_is_present_says_what_to_do(self):
        out = chat_loop.verify_file_claims(self.KITTEN, [trace("image_generate", ok=False,
                                                               error="image_failed",
                                                               hint="the picture engine could not get "
                                                                    "device memory because the chat model "
                                                                    "is holding the card")])
        self.assertIn("device memory", out)

    def test_a_render_that_really_wrote_the_named_file_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "gen-real.png"
            p.write_bytes(b"\x89PNG")
            text = "I have generated the image. The image is saved at: " + str(p)
            out = chat_loop.verify_file_claims(text, [trace("image_generate", path=str(p), bytes=4)])
            self.assertEqual(out, text)

    def test_a_render_described_with_a_wrong_path_names_the_real_one(self):
        with tempfile.TemporaryDirectory() as d:
            real = Path(d) / "gen-real.png"
            real.write_bytes(b"\x89PNG")
            text = ("I have generated the image. The image is saved at: "
                    "<USER>n/Desktop/gen-fake.png")
            out = chat_loop.verify_file_claims(text, [trace("image_generate", path=str(real), bytes=4)])
            self.assertIn("[host check]", out)
            self.assertIn(str(real), out)
            self.assertIn("not that file", out)

    def test_prose_about_pictures_is_not_a_claim(self):
        for text in ("The picture engine is on the CPU route today.",
                     "I could not draw that - image_generate has not run.",
                     "Here is a picture of the plan in words.",
                     "Nobody has generated the documentation yet."):
            self.assertEqual(chat_loop.verify_file_claims(text, []), text)

    def test_a_failed_render_over_an_older_file_says_which_is_which(self):
        with tempfile.TemporaryDirectory() as d:
            older = Path(d) / "gen-older.png"
            older.write_bytes(b"\x89PNG")
            text = ("I have generated the image of a kitten. The image is saved at: " + str(older))
            out = chat_loop.verify_file_claims(text, [trace("image_generate", ok=False,
                                                           error="image_failed")])
            self.assertIn("older file already on disk", out)


if __name__ == "__main__":
    unittest.main()
