"""Task queue and worker: work that outlives the turn that asked for it.

Ported in spirit from the Hermes agent loop's liveness family (`turn_liveness.py`, `deadline.py`,
`estop.py`): a long job must not hold the conversation hostage, and a job that stops making
progress must be *visible* as stalled rather than looking busy forever.

Properties this module guarantees
---------------------------------
- `add()` returns immediately, with an id. The agent never waits on the queue.
- One daemon worker runs jobs in order, heart-beating while it works, so a stalled job can be told
  apart from a slow one.
- Every task carries a deadline. Past it, a running task is reported `stalled` and the queue moves
  on - the console cannot be wedged by one job.
- State lives in a JSONL event log (`data/tasks.jsonl`), so the record survives a restart and the
  operator can read what happened without asking the process.

A job is a limb call (`task_add target=self_test args={}`), never a command line: the limb's own
guards and consent rules still apply.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LOG = DATA / "tasks.jsonl"
STALL_AFTER = 90.0          # seconds without a heartbeat while running -> stalled
DEFAULT_DEADLINE = 900.0    # seconds a task may run before it is called stalled
MAX_ROWS = 4000

# RE-ENTRANT, deliberately. MEASURED 2026-10-16: the watchdog (reconcile) holds this lock and then
# appends, and _append() takes the same lock - a self-deadlock that froze `task_recover` past 240 s, so
# the queue's own health check was the one thing that could never answer. A plain Lock was fine only
# while nothing under it ever wrote. RLock keeps the single-writer guarantee AND lets a compound
# operation (read, decide, append) be one atomic step instead of a deadlock.
_lock = threading.RLock()
_worker: "Worker | None" = None


def _now() -> float:
    return time.time()


def _stamp(t: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t if t is not None else _now()))


def _append(row: dict[str, Any]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with _lock:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line)
        # keep the log from growing without bound: fold to the newest MAX_ROWS when it doubles
        if LOG.stat().st_size > 2_000_000:
            rows = _read_rows()[-MAX_ROWS:]
            tmp = LOG.with_suffix(".jsonl.tmp")
            tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
            os.replace(tmp, LOG)


def _read_rows() -> list[dict[str, Any]]:
    if not LOG.is_file():
        return []
    rows = []
    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def fold(rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Fold the event log into current task state, newest first."""
    tasks: dict[str, dict[str, Any]] = {}
    for row in rows if rows is not None else _read_rows():
        tid = str(row.get("id") or "")
        if not tid:
            continue
        t = tasks.setdefault(tid, {"id": tid, "log": []})
        for k, v in row.items():
            if k != "log":
                t[k] = v
        if row.get("event") in ("beat", "run", "add"):
            t.setdefault("log", []).append({"t": row.get("t"), "event": row.get("event"),
                                            "note": row.get("note", "")})
    out = list(tasks.values())
    for t in out:
        if t.get("state") == "running" and (_now() - float(t.get("beat") or t.get("started") or 0)) > STALL_AFTER:
            t["state"] = "stalled"
        t["age_s"] = round(_now() - float(t.get("created") or _now()), 1)
    out.sort(key=lambda t: float(t.get("created") or 0), reverse=True)
    return out


def add(target: str, args: dict[str, Any] | None = None, deadline: float = DEFAULT_DEADLINE,
        note: str = "", origin: str = "console") -> dict[str, Any]:
    """Queue one limb call. Returns at once - the caller never waits for the work."""
    target = str(target or "").strip()
    if not target:
        return {"ok": False, "error": "empty_target", "hint": "task_add needs a limb name, e.g. self_test"}
    tid = "t" + uuid.uuid4().hex[:10]
    row = {"id": tid, "event": "add", "t": _now(), "when": _stamp(), "state": "queued", "target": target,
           "args": args or {}, "deadline": float(deadline or DEFAULT_DEADLINE), "note": note, "origin": origin,
           "created": _now(), "beats": 0}
    _append(row)
    start_worker()
    return {"ok": True, "id": tid, "state": "queued", "target": target,
            "note": note or f"{target} queued; read it with task_list", "deadline_s": row["deadline"]}


def cancel(tid: str) -> dict[str, Any]:
    with _lock:
        tasks = {t["id"]: t for t in fold()}
    t = tasks.get(str(tid))
    if not t:
        return {"ok": False, "error": "no_such_task", "id": tid}
    if t.get("state") in ("done", "failed", "cancelled"):
        return {"ok": False, "error": "already_finished", "state": t.get("state"), "id": tid}
    _append({"id": tid, "event": "cancel", "t": _now(), "when": _stamp(), "state": "cancelled",
             "note": "cancelled before it ran"})
    return {"ok": True, "id": tid, "state": "cancelled"}


def list_tasks(limit: int = 20, state: str = "") -> dict[str, Any]:
    try:
        limit = max(1, min(200, int(limit)))
    except (TypeError, ValueError):
        limit = 20
    tasks = fold()
    if state:
        tasks = [t for t in tasks if t.get("state") == state]
    counts: dict[str, int] = {}
    for t in fold():
        counts[str(t.get("state"))] = counts.get(str(t.get("state")), 0) + 1
    slim = [{k: t.get(k) for k in ("id", "state", "target", "note", "created", "age_s", "ended", "result")}
            for t in tasks[:limit]]
    return {"ok": True, "counts": counts, "tasks": slim, "n": len(slim),
            "worker": worker_state(), "stall_after_s": STALL_AFTER}


def show(tid: str) -> dict[str, Any]:
    for t in fold():
        if t.get("id") == str(tid):
            return {"ok": True, "task": t}
    return {"ok": False, "error": "no_such_task", "id": tid}


def _run_one(task: dict[str, Any]) -> dict[str, Any]:
    """Run a queued task by dispatching to the limb layer, heart-beating while it works."""
    tid = task["id"]
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(15):
            _append({"id": tid, "event": "beat", "t": _now(), "when": _stamp(), "state": "running"})

    _append({"id": tid, "event": "run", "t": _now(), "when": _stamp(), "state": "running",
             "started": _now()})
    hb = threading.Thread(target=beat, name=f"task-beat-{tid}", daemon=True)
    hb.start()
    t0 = _now()
    try:
        import tools  # the console's own limb layer: guards, consent and all
        result = tools.dispatch(str(task.get("target")), dict(task.get("args") or {}))
        ok = bool(result.get("ok")) if isinstance(result, dict) else False
        state = "done" if ok else "failed"
        row = {"id": tid, "event": "finish", "t": _now(), "when": _stamp(), "state": state,
               "ended": _now(), "seconds": round(_now() - t0, 1),
               "result": json.dumps(result, ensure_ascii=False)[:1500] if result is not None else "null"}
    except Exception as exc:  # a limb that raises must not kill the worker
        state = "failed"
        row = {"id": tid, "event": "finish", "t": _now(), "when": _stamp(), "state": "failed",
               "ended": _now(), "seconds": round(_now() - t0, 1),
               "result": json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)[:300]})}
    finally:
        stop.set()
    _append(row)
    return row


# --------------------------------------------------------------------------- reconciliation
#: A running job with no heartbeat for this long whose runner is gone is not "slow", it is abandoned.
INTERRUPT_AFTER = STALL_AFTER
#: A job is put back ONCE. A job that keeps dying is a finding for the operator, not a loop.
RECOVER_LIMIT = 1


def reconcile(reason: str = "no heartbeat; the process running it is gone") -> dict[str, Any]:
    """Write the truth about a job whose runner died, so a queue cannot look busy for ever.

    State was only ever DERIVED on read: fold() calls a heartbeat-less `running` row `stalled` and the
    record itself still said running, so nothing could retry it and nothing could tell it apart from a
    job that was genuinely working. MEASURED 2026-10-16: a cron queued its job inside a sweep process,
    the job started, the process exited mid-job - `task_list` reported `stalled` for ever afterwards and
    the scheduled run was simply a hole. A `cron:` job is repeat work the operator already asked for, so
    it goes back on the queue once; anything else is recorded `interrupted` and left alone.
    """
    found: list[dict[str, Any]] = []
    with _lock:
        for t in fold():
            if t.get("state") != "stalled":
                continue
            attempts = int(t.get("recovered") or 0)
            back = attempts < RECOVER_LIMIT and str(t.get("origin") or "").startswith("cron:")
            row = {"id": t["id"], "event": "interrupt", "t": _now(), "when": _stamp(),
                   "state": "queued" if back else "interrupted", "reason": reason,
                   "target": t.get("target"), "recovered": attempts + 1 if back else attempts,
                   "beat": _now(), "started": 0.0, "beats": 0}
            _append(row)
            found.append({"id": t["id"], "target": t.get("target"), "state": row["state"],
                          "age_s": t.get("age_s"), "requeued": back, "recovered": row["recovered"]})
    return {"ok": True, "reason": reason, "abandoned": len(found), "tasks": found}


def health() -> dict[str, Any]:
    """The queue's own condition: what is waiting, what stopped moving, and whether a worker is alive."""
    tasks = fold()
    counts: dict[str, int] = {}
    for t in tasks:
        counts[str(t.get("state"))] = counts.get(str(t.get("state")), 0) + 1
    stuck = [t for t in tasks if t.get("state") in ("stalled", "interrupted")]
    return {"counts": counts, "stalled": len(stuck), "queued": int(counts.get("queued") or 0),
            "oldest_stalled_s": round(max((float(t.get("age_s") or 0) for t in stuck), default=0.0), 1),
            "stalled_ids": [t.get("id") for t in stuck][:5], "worker": worker_state()}


def watch() -> dict[str, Any]:
    """The tasking watchdog: reconcile the record first, then report what the queue looks like."""
    got = reconcile(reason="watchdog: no heartbeat, runner gone")
    return {"ok": True, "abandoned": got.get("abandoned"), "reconciled": got.get("tasks"),
            "health": health(),
            "note": ("every job is accounted for" if not got.get("abandoned")
                     else f"{got.get('abandoned')} abandoned job(s) reconciled")}


class Worker(threading.Thread):
    """One daemon thread draining the queue. Started on demand, never blocks a turn."""

    def __init__(self, poll_s: float = 1.0):
        super().__init__(name="lygo-task-worker", daemon=True)
        self.poll_s = poll_s
        self.started_at = _now()
        self.last_action = ""
        self.done = 0

    def run(self) -> None:
        while True:
            try:
                queued = [t for t in fold() if t.get("state") == "queued"]
                queued.sort(key=lambda t: float(t.get("created") or 0))
                if queued:
                    task = queued[0]
                    self.last_action = f"{task['id']} {task.get('target')}"
                    _run_one(task)
                    self.done += 1
            except Exception:
                pass
            time.sleep(self.poll_s)


def start_worker(poll_s: float = 1.0) -> dict[str, Any]:
    global _worker
    if _worker is None or not _worker.is_alive():
        # A fresh worker is the first thing in the process that can tell an abandoned job from a working
        # one, so reconcile before draining rather than letting a dead job's row ride along for ever.
        reconcile(reason="worker start: no heartbeat, runner gone")
        _worker = Worker(poll_s=poll_s)
        _worker.start()
    return worker_state()


def worker_state() -> dict[str, Any]:
    if _worker is None:
        return {"started": False, "alive": False}
    return {"started": True, "alive": _worker.is_alive(), "since": _stamp(_worker.started_at),
            "done": _worker.done, "last": _worker.last_action}


def status() -> dict[str, Any]:
    tasks = fold()
    counts: dict[str, int] = {}
    for t in tasks:
        counts[str(t.get("state"))] = counts.get(str(t.get("state")), 0) + 1
    running = [t for t in tasks if t.get("state") == "running"]
    return {"ok": True, "health": health(), "counts": counts, "worker": worker_state(),
            "running": [{k: r.get(k) for k in ("id", "target", "age_s")} for r in running][:5],
            "stalled": counts.get("stalled", 0), "stall_after_s": STALL_AFTER, "log": str(LOG)}
