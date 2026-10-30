"""The Judge: the only authority on `done` (§5.5).

The judge exists because "I'm done" is not evidence. It reads the contract, runs every check
against the world (filesystem, command exit codes, HTTP, JSON schema, a strict rubric call, or a
named verifier), and returns one of four verdicts. Nothing about the model's prose enters this
module: `run_check` receives a contract entry and a path base, never a transcript.

Two disciplines are load-bearing and are implemented exactly as §5.5 words them:

* **All checks must pass.** Not a quorum, not a majority. A lenient rubric cannot outvote a
  deterministic check that failed.
* **The rubric prompt is strict and parsed strictly.** Temperature 0 (the caller's job), two
  retries on invalid JSON, score 0 when evidence is missing.

Hard-block vs retryable is the difference between "another turn could fix this" and "no turn can":
a missing file is retryable, a malformed check is not, because the contract itself is broken.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

import jsonschema

from .config import RuntimeConfig

log = logging.getLogger("auton.judge")

#: §5.4, verbatim. The shape of this block is the fix for announce-and-stall: it names what is
#: still failing, shows what was observed, and forbids restating the plan.
CONTINUATION_TEMPLATE = """CONTINUE. The goal is not yet complete.
Objective: {objective}
Completion contract summary: {summary}
Latest progress: {progress}
Blocking checks (still failing):
{failing}
Do not restate your plan. Take the next concrete action (tool call or
artifact write) that moves a failing check toward passing. If you are
genuinely blocked, call task.block with a reason."""

#: §5.5 LLM-as-judge prompt, verbatim. The literal JSON braces are doubled because this is a
#: `str.format` template; the rendered prompt is exactly the guide's text.
RUBRIC_PROMPT = (
    'You are a strict verifier. Given the rubric and the evidence below, return JSON '
    '{{"score": <int>, "pass": <bool>, "reason": "<short>"}}. Do not be lenient. If evidence is '
    "missing, score 0.\n\n"
    "Rubric:\n{rubric}\n\n"
    "Evidence:\n{evidence}\n"
)

STATUSES: tuple[str, ...] = ("done", "continue", "blocked", "failed")


# ---------------------------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------------------------
@dataclass
class Verdict:
    """§5.5 verdict. `evidence` is per-check evidence, not prose.

    `_store` / `_task_id` are not serialised; they let `same_state_as_previous` consult the
    checkpoint log after a restart, which is why the no-progress breaker survives a crash.
    """

    status: str
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    continuation_prompt: str = ""
    fingerprint: str = ""
    _store: Any = field(default=None, repr=False, compare=False)
    _task_id: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown verdict status {self.status!r}; expected one of {STATUSES}")

    def to_dict(self) -> dict[str, Any]:
        """The serialisable form written to task_steps."""
        return {
            "status": self.status,
            "evidence": self.evidence,
            "reason": self.reason,
            "continuation_prompt": self.continuation_prompt,
            "fingerprint": self.fingerprint,
        }

    @property
    def failing(self) -> list[dict[str, Any]]:
        return [c for c in (self.evidence.get("checks") or []) if not c.get("passed")]

    def same_state_as_previous(self, window: int = 3) -> bool:
        """§6 step 5: has the failing set + progress note been identical for `window` turns?

        Reads judgment checkpoints rather than holding state in the worker: a resumed worker gets
        the same answer as one that never died.
        """
        if not self.fingerprint or self._store is None or not self._task_id:
            return False
        window = max(1, int(window))
        steps = [s for s in self._store.recent_steps(self._task_id, n=window * 3) if s.kind == "judgment"]
        prints: list[str] = []
        for step in reversed(steps):
            out = step.output if isinstance(step.output, Mapping) else {}
            fp = str(out.get("fingerprint") or "")
            prints.append(fp)
        prints = prints[-window:]
        if len(prints) < window:
            return False
        return len(set(prints)) == 1 and prints[0] == self.fingerprint


@dataclass
class CheckResult:
    """One check's outcome, with the two flags the verdict algorithm branches on."""

    check: dict[str, Any]
    passed: bool
    evidence: str = ""
    hard_block: bool = False
    retryable: bool = True
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        target = (
            self.check.get("path")
            or self.check.get("url")
            or self.check.get("cmd")
            or self.check.get("name")
            or self.check.get("rubric")
            or ""
        )
        return f"{self.check.get('type')}:{target}"

    @property
    def description(self) -> str:
        """Human wording for the continuation block. Deterministic, no model involved."""
        c = self.check
        ctype = c.get("type")
        if c.get("description"):
            return str(c["description"])
        if ctype == "file_exists":
            return f"file exists: {c.get('path')}"
        if ctype == "file_min_bytes":
            return f"file {c.get('path')} is at least {c.get('min')} bytes"
        if ctype == "command_exit_zero":
            return f"command exits 0: {c.get('cmd')}"
        if ctype == "json_schema":
            return f"file {c.get('path')} matches its JSON schema"
        if ctype == "http_status":
            return f"GET {c.get('url')} returns HTTP {c.get('expect', 200)}"
        if ctype == "llm_rubric":
            return f"rubric (min score {c.get('min_score')}): {c.get('rubric')}"
        if ctype == "custom_verifier":
            return f"verifier {c.get('name')} returns True"
        return str(ctype)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.check.get("type"),
            "check": self.check,
            "description": self.description,
            "passed": bool(self.passed),
            "hard_block": bool(self.hard_block),
            "retryable": bool(self.retryable),
            "evidence": self.evidence,
            "detail": self.detail,
        }


@dataclass
class CheckContext:
    """Everything a check may read. Note what is absent: the model's last turn."""

    task: Any
    store: Any = None
    base_dir: Path = field(default_factory=Path.cwd)
    judge_fn: Callable[[str], Any] | None = None
    verifiers_dir: Path | None = None
    cmd_timeout_s: float = 600.0
    http_timeout_s: float = 15.0
    judge_max_retries: int = 2

    def resolve(self, raw: str) -> Path:
        p = Path(str(raw))
        if not p.is_absolute():
            p = self.base_dir / p
        return p

    def payload(self) -> dict[str, Any]:
        task = self.task
        if task is None:
            return {}
        payload = getattr(task, "payload", None)
        if payload is None and isinstance(task, Mapping):
            payload = task.get("payload")
        return dict(payload or {})


def _fail(check: dict[str, Any], evidence: str, *, hard: bool = False, retryable: bool = True, **detail: Any) -> CheckResult:
    return CheckResult(check=check, passed=False, evidence=evidence, hard_block=hard, retryable=retryable, detail=detail)


def _ok(check: dict[str, Any], evidence: str = "", **detail: Any) -> CheckResult:
    return CheckResult(check=check, passed=True, evidence=evidence, detail=detail)


# ---------------------------------------------------------------------------------------------
# The seven check types (§5.5 table)
# ---------------------------------------------------------------------------------------------
def check_file_exists(check: dict[str, Any], ctx: CheckContext) -> CheckResult:
    raw = check.get("path")
    if not raw:
        return _fail(check, "malformed check: missing 'path'", hard=True)
    p = ctx.resolve(str(raw))
    try:
        exists = p.exists()
    except OSError as exc:
        return _fail(check, f"cannot stat {p}: {exc}", hard=True)
    if not exists:
        # A missing artifact is exactly what another turn can fix.
        return _fail(check, f"missing: {p}", retryable=True)
    return _ok(check, f"present: {p}", path=str(p))


def check_file_min_bytes(check: dict[str, Any], ctx: CheckContext) -> CheckResult:
    raw = check.get("path")
    if not raw:
        return _fail(check, "malformed check: missing 'path'", hard=True)
    minimum = check.get("min")
    if not isinstance(minimum, int) or isinstance(minimum, bool):
        return _fail(check, "malformed check: 'min' must be an integer", hard=True)
    p = ctx.resolve(str(raw))
    if not p.exists():
        return _fail(check, f"missing: {p}", retryable=True)
    try:
        size = p.stat().st_size
    except OSError as exc:
        return _fail(check, f"cannot stat {p}: {exc}", retryable=True)
    if size < minimum:
        return _fail(check, f"{p} is {size} bytes, needs >= {minimum}", retryable=True, bytes=size)
    return _ok(check, f"{p} is {size} bytes (>= {minimum})", bytes=size)


def check_command_exit_zero(check: dict[str, Any], ctx: CheckContext) -> CheckResult:
    cmd = check.get("cmd")
    if not cmd:
        return _fail(check, "malformed check: missing 'cmd'", hard=True)
    timeout = float(check.get("timeout_s") or ctx.cmd_timeout_s)
    shell = isinstance(cmd, str)
    try:
        proc = subprocess.run(
            cmd,
            shell=shell,
            cwd=str(ctx.base_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return _fail(check, f"command not found: {exc}", hard=True)
    except subprocess.TimeoutExpired:
        return _fail(check, f"command timed out after {timeout}s: {cmd}", retryable=True)
    except OSError as exc:
        return _fail(check, f"command could not run: {exc}", hard=True)
    tail = (proc.stdout or "")[-2000:]
    err_tail = (proc.stderr or "")[-2000:]
    detail = {"exit_code": proc.returncode, "stdout": tail, "stderr": err_tail}
    if proc.returncode != 0:
        return _fail(
            check,
            f"exit {proc.returncode}; stderr: {err_tail.strip()[:300] or '(empty)'}",
            retryable=True,
            **detail,
        )
    return _ok(check, f"exit 0; stdout: {tail.strip()[:300] or '(empty)'}", **detail)


def check_json_schema(check: dict[str, Any], ctx: CheckContext) -> CheckResult:
    raw = check.get("path")
    if not raw:
        return _fail(check, "malformed check: missing 'path'", hard=True)
    p = ctx.resolve(str(raw))
    if not p.is_file():
        return _fail(check, f"missing: {p}", retryable=True)
    schema = check.get("schema")
    if schema is None:
        schema_path = check.get("schema_path")
        if not schema_path:
            return _fail(check, "malformed check: needs 'schema' or 'schema_path'", hard=True)
        sp = ctx.resolve(str(schema_path))
        if not sp.is_file():
            return _fail(check, f"schema file missing: {sp}", hard=True)
        try:
            schema = json.loads(sp.read_text(encoding="utf-8"))
        except ValueError as exc:
            return _fail(check, f"schema file is not JSON: {exc}", hard=True)
    try:
        instance = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return _fail(check, f"{p} is not readable JSON: {exc}", retryable=True)
    try:
        validator = jsonschema.Draft202012Validator(schema)
    except jsonschema.SchemaError as exc:
        return _fail(check, f"invalid JSON schema: {exc.message}", hard=True)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(x) for x in first.path) or "<root>"
        return _fail(check, f"{where}: {first.message}", retryable=True, errors=len(errors))
    return _ok(check, f"{p} matches the schema")


def check_http_status(check: dict[str, Any], ctx: CheckContext) -> CheckResult:
    url = check.get("url")
    if not url:
        return _fail(check, "malformed check: missing 'url'", hard=True)
    expect = int(check.get("expect", 200) or 200)
    timeout = float(check.get("timeout_s") or ctx.http_timeout_s)
    req = urllib.request.Request(str(url), method="GET", headers={"User-Agent": "lygo-auton-judge/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = int(resp.status)
            body = resp.read(512).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        code = int(exc.code)
        body = ""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # A dead endpoint is worth another turn; it is not a broken contract.
        return _fail(check, f"request failed: {exc}", retryable=True)
    detail = {"status": code, "body_head": body[:200]}
    if code != expect:
        return _fail(check, f"HTTP {code}, expected {expect}", retryable=True, **detail)
    return _ok(check, f"HTTP {code}", **detail)


def _parse_rubric_reply(text: str) -> dict[str, Any] | None:
    """Strict parse: a JSON object with int score and bool pass. No guessing."""
    raw = str(text or "").strip()
    if not raw:
        return None
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(raw[start : end + 1])
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    score = obj.get("score")
    passed = obj.get("pass")
    if isinstance(score, bool) or not isinstance(score, int):
        return None
    if not isinstance(passed, bool):
        return None
    return obj


def check_llm_rubric(check: dict[str, Any], ctx: CheckContext) -> CheckResult:
    rubric = check.get("rubric")
    min_score = check.get("min_score")
    if not rubric:
        return _fail(check, "malformed check: missing 'rubric'", hard=True)
    if not isinstance(min_score, int) or isinstance(min_score, bool):
        return _fail(check, "malformed check: missing integer 'min_score'", hard=True)
    if ctx.judge_fn is None:
        # No judge model wired: the contract cannot be settled at all. Not retryable - another
        # identical turn changes nothing.
        return _fail(check, "no judge model configured for llm_rubric", hard=True)
    evidence = _rubric_evidence(ctx)
    prompt = RUBRIC_PROMPT.format(rubric=rubric, evidence=evidence or "(no evidence found)")
    attempts = 1 + max(0, int(ctx.judge_max_retries))
    last_raw = ""
    for attempt in range(attempts):
        try:
            reply = ctx.judge_fn(prompt)
        except Exception as exc:  # noqa: BLE001 - a flaky judge is a retry, not a pass
            last_raw = f"{type(exc).__name__}: {exc}"
            time.sleep(0.05 * (attempt + 1))
            continue
        text = reply.get("text") if isinstance(reply, Mapping) else getattr(reply, "text", reply)
        last_raw = str(text or "")
        parsed = _parse_rubric_reply(last_raw)
        if parsed is None:
            log.warning("rubric reply %d was not valid JSON: %r", attempt + 1, last_raw[:160])
            continue
        score = int(parsed["score"])
        max_score = int(check.get("max_score", 5) or 5)
        passed = bool(parsed["pass"]) and score >= int(min_score)
        detail = {"score": score, "max_score": max_score, "judge_pass": bool(parsed["pass"]),
                  "judge_reason": str(parsed.get("reason") or "")[:400], "attempts": attempt + 1}
        if not passed:
            return _fail(
                check,
                f"score {score}/{max_score} < required {min_score}: {detail['judge_reason'] or 'no reason given'}",
                retryable=True,
                **detail,
            )
        return _ok(check, f"score {score}/{max_score} >= {min_score}", **detail)
    return _fail(
        check,
        f"judge returned no valid JSON after {attempts} attempts: {last_raw[:200]!r}",
        retryable=True,
        raw=last_raw[:1000],
    )


def _rubric_evidence(ctx: CheckContext) -> str:
    """Evidence for the rubric = real artifacts, never the model's narration (§5.5 anti-gaming)."""
    payload = ctx.payload()
    pieces: list[str] = []
    for raw in payload.get("evidence_paths") or []:
        p = ctx.resolve(str(raw))
        if not p.is_file():
            pieces.append(f"- {p}: missing")
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pieces.append(f"- {p}: unreadable")
            continue
        pieces.append(f"- {p} ({p.stat().st_size} bytes):\n{text[:4000]}")
    for item in payload.get("evidence") or []:
        pieces.append(f"- {str(item)[:1000]}")
    if ctx.store is not None and ctx.task is not None:
        for step in ctx.store.recent_steps(getattr(ctx.task, "id", ""), n=10):
            if step.kind == "tool_call":
                pieces.append(f"- tool {json.dumps(step.input)[:300]} -> {json.dumps(step.output)[:600]}")
    return "\n".join(pieces)


def check_custom_verifier(check: dict[str, Any], ctx: CheckContext) -> CheckResult:
    name = check.get("name")
    if not name:
        return _fail(check, "malformed check: missing 'name'", hard=True)
    safe = "".join(ch for ch in str(name) if ch.isalnum() or ch in "_-")
    if safe != str(name):
        return _fail(check, f"malformed check: verifier name {name!r} is not a plain module name", hard=True)
    base = Path(ctx.verifiers_dir) if ctx.verifiers_dir else Path(__file__).resolve().parents[2] / "verifiers"
    candidates = [base / f"{safe}.py", base / safe / "__init__.py"]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        return _fail(check, f"verifier not found: {name} (looked in {base})", hard=True)
    mod_name = f"auton_verifier_{safe}"
    try:
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            return _fail(check, f"verifier {name} could not be loaded", hard=True)
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - a verifier that cannot import is a broken contract
        return _fail(check, f"verifier {name} failed to import: {type(exc).__name__}: {exc}", hard=True)
    func = getattr(module, str(check.get("func") or "verify"), None)
    if not callable(func):
        return _fail(check, f"verifier {name} has no callable {check.get('func') or 'verify'}", hard=True)
    kwargs = _verifier_kwargs(func, check, ctx)
    try:
        verdict = func(**kwargs)
    except Exception as exc:  # noqa: BLE001
        return _fail(check, f"verifier {name} raised {type(exc).__name__}: {exc}", retryable=True)
    if not isinstance(verdict, bool):
        return _fail(check, f"verifier {name} returned {type(verdict).__name__}, must return bool", hard=True)
    if not verdict:
        return _fail(check, f"verifier {name} returned False", retryable=True)
    return _ok(check, f"verifier {name} returned True")


def _verifier_kwargs(func: Callable[..., Any], check: dict[str, Any], ctx: CheckContext) -> dict[str, Any]:
    """Pass only what the verifier asks for: task, store, args, payload, base_dir."""
    import inspect

    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return {}
    available = {
        "task": ctx.task,
        "store": ctx.store,
        "args": dict(check.get("args") or {}),
        "payload": ctx.payload(),
        "base_dir": ctx.base_dir,
    }
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return available
    return {k: v for k, v in available.items() if k in params}


CHECKS: dict[str, Callable[[dict[str, Any], CheckContext], CheckResult]] = {
    "file_exists": check_file_exists,
    "file_min_bytes": check_file_min_bytes,
    "command_exit_zero": check_command_exit_zero,
    "json_schema": check_json_schema,
    "http_status": check_http_status,
    "llm_rubric": check_llm_rubric,
    "custom_verifier": check_custom_verifier,
}


def run_check(check: Mapping[str, Any], ctx: CheckContext) -> CheckResult:
    """Dispatch one check. Unknown type = broken contract = hard block (never a silent pass)."""
    if not isinstance(check, Mapping):
        return _fail({"type": "?", "raw": str(check)[:200]}, "malformed check: not an object", hard=True)
    ctype = str(check.get("type") or "")
    fn = CHECKS.get(ctype)
    if fn is None:
        return _fail(dict(check), f"malformed check: unknown type {ctype!r}", hard=True)
    try:
        return fn(dict(check), ctx)
    except Exception as exc:  # noqa: BLE001 - a bug in a check must not crash the worker
        log.exception("check %s raised", ctype)
        return _fail(dict(check), f"check raised {type(exc).__name__}: {exc}", hard=True)


# ---------------------------------------------------------------------------------------------
# Evaluation (§5.5 algorithm) and the continuation text (§5.4)
# ---------------------------------------------------------------------------------------------
def fingerprint(failing: list[CheckResult], progress_note: str | None) -> str:
    """§6: hash(sorted(failing ids) + progress_note_hash). Stable across processes."""
    ids = sorted(r.id for r in failing)
    note_hash = hashlib.sha256((progress_note or "").encode("utf-8")).hexdigest()
    blob = "\n".join(ids) + "\n" + note_hash
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def render_continuation(
    objective: str,
    summary: str,
    progress: str | None,
    failing: list[CheckResult],
) -> str:
    lines = [f"  - {r.description} \u2192 observed: {r.evidence or 'nothing observed'}" for r in failing]
    return CONTINUATION_TEMPLATE.format(
        objective=objective.strip(),
        summary=summary.strip() or "(no checks)",
        progress=(progress or "(no progress recorded yet)").strip(),
        failing="\n".join(lines) if lines else "  - (none)",
    )


def evaluate(
    contract: Mapping[str, Any],
    task: Any,
    store: Any = None,
    cfg: RuntimeConfig | None = None,
    judge_fn: Callable[[str], Any] | None = None,
    base_dir: str | Path | None = None,
    verifiers_dir: str | Path | None = None,
) -> Verdict:
    """Run every check, then apply §5.5's four-way branch. The only producer of `done`."""
    cfg = cfg or RuntimeConfig()
    payload = dict(getattr(task, "payload", None) or {}) if task is not None else {}
    root = base_dir or payload.get("cwd") or Path.cwd()
    ctx = CheckContext(
        task=task,
        store=store,
        base_dir=Path(root),
        judge_fn=judge_fn,
        verifiers_dir=Path(verifiers_dir) if verifiers_dir else (Path(cfg.verifiers_dir) if cfg.verifiers_dir else None),
        judge_max_retries=int(cfg.judge.max_retries),
    )
    checks = list(contract.get("checks") or [])
    results = [run_check(c, ctx) for c in checks]

    failing = [r for r in results if not r.passed]
    objective = str(contract.get("objective") or "")
    evidence = {"checks": [r.to_dict() for r in results], "objective": objective}

    if not failing:
        if contract.get("requires_human_review"):
            # §5.1: the runtime will not auto-complete such goals.
            return Verdict(
                "blocked",
                evidence,
                "requires_human_review: all checks passed, but a human must approve the result",
                "",
                "",
                _store=store,
                _task_id=getattr(task, "id", None),
            )
        return Verdict("done", evidence, "all checks passed", "", "", _store=store, _task_id=getattr(task, "id", None))

    if any(r.hard_block for r in failing):
        reason = "unrecoverable check failed: " + "; ".join(r.description for r in failing if r.hard_block)
        return Verdict("blocked", evidence, reason, "", "", _store=store, _task_id=getattr(task, "id", None))

    progress_note = getattr(task, "progress_note", None)
    fp = fingerprint(failing, progress_note)

    if all(r.retryable for r in failing):
        summary = _summary(contract)
        cont = render_continuation(objective, summary, progress_note, failing)
        return Verdict(
            "continue",
            evidence,
            f"{len(failing)} checks still failing",
            cont,
            fp,
            _store=store,
            _task_id=getattr(task, "id", None),
        )

    return Verdict(
        "failed",
        evidence,
        "non-retryable failure: " + "; ".join(r.description for r in failing if not r.retryable),
        "",
        fp,
        _store=store,
        _task_id=getattr(task, "id", None),
    )


def _summary(contract: Mapping[str, Any]) -> str:
    from .contract import summarize

    return summarize(contract)
