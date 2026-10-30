from __future__ import annotations

import hmac
import json
import os
import secrets
from pathlib import Path

from atomicio import atomic_write_text, read_text as read_text_locked

from paths import DATA, TOKEN_PATH, LLAMA_KEY_PATH, ensure_dirs

HEADER = "X-LYGO-LLM-Token"
COOKIE_NAME = "lygo_token"


def _chmod600(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def ensure_token() -> str:
    ensure_dirs()
    DATA.mkdir(parents=True, exist_ok=True)
    if TOKEN_PATH.is_file():
        # A token read during a write looked empty and silently minted a new one, breaking every
        # open browser tab; read through the retrying reader and write atomically instead.
        try:
            t = read_text_locked(TOKEN_PATH).strip()
        except OSError:
            t = ""
        if t:
            return t
    t = secrets.token_urlsafe(24)
    atomic_write_text(TOKEN_PATH, t)
    _chmod600(TOKEN_PATH)
    return t


# Placeholder values the kit has shipped or a hand-edited config may carry. The engine key is the
# only thing between llama-server and anything else on this box that can reach loopback, so one of
# these must never become a permanent answer just because it is sitting in the file.
WEAK_LLAMA_KEYS = {
    "secret-key",
    "secret",
    "changeme",
    "change-me",
    "key",
    "test",
    "password",
    "lygo",
}
MIN_KEY_CHARS = 16


def ensure_llama_key() -> str:
    """The engine's API key: generated per install, stored 0600, reused once it is a real key."""
    ensure_dirs()
    if LLAMA_KEY_PATH.is_file():
        try:
            k = read_text_locked(LLAMA_KEY_PATH).strip()
        except OSError:
            k = ""
        if k and len(k) >= MIN_KEY_CHARS and k.lower() not in WEAK_LLAMA_KEYS:
            return k
    k = secrets.token_urlsafe(32)
    atomic_write_text(LLAMA_KEY_PATH, k)
    _chmod600(LLAMA_KEY_PATH)
    return k


def check(provided: str | None, expected: str) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.strip(), expected.strip())


def token_from_request(headers: dict[str, str], query: dict[str, list[str]]) -> str | None:
    # Header NAMES are case-insensitive (RFC 9110) but this lookup was done in the case the client
    # happened to send. Clients canonicalise: urllib title-cases every part (X-Lygo-Llm-Token),
    # HTTP/2 lowercases them all, and a proxy may do either - so a caller holding the correct token
    # was told 401 by the receipts gate. Measured 2026-09-20: the same token answered 401 through
    # X-Lygo-Llm-Token and 200 through Authorization: Bearer. Fold the names once, here.
    lower = {str(k).lower(): v for k, v in headers.items()}
    h = lower.get(HEADER.lower())
    if h:
        return h.strip()
    auth = lower.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    qs = query.get("t") or query.get("token")
    if qs:
        return qs[0]
    # Set once by the portal shell for an operator who already proved the token, so it need not
    # sit in the URL and in browser history. SameSite=Strict, so it is never sent cross-site.
    cookie = headers.get("Cookie") or headers.get("cookie") or ""
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE_NAME and value:
            return value.strip()
    return None
