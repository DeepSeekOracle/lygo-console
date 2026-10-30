from __future__ import annotations

import hashlib
from typing import Any

HEX_POSITIONS = {
    1: (1, 2),
    2: (2, 1),
    3: (2, 2),
    4: (1, 0),
    5: (0, 1),
    6: (0, 2),
    7: (2, 0),
    8: (0, 0),
    9: (1, 1),
}


def vortex_signature(data: str) -> dict[str, Any]:
    digest = hashlib.sha256((data or "").encode("utf-8")).digest()
    total = sum(digest)
    digit = total % 9
    digit = 9 if digit == 0 else digit
    hx, hy = HEX_POSITIONS.get(digit, (1, 1))
    if digit in (3,):
        gov = "Creation"
    elif digit in (6,):
        gov = "Relation"
    elif digit in (9,):
        gov = "Completion"
    elif digit in (1, 2, 4):
        gov = "Creation"
    elif digit in (5, 7):
        gov = "Relation"
    else:
        gov = "Completion"
    return {
        "vortex_digit": digit,
        "hex_coord": (hx, hy),
        "governing": gov,
        "sha256_prefix": hashlib.sha256((data or "").encode("utf-8")).hexdigest()[:16],
        "consensus_found": True,
        "mode": "single_agent_identity",
    }
