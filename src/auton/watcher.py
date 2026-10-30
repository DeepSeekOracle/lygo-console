"""The Watcher: the outbox reader (§5.6, rule 3).

The requirement this module answers is not "show notifications" - it is "keep the orchestrator's
context window out of the execution path". The console must never poll tasks to find out whether
work finished; it reads a small append-only event log on its own schedule. So the watcher:

* reads `events WHERE consumed_at IS NULL` in batches,
* hands each event to one or more sinks (an append-only JSONL log by default, plus an optional
  external command such as a webhook poster),
* marks exactly those rows consumed,
* and **never touches `tasks` or `goals`**. There is no store.transition call in this file and there
  must never be one.

`needs_review` deserves a note: §5.6 says "notify and pause the goal", but pausing a goal is a task
state mutation, which rule 3 forbids here. The worker pauses the goal when it emits the event (see
`worker.finish`), so the watcher only has to make sure a human sees it.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .config import RuntimeConfig
from .store import Event, Store, log_ctx

log = logging.getLogger("auton.watcher")

#: §5.6 notification rules. Every known kind notifies; unknown kinds notify too (fail open on
#: observability: better one extra line than a silent failure).
ALWAYS_NOTIFY: frozenset[str] = frozenset({"done", "failed", "blocked", "heartbeat_lost", "needs_review"})


@dataclass
class WatcherStats:
    batches: int = 0
    seen: int = 0
    notified: int = 0
    consumed: int = 0
    errors: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "batches": self.batches,
            "seen": self.seen,
            "notified": self.notified,
            "consumed": self.consumed,
            "errors": self.errors,
        }


def default_log_path(cfg: RuntimeConfig) -> Path:
    if cfg.notify_log:
        return Path(cfg.notify_log)
    return cfg.sqlite_path.parent / "events.jsonl"


def log_sink(path: str | Path) -> Callable[[Event], None]:
    """Append-only JSONL. Never rewritten, never truncated: the log is evidence."""
    p = Path(path)

    def sink(event: Event) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    return sink


def cmd_sink(cmd: str, timeout_s: float = 30.0) -> Callable[[Event], None]:
    """Run an external notifier with the event JSON on stdin. Non-zero exit is an error, not a drop."""

    def sink(event: Event) -> None:
        proc = subprocess.run(
            cmd,
            shell=True,
            input=json.dumps(event.to_dict(), ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=timeout_s,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace")[-400:]
            raise RuntimeError(f"notify command exited {proc.returncode}: {err or '(no stderr)'}")

    return sink


def watcher_loop(
    cfg: RuntimeConfig | None = None,
    store: Store | None = None,
    notify: Callable[[Event], Any] | Sequence[Callable[[Event], Any]] | None = None,
    batch_size: int = 100,
    once: bool = False,
    duration_s: float | None = None,
    should_stop: Callable[[], bool] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> WatcherStats:
    """Read, notify, mark consumed. Bounded by `once`, `duration_s` or `should_stop`."""
    cfg = cfg or RuntimeConfig()
    store = store or Store(cfg.sqlite_path)
    sinks: list[Callable[[Event], Any]] = []
    if notify is None:
        sinks.append(log_sink(default_log_path(cfg)))
        if cfg.notify_cmd:
            sinks.append(cmd_sink(cfg.notify_cmd))
    elif callable(notify):
        sinks.append(notify)
    else:
        sinks.extend(notify)

    stats = WatcherStats()
    started = time.monotonic()
    while True:
        if should_stop is not None and should_stop():
            break
        events = store.read_unconsumed_events(limit=batch_size)
        stats.batches += 1
        stats.seen += len(events)
        consumed: list[int] = []
        for event in events:
            delivered = True
            for sink in sinks:
                try:
                    sink(event)
                except Exception:  # noqa: BLE001 - one sink failing must not starve the others
                    delivered = False
                    stats.errors += 1
                    log.exception(log_ctx("watcher", task_id=event.task_id, goal_id=event.goal_id,
                                          kind=event.kind, reason="notify_failed"))
            if delivered:
                stats.notified += 1
                consumed.append(event.id)
            log.info(
                log_ctx("watcher", task_id=event.task_id, goal_id=event.goal_id, status=event.kind,
                        event_id=event.id, delivered=delivered)
            )
        if consumed:
            stats.consumed += store.mark_events_consumed(consumed)

        if once:
            break
        if duration_s is not None and (time.monotonic() - started) >= float(duration_s):
            break
        if not events:
            sleep_fn(float(cfg.watcher_interval_s))
    return stats
