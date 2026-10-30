"""Standalone console notepad. Files live under save/notepad (kit-local, not MEMORY.md)."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from paths import SAVE, ensure_dirs
from atomicio import atomic_write_text, read_text
from p0_hook import gate_prompt

NOTEPAD_ROOT = SAVE / "notepad"
NOTES_DIR = NOTEPAD_ROOT / "notes"
INDEX_PATH = NOTEPAD_ROOT / "index.json"
ID_RE = re.compile(r"^[a-zA-Z0-9._-]{1,80}$")
MAX_BYTES = 256_000
MAX_NOTES = 80
SCRATCH_ID = "scratch"


def ensure() -> None:
    ensure_dirs()
    NOTEPAD_ROOT.mkdir(parents=True, exist_ok=True)
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    if not (NOTES_DIR / f"{SCRATCH_ID}.txt").is_file():
        atomic_write_text(NOTES_DIR / f"{SCRATCH_ID}.txt", "")
    _rebuild_index()


def _ok_id(nid: str) -> bool:
    return bool(nid) and bool(ID_RE.match(nid)) and ".." not in nid


def _note_path(nid: str) -> Path:
    return NOTES_DIR / f"{nid}.txt"


def _load_index() -> list[dict[str, Any]]:
    if not INDEX_PATH.is_file():
        return []
    try:
        data = json.loads(read_text(INDEX_PATH))
    except (OSError, json.JSONDecodeError):
        return []
    notes = data.get("notes") if isinstance(data, dict) else data
    if not isinstance(notes, list):
        return []
    return [n for n in notes if isinstance(n, dict) and _ok_id(str(n.get("id") or ""))]


def _save_index(notes: list[dict[str, Any]]) -> None:
    if INDEX_PATH.is_file():
        try:
            current = json.loads(read_text(INDEX_PATH))
        except (OSError, json.JSONDecodeError):
            current = None
        if isinstance(current, dict) and current.get("notes") == notes:
            return  # unchanged: rewriting on every list request made readers collide with
            # the swap (WinError 5/32) on a stick, and bumped `updated` for no reason
    payload = {"updated": time.time(), "notes": notes}
    atomic_write_text(INDEX_PATH, json.dumps(payload, indent=2))


def _rebuild_index() -> list[dict[str, Any]]:
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    by_id = {str(n.get("id")): n for n in _load_index()}
    notes: list[dict[str, Any]] = []
    for p in sorted(NOTES_DIR.glob("*.txt"), key=lambda x: x.stat().st_mtime, reverse=True):
        nid = p.stem
        if not _ok_id(nid):
            continue
        prev = by_id.get(nid) or {}
        st = p.stat()
        notes.append(
            {
                "id": nid,
                "title": str(prev.get("title") or ("Scratch" if nid == SCRATCH_ID else nid)),
                "updated": st.st_mtime,
                "bytes": st.st_size,
            }
        )
    if not any(n["id"] == SCRATCH_ID for n in notes):
        notes.insert(0, {"id": SCRATCH_ID, "title": "Scratch", "updated": time.time(), "bytes": 0})
    _save_index(notes)
    return notes


def list_notes() -> dict[str, Any]:
    ensure()
    notes = _rebuild_index()
    return {"ok": True, "path": str(NOTEPAD_ROOT), "notes": notes, "n": len(notes)}


def read_note(nid: str) -> dict[str, Any]:
    ensure()
    nid = (nid or SCRATCH_ID).strip()
    if not _ok_id(nid):
        return {"ok": False, "error": "bad_id"}
    p = _note_path(nid)
    if not p.is_file():
        return {"ok": False, "error": "missing", "id": nid}
    text = read_text(p, errors="replace")
    title = nid
    for n in _load_index():
        if n.get("id") == nid:
            title = str(n.get("title") or nid)
            break
    return {"ok": True, "id": nid, "title": title, "text": text, "bytes": len(text.encode("utf-8")), "path": str(p)}


def write_note(nid: str | None, title: str, text: str) -> dict[str, Any]:
    ensure()
    body = text if isinstance(text, str) else str(text or "")
    raw = body.encode("utf-8")
    if len(raw) > MAX_BYTES:
        return {"ok": False, "error": "too_large", "max": MAX_BYTES}
    if gate_prompt(body[:8000]).get("verdict") == "QUARANTINE":
        return {"ok": False, "error": "p0_blocked"}
    notes = _rebuild_index()
    nid = (nid or "").strip()
    if not nid:
        nid = time.strftime("%Y%m%d-%H%M%S")
    if not _ok_id(nid):
        return {"ok": False, "error": "bad_id"}
    if nid not in {n["id"] for n in notes} and len(notes) >= MAX_NOTES:
        return {"ok": False, "error": "too_many", "max": MAX_NOTES}
    title = (title or "").strip()[:120] or ("Scratch" if nid == SCRATCH_ID else nid)
    p = _note_path(nid)
    atomic_write_text(p, body)  # note bodies are the most valuable content on the stick: never torn
    st = p.stat()
    found = False
    out: list[dict[str, Any]] = []
    for n in notes:
        if n["id"] == nid:
            out.append({"id": nid, "title": title, "updated": st.st_mtime, "bytes": st.st_size})
            found = True
        else:
            out.append(n)
    if not found:
        out.insert(0, {"id": nid, "title": title, "updated": st.st_mtime, "bytes": st.st_size})
    _save_index(out)
    return {"ok": True, "id": nid, "title": title, "bytes": st.st_size, "path": str(p)}


def delete_note(nid: str) -> dict[str, Any]:
    ensure()
    nid = (nid or "").strip()
    if nid == SCRATCH_ID:
        p = _note_path(SCRATCH_ID)
        atomic_write_text(p, "")
        return write_note(SCRATCH_ID, "Scratch", "")
    if not _ok_id(nid):
        return {"ok": False, "error": "bad_id"}
    p = _note_path(nid)
    if p.is_file():
        p.unlink()
    notes = [n for n in _load_index() if n.get("id") != nid]
    _save_index(notes)
    return {"ok": True, "deleted": nid}


def new_note(title: str = "") -> dict[str, Any]:
    return write_note(time.strftime("%Y%m%d-%H%M%S"), title or "Untitled", "")
