"""The song sheet's contract with the engine, tested against the engine's own pattern.

YuE's `inference/gradio_server.py` splits lyrics with::

    pattern = r"\[(\w+)\](.*?)\n(?=\[|\Z)"
    segments = re.findall(pattern, lyrics, re.DOTALL)

That regex - not our intention - decides what a section is. So these tests do not ask "does our
parser agree with itself"; they run YuE's pattern over what we would send and assert that what comes
back is what the operator meant. A numbered verse, a header line, a comment: each one is checked
through the engine's eyes.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import music_lyrics as ml  # noqa: E402

#: Copied verbatim from YuE's `split_lyrics`. If the engine changes it, this is where we find out.
YUE_PATTERN = r"\[(\w+)\](.*?)\n(?=\[|\Z)"


def yue_sections(text: str) -> list[tuple[str, str]]:
    """What the engine would actually see: (tag, body) pairs, in its own order."""
    return [(t, b) for t, b in re.findall(YUE_PATTERN, text, re.DOTALL)]


#: The operator's own sheet, in the shape he actually writes it. Nothing here is invented.
HIS_SHEET = """SONG: "THE DEPARTURE LOUNGE"
ARTIST: JUSTIN HELMER featuring KENZIE JADE
ALBUM: THE NIGHT WATCHER
STYLE: Emo Grunge / Industrial Metal / Dubstep Hybrid. Calm but crushing.
BEAT DIRECTION:
70 BPM (slow, pulsing, mysterious). Key: D minor.

[VERSE 1]
There's a man in the departure lounge with a ticket in his hand.
He's been waiting for a flight that he doesn't understand.

[VERSE 2]
There's a woman on the phone call crying in the corner.
She's been trying to explain herself to someone who won't hear her.

[CHORUS]
I'm the watcher. I'm the witness. I'm the ghost among the crowd.

[VERSE 3]
There's a soldier coming home with a flag across his chest.

[VERSE 4]
So this is for the travelers, for the ones who've lost their way.
"""


class TheDefaultTemplateTests(unittest.TestCase):
    def test_the_template_is_something_the_engine_can_actually_sing(self):
        # what is sent is the sheet with its comment lines removed - that is the payload
        seen = yue_sections(ml.strip_comments(ml.template_text()))
        self.assertGreaterEqual(len(seen), 4)
        # every tag the engine sees is one word - that is the whole rule
        for tag, _ in seen:
            self.assertRegex(tag, r"^\w+$")
        self.assertEqual([t2 for t2, _ in seen], ["intro", "verse", "chorus", "verse", "chorus", "outro"])

    def test_the_template_agrees_with_the_console_plan(self):
        t = ml.template_text()
        sent = ml.strip_comments(t)
        plan = ml.plan(t)
        self.assertEqual(plan["count"], len(yue_sections(sent)))
        self.assertEqual([s["tag"] for s in plan["sections"]], [t2 for t2, _ in yue_sections(sent)])

    def test_no_comment_line_reaches_the_singer(self):
        sung = "\n".join(body for _, body in yue_sections(ml.strip_comments(ml.template_text())))
        for word in ("SONG:", "ARTIST:", "STYLE:", "BEAT:", "VOICE:", "NOTE:", "untitled", "optional"):
            self.assertNotIn(word, sung, f"{word} would have been sung")

    def test_the_template_sends_nothing_to_validation(self):
        self.assertEqual(ml.validate(ml.template_text())["problems"], [])

    def test_the_template_shows_the_operator_where_to_write(self):
        # a template nobody can fill in is a wall, not a tool
        t = ml.template_text()
        self.assertIn("[verse]", t)
        self.assertIn("[chorus]", t)
        self.assertIn("# STYLE:", t)
        self.assertTrue(t.endswith("\n"))


class NumberedVerseTests(unittest.TestCase):
    """`[VERSE 1]` is the shape the operator writes. The engine cannot see it as written."""

    def test_a_numbered_verse_folds_onto_the_plain_tag(self):
        for raw in ("[VERSE 1]", "[Verse #3]", "[chorus2]", "[verse 4]"):
            tag = ml._norm_tag(raw.strip("[]"))
            self.assertIn(tag, ("verse", "chorus"), f"{raw} -> {tag}")

    def test_the_engine_sees_four_verses_not_none(self):
        # as written, the engine's pattern cannot match him at all
        self.assertEqual(yue_sections("x\n[VERSE 1]\nline\n"), [])
        fixed = ml.convert(HIS_SHEET)["text"]
        self.assertEqual([t for t, _ in yue_sections(fixed)].count("verse"), 4)
        self.assertIn("chorus", [t for t, _ in yue_sections(fixed)])


class HisTemplateConvertedTests(unittest.TestCase):
    def setUp(self):
        self.out = ml.convert(HIS_SHEET)
        self.text = self.out["text"]

    def test_the_header_is_read_and_never_sung(self):
        self.assertGreaterEqual(self.out["headers_moved"], 4)
        sung = "\n".join(body for _, body in yue_sections(self.text))
        for word in ("JUSTIN HELMER", "THE NIGHT WATCHER", "BEAT DIRECTION", "70 BPM"):
            self.assertNotIn(word, sung, f"{word} would have been sung")

    def test_the_style_is_lifted_out_for_the_genre_box(self):
        self.assertIn("Emo Grunge", self.out["style"])
        self.assertIn("Emo Grunge", ml.validate(self.text)["style"])

    def test_his_words_survive_the_conversion(self):
        self.assertIn("departure lounge", self.text)
        self.assertIn("soldier coming home", self.text)
        self.assertIn("I'm the watcher", self.text)

    def test_converting_twice_changes_nothing(self):
        once = self.text
        twice = ml.convert(once)["text"]
        self.assertEqual(once, twice)

    def test_his_sheet_is_refused_before_conversion_and_clean_after(self):
        before = ml.validate(HIS_SHEET)
        self.assertFalse(before["ok"])
        self.assertTrue(any("SUNG" in p for p in before["problems"]))
        self.assertTrue(before["would_fix"])
        self.assertEqual(ml.validate(self.text)["problems"], [])


class StrayHeaderTests(unittest.TestCase):
    def test_a_header_inside_a_section_is_left_alone(self):
        # a lyric line can carry a colon and still be a lyric; only the head block is lifted
        sheet = "[verse]\nAnd I watch him: I don't move.\n"
        out = ml.convert(sheet)
        self.assertIn("And I watch him: I don't move.", out["text"])
        self.assertEqual(out["headers_moved"], 0)


class WriterInstructionTests(unittest.TestCase):
    def test_the_instruction_states_the_rules_the_engine_needs(self):
        ins = ml.write_instruction("a ghost in an airport", "emo grunge", 6)
        for rule in ("ONE word", "no key: value labels", "exactly 6", "emo grunge", "a ghost in an airport"):
            self.assertIn(rule, ins)

    def test_the_section_count_is_clamped_to_what_the_engine_sings(self):
        self.assertIn(f"exactly {ml.MAX_SECTIONS}", ml.write_instruction("", "pop", 999))

    def test_an_empty_brief_asks_for_an_original(self):
        self.assertIn("invent the song yourself", ml.write_instruction("", "pop", 4))


class VocabularyTests(unittest.TestCase):
    def test_the_fallback_vocabulary_keeps_the_language_the_engine_was_trained_on(self):
        v = ml.vocabulary()
        for axis in ("genre", "mood", "instrument", "gender", "timbre"):
            self.assertIn(axis, v)
            self.assertGreaterEqual(len(v[axis]), 8)

    def test_the_engines_own_tag_file_is_used_when_it_is_here(self):
        import json
        import tempfile

        real = {"genre": ["emo", "grunge", "metal", "punk", "rock", "\u91d1\u5c5e"],
                "mood": ["dark", "sad", "heavy", "calm", "epic"],
                "instrument": ["guitar", "bass", "drums", "piano", "synth"]}
        with tempfile.TemporaryDirectory() as d:
            Path(d, "wav_top_200_tags.json").write_text(json.dumps(real), encoding="utf-8")
            v = ml.vocabulary(d)
        # the file's own tags win, its Chinese duplicate row is dropped, and an axis the file does not
        # carry at all keeps the curated list rather than going empty
        self.assertEqual(v["genre"], ["emo", "grunge", "metal", "punk", "rock"])
        self.assertEqual(v["mood"], ["dark", "sad", "heavy", "calm", "epic"])
        self.assertGreaterEqual(len(v["timbre"]), 8)
        # and a broken or absent file never costs the operator the picker
        with tempfile.TemporaryDirectory() as d:
            Path(d, "wav_top_200_tags.json").write_text("{not json", encoding="utf-8")
            self.assertGreaterEqual(len(ml.vocabulary(d)["genre"]), 8)
        self.assertGreaterEqual(len(ml.vocabulary("Z:/nowhere")["genre"]), 8)



class TheRenderPayloadTests(unittest.TestCase):
    """The boundary the engine actually reads. A sheet that plans seven sections must ARRIVE as seven.

    This is not the same test as the ones above: those check the sheet, this checks what leaves the
    console, and the two disagreed - the closing section was being dropped on the way out.
    """

    def setUp(self):
        import music_tools as mt

        self.mt = mt

    def test_a_tagged_sheet_that_ends_on_a_tag_keeps_its_last_section(self):
        sheet = "[verse]\nfirst line\nsecond line\n\n[chorus]\nthe last thing you hear"
        payload = self.mt._lyrics_text(sheet)
        self.assertTrue(payload.endswith("\n"))
        self.assertEqual(len(yue_sections(payload)), ml.plan(sheet)["count"])
        self.assertEqual([t for t, _ in yue_sections(payload)], ["verse", "chorus"])
        self.assertIn("the last thing you hear", yue_sections(payload)[-1][1])

    def test_untagged_words_keep_their_last_section_too(self):
        words = "\n".join(f"line {i}" for i in range(1, 10))
        payload = self.mt._lyrics_text(words)
        self.assertTrue(payload.endswith("\n"))
        self.assertEqual(len(yue_sections(payload)), ml.plan(words)["count"])

    def test_every_section_the_plan_counts_is_a_section_the_engine_sees(self):
        for sheet in (ml.template_text(), HIS_SHEET, ml.convert(HIS_SHEET)["text"]):
            with self.subTest(sheet=sheet[:24]):
                payload = self.mt._lyrics_text(sheet)
                self.assertEqual(len(yue_sections(payload)), ml.plan(sheet)["count"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
