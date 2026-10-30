"""The second engine: declared, wired, and honest about which half it can do.

ACE-Step is the FAST engine on this card - the one that makes a full song in minutes where the YuE
path buys it in hours - so the console has to be able to say three things about it truthfully:

1. **It is installed.** The app, its own python and each of its four weight folders are on disk, found
   by the app's own layout (`checkpoints/<name>`), and the switch reports it as renderable.
2. **A different engine is a different CONTRACT.** ACE-Step takes a song LENGTH and runs one-shot; YuE
   takes sections and tokens and runs as a server with a job queue. Neither may be driven with the
   other's arguments, so the adapter is chosen by name and the slot layout is not shared.
3. **Its progress is its own.** Its CLI writes `@@ACESTEP_PROGRESS@@ {json}` stage lines and one final
   `@@ACESTEP_RESULT@@ {json}`; the panel needs the description and percentage it reports, because a
   render that says nothing for minutes cannot be told from one that hung.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import music_tools as mt  # noqa: E402

APP = Path(r"C:\pinokio\api\acestep.git\app")


def _state(eid: str) -> dict:
    for row in mt.engines_state():
        if row.get("id") == eid:
            return row
    raise AssertionError(f"engine {eid} is not declared")


class TheSecondEngineIsReal(unittest.TestCase):
    def test_it_is_declared_and_this_build_drives_it(self):
        self.assertIn("ace_step", {e.get("id") for e in mt.ENGINES})
        self.assertEqual(mt.ENGINE_ADAPTERS.get("ace_step"), "acestep",
                         "the switch is only real when an adapter exists")
        self.assertTrue(_state("ace_step").get("wired"))

    def test_a_different_engine_is_a_different_contract(self):
        # YuE's own entry is a server; ACE-Step's is a CLI. Driving one with the other's entry is the
        # class of bug this pins: the render would look wired and post a job nothing ever answers.
        yue = _state("yue")
        ace = _state("ace_step")
        self.assertIn("gradio_server.py", str(yue.get("entry")))
        self.assertEqual(ace.get("entry"), "render_cli.py")
        self.assertNotEqual(yue.get("adapter"), ace.get("adapter"))

    def test_when_it_is_installed_it_names_its_own_weights(self):
        row = _state("ace_step")
        if row.get("state") != "installed":
            self.skipTest(f"ACE-Step is not installed on this machine: {row.get('missing')}")
        ids = {str(w.get("id")) for w in (row.get("weights") or [])}
        self.assertIn("acestep-v15-turbo", ids, "the diffusion model is named, not counted")
        self.assertIn("acestep-5Hz-lm-0.6B", ids, "the lyric LM is named too")
        self.assertTrue(all(w.get("present") for w in (row.get("weights") or [])),
                        "installed means every named weight is on disk")

    def test_its_cost_is_stated_in_its_own_measurement(self):
        row = _state("ace_step")
        cost = str(row.get("cost") or "")
        if row.get("state") == "installed":
            self.assertIn("measured", cost.lower(),
                          "the price of a song is the one number this machine can supply")
            self.assertIn("50 s", cost, "the measured render time, not a vendor claim")


class ItsOwnProgress(unittest.TestCase):
    #: The real lines, copied out of a run of its own CLI on this machine.
    REAL = "\n".join([
        '@@ACESTEP_PROGRESS@@ {"stage": "dit_load", "model": "acestep-v15-turbo"}',
        '@@ACESTEP_PROGRESS@@ {"stage": "dit_ready", "ok": true, "seconds": 3.5}',
        '@@ACESTEP_PROGRESS@@ {"stage": "lm_load", "model": "acestep-5Hz-lm-0.6B", "backend": "vllm"}',
        '@@ACESTEP_PROGRESS@@ {"stage": "lm_ready", "ok": true, "seconds": 41.3}',
        '@@ACESTEP_PROGRESS@@ {"stage": "progress", "pct": 51.0, "desc": "Preparing inputs...", '
        '"elapsed_s": 9.1}',
        '@@ACESTEP_PROGRESS@@ {"stage": "generating", "params": {"duration": 30.0, "steps": 8, '
        '"seed": 963}}',
    ])

    def _log(self) -> Path:
        p = Path(__file__).parent / "_ace_progress_fixture.log"
        p.write_text(self.REAL, encoding="utf-8")
        self.addCleanup(lambda: p.unlink(missing_ok=True))
        return p

    def test_the_newest_stage_is_what_is_read(self):
        pos = mt.stage_progress(self._log())
        self.assertEqual(pos["ace_stage"], "generating")
        self.assertIn("30 s of audio", pos["text"])
        self.assertIn("8 steps", pos["text"])

    def test_the_percentage_it_reports_is_carried_through(self):
        # A render that reports only "progress" for minutes is indistinguishable from one that hung.
        pos = mt._acestep_progress(
            '@@ACESTEP_PROGRESS@@ {"stage": "progress", "pct": 51.0, "desc": "Preparing inputs..."}')
        self.assertEqual(pos["pct"], 51.0)
        self.assertIn("Preparing inputs", pos["text"])
        self.assertIn("51%", pos["text"])

    def test_a_stage_with_nothing_to_say_is_still_named(self):
        pos = mt._acestep_progress('@@ACESTEP_PROGRESS@@ {"stage": "lm_load"}')
        self.assertIn("lyric model", pos["text"])

    def test_junk_is_not_mistaken_for_progress(self):
        self.assertEqual(mt._acestep_progress(""), {})
        self.assertEqual(mt._acestep_progress("@@ACESTEP_PROGRESS@@ not json"), {})
        self.assertEqual(mt.stage_progress(None), {})

    def test_the_result_line_is_json_a_caller_can_read(self):
        line = ('@@ACESTEP_RESULT@@ {"ok": true, "audios": [{"path": "x.wav"}], "measured": '
                '[{"seconds": 30.0}]}')
        payload = json.loads(line.split("@@ACESTEP_RESULT@@ ", 1)[1])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["measured"][0]["seconds"], 30.0)


if __name__ == "__main__":
    unittest.main()
