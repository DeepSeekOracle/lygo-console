"""The turn adapter: the one seam between the runtime and whatever plays the model.

The runtime does not care *how* a turn is produced, only that it is a dict of
`{"text": str, "tool_calls": [{"name": ..., "arguments": {...}}], "tokens": int}`. That seam is why
the whole continuation loop can be exercised offline by `ScriptedTurn`, and why the same worker can
be driven by an external process (`CmdTurn`) or by the console itself (`console_turn` below).

**Wiring a real console turn.** The kit executes model tool calls with
`from tools import dispatch; dispatch(name, args)` (`src/tools.py:507`, imported the same way at
`src/chat_loop.py:11`). A production adapter therefore looks like this:

    from tools import dispatch                      # src/tools.py, needs <kit>/src on sys.path
    turn = {"text": reply_text, "tool_calls": parsed_calls, "tokens": usage.total_tokens}

The worker never calls `dispatch` itself - every tool call goes through
`auton.tools_bridge.execute_tool_idempotent`, which is what wraps it with the idempotency key and
the checkpoint rows. The console adapter's only job is to produce the turn dict; the bridge owns
execution.

`CmdTurn` exists for two reasons: a worker spawned by the dispatcher is a separate process and
cannot inherit a Python callable, and an operator needs a way to plug in a different backend
without editing the kit.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

#: Only these shapes are accepted from an adapter.
TURN_KEYS: tuple[str, ...] = ("text", "tool_calls", "tokens")


class TurnError(RuntimeError):
    """Base class for adapter failures, named so a worker can log a reason instead of a traceback."""


class ScriptedTurnExhausted(TurnError):
    """The script ran out of turns. In a test this is a bug in the script, not a model failure."""


class CmdTurnError(TurnError):
    """The external turn command failed, timed out, or printed something that is not a turn."""


@dataclass
class Turn:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "tool_calls": self.tool_calls, "tokens": self.tokens}

    @property
    def is_noop(self) -> bool:
        """§5.4: a turn with no tool call is a no-op. Prose alone proves nothing."""
        return not self.tool_calls


def as_turn(reply: Any) -> Turn:
    """Normalise anything an adapter returned into a `Turn`.

    Accepts a Turn, a mapping with text/tool_calls/tokens, or a bare string (treated as prose, which
    is a no-op turn by definition).
    """
    if isinstance(reply, Turn):
        return reply
    if isinstance(reply, str):
        return Turn(text=reply)
    if not isinstance(reply, Mapping):
        raise TurnError(f"turn adapter returned {type(reply).__name__}, expected mapping or str")
    calls = reply.get("tool_calls") or []
    if isinstance(calls, Mapping):
        calls = [calls]
    normalised: list[dict[str, Any]] = []
    for call in calls:
        if not isinstance(call, Mapping):
            raise TurnError(f"tool call is {type(call).__name__}, expected object")
        name = call.get("name") or call.get("tool")
        if not name:
            raise TurnError(f"tool call without a name: {dict(call)!r}")
        args = call.get("arguments", call.get("args", {})) or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"value": args}
        normalised.append({"name": str(name), "arguments": dict(args)})
    tokens = reply.get("tokens", 0) or 0
    try:
        tokens = int(tokens)
    except (TypeError, ValueError):
        tokens = 0
    return Turn(text=str(reply.get("text") or ""), tool_calls=normalised, tokens=tokens, raw=dict(reply))


# ---------------------------------------------------------------------------------------------
# (a) ScriptedTurn - the test double that makes the whole loop provable offline
# ---------------------------------------------------------------------------------------------
class ScriptedTurn:
    """A deterministic stand-in for the model.

    Each entry is either a turn mapping or a callable `context -> turn`, so a script can react to
    the continuation prompt (which is exactly what a real model does). Every context is retained in
    `contexts`, and `calls` counts invocations, so a test can assert what the worker actually fed
    the model.
    """

    def __init__(self, turns: Sequence[Any], name: str = "scripted") -> None:
        self._turns = list(turns)
        self.name = name
        self.contexts: list[Any] = []
        self.calls = 0

    def __len__(self) -> int:
        return len(self._turns)

    def __call__(self, context: Any) -> Turn:
        # The worker passes a dict; the Goal Manager passes a plain prompt string. Both are contexts.
        recorded = dict(context) if isinstance(context, Mapping) else context
        self.contexts.append(recorded)
        index = self.calls
        self.calls += 1
        if index >= len(self._turns):
            raise ScriptedTurnExhausted(
                f"scripted turn {index + 1} requested but only {len(self._turns)} were provided"
            )
        item = self._turns[index]
        if callable(item):
            return as_turn(item(dict(recorded) if isinstance(recorded, Mapping) else recorded))
        return as_turn(item)

    # -- convenience builders -------------------------------------------------
    @staticmethod
    def prose(*texts: str) -> "ScriptedTurn":
        """A script of turns that say things and do nothing - the announce-and-stall shape."""
        return ScriptedTurn([{"text": t, "tool_calls": [], "tokens": 5} for t in texts])

    @staticmethod
    def writes(*calls: tuple[str, dict[str, Any]], tokens: int = 10) -> "ScriptedTurn":
        """One tool call per turn, naming each call in order."""
        return ScriptedTurn(
            [{"text": f"call {name}", "tool_calls": [{"name": name, "arguments": args}], "tokens": tokens}
             for name, args in calls]
        )


# ---------------------------------------------------------------------------------------------
# (b) CmdTurn - an external process speaking JSON in / JSON out
# ---------------------------------------------------------------------------------------------
@dataclass
class CmdTurn:
    """Shell out for each turn: context JSON on stdin, turn JSON on stdout.

    This is the adapter a dispatcher-spawned worker can actually use, because a subprocess cannot
    inherit a callable. Exit code must be 0; stdout must be one JSON object. Anything else raises
    `CmdTurnError` with the tail of stderr, which the worker records as a failed turn rather than
    crashing the process.
    """

    cmd: Sequence[str] | str
    cwd: str | None = None
    timeout_s: float = 300.0

    def __call__(self, context: Mapping[str, Any]) -> Turn:
        payload = json.dumps(dict(context), ensure_ascii=False).encode("utf-8")
        shell = isinstance(self.cmd, str)
        try:
            proc = subprocess.run(
                self.cmd,
                shell=shell,
                cwd=self.cwd,
                input=payload,
                capture_output=True,
                timeout=self.timeout_s,
            )
        except FileNotFoundError as exc:
            raise CmdTurnError(f"turn command not found: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise CmdTurnError(f"turn command timed out after {self.timeout_s}s") from exc
        except OSError as exc:
            raise CmdTurnError(f"turn command could not start: {exc}") from exc
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace")[-800:]
            raise CmdTurnError(f"turn command exited {proc.returncode}: {err or '(no stderr)'}")
        out = (proc.stdout or b"").decode("utf-8", "replace").strip()
        if not out:
            raise CmdTurnError("turn command printed nothing on stdout")
        try:
            reply = json.loads(out)
        except ValueError as exc:
            raise CmdTurnError(f"turn command stdout is not JSON: {out[:200]!r}") from exc
        return as_turn(reply)


def turn_from_config(cfg: Any) -> Callable[[Mapping[str, Any]], Turn] | None:
    """Build the turn adapter a spawned worker should use, or None if none is configured."""
    cmd = getattr(cfg, "turn_cmd", None)
    if cmd:
        return CmdTurn(cmd, cwd=str(Path.cwd()))
    return None


def python_script_turn(script: str | Path, timeout_s: float = 300.0) -> CmdTurn:
    """Convenience: run `python <script>` as the turn command (used by the CLI and the tests)."""
    return CmdTurn([sys.executable, str(script)], timeout_s=timeout_s)
