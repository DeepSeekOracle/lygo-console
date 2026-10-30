"""Image limb path policy: read through the mapped roots, never silently rewrite a path.

The bug this covers: image_info had its own private workspace guard. Given an absolute path outside
the kit it swapped in WORKSPACE/<name> and then answered "missing", so a real picture on another
drive (already mapped in workspace_map) looked absent. The model could only report that the file
does not exist - and with the LYGO ALIGN honesty rule that becomes "UNKNOWN (SHADOW) - the
specified image file path does not exist", repeated on every turn.

Rules under test:
- a workspace path keeps working (relative paths resolve under the workspace);
- a path outside every read root is refused WITH A REASON (outside_read_roots / denied_path), and
  never as a bare "missing" - the policy must be visible in the answer, not disguised as absence;
- a path inside a mapped read root resolves and reads (hermetic: the root is injected here).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import image_tools  # noqa: E402

# Minimal PNG: magic + IHDR chunk header + 1024x1536, enough for the header reader.
PNG_1024x1536 = (
    bytes((137, 80, 78, 71, 13, 10, 26, 10))
    + bytes((0, 0, 0, 13))
    + b"IHDR"
    + bytes((0, 0, 4, 0))
    + bytes((0, 0, 6, 0))
    + bytes((8, 6, 0, 0, 0))
)


class ImagePathPolicyTest(unittest.TestCase):
    def test_workspace_path_still_resolves_and_reports_missing(self):
        got = image_tools.image_info("images/definitely-not-here.png")
        self.assertFalse(got["ok"])
        self.assertEqual(got["error"], "missing")
        self.assertTrue(str(got["path"]).lower().startswith(str(ROOT).lower()))

    def test_outside_every_root_is_refused_with_a_reason_not_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "picture.png"
            outside.write_bytes(PNG_1024x1536)
            got = image_tools.image_info(str(outside))
        self.assertFalse(got["ok"])
        self.assertIn(got["error"], {"outside_read_roots", "denied_path"})
        self.assertNotEqual(got["error"], "missing",
                            "an unmapped path must be refused by policy, not disguised as absent")
        self.assertIn("read_roots", got, "the refusal must say where reads are allowed")

    def test_mapped_read_root_is_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pic = root / "mapped.png"
            pic.write_bytes(PNG_1024x1536)
            real_read_roots = image_tools.read_roots
            image_tools.read_roots = lambda: (root,)
            try:
                got = image_tools.image_info(str(pic))
            finally:
                image_tools.read_roots = real_read_roots
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["kind"], "png")
        self.assertEqual((got["width"], got["height"]), (1024, 1536))

    def test_credential_tree_stays_denied(self):
        got = image_tools.image_info("I:" + chr(92) + "LYGO_SERVER_KEYS" + chr(92) + "lygo.pass")
        self.assertFalse(got["ok"])
        self.assertEqual(got["error"], "denied_path")


if __name__ == "__main__":
    unittest.main()
