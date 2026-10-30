"""Attachments: a photo or a file the operator picks has to reach the agent.

Two ways in, and each needs a different path to work:

  * img  - the picture rides the message itself as an ``image_url`` part, which the engine's own
           projector reads (llama-server --mmproj). No limb call, nothing to lose between turns.
  * file - text is inlined into the message; anything else is written under ``workspace/uploads/`` so
           the agent opens it with ``read_file``. That path MUST sit in a root its own limbs cover, or
           the button hands the agent a file it cannot read.

Both used to fail silently: the picker held the file with no sign of it on screen, a photo over 4 MB was
refused by the chat body limit as a silent 413, and a picture attached as a *file* was a bare path with no
limb in the local schema able to look at it.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


class VisionLimbTests(unittest.TestCase):
    def test_the_local_agent_can_look_at_a_picture_file(self):
        """Without the vision limbs in the LOCAL schema, a photo attached as a file is a path the agent
        can only apologise about: core_schema is what the local brain is actually given."""
        import sys

        sys.path.insert(0, str(ROOT / "src"))
        from tools import core_schema

        names = [t["function"]["name"] for t in core_schema()]
        self.assertIn("image_see", names, "the local schema must be able to look at a picture file")
        self.assertIn("image_info", names)


class UploadRouteTests(unittest.TestCase):
    def test_the_upload_route_writes_where_the_limbs_can_read(self):
        src = _src("src/server.py")
        self.assertIn('path == "/api/upload"', src)
        self.assertIn('WORKSPACE / "uploads"', src, "uploads must land in a root the limbs already cover")
        self.assertIn("read_file limb", src, "the answer must tell the agent how to open it")

    def test_a_filename_cannot_climb_out_of_the_upload_directory(self):
        """The name arrives from the browser, so it is cut to its last segment before it is used."""
        src = _src("src/server.py")
        self.assertIn('name.replace("\\\\", "/").split("/")[-1]', src)
        self.assertIn('name.replace("\\\\", "/").split("/")[-1]', src, "no traversal by design")

    def test_the_chat_body_fits_a_photo(self):
        """A photo arrives as base64 inside the chat JSON: at 1.3x its size, the old 4 MB limit turned
        'attach a photo' into a silent 413 on the operator."""
        src = _src("src/server.py")
        self.assertIn("self._read_body(12 * 1024 * 1024)", src)


class ComposerTests(unittest.TestCase):
    def test_both_pickers_exist_and_what_is_attached_is_visible(self):
        html = _src("portal/index.html")
        self.assertIn('id="img"', html)
        self.assertIn('id="file"', html)
        self.assertIn('id="attach-strip"', html, "an attachment the operator cannot see is one they will re-pick")
        js = _src("portal/app.js")
        self.assertIn("function renderAttach()", js)
        self.assertIn("function syncAttach()", js)
        self.assertIn("{ type: \"image_url\", image_url: { url: a.dataUrl } }", js)

    def test_a_photo_picked_through_the_file_button_still_travels_as_a_photo(self):
        js = _src("portal/app.js")
        block = js[js.index("function addFile(f) {"):]
        block = block[: block.index("\n  function ", 10)]
        self.assertIn("addImageFile(f)", block,
                      "a photo chosen with the file button must not become a bare path the agent cannot see")

    def test_text_files_are_inlined_and_everything_else_is_saved(self):
        js = _src("portal/app.js")
        self.assertIn('fetch("/api/upload"', js)
        self.assertIn("its contents follow", js, "a small text file should ride the message itself")
        # The path rides its own line, because this kit's folder is "<EDRIVE>\\..." and the space
        # inside it truncates any inline path pattern (chat_loop.PATH_LINE reads a whole line).
        self.assertIn("is saved in the workspace as:", js)
        self.assertIn("pen it with the read_file limb before you answer, and say plainly if you cannot", js)

    def test_photos_are_shrunk_before_they_are_sent(self):
        """A phone photo is 3-8 MB; downscaling here is the difference between an attachment that flows
        and one that times out on a mid machine."""
        js = _src("portal/app.js")
        self.assertIn("IMG_MAX_PX", js)
        self.assertIn("drawImage(", js)
        self.assertIn('toDataURL("image/jpeg"', js)


if __name__ == "__main__":
    unittest.main()
