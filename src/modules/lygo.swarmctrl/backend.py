"""lygo.swarmctrl — Hermes-managed bot swarm on the Manager harness."""
from __future__ import annotations

from typing import Any

ROUTE = "/api/swarm"


def data(ctx: Any, req: Any) -> None:
    del ctx
    import swarm

    req.json(200, swarm.status())


def control(ctx: Any, req: Any) -> None:
    del ctx
    import swarm

    obj = req.body(80_000)
    action = str(obj.get("action") or "status")
    req.json(200, swarm.handle(action, obj))


def health(ctx: Any) -> dict[str, Any]:
    del ctx
    try:
        import swarm

        st = swarm.status()
        return {
            "ok": True,
            "detail": f"{len(st.get('agents') or [])} agents · engine={'up' if st.get('engine_alive') else 'down'} · advisor={'up' if st.get('advisor_alive') else 'down'}",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": type(exc).__name__}


def register(ctx: Any) -> dict[str, Any]:
    del ctx
    return {
        "routes": [("GET", ROUTE, data), ("POST", ROUTE, control)],
        "limbs": [],
        "panes": [{
            "id": "dock.swarm",
            "slot": "dock",
            "title": "Swarm",
            "data": "GET /api/swarm",
            "refresh_s": 20,
            "inner_scroll": False,
        }],
        "health": health,
        "startup": None,
        "shutdown": None,
    }
