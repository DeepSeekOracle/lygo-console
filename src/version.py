"""The one place this kit's release number lives.

Every move forward is a real version change: bump ``VERSION`` and the console header, ``/api/health`` and
the runtime facts the model is told all follow in the same second - there is no second string to forget.

Why this file exists: the release used to be stamped in THREE places - ``server.BUILD``
(``v1.1-20260917api2``), ``runtime_facts._FALLBACK_BUILD`` (the same string again) and the portal's own
``LYGO_BUILD`` const (``1.1.0``) - so the header, the health line and the facts handed to the model could
disagree about which build the operator was running, and a version bump was three edits that had to be
remembered together.

``VERSION`` holds the release on line 1 and an optional tag on line 2::

    1.1.0
    stable-anchor 2026-09-19

Everything here is read-only and cheap: the file is parsed on each call (a few hundred bytes) so a bump
needs no restart of an import cache.
"""
from __future__ import annotations

import re
from pathlib import Path

FALLBACK_RELEASE = "1.1.0"
VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION"


def _lines() -> list[str]:
    try:
        return [line.strip() for line in VERSION_FILE.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return []


def release() -> str:
    """The release from VERSION (X.Y.Z), or the fallback if the file is missing/odd."""
    lines = _lines()
    if lines and re.fullmatch(r"\d+\.\d+\.\d+", lines[0]):
        return lines[0]
    return FALLBACK_RELEASE


def tag() -> str:
    """The optional human tag on line 2 (e.g. 'stable-anchor 2026-09-19'), or ''."""
    lines = _lines()
    return lines[1] if len(lines) > 1 else ""


def stamp() -> str:
    """What the header, health and receipts show: 'v' + release()."""
    return "v" + release()
