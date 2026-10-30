from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from paths import MYCELIUM, RECEIPTS, WORKSPACE, ensure_dirs
from atomicio import atomic_write_text, read_text
from p3_note import vortex_signature


# ---------------------------------------------------------------------------
# Retention (FIX 3): one receipt per turn, one events.jsonl line per turn and one todo.jsonl line
# per todo grow forever, and on a USB stick an unbounded save/ tree eventually fills the drive.
# Keep the newest N of each: LYGO_RECEIPTS_KEEP receipts (default 500) and LYGO_EVENTS_KEEP lines
# per jsonl store (default 5000). Pruning is throttled (PRUNE_MIN_INTERVAL) so a busy turn does not
# rescan the stores on every single write, and it always keeps the most recent entries — a file
# whose mtime ties the newest kept one is never deleted, and a rewrite only ever drops the head.
# ---------------------------------------------------------------------------
RECEIPTS_KEEP_ENV = "LYGO_RECEIPTS_KEEP"
EVENTS_KEEP_ENV = "LYGO_EVENTS_KEEP"
RECEIPTS_KEEP_DEFAULT = 500
EVENTS_KEEP_DEFAULT = 5000
KEEP_MAX = 100_000           # sanity ceiling for an env override
PRUNE_MIN_INTERVAL = 300.0   # seconds between periodic prunes

PRUNE_STATUS: dict[str, Any] = {"at": 0.0, "receipts": None, "events": None, "todos": None}
_PRUNE_LAST = 0.0


def _events_path() -> Path:
    return MYCELIUM / "events.jsonl"


def _todos_path() -> Path:
    return WORKSPACE / "todo.jsonl"


def _keep_from_env(name: str, default: int) -> int:
    """Positive int from the environment, else *default* (garbage is ignored, not fatal)."""
    raw = (os.environ.get(name) or "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if 10 <= value <= KEEP_MAX:
            return value
    return default


def _prune_receipts(keep: int) -> dict[str, Any]:
    """Delete all but the newest *keep* receipt files, oldest first."""
    out: dict[str, Any] = {"kept": 0, "deleted": 0, "freed_bytes": 0}
    if not RECEIPTS.is_dir():
        return out
    entries: list[tuple[float, str, Path, int]] = []
    for p in RECEIPTS.glob("*.json"):
        try:
            st = p.stat()
        except OSError:
            continue
        entries.append((st.st_mtime, p.name, p, int(st.st_size)))
    entries.sort(key=lambda e: (e[0], e[1]), reverse=True)
    out["kept"] = min(len(entries), keep)
    out["bytes"] = sum(e[3] for e in entries[:keep])
    if len(entries) <= keep:
        return out
    boundary = entries[keep - 1][0]
    for mtime, _name, p, size in entries[keep:]:
        if mtime >= boundary:
            continue  # same instant as the newest kept file: never gamble on the recent ones
        try:
            p.unlink()
        except OSError:
            continue
        out["deleted"] += 1
        out["freed_bytes"] += size
    return out


def _prune_lines(path: Path, keep: int) -> dict[str, Any]:
    """Keep only the newest *keep* lines of a jsonl store, rewritten atomically."""
    out: dict[str, Any] = {"path": str(path), "kept": 0, "deleted": 0, "bytes": 0}
    if not path.is_file():
        return out
    try:
        text = read_text(path, errors="replace")
    except OSError as exc:
        out["error"] = str(exc)
        return out
    lines = text.splitlines(keepends=True)
    out["kept"] = min(len(lines), keep)
    out["bytes"] = len(text.encode("utf-8", errors="replace"))
    if len(lines) <= keep:
        return out
    tail = "".join(lines[-keep:])
    if not tail.endswith("\n"):
        tail += "\n"
    atomic_write_text(path, tail)
    out["deleted"] = len(lines) - keep
    out["bytes"] = len(tail.encode("utf-8", errors="replace"))
    return out


def prune_state(*, receipts_keep: int | None = None, events_keep: int | None = None) -> dict[str, Any]:
    """Bound the on-stick stores; returns counts/sizes so a caller can report them.

    Importable on purpose (the server may call it at startup and in its status): it prunes the
    receipts directory, mycelium/events.jsonl and workspace/todo.jsonl and returns
    {"receipts": {...}, "events": {...}, "todos": {...}, "at": <epoch>}.
    """
    r_keep = receipts_keep or _keep_from_env(RECEIPTS_KEEP_ENV, RECEIPTS_KEEP_DEFAULT)
    e_keep = events_keep or _keep_from_env(EVENTS_KEEP_ENV, EVENTS_KEEP_DEFAULT)
    result = {
        "receipts": _prune_receipts(r_keep),
        "events": _prune_lines(_events_path(), e_keep),
        "todos": _prune_lines(_todos_path(), e_keep),
        "at": time.time(),
    }
    PRUNE_STATUS.update(result)
    return result


def prune_at_startup() -> dict[str, Any]:
    """Explicit startup prune (counts/sizes returned); safe to call before the first turn."""
    global _PRUNE_LAST
    result = prune_state()
    _PRUNE_LAST = float(result.get("at") or time.time())
    return result


def _maybe_prune() -> dict[str, Any] | None:
    """Periodic prune after a write: throttled, and never allowed to fail a turn."""
    global _PRUNE_LAST
    now = time.time()
    if _PRUNE_LAST and (now - _PRUNE_LAST) < PRUNE_MIN_INTERVAL:
        return None
    try:
        return prune_at_startup()
    except Exception as exc:  # noqa: BLE001 - retention must not break receipt writing
        PRUNE_STATUS["error"] = str(exc)
        _PRUNE_LAST = now
        return None


def create_node(command: str, args: list[str] | None = None) -> dict[str, Any]:
    args = args or []
    raw = f"{time.time():.6f}|{command}|{' '.join(args)}"
    light = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    mass = max(0.1, min(1.0, 1.0 - len(command) / 500.0))
    return {
        "light_code": light,
        "ethical_mass": round(mass, 3),
        "command": command,
        "signature": "Δ9Φ963-SDA-P5-v1",
    }


def write_receipt(*, prompt: str, output: str, model: str, gate: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_dirs()
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    MYCELIUM.mkdir(parents=True, exist_ok=True)
    rid = str(uuid.uuid4())
    node = create_node("chat")
    rec = {
        "id": rid,
        "ts": time.time(),
        "model": model,
        "message_sha256": hashlib.sha256((prompt or "").encode("utf-8")).hexdigest(),
        "output_sha256": hashlib.sha256((output or "").encode("utf-8")).hexdigest(),
        "gate_verdict": gate.get("verdict"),
        "p3": vortex_signature(prompt or ""),
        "p5": node,
        "has_image": bool((extra or {}).get("has_image")),
    }
    atomic_write_text(RECEIPTS / f"{rid}.json", json.dumps(rec, indent=2))
    row = {"id": rid, "ts": rec["ts"], "bundle": {"receipt_id": rid, "model": model, "verdict": rec["gate_verdict"]}}
    with _events_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    _maybe_prune()  # bounded retention (throttled): receipts, events.jsonl, todo.jsonl
    return rec
