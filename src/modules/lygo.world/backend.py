"""lygo.world — adapter over `src/world_clock.py`.

Δ9Φ963-LYGO-MODULE-CORE-v1

M1 SHIM, BEHAVIOUR UNCHANGED: `GET /api/world` answers exactly what 1.1.1 answered — the bytes come
from the same `world_clock.pulse()` call the kernel used to make inline. Nothing here re-implements
the clock; if the clock changes, this module follows for free.

Read-only: the weather lookup caches in memory inside world_clock, so the module owns no state and
needs no gate. A network failure is already handled inside the clock (it returns the stamps without
weather), so this route does not fail when the laptop is offline.
"""

from __future__ import annotations

from typing import Any


def world(ctx: Any, req: Any) -> None:
    """GET /api/world — the same body 1.1.1 served."""
    from world_clock import pulse

    req.json(200, pulse())


def health(ctx: Any) -> dict[str, Any]:
    """Answers whether the clock itself can compute stamps (it does not need the network for this)."""
    try:
        from world_clock import pulse_stamps

        stamps = pulse_stamps()
        if not stamps or not stamps.get("utc_iso"):
            return {"ok": False, "detail": "world_clock answered no stamps"}
        return {"ok": True, "detail": f"{stamps.get('weekday')} {stamps.get('local_iso')} ({stamps.get('local_tz')})"}
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return {"ok": False, "detail": f"world_clock raised: {exc}"}


def register(ctx: Any) -> dict[str, Any]:
    return {
        "routes": [("GET", "/api/world", world)],
        "limbs": [],
        "panes": [],
        "health": health,
        "startup": None,
        "shutdown": None,
    }
