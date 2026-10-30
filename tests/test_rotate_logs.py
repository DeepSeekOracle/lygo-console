"""Pin the log rotation: it protects the honest reading of `save/logs/`, and it never deletes.

`lygo.envwatch` reads `save/logs/*.log` as the live set, so a fault line from an old test run would
hold the strip amber for ever over something that cannot happen again. Rotation fixes that without
destroying the record, and these tests hold both halves: the old log leaves the live set, the new one
stays, nothing is ever unlinked, and an open log is left alone rather than removed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = KIT_ROOT / "scripts" / "rotate_logs.py"


def _fake_kit() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="lygo_rotate_"))
    (tmp / "save" / "logs").mkdir(parents=True)
    return tmp


def _write_log(p: Path, age_days: float) -> None:
    p.write_text("[test] a line\n", encoding="utf-8")
    when = time.time() - age_days * 86400.0
    os.utime(p, (when, when))


class RotateLogsTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60)

    def test_a_dry_run_reports_and_moves_nothing(self) -> None:
        kit = _fake_kit()
        _write_log(kit / "save" / "logs" / "old.log", 3.0)
        _write_log(kit / "save" / "logs" / "fresh.log", 0.01)
        r = self.run_cli("--root", str(kit))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would move 1", r.stdout)
        self.assertTrue((kit / "save" / "logs" / "old.log").is_file(), "a dry run moved a file")
        self.assertFalse((kit / "save" / "logs" / "archive").exists(), "a dry run created the archive")

    def test_apply_moves_the_old_log_and_leaves_the_live_one(self) -> None:
        kit = _fake_kit()
        _write_log(kit / "save" / "logs" / "old.log", 3.0)
        _write_log(kit / "save" / "logs" / "fresh.log", 0.01)
        r = self.run_cli("--root", str(kit), "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((kit / "save" / "logs" / "archive" / "old.log").is_file())
        self.assertFalse((kit / "save" / "logs" / "old.log").exists(), "the rotated log is still in the live set")
        self.assertTrue((kit / "save" / "logs" / "fresh.log").is_file(), "the live log was rotated away")

    def test_it_never_deletes_anything(self) -> None:
        """The whole point: evidence moves, it does not disappear."""
        kit = _fake_kit()
        _write_log(kit / "save" / "logs" / "old.log", 9.0)
        before = (kit / "save" / "logs" / "old.log").read_bytes()
        self.run_cli("--root", str(kit), "--days", "1", "--apply")
        after = (kit / "save" / "logs" / "archive" / "old.log").read_bytes()
        self.assertEqual(before, after, "the rotated log's bytes changed")
        self.assertEqual(sorted(q.name for q in (kit / "save" / "logs").glob("*.log")), [], "a live log survived")

    def test_a_kit_with_no_log_folder_says_so_instead_of_crashing(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="lygo_rotate_nolog_"))
        r = self.run_cli("--root", str(tmp))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no log folder", r.stdout + r.stderr)

    def test_the_threshold_is_the_flag_not_a_guess(self) -> None:
        kit = _fake_kit()
        _write_log(kit / "save" / "logs" / "two_days.log", 2.0)
        r = self.run_cli("--root", str(kit), "--days", "7", "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((kit / "save" / "logs" / "two_days.log").is_file(), "rotated inside the window")
        r2 = self.run_cli("--root", str(kit), "--days", "1", "--apply")
        self.assertIn("moved 1", r2.stdout)


if __name__ == "__main__":
    unittest.main()
