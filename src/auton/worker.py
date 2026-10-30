"""The Worker: one task, many turns, no trust in prose (§5.4, §6).

This is where announce-and-stall dies. The unit of work is not the turn; it is the contract. After
every turn the worker asks the Judge "is it satisfied?" and, when it is not, feeds the failing checks
back in and runs another turn. The model never decides to continue and never decides to finish.

The loop, in the order the guide specifies:

    budget -> heartbeat -> context -> turn -> checkpoints -> tools -> judge -> branch

with a checkpoint for every meaningful action (`llm_call`, `tool_call`, `judgment`, `note`) so a
replay from `last_seq` is possible and so the no-progress fingerprint has something to read.

Three rules get their own code because they are easy to get subtly wrong:

* **A prose-only turn is a no-op.** It is still counted against `used_turns` (it cost a turn), it
  resets nothing, and two in a row trip the circuit breaker.
* **`task.complete` does not exist.** If a model emits it anyway the call is recorded as a refused
  tool result and the turn continues to the Judge like any other. Nothing else in the runtime reads
  that name.
* **`requires_human_review` never auto-completes.** The judge returns blocked for a satisfied
  contract that demands review; the worker emits `needs_review` and pauses the goal.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from . import judge as judge_mod
from .config import RuntimeConfig
from .model import Turn, TurnError, as_turn
from .store import (
    NON_TERMINAL_STATES,
    Goal,
    Step,
    Store,
    Task,
    legal_transition,
    log_ctx,
    now_ms,
)
from .tools_bridge import execute_tool_idempotent

log = logging.getLogger("auton.worker")

#: §8, verbatim. Exactly three model-callable controls; `task.complete` is explicitly forbidden.
CONTROL_TOOLS: list[dict[str, Any]] = [
    {
        "name": "task.block",
        "description": "Declare the current task blocked. Provide a reason. Only use when a required input is missing or a hard error occurred.",
        "parameters": {"reason": {"type": "string"}},
    },
    {
        "name": "task.note",
        "description": "Record a short progress note. Does not complete the task.",
        "parameters": {"note": {"type": "string"}},
    },
    {
        "name": "goal.status",
        "description": "Read-only. Returns current goal text, contract, failing checks, turn budget remaining.",
        "parameters": {},
    },
]

CONTROL_NAMES: frozenset[str] = frozenset({"task.block", "task.note", "goal.status"})

#: §8 "Explicitly forbidden: any task.complete tool."
FORBIDDEN_NAMES: frozenset[str] = frozenset({"task.complete", "task.done", "task.finish"})


@dataclass
class WorkerOutcome:
    task_id: str
    status: str
    reason: str = ""
    turns: int = 0
    tokens: int = 0
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.status in {"done", "blocked", "failed", "cancelled"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "reason": self.reason,
            "turns": self.turns,
            "tokens": self.tokens,
            "detail": self.detail,
        }


class LeaseLost(RuntimeError):
    """The heartbeat says another worker owns this task now. Stop without writing state."""


# ---------------------------------------------------------------------------------------------
# Heartbeat (§5.3: every lease_ms/3, in its own thread so a slow tool cannot starve it)
# ---------------------------------------------------------------------------------------------
class _Heartbeat(threading.Thread):
    def __init__(self, store: Store, task_id: str, worker_id: str, interval_ms: int) -> None:
        super().__init__(name=f"auton-heartbeat-{task_id[:8]}", daemon=True)
        self.store = store
        self.task_id = task_id
        self.worker_id = worker_id
        self.interval_s = max(0.02, int(interval_ms) / 1000.0)
        self.lost = threading.Event()
        self._stop = threading.Event()

    def run(self) -> None:  # pragma: no cover - timing dependent, asserted through `lost`
        while not self._stop.wait(self.interval_s):
            try:
                if not self.store.heartbeat(self.task_id, self.worker_id, self.lease_ms):
                    self.lost.set()
                    return
            except Exception:  # noqa: BLE001 - a heartbeat error must not kill the worker thread
                log.exception(log_ctx("worker", task_id=self.task_id, worker_id=self.worker_id, reason="heartbeat_error"))

    def stop(self) -> None:
        self._stop.set()


def start_heartbeat(store: Store, task_id: str, worker_id: str, interval_ms: int, lease_ms: int) -> _Heartbeat:
    beat = _Heartbeat(store, task_id, worker_id, interval_ms)
    beat.lease_ms = int(lease_ms)
    beat.start()
    return beat


def stop_heartbeat(beat: _Heartbeat | None) -> None:
    if beat is not None:
        beat.stop()


# ---------------------------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------------------------
def goal_status(store: Store, goal: Goal, task: Task | None = None, failing: list[Any] | None = None) -> dict[str, Any]:
    """The payload of the read-only `goal.status` control (§8)."""
    budget = goal.budget or {}
    max_turns = int(budget.get("max_turns", 0) or 0)
    return {
        "goal_id": goal.id,
        "text": goal.text,
        "status": goal.status,
        "contract": goal.contract,
        "failing_checks": [
            {"description": getattr(r, "description", str(r)), "evidence": getattr(r, "evidence", "")}
            for r in (failing or [])
        ],
        "turns_used": goal.used_turns,
        "turns_remaining": max(0, max_turns - goal.used_turns) if max_turns else None,
        "tokens_used": goal.used_tokens,
        "task": {"id": task.id, "status": task.status, "attempt": task.attempt_count} if task else None,
    }


def build_context(goal: Goal, task: Task, steps: list[Step], continuation: str | None = None) -> dict[str, Any]:
    """What the model sees: the objective, the contract, the recent checkpoints, the continuation.

    Deliberately not the transcript. Checkpoints are the durable record; anything not checkpointed
    did not happen as far as the runtime is concerned.
    """
    return {
        "goal": {
            "id": goal.id,
            "text": goal.text,
            "contract": goal.contract,
            "budget": goal.budget,
            "used_turns": goal.used_turns,
            "used_tokens": goal.used_tokens,
        },
        "task": {
            "id": task.id,
            "goal_id": task.goal_id,
            "status": task.status,
            "payload": task.payload,
            "progress_note": task.progress_note,
            "attempt": task.attempt_count,
            "max_attempts": task.max_attempts,
        },
        "steps": [{"seq": s.seq, "kind": s.kind, "input": s.input, "output": s.output} for s in steps],
        "continuation": continuation,
        "control_tools": CONTROL_TOOLS,
    }


# ---------------------------------------------------------------------------------------------
# Budgets (§5.4 step 2, rule 6)
# ---------------------------------------------------------------------------------------------
def budget_reason(goal: Goal, task: Task, started_ms: int, cfg: RuntimeConfig) -> str | None:
    """Why this turn must not start, or None. Checked before every turn, never mid-turn."""
    budget = goal.budget or {}
    max_turns = int(budget.get("max_turns", cfg.budgets.default_max_turns) or 0)
    max_wall_s = int(budget.get("max_wall_s", cfg.budgets.default_max_wall_s) or 0)
    max_tokens = int(budget.get("max_tokens", cfg.budgets.default_max_tokens) or 0)
    if max_turns and goal.used_turns >= max_turns:
        return "turn_budget_exhausted"
    if max_wall_s and (now_ms() - int(started_ms)) >= max_wall_s * 1000:
        return "wall_clock_exhausted"
    if max_tokens and goal.used_tokens >= max_tokens:
        return "token_budget_exhausted"
    return None


# ---------------------------------------------------------------------------------------------
# Finish helpers
# ---------------------------------------------------------------------------------------------
def _event_kind_for(status: str, result: Mapping[str, Any]) -> str:
    if status == "done":
        return "done"
    if status == "failed":
        return "failed"
    if status == "blocked" and str(result.get("reason") or "").startswith("requires_human_review"):
        return "needs_review"
    return "blocked"


def refresh_goal_status(store: Store, goal_id: str) -> str | None:
    """Roll the tasks up into the goal once nothing is left running. Never overwrites `paused`."""
    goal = store.get_goal(goal_id)
    if goal is None or goal.status in {"paused", "cancelled"}:
        return goal.status if goal else None
    tasks = store.list_tasks(goal_id=goal_id)
    if not tasks or any(t.status in NON_TERMINAL_STATES for t in tasks):
        return goal.status
    statuses = {t.status for t in tasks}
    if statuses == {"done"}:
        new = "done"
    elif "failed" in statuses:
        new = "failed"
    elif "blocked" in statuses:
        new = "blocked"
    elif "cancelled" in statuses:
        new = "cancelled"
    else:
        new = goal.status
    store.set_goal_status(goal_id, new)
    return new


def finish(store: Store, task: Task, status: str, result: Mapping[str, Any] | None = None) -> bool:
    """Move the task to a terminal state and emit exactly one event for the move.

    Idempotent by construction: the transition is compare-and-set, and the event is only appended
    when this call actually performed the move. That is what makes M5's "exactly one done event"
    hold even if a worker is replayed.
    """
    result = dict(result or {})
    current = store.get_task(task.id)
    if current is None:
        return False
    if current.status == status:
        return True
    if not legal_transition(current.status, status):
        log.error(
            log_ctx("worker", task_id=task.id, goal_id=task.goal_id, status="illegal_finish",
                    reason=f"{current.status}->{status}")
        )
        return False
    moved = store.transition(task.id, current.status, status, result=result)
    if moved:
        kind = _event_kind_for(status, result)
        store.append_event(task.id, task.goal_id, kind, result)
        if kind == "needs_review":
            store.set_goal_status(task.goal_id, "paused")
        else:
            refresh_goal_status(store, task.goal_id)
        log.info(
            log_ctx("worker", task_id=task.id, goal_id=task.goal_id, status=status,
                    reason=result.get("reason") or kind)
        )
    return moved


# ---------------------------------------------------------------------------------------------
# Control tools (§8)
# ---------------------------------------------------------------------------------------------
def _control_result(
    store: Store,
    task: Task,
    goal: Goal,
    call: Mapping[str, Any],
    seq: int,
) -> tuple[dict[str, Any], str | None]:
    """Handle a control call. Returns (tool output, finish_status_or_None).

    Only three names arrive here. `task.complete` never does - it is refused before this point.
    """
    name = str(call.get("name"))
    args = dict(call.get("arguments") or {})
    if name == "task.block":
        reason = str(args.get("reason") or "no reason given")
        store.checkpoint(task.id, "note", {"control": name, "reason": reason}, {"blocked": reason}, seq=seq)
        return {"ok": True, "control": name, "reason": reason}, "blocked"
    if name == "task.note":
        note = str(args.get("note") or "")
        store.set_progress_note(task.id, note[:2000])
        store.checkpoint(task.id, "note", {"control": name}, {"note": note[:2000]}, seq=seq)
        return {"ok": True, "control": name, "note": note[:2000]}, None
    if name == "goal.status":
        payload = goal_status(store, goal, task)
        store.checkpoint(task.id, "tool_call", {"control": name}, payload, seq=seq)
        return {"ok": True, "control": name, **payload}, None
    return {"ok": False, "error": "unknown_control", "tool": name}, None


# ---------------------------------------------------------------------------------------------
# worker_main
# ---------------------------------------------------------------------------------------------
def worker_main(
    task_id: str,
    worker_id: str | None = None,
    cfg: RuntimeConfig | None = None,
    turn_fn: Callable[[Mapping[str, Any]], Any] | None = None,
    store: Store | None = None,
    dispatcher: Callable[..., dict[str, Any]] | None = None,
    judge_fn: Callable[[str], Any] | None = None,
    base_dir: str | Path | None = None,
    halt_after_turns: int | None = None,
    raise_on_error: bool = False,
) -> WorkerOutcome:
    """Execute exactly one task until the Judge says done/blocked/failed or a budget runs out.

    `halt_after_turns` is a harness knob: it returns *without writing anything*, leaving the task
    `running` with a live lease - byte-for-byte the state a `kill -9` leaves behind, which is what
    the M4 test needs. Nothing in the dispatcher sets it.
    """
    cfg = cfg or RuntimeConfig()
    store = store or Store(cfg.sqlite_path)
    worker_id = worker_id or f"inline-{os.getpid()}-{int(time.time() * 1000) % 100000}"
    task = store.get_task(task_id)
    if task is None:
        return WorkerOutcome(task_id, "missing", "task_not_found")

    if task.terminal:
        return WorkerOutcome(task_id, task.status, "already_terminal")

    if task.worker_id and task.worker_id != worker_id and task.status in {"claimed", "running"}:
        # The claim is the authority on who owns the lease: a worker that was handed the wrong id
        # would heartbeat against a lease it does not hold and abort at its first check.
        log.warning(
            log_ctx("worker", task_id=task.id, worker_id=worker_id, status="adopting_claim_owner",
                    reason=f"claimed by {task.worker_id}")
        )
        worker_id = task.worker_id

    if task.status == "queued":
        # The dispatcher claims; a worker that starts without a claim is a bug worth naming.
        return WorkerOutcome(task_id, "not_claimed", "task_is_queued")
    if task.status == "retrying":
        return WorkerOutcome(task_id, "not_claimed", "task_is_retrying")

    if task.status == "claimed":
        if not store.transition(task.id, "claimed", "running"):
            fresh = store.get_task(task.id)
            if fresh is None or fresh.status != "running":
                return WorkerOutcome(task_id, fresh.status if fresh else "missing", "claim_lost")
        task = store.get_task(task_id) or task

    if turn_fn is None:
        from .model import turn_from_config

        turn_fn = turn_from_config(cfg)
    if turn_fn is None:
        # No adapter at all: fail honestly rather than pretend a turn happened.
        finish(store, task, "failed", {"reason": "no_turn_adapter"})
        return WorkerOutcome(task_id, "failed", "no_turn_adapter")

    store.register_worker(worker_id, pid=os.getpid())
    store.bind_worker(worker_id, task.id)

    beat = start_heartbeat(store, task.id, worker_id, cfg.heartbeat_ms(), cfg.lease_ms)
    started_ms = int(task.created_at or now_ms())
    turns = 0
    tokens = 0
    noop_streak = 0
    continuation: str | None = (task.payload or {}).get("continuation")
    detail: dict[str, Any] = {}

    try:
        while True:
            goal = store.get_goal(task.goal_id)
            if goal is None:
                finish(store, task, "failed", {"reason": "goal_missing", "goal_id": task.goal_id})
                return WorkerOutcome(task.id, "failed", "goal_missing")
            task = store.get_task(task.id) or task

            # ---- budget checks, before every turn (rule 6) -------------------
            reason = budget_reason(goal, task, started_ms, cfg)
            if reason:
                finish(store, task, "blocked", {"reason": reason, "turns": turns})
                return WorkerOutcome(task.id, "blocked", reason, turns, tokens, detail)

            if beat.lost.is_set():
                return WorkerOutcome(task.id, "lease_lost", "heartbeat_lease_lost", turns, tokens, detail)
            if not store.heartbeat(task.id, worker_id, cfg.lease_ms):
                return WorkerOutcome(task.id, "lease_lost", "lease_lost", turns, tokens, detail)

            if halt_after_turns is not None and turns >= int(halt_after_turns):
                # Simulated kill: no writes, lease still live, status still running.
                detail["halted"] = True
                return WorkerOutcome(task.id, "halted", "halted_by_harness", turns, tokens, detail)

            # ---- one turn ----------------------------------------------------
            ctx = build_context(goal, task, store.recent_steps(task.id, n=20), continuation)
            try:
                turn: Turn = as_turn(turn_fn(ctx))
            except TurnError as exc:
                store.checkpoint(task.id, "note", {"turn": turns + 1}, {"turn_error": f"{type(exc).__name__}: {exc}"})
                finish(store, task, "failed", {"reason": "turn_adapter_failed", "detail": str(exc)})
                return WorkerOutcome(task.id, "failed", "turn_adapter_failed", turns, tokens, detail)

            turns += 1
            goal.used_turns = store.increment_goal_turns(goal.id)
            if turn.tokens:
                goal.used_tokens = store.increment_goal_tokens(goal.id, turn.tokens)
                tokens += int(turn.tokens)

            seq = store.last_seq(task.id) + 1
            store.checkpoint(
                task.id,
                "llm_call",
                {"turn": turns, "continuation": bool(continuation), "steps": len(ctx["steps"])},
                turn.to_dict(),
                seq=seq,
            )

            # ---- tool calls, each idempotent ---------------------------------
            if not turn.tool_calls:
                noop_streak += 1
                detail["noop_turns"] = noop_streak
                log.info(log_ctx("worker", task_id=task.id, goal_id=goal.id, worker_id=worker_id,
                                 seq=seq, reason="noop_turn"))
            else:
                noop_streak = 0

            for call in turn.tool_calls:
                name = str(call.get("name") or "")
                seq = store.last_seq(task.id) + 1
                if name in FORBIDDEN_NAMES:
                    # §8: the model cannot declare completion. Record the refusal, keep going.
                    store.checkpoint(
                        task.id,
                        "tool_call",
                        call,
                        {"ok": False, "error": "forbidden_tool", "tool": name,
                         "detail": "only the Judge may complete a task; use task.block if genuinely stuck"},
                        seq=seq,
                    )
                    log.warning(log_ctx("worker", task_id=task.id, worker_id=worker_id, seq=seq,
                                        reason="forbidden_tool", tool=name))
                    continue
                if name in CONTROL_NAMES:
                    output, control_finish = _control_result(store, task, goal, call, seq)
                    if control_finish == "blocked":
                        reason = str((call.get("arguments") or {}).get("reason") or "no reason given")
                        finish(store, task, "blocked", {"reason": f"model_blocked:{reason}", "source": "task.block"})
                        return WorkerOutcome(task.id, "blocked", "model_blocked", turns, tokens, detail)
                    detail.setdefault("controls", []).append(name)
                    continue
                output = execute_tool_idempotent(call, store, task.id, seq, dispatcher=dispatcher)
                detail["tool_calls"] = int(detail.get("tool_calls", 0)) + 1
                if not output.get("ok", True):
                    detail["tool_errors"] = int(detail.get("tool_errors", 0)) + 1

            # ---- judge -------------------------------------------------------
            task = store.get_task(task.id) or task
            verdict = judge_mod.evaluate(
                goal.contract,
                task,
                store,
                cfg,
                judge_fn=judge_fn,
                base_dir=base_dir,
                verifiers_dir=cfg.verifiers_dir,
            )
            seq = store.last_seq(task.id) + 1
            store.checkpoint(task.id, "judgment", {"turn": turns}, verdict.to_dict(), seq=seq)
            detail["last_verdict"] = verdict.status
            detail["last_reason"] = verdict.reason

            if verdict.status == "done":
                finish(store, task, "done", {"reason": "all checks passed", "evidence": verdict.evidence})
                return WorkerOutcome(task.id, "done", "all checks passed", turns, tokens, detail)
            if verdict.status == "blocked":
                finish(store, task, "blocked", {"reason": verdict.reason, "evidence": verdict.evidence})
                return WorkerOutcome(task.id, "blocked", verdict.reason, turns, tokens, detail)
            if verdict.status == "failed":
                finish(store, task, "failed", {"reason": verdict.reason, "evidence": verdict.evidence})
                return WorkerOutcome(task.id, "failed", verdict.reason, turns, tokens, detail)

            # ---- continue ----------------------------------------------------
            store.set_progress_note(task.id, verdict.reason)
            continuation = verdict.continuation_prompt

            # circuit breakers: identical failing set + progress note for K turns
            if verdict.same_state_as_previous(cfg.no_progress_window):
                finish(
                    store,
                    task,
                    "blocked",
                    {"reason": "no_progress", "detail": f"fingerprint unchanged for {cfg.no_progress_window} turns",
                     "fingerprint": verdict.fingerprint, "turns": turns},
                )
                return WorkerOutcome(task.id, "blocked", "no_progress", turns, tokens, detail)
            if noop_streak >= int(cfg.max_noop_turns):
                finish(
                    store,
                    task,
                    "blocked",
                    {"reason": "no_progress", "detail": f"{noop_streak} consecutive turns with no tool call",
                     "turns": turns},
                )
                return WorkerOutcome(task.id, "blocked", "no_progress", turns, tokens, detail)

    except Exception as exc:  # noqa: BLE001 - §5.4's handler: checkpoint, fail, emit, return
        log.exception(log_ctx("worker", task_id=task.id, goal_id=task.goal_id, worker_id=worker_id))
        try:
            store.checkpoint(task.id, "note", None, {"error": f"{type(exc).__name__}: {exc}"})
            current = store.get_task(task.id)
            if current is not None and legal_transition(current.status, "failed"):
                finish(store, task, "failed", {"error": f"{type(exc).__name__}: {exc}"})
        except Exception:  # noqa: BLE001 - never mask the original failure
            log.exception("auton.worker could not record its own failure")
        if raise_on_error:
            raise
        return WorkerOutcome(task.id, "failed", f"exception:{type(exc).__name__}", turns, tokens, detail)
    finally:
        stop_heartbeat(beat)


__all__ = [
    "CONTROL_TOOLS",
    "CONTROL_NAMES",
    "FORBIDDEN_NAMES",
    "WorkerOutcome",
    "budget_reason",
    "build_context",
    "finish",
    "goal_status",
    "refresh_goal_status",
    "start_heartbeat",
    "stop_heartbeat",
    "worker_main",
]
