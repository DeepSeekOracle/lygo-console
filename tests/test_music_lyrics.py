"""The lyrics-to-sections system: what makes a full song possible at all.

The load-bearing test is `test_the_engines_own_parser_reads_what_we_write`: the section format is not
ours to invent, it is the app's regex (`split_lyrics()` in its `gradio_server.py`), so it is copied
here verbatim and the assertion is that the ENGINE would see the sections we promise.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import music_lyrics as ml

#: The app's own pattern, copied from its `split_lyrics()`. If this file and the app ever disagree,
#: this test is the thing that goes red.
ENGINE_PATTERN = r"\[(\w+)\](.*?)\n(?=\[|\Z)"

PLAIN = """the kettle sings a morning song
the floorboards keep the time
I count the cups and carry on
and leave the rest behind

so pour it slow, pour it slow
let the steam decide the day

the kettle sings a morning song
the floorboards keep the time
I count the cups and carry on
and leave the rest behind"""

TAGGED = """[Intro]
a hum in the dark

[Verse]
the kettle sings a morning song
the floorboards keep the time

[Chorus]
so pour it slow, pour it slow

[Outro]
the light comes anyway"""


class SectionsTest(unittest.TestCase):
    def test_plain_words_are_grouped_and_the_grouping_is_reported(self) -> None:
        p = ml.plan(PLAIN)
        self.assertFalse(p["tagged"], "plain words must never be reported as the operator's own structure")
        self.assertEqual(p["count"], 3, "three stanzas are three sections")
        self.assertEqual([s["tag"] for s in p["sections"]], ["chorus", "verse", "chorus"],
                         "a stanza that comes back at all is the chorus, both times")
        self.assertIn("grouped", p["note"])

    def test_the_operators_own_tags_win(self) -> None:
        p = ml.plan(TAGGED)
        self.assertTrue(p["tagged"])
        self.assertEqual([s["tag"] for s in p["sections"]], ["intro", "verse", "chorus", "outro"])
        self.assertEqual(p["count"], 4)
        self.assertEqual(p["note"], "", "their own structure needs no note")

    def test_a_tag_the_engine_cannot_see_is_folded(self) -> None:
        # `[pre-chorus]` breaks the app's regex: a hyphen is not \w, so the marker would be swallowed
        # into the previous section's words. Folding it is what keeps the section count honest.
        p = ml.plan("[pre-chorus]\nquiet now\n\n[chorus]\nloud again")
        self.assertEqual([s["tag"] for s in p["sections"]], ["prechorus", "chorus"])
        self.assertEqual(len(re.findall(ENGINE_PATTERN, ml.structured("[pre-chorus]\nquiet now\n\n[chorus]\nloud again"), re.DOTALL)), 2)

    def test_the_engines_own_parser_reads_what_we_write(self) -> None:
        for text, want in ((PLAIN, 3), (TAGGED, 4), ("[verse]\none line", 1)):
            got = re.findall(ENGINE_PATTERN, ml.structured(text), re.DOTALL)
            self.assertEqual(len(got), want, f"the engine would see {len(got)} sections, not {want}: {text[:40]!r}")
            self.assertTrue(all(tag and body.strip() for tag, body in got), "no empty section may be sent")

    def test_a_wall_of_words_becomes_stanzas_and_says_so(self) -> None:
        p = ml.plan("\n".join(f"line {i}" for i in range(1, 13)))
        self.assertEqual(p["count"], 3, "twelve lines with no blank lines are three four-line stanzas")

    def test_the_plan_states_the_song_length_from_the_apps_own_rate(self) -> None:
        p = ml.plan(TAGGED, tokens=3000)
        self.assertEqual(p["seconds_per_section"], 30.0, "1000 tokens = 10 s, so the app's default 3000 = 30 s")
        self.assertEqual(p["seconds"], 120.0, "four sections of 30 s")
        self.assertEqual(ml.seconds_for(6000, 10), 600.0)

    def test_sections_are_capped_at_the_apps_own_maximum(self) -> None:
        many = "".join(f"[verse]\nline {i}\n\n" for i in range(1, 15))
        p = ml.plan(many)
        self.assertEqual(p["count"], 14, "the words are counted as written")
        self.assertEqual(p["run_n_segments"], ml.MAX_SECTIONS, "but no more than the app can run")
        self.assertEqual(ml.MAX_SECTIONS, 10, "the app's own slider tops out at 10 sequences")

    def test_asking_for_fewer_sections_says_what_is_left_unsung(self) -> None:
        p = ml.plan(TAGGED, want_sections=2)
        self.assertEqual(p["run_n_segments"], 2)
        self.assertIn("left unsung", p["note"])

    def test_no_words_is_a_named_refusal_not_an_empty_song(self) -> None:
        p = ml.plan("")
        self.assertEqual(p["count"], 0)
        self.assertEqual(p["seconds"], 0.0)
        self.assertIn("no lyrics", p["note"])

    def test_structured_output_is_stable_for_the_engine(self) -> None:
        once = ml.structured(TAGGED)
        self.assertEqual(once, ml.structured(once), "structuring the engine's own format must be a no-op")
        self.assertTrue(once.endswith("\n\n"), "the engine's lookahead needs the trailing break")


if __name__ == "__main__":
    unittest.main()
