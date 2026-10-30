"""The photo-to-mesh limb stays inside the workspace and refuses a path it should not read."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import mesh_tools as mesh  # noqa: E402


class MeshPathTests(unittest.TestCase):
    def test_a_mesh_name_cannot_leave_the_folder(self):
        self.assertIsNone(mesh.workspace_mesh_path("../secret.glb"))
        self.assertIsNone(mesh.workspace_mesh_path("notes.txt"))
        self.assertIsNone(mesh.workspace_mesh_path(""))

    def test_resolution_and_ratio_stay_in_range(self):
        self.assertEqual(mesh._resolution("nope"), 192)
        self.assertEqual(mesh._resolution(9999), 256)
        self.assertEqual(mesh._resolution(8), 64)
        self.assertAlmostEqual(mesh._foreground("nope"), 0.85)
        self.assertAlmostEqual(mesh._foreground(0.1), 0.5)
        self.assertAlmostEqual(mesh._foreground(2), 1.0)
        self.assertEqual(mesh._ratio(0), 0.15)
        self.assertEqual(mesh._ratio(4), 1.0)

    def test_a_system_file_is_not_a_photo(self):
        got = mesh.mesh_from_image(r"C:\Windows\win.ini")
        self.assertFalse(got.get("ok"))
        self.assertIn(got.get("error"), {"outside_read_roots", "denied_path", "not_image", "missing"})

    def test_status_names_the_engine_without_starting_it(self):
        got = mesh.mesh_status()
        self.assertTrue(got.get("ok"))
        self.assertEqual(got.get("engine"), "TripoSR")
        self.assertEqual(got.get("modly"), "https://github.com/lightningpixel/modly")
        self.assertIn("Lightning Pixel", got.get("credit") or "")
        self.assertIn("ready", got)
        self.assertIsInstance(got.get("ready"), bool)

    def test_decimate_refuses_a_name_that_is_not_here(self):
        got = mesh.mesh_decimate("not-a-real-mesh.glb")
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("error"), "missing")


if __name__ == "__main__":
    unittest.main()
