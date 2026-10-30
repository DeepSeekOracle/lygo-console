"""Cron jobs for the console: a small schedule the keeper drives.

Ported from the Hermes cron engine (`cron/jobs.py`, `cron/scheduler.py`): jobs are declared data,
not running timers, and the scheduler is idempotent - `due()` answers from the record of the last
run, so a keeper restart cannot double-fire a job.

A job runs by being **enqueued into the task queue** rather than run inline. That keeps the
scheduler itself instant (a slow job must never delay the next sweep) and it puts every scheduled
run in the same task list the operator already reads.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
JOBS = DATA / "crons.json"


#: Crons every console carries, seeded ONCE by ensure_defaults(). The monitoring job is the point:
#: the queue only ever told the truth when somebody asked it to, so the console now asks itself - and
#: because the keeper is what runs crons, this is also the thing that makes tasking self-monitoring
#: rather than something an operator has to remember to look at.
DEFAULT_JOBS: tuple[dict[str, Any], ...] = (
    {"name": "tasking-watch", "limb": "task_recover", "every": 600,
     "note": "watch the background queue: reconcile a job whose runner died, report the queue's condition"},
    {"name": "swarm-pulse", "limb": "swarm_pulse", "every": 900,
     "note": "record whether the QwenPaw helper on 11441 and the API advisor on 18900 are alive"},
)


def ensure_defaults() -> dict[str, Any]:
    """Seed the crons every console should carry, without touching any job the operator made."""
    have = {str(j.get("name") or "") for j in _load()}
    seeded: list[dict[str, Any]] = []
    kept: list[str] = []
    for spec in DEFAULT_JOBS:
        if spec["name"] in have:
            kept.append(str(spec["name"]))
            continue
        got = add_job(str(spec["name"]), str(spec["limb"]), {}, every=int(spec["every"]),
                      note=str(spec.get("note") or ""))
        seeded.append({"name": spec["name"], "limb": spec["limb"], "every": spec["every"],
                       "ok": bool(got.get("ok")), "error": got.get("error", "")})
    return {"ok": True, "seeded": seeded, "already_there": kept}


def _load() -> list[dict[str, Any]]:
    if not JOBS.is_file():
        return []
    try:
        rows = json.loads(JOBS.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [r for r in rows if isinstance(r, dict)]


def _save(jobs: list[dict[str, Any]]) -> None:
    JOBS.parent.mkdir(parents=True, exist_ok=True)
    tmp = JOBS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, JOBS)


def add_job(name: str, limb: str, args: dict[str, Any] | None = None, every: int = 0,
            at: str = "", enabled: bool = True, note: str = "") -> dict[str, Any]:
    """Declare a job. `every` is seconds; `at` is HH:MM daily. One of them must be set."""
    name = str(name or "").strip()
    limb = str(limb or "").strip()
    if not name or not limb:
        return {"ok": False, "error": "needs_name_and_limb", "hint": "cron_add name=nightly limb=self_test"}
    try:
        every = int(every or 0)
    except (TypeError, ValueError):
        every = 0
    at = (at or "").strip()
    if not every and not at:
        return {"ok": False, "error": "needs_schedule",
                "hint": "pass every=<seconds> or at=HH:MM (daily)"}
    if at:
        ok = (len(at) == 5 and at[2] == ":" and at[:2].isdigit() and at[3:].isdigit()
              and 0 <= int(at[:2]) <= 23 and 0 <= int(at[3:]) <= 59)
        if not ok:
            return {"ok": False, "error": "bad_time",
                    "hint": "at must be HH:MM in 24-hour time (00-23:00-59)"}
    jobs = _load()
    if any(j.get("name") == name for j in jobs):
        return {"ok": False, "error": "name_taken", "hint": f"a job called {name} exists; remove it first"}
    job = {"name": name, "limb": limb, "args": args or {}, "every": every, "at": at,
           "enabled": bool(enabled), "note": note, "last_run": 0.0, "last_state": "", "runs": 0}
    jobs.append(job)
    _save(jobs)
    return {"ok": True, "job": job, "n": len(jobs)}


def list_jobs() -> dict[str, Any]:
    jobs = _load()
    now = time.time()
    for j in jobs:
        j["due_in_s"] = _due_in(j, now)
        j["last_run_human"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(j["last_run"])) if j.get("last_run") else ""
    return {"ok": True, "jobs": jobs, "n": len(jobs), "file": str(JOBS)}


def remove_job(name: str) -> dict[str, Any]:
    jobs = _load()
    keep = [j for j in jobs if j.get("name") != name]
    if len(keep) == len(jobs):
        return {"ok": False, "error": "no_such_job", "name": name}
    _save(keep)
    return {"ok": True, "removed": name, "n": len(keep)}


def set_enabled(name: str, enabled: bool) -> dict[str, Any]:
    jobs = _load()
    for j in jobs:
        if j.get("name") == name:
            j["enabled"] = bool(enabled)
            _save(jobs)
            return {"ok": True, "name": name, "enabled": bool(enabled)}
    return {"ok": False, "error": "no_such_job", "name": name}


def _due_in(job: dict[str, Any], now: float) -> float:
    """Seconds until this job is next due; <= 0 means due now."""
    if not job.get("enabled"):
        return float("inf")
    last = float(job.get("last_run") or 0)
    every = int(job.get("every") or 0)
    if every:
        return max(0.0, every - (now - last))
    at = str(job.get("at") or "")
    if not at:
        return float("inf")
    hh, mm = int(at[:2]), int(at[3:])
    lt = time.localtime(now)
    target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, lt.tm_wday, lt.tm_yday, -1))
    if target <= now:
        target += 86400
    # a daily job that has already run today is not due again today
    if last and time.strftime("%Y-%m-%d", time.localtime(last)) == time.strftime("%Y-%m-%d", lt):
        return max(0.0, target - now)
    return max(0.0, target - now)


def due(now: float | None = None) -> list[dict[str, Any]]:
    now = time.time() if now is None else now
    return [j for j in _load() if _due_in(j, now) <= 0]


def run_due(now: float | None = None, enqueue: Callable[..., dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Enqueue every due job. Never runs a job inline - the queue does that."""
    if enqueue is None:
        import tasking

        enqueue = tasking.add
    started = []
    jobs = _load()
    changed = False
    for j in jobs:
        if not j.get("enabled") or _due_in(j, time.time() if now is None else now) > 0:
            continue
        got = enqueue(str(j["limb"]), dict(j.get("args") or {}), origin=f"cron:{j['name']}")
        j["last_run"] = time.time() if now is None else now
        j["runs"] = int(j.get("runs") or 0) + 1
        j["last_state"] = "queued" if got.get("ok") else "refused"
        j["last_task"] = got.get("id", "")
        started.append({"name": j["name"], "limb": j["limb"], "task": got.get("id", ""), "ok": got.get("ok", False)})
        changed = True
    if changed:
        _save(jobs)
    return started
