"""One engine API key for the LYGO Manager Console.

The key is generated locally, shown once, and stored as a SHA-256 hash. It is accepted by the
console's OpenAI-compatible /v1 routes through either `Authorization: Bearer <key>` or
`X-API-Key: <key>`. Rotating replaces the hash; revoking removes it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import threading

from atomicio import atomic_write_text, read_text as read_text_locked
from paths import SAVE, ensure_dirs

STORE = SAVE / "engine_api_key.json"
PREFIX = "lygo_sk_"
_LOCK = threading.RLock()
_LAST_SEEN_WRITE = 0.0


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _chmod600() -> None:
    try:
        os.chmod(STORE, 0o600)
    except OSError:
        pass


def _read() -> dict:
    try:
        raw = read_text_locked(STORE)
        obj = json.loads(raw or "{}")
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write(obj: dict) -> None:
    ensure_dirs()
    SAVE.mkdir(parents=True, exist_ok=True)
    atomic_write_text(STORE, json.dumps(obj, indent=2) + "\n")
    _chmod600()


def status() -> dict:
    rec = _read()
    configured = bool(rec.get("hash") and rec.get("prefix"))
    return {
        "configured": configured,
        "prefix": rec.get("prefix", ""),
        "created_at": rec.get("created_at"),
        "last_used": rec.get("last_used"),
        "label": rec.get("label", "LYGO Manager Console"),
        "endpoint": "/v1/chat/completions",
    }


def configured() -> bool:
    return bool(_read().get("hash"))


def create(label: str = "LYGO Manager Console") -> dict:
    secret = PREFIX + secrets.token_urlsafe(32)
    with _LOCK:
        _write({
            "version": 1,
            "prefix": secret[: len(PREFIX) + 8],
            "hash": _digest(secret),
            "created_at": _now(),
            "last_used": None,
            "label": (label or "LYGO Manager Console").strip()[:120],
        })
    out = status()
    out["secret"] = secret
    return out


def verify(candidate: str | None) -> bool:
    if not candidate:
        return False
    candidate = candidate.strip()
    if not candidate.startswith(PREFIX):
        return False
    with _LOCK:
        rec = _read()
        stored = str(rec.get("hash") or "")
        if not stored or not hmac.compare_digest(_digest(candidate), stored):
            return False
        # Record use without writing on every single request.
        global _LAST_SEEN_WRITE
        import time as _time
        now = _time.time()
        if now - _LAST_SEEN_WRITE > 30:
            rec["last_used"] = _now()
            try:
                _write(rec)
                _LAST_SEEN_WRITE = now
            except OSError:
                pass
    return True


def revoke() -> dict:
    with _LOCK:
        if STORE.exists():
            try:
                STORE.unlink()
            except OSError:
                _write({})
    out = status()
    out["revoked"] = True
    return out


def from_headers(headers: dict[str, str]) -> str | None:
    lower = {str(k).lower(): v for k, v in (headers or {}).items()}
    for name in ("x-api-key", "x-lygo-api-key"):
        value = lower.get(name)
        if value:
            return str(value).strip()
    auth = lower.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        value = auth[7:].strip()
        if value.startswith(PREFIX):
            return value
    return None
