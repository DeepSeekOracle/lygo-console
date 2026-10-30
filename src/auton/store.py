"""The task store: SQLite, WAL, one thin transactional API.

Everything the runtime promises about durability is a property of this module. The guide is blunt
about it (§3, §5.2): goals make objectives survive turns, tasks are what the dispatcher claims,
task_steps is the checkpoint log that lets a worker resume mid-task, events is the outbox that
decouples monitoring from execution, and workers/leases are what make a dead process detectable.

Design notes that matter:

* **One connection per thread.** SQLite connections are not shareable across threads and a spawned
  worker is a different process in any case. `Store` keeps a thread-local connection and every
  process that wants the store constructs its own `Store` from the same path.
* **Explicit transactions.** `isolation_level=None` (autocommit) plus `BEGIN IMMEDIATE` in the
  writer paths. That is what makes §5.2's single-statement claim actually atomic: the write lock is
  taken *before* the subquery picks a row, so two claimers can never read the same `queued` row and
  then both write. A plain deferred UPDATE would let the second writer read the old snapshot and
  fail with SQLITE_BUSY_SNAPSHOT instead of claiming the next row.
* **Illegal transitions raise.** §4 and rule 8. There is exactly one table of legal moves and one
  function that reads it; nothing else in the runtime is allowed to write `tasks.status`.
* **Compare-and-set everywhere.** `transition` takes the status the caller believes the row is in
  and returns False when reality moved on. A lost race is not an error; it is information.

The SQL schema below is copied verbatim from Engineering Guide §3, followed by two additions the
guide asks for in prose: `tasks_archive` (§10) and an index on unconsumed events (§10's "alert on
unconsumed count").
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

log = logging.getLogger("auton.store")

# ---------------------------------------------------------------------------------------------
# §3 Data model, verbatim.
# ---------------------------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
  id              TEXT PRIMARY KEY,
  text            TEXT NOT NULL,
  contract_json   TEXT NOT NULL,
  status          TEXT NOT NULL,
  budget_json     TEXT NOT NULL,
  used_turns      INTEGER NOT NULL DEFAULT 0,
  used_tokens     INTEGER NOT NULL DEFAULT 0,
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id              TEXT PRIMARY KEY,
  goal_id         TEXT NOT NULL REFERENCES goals(id),
  parent_task_id  TEXT,
  status          TEXT NOT NULL,
  payload_json    TEXT NOT NULL,
  result_json     TEXT,
  progress_note   TEXT,
  worker_id       TEXT,
  lease_expires_at INTEGER,
  heartbeat_at    INTEGER,
  attempt_count   INTEGER NOT NULL DEFAULT 0,
  max_attempts    INTEGER NOT NULL DEFAULT 3,
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_ready ON tasks(status, lease_expires_at);

CREATE TABLE IF NOT EXISTS task_steps (
  id              TEXT PRIMARY KEY,
  task_id         TEXT NOT NULL REFERENCES tasks(id),
  seq             INTEGER NOT NULL,
  kind            TEXT NOT NULL,
  input_json      TEXT,
  output_json     TEXT,
  idempotency_key TEXT UNIQUE,
  created_at      INTEGER NOT NULL,
  UNIQUE(task_id, seq)
);

CREATE TABLE IF NOT EXISTS events (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id         TEXT,
  goal_id         TEXT,
  kind            TEXT NOT NULL,
  payload_json    TEXT,
  consumed_at     INTEGER,
  created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS workers (
  id              TEXT PRIMARY KEY,
  pid             INTEGER,
  host            TEXT,
  started_at      INTEGER NOT NULL,
  last_heartbeat  INTEGER NOT NULL,
  current_task_id TEXT
);
"""

#: §10 "store grows unbounded": terminal tasks are copied here, never deleted outright.
ARCHIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks_archive (
  id              TEXT PRIMARY KEY,
  goal_id         TEXT NOT NULL,
  parent_task_id  TEXT,
  status          TEXT NOT NULL,
  payload_json    TEXT NOT NULL,
  result_json     TEXT,
  progress_note   TEXT,
  worker_id       TEXT,
  lease_expires_at INTEGER,
  heartbeat_at    INTEGER,
  attempt_count   INTEGER NOT NULL DEFAULT 0,
  max_attempts    INTEGER NOT NULL DEFAULT 3,
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL,
  archived_at     INTEGER NOT NULL
)
"""

#: The watcher's read is `WHERE consumed_at IS NULL ORDER BY id`, so index exactly that.
EVENT_INDEX = "CREATE INDEX IF NOT EXISTS idx_events_unread ON events(consumed_at, id)"

# ---------------------------------------------------------------------------------------------
# §4 State machine.
# ---------------------------------------------------------------------------------------------
TASK_STATES: tuple[str, ...] = (
    "queued",
    "claimed",
    "running",
    "blocked",
    "done",
    "failed",
    "retrying",
    "cancelled",
)
TERMINAL_STATES: frozenset[str] = frozenset({"done", "blocked", "failed", "cancelled"})
NON_TERMINAL_STATES: frozenset[str] = frozenset(set(TASK_STATES) - TERMINAL_STATES)

#: The "Legal transitions" table from §4, transcribed. `running -> running` is a judge continue
#: with a checkpoint written, and is intentionally the only self-loop.
TASK_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("queued", "claimed"),
        ("claimed", "running"),
        ("running", "running"),
        ("running", "done"),
        ("running", "blocked"),
        ("running", "failed"),
        ("running", "retrying"),
        ("claimed", "retrying"),
        ("retrying", "queued"),
        ("retrying", "failed"),
    }
)

#: §5.6 event kinds. Not enforced as a CHECK constraint: an operator adding a kind should not need
#: a migration, and the watcher treats unknown kinds as "notify".
EVENT_KINDS: tuple[str, ...] = ("blocked", "done", "failed", "needs_review", "heartbeat_lost")


class IllegalTransition(ValueError):
    """Raised for any move not in §4's table. Rule 8: raise, never coerce."""

    def __init__(self, task_id: str, from_status: str, to_status: str) -> None:
        self.task_id = task_id
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"illegal task transition for {task_id}: {from_status!r} -> {to_status!r} "
            f"(legal from {from_status!r}: {sorted(t for f, t in TASK_TRANSITIONS if f == from_status)})"
        )


def legal_transition(from_status: str, to_status: str) -> bool:
    """True when §4 allows the move. `any non-terminal -> cancelled` is the only wildcard."""
    if to_status == "cancelled":
        return from_status in NON_TERMINAL_STATES
    if from_status == to_status == "running":
        return True
    return (from_status, to_status) in TASK_TRANSITIONS


def now_ms() -> int:
    """All timestamps in this module are epoch milliseconds (§3 allows UTC ISO-8601 or epoch ms)."""
    return int(time.time() * 1000)


def new_id() -> str:
    return str(uuid.uuid4())


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def log_ctx(module: str, **fields: Any) -> str:
    """Rule 9: one structured line shape for the whole runtime.

    Kept as a plain formatter (not a logging config) so a caller's logging setup decides where it
    goes. Every runtime log call goes through this so `goal_id`/`task_id`/`worker_id`/`seq` are
    greppable in the console's existing log files.
    """
    parts = [f"auton.{module}"]
    for key in ("goal_id", "task_id", "worker_id", "seq", "status", "reason"):
        if fields.get(key) is not None:
            parts.append(f"{key}={fields[key]}")
    for key, value in fields.items():
        if key in {"goal_id", "task_id", "worker_id", "seq", "status", "reason"}:
            continue
        if value is not None:
            parts.append(f"{key}={value}")
    return " ".join(parts)


# ---------------------------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------------------------
@dataclass
class Goal:
    id: str
    text: str
    contract: dict[str, Any]
    status: str
    budget: dict[str, Any]
    used_turns: int = 0
    used_tokens: int = 0
    created_at: int = 0
    updated_at: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Goal":
        return cls(
            id=row["id"],
            text=row["text"],
            contract=_loads(row["contract_json"], {}) or {},
            status=row["status"],
            budget=_loads(row["budget_json"], {}) or {},
            used_turns=int(row["used_turns"] or 0),
            used_tokens=int(row["used_tokens"] or 0),
            created_at=int(row["created_at"] or 0),
            updated_at=int(row["updated_at"] or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Task:
    id: str
    goal_id: str
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    progress_note: str | None = None
    worker_id: str | None = None
    lease_expires_at: int | None = None
    heartbeat_at: int | None = None
    attempt_count: int = 0
    max_attempts: int = 3
    parent_task_id: str | None = None
    created_at: int = 0
    updated_at: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Task":
        return cls(
            id=row["id"],
            goal_id=row["goal_id"],
            status=row["status"],
            payload=_loads(row["payload_json"], {}) or {},
            result=_loads(row["result_json"]),
            progress_note=row["progress_note"],
            worker_id=row["worker_id"],
            lease_expires_at=row["lease_expires_at"],
            heartbeat_at=row["heartbeat_at"],
            attempt_count=int(row["attempt_count"] or 0),
            max_attempts=int(row["max_attempts"] or 3),
            parent_task_id=row["parent_task_id"],
            created_at=int(row["created_at"] or 0),
            updated_at=int(row["updated_at"] or 0),
        )

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATES

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Step:
    id: str
    task_id: str
    seq: int
    kind: str
    input: Any = None
    output: Any = None
    idempotency_key: str | None = None
    created_at: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Step":
        return cls(
            id=row["id"],
            task_id=row["task_id"],
            seq=int(row["seq"]),
            kind=row["kind"],
            input=_loads(row["input_json"]),
            output=_loads(row["output_json"]),
            idempotency_key=row["idempotency_key"],
            created_at=int(row["created_at"] or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Event:
    id: int
    kind: str
    task_id: str | None = None
    goal_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    consumed_at: int | None = None
    created_at: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Event":
        return cls(
            id=int(row["id"]),
            kind=row["kind"],
            task_id=row["task_id"],
            goal_id=row["goal_id"],
            payload=_loads(row["payload_json"], {}) or {},
            consumed_at=row["consumed_at"],
            created_at=int(row["created_at"] or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------------------------
class Store:
    """Thin transactional wrapper over the §3 schema.

    Every public writer bumps `updated_at` and commits before returning; a caller that gets a value
    back can assume it is durable (subject to the OS, as always with SQLite).
    """

    def __init__(self, path: str | Path, busy_timeout_ms: int = 5000, init: bool = True) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = int(busy_timeout_ms)
        self._local = threading.local()
        if init:
            self.init_schema()

    # -- connection -----------------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=self.busy_timeout_ms / 1000.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
            self._local.depth = 0
        return conn

    @contextmanager
    def _tx(self, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """Join the outer transaction or open one. Writers use IMMEDIATE to take the write lock
        before reading the row they are about to modify."""
        conn = self.conn
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            try:
                yield conn
            finally:
                self._local.depth = depth
            return
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        self._local.depth = 1
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            self._local.depth = 0

    def init_schema(self) -> None:
        # `executescript` implicitly COMMITs any open transaction, so the DDL is applied statement
        # by statement inside one explicit write transaction here.
        with self._tx(immediate=True) as c:
            for statement in _statements(SCHEMA) + [ARCHIVE_SCHEMA, EVENT_INDEX]:
                c.execute(statement)

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    # -- goals ----------------------------------------------------------------
    def create_goal(self, goal: dict[str, Any]) -> str:
        """Insert a goal. `contract` may be a dict or already-serialised JSON."""
        gid = str(goal.get("id") or new_id())
        ts = now_ms()
        contract = goal.get("contract", goal.get("contract_json", {}))
        budget = goal.get("budget", goal.get("budget_json", {})) or {}
        with self._tx() as c:
            c.execute(
                "INSERT INTO goals (id,text,contract_json,status,budget_json,used_turns,used_tokens,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    gid,
                    str(goal.get("text") or ""),
                    _dumps(contract) or "{}",
                    str(goal.get("status") or "active"),
                    _dumps(budget) or "{}",
                    int(goal.get("used_turns") or 0),
                    int(goal.get("used_tokens") or 0),
                    ts,
                    ts,
                ),
            )
        return gid

    def get_goal(self, goal_id: str) -> Goal | None:
        row = self.conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
        return Goal.from_row(row) if row else None

    def list_goals(self, status: str | None = None, limit: int = 200) -> list[Goal]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM goals WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM goals ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [Goal.from_row(r) for r in rows]

    def set_goal_status(self, goal_id: str, status: str) -> bool:
        with self._tx() as c:
            cur = c.execute(
                "UPDATE goals SET status=?, updated_at=? WHERE id=? AND status<>?",
                (status, now_ms(), goal_id, status),
            )
            return cur.rowcount > 0

    def increment_goal_turns(self, goal_id: str, n: int = 1) -> int:
        with self._tx() as c:
            c.execute(
                "UPDATE goals SET used_turns=used_turns+?, updated_at=? WHERE id=?",
                (int(n), now_ms(), goal_id),
            )
            row = c.execute("SELECT used_turns FROM goals WHERE id=?", (goal_id,)).fetchone()
        return int(row["used_turns"]) if row else 0

    def increment_goal_tokens(self, goal_id: str, tokens: int) -> int:
        with self._tx() as c:
            c.execute(
                "UPDATE goals SET used_tokens=used_tokens+?, updated_at=? WHERE id=?",
                (max(0, int(tokens or 0)), now_ms(), goal_id),
            )
            row = c.execute("SELECT used_tokens FROM goals WHERE id=?", (goal_id,)).fetchone()
        return int(row["used_tokens"]) if row else 0

    # -- tasks ----------------------------------------------------------------
    def create_task(
        self,
        goal_id: str,
        payload: dict[str, Any] | None = None,
        parent_task_id: str | None = None,
        max_attempts: int = 3,
        status: str = "queued",
    ) -> str:
        tid = new_id()
        ts = now_ms()
        with self._tx() as c:
            c.execute(
                "INSERT INTO tasks (id,goal_id,parent_task_id,status,payload_json,attempt_count,"
                "max_attempts,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    tid,
                    goal_id,
                    parent_task_id,
                    status,
                    _dumps(payload or {}) or "{}",
                    0,
                    int(max_attempts),
                    ts,
                    ts,
                ),
            )
        return tid

    def create_goal_and_task(
        self,
        text: str,
        contract: dict[str, Any],
        budget: dict[str, Any],
        payload: dict[str, Any] | None = None,
        max_attempts: int = 3,
        gid: str | None = None,
    ) -> tuple[str, str]:
        """§5.1 step 3: "Persist goal and first task in one transaction"."""
        gid = str(gid or new_id())
        tid = new_id()
        ts = now_ms()
        with self._tx() as c:
            c.execute(
                "INSERT INTO goals (id,text,contract_json,status,budget_json,used_turns,used_tokens,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    gid,
                    text,
                    _dumps(contract) or "{}",
                    "active",
                    _dumps(budget) or "{}",
                    0,
                    0,
                    ts,
                    ts,
                ),
            )
            c.execute(
                "INSERT INTO tasks (id,goal_id,parent_task_id,status,payload_json,attempt_count,"
                "max_attempts,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (tid, gid, None, "queued", _dumps(payload or {}) or "{}", 0, int(max_attempts), ts, ts),
            )
        return gid, tid

    def get_task(self, task_id: str) -> Task | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return Task.from_row(row) if row else None

    def list_tasks(self, goal_id: str | None = None, status: str | None = None, limit: int = 500) -> list[Task]:
        sql = "SELECT * FROM tasks"
        clauses: list[str] = []
        args: list[Any] = []
        if goal_id:
            clauses.append("goal_id=?")
            args.append(goal_id)
        if status:
            clauses.append("status=?")
            args.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at ASC LIMIT ?"
        args.append(int(limit))
        return [Task.from_row(r) for r in self.conn.execute(sql, tuple(args)).fetchall()]

    def count_tasks(self, status: str | None = None) -> int:
        if status:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE status=?", (status,)).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()
        return int(row["n"])

    def claim_task(self, worker_id: str, lease_ms: int) -> Task | None:
        """§5.2 atomic claim: one UPDATE, in a write transaction, with RETURNING.

        The inner SELECT runs under the write lock because BEGIN IMMEDIATE is held. That is the
        whole trick: no read-then-write window exists for a second claimer to slip into.
        """
        ts = now_ms()
        lease_until = ts + int(lease_ms)
        sql = (
            "UPDATE tasks\n"
            "SET status='claimed', worker_id=?, lease_expires_at=?, heartbeat_at=?, updated_at=?\n"
            "WHERE id = (\n"
            "  SELECT id FROM tasks\n"
            "  WHERE status='queued'\n"
            "  ORDER BY created_at\n"
            "  LIMIT 1\n"
            ")\n"
            "RETURNING *;"
        )
        with self._tx(immediate=True) as c:
            row = c.execute(sql, (worker_id, lease_until, ts, ts)).fetchone()
        if not row:
            return None
        task = Task.from_row(row)
        log.debug(log_ctx("store", task_id=task.id, worker_id=worker_id, status="claimed"))
        return task

    def heartbeat(self, task_id: str, worker_id: str, lease_ms: int) -> bool:
        """Renew the lease. False means the lease is gone (worker must stop; §5.4 step 3)."""
        ts = now_ms()
        with self._tx() as c:
            cur = c.execute(
                "UPDATE tasks SET heartbeat_at=?, lease_expires_at=?, updated_at=? "
                "WHERE id=? AND worker_id=? AND status IN ('claimed','running')",
                (ts, ts + int(lease_ms), ts, task_id, worker_id),
            )
            if cur.rowcount <= 0:
                return False
            c.execute("UPDATE workers SET last_heartbeat=? WHERE id=?", (ts, worker_id))
        return True

    def transition(
        self,
        task_id: str,
        from_status: str,
        to_status: str,
        result: dict[str, Any] | None = None,
    ) -> bool:
        """Compare-and-set transition (§5.2). Raises `IllegalTransition` for a move §4 forbids.

        Returns False when the row was not in `from_status` (the CAS lost). Callers treat that as
        "someone else already moved it" and stop, never as permission to force the write.
        """
        if not legal_transition(from_status, to_status):
            raise IllegalTransition(task_id, from_status, to_status)
        ts = now_ms()
        sets = ["status=?", "updated_at=?"]
        args: list[Any] = [to_status, ts]
        if result is not None:
            sets.append("result_json=?")
            args.append(_dumps(result))
        if to_status in TERMINAL_STATES or to_status == "queued":
            sets.append("worker_id=NULL")
            sets.append("lease_expires_at=NULL")
        args.extend([task_id, from_status])
        with self._tx() as c:
            cur = c.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=? AND status=?", tuple(args))
            ok = cur.rowcount > 0
        if ok:
            log.debug(log_ctx("store", task_id=task_id, status=f"{from_status}->{to_status}"))
        else:
            log.warning(
                log_ctx("store", task_id=task_id, status="cas_lost", reason=f"expected {from_status}, wanted {to_status}")
            )
        return ok

    def increment_attempt(self, task_id: str) -> int:
        with self._tx() as c:
            c.execute("UPDATE tasks SET attempt_count=attempt_count+1, updated_at=? WHERE id=?", (now_ms(), task_id))
            row = c.execute("SELECT attempt_count FROM tasks WHERE id=?", (task_id,)).fetchone()
        return int(row["attempt_count"]) if row else 0

    def set_progress_note(self, task_id: str, note: str | None) -> None:
        with self._tx() as c:
            c.execute("UPDATE tasks SET progress_note=?, updated_at=? WHERE id=?", (note, now_ms(), task_id))

    def find_expired_leases(self, now: int | None = None) -> list[Task]:
        now = now_ms() if now is None else int(now)
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE status IN ('claimed','running') "
            "AND lease_expires_at IS NOT NULL AND lease_expires_at < ? ORDER BY lease_expires_at",
            (now,),
        ).fetchall()
        return [Task.from_row(r) for r in rows]

    def requeue_stale(self, now: int | None = None) -> list[str]:
        """§5.3 step 1 in one call: reap every expired lease.

        The guide's sketch moves `claimed|running -> queued` directly, but §4's table only allows
        `claimed|running -> retrying` and `retrying -> queued`. Rule 8 wins over the sketch, so the
        reap takes the two legal hops. Same outcome, no illegal edge.
        """
        now = now_ms() if now is None else int(now)
        reaped: list[str] = []
        for task in self.find_expired_leases(now):
            if self._reap(task, "lease_expired", now):
                reaped.append(task.id)
        return reaped

    def _reap(self, task: Task, reason: str, now: int | None = None) -> bool:
        """Retrying -> queued while attempts remain, else failed. Emits the §5.3 event."""
        now = now_ms() if now is None else int(now)
        attempts = int(task.attempt_count or 0)
        if attempts + 1 >= int(task.max_attempts or 3):
            if not self.transition(task.id, task.status, "retrying"):
                return False
            moved = self.transition(task.id, "retrying", "failed", result={"reason": reason})
            if moved:
                self.append_event(task.id, task.goal_id, "failed", {"reason": reason})
            return moved
        self.increment_attempt(task.id)
        if not self.transition(task.id, task.status, "retrying"):
            return False
        moved = self.transition(task.id, "retrying", "queued")
        if moved:
            self.append_event(task.id, task.goal_id, "heartbeat_lost", {"reason": reason, "attempt": attempts + 1})
        return moved

    def find_stale_progress(self, older_than: int) -> list[Task]:
        """§10 "worker hangs on a tool call": running, heartbeat alive, no checkpoint for N ms."""
        rows = self.conn.execute(
            "SELECT t.* FROM tasks t "
            "LEFT JOIN (SELECT task_id, MAX(created_at) AS last_step FROM task_steps GROUP BY task_id) s "
            "  ON s.task_id = t.id "
            "WHERE t.status IN ('claimed','running') "
            "  AND COALESCE(s.last_step, t.created_at) < ? "
            "ORDER BY t.created_at",
            (int(older_than),),
        ).fetchall()
        return [Task.from_row(r) for r in rows]

    def reap_stale_progress(self, older_than: int) -> list[str]:
        """Requeue tasks that are still leased but have written no checkpoint for `older_than` ms.

        The lease alone cannot catch this: a worker hung inside a tool call keeps heartbeating, so
        the lease stays fresh while no progress is made. §10 gives the fix a name - a stale-progress
        check in the dispatcher - and this is it.
        """
        reaped: list[str] = []
        for task in self.find_stale_progress(older_than):
            if self._reap(task, "stale_progress"):
                reaped.append(task.id)
        return reaped

    # -- steps / checkpoints --------------------------------------------------
    def last_seq(self, task_id: str) -> int:
        row = self.conn.execute("SELECT COALESCE(MAX(seq),0) AS m FROM task_steps WHERE task_id=?", (task_id,)).fetchone()
        return int(row["m"]) if row else 0

    def checkpoint(
        self,
        task_id: str,
        kind: str,
        input: Any = None,
        output: Any = None,
        idem_key: str | None = None,
        seq: int | None = None,
    ) -> int:
        """Append a checkpoint row (§5.2 signature + an optional explicit `seq`).

        The guide's worker sketch passes the running `seq` as the last positional argument while the
        store signature calls that argument `idem_key`; both are supported here (`seq=` keyword,
        `idem_key=` keyword). When `idem_key` already exists the stored seq is returned and nothing
        is written - that is the replay path of §5.7.
        """
        with self._tx() as c:
            if idem_key:
                row = c.execute("SELECT seq FROM task_steps WHERE idempotency_key=?", (idem_key,)).fetchone()
                if row:
                    return int(row["seq"])
            if seq is None:
                row = c.execute("SELECT COALESCE(MAX(seq),0) AS m FROM task_steps WHERE task_id=?", (task_id,)).fetchone()
                seq = int(row["m"]) + 1
            else:
                seq = int(seq)
                taken = c.execute("SELECT 1 FROM task_steps WHERE task_id=? AND seq=?", (task_id, seq)).fetchone()
                if taken:
                    row = c.execute(
                        "SELECT COALESCE(MAX(seq),0) AS m FROM task_steps WHERE task_id=?", (task_id,)
                    ).fetchone()
                    new_seq = int(row["m"]) + 1
                    log.warning(
                        log_ctx("store", task_id=task_id, seq=seq, status="seq_taken", reason=f"bumped to {new_seq}")
                    )
                    seq = new_seq
            c.execute(
                "INSERT INTO task_steps (id,task_id,seq,kind,input_json,output_json,idempotency_key,"
                "created_at) VALUES (?,?,?,?,?,?,?,?)",
                (new_id(), task_id, seq, str(kind), _dumps(input), _dumps(output), idem_key, now_ms()),
            )
            c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now_ms(), task_id))
        return seq

    def find_step_by_idem(self, idem_key: str) -> Step | None:
        row = self.conn.execute("SELECT * FROM task_steps WHERE idempotency_key=?", (idem_key,)).fetchone()
        return Step.from_row(row) if row else None

    def get_step(self, task_id: str, seq: int) -> Step | None:
        row = self.conn.execute("SELECT * FROM task_steps WHERE task_id=? AND seq=?", (task_id, int(seq))).fetchone()
        return Step.from_row(row) if row else None

    def update_step_output(self, task_id: str, seq: int, output: Any) -> bool:
        with self._tx() as c:
            cur = c.execute(
                "UPDATE task_steps SET output_json=? WHERE task_id=? AND seq=?",
                (_dumps(output), task_id, int(seq)),
            )
            return cur.rowcount > 0

    def recent_steps(self, task_id: str, n: int = 20) -> list[Step]:
        rows = self.conn.execute(
            "SELECT * FROM task_steps WHERE task_id=? ORDER BY seq DESC LIMIT ?", (task_id, int(n))
        ).fetchall()
        return [Step.from_row(r) for r in reversed(rows)]

    def list_steps(self, task_id: str) -> list[Step]:
        rows = self.conn.execute("SELECT * FROM task_steps WHERE task_id=? ORDER BY seq", (task_id,)).fetchall()
        return [Step.from_row(r) for r in rows]

    def count_steps(self, task_id: str, kind: str | None = None) -> int:
        if kind:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM task_steps WHERE task_id=? AND kind=?", (task_id, kind)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM task_steps WHERE task_id=?", (task_id,)).fetchone()
        return int(row["n"])

    # -- events ---------------------------------------------------------------
    def append_event(
        self,
        task_id: str | None,
        goal_id: str | None,
        kind: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO events (task_id,goal_id,kind,payload_json,consumed_at,created_at) "
                "VALUES (?,?,?,?,NULL,?)",
                (task_id, goal_id, str(kind), _dumps(payload or {}) or "{}", now_ms()),
            )
            return int(cur.lastrowid)

    def read_unconsumed_events(self, limit: int = 100) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE consumed_at IS NULL ORDER BY id LIMIT ?", (int(limit),)
        ).fetchall()
        return [Event.from_row(r) for r in rows]

    def mark_events_consumed(self, ids: Sequence[int]) -> int:
        ids = [int(i) for i in ids]
        if not ids:
            return 0
        ts = now_ms()
        with self._tx() as c:
            marks = ",".join("?" for _ in ids)
            cur = c.execute(
                f"UPDATE events SET consumed_at=? WHERE id IN ({marks}) AND consumed_at IS NULL",
                tuple([ts] + ids),
            )
            return cur.rowcount

    def count_unconsumed_events(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM events WHERE consumed_at IS NULL").fetchone()
        return int(row["n"])

    def list_events(self, task_id: str | None = None, limit: int = 200) -> list[Event]:
        if task_id:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, int(limit))
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [Event.from_row(r) for r in rows]

    def events_of_kind(self, task_id: str, kind: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE task_id=? AND kind=?", (task_id, str(kind))
        ).fetchone()
        return int(row["n"])

    # -- workers --------------------------------------------------------------
    def register_worker(self, worker_id: str, pid: int | None = None, host: str | None = None) -> None:
        import socket

        ts = now_ms()
        host = host or socket.gethostname()
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO workers (id,pid,host,started_at,last_heartbeat,current_task_id) "
                "VALUES (?,?,?,?,?,NULL)",
                (worker_id, pid, host, ts, ts),
            )

    def bind_worker(self, worker_id: str, task_id: str) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE workers SET current_task_id=?, last_heartbeat=? WHERE id=?",
                (task_id, now_ms(), worker_id),
            )

    def worker_alive(self, worker_id: str, within_ms: int) -> bool:
        row = self.conn.execute("SELECT last_heartbeat FROM workers WHERE id=?", (worker_id,)).fetchone()
        return bool(row) and (now_ms() - int(row["last_heartbeat"] or 0)) <= int(within_ms)

    # -- archival -------------------------------------------------------------
    def archive_terminal(self, now: int | None = None, older_than_days: int = 30) -> int:
        """§10: nightly archival of terminal tasks. Copied to tasks_archive, then removed here.

        "Archived, not deleted" (rule 10) means the row is never lost: tasks_archive keeps every
        column plus archived_at, and task_steps/events keep their foreign-key strings.
        """
        now = now_ms() if now is None else int(now)
        cutoff = now - int(older_than_days) * 86_400_000
        moved = 0
        with self._tx() as c:
            rows = c.execute(
                "SELECT * FROM tasks WHERE status IN ('done','blocked','failed','cancelled') "
                "AND updated_at < ? ORDER BY updated_at LIMIT 500",
                (cutoff,),
            ).fetchall()
            for row in rows:
                c.execute(
                    "INSERT OR REPLACE INTO tasks_archive (id,goal_id,parent_task_id,status,payload_json,"
                    "result_json,progress_note,worker_id,lease_expires_at,heartbeat_at,attempt_count,"
                    "max_attempts,created_at,updated_at,archived_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row["id"],
                        row["goal_id"],
                        row["parent_task_id"],
                        row["status"],
                        row["payload_json"],
                        row["result_json"],
                        row["progress_note"],
                        row["worker_id"],
                        row["lease_expires_at"],
                        row["heartbeat_at"],
                        row["attempt_count"],
                        row["max_attempts"],
                        row["created_at"],
                        row["updated_at"],
                        now,
                    ),
                )
                c.execute("DELETE FROM tasks WHERE id=?", (row["id"],))
                moved += 1
        return moved

    def count_archived(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM tasks_archive").fetchone()
        return int(row["n"])


def _statements(script: str) -> list[str]:
    """Split the DDL on semicolons. `executescript` would implicitly COMMIT our transaction."""
    out = [s.strip() for s in script.split(";") if s.strip()]
    return out
