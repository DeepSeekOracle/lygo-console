"""LYGO Function Modules — the console's plug-in surface.

Δ9Φ963-LYGO-MODULE-CORE-v1

A module is a directory `src/modules/lygo.<name>/` holding a validated `module.json` and a
`backend.py` that exposes `register(ctx)`. `modules/host.py` loads them at boot, owns the single
table of routes/limbs/health, and hands each module only kernel services (see
`MODULE_CONTRACT_SPEC_v1.md`).

Nothing in the console imports a module directly, and no module imports another module's
internals: the only sanctioned coupling is `ctx.bus`.

Import cost: this package imports nothing heavy. `host.load()` decides what gets wired.
"""

from __future__ import annotations

__all__ = ["host", "validate"]

SIGNATURE = "Δ9Φ963-LYGO-MODULE-CORE-v1"
