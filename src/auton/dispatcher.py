"""The Dispatcher: claim, spawn, reap. Never calls the model (§5.3, rule 3).

It is a supervisor, not a worker. Its whole job is to keep the number of live workers at or below
`max_concurrent_workers` while nothing sits in `queued` longer than it has to, and to notice when a
lease has died so the task can be retried.

Two details worth spelling out:

* **Workers are separate processes** (`multiprocessing` with the `spawn` start method). A worker
  that segfaults, allocates 40 GB or gets killed by the operator cannot take the dispatcher with
  it - §5.3's isolation requirement and §7's supervisor-tree pattern. A `spawn_fn` parameter keeps
  the loop testable without paying for real processes in every unit test.
* **The lease is not the only liveness signal.** A worker hung inside a tool call still heartbeats,
  so its lease never expires while it makes no progress. The stale-progress sweep reaps those
  (batched to once a minute so it costs one indexed query, not one per poll).

On a requeue the task goes `claimed|running -> retrying -> queued`, incrementing `attempt_count`;
when attempts are exhausted it is `failed` with `reason: lease_expired`. That is §5.3's policy
routed through §4's legal edges - see `store.requeue_stale` for why the intermediate state exists.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import RuntimeConfig
from .store import Store, log_ctx, now_ms

log = logging.getLogger("auton.dispatcher")

#: How often the (comparatively expensive) stale-progress sweep runs.
STALE_SWEEP_INTERVAL_MS = 60_000


@dataclass
class DispatcherStats:
    polls: int = 0
    claims: int = 0
    spawned: int = 0
    reaped: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    finished: int = 0
    errors: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "polls": self.polls,
            "claims": self.claims,
            "spawned": self.spawned,
            "reaped": len(self.reaped),
            "reaped_ids": list(self.reaped),
            "stale": len(self.stale),
            "stale_ids": list(self.stale),
            "finished": self.finished,
            "errors": self.errors,
        }


def new_worker_id() -> str:
    return f"w-{uuid.uuid4().hex[:12]}"


class WorkerHandle:
    """Uniform view over a spawned process (or a test double standing in for one)."""

    def __init__(self, task_id: str, worker_id: str, proc: Any) -> None:
        self.task_id = task_id
        self.worker_id = worker_id
        self.proc = proc

    def alive(self) -> bool:
        is_alive = getattr(self.proc, "is_alive", None)
        if callable(is_alive):
            return bool(is_alive())
        return bool(getattr(self.proc, "alive", True))


def spawn_worker(cfg: RuntimeConfig, task_id: str, worker_id: str, turn_cmd: str | None = None) -> multiprocessing.Process:
    """Spawn one worker process (§5.3 isolation). Importable so a caller can use it directly."""
    ctx = multiprocessing.get_context("spawn")
    cfg_dict = cfg.to_dict()
    if turn_cmd:
        cfg_dict["turn_cmd"] = turn_cmd
    proc = ctx.Process(
        target=_spawn_entry,
        args=(cfg_dict, task_id, worker_id),
        name=f"auton-{task_id[:8]}",
        daemon=False,
    )
    proc.start()
    return proc


def _spawn_entry(cfg_dict: dict[str, Any], task_id: str, worker_id: str) -> None:  # pragma: no cover - child
    """Child entry point. Rebuilds the config, fixes sys.path, then runs exactly one task."""
    cfg = RuntimeConfig.from_dict(cfg_dict)
    for path in reversed(list(cfg.pythonpath or [])):
        if path and path not in sys.path:
            sys.path.insert(0, path)
    try:
        from auton.model import turn_from_config
        from auton.worker import worker_main

        worker_main(task_id, worker_id, cfg=cfg, turn_fn=turn_from_config(cfg))
    except BaseException:  # noqa: BLE001 - the child must log and exit, never hang the parent
        log.exception("auton.dispatcher worker crashed task=%s worker=%s", task_id, worker_id)
        raise


def dispatcher_loop(
    cfg: RuntimeConfig | None = None,
    store: Store | None = None,
    spawn_fn: Callable[[RuntimeConfig, str, str], Any] | None = None,
    should_stop: Callable[[], bool] | None = None,
    max_iterations: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    duration_s: float | None = None,
    sweep_stale: bool = True,
) -> DispatcherStats:
    """§5.3's loop. Returns counters, so a CLI run can report what it actually did.

    Stops on `should_stop()`, after `max_iterations` polls, or after `duration_s` seconds. The
    last two exist so `tools/auton.py dispatch --seconds N` and the tests can bound a run without
    signals.
    """
    cfg = cfg or RuntimeConfig()
    store = store or Store(cfg.sqlite_path)
    spawn = spawn_fn or (lambda c, tid, wid: spawn_worker(c, tid, wid))
    stats = DispatcherStats()
    active: list[WorkerHandle] = []
    started = time.monotonic()
    last_sweep = 0

    try:
        while True:
            if should_stop is not None and should_stop():
                break
            if max_iterations is not None and stats.polls >= int(max_iterations):
                break
            if duration_s is not None and (time.monotonic() - started) >= float(duration_s):
                break

            stats.polls += 1
            now = now_ms()

            # ---- 1. Reap leases (§5.3 step 1) --------------------------------
            try:
                stats.reaped.extend(store.requeue_stale(now))
                if sweep_stale and (now - last_sweep) >= STALE_SWEEP_INTERVAL_MS:
                    last_sweep = now
                    stats.stale.extend(store.reap_stale_progress(now - int(cfg.stale_progress_ms)))
            except Exception:  # noqa: BLE001 - one bad row must not stop the supervisor
                stats.errors += 1
                log.exception("auton.dispatcher reap failed")

            # ---- 2. Reap finished workers ------------------------------------
            still: list[WorkerHandle] = []
            for handle in active:
                if handle.alive():
                    still.append(handle)
                else:
                    stats.finished += 1
                    try:
                        handle.proc.join(timeout=0.01)
                    except Exception:  # noqa: BLE001
                        pass
            active = still

            # ---- 3. Spawn up to the concurrency limit ------------------------
            no_work = False
            while len(active) < int(cfg.max_concurrent_workers):
                worker_id = new_worker_id()
                try:
                    task = store.claim_task(worker_id=worker_id, lease_ms=cfg.lease_ms)
                except Exception:  # noqa: BLE001
                    stats.errors += 1
                    log.exception("auton.dispatcher claim failed")
                    break
                if task is None:
                    no_work = True
                    break
                stats.claims += 1
                try:
                    store.register_worker(worker_id, pid=os.getpid())
                    store.bind_worker(worker_id, task.id)
                    proc = spawn(cfg, task.id, worker_id)
                except Exception:  # noqa: BLE001 - a failed spawn must return the task to the queue
                    stats.errors += 1
                    log.exception("auton.dispatcher spawn failed task=%s", task.id)
                    try:
                        store.transition(task.id, "claimed", "retrying")
                        store.transition(task.id, "retrying", "queued")
                    except Exception:  # noqa: BLE001
                        pass
                    break
                active.append(WorkerHandle(task.id, worker_id, proc))
                stats.spawned += 1
                log.info(log_ctx("dispatcher", task_id=task.id, worker_id=worker_id, status="spawned"))

            # Nothing running and nothing to claim: the queue is finished, so a bounded run should
            # return instead of polling out its whole window (measured 2026-10-17: a 1-hour window
            # spent 1800 polls doing nothing after the single task finished).
            if no_work and not active:
                log.info(log_ctx("dispatcher", status="idle", polls=stats.polls))
                break

            sleep_fn(float(cfg.poll_interval_s))
    finally:
        # Do not leave orphans behind: give them a moment, then terminate (§7 supervisor tree).
        for handle in active:
            try:
                if handle.alive():
                    terminate = getattr(handle.proc, "terminate", None)
                    if callable(terminate):
                        terminate()
            except Exception:  # noqa: BLE001
                pass
    return stats
