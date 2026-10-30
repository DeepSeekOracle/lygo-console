"""Rotate the console's own logs: move logs nothing has written to recently into save/logs/archive/.

Why rotate instead of delete: a log line is evidence. `lygo.envwatch` reads `save/logs/*.log` as the
*live* set, so a fault line from last week's test run holds the strip amber for ever with something
that cannot happen again - but deleting the log destroys the record of what did happen. Rotating
keeps both: the live set stays honest, the history stays readable, and the card counts the archive so
the reader knows it exists (see the "Archived logs" row).

Usage:
    python scripts/rotate_logs.py                  # dry run - says what would move
    python scripts/rotate_logs.py --apply          # move it (default: older than 1 day)
    python scripts/rotate_logs.py --days 7 --apply
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DAYS = 1.0


def _age_days(p: Path) -> float:
    return max(0.0, (time.time() - p.stat().st_mtime) / 86400.0)


def rotate(days: float = DEFAULT_DAYS, apply: bool = False, root: Path | None = None) -> dict[str, object]:
    """Move `save/logs/*.log` older than `days` into `save/logs/archive/`. Never deletes anything.

    A console that is running holds its log open; on Windows a move of an open file raises
    PermissionError, and the honest answer is to leave it alone and say so - not to delete it.
    """
    kit = Path(root) if root else KIT_ROOT
    log_dir = kit / "save" / "logs"
    archive = log_dir / "archive"
    moved: list[tuple[str, float]] = []
    skipped: list[tuple[str, str]] = []
    if not log_dir.is_dir():
        return {"ok": False, "reason": f"no log folder at {log_dir}", "moved": [], "skipped": []}
    for f in sorted(log_dir.glob("*.log")):
        try:
            age = _age_days(f)
        except OSError as exc:
            skipped.append((f.name, f"stat failed ({exc.__class__.__name__})"))
            continue
        if age < days:
            continue
        if not apply:
            moved.append((f.name, age))
            continue
        try:
            archive.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(archive / f.name))
            moved.append((f.name, age))
        except OSError as exc:
            skipped.append((f.name, f"could not move it ({exc.__class__.__name__}: {exc}) - still being written?"))
    return {"ok": True, "moved": moved, "skipped": skipped, "apply": apply, "days": days, "archive": str(archive)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rotate the console's own logs into save/logs/archive/.")
    ap.add_argument("--days", type=float, default=DEFAULT_DAYS, help="age in days before a log is rotated (default 1)")
    ap.add_argument("--root", default="", help="the kit to rotate (default: the kit this script lives in)")
    ap.add_argument("--apply", action="store_true", help="actually move the files (default is a dry run)")
    args = ap.parse_args(argv)

    res = rotate(args.days, args.apply, Path(args.root) if args.root else None)
    if not res["ok"]:
        print("rotate_logs:", res["reason"])
        return 1
    verb = "moved" if args.apply else "would move"
    moved = res["moved"]
    print(f"rotate_logs: {verb} {len(moved)} log(s) older than {args.days:g} day(s) -> {res['archive']}")
    for name, age in moved:
        print(f"  {name:<44} last written {age:.1f} day(s) ago")
    for name, why in res["skipped"]:
        print(f"  LEFT ALONE {name}: {why}")
    if not moved:
        print("  nothing to do: every log in save/logs was written to inside the window")
    if moved and not args.apply:
        print("  (dry run - re-run with --apply to move them; nothing is ever deleted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
