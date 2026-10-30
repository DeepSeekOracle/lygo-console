"""Operator-editable folder/drive mounts. Overlay on admin.json roots."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from paths import KIT_ROOT, SAVE, WORKSPACE, ensure_dirs
from atomicio import atomic_write_text, read_text

MAP_PATH = SAVE / "workspace_map.json"
WRITE_BLOCK = (
    r"c:\windows",
    r"c:\program files",
    r"c:\program files (x86)",
    r"c:\programdata",
)
PINNED = "pinned"


def _load() -> dict[str, Any]:
    ensure_dirs()
    if not MAP_PATH.is_file():
        return {"mounts": [], "revoke": []}
    try:
        data = json.loads(read_text(MAP_PATH))
    except (OSError, json.JSONDecodeError):
        return {"mounts": [], "revoke": []}
    if not isinstance(data, dict):
        return {"mounts": [], "revoke": []}
    data.setdefault("mounts", [])
    data.setdefault("revoke", [])
    return data


def _save(data: dict[str, Any]) -> None:
    MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(MAP_PATH, json.dumps(data, indent=2))
    try:
        from admin_map import invalidate

        invalidate()
    except Exception:
        pass


def _norm(p: Path) -> str:
    try:
        return str(p.resolve())
    except OSError:
        return str(p)


def _blocked_write(p: Path) -> bool:
    s = _norm(p).replace("/", "\\").lower().rstrip("\\")
    if s in {"c:", "c:\\"} or re.fullmatch(r"[a-z]:", s):
        return True
    return any(s == b or s.startswith(b + "\\") for b in WRITE_BLOCK)


def _pinned_paths() -> set[str]:
    out = {_norm(WORKSPACE), _norm(SAVE), _norm(KIT_ROOT)}
    return out


def extra_paths(kind: str) -> list[Path]:
    data = _load()
    revoked = {_norm(Path(x)) for x in (data.get("revoke") or []) if x}
    out: list[Path] = []
    for m in data.get("mounts") or []:
        if not isinstance(m, dict):
            continue
        if kind == "read" and not m.get("read", True):
            continue
        if kind == "write" and not m.get("write"):
            continue
        if kind == "search" and not m.get("search", True):
            continue
        p = Path(str(m.get("path") or ""))
        if not p.exists():
            continue
        key = _norm(p)
        if key in revoked:
            continue
        out.append(p)
    return out


def revoked_set() -> set[str]:
    data = _load()
    return {_norm(Path(x)) for x in (data.get("revoke") or []) if x}


def add_mount(path: str, *, read: bool = True, write: bool = False, search: bool = True, label: str = "") -> dict[str, Any]:
    raw = (path or "").strip().strip('"')
    if not raw:
        return {"ok": False, "error": "empty"}
    p = Path(raw)
    if not p.exists():
        return {"ok": False, "error": "missing", "path": raw}
    if not p.is_dir():
        p = p.parent
    if write and _blocked_write(p):
        write = False
        note = "write refused on OS/system root; read/search allowed"
    else:
        note = ""
    data = _load()
    key = _norm(p)
    data["revoke"] = [x for x in (data.get("revoke") or []) if _norm(Path(x)) != key]
    mounts = [m for m in (data.get("mounts") or []) if isinstance(m, dict) and _norm(Path(str(m.get("path") or ""))) != key]
    mounts.append(
        {
            "path": str(p.resolve()),
            "read": bool(read),
            "write": bool(write),
            "search": bool(search if read else False),
            "label": (label or p.name or str(p))[:80],
        }
    )
    data["mounts"] = mounts
    _save(data)
    return {"ok": True, "path": str(p.resolve()), "write": write, "note": note, "mounts": list_mounts().get("mounts")}


def remove_mount(path: str) -> dict[str, Any]:
    raw = (path or "").strip().strip('"')
    if not raw:
        return {"ok": False, "error": "empty"}
    key = _norm(Path(raw))
    if key in _pinned_paths():
        return {"ok": False, "error": "pinned", "path": key, "hint": "kit workspace/save cannot be unmounted"}
    data = _load()
    data["mounts"] = [
        m
        for m in (data.get("mounts") or [])
        if not (isinstance(m, dict) and _norm(Path(str(m.get("path") or ""))) == key)
    ]
    rev = list(data.get("revoke") or [])
    if key not in {_norm(Path(x)) for x in rev}:
        rev.append(key)
    data["revoke"] = rev
    _save(data)
    return {"ok": True, "removed": key, "mounts": list_mounts().get("mounts")}


def list_mounts() -> dict[str, Any]:
    from admin_map import is_admin, load as admin_load

    data = _load()
    revoked = revoked_set()
    pinned = _pinned_paths()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_row(path: Path, *, read: bool, write: bool, search: bool, source: str, label: str = "") -> None:
        if not path.exists():
            st = "missing"
        else:
            st = "ok"
        try:
            key = _norm(path)
        except OSError:
            return
        if key in seen:
            return
        seen.add(key)
        rows.append(
            {
                "path": key,
                "label": label or path.name or key,
                "read": read,
                "write": write and not _blocked_write(path),
                "search": search,
                "source": source,
                "pinned": key in pinned,
                "revoked": key in revoked,
                "status": "revoked" if key in revoked else st,
            }
        )

    add_row(WORKSPACE, read=True, write=True, search=True, source=PINNED, label="kit workspace")
    add_row(SAVE, read=True, write=True, search=False, source=PINNED, label="kit save")
    adm = admin_load()
    if is_admin():
        from admin_map import _expand_path

        merged: dict[str, dict[str, bool]] = {}
        for key, flag in (("read_roots", "read"), ("write_roots", "write"), ("search_roots", "search")):
            for raw in adm.get(key) or []:
                p = _expand_path(str(raw))
                k = _norm(p) if p.exists() else str(p)
                merged.setdefault(k, {"path": p, "read": False, "write": False, "search": False})
                merged[k][flag] = True
        for item in merged.values():
            add_row(item["path"], read=item["read"], write=item["write"], search=item["search"], source="admin.json")
    for m in data.get("mounts") or []:
        if not isinstance(m, dict):
            continue
        add_row(
            Path(str(m.get("path") or "")),
            read=bool(m.get("read", True)),
            write=bool(m.get("write")),
            search=bool(m.get("search", True)),
            source="operator",
            label=str(m.get("label") or ""),
        )
    live = [r for r in rows if r.get("status") == "ok" and not r.get("revoked")]
    return {
        "ok": True,
        "path": str(MAP_PATH),
        "admin": is_admin(),
        "mounts": rows,
        "live": live,
        "n_live": len(live),
        "workspace": str(WORKSPACE),
    }
