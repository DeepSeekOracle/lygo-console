"""Guards on the config-path repair.

repair_paths rewrites drive-anchored roots in config/admin.json when the kit moves. Two ways it
used to damage a copy, both silent, both covered here:

1. It remapped any origin-drive path to {drive} without checking that the result exists, so a hub
   on D: got D:\\E Drive\\LYRA LOCAL while the real folder stayed on I: - a read root that resolves
   nowhere and simply yields no files.
2. On a second pass the stamped origin drive becomes the kit's own drive, at which point every
   path on it looks like an origin path: C:\\Users\\<user>\\Pictures was rewritten to
   {drive}:\\Users\\<user>\\Pictures - a path that exists but is not what the steward configured.

Both must be refused while genuine kit-owned paths still move.
"""
import json
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import repair_paths  # noqa: E402


def _ctx(origin_drive: str) -> dict:
    """Build the context with the module's own builder so it always carries every key.

    Hand-rolling the dict breaks whenever the module grows a key (it needs path_keys_only too).
    """
    kit = str(repair_paths.KIT_ROOT)
    data = {
        "path_policy": {
            "origin_drive": origin_drive,
            "origin_kit_root": "<EDRIVE>\\lygo-protocol-stack\\lygo_llm_console",
            "origin_stack_root": "<EDRIVE>\\lygo-protocol-stack",
        }
    }
    ctx = repair_paths._ctx_for(data, json.dumps(data), Path(kit) / "config" / "admin.json")
    ctx["origin_drive"] = origin_drive.upper()
    return ctx


class RepairGuards(unittest.TestCase):
    def setUp(self):
        self._saved = repair_paths.KIT_ROOT
        # Worst case for the guards: the kit lives on the same drive as the host folders.
        repair_paths.KIT_ROOT = Path("C:/kit_copy")

    def tearDown(self):
        repair_paths.KIT_ROOT = self._saved

    def test_host_folders_are_never_adopted(self):
        ctx = _ctx("C")
        for value in ("<USER>\\Pictures",
                      "<USER>\\Documents",
                      "C:\\Windows\\System32",
                      "C:\\Program Files\\Some App"):
            new, action, reason = repair_paths.rewrite_value(value, "read_roots", ctx)
            self.assertIsNone(new, f"{value} must not be rewritten, got {new}")
            self.assertEqual(action, "keep", f"{value}: {reason}")

    def test_unresolvable_remap_keeps_the_original(self):
        # The origin path exists on the origin machine, not here: keeping it beats writing a
        # drive-relative path that resolves to nothing.
        new, action, reason = repair_paths.rewrite_value("<EDRIVE>\\LYRA LOCAL", "read_roots", _ctx("I"))
        self.assertIsNone(new)
        self.assertEqual(action, "keep")
        self.assertIn("does not exist here", reason)

    def test_kit_owned_path_still_follows_the_drive(self):
        # Two legitimate rewrites: a path inside the kit uses the {kit} token; a folder on the
        # kit's drive but outside it follows the drive. Neither is a host folder, so neither is
        # caught by the guards - the repair must still move them.
        inside = Path("C:/kit_copy/workspace")
        neighbour = Path("C:/kit_neighbour")
        inside.mkdir(parents=True, exist_ok=True)
        neighbour.mkdir(parents=True, exist_ok=True)
        try:
            new, action, _ = repair_paths.rewrite_value(str(inside), "read_roots", _ctx("C"))
            self.assertEqual(new, "{kit}\\workspace",
                             "a path inside the kit should use the portable {kit} token")
            self.assertEqual(action, "token", "kit-relative rewrites report the 'token' action")
            new2, action2, _ = repair_paths.rewrite_value(str(neighbour), "read_roots", _ctx("C"))
            self.assertEqual(new2, "{drive}:\\kit_neighbour")
            self.assertEqual(action2, "remap")
        finally:
            for p in (inside, Path("C:/kit_copy"), neighbour):
                try:
                    p.rmdir()
                except OSError:
                    pass

    def test_portable_values_are_left_alone(self):
        ctx = _ctx("I")
        for value in ("{kit}\\save", "{workspace}", "{stack}", "{usb}\\docs", "%USERPROFILE%\\.ollama\\models"):
            new, action, _ = repair_paths.rewrite_value(value, "read_roots", ctx)
            self.assertIsNone(new, f"{value} should not be rewritten")
            self.assertEqual(action, "keep")


if __name__ == "__main__":
    unittest.main()
