"""The song studio panel's own assertions, run under node: the half of the studio no Python test sees.

The panel decides what the operator is told, and it is where a finished song was reported as "nothing
here is claimed as finished" — its fixtures carried the song as an object while the server sends a
path string, so the panel's `j.song.name` was always undefined. The harness now feeds the real wire
shapes, and it goes red on the old logic (verified: 5 failures).

Skipped, not failed, where node is not installed: this kit runs without it.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "js" / "music_studio_panel.js"


@unittest.skipUnless(shutil.which("node"), "node is not installed: the panel's own test cannot run")
class SongStudioPanelTest(unittest.TestCase):
    """One harness run, four scenarios: the happy path, no engine, a refused GET, a refused POST."""

    def test_the_panel_stands_behind_what_it_says(self) -> None:
        out = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, timeout=240)
        if out.returncode != 0 or "TOTAL FAILURES" not in out.stdout:
            self.fail("the panel harness did not finish:\n" + out.stdout[-3000:] + out.stderr[-2000:])
        self.assertNotIn("  FAIL  ", out.stdout, "panel assertions failed:\n" + out.stdout[-3000:])
        self.assertIn("TOTAL FAILURES: 0", out.stdout)
        self.assertIn("[4] ALL PASS", out.stdout, "the refusal-with-remedy scenario did not pass")


if __name__ == "__main__":
    unittest.main()
