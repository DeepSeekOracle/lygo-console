"""The full-song system: the words decide the sections, and a long render says where it is.

1. Sections come from the words. `run_n_segments` must equal the number of sections in what the
   operator wrote, and the engine must be posted the `[tag]` form its own `split_lyrics` parses:
   raw words parse as ZERO sections and sing nothing.
2. A multi-section job gets time proportional to its sections. `music_timeout_s` is ONE section's
   budget; a six-section song killed after one section's worth of time is a song lost.
3. The engine's own position is readable, because hours of "rendering" with no position cannot be
   told from a hung render.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import music_jobs  # noqa: E402
import music_lyrics as ml  # noqa: E402
import music_tools as mt  # noqa: E402
from paths import WORKSPACE  # noqa: E402

#: The engine's OWN section parser, copied from `split_lyrics()` in the installed app
#: (`inference/gradio_server.py`, upstream m-a-p/YuE). Not ours to choose: what this finds is exactly
#: what the engine will sing, so a format this does not match is a silent no-op.
ENGINE_PATTERN = re.compile(r"\[(\w+)\](.*?)\n(?=\[|\Z)", re.DOTALL)

SONGS = WORKSPACE / "audio" / "songs"

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

SIX = "\n\n".join(f"[verse]\nline one of part {i}\nline two of part {i}" for i in range(1, 7))

ENGINE_LOG = """\
Loading model ...
[INFO] Stage 1: Generating Sequence 1 out of 6
  0%|          | 0/1 [00:00<?, ?it/s]
Stage 2: Decoding 1/2, segment 1 out of 3
Stage 1: Generating Sequence 4 out of 6
 50%|#####     | 1/2 [09:11<09:11, 551.02s/it]
"""


class _Proc:
    pid = 424242

    def poll(self):
        return None

    def terminate(self):
        pass

    def kill(self):
        pass


class SectionsFromTheWords(unittest.TestCase):
    def setUp(self) -> None:
        self.before = {p.name for p in SONGS.glob("*")} if SONGS.is_dir() else set()
        self.posted: dict = {}
        self.to = None
        self._saved = (mt._start_server, mt._post_job, mt._stop_server, mt._free_port)
        mt._free_port = lambda: 9

        def _start(py, app, prof, port, to, log_path):  # noqa: ANN001
            Path(log_path).write_text("", encoding="utf-8")
            self.to = to
            return _Proc(), ""

        def _post(port, payload, to):  # noqa: ANN001
            self.posted = {"payload": payload, "to": to}
            self.to = to
            return {"ok": True}

        mt._start_server = _start
        mt._post_job = _post
        mt._stop_server = lambda *a, **k: None

    def tearDown(self) -> None:
        (mt._start_server, mt._post_job, mt._stop_server, mt._free_port) = self._saved
        if SONGS.is_dir():
            for p in SONGS.glob("*"):
                if p.name not in self.before:
                    shutil.rmtree(p, ignore_errors=True)

    def _render(self, lyrics: str, **kw):
        if not mt.engine_state().get("app"):
            self.skipTest("no music engine on this machine to drive")
        # This file pins the YuE contract (its slot order, its section count, its section budget), so it
        # names that engine rather than depending on whichever engine this machine last selected.
        kw.setdefault("engine", "yue")
        out = mt.music_generate(style="warm folk", lyrics=lyrics, seed=7, **kw)
        self.assertTrue(self.posted, f"nothing was posted to the engine: {out}")
        return out

    def test_the_slots_are_in_the_engines_own_order(self):
        # The engine names them genres_input, lyrics_input, run_n_segments, seed, max_new_tokens, ...
        # Pinning the layout matters: a slot shifted by one is a song made from the wrong words.
        self._render(PLAIN, max_new_tokens=1500)
        slots = self.posted["payload"]["data"]
        self.assertEqual(slots[0], "warm folk")
        self.assertIn("[verse]", slots[1])
        self.assertEqual(slots[2], 3)
        self.assertEqual(slots[3], 7)
        self.assertEqual(slots[4], 1500)
        self.assertEqual(len(slots), 11, "the trailing gr.State slot is what made a job malformed")

    def test_the_engine_is_given_one_section_per_stanza_of_the_words(self):
        self._render(PLAIN)
        slots = self.posted["payload"]["data"]
        self.assertEqual(slots[2], 3, "run_n_segments must be the number of sections in the words")
        words = slots[1]
        for tag in ("[chorus]", "[verse]"):
            self.assertIn(tag, words, "the engine is posted the [tag] form its own parser reads")
        self.assertEqual(len(ENGINE_PATTERN.findall(words)), 3)

    def test_a_six_section_song_is_posted_as_six(self):
        self._render(SIX)
        self.assertEqual(self.posted["payload"]["data"][2], 6)

    def test_an_explicit_fragment_still_wins_over_the_words(self):
        self._render(SIX, segments=2)
        self.assertEqual(self.posted["payload"]["data"][2], 2)

    def test_more_sections_than_the_engine_accepts_is_capped_at_its_own_limit(self):
        self._render("\n\n".join(f"[verse]\nline {i}" for i in range(1, 15)))
        self.assertEqual(self.posted["payload"]["data"][2], ml.MAX_SECTIONS)

    def test_the_timeout_is_one_section_budget_multiplied_by_the_sections(self):
        self._render(SIX, timeout=600)
        self.assertEqual(self.to, 3600, "six sections get six budgets, not one")

    def test_a_single_section_job_keeps_the_plain_budget(self):
        self._render("[verse]\njust the one part", timeout=600)
        self.assertEqual(self.to, 600)

    def test_no_sections_to_sing_is_refused_by_name_before_any_engine_starts(self):
        if not mt.engine_state().get("app"):
            self.skipTest("no music engine on this machine to drive")
        out = mt.music_generate(style="warm folk", lyrics="   \n\n   ", engine="yue")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "lyrics_required")
        self.assertFalse(self.posted, "nothing may be posted when there are no words")


class TheEnginesOwnPosition(unittest.TestCase):
    def test_the_sequence_line_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "engine.log"
            p.write_text(ENGINE_LOG, encoding="utf-8")
            pos = mt.stage_progress(p)
        self.assertEqual(pos["stage"], 1, "the newest position wins, not the first one seen")
        self.assertEqual((pos["at"], pos["of"]), (4, 6))
        self.assertIn("section 4 of 6", pos["text"])

    def test_a_finished_stage_two_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "engine.log"
            p.write_text(ENGINE_LOG + "Stage 2: Decoding 2/2, segment 4 out of 6\n", encoding="utf-8")
            pos = mt.stage_progress(p)
        self.assertEqual(pos["stage"], 2)
        self.assertEqual((pos["at"], pos["of"]), (4, 6))

    def test_a_log_with_nothing_to_say_says_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "engine.log"
            p.write_text("loading\n", encoding="utf-8")
            self.assertEqual(mt.stage_progress(p), {})
        self.assertEqual(mt.stage_progress(None), {})
        self.assertEqual(mt.stage_progress(""), {})

    def test_a_missing_log_is_not_an_error(self):
        self.assertEqual(mt.stage_progress(SONGS / "nope" / "engine.log"), {})

    def test_a_running_job_carries_the_position_into_its_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "engine.log"
            p.write_text(ENGINE_LOG, encoding="utf-8")
            saved = music_jobs._running_job
            music_jobs._running_job = lambda: {
                "id": "j1", "state": "running", "status": "rendering", "engine_log": str(p)}
            try:
                st = music_jobs.state()
            finally:
                music_jobs._running_job = saved
        self.assertEqual(st["progress"]["at"], 4)
        self.assertIn("section 4 of 6", st["status"], "the position reaches the panel through status")


if __name__ == "__main__":
    unittest.main()
