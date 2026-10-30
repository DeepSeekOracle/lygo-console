"""Runtime configuration for the autonomous task runtime.

The console's own settings live in `config/console.json` and are owned by `src/paths.py`; this
module deliberately does not touch them. The auton runtime is additive, so it carries its own
dataclass of defaults plus an optional JSON file that is the exact YAML-equivalent block from
Engineering Guide §9. No dependency on PyYAML: the file is JSON, which is what the rest of the kit
already uses for config.

Why a dataclass and not a dict: the dispatcher, the worker and the CLI all read the same knobs, and
a mistyped key (`lease_ms` vs `lease`) must fail at construction, not silently fall back to a
default three processes later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path
from typing import Any


@dataclass
class JudgeConfig:
    """§9 `runtime.judge`. `model` names a small, fast model used only for rubric checks."""

    model: str = "small-fast-model"
    temperature: float = 0
    max_retries: int = 2


@dataclass
class Budgets:
    """§9 `runtime.budgets` and §5.1's recorded defaults."""

    default_max_turns: int = 30
    default_max_attempts: int = 3
    default_max_wall_s: int = 3600
    default_max_tokens: int = 200000

    def as_task_budget(self) -> dict[str, int]:
        return {
            "max_turns": self.default_max_turns,
            "max_attempts": self.default_max_attempts,
            "max_wall_s": self.default_max_wall_s,
            "max_tokens": self.default_max_tokens,
        }


@dataclass
class RuntimeConfig:
    """One object that travels to every process: dispatcher, worker, watcher, CLI.

    The first block is verbatim §9. The second block is runtime plumbing the guide names in prose
    (stale-progress check, no-op breaker, verifier location, notification sink) but does not put in
    the YAML; it is separated so the mapping stays obvious.
    """

    # --- §9, verbatim -------------------------------------------------------
    store_dsn: str = "sqlite:///var/lib/console/runtime.db"
    poll_interval_s: float = 2.0
    watcher_interval_s: float = 5.0
    lease_ms: int = 120_000
    heartbeat_interval_ms: int = 40_000
    max_concurrent_workers: int = 2
    no_progress_window: int = 3
    judge: JudgeConfig = field(default_factory=JudgeConfig)
    budgets: Budgets = field(default_factory=Budgets)

    # --- runtime plumbing ---------------------------------------------------
    #: §10 "worker hangs on a tool call": heartbeat alive, no checkpoint for N minutes.
    stale_progress_ms: int = 900_000
    #: §5.4: "after 2 consecutive no-op turns, the circuit breaker fires".
    max_noop_turns: int = 2
    #: §5.5 custom_verifier: `verifiers/<name>.py`. Empty means <kit>/verifiers.
    verifiers_dir: str | None = None
    #: §5.6 notification sink (append-only JSONL) and optional external command.
    notify_log: str | None = None
    notify_cmd: str | None = None
    #: Where a spawned worker finds the kit's `src/` on sys.path (multiprocessing spawn).
    pythonpath: list[str] = field(default_factory=list)
    #: External turn command for a real (non-scripted) worker; see model.CmdTurn.
    turn_cmd: str | None = None

    # ------------------------------------------------------------------------
    @property
    def sqlite_path(self) -> Path:
        """The filesystem path behind `store_dsn` (`sqlite:///<path>`), for pathlib callers."""
        dsn = (self.store_dsn or "").strip()
        if dsn.startswith("sqlite:///"):
            raw = dsn[len("sqlite:///") :]
        elif dsn.startswith("sqlite://"):
            raw = dsn[len("sqlite://") :]
        else:
            raw = dsn
        p = Path(raw)
        if not p.is_absolute():
            p = Path.cwd() / p
        return p

    def with_db(self, path: str | Path) -> "RuntimeConfig":
        """Return a copy pointed at another database file (the CLI's `--db`)."""
        clone = RuntimeConfig.from_dict(self.to_dict())
        clone.store_dsn = "sqlite:///" + str(Path(path).resolve()).replace("\\", "/")
        return clone

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RuntimeConfig":
        raw = dict(data or {})
        # §9 nests the whole block under `runtime:`; accept either shape.
        if "runtime" in raw and isinstance(raw["runtime"], dict):
            raw = dict(raw["runtime"])
        judge = raw.pop("judge", None)
        budgets = raw.pop("budgets", None)
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"unknown runtime config keys: {sorted(unknown)}")
        cfg = cls(**raw)
        if isinstance(judge, dict):
            cfg.judge = _build(JudgeConfig, judge)
        if isinstance(budgets, dict):
            cfg.budgets = _build(Budgets, budgets)
        if not cfg.pythonpath:
            cfg.pythonpath = [str(Path(__file__).resolve().parents[1])]
        return cfg

    @classmethod
    def load(cls, path: str | Path | None) -> "RuntimeConfig":
        """Load the optional JSON config; no path means pure defaults."""
        if not path:
            return cls.from_dict({})
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"runtime config not found: {p}")
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def heartbeat_ms(self) -> int:
        """§5.3: heartbeat every lease_ms/3, unless the operator set an explicit interval."""
        configured = int(self.heartbeat_interval_ms or 0)
        third = max(1, int(self.lease_ms // 3))
        # An explicit value that is shorter than the lease is honoured; the default (40s vs a 120s
        # lease) is already lease/3. Clamp only when it would outlive the lease.
        return configured if 0 < configured <= int(self.lease_ms) else third


def _build(klass, data: dict[str, Any]):
    known = {f.name for f in fields(klass)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown {klass.__name__} keys: {sorted(unknown)}")
    return klass(**data)
