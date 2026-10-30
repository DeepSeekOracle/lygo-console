from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Iterator

from paths import LLAMA_PORT

# A thinking model given this console's own prompt will spend the whole budget in its reasoning channel
# and hand back empty content - which reaches the operator as an answer that never arrives, and the
# portal stops it as "engine not responding". llama-server renders chat_template_kwargs into the chat
# template, so this asks the template to skip that channel and answer. Templates that do not use the
# flag simply ignore it. LYGO_THINKING=1 turns the reasoning channel back on for anyone who wants it.
THINKING_OFF = {"enable_thinking": False}


def sampling_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    """The operator's sampling block, for the keys a caller left unstated.

    One chokepoint on purpose: the console builds its local turn in six places (the main turn, the
    follow-up after a limb, the card, the nudge, and the /v1 passthrough), and a parameter applied at
    five of them is a parameter that is wrong somewhere. Explicit caller values always win, so the API
    surface keeps working the way its caller expects.
    """
    try:
        from paths import console_sampling

        block = console_sampling()
    except Exception:  # noqa: BLE001 - a config read must never break a turn
        return {}
    return {k: v for k, v in block.items() if k not in payload}


def for_local_engine(payload: dict[str, Any]) -> dict[str, Any]:
    """The payload as our own engine should receive it."""
    out = dict(payload)
    out.update(sampling_defaults(out))
    if str(os.environ.get("LYGO_THINKING") or "").lower() in ("1", "true", "yes", "on"):
        return out
    kwargs = dict(out.get("chat_template_kwargs") or {})
    kwargs.setdefault("enable_thinking", False)
    out["chat_template_kwargs"] = kwargs
    return out


def llama_chat(
    *,
    api_key: str,
    payload: dict[str, Any],
    port: int = LLAMA_PORT,
    timeout: float = 600.0,
) -> tuple[int, bytes, str]:
    data = json.dumps(for_local_engine(payload)).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type") or "application/json"
            return resp.status, body, ctype
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"{}", "application/json"


def llama_chat_stream(*, api_key: str, payload: dict[str, Any], port: int = LLAMA_PORT) -> Iterator[bytes]:
    data = json.dumps(for_local_engine(payload)).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        while True:
            line = resp.readline()
            if not line:
                break
            yield line
