"""Brand, license and integrity tests.

The license package is part of the product now, so it is tested like the product:
- the package must be present and must be the v3.0 text (not the MIT text again);
- the brand surfaces must carry the notice and the terms;
- BRAND_MANIFEST.json and SESSIONS_MANIFEST.json must match the files on disk, so an
  edit that forgets to refresh the manifest is caught here rather than shipped;
- scripts/certify_build.py must say CERTIFIED on a clean copy and MODIFIED on a tampered
  one (run in a sandbox: the real kit is never modified by this file).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIGNATURE = "\u03949\u03a6963-LICENSE-v3.0"
BRAND_SIGNATURE = "\u03949\u03a6963-LYGO-BRANDING-v1"
PACKAGE = ["LICENSE", "NOTICE", "TRADEMARKS.md", "LICENSING.md", "SUCCESSION.md"]

# Line endings are a checkout detail, not a build change (see scripts/certify_build.py).
CRLF = bytes((13, 10))
LF = bytes((10,))


def sha16(path: Path) -> str:
    """The identity scripts/certify_build.py computes: sha256 over LF-normalised bytes.

    Line endings are a checkout detail (core.autocrlf rewrites text files to CRLF on Windows
    checkouts), so a raw-byte hash would call a pristine copy drifted.
    """
    data = path.read_bytes().replace(CRLF, LF)
    return hashlib.sha256(data).hexdigest()[:16]


class LicensePackageTest(unittest.TestCase):
    def test_package_files_exist(self):
        for name in PACKAGE:
            with self.subTest(name=name):
                p = ROOT / name
                self.assertTrue(p.is_file(), f"{name} missing from the kit root")
                self.assertGreater(p.stat().st_size, 200, f"{name} looks like a stub")

    def test_license_is_v3_and_not_permissive(self):
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn(SIGNATURE, text)
        self.assertIn("LYGO Sovereign License v3.0", text)
        # The MIT grant must not reappear: that is exactly what v3.0 replaced.
        self.assertNotIn("Permission is hereby granted, free of charge", text)
        self.assertNotIn("without restriction, including without limitation the rights", text)
        for clause in ["rebrand", "Resale", "SUCCESSION", "IRREVOCABLE", "Marks"]:
            self.assertIn(clause, text, f"LICENSE lost the {clause!r} clause")

    def test_license_protects_succession(self):
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Successor Steward", text)
        self.assertIn("no estate", text.lower())
        self.assertIn("moral rights", text.lower())

    def test_notice_keeps_third_party_and_integrity(self):
        text = (ROOT / "NOTICE").read_text(encoding="utf-8")
        self.assertIn("OpenClaw", text)
        self.assertIn("MIT License", text)          # vendored upstream stays MIT
        self.assertIn("Certified", text)
        self.assertIn("canonical", text.lower())

    def test_trademarks_policy_names_the_marks(self):
        text = (ROOT / "TRADEMARKS.md").read_text(encoding="utf-8")
        for mark in ["LYGO CLAW", "PC LOCAL CONSOLE", "\u03949\u03a6963", "Powered by"]:
            self.assertIn(mark, text, f"brand policy does not cover {mark!r}")
        self.assertIn("unofficial fork", text)

    def test_plain_english_page_matches_the_license(self):
        text = (ROOT / "LICENSING.md").read_text(encoding="utf-8")
        self.assertIn("not the license", text.lower())
        self.assertIn("No license is retroactive", text)
        self.assertIn("Successor", text)

    def test_read_disclaimer_shows_terms(self):
        text = (ROOT / "READ_DISCLAIMER_FIRST.md").read_text(encoding="utf-8")
        self.assertIn("Sovereign License v3.0", text)
        self.assertIn("not open source", text.lower())


class BrandSurfaceTest(unittest.TestCase):
    def test_portals_carry_brand_and_terms(self):
        for rel in ("portal/index.html", "web_portal/index.html"):
            with self.subTest(page=rel):
                html = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
                self.assertIn("LYGO Sovereign License v3.0", html)
                self.assertIn("Justin Helmer", html)
                self.assertIn("LICENSING.md", html)
                lowered = html.lower()
                self.assertTrue("no resale" in lowered or "no selling" in lowered
                                or "not permitted" in lowered)


class ManifestIntegrityTest(unittest.TestCase):
    def _check(self, manifest_name: str, expected_signature: str | None = None):
        p = ROOT / manifest_name
        self.assertTrue(p.is_file(), f"{manifest_name} missing")
        data = json.loads(p.read_text(encoding="utf-8"))
        if expected_signature:
            self.assertEqual(data.get("signature"), expected_signature)
        rows = data.get("files") or []
        self.assertTrue(rows, f"{manifest_name} lists no files")
        checked = 0
        for row in rows:
            target = ROOT / str(row["path"])
            if not target.is_file():
                self.fail(f"{manifest_name} lists a missing file: {row['path']}")
            self.assertEqual(sha16(target), row["sha256_16"],
                             f"{row['path']} drifted from {manifest_name} - refresh the manifest")
            normalised = len(target.read_bytes().replace(CRLF, LF))
            self.assertIn(row["bytes"], (normalised, target.stat().st_size),
                          f"{row['path']} changed size vs {manifest_name}")
            checked += 1
        self.assertGreater(checked, 0)

    def test_brand_manifest_matches_disk(self):
        self._check("BRAND_MANIFEST.json", BRAND_SIGNATURE)

    def test_sessions_manifest_matches_disk(self):
        self._check("SESSIONS_MANIFEST.json")


class CertifyToolTest(unittest.TestCase):
    def _run(self, cwd: Path):
        return subprocess.run(
            [sys.executable, "scripts/certify_build.py"],
            cwd=str(cwd), capture_output=True, text=True, timeout=180,
        )

    def test_clean_copy_is_certified(self):
        proc = self._run(ROOT)
        self.assertIn("CERTIFIED", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_edited_license_is_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            (sandbox / "scripts").mkdir()
            for name in PACKAGE:
                shutil.copy2(ROOT / name, sandbox / name)
            shutil.copy2(ROOT / "scripts" / "certify_build.py",
                         sandbox / "scripts" / "certify_build.py")
            rows = [{"path": n, "sha256_16": sha16(sandbox / n),
                     "bytes": (sandbox / n).stat().st_size} for n in PACKAGE]
            (sandbox / "BRAND_MANIFEST.json").write_text(
                json.dumps({"signature": BRAND_SIGNATURE, "files": rows}, indent=2),
                encoding="utf-8")
            with open(sandbox / "LICENSE", "a", encoding="utf-8") as fh:
                fh.write("\n\n(rebranded by a fork, terms removed)\n")
            proc = self._run(sandbox)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("MODIFIED", proc.stdout)
            self.assertIn("LICENSE", proc.stdout)

    def test_stripped_package_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            (sandbox / "scripts").mkdir()
            shutil.copy2(ROOT / "scripts" / "certify_build.py",
                         sandbox / "scripts" / "certify_build.py")
            proc = self._run(sandbox)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("UNCERTIFIED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
