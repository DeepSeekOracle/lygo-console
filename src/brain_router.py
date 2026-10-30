"""Local-first brain routing for the LYGO LLM Console.

The GGUF engine on this machine is the DEFAULT brain and always stays complete.
A cloud API (DeepSeek / Gemini / Groq / OpenAI / xAI / OpenRouter / custom URL) is an
*option* the operator switches on. When an API turn cannot be served — out of tokens or
credit, rate limit, dead key, outage — the turn is handed back to the local engine with a
visible banner instead of the chat dying.

Pure logic only: no server, no key, no engine needed, so it is unit-testable.
"""
from __future__ import annotations

from typing import Any

LOCAL = "local"
API = "api"
MODES = (LOCAL, API)

# HTTP codes that mean "this API turn is not going to work" -> hand the turn to local.
FALLBACK_CODES = frozenset({0, 400, 401, 402, 403, 404, 408, 409, 425, 429})

REASONS: dict[int, str] = {
    0: "API unreachable",
    400: "API rejected the request",
    401: "API key rejected",
    402: "API out of tokens/credit",
    403: "API refused the key",
    404: "API endpoint or model not found",
    408: "API timed out",
    409: "API conflict",
    425: "API too early",
    429: "API rate limit reached",
    500: "API server error",
    502: "API unreachable",
    503: "API unavailable",
    504: "API gateway timeout",
}

HINTS = (
    "insufficient",
    "balance",
    "quota",
    "credit",
    "rate limit",
    "rate_limit",
    "too many requests",
    "unauthorized",
    "invalid api key",
    "authentication",
    "expired",
    "billing",
    "exceeded",
)


def _code(code: Any) -> int:
    try:
        return int(code or 0)
    except (TypeError, ValueError):
        return 0


def should_fallback(code: Any) -> bool:
    """True when a cloud failure should be handed to the local engine."""
    c = _code(code)
    return c in FALLBACK_CODES or c >= 500 or c < 0


def reason(code: Any, message: str = "") -> str:
    """Short operator-facing 'why the API did not answer'."""
    c = _code(code)
    base = REASONS.get(c) or (f"API error HTTP {c}" if c else "API unreachable")
    msg = " ".join(str(message or "").split())[:200]
    if msg:
        low = msg.lower()
        if any(h in low for h in HINTS) and c in (0, 400, 500, 502):
            base = REASONS.get(402) or base
        if low not in base.lower():
            return f"{base} — {msg}"
    return base


def mode_of(status: dict[str, Any] | None) -> str:
    """Persisted brain mode: API only while a key is saved, switched on, and not in handoff."""
    st = status or {}
    if st.get("enabled") and st.get("has_key") and not st.get("degraded"):
        return API
    return LOCAL


def label_of(status: dict[str, Any] | None) -> str:
    st = status or {}
    name = st.get("label") or st.get("provider") or "api"
    model = st.get("model") or ""
    return f"{name}/{model}" if model else str(name)


def handoff_info(code: Any, message: str, api_label: str = "", *, skipped: bool = False) -> dict[str, Any]:
    """Record for the UI/receipt/health payload describing one local takeover."""
    return {
        "code": _code(code),
        "why": reason(code, message),
        "api": api_label,
        "skipped": bool(skipped),
    }


def banner(why: str, api_label: str = "", local_model: str = "", *, skipped: bool = False) -> str:
    """Line prepended to the answer that the local engine produced instead of the API."""
    line = "\u26a0 API handoff — " + (why or "the API did not answer") + "."
    if api_label:
        line += f" {api_label} did not answer."
    line += " The local engine answered instead"
    if local_model:
        line += f" ({local_model})"
    line += "."
    line += " Local is back to being the default; press API when you have tokens again."
    return line
