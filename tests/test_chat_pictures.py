"""Generated pictures must be viewable in the chat bubble, not only as a path string."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import media_tools as mt  # noqa: E402

# a real 1x1 PNG: the lookup only ever checks extension + presence, but a real file keeps this test
# honest if that ever changes
_ONE_PIXEL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d4944415478da63f8cfc000000301010018dd8db0"
    "0000000049454e44ae426082")


class WorkspaceImagePathTests(unittest.TestCase):
    def test_a_real_generated_png_is_found_by_basename(self):
        """Proved against a picture this test writes, not one a previous session happened to make.

        The earlier version named a real file from the live workspace, so it passed only on the
        machine that generated it and failed on the stick copy and in any fresh install - where
        workspace media is excluded on purpose. A suite that is red on the copy cannot sign the
        copy off, so the fixture is created here and removed again.
        """
        images = mt.WORKSPACE / "images"
        images.mkdir(parents=True, exist_ok=True)
        name = "gen-selftest-000000.png"
        target = images / name
        target.write_bytes(_ONE_PIXEL_PNG)
        try:
            p = mt.workspace_image_path(name)
            self.assertIsNotNone(p)
            self.assertTrue(p.is_file())
            self.assertEqual(p.name, name)
        finally:
            target.unlink(missing_ok=True)

    def test_path_traversal_is_refused(self):
        self.assertIsNone(mt.workspace_image_path("../SOUL.md"))
        self.assertIsNone(mt.workspace_image_path("..\\SOUL.md"))
        self.assertIsNone(mt.workspace_image_path("/etc/passwd"))

    def test_a_non_image_is_refused(self):
        self.assertIsNone(mt.workspace_image_path("upload.bin"))
        self.assertIsNone(mt.workspace_image_path(""))


class PortalShowsThePictureTests(unittest.TestCase):
    def test_the_page_fetches_the_sandbox_image_route(self):
        js = (ROOT / "portal" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/media/image/", js)
        self.assertIn("function finishBubblePictures", js)
        self.assertIn("function pictureNames", js)
        self.assertIn("bubble-pic", js)
        css = (ROOT / "portal" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".bubble-pic", css)

    def test_the_server_owns_the_media_route(self):
        src = (ROOT / "src" / "server.py").read_text(encoding="utf-8")
        self.assertIn('/api/media/image/', src)
        self.assertIn("workspace_image_path", src)


if __name__ == "__main__":
    unittest.main()
