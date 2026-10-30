"""The music limb: wired so the console can really make a song, and honest when it cannot.

The three things this pins, in order of how much they matter:

1. **The limb is reachable.** `music_generate` is in the core schema the on-box brain is handed AND
   in `CORE_NAMES`, and the dispatcher has a branch for it. A limb missing from `CORE_NAMES` is
   invisible to the console's own brain while the source says otherwise - the fault no human sees.
2. **A song the host promised is a file that exists.** The claim machinery that watches a picture is
   extended to a song: "I generated the song" with no render behind it is caught and answered with
   the path the limb really wrote, or with a refusal by name.
3. **Every refusal is by NAME.** No engine, no weights, no lyrics, an empty request: each one comes
   back as a named error with a remedy, never as a silent prose answer.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import chat_loop  # noqa: E402
import limbs  # noqa: E402
import music_tools  # noqa: E402
import tools  # noqa: E402


class LimbIsReachable(unittest.TestCase):
    def test_the_brain_is_offered_the_limb(self):
        names = {(t.get("function") or {}).get("name") for t in tools.core_schema()}
        self.assertIn("music_generate", names)
        self.assertIn("music_generate", tuple(tools.CORE_NAMES))

    def test_the_dispatcher_has_a_branch_for_it(self):
        src = Path(limbs.__file__).read_text(encoding="utf-8")
        self.assertIn('name == "music_generate"', src)

    def test_the_schema_caps_still_hold(self):
        names = {(t.get("function") or {}).get("name") for t in tools.core_schema()}
        # 44 slots / 25256 chars as of the film module; the arithmetic is measured in
        # tests/test_tool_battery.py, and re-asserted here so a limb landing later cannot quietly
        # blow the budget a small model pays for every turn.
        self.assertLessEqual(len(names), 44)
        self.assertLess(len(str(tools.core_schema())), 26980)

    def test_a_small_model_s_aliases_fill_style_and_lyrics(self):
        _name, args = limbs.canonicalize("music_generate", {"genre": "dark synthwave", "words": "[verse]\nhello"})
        self.assertEqual(args.get("style"), "dark synthwave")
        self.assertEqual(args.get("lyrics"), "[verse]\nhello")

    def test_escaped_lyrics_are_repaired_not_sung_as_backslash_n(self):
        _name, args = limbs.canonicalize("music_generate", {"style": "pop", "lyrics": "[verse]\\nline one\\n[chorus]\\nline two"})
        self.assertIn("\n", args.get("lyrics", ""))
        self.assertNotIn("\\n", args.get("lyrics", ""))


class RefusalsByName(unittest.TestCase):
    def test_an_empty_request_is_named(self):
        out = music_tools.music_generate("", "")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "empty_request")

    def test_a_song_with_no_lyrics_asks_for_words_by_name(self):
        if not music_tools.engine_state().get("app"):
            self.skipTest("no music engine on this machine to ask")
        out = music_tools.music_generate("warm folk", "")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "lyrics_required")
        self.assertTrue(out.get("hint"), "the refusal must say what to do next")

    def test_no_engine_is_named_with_a_remedy(self):
        # The engine's own row is what decides whether its app is there (it is resolved per ENGINE, so a
        # second engine can never inherit the first one's paths), so that is what this stub removes.
        real = music_tools.engines_state

        def _no_app():
            rows = real()
            for row in rows:
                if row.get("id") == "yue":
                    row["app"], row["app_ready"] = None, False
            return rows

        try:
            music_tools.engines_state = _no_app
            out = music_tools.music_generate("pop", "[verse]\nwords here", engine="yue")
        finally:
            music_tools.engines_state = real
        self.assertFalse(out["ok"])
        # "not installed" is the honest name when the app is what is absent, and it fires before the
        # vaguer "no engine" ever could.
        self.assertEqual(out["error"], "engine_not_installed")
        hint = str(out.get("hint") or "")
        self.assertTrue("yue_root" in hint or "music_root" in hint,
                        "the remedy must name the config key that fixes it: " + hint)

    def test_a_missing_weight_is_named(self):
        # Injects the absent weight into the row of WHICHEVER ENGINE IS CHOSEN, rather than patching
        # `_weights`. `_weights` feeds YuE's row alone; this machine's chosen engine is ace_step, so
        # the old patch landed on a row the render never read and the limb answered ok=True. A test
        # that only passes while one particular engine is configured is testing the machine, not the
        # code: this version moves the fault into the engine actually under test.
        real = music_tools.engines_state
        chosen = music_tools.chosen_engine()

        def _missing_weight():
            rows = real()
            for row in rows:
                if row.get("id") == chosen:
                    row["app_ready"] = True
                    row["missing"] = []
                    row["weights"] = [
                        {"id": "YuE-s1-7B-anneal-en-cot", "path": "x", "present": False,
                         "bytes": 0, "note": "the singer"},
                    ]
            return rows

        try:
            music_tools.engines_state = _missing_weight
            out = music_tools.music_generate("pop", "[verse]\nwords here")
        finally:
            music_tools.engines_state = real
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "no_music_weights")
        self.assertIn("YuE-s1-7B-anneal-en-cot", out["missing"])


class TheOperatorAsksForASong(unittest.TestCase):
    def test_a_song_ask_is_read_as_a_song_ask(self):
        req = chat_loop.song_request("write me a hip hop song about the lattice waking up")
        self.assertIsNotNone(req)
        self.assertIn("hip hop", req["style"])
        self.assertIn("lattice", req["theme"])
        self.assertEqual(req["lyrics"], "")

    def test_a_search_for_somebody_else_s_music_is_not_a_song_request(self):
        for ask in ("find me the lyrics to that song", "what's the name of that track",
                    "search for a spotify playlist of country songs"):
            self.assertIsNone(chat_loop.song_request(ask), ask)

    def test_a_song_ask_the_operator_asked_for_is_not_disturbed(self):
        self.assertIsNone(chat_loop.song_request("what is a song"))
        self.assertIsNone(chat_loop.song_request("how many songs are in my library"))

    def test_lyrics_the_operator_wrote_are_used_as_given(self):
        ask = ("make me a song about the sea\n"
               "[verse]\nSalt on the window, salt in the air\n"
               "[chorus]\nSing it back, sing it back to me")
        req = chat_loop.song_request(ask)
        self.assertIsNotNone(req)
        self.assertIn("[verse]", req["lyrics"])

    def test_the_host_writes_the_words_and_calls_the_limb(self):
        seen: dict = {}

        def fake_dispatch(name, args):
            seen["name"] = name
            seen["args"] = args
            return {"ok": True, "path": "X:/songs/song.mp3", "bytes": 1, "audio_seconds": 12.0}

        real_dispatch, real_write = chat_loop.dispatch, chat_loop.write_lyrics
        try:
            chat_loop.dispatch = fake_dispatch
            chat_loop.write_lyrics = lambda theme, style, timeout=300.0: "[verse]\nwritten here\n[chorus]\nsung here"
            traces = chat_loop.host_song_generate("write me a folk song about the river")
        finally:
            chat_loop.dispatch, chat_loop.write_lyrics = real_dispatch, real_write
        self.assertEqual(seen.get("name"), "music_generate")
        self.assertIn("[verse]", seen["args"]["lyrics"])
        self.assertTrue(traces and traces[0]["host"])
        self.assertIn("folk", traces[0]["arguments"]["style"])

    def test_a_song_ask_with_no_engine_comes_back_as_the_engine_s_own_refusal(self):
        real = music_tools.engines_state
        real_dispatch = chat_loop.dispatch

        def _no_app():
            # EVERY declared engine loses its app, not just YuE. This test rides `chosen_engine()`
            # rather than naming an engine, and it used to blank the yue row only - so once this
            # machine's chosen engine became ace_step, a ready engine survived the patch, the engine
            # check passed, and the refusal came back as `lyrics_required`. Whatever `chosen_engine()`
            # picks, this hides it. (The sibling test above pins engine="yue" and so may keep its
            # narrow stub: there the engine is named, not chosen.)
            rows = real()
            for row in rows:
                row["app"], row["app_ready"] = None, False
            return rows

        try:
            music_tools.engines_state = _no_app
            chat_loop.dispatch = tools.dispatch
            traces = chat_loop.host_song_generate("write me a pop song about the rain")
        finally:
            music_tools.engines_state = real
            chat_loop.dispatch = real_dispatch
        self.assertTrue(traces, "the limb must be called even when it will refuse")
        self.assertFalse(traces[0]["result"]["ok"])
        self.assertEqual(traces[0]["result"]["error"], "engine_not_installed")


class ASongClaimIsCheckedLikeAPictureClaim(unittest.TestCase):
    def test_a_song_claimed_with_no_render_is_caught(self):
        text = "I have generated the song for you. The song is saved at: `C:/nowhere/song.mp3`"
        out = chat_loop.verify_file_claims(text, [])
        self.assertIn("[host check]", out)
        self.assertIn("song", out)

    def test_a_song_that_really_rendered_is_not_second_guessed(self):
        text = "Made it - the track is at X:/songs/song.mp3"
        traces = [{"name": "music_generate", "arguments": {}, "host": True,
                   "result": {"ok": True, "path": "X:/songs/song.mp3", "bytes": 10}}]
        out = chat_loop.verify_file_claims(text, traces)
        self.assertNotIn("[host check]", out)

    def test_a_failed_render_is_reported_as_a_failure_not_as_a_song(self):
        text = "The song has been saved for you."
        traces = [{"name": "music_generate", "arguments": {}, "host": True,
                   "result": {"ok": False, "error": "music_engine_failed", "why": "CUDA out of memory",
                              "hint": "the card is held; close the console and ask again"}}]
        out = chat_loop.verify_file_claims(text, traces)
        self.assertIn("[host check]", out)
        self.assertIn("music_engine_failed", out)
        self.assertIn("held", out, "the engine's own hint must reach the operator")

    def test_a_picture_claim_still_works_exactly_as_before(self):
        out = chat_loop.verify_file_claims("I have generated the image. It is saved at C:/nowhere/cat.png", [])
        self.assertIn("[host check]", out)
        self.assertIn("picture", out)

    def test_a_request_is_not_read_as_a_claim(self):
        self.assertIsNone(chat_loop.CLAIM_SONG.search("can you make a song about the lattice"))
        self.assertIsNotNone(chat_loop.CLAIM_SONG.search("I made the song about the lattice"))


class ArtifactsAreNamedUnderTheAnswer(unittest.TestCase):
    def test_a_real_song_is_named_as_a_song(self):
        traces = [{"name": "music_generate", "arguments": {}, "host": True,
                   "result": {"ok": True, "path": "X:/songs/song.mp3", "bytes": 2048}}]
        out = chat_loop.surface_artifacts("Here it is.", traces)
        self.assertIn("\u00b7 song:", out)
        self.assertIn("song.mp3", out)

    def test_a_real_picture_still_says_picture(self):
        traces = [{"name": "image_generate", "arguments": {}, "host": True,
                   "result": {"ok": True, "path": "X:/pics/cat.png", "bytes": 2048}}]
        out = chat_loop.surface_artifacts("Here it is.", traces)
        self.assertIn("\u00b7 picture:", out)


class EngineControlBoundsAreRefusedByName(unittest.TestCase):
    """The engine's own sliders are a contract; a value outside them is refused HERE, by name.

    YuE's gradio_server.py declares max_new_tokens as Slider(300, 6000, step=300) and its sequences
    slider as Slider(1, 10). Gradio enforces those in its own preprocess, so the engine has already
    booted (MEASURED 2026-09-25: 91 s) before it answers `Value 200 is less than minimum value 300.` and
    the operator is left with a bare `song_failed` whose cause is only in the engine log. The limb now
    checks the engine's own bounds first: milliseconds, and the hint names the range to type instead.
    """

    WORDS = "[verse]\na line of words\n[chorus]\nanother line"

    def setUp(self):
        # A bounds refusal must land BEFORE an engine exists. This guard makes that a proven fact and
        # not a hope: MEASURED 2026-09-25 - the first cut of this class checked the PLANNED values,
        # which `ml.plan()` had already normalised into range, so it booted a real YuE render server
        # and rendered a whole song from inside this suite. Nothing here may spawn anything.
        self.spawns = []
        self.real_start = music_tools._start_server

        def _never(*a, **k):
            self.spawns.append(a)
            raise AssertionError("a refused request must not start an engine: " + repr(a))

        music_tools._start_server = _never

    def tearDown(self):
        music_tools._start_server = self.real_start

    def test_a_token_count_below_the_engines_floor_is_refused(self):
        import time  # local: this is a timing claim, and the file needs no clock otherwise

        t0 = time.perf_counter()
        out = music_tools.music_generate("a style", self.WORDS, engine="yue",
                                         segments=2, max_new_tokens=200)
        took = time.perf_counter() - t0
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error"), "tokens_out_of_range")
        self.assertEqual(out.get("range"), [300, 6000])
        self.assertEqual(out.get("tokens"), 200)
        self.assertLess(took, 2.0, "a bounds refusal must not boot the engine to find out")
        self.assertIn("300", str(out.get("hint")))
        self.assertEqual(self.spawns, [], "a refusal must not start an engine")

    def test_more_sections_than_the_engine_can_sing_is_refused(self):
        out = music_tools.music_generate("a style", self.WORDS, engine="yue",
                                         segments=12, max_new_tokens=300)
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error"), "sections_out_of_range")
        self.assertEqual(out.get("range"), [1, 10])
        self.assertEqual(out.get("sections"), 12)
        self.assertEqual(self.spawns, [], "a refusal must not start an engine")

    def test_a_song_longer_than_the_engine_can_sing_is_refused(self):
        # The engine would otherwise be handed the request, quietly shortened to its own ceiling
        # (`min(asked, 10)` sections), and deliver a different song than the one asked for.
        out = music_tools.music_generate("a style", self.WORDS, engine="yue", seconds=3600)
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error"), "sections_out_of_range")
        self.assertGreater(out.get("sections"), 10)
        self.assertEqual(out.get("range"), [1, 10])
        self.assertEqual(self.spawns, [], "a refusal must not start an engine")


if __name__ == "__main__":
    unittest.main(verbosity=2)
