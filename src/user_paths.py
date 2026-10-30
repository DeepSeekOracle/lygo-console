"""The operator's own folders, resolved on this machine.

A console that can only write inside its workspace cannot do what it is asked - "create a note on the
desktop" is a reasonable request and it should land on the desktop. This resolves the real folders,
handles the OneDrive redirect Windows uses by default, and says so honestly when one is absent.
"""
from __future__ import annotations

import os
from pathlib import Path

_NAMES = {
    "desktop": ("Desktop",),
    "documents": ("Documents",),
    "downloads": ("Downloads",),
    "pictures": ("Pictures",),
}


def _candidates(name: str) -> list[Path]:
    home = Path(os.path.expanduser("~"))
    parts = _NAMES.get(name, (name.title(),))
    out: list[Path] = [home / part for part in parts]
    # OneDrive moves these by default: "~/OneDrive/Desktop", "~/OneDrive - Org/Desktop"
    for base in home.glob("OneDrive*"):
        out.extend(base / part for part in parts)
    return out


def known_folder(name: str) -> Path | None:
    """The real folder for desktop/documents/downloads/pictures, or None if this box has no such place."""
    if not name:
        return None
    key = str(name).strip().lower()
    if key in ("home", "user", "profile", "~"):
        return Path(os.path.expanduser("~"))
    for cand in _candidates(key):
        try:
            if cand.is_dir():
                return cand
        except OSError:
            continue
    return None


def folder_map() -> dict[str, str]:
    """What exists here, as absolute paths - so the agent maps instead of guessing or refusing."""
    found: dict[str, str] = {}
    for key in (*_NAMES, "home"):
        got = known_folder(key)
        if got:
            found[key] = str(got)
    return found


def resolve(target: str) -> Path | None:
    """Turn what an operator would say ("desktop", "my documents", "D:/notes") into a real path."""
    raw = str(target or "").strip().strip('"')
    if not raw:
        return None
    low = raw.lower()
    for alias, key in (("desktop", "desktop"), ("document", "documents"), ("download", "downloads"),
                       ("picture", "pictures"), ("home", "home"), ("user profile", "home")):
        if alias in low and len(low) <= len(alias) + 12:
            got = known_folder(key)
            if got:
                return got
    p = Path(raw)
    if p.is_absolute():
        return p
    return None
