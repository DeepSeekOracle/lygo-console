"""The archive band's own assertions, run under node: the half of it no Python test sees.

The band is where the operator meets the archive, and it is where the three rules that only exist in
the browser half have to hold: an archived page is inserted as TEXT (somebody else's page is data, not
code), a search that could not run is never painted as a search that found nothing, and a field
written from a result announces itself the way a keystroke would.

The harness runs the shipped `portal/archive.js` itself - not a copy - against a stub DOM and a stub
wire, so a change to the band is what this test sees. Skipped, not failed, where node is not
installed: this kit runs without it.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "js" / "archive_panel.js"


@unittest.skipUnless(shutil.which("node"), "node is not installed: the band's own test cannot run")
class ArchiveBandTest(unittest.TestCase):
    """One harness run, eight scenarios: the read-out, a search, a busy index, a read, as-of,
    an empty ask, clear/undo, and a console that is not there."""

    def test_the_band_stands_behind_what_it_says(self) -> None:
        out = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, timeout=240)
        if out.returncode != 0 or "TOTAL FAILURES" not in out.stdout:
            self.fail("the band harness did not finish:\n" + out.stdout[-3000:] + out.stderr[-2000:])
        self.assertNotIn("  FAIL  ", out.stdout, "band assertions failed:\n" + out.stdout[-3000:])
        self.assertIn("TOTAL FAILURES: 0", out.stdout)
        for marker in ("[1] ALL PASS", "[4] ALL PASS", "[6] ALL PASS", "[8] ALL PASS"):
            self.assertIn(marker, out.stdout, f"scenario {marker} did not pass")


if __name__ == "__main__":
    unittest.main()
