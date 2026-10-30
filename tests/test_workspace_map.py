from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import workspace_map  # noqa: E402
from admin_map import read_roots  # noqa: E402
from workspace_map import add_mount, list_mounts, remove_mount  # noqa: E402


class WorkspaceMapTests(unittest.TestCase):
    def setUp(self) -> None:
        """Mount into a throwaway map, never the operator's live save/workspace_map.json.

        The test used to add and remove a mount in the real map: two concurrent runs interleaved
        on one file, and a run that died mid-test left a dead temp path mounted (defect D30).
        """
        self._td = tempfile.TemporaryDirectory(prefix="lygo_wsm_")
        self.addCleanup(self._td.cleanup)
        patcher = patch.object(workspace_map, "MAP_PATH", Path(self._td.name) / "workspace_map.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_add_remove_temp(self):
        with tempfile.TemporaryDirectory() as td:
            r = add_mount(td, read=True, write=True, search=True, label="tmp")
            self.assertTrue(r.get("ok"), r)
            live = [m["path"] for m in list_mounts().get("live") or []]
            self.assertTrue(any(Path(td).resolve().as_posix().lower() in Path(x).as_posix().lower() or str(Path(td).resolve()) in x for x in live))
            roots = [str(p) for p in read_roots()]
            self.assertTrue(any(str(Path(td).resolve()) in x for x in roots))
            gone = remove_mount(td)
            self.assertTrue(gone.get("ok"))
            roots2 = [str(p) for p in read_roots()]
            self.assertFalse(any(str(Path(td).resolve()) == x for x in roots2))

    def test_portal_has_mount_ui(self):
        html = (ROOT / "portal" / "index.html").read_text(encoding="utf-8")
        self.assertIn("ws-mounts", html)
        self.assertIn("Add access", html)


if __name__ == "__main__":
    unittest.main()
