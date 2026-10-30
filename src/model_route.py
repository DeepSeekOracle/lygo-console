"""Route a turn to something that can actually take it - and name the RIG when nothing can.

The console must never be the thing that fails. Three rules, in this order:

1. SEE everything. Every model on every drive stays in the list even when this machine cannot run it.
2. ATTEMPT, then fail SOFT. A model that will not boot produces a named rig limit (`class: "rig"`) with
   the reason and the arithmetic, never a stack trace and never a dead console. The engine keeps
   running, the session survives, and the operator picks another route.
3. TASK to the best model that CAN take it. Asked for a picture, route to a model with a projector, or
   to a cloud route whose provider accepts images - never to a text model that will answer about a
   photo it never saw.

The API brains are first-class routes, not a fallback shame: the local model is one part of the build,
and a hosted model that accepts an image outranks a local one that cannot. Caps come from measurement
(save/model_check.json, written by model_check.py) for local models and from the provider's published
capabilities for cloud routes - every cloud cap carries its source URL, and an unknown one says unknown
rather than guessing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SAVE = ROOT / "save"
CHECK = SAVE / "model_check.json"
REG = SAVE / "registry.json"

# The routes that are not local models. Caps are what the CONSOLE can send through that route, taken
# from the provider's own documentation (`caps_source`) - never inferred from a model's name.
CLOUD_CAPS: dict[str, dict[str, Any]] = {
    "deepseek": {"caps": ["text", "tools"], "source": "https://api-docs.deepseek.com/"},
    "groq": {"caps": ["text", "tools"], "source": "https://console.groq.com/docs/models"},
    "openai": {"caps": ["text", "tools", "image-in"], "source": "https://platform.openai.com/docs/guides/vision"},
    "xai": {"caps": ["text", "tools", "image-in"], "source": "https://docs.x.ai/docs/models"},
    "gemini": {"caps": ["text", "tools", "image-in", "sound-in"],
               "source": "https://ai.google.dev/gemini-api/docs/vision"},
    "openrouter": {"caps": ["text", "tools", "image-in"], "source": "https://openrouter.ai/docs/features/multimodal/overview"},
    "custom": {"caps": [], "source": "unknown - a custom endpoint's capabilities are the operator's to declare"},
}

# Local non-chat routes the console already owns. Media generation and speech are OUR services, not
# limbs bolted onto the chat engine, so they are routes in their own right.
MEDIA_ROUTES = [
    {"id": "media:image", "caps": ["image-out"], "where": "local media engine (configured media_root)",
     "note": "diffusion backend is a separate service by design"},
    {"id": "media:sound", "caps": ["sound-out"], "where": "local media engine (configured media_root)",
     "note": "speech is rendered by our own engine, not the chat model"},
]


def _json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def cloud_caps() -> dict[str, dict[str, Any]]:
    """The provider capability table. cloud_api owns it; the copy above is only a fallback for when the
    router is imported without the kit on the path (a unit test, a tool). One console, one answer."""
    try:
        import cloud_api

        table = getattr(cloud_api, "PROVIDER_CAPS", None)
        if table:
            return dict(table)
    except Exception:
        pass
    return CLOUD_CAPS


def check_path() -> Path:
    return SAVE / "model_check.json"


def reg_path() -> Path:
    return SAVE / "registry.json"


def results() -> dict[str, Any]:
    """What the checker measured, per model id."""
    return (_json(check_path(), {}) or {}).get("models") or {}


def records() -> list[dict[str, Any]]:
    d = _json(reg_path(), {})
    return list((d.get("models") if isinstance(d, dict) else d) or [])


def model_row(rec: dict[str, Any], res: dict[str, Any] | None = None) -> dict[str, Any]:
    """One model as the router sees it: its labels, and whether this host proved it can run."""
    r = res if res is not None else (results().get(str(rec.get("id"))) or {})
    caps = list(r.get("caps") or rec.get("caps") or [])
    verdict = str(r.get("verdict") or "")
    return {
        "id": rec.get("id"),
        "kind": rec.get("kind"),
        "caps": caps,
        "verdict": verdict or "unchecked",
        "runs": verdict in ("runs",) or (not verdict and bool(rec.get("runnable"))),
        "fail_class": r.get("fail_class"),
        "why": r.get("why"),
        # The operator's next move lives in the hint (a newer engine build, a smaller quant, another
        # route). A panel that shows only the error makes the reader do the diagnosis again.
        "hint": r.get("hint"),
        "gen_tps": (r.get("probe") or {}).get("gen_tps"),
        "boot_s": r.get("boot_s"),
        "aliases": list(rec.get("also_known_as") or rec.get("aliases") or []),
    }


def routes(cap: str | None = None, *, cloud: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every route that can take a turn needing `cap`, best first: proven local models, then cloud.

    Ordering is by MEASURED generation speed where it exists (a proven 6 GB model beats a 21 GB one that
    also "runs"), because the point is to answer the operator, not to win an argument about size.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rec in records():
        path = str(rec.get("path") or "")
        if path and path in seen:
            continue
        if path:
            seen.add(path)
        row = model_row(rec)
        if cap and cap not in row["caps"]:
            continue
        out.append({**row, "route": "local"})
    out.sort(key=lambda r: (not r["runs"], -(r.get("gen_tps") or 0)))

    for name, spec in cloud_caps().items():
        caps = list(spec.get("caps") or [])
        if cap and cap not in caps:
            continue
        connected = None
        if isinstance(cloud, dict):
            connected = bool((cloud.get("providers") or {}).get(name))
        out.append({"id": f"api:{name}", "route": "cloud", "caps": caps,
                    "runs": bool(caps), "gen_tps": None, "boot_s": None, "aliases": [],
                    "caps_source": spec.get("source"), "has_key": connected,
                    "why": "provider capability, per its own docs" if caps else "capabilities unknown"})
    for m in MEDIA_ROUTES:
        if cap and cap not in m["caps"]:
            continue
        out.append({"id": m["id"], "route": "media", "caps": m["caps"], "runs": True,
                    "gen_tps": None, "boot_s": None, "aliases": [], "why": m["note"],
                    "where": m["where"]})
    return out


def best_for(cap: str | None = None, *, cloud: dict[str, Any] | None = None,
             prefer: str | None = None) -> dict[str, Any] | None:
    """The model to task with this turn: the operator's own pick when it can take it, else the best."""
    rs = routes(cap, cloud=cloud)
    if prefer:
        for r in rs:
            if str(r.get("id")) == str(prefer) or prefer in (r.get("aliases") or []):
                if r.get("runs"):
                    return r
    for r in rs:
        if r.get("runs"):
            return r
    return None


def alternatives_for(model_id: str, cap: str | None = None, *, cloud: dict[str, Any] | None = None,
                     limit: int = 4) -> list[dict[str, Any]]:
    """Routes to offer when a boot failed: everything else that can take this turn."""
    return [r for r in routes(cap, cloud=cloud) if str(r.get("id")) != str(model_id)][:limit]


def soft_failure(model: dict[str, Any] | str, why: str, *, cap: str | None = None,
                 cloud: dict[str, Any] | None = None, log_tail: str = "") -> dict[str, Any]:
    """The payload for a boot that did not work: a RIG limit with a way forward, never an agent fault.

    `agent_fault: False` is the load-bearing line. The operator must be able to tell "this box cannot
    hold that model" from "my console is broken", because the first is a route change and the second is
    a support call - and the console answers a perfectly good turn while the message is on screen.
    """
    mid = model if isinstance(model, str) else str((model or {}).get("id"))
    alts = alternatives_for(mid, cap, cloud=cloud)
    return {
        "ok": False,
        "class": "rig",
        "agent_fault": False,
        "model": mid,
        "why": why,
        "log_tail": log_tail,
        "message": (f"{mid} did not boot on this machine: {why}. "
                    f"That is a rig limit, not a fault in the console or the agent - "
                    f"pick another route or connect an API brain."),
        "alternatives": alts,
        "cloud_routes": [r for r in routes(cap, cloud=cloud) if r.get("route") == "cloud" and r.get("caps")][:4],
    }


def gpu_proven() -> dict[str, Any] | None:
    """Proof a GPU actually CARRIED a turn here - never a plan that said it could.

    A fit verdict is arithmetic over sizes: it says a model WOULD fit in the card and nothing more.
    Treating it as proof is how a panel stops reporting a real gap. Measured on this box 2026-09-20:
    the arithmetic said gpu_full for eight models while the engine in use was the CPU-only base build
    (`--list-devices` -> "(none)", `backends.report()["active"]` -> "cpu", cuda and vulkan installed but
    unproven) - the honest answer was the amber that this guard would have silenced. Proof is a device
    the ENGINE saw or layers it actually offloaded, and both come from the launch, not the arithmetic.
    """
    best: dict[str, Any] | None = None
    for mid, res in results().items():
        layers = int(res.get("gpu_layers_used") or 0)
        # ``devices_seen`` became ``gpu_device`` once the checker read the device list from the build that
        # actually answered. An older record's unstated count is still honoured; absence is not a zero.
        devices = 1 if res.get("gpu_device") else int(res.get("devices_seen") or 0)
        if str(res.get("verdict")) == "runs" and (layers > 0 or devices > 0):
            row = {"id": mid, "gen_tps": (res.get("probe") or {}).get("gen_tps"),
                   "gpu_layers_used": layers, "devices_seen": devices}
            if best is None or layers > int(best["gpu_layers_used"]):
                best = row
    return best


def rig_report(*, cloud: dict[str, Any] | None = None) -> dict[str, Any]:
    """The whole picture, for the LLM panel: what runs, what does not, and why - in words."""
    res = results()
    rows = [model_row(rec) for rec in records()]
    runs = [r for r in rows if r["runs"]]
    broken = [r for r in rows if r["verdict"] not in ("runs", "unchecked")]
    rig_limits = [r for r in broken if r.get("fail_class") == "rig"]
    wiring = [r for r in broken if r.get("fail_class") == "wiring"]
    caps: dict[str, int] = {}
    for r in rows:
        for c in r["caps"]:
            caps[c] = caps.get(c, 0) + 1
    return {
        "checked_at": (_json(check_path(), {}) or {}).get("host", {}).get("at"),
        "host": (_json(check_path(), {}) or {}).get("host") or {},
        "models_total": len(rows),
        "models_running": len(runs),
        "caps": caps,
        "rig_limits": rig_limits,
        "wiring_faults": wiring,
        "routes": rows,
        "cloud_routes": [r for r in routes(cloud=cloud) if r.get("route") == "cloud"],
        "media_routes": MEDIA_ROUTES,
        "note": ("A model that will not boot here is a RIG limit: it stays visible, it stays attemptable, "
                 "and the console routes the turn to something that can take it."),
    }
