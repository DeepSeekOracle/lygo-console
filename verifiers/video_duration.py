"""Video duration verifier for the autonomous runtime.

A file that exists is not a film: a killed render leaves a few hundred bytes of header, and a mistyped
`--seconds` leaves a film of the wrong length. This checks the container's real duration with ffprobe
and requires a plausible size as well, so "the file is there" cannot pass for "the shot was rendered".

    {"type": "custom_verifier", "name": "video_duration",
     "args": {"path": "workspace/video/x/film.mp4", "min_s": 14.0, "max_s": 16.5, "min_bytes": 100000}}
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def verify(args: dict | None = None, base_dir: str | None = None, **_kwargs) -> bool:
    args = args or {}
    raw = str(args.get("path") or "").strip()
    if not raw:
        return False
    path = Path(raw)
    if not path.is_absolute() and base_dir:
        path = Path(base_dir) / raw
    if not path.is_file():
        return False
    if path.stat().st_size < int(args.get("min_bytes") or 100_000):
        return False
    probe = shutil.which("ffprobe") or "ffprobe"
    try:
        r = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    try:
        duration = float((r.stdout or "").strip())
    except ValueError:
        return False
    low = float(args.get("min_s") or 0.0)
    high = float(args.get("max_s") or 1e9)
    return low <= duration <= high
