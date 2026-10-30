"""Idempotent tool execution: the shim every side effect goes through (§5.7, rule 4).

Why this module exists at all: a worker that dies mid-turn is replayed from its last checkpoint.
If the tool it called had already charged a card or written an artifact, the replay must not do it
twice. The kit's `tools.dispatch` knows nothing about that, so the runtime wraps it.

The contract:

* the key is `sha256(task_id, seq, kind, canonical_json(args))` - deterministic across processes,
  because `json.dumps(sort_keys=True)` is;
* `task_steps.idempotency_key` is UNIQUE, so the database itself is the dedupe ledger;
* a completed step returns its stored output with `replayed: true` and never calls the tool again.

Honest limitation: a crash between the side effect and the "complete" write leaves a `pending` row.
That window cannot be closed without the tool participating (an outbox, or a native idempotency
key). The shim is therefore *exactly once for completed calls*, at-least-once for the one call in
flight, which is the strongest guarantee a generic wrapper can make. `pending` rows are logged.

The kit is not always importable (a bare checkout, a test machine, a frozen build). When
`tools.dispatch` cannot be imported this module returns a named error dict instead of raising: the
worker records the tool result and keeps its own failure accounting, exactly as it does for any
other tool error.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable, Mapping

log = logging.getLogger("auton.bridge")

#: Separator for the key material. A unit separator cannot appear in a tool name or a JSON value,
#: so `("a","bc")` and `("ab","c")` can never collide.
_SEP = "\x1f"

DispatchFn = Callable[..., dict[str, Any]]

_cached: Any = None
_resolved = False


def canonical_json(value: Any) -> str:
    """Stable JSON for hashing: sorted keys, no whitespace, UTF-8 preserved."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def idempotency_key(task_id: str, seq: int, kind: str, args: Any) -> str:
    """sha256(task_id + seq + kind + canonical(args)), per §5.2."""
    material = _SEP.join((str(task_id), str(int(seq)), str(kind), canonical_json(args)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def resolve_kit_dispatch() -> DispatchFn | None:
    """Return the console's `tools.dispatch`, or None when this checkout cannot provide it.

    Imported lazily and cached: `src/tools.py` is heavy (it pulls paths, guards and the whole limb
    layer) and most tests inject their own dispatcher.
    """
    global _cached, _resolved
    if _resolved:
        return _cached
    _resolved = True
    try:
        from tools import dispatch  # src/tools.py:507 - the console's single tool door

        _cached = dispatch
    except Exception as exc:  # noqa: BLE001 - absence is a state, not a crash
        log.info("auton.bridge kit dispatch unavailable: %s: %s", type(exc).__name__, exc)
        _cached = None
    return _cached


def call_args(call: Mapping[str, Any]) -> dict[str, Any]:
    args = call.get("arguments", call.get("args", {})) or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"value": args}
    return dict(args)


def call_name(call: Mapping[str, Any]) -> str:
    return str(call.get("name") or call.get("tool") or "")


def execute_tool_idempotent(
    call: Mapping[str, Any],
    store,
    task_id: str,
    seq: int,
    dispatcher: DispatchFn | None = None,
) -> dict[str, Any]:
    """Run one tool call at most once for its `(task_id, seq, name, args)` identity.

    Returns the tool's output dict, always shaped as a dict. On replay the stored output comes back
    with `replayed: true`; on an unimportable kit the result is
    `{"ok": false, "error": "dispatch_unavailable", ...}` and nothing is raised.
    """
    name = call_name(call)
    args = call_args(call)
    if not name:
        return {"ok": False, "error": "missing_tool_name", "call": dict(call)}

    key = idempotency_key(task_id, seq, name, args)
    existing = store.find_step_by_idem(key)
    if existing is not None:
        stored = existing.output if isinstance(existing.output, dict) else {"result": existing.output}
        if stored.get("state") == "pending":
            # The worker died after writing the intent but before recording the result. Re-run:
            # at-least-once is the honest guarantee here (see module docstring).
            log.warning("auton.bridge replaying a pending tool call seq=%s tool=%s", existing.seq, name)
        else:
            out = dict(stored)
            out["replayed"] = True
            out["idempotency_key"] = key
            log.info("auton.bridge replayed seq=%s tool=%s", existing.seq, name)
            return out

    # 1. Checkpoint the intent before the side effect (§5.7).
    store.checkpoint(
        task_id,
        "tool_call",
        {"name": name, "arguments": args},
        {"state": "pending", "idempotency_key": key},
        idem_key=key,
        seq=int(seq),
    )

    fn = dispatcher if dispatcher is not None else resolve_kit_dispatch()
    if fn is None:
        output: dict[str, Any] = {
            "ok": False,
            "error": "dispatch_unavailable",
            "tool": name,
            "hint": "pass dispatcher=..., or run inside the kit so `from tools import dispatch` works",
        }
    else:
        try:
            raw = fn(name, args)
            output = raw if isinstance(raw, dict) else {"ok": True, "result": raw}
        except Exception as exc:  # noqa: BLE001 - a tool exception is a tool result
            log.exception("auton.bridge tool %s raised", name)
            output = {"ok": False, "error": "tool_exception", "tool": name, "detail": f"{type(exc).__name__}: {exc}"}

    output.setdefault("ok", True)
    output["idempotency_key"] = key

    # 2. Record the result against the same step (§5.7 "after the side effect returns").
    store.update_step_output(task_id, int(seq), output)
    return output
