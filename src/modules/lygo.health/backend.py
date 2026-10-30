"""lygo.health — adapter over the kernel's own status facts.

Δ9Φ963-LYGO-MODULE-CORE-v1

M1 BEHAVIOUR: this module owns no HTTP route on purpose (see module.json `notes`). It answers the
question "is this console healthy, and on which edition?" from facts the kernel already publishes,
and it is the module every other module's health is read next to in /api/modules.

No state, no network, no engine: this adapter is safe to call at any time, including during boot.
"""

from __future__ import annotations

from typing import Any


def _counts(ctx: Any) -> dict[str, Any]:
    """Cheap, honest counts: what exists on disk, not what should exist."""
    out: dict[str, Any] = {}
    try:
        from paths import KIT_ROOT, SAVE, VERSION_FILE  # type: ignore[attr-defined]
    except Exception:
        try:
            from paths import KIT_ROOT, SAVE
        except Exception:
            return {"kit_root": "unavailable", "save": "unavailable", "version_file": "unavailable"}
    try:
        out["kit_root"] = str(KIT_ROOT)
        out["save"] = SAVE.is_dir()
        version_file = KIT_ROOT / "VERSION"
        out["version_file"] = version_file.is_file()
    except Exception as exc:  # noqa: BLE001
        out["probe_error"] = str(exc)[:120]
    return out


def health(ctx: Any) -> dict[str, Any]:
    """The status answer for this module.

    `ok` is False only for something that is really wrong (the kit root or the VERSION file is not
    there). A missing save/ directory is not a fault: it is created on first write.
    """
    facts = _counts(ctx)
    ok = bool(facts.get("version_file", False))
    return {
        "ok": ok,
        "detail": (
            f"{ctx.edition} · release {ctx.release} · build {ctx.build}"
            if ok
            else "VERSION file not found in the kit root: this is not a complete kit"
        ),
        "edition": ctx.edition,
        "release": ctx.release,
        "build": ctx.build,
        "kernel_route": "GET /api/modules is served by the module host",
        **facts,
    }


def register(ctx: Any) -> dict[str, Any]:
    return {
        "routes": [],
        "limbs": [],
        "panes": [],
        "health": health,
        "startup": None,
        "shutdown": None,
    }
