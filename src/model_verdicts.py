"""Models THIS host has already proved it cannot load.

A GGUF can parse — so the scanner advertises it as runnable — and still fail at load: wrong tensor
offsets, an unsupported quant type, a half-copied file. Verified case: the PrismML ternary GGUFs
parse as qwen3/65536-ctx and then die with "tensor 'output_norm.weight' has offset ... expected
...". Without a memory of that, RAM-auto picks the same broken file on every boot and the console
never comes up.

Verdicts are keyed to the model's own file (path + size + mtime) AND to the host, so replacing the
file, or walking the stick onto a different PC, clears the verdict by construction.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from paths import DATA, ensure_dirs

VERDICTS_JSON = DATA / "model_verdicts.json"
MAX_ENTRIES = 24


def _host() -> str:
    try:
        from perf import host_id

        return str(host_id())
    except Exception:  # noqa: BLE001
        return "unknown-host"


def _fingerprint(path: str) -> dict[str, Any]:
    try:
        st = Path(path).stat()
        return {"size": int(st.st_size), "mtime": int(st.st_mtime)}
    except OSError:
        return {"size": 0, "mtime": 0}


def _load() -> dict[str, Any]:
    try:
        obj = json.loads(VERDICTS_JSON.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            return obj
    except (OSError, json.JSONDecodeError):
        pass
    return {"hosts": {}}


def _save(obj: dict[str, Any]) -> None:
    """Write through the kit's atomic writer.

    The naive tmp+os.replace this replaces is the one pattern that already bit this project:
    a reader or a dying process holding the file makes the write raise WinError 5/32, and the
    except below would swallow it as "verdict not remembered". atomicio retries under a lock.
    """
    text = json.dumps(obj, indent=2)
    try:
        ensure_dirs()
        DATA.mkdir(parents=True, exist_ok=True)
        import atomicio

        atomicio.atomic_write_text(VERDICTS_JSON, text)
    except Exception:  # noqa: BLE001 - a verdict is an optimisation, never a boot requirement
        pass


def entries(host: str | None = None) -> dict[str, dict[str, Any]]:
    obj = _load()
    hosts = obj.get("hosts") if isinstance(obj.get("hosts"), dict) else {}
    raw = hosts.get(host or _host()) or {}
    return raw if isinstance(raw, dict) else {}


def _still_valid(rec: dict[str, Any]) -> bool:
    fp = _fingerprint(str(rec.get("path") or ""))
    return bool(fp["size"]) and fp["size"] == int(rec.get("size") or 0) and fp["mtime"] == int(
        rec.get("mtime") or 0
    )


def bad_ids(host: str | None = None) -> set[str]:
    """Ids this host proved unloadable, where the verdict still describes the file on disk."""
    return {str(mid) for mid, rec in entries(host).items() if isinstance(rec, dict) and _still_valid(rec)}


def is_bad(model_id: str, path: str = "", host: str | None = None) -> bool:
    rec = entries(host).get(str(model_id)) or {}
    if not rec:
        return False
    if path and str(rec.get("path") or "") != str(path):
        return False
    return _still_valid(rec)


def mark_bad(model_id: str, path: str, why: str = "", host: str | None = None) -> dict[str, Any]:
    obj = _load()
    hosts = obj.setdefault("hosts", {})
    bucket = hosts.setdefault(host or _host(), {})
    rec = {"path": str(path), "why": str(why)[:300], "at": round(time.time(), 3), **_fingerprint(path)}
    bucket[str(model_id)] = rec
    if len(bucket) > MAX_ENTRIES:
        oldest = sorted(bucket.items(), key=lambda kv: float((kv[1] or {}).get("at") or 0))
        for mid, _ in oldest[: len(bucket) - MAX_ENTRIES]:
            bucket.pop(mid, None)
    _save(obj)
    return rec


def clear(model_id: str = "", host: str | None = None) -> None:
    """Forget one model's verdict, or every verdict for this host."""
    obj = _load()
    hosts = obj.setdefault("hosts", {})
    h = host or _host()
    if model_id:
        (hosts.get(h) or {}).pop(str(model_id), None)
    else:
        hosts[h] = {}
    _save(obj)
