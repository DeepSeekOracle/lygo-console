"""The autonomous task runtime for the LYGO LLM console.

Read `RULES.md` in this directory first: it is the ten-rule contract from Engineering Guide §13 and
every module here is a consequence of one of them. The short version is that a turn is not a unit
of work - a contract is.

Modules, in dependency order:

    config     RuntimeConfig: the §9 block as a dataclass
    store      SQLite (WAL) goals/tasks/task_steps/events/workers + the §5.2 API
    contract   completion-contract schema, validation, the §5.1 Goal Manager
    judge      the seven check types, Verdict, evaluate(), render_continuation()
    model      turn adapters: ScriptedTurn (tests), CmdTurn (external process)
    tools_bridge  idempotent tool execution through the kit's tools.dispatch
    worker     worker_main: the §6 continuation loop
    dispatcher dispatcher_loop: claim, spawn, reap; never calls the model
    watcher    watcher_loop: the events outbox reader; never mutates tasks

Nothing in this package imports ComfyUI, restarts a service or edits an existing kit file.
Attributes are resolved lazily so importing `auton` costs one sqlite3 import.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "RuntimeConfig",
    "Store",
    "StoreError",
    "IllegalTransition",
    "Verdict",
    "evaluate",
    "worker_main",
    "WorkerOutcome",
    "dispatcher_loop",
    "watcher_loop",
    "build_contract",
    "validate_contract",
    "ScriptedTurn",
    "CmdTurn",
    "execute_tool_idempotent",
]

_EXPORTS = {
    "RuntimeConfig": ("config", "RuntimeConfig"),
    "Store": ("store", "Store"),
    "StoreError": ("store", "IllegalTransition"),
    "IllegalTransition": ("store", "IllegalTransition"),
    "Verdict": ("judge", "Verdict"),
    "evaluate": ("judge", "evaluate"),
    "worker_main": ("worker", "worker_main"),
    "WorkerOutcome": ("worker", "WorkerOutcome"),
    "dispatcher_loop": ("dispatcher", "dispatcher_loop"),
    "watcher_loop": ("watcher", "watcher_loop"),
    "build_contract": ("contract", "build_contract"),
    "validate_contract": ("contract", "validate_contract"),
    "ScriptedTurn": ("model", "ScriptedTurn"),
    "CmdTurn": ("model", "CmdTurn"),
    "execute_tool_idempotent": ("tools_bridge", "execute_tool_idempotent"),
}

__version__ = "1.0.0"


def __getattr__(name: str) -> Any:  # pragma: no cover - thin PEP 562 shim
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'auton' has no attribute {name!r}")
    import importlib

    module = importlib.import_module(f".{target[0]}", __name__)
    return getattr(module, target[1])
