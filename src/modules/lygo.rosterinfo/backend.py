"""lygo.rosterinfo — skills, limbs, and the Unity harness in one card.

The scan lives in src/roster.py. This module only serves it. health() does not
walk skill folders: that walk is the card's, and it is cached.
"""
from __future__ import annotations

from typing import Any

ROUTE = "/api/roster"


def data(ctx: Any, req: Any) -> None:
    del ctx
    try:
        import roster

        payload = roster.card(str(req.get("q") or ""))
    except Exception as exc:  # noqa: BLE001 - a card that cannot read is a named gap
        payload = {
            "ok": False,
            "state": "amber",
            "state_text": "roster unread",
            "lights": [],
            "groups": [],
            "missing": [f"roster could not be asked: {type(exc).__name__}"],
        }
    req.json(200, payload)


def health(ctx: Any) -> dict[str, Any]:
    """Cheap boot check. Does not scan skill trees."""
    del ctx
    return {
        "ok": True,
        "detail": "roster is a local read; the card scans on GET /api/roster",
    }


def register(ctx: Any) -> dict[str, Any]:
    del ctx
    return {
        "routes": [("GET", ROUTE, data)],
        "limbs": [],
        "panes": [
            {
                "id": "dock.roster",
                "slot": "dock",
                "title": "Roster",
                "data": f"GET {ROUTE}",
                "refresh_s": 30,
                "inner_scroll": False,
            }
        ],
        "health": health,
    }
