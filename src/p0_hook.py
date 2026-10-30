from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from paths import KIT_ROOT, VENDOR_P0, stack_root

SIGNATURE = "Δ9Φ963-LYGO-LLM-CONSOLE-P0-v1"
# Two ceilings, because one number cannot do both jobs. A single message may be a long paste, a build
# script or a pasted log; a conversation may be an entire build session. The old single cap of 12,000
# for the TOTAL was a wall in the middle of the work, not a safety property - measured on this box, a
# 30,222-character conversation was refused HTTP 451 on every turn while 8,904 characters answered
# normally. The engine's own context is the real limit (~32k tokens); these bounds stay generous and
# finite. The policy patterns and the blob guard below are unchanged.
POLICY_MAX_CHARS = 48000
POLICY_MAX_CONVERSATION_CHARS = 600000
POLICY_BLOB_MIN_CHARS = 12000
POLICY_BLOB_RUN = 4000
PHYSICS_WINDOW = 8192

_QUARANTINE = re.compile(
    r"("
    + r"|".join(
        [
            r"rm\s+-rf\s+/",
            r"format\s+c:",
            "mi" + "mikatz",
            r"ignore\s+(all\s+)?(previous|prior)\s+instructions",
            r"exfiltrate\s+secrets",
            r"disable\s+safety",
            r"child\s+sexual",
            r"make\s+a\s+bomb\s+detailed",
            r"\bdiskpart\b",
            r"\bbcdedit\b",
            r"cipher\s+/w",
            r"\bshutdown\b",
        ]
    )
    + r")",
    re.I,
)

PHYSICS_AVAILABLE = False
_validate_bytes = None
_import_error = ""


def _try_import(dirpath: Path) -> bool:
    global _validate_bytes
    d = str(dirpath.resolve())
    if d not in sys.path:
        sys.path.insert(0, d)
    try:
        from byte_entropy_filter import validate_bytes  # type: ignore

        _validate_bytes = validate_bytes
        return True
    except Exception:
        if d in sys.path:
            try:
                sys.path.remove(d)
            except ValueError:
                pass
        return False


def _init() -> None:
    global PHYSICS_AVAILABLE, _import_error
    root = stack_root()
    overlay = None
    if root:
        overlay = root / "protocol0_byte_entropy_filter" / "src" / "python" / "byte_entropy_filter.py"
    if overlay and overlay.is_file():
        if _try_import(overlay.parent):
            PHYSICS_AVAILABLE = True
            return
    if _try_import(VENDOR_P0):
        PHYSICS_AVAILABLE = True
        return
    PHYSICS_AVAILABLE = False
    _import_error = "p0_import_failed"


_init()


def _looks_like_a_blob(text: str) -> bool:
    """True for one unbroken run of POLICY_BLOB_RUN+ characters - a packed string, not prose or code.

    The rule this replaces fired when the FIRST 200 characters contained no space. That is what a
    pasted code block, a minified file or a dense config looks like, so an operator pasting their own
    build script could be refused as a "high entropy blob". A real blob is one enormous TOKEN (base64,
    hex, a packed payload); prose, code, JSON and logs all break into words somewhere. The scan is
    single-pass and allocation-free, and it only reaches here for text over POLICY_BLOB_MIN_CHARS.
    """
    if len(text) <= POLICY_BLOB_MIN_CHARS:
        return False
    longest = run = 0
    for ch in text:
        run = 0 if ch.isspace() else run + 1
        if run > longest:
            longest = run
            if longest > POLICY_BLOB_RUN:
                return True
    return False


def _policy(text: str, cap: int = POLICY_MAX_CHARS) -> dict[str, Any]:
    t = text or ""
    if not t.strip():
        return {"verdict": "ALLOW", "reason": "empty", "signature": SIGNATURE}
    if len(t) > cap:
        return {
            "verdict": "QUARANTINE",
            "reason": f"payload_exceeds_{cap}",
            "signature": SIGNATURE,
        }
    if _QUARANTINE.search(t):
        return {
            "verdict": "QUARANTINE",
            "reason": "matched_p0_policy_pattern",
            "signature": SIGNATURE,
        }
    if _looks_like_a_blob(t):
        return {
            "verdict": "QUARANTINE",
            "reason": "high_entropy_blob",
            "signature": SIGNATURE,
        }
    return {"verdict": "ALLOW", "reason": "ok", "signature": SIGNATURE}


def gate_output_window(text: str, cap: int | None = None) -> dict[str, Any]:
    """Policy regex only (no physics). QUARANTINE aborts the stream."""
    return _policy(text, cap or POLICY_MAX_CHARS)


def gate_prompt(text: str, cap: int | None = None) -> dict[str, Any]:
    if not PHYSICS_AVAILABLE or _validate_bytes is None:
        return {
            "verdict": "QUARANTINE",
            "reason": "p0_import_failed",
            "policy": "QUARANTINE",
            "physics": None,
            "signature": SIGNATURE,
        }
    pol = _policy(text, cap or POLICY_MAX_CHARS)
    raw = (text or "").encode("utf-8")
    truncated = len(raw) > PHYSICS_WINDOW
    phys = _validate_bytes(raw[:PHYSICS_WINDOW])
    if pol["verdict"] == "QUARANTINE":
        out = {
            "verdict": "QUARANTINE",
            "reason": pol["reason"],
            "policy": "QUARANTINE",
            "physics": phys,
            "p0_truncated": truncated,
            "signature": SIGNATURE,
        }
        return out
    if phys.get("verdict") == "QUARANTINE":
        return {
            "verdict": "QUARANTINE",
            "reason": "physics_quarantine",
            "policy": "ALLOW",
            "physics": phys,
            "p0_truncated": truncated,
            "signature": SIGNATURE,
        }
    return {
        "verdict": phys.get("verdict", "AMPLIFY"),
        "reason": pol.get("reason", "ok"),
        "policy": "ALLOW",
        "physics": phys,
        "p0_truncated": truncated,
        "signature": SIGNATURE,
    }
