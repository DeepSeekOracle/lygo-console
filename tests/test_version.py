"""The release number is ONE file, and everything that shows a version reads it.

The rule these tests pin: bumping the release is a one-line edit.

Before this, the release was stamped in three places - ``server.BUILD`` (``v1.1-20260917api2``),
``runtime_facts._FALLBACK_BUILD`` (the same string again) and the portal's own ``LYGO_BUILD`` const
(``1.1.0``) - so the console header, ``/api/health`` and the facts handed to the model could disagree
about which build the operator was actually running, and a bump was three edits someone had to remember
together. Now ``VERSION`` is the source; the rest derive from it, and ``refresh_manifests.py`` stamps the
manifest from the same file.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import version  # noqa: E402

OLD_STAMP = "v1.1-20260917api2"


class VersionFileTests(unittest.TestCase):
    def test_the_file_holds_a_release_on_line_one(self):
        lines = version.VERSION_FILE.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines, "VERSION is empty")
        self.assertRegex(lines[0].strip(), r"^\d+\.\d+\.\d+$", "line 1 must be X.Y.Z")

    def test_release_and_stamp_come_from_that_file(self):
        lines = version.VERSION_FILE.read_text(encoding="utf-8").splitlines()
        self.assertEqual(version.release(), lines[0].strip())
        self.assertEqual(version.stamp(), "v" + version.release())

    def test_a_bump_in_the_file_moves_everything(self):
        """The whole point: edit one line and release()/stamp()/tag() follow, nothing else to change."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "VERSION"
            fake.write_text("9.9.9\nnext-release test\n", encoding="utf-8")
            original, version.VERSION_FILE = version.VERSION_FILE, fake
            try:
                self.assertEqual(version.release(), "9.9.9")
                self.assertEqual(version.stamp(), "v9.9.9")
                self.assertEqual(version.tag(), "next-release test")
            finally:
                version.VERSION_FILE = original

    def test_a_missing_or_odd_file_falls_back_instead_of_crashing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            original, version.VERSION_FILE = version.VERSION_FILE, Path(tmp) / "nope"
            try:
                self.assertEqual(version.release(), version.FALLBACK_RELEASE)
                self.assertEqual(version.stamp(), "v" + version.FALLBACK_RELEASE)
                self.assertEqual(version.tag(), "")
            finally:
                version.VERSION_FILE = original
            odd = Path(tmp) / "VERSION"
            odd.write_text("not-a-version\n", encoding="utf-8")
            original, version.VERSION_FILE = version.VERSION_FILE, odd
            try:
                self.assertEqual(version.release(), version.FALLBACK_RELEASE)
            finally:
                version.VERSION_FILE = original


class EverySurfaceCarriesItTests(unittest.TestCase):
    def test_server_takes_its_build_from_version(self):
        src = (ROOT / "src" / "server.py").read_text(encoding="utf-8")
        self.assertIn("BUILD = version.stamp()", src)
        self.assertNotIn(OLD_STAMP, src, "the hard-coded stamp is back - a bump would not move /api/health")
        # The health answer now routes every live probe through health_payload's safe() wrapper (defect
        # 34: the route may degrade one field, never 500). The owner of the release string is unchanged.
        self.assertIn('"release": safe("release", _version.release, "")', src)
        self.assertIn('"release_tag": safe("release_tag", _version.tag, "")', src)

    def test_runtime_facts_tells_the_model_the_same_release(self):
        src = (ROOT / "src" / "runtime_facts.py").read_text(encoding="utf-8")
        self.assertIn("_FALLBACK_BUILD = version.stamp()", src)
        self.assertNotIn(OLD_STAMP, src)

    def test_the_portal_header_shows_what_the_console_serves(self):
        js = (ROOT / "portal" / "app.js").read_text(encoding="utf-8")
        self.assertIn("servedBuild", js, "the header went back to printing only its own const")
        self.assertIn("const stamp = servedBuild || BUILD_STAMP;", js)
        self.assertRegex(js, r"if \(j && j\.build\) \{ servedBuild = String\(j\.build\); paintBrand\(\); \}")

    def test_the_portals_fallback_const_cannot_drift_from_VERSION(self):
        js = (ROOT / "portal" / "app.js").read_text(encoding="utf-8")
        m = re.search(r'const LYGO_BUILD = "([^"]+)"', js)
        self.assertIsNotNone(m, "the portal's fallback const is gone")
        self.assertEqual(m.group(1), version.release(),
                         "portal fallback %s != VERSION %s - bump both or neither"
                         % (m.group(1), version.release()))

    def test_the_manifest_is_stamped_from_the_same_file(self):
        mf = json.loads((ROOT / "BUILD_MANIFEST.json").read_text(encoding="utf-8"))
        if mf.get("build") in (None, ""):
            self.skipTest("manifest carries no build field")
        self.assertEqual(mf.get("build"), version.stamp(),
                         "manifest build %r != VERSION %s - run scripts/refresh_manifests.py --note "
                         "\"<why>\" as part of the release" % (mf.get("build"), version.release()))
        self.assertEqual(mf.get("release"), version.release())

    def test_the_build_notes_section_exists_for_this_release(self):
        notes = (ROOT / "BUILD_NOTES.md").read_text(encoding="utf-8")
        self.assertIn("## %s " % version.release(), notes,
                      "add the release section to BUILD_NOTES.md - the notes are the release record")


if __name__ == "__main__":
    unittest.main()
