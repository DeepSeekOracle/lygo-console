"""lygo.dispatcherctrl — worker registry, scan, chat, boot for Hermes."""
from __future__ import annotations

from typing import Any

ROUTE = "/api/dispatcher"


def data(ctx: Any, req: Any) -> None:
    del ctx
    import dispatcher

    req.json(200, dispatcher.pane())


def control(ctx: Any, req: Any) -> None:
    del ctx
    import dispatcher

    obj = req.body(80_000)
    req.json(200, dispatcher.handle(str(obj.get("action") or "status"), obj))


def health(ctx: Any) -> dict[str, Any]:
    del ctx
    try:
        import dispatcher

        live = sum(1 for port in (11441, 18900, 8790, 8080) if dispatcher._tcp("127.0.0.1", port, 0.2))
        return {"ok": True, "detail": f"{live} local worker ports open"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": type(exc).__name__}


def register(ctx: Any) -> dict[str, Any]:
    del ctx
    return {
        "routes": [("GET", ROUTE, data), ("POST", ROUTE, control)],
        "limbs": [],
        "panes": [{
            "id": "dock.dispatcher",
            "slot": "dock",
            "title": "Dispatcher",
            "data": "GET /api/dispatcher",
            "refresh_s": 8,
            "inner_scroll": False,
        }],
        "health": health,
        "startup": None,
        "shutdown": None,
    }
