"""Which kit is running the suite: the PC build or the stick copy?

Three assertions in this suite read the *shipped PC config* (``config/console.json``) or require a git
checkout above the kit. The stick carries a hand-kept portable profile and sits in no repository, so
those assertions cannot hold there - and a stick whose suite is red cannot be signed off. They are
skipped on removable media instead of being weakened: ``src/surface.py`` already answers the question
from the Windows volume type.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def on_a_stick() -> bool:
    """True when this kit lives on removable media (the stick copy, not the PC build)."""
    try:
        if str(ROOT / "src") not in sys.path:
            sys.path.insert(0, str(ROOT / "src"))
        import surface  # type: ignore

        return surface.media(ROOT)[0] == "usb"
    except Exception:
        return False


def stick_why() -> str:
    try:
        if str(ROOT / "src") not in sys.path:
            sys.path.insert(0, str(ROOT / "src"))
        import surface  # type: ignore

        return surface.media(ROOT)[1]
    except Exception:
        return "removable media"
