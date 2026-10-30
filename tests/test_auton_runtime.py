"""Test harness for the autonomous task runtime (Engineering Guide §12).

Everything here runs offline: no model, no network beyond a loopback HTTP server the test itself
starts, no ComfyUI, no console. The tests are grouped by the milestone they prove:

    M1  persistence, contract validation, the goal manager
    M2  claim -> run -> done for a trivially satisfied contract
    M3  judge + continuation loop (ScriptedTurn), no-progress breaker
    M4  lease expiry, requeue, resume from the last checkpoint without repeating a completed call
    M5  the outbox: exactly one done event, read and marked consumed
    M7  idempotency shim, budgets, forbidden tools, human-review contracts

The property tests at the bottom assert the one invariant that must never depend on the script:
whatever the judge says, the task reaches a terminal state inside its budget.
"""

from __future__ import annotations

import importlib
import json
import random
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest

KIT = Path(__file__).resolve().parents[1]
if str(KIT / "src") not in sys.path:
    sys.path.insert(0, str(KIT / "src"))

from auton import contract as contract_mod  # noqa: E402
from auton.config import Budgets, RuntimeConfig  # noqa: E402
from auton.dispatcher import dispatcher_loop  # noqa: E402
from auton.judge import CheckContext, Verdict, render_continuation, run_check  # noqa: E402
from auton.model import ScriptedTurn, ScriptedTurnExhausted, as_turn  # noqa: E402
from auton.store import (  # noqa: E402
    TASK_STATES,
    TERMINAL_STATES,
    IllegalTransition,
    Store,
    legal_transition,
    now_ms,
)
from auton.tools_bridge import execute_tool_idempotent, idempotency_key  # noqa: E402
from auton.watcher import watcher_loop  # noqa: E402
from auton.worker import CONTROL_TOOLS, FORBIDDEN_NAMES, budget_reason, worker_main  # noqa: E402


# ---------------------------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------------------------
@pytest.fixture()
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "runtime.db")
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def cfg(tmp_path: Path) -> RuntimeConfig:
    c = RuntimeConfig.from_dict({})
    c.store_dsn = "sqlite:///" + str(tmp_path / "runtime.db").replace("\\", "/")
    c.lease_ms = 5_000
    c.heartbeat_interval_ms = 500
    c.no_progress_window = 3
    c.max_concurrent_workers = 2
    c.budgets = Budgets(default_max_turns=10, default_max_attempts=3, default_max_wall_s=120,
                        default_max_tokens=100_000)
    return c


class FakeTools:
    """A dict-backed tool layer that counts real side effects, so duplication is visible."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.effects: list[str] = []

    def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "write_file":
            path = self.root / str(args["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(args.get("content") or ""), encoding="utf-8")
            self.effects.append("write:" + str(args["path"]))
            return {"ok": True, "path": str(path)}
        if name == "append":
            path = self.root / str(args.get("path") or "scratch.log")
            with path.open("a", encoding="utf-8") as fh:
                fh.write(str(args.get("text") or "") + "\n")
            self.effects.append("append:" + str(path))
            return {"ok": True, "path": str(path)}
        if name == "boom":
            raise RuntimeError("tool exploded on purpose")
        return {"ok": False, "error": f"unknown_tool:{name}"}

    def count(self, prefix: str) -> int:
        return sum(1 for e in self.effects if e.startswith(prefix))


def make_goal(store: Store, cfg: RuntimeConfig, root: Path, checks: list[dict[str, Any]],
              text: str = "test goal", budget: dict[str, Any] | None = None,
              payload: dict[str, Any] | None = None) -> tuple[str, str]:
    contract = {"objective": text, "checks": checks, "requires_human_review":
                all(c["type"] not in contract_mod.OBSERVABLE_TYPES for c in checks)}
    return contract_mod.create_goal(
        store,
        text,
        contract,
        budget=budget or dict(cfg.budgets.as_task_budget()),
        payload={"cwd": str(root), **(payload or {})},
    )


def claim(store: Store, cfg: RuntimeConfig, worker_id: str = "w-test"):
    task = store.claim_task(worker_id, cfg.lease_ms)
    assert task is not None, "expected a queued task to claim"
    return task


def check(check_spec: dict[str, Any], root: Path, **kw: Any):
    ctx = CheckContext(task=kw.pop("task", None), store=kw.pop("store", None), base_dir=Path(root), **kw)
    return run_check(check_spec, ctx)


# ---------------------------------------------------------------------------------------------
# M1 / Judge unit: the seven check types, pass + fail + malformed
# ---------------------------------------------------------------------------------------------
def test_file_exists_pass_fail_malformed(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    assert check({"type": "file_exists", "path": "a.txt"}, tmp_path).passed
    missing = check({"type": "file_exists", "path": "nope.txt"}, tmp_path)
    assert not missing.passed and missing.retryable and not missing.hard_block
    malformed = check({"type": "file_exists"}, tmp_path)
    assert not malformed.passed and malformed.hard_block
    assert "malformed" in malformed.evidence


def test_file_min_bytes_pass_fail_malformed(tmp_path: Path) -> None:
    (tmp_path / "big.bin").write_bytes(b"0" * 64)
    assert check({"type": "file_min_bytes", "path": "big.bin", "min": 64}, tmp_path).passed
    small = check({"type": "file_min_bytes", "path": "big.bin", "min": 65}, tmp_path)
    assert not small.passed and small.retryable
    assert "needs >= 65" in small.evidence
    assert check({"type": "file_min_bytes", "min": 1}, tmp_path).hard_block
    assert check({"type": "file_min_bytes", "path": "big.bin", "min": "lots"}, tmp_path).hard_block
    assert check({"type": "file_min_bytes", "path": "gone.bin", "min": 1}, tmp_path).retryable


def test_command_exit_zero_pass_fail_malformed(tmp_path: Path) -> None:
    ok = check({"type": "command_exit_zero", "cmd": [sys.executable, "-c", "print('hello')"]}, tmp_path)
    assert ok.passed and "hello" in ok.evidence
    bad = check({"type": "command_exit_zero", "cmd": [sys.executable, "-c", "import sys; sys.exit(3)"]}, tmp_path)
    assert not bad.passed and bad.retryable and "exit 3" in bad.evidence
    malformed = check({"type": "command_exit_zero"}, tmp_path)
    assert malformed.hard_block
    notfound = check({"type": "command_exit_zero", "cmd": ["definitely-not-a-real-binary-xyz"]}, tmp_path)
    assert not notfound.passed and notfound.hard_block


def test_json_schema_pass_fail_malformed(tmp_path: Path) -> None:
    schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
    (tmp_path / "good.json").write_text('{"name": "lygo"}', encoding="utf-8")
    (tmp_path / "bad.json").write_text('{"nome": "lygo"}', encoding="utf-8")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert check({"type": "json_schema", "path": "good.json", "schema": schema}, tmp_path).passed
    failed = check({"type": "json_schema", "path": "bad.json", "schema": schema}, tmp_path)
    assert not failed.passed and failed.retryable and "name" in failed.evidence
    assert check({"type": "json_schema", "path": "broken.json", "schema": schema}, tmp_path).retryable
    assert check({"type": "json_schema", "schema": schema}, tmp_path).hard_block
    assert check({"type": "json_schema", "path": "good.json"}, tmp_path).hard_block
    assert check({"type": "json_schema", "path": "good.json", "schema": {"type": 7}}, tmp_path).hard_block
    # schema_path variant, the shape a goal manager is more likely to emit
    (tmp_path / "s.json").write_text(json.dumps(schema), encoding="utf-8")
    assert check({"type": "json_schema", "path": "good.json", "schema_path": "s.json"}, tmp_path).passed


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server's spelling
        code = 200 if self.path == "/ok" else 404
        body = b'{"ok": true}'
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # silence the test output
        return


@contextmanager
def loopback_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_status_pass_fail_malformed(tmp_path: Path) -> None:
    with loopback_server() as base:
        assert check({"type": "http_status", "url": base + "/ok"}, tmp_path).passed
        missing = check({"type": "http_status", "url": base + "/missing"}, tmp_path)
        assert not missing.passed and missing.retryable and "HTTP 404" in missing.evidence
        assert check({"type": "http_status", "url": base + "/missing", "expect": 404}, tmp_path).passed
    # A refused connection is retryable, not a broken contract.
    dead = check({"type": "http_status", "url": "http://127.0.0.1:9/none", "timeout_s": 1}, tmp_path)
    assert not dead.passed and dead.retryable
    assert check({"type": "http_status"}, tmp_path).hard_block


def test_llm_rubric_pass_fail_malformed_and_strict_parse(tmp_path: Path) -> None:
    spec = {"type": "llm_rubric", "rubric": "The text mentions LYGO", "max_score": 5, "min_score": 4}
    prompts: list[str] = []

    def good(prompt: str) -> str:
        prompts.append(prompt)
        return 'Here: {"score": 5, "pass": true, "reason": "it does"}'

    assert check(dict(spec), tmp_path, judge_fn=good).passed
    assert "strict verifier" in prompts[0] and "LYGO" in prompts[0]

    def low(prompt: str) -> str:
        return '{"score": 2, "pass": false, "reason": "too vague"}'

    failed = check(dict(spec), tmp_path, judge_fn=low)
    assert not failed.passed and failed.retryable and "score 2/5" in failed.evidence

    # Score alone is not enough: `pass: false` beats a high score.
    def liar(prompt: str) -> str:
        return '{"score": 5, "pass": false, "reason": "not really"}'

    assert not check(dict(spec), tmp_path, judge_fn=liar).passed

    calls = {"n": 0}

    def flaky(prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            return "not json at all"
        return '{"score": 4, "pass": true, "reason": "finally"}'

    assert check(dict(spec), tmp_path, judge_fn=flaky, judge_max_retries=2).passed
    assert calls["n"] == 3

    def never(prompt: str) -> str:
        return "sorry, no JSON"

    exhausted = check(dict(spec), tmp_path, judge_fn=never, judge_max_retries=2)
    assert not exhausted.passed and exhausted.retryable and "no valid JSON" in exhausted.evidence

    # No judge model at all: unrecoverable, so a hard block rather than a silent pass.
    nojudge = check(dict(spec), tmp_path)
    assert not nojudge.passed and nojudge.hard_block
    assert check({"type": "llm_rubric", "rubric": "x"}, tmp_path, judge_fn=good).hard_block
    assert check({"type": "llm_rubric", "min_score": 3}, tmp_path, judge_fn=good).hard_block


def test_custom_verifier_pass_fail_malformed(tmp_path: Path) -> None:
    vdir = tmp_path / "verifiers"
    vdir.mkdir()
    (vdir / "yes.py").write_text(
        "def verify(task=None, store=None, args=None):\n"
        "    return bool((args or {}).get('value', True))\n",
        encoding="utf-8",
    )
    (vdir / "notbool.py").write_text("def verify(task=None, store=None):\n    return 'yes'\n", encoding="utf-8")
    (vdir / "raises.py").write_text("def verify(task=None, store=None):\n    raise RuntimeError('nope')\n",
                                    encoding="utf-8")
    assert check({"type": "custom_verifier", "name": "yes"}, tmp_path, verifiers_dir=vdir).passed
    failed = check({"type": "custom_verifier", "name": "yes", "args": {"value": False}}, tmp_path,
                   verifiers_dir=vdir)
    assert not failed.passed and failed.retryable
    assert check({"type": "custom_verifier", "name": "notbool"}, tmp_path, verifiers_dir=vdir).hard_block
    raised = check({"type": "custom_verifier", "name": "raises"}, tmp_path, verifiers_dir=vdir)
    assert not raised.passed and raised.retryable
    missing = check({"type": "custom_verifier", "name": "absent"}, tmp_path, verifiers_dir=vdir)
    assert missing.hard_block
    assert check({"type": "custom_verifier", "path": "x"}, tmp_path, verifiers_dir=vdir).hard_block
    assert check({"type": "custom_verifier", "name": "../evil"}, tmp_path, verifiers_dir=vdir).hard_block


def test_unknown_check_type_is_a_hard_block(tmp_path: Path) -> None:
    result = check({"type": "vibes", "path": "x"}, tmp_path)
    assert not result.passed and result.hard_block
    assert "unknown type" in result.evidence


# ---------------------------------------------------------------------------------------------
# M1: contracts, the construction protocol and the human-review rule
# ---------------------------------------------------------------------------------------------
def test_contract_validation_accepts_the_guide_example() -> None:
    contract = {
        "objective": "Produce a 30s MP4 video about X saved at /out/x.mp4",
        "checks": [
            {"type": "file_exists", "path": "/out/x.mp4"},
            {"type": "file_min_bytes", "path": "/out/x.mp4", "min": 100000},
            {"type": "command_exit_zero", "cmd": "ffprobe -v error /out/x.mp4"},
            {"type": "llm_rubric", "rubric": "The video script matches the topic.", "max_score": 5, "min_score": 4},
        ],
        "requires_human_review": False,
    }
    assert contract_mod.validate_contract(contract) == []
    assert contract_mod.validate_or_raise(contract)["requires_human_review"] is False


def test_contract_validation_rejects_broken_contracts() -> None:
    assert contract_mod.validate_contract({"objective": "x"})
    assert contract_mod.validate_contract({"objective": "x", "checks": []})
    assert contract_mod.validate_contract({"objective": "x", "checks": [{"type": "file_exists"}]})
    assert contract_mod.validate_contract({"objective": "", "checks": [{"type": "file_exists", "path": "a"}]})
    assert contract_mod.validate_contract("not json at all")
    bad_rubric = contract_mod.validate_contract(
        {"objective": "x", "checks": [{"type": "llm_rubric", "rubric": "r", "min_score": 5, "max_score": 3}]}
    )
    assert any("min_score" in e for e in bad_rubric)
    with pytest.raises(contract_mod.ContractError):
        contract_mod.validate_or_raise({"objective": "x", "checks": [{"type": "nope"}]})


def test_rubric_only_contract_requires_human_review() -> None:
    only_rubric = {"objective": "x", "checks": [{"type": "llm_rubric", "rubric": "r", "min_score": 4}]}
    out = contract_mod.validate_or_raise(only_rubric)
    assert out["requires_human_review"] is True
    observable = {"objective": "x", "checks": [{"type": "file_exists", "path": "a"}]}
    assert contract_mod.validate_or_raise(observable)["requires_human_review"] is False


def test_build_contract_retries_then_falls_back() -> None:
    good = {"objective": "write a", "checks": [{"type": "file_exists", "path": "a.txt"}],
            "requires_human_review": False}
    scripted = ScriptedTurn([{"text": "here you go:\n```json\n" + json.dumps(good) + "\n```"}])
    contract, history = contract_mod.build_contract("write a.txt", scripted)
    assert contract["objective"] == "write a" and len(contract["checks"]) == 1
    assert any("accepted" in h for h in history)

    # First reply invalid, second valid: one retry is spent, the contract is still accepted.
    retry = ScriptedTurn([
        {"text": "I think the user wants a video!"},
        {"text": json.dumps(good)},
    ])
    contract2, history2 = contract_mod.build_contract("write a.txt", retry)
    assert contract2["checks"][0]["type"] == "file_exists"
    assert any(("invalid contract" in h) or ("no JSON" in h) for h in history2)

    # Always invalid: rubric-only fallback, flagged for a human.
    broken = ScriptedTurn([{"text": "no json"}, {"text": '{"objective": "x"}'}])
    contract3, history3 = contract_mod.build_contract("do something vague", broken)
    assert contract3["requires_human_review"] is True
    assert [c["type"] for c in contract3["checks"]] == ["llm_rubric"]
    assert any("fallback" in h for h in history3)
    assert contract_mod.validate_contract(contract3) == []


def test_goal_manager_persists_goal_and_task_together(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    goal_id, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a.txt"}])
    goal = store.get_goal(goal_id)
    task = store.get_task(task_id)
    assert goal is not None and task is not None
    assert goal.status == "active" and task.status == "queued"
    assert goal.budget["max_turns"] == cfg.budgets.default_max_turns
    assert task.payload["cwd"] == str(tmp_path)
    assert contract_mod.validate_contract(goal.contract) == []


# ---------------------------------------------------------------------------------------------
# State machine (§4): illegal transitions raise, legal ones work
# ---------------------------------------------------------------------------------------------
def test_every_illegal_transition_raises(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a.txt"}])
    checked = 0
    for from_status in TASK_STATES:
        for to_status in TASK_STATES:
            if legal_transition(from_status, to_status):
                continue
            with pytest.raises(IllegalTransition) as err:
                store.transition(task_id, from_status, to_status)
            assert err.value.from_status == from_status and err.value.to_status == to_status
            checked += 1
    assert checked > 0
    # Rule 8 in one line: nothing was coerced while we proved the rejections.
    assert store.get_task(task_id).status == "queued"


def test_legal_paths_through_the_state_machine(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a.txt"}])
    assert store.transition(task_id, "queued", "claimed")
    assert store.transition(task_id, "claimed", "running")
    assert store.transition(task_id, "running", "running")  # judge continue
    assert store.transition(task_id, "running", "done")
    assert store.get_task(task_id).status == "done"
    # Terminal is terminal.
    with pytest.raises(IllegalTransition):
        store.transition(task_id, "done", "running")

    _, t2 = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "b.txt"}])
    assert store.transition(t2, "queued", "claimed")
    assert store.transition(t2, "claimed", "retrying")
    assert store.transition(t2, "retrying", "queued")
    assert store.get_task(t2).status == "queued"
    assert store.transition(t2, "queued", "cancelled")  # any non-terminal -> cancelled


def test_transition_is_compare_and_set(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a.txt"}])
    assert store.transition(task_id, "queued", "claimed") is True
    # A second claimer that still believes it is queued loses the CAS instead of double-claiming.
    assert store.transition(task_id, "queued", "claimed") is False
    assert store.get_task(task_id).status == "claimed"


# ---------------------------------------------------------------------------------------------
# Atomic claim under concurrency (§5.2)
# ---------------------------------------------------------------------------------------------
def test_atomic_claim_with_ten_concurrent_claimers(tmp_path: Path) -> None:
    db = tmp_path / "claim.db"
    setup = Store(db)
    for i in range(10):
        gid = setup.create_goal({"text": f"g{i}", "contract": {"objective": "x", "checks": [
            {"type": "file_exists", "path": "x"}]}, "budget": {}})
        setup.create_task(gid, {"i": i})
    setup.close()

    barrier = threading.Barrier(10)
    claimed: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def claimer(n: int) -> None:
        s = Store(db)
        try:
            barrier.wait(timeout=30)
            task = s.claim_task(f"w{n}", 60_000)
            if task is not None:
                with lock:
                    claimed.append(task.id)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            with lock:
                errors.append(exc)
        finally:
            s.close()

    threads = [threading.Thread(target=claimer, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"claimers raised: {errors}"
    assert len(claimed) == 10, f"expected 10 claims, got {len(claimed)}"
    assert len(set(claimed)) == 10, "the same task was claimed twice"

    check_store = Store(db)
    try:
        assert check_store.count_tasks("queued") == 0
        assert check_store.count_tasks("claimed") == 10
        # And an eleventh claimer gets nothing rather than a duplicate.
        assert check_store.claim_task("w-late", 1000) is None
    finally:
        check_store.close()


# ---------------------------------------------------------------------------------------------
# M2 + M3: worker, judge, continuation
# ---------------------------------------------------------------------------------------------
def test_m2_single_turn_task_reaches_done(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    tools = FakeTools(tmp_path)
    goal_id, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a.txt"}])
    claim(store, cfg)
    outcome = worker_main(
        task_id, "w-m2", cfg=cfg, store=store, dispatcher=tools, base_dir=tmp_path, raise_on_error=True,
        turn_fn=ScriptedTurn.writes(("write_file", {"path": "a.txt", "content": "done"})),
    )
    assert outcome.status == "done" and outcome.turns == 1
    assert store.get_task(task_id).status == "done"
    assert store.get_goal(goal_id).status == "done"
    assert tools.count("write:a.txt") == 1


def test_m3_three_step_continuation_loop(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    tools = FakeTools(tmp_path)
    checks = [{"type": "file_exists", "path": p} for p in ("a.txt", "b.txt", "c.txt")]
    goal_id, task_id = make_goal(store, cfg, tmp_path, checks, text="write A, then B, then C")
    claim(store, cfg)
    script = ScriptedTurn(
        [
            {"text": "Writing A.", "tool_calls": [{"name": "write_file", "arguments": {"path": "a.txt"}}], "tokens": 7},
            {"text": "Writing B.", "tool_calls": [{"name": "write_file", "arguments": {"path": "b.txt"}}], "tokens": 7},
            {"text": "Writing C.", "tool_calls": [{"name": "write_file", "arguments": {"path": "c.txt"}}], "tokens": 7},
        ]
    )
    outcome = worker_main(task_id, "w-m3", cfg=cfg, store=store, dispatcher=tools, base_dir=tmp_path,
                          turn_fn=script, raise_on_error=True)

    assert outcome.status == "done", outcome
    assert outcome.turns == 3 and script.calls == 3, "a 3-step contract must take 3 judged turns"
    assert store.get_task(task_id).status == "done"
    assert store.get_goal(goal_id).status == "done"
    assert (tmp_path / "a.txt").is_file() and (tmp_path / "b.txt").is_file() and (tmp_path / "c.txt").is_file()

    # The continuation prompt is the fix: the second and third turns were told what still failed.
    assert script.contexts[0]["continuation"] is None
    for ctx in script.contexts[1:]:
        cont = ctx["continuation"]
        assert cont.startswith("CONTINUE. The goal is not yet complete.")
        assert "Objective: write A, then B, then C" in cont
        assert "Blocking checks (still failing):" in cont
        assert "Do not restate your plan." in cont
        assert "call task.block with a reason." in cont
    assert "b.txt" in script.contexts[1]["continuation"]
    assert "  - file exists: a.txt" not in script.contexts[1]["continuation"], \
        "a passing check must not appear in the failing list"
    assert "c.txt" in script.contexts[2]["continuation"]
    assert "  - file exists: b.txt" not in script.contexts[2]["continuation"]
    assert len(script.contexts[2]["continuation"].splitlines()) >= 7

    # Every meaningful action is a checkpoint, in order, with a judgment after each turn.
    steps = store.list_steps(task_id)
    kinds = [s.kind for s in steps]
    assert kinds.count("llm_call") == 3
    assert kinds.count("tool_call") == 3
    assert kinds.count("judgment") == 3
    assert [s.seq for s in steps] == list(range(1, len(steps) + 1))
    verdicts = [s.output["status"] for s in steps if s.kind == "judgment"]
    assert verdicts == ["continue", "continue", "done"]


def test_continuation_text_names_failing_checks_and_evidence(tmp_path: Path) -> None:
    failing = [
        check({"type": "file_exists", "path": "b.txt"}, tmp_path),
        check({"type": "file_min_bytes", "path": "a.txt", "min": 500}, tmp_path),
    ]
    text = render_continuation("write A then B", "a.txt exists; b.txt exists", "2 checks still failing", failing)
    assert text.startswith("CONTINUE. The goal is not yet complete.")
    assert "  - file exists: b.txt \u2192 observed: missing:" in text
    assert "  - file a.txt is at least 500 bytes \u2192 observed: missing:" in text
    assert text.endswith("call task.block with a reason.")


def test_prose_only_turn_is_a_noop_and_breaker_fires(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    tools = FakeTools(tmp_path)
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a.txt"}])
    claim(store, cfg)
    script = ScriptedTurn.prose(*["I will absolutely make the video now."] * 6)
    outcome = worker_main(task_id, "w-noop", cfg=cfg, store=store, dispatcher=tools, base_dir=tmp_path,
                          turn_fn=script, raise_on_error=True)
    assert outcome.status == "blocked"
    assert (store.get_task(task_id).result or {})["reason"] == "no_progress"
    assert script.calls == cfg.max_noop_turns, "the no-op breaker must fire after K_2 prose-only turns"
    assert store.get_goal(store.get_task(task_id).goal_id).used_turns == cfg.max_noop_turns
    assert tools.effects == []


def test_no_progress_fingerprint_breaker_fires_after_k_turns(store: Store, cfg: RuntimeConfig,
                                                             tmp_path: Path) -> None:
    """The model looks busy - a real tool call every turn - but the contract never advances."""
    tools = FakeTools(tmp_path)
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "report.md"}])
    claim(store, cfg)
    script = ScriptedTurn([
        {"text": f"pass {i}", "tool_calls": [{"name": "append", "arguments": {"text": f"pass {i}"}}]}
        for i in range(1, 9)
    ])
    outcome = worker_main(task_id, "w-noprog", cfg=cfg, store=store, dispatcher=tools, base_dir=tmp_path,
                          turn_fn=script, raise_on_error=True)
    result = store.get_task(task_id).result or {}
    assert outcome.status == "blocked" and result.get("reason") == "no_progress"
    assert "fingerprint unchanged" in str(result.get("detail"))
    assert outcome.turns >= cfg.no_progress_window
    # It kept calling the tool, so the no-op breaker is not what stopped it.
    assert result.get("detail", "").find("no tool call") == -1
    judgments = [s.output for s in store.list_steps(task_id) if s.kind == "judgment"]
    assert len(judgments) == outcome.turns
    tail = [j["fingerprint"] for j in judgments[-cfg.no_progress_window:]]
    assert len(set(tail)) == 1, "the breaker fired on a changing fingerprint"


def test_budget_exhaustion_blocks_with_the_right_reason(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    tools = FakeTools(tmp_path)
    checks = [{"type": "file_exists", "path": p} for p in ("a.txt", "b.txt", "c.txt")]
    budget = dict(cfg.budgets.as_task_budget())
    budget["max_turns"] = 2
    _, task_id = make_goal(store, cfg, tmp_path, checks, budget=budget)
    claim(store, cfg)
    script = ScriptedTurn.writes(*[("write_file", {"path": p}) for p in ("a.txt", "b.txt", "c.txt")])
    outcome = worker_main(task_id, "w-budget", cfg=cfg, store=store, dispatcher=tools, base_dir=tmp_path,
                          turn_fn=script, raise_on_error=True)
    assert outcome.status == "blocked"
    assert (store.get_task(task_id).result or {})["reason"] == "turn_budget_exhausted"
    assert outcome.turns == 2 and script.calls == 2, "budgets are checked BEFORE the turn, so turn 3 never ran"

    # Token budget: one 10-token turn against max_tokens=5 stops before turn 2.
    budget2 = dict(cfg.budgets.as_task_budget())
    budget2["max_tokens"] = 5
    _, t2 = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "z.txt"}], budget=budget2)
    claim(store, cfg)
    script2 = ScriptedTurn([{"text": "x", "tool_calls": [{"name": "write_file", "arguments": {"path": "z"}}],
                            "tokens": 10}] * 3)
    out2 = worker_main(t2, "w-tok", cfg=cfg, store=store, dispatcher=tools, base_dir=tmp_path,
                       turn_fn=script2, raise_on_error=True)
    assert out2.status == "blocked" and (store.get_task(t2).result or {})["reason"] == "token_budget_exhausted"
    assert out2.turns == 1

    # Wall clock: backdate the task so the deadline is already behind it.
    budget3 = dict(cfg.budgets.as_task_budget())
    budget3["max_wall_s"] = 2
    _, t3 = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "w.txt"}], budget=budget3)
    store.conn.execute("UPDATE tasks SET created_at=? WHERE id=?", (now_ms() - 10_000, t3))
    claim(store, cfg)
    out3 = worker_main(t3, "w-wall", cfg=cfg, store=store, dispatcher=tools, base_dir=tmp_path,
                       turn_fn=ScriptedTurn.writes(("write_file", {"path": "w.txt"})), raise_on_error=True)
    assert out3.status == "blocked" and (store.get_task(t3).result or {})["reason"] == "wall_clock_exhausted"
    assert out3.turns == 0


def test_budget_reason_reads_each_limit(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    gid, tid = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a"}])
    goal = store.get_goal(gid)
    task = store.get_task(tid)
    assert budget_reason(goal, task, now_ms(), cfg) is None
    goal.used_turns = goal.budget["max_turns"]
    assert budget_reason(goal, task, now_ms(), cfg) == "turn_budget_exhausted"
    goal.used_turns = 0
    goal.used_tokens = goal.budget["max_tokens"]
    assert budget_reason(goal, task, now_ms(), cfg) == "token_budget_exhausted"
    goal.used_tokens = 0
    assert budget_reason(goal, task, now_ms() - 10_000_000, cfg) == "wall_clock_exhausted"


def test_forbidden_task_complete_is_refused(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    tools = FakeTools(tmp_path)
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a.txt"}])
    claim(store, cfg)
    script = ScriptedTurn([
        {"text": "All done!", "tool_calls": [{"name": "task.complete", "arguments": {}}]},
        {"text": "Okay, writing it.", "tool_calls": [{"name": "write_file", "arguments": {"path": "a.txt"}}]},
    ])
    outcome = worker_main(task_id, "w-forbid", cfg=cfg, store=store, dispatcher=tools, base_dir=tmp_path,
                          turn_fn=script, raise_on_error=True)
    assert "task.complete" in FORBIDDEN_NAMES
    assert not any(t["name"] == "task.complete" for t in CONTROL_TOOLS)
    steps = store.list_steps(task_id)
    refused = [s for s in steps if s.kind == "tool_call" and (s.output or {}).get("error") == "forbidden_tool"]
    assert len(refused) == 1
    assert refused[0].output["detail"].startswith("only the Judge may complete")
    # The model's claim did not finish the task; the artifact did.
    assert outcome.status == "done" and outcome.turns == 2


def test_control_tools_are_exactly_three_and_block_works(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    tools = FakeTools(tmp_path)
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a.txt"}])
    claim(store, cfg)
    script = ScriptedTurn([
        {"text": "checking", "tool_calls": [{"name": "goal.status", "arguments": {}}]},
        {"text": "noting", "tool_calls": [{"name": "task.note", "arguments": {"note": "halfway"}}]},
        {"text": "stuck", "tool_calls": [{"name": "task.block", "arguments": {"reason": "no source footage"}}]},
    ])
    outcome = worker_main(task_id, "w-control", cfg=cfg, store=store, dispatcher=tools, base_dir=tmp_path,
                          turn_fn=script, raise_on_error=True)
    assert outcome.status == "blocked"
    result = store.get_task(task_id).result or {}
    assert result["reason"] == "model_blocked:no source footage"
    assert result["source"] == "task.block"
    assert {t["name"] for t in CONTROL_TOOLS} == {"task.block", "task.note", "goal.status"}
    assert store.events_of_kind(task_id, "blocked") == 1


def test_requires_human_review_never_auto_completes(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("ok", encoding="utf-8")
    contract = {"objective": "judge this", "requires_human_review": True,
                "checks": [{"type": "file_exists", "path": "a.txt"}]}
    goal_id, task_id = contract_mod.create_goal(store, "judge this", contract,
                                                budget=dict(cfg.budgets.as_task_budget()),
                                                payload={"cwd": str(tmp_path)})
    claim(store, cfg)
    tools = FakeTools(tmp_path)
    outcome = worker_main(task_id, "w-review", cfg=cfg, store=store, dispatcher=tools, base_dir=tmp_path,
                          turn_fn=ScriptedTurn.prose("nothing to do"), raise_on_error=True)
    assert outcome.status == "blocked"
    assert "requires_human_review" in (store.get_task(task_id).result or {})["reason"]
    assert store.events_of_kind(task_id, "needs_review") == 1
    assert store.get_goal(goal_id).status == "paused"


# ---------------------------------------------------------------------------------------------
# M4: durability - kill, lease expiry, requeue, resume
# ---------------------------------------------------------------------------------------------
def test_m4_killed_worker_resumes_from_checkpoint_without_repeating_calls(
    store: Store, cfg: RuntimeConfig, tmp_path: Path
) -> None:
    tools = FakeTools(tmp_path)
    checks = [{"type": "file_exists", "path": p} for p in ("a.txt", "b.txt", "c.txt")]
    goal_id, task_id = make_goal(store, cfg, tmp_path, checks)
    claim(store, cfg, "w-first")

    first = ScriptedTurn([
        {"text": "A", "tool_calls": [{"name": "write_file", "arguments": {"path": "a.txt"}}]},
        {"text": "B", "tool_calls": [{"name": "write_file", "arguments": {"path": "b.txt"}}]},
        {"text": "C", "tool_calls": [{"name": "write_file", "arguments": {"path": "c.txt"}}]},
    ])
    killed = worker_main(task_id, "w-first", cfg=cfg, store=store, dispatcher=tools, base_dir=tmp_path,
                         turn_fn=first, halt_after_turns=2, raise_on_error=True)
    assert killed.status == "halted" and killed.turns == 2
    # Exactly the state a kill -9 leaves: still running, still leased, checkpoints intact.
    row = store.get_task(task_id)
    assert row.status == "running" and row.worker_id == "w-first" and row.lease_expires_at is not None
    assert tools.count("write:a.txt") == 1 and tools.count("write:b.txt") == 1
    assert (tmp_path / "a.txt").is_file() and not (tmp_path / "c.txt").exists()
    steps_before = len(store.list_steps(task_id))

    # The lease expires; the dispatcher's reap puts the task back in the queue and counts the attempt.
    assert [t.id for t in store.find_expired_leases(row.lease_expires_at + 1)] == [task_id]
    assert store.requeue_stale(row.lease_expires_at + 1) == [task_id]
    row = store.get_task(task_id)
    assert row.status == "queued" and row.attempt_count == 1 and row.lease_expires_at is None
    assert store.events_of_kind(task_id, "heartbeat_lost") == 1

    # A second worker resumes. Its first context already contains the completed steps, so the model
    # continues with C instead of redoing A or B.
    resumed = claim(store, cfg, "w-second")
    assert resumed.id == task_id
    second = ScriptedTurn([
        {"text": "B is done; writing C", "tool_calls": [{"name": "write_file", "arguments": {"path": "c.txt"}}]}
    ])
    outcome = worker_main(task_id, "w-second", cfg=cfg, store=store, dispatcher=tools, base_dir=tmp_path,
                          turn_fn=second, raise_on_error=True)
    assert outcome.status == "done", outcome
    ctx_steps = second.contexts[0]["steps"]
    assert any(s["kind"] == "tool_call" for s in ctx_steps), "the resumed worker must see prior checkpoints"
    assert len(store.list_steps(task_id)) > steps_before
    assert tools.effects.count("write:a.txt") == 1, "a completed tool call was repeated"
    assert tools.effects.count("write:b.txt") == 1, "a completed tool call was repeated"
    assert tools.count("write:c.txt") == 1
    assert store.get_task(task_id).status == "done"
    assert store.get_goal(goal_id).status == "done"
    # Sequence numbers continue, they do not restart.
    seqs = [s.seq for s in store.list_steps(task_id)]
    assert seqs == list(range(1, len(seqs) + 1))


def test_m4_retry_exhaustion_fails_the_task(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a"}])
    store.conn.execute("UPDATE tasks SET max_attempts=1 WHERE id=?", (task_id,))
    claim(store, cfg, "w-dead")
    assert store.requeue_stale(now_ms() + 10 * cfg.lease_ms) == [task_id]
    row = store.get_task(task_id)
    assert row.status == "failed" and (row.result or {})["reason"] == "lease_expired"
    assert store.events_of_kind(task_id, "failed") == 1


def test_m4_stale_progress_requeues_a_hung_worker(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a"}])
    task = claim(store, cfg, "w-hung")
    store.transition(task.id, "claimed", "running")
    store.checkpoint(task.id, "llm_call", {}, {"text": "thinking"})
    assert store.reap_stale_progress(now_ms() + 10) == [task_id]
    row = store.get_task(task_id)
    assert row.status == "queued" and row.attempt_count == 1
    assert store.events_of_kind(task_id, "heartbeat_lost") == 1


def test_heartbeat_lost_is_detected(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a"}])
    task = claim(store, cfg, "w-beat")
    assert store.heartbeat(task.id, "w-beat", cfg.lease_ms) is True
    assert store.heartbeat(task.id, "someone-else", cfg.lease_ms) is False
    store.transition(task.id, "claimed", "retrying")
    assert store.heartbeat(task.id, "w-beat", cfg.lease_ms) is False


# ---------------------------------------------------------------------------------------------
# M5: watcher + events
# ---------------------------------------------------------------------------------------------
def test_m5_done_produces_exactly_one_event_read_and_consumed(
    store: Store, cfg: RuntimeConfig, tmp_path: Path
) -> None:
    tools = FakeTools(tmp_path)
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a.txt"}])
    claim(store, cfg)
    worker_main(task_id, "w-m5", cfg=cfg, store=store, dispatcher=tools, base_dir=tmp_path,
                turn_fn=ScriptedTurn.writes(("write_file", {"path": "a.txt"})), raise_on_error=True)

    assert store.events_of_kind(task_id, "done") == 1, "a done task must produce exactly one done event"
    pending = store.read_unconsumed_events()
    assert [e.kind for e in pending] == ["done"]
    assert pending[0].goal_id is not None

    stats = watcher_loop(cfg, store, once=True, notify=None, sleep_fn=lambda s: None)
    # the default sink is next to the db (tmp_path); asserted through an explicit sink below
    seen: list[dict[str, Any]] = []
    store2 = Store(store.path)
    try:
        # mark the one above consumed, then produce another event to watch with an explicit sink
        store2.mark_events_consumed([pending[0].id])
        store2.append_event(task_id, pending[0].goal_id, "blocked", {"reason": "test"})
        stats2 = watcher_loop(cfg, store2, once=True, notify=lambda e: seen.append(e.to_dict()), sleep_fn=lambda s: None)
    finally:
        store2.close()
    assert stats.consumed == 1
    assert stats2.consumed == 1 and stats2.seen == 1
    assert [e["kind"] for e in seen] == ["blocked"]
    assert seen[0]["payload"] == {"reason": "test"}
    assert store.count_unconsumed_events() == 0

    # A second drain finds nothing: the outbox is not re-delivered.
    again = watcher_loop(cfg, store, once=True, notify=lambda e: None, sleep_fn=lambda s: None)
    assert again.seen == 0 and again.consumed == 0


def test_m5_watcher_never_mutates_tasks(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a"}])
    task = claim(store, cfg, "w-quiet")
    before = store.get_task(task_id)
    store.append_event(task_id, task.goal_id, "heartbeat_lost", {})
    log = tmp_path / "notify.jsonl"
    from auton.watcher import log_sink

    stats = watcher_loop(cfg, store, once=True, notify=log_sink(log), sleep_fn=lambda s: None)
    after = store.get_task(task_id)
    assert stats.consumed == 1
    assert (after.status, after.updated_at, after.worker_id) == (before.status, before.updated_at, before.worker_id)
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["kind"] == "heartbeat_lost"


def test_m5_notify_command_receives_the_event(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    out = tmp_path / "notified.json"
    script = tmp_path / "notify.py"
    script.write_text(
        "import sys\n"
        f"with open(r'{out}', 'w', encoding='utf-8') as fh:\n"
        "    fh.write(sys.stdin.read())\n",
        encoding="utf-8",
    )
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a"}])
    task = store.get_task(task_id)
    store.append_event(task_id, task.goal_id, "failed", {"reason": "unit"})
    cfg.notify_cmd = f'"{sys.executable}" "{script}"'
    watcher_loop(cfg, store, once=True, sleep_fn=lambda s: None)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["kind"] == "failed" and payload["payload"]["reason"] == "unit"


# ---------------------------------------------------------------------------------------------
# M7 / tools bridge: idempotency
# ---------------------------------------------------------------------------------------------
def test_idempotency_key_is_stable_and_collision_resistant() -> None:
    a = idempotency_key("task", 1, "write_file", {"path": "a", "content": "x"})
    b = idempotency_key("task", 1, "write_file", {"content": "x", "path": "a"})
    assert a == b and len(a) == 64
    assert a != idempotency_key("task", 2, "write_file", {"path": "a", "content": "x"})
    assert a != idempotency_key("task", 1, "other", {"path": "a", "content": "x"})
    assert idempotency_key("a", 1, "bc", {}) != idempotency_key("ab", 1, "c", {})


def test_tool_call_replay_executes_once(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    tools = FakeTools(tmp_path)
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a.txt"}])
    call = {"name": "write_file", "arguments": {"path": "a.txt", "content": "once"}}
    first = execute_tool_idempotent(call, store, task_id, 1, dispatcher=tools)
    second = execute_tool_idempotent(call, store, task_id, 1, dispatcher=tools)
    assert first["ok"] is True and "replayed" not in first
    assert second["replayed"] is True and second["idempotency_key"] == first["idempotency_key"]
    assert tools.effects.count("write:a.txt") == 1, "the replay re-executed the side effect"
    assert store.count_steps(task_id, "tool_call") == 1
    # Replaying the whole log from scratch does not duplicate anything (M7 acceptance).
    for _ in range(3):
        execute_tool_idempotent(call, store, task_id, 1, dispatcher=tools)
    assert tools.effects.count("write:a.txt") == 1


def test_tool_exception_becomes_a_result_not_a_crash(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    tools = FakeTools(tmp_path)
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a.txt"}])
    out = execute_tool_idempotent({"name": "boom", "arguments": {}}, store, task_id, 1, dispatcher=tools)
    assert out["ok"] is False and out["error"] == "tool_exception"
    assert "exploded on purpose" in out["detail"]


def test_bridge_degrades_when_the_kit_is_not_importable(store: Store, cfg: RuntimeConfig,
                                                        tmp_path: Path, monkeypatch) -> None:
    import auton.tools_bridge as bridge

    monkeypatch.setattr(bridge, "resolve_kit_dispatch", lambda: None)
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a.txt"}])
    out = bridge.execute_tool_idempotent({"name": "write_file", "arguments": {"path": "a"}}, store, task_id, 1)
    assert out["ok"] is False and out["error"] == "dispatch_unavailable"
    # And the named failure is checkpointed, so a replay is stable rather than silently different.
    stored = store.get_step(task_id, 1)
    assert stored is not None and stored.output["error"] == "dispatch_unavailable"


def test_kit_dispatch_is_the_real_entry_point() -> None:
    """The bridge's production path is `from tools import dispatch` - it must exist in the kit."""
    source = (KIT / "src" / "tools.py").read_text(encoding="utf-8")
    assert "\ndef dispatch(" in source
    chat = (KIT / "src" / "chat_loop.py").read_text(encoding="utf-8")
    assert "from tools import" in chat and "dispatch" in chat


def test_real_kit_dispatch_executes_a_worker_tool_call(store: Store, cfg: RuntimeConfig,
                                                       tmp_path: Path) -> None:
    """End to end through the console's own tool door, not a fake.

    The worker is given no `dispatcher`, so `tools_bridge` resolves `from tools import dispatch`
    exactly as it does in production, and the judge checks the artifact on disk.
    """
    try:
        kit_tools = importlib.import_module("tools")  # the console's own tool layer
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"kit tools not importable in this environment: {type(exc).__name__}: {exc}")
    assert callable(kit_tools.dispatch)

    name = f"auton-e2e-{time.time_ns()}.txt"
    target = KIT / "workspace" / "auton" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        _, task_id = make_goal(store, cfg, KIT, [{"type": "file_exists", "path": str(target)}])
        claim(store, cfg, "w-real")
        outcome = worker_main(
            task_id, "w-real", cfg=cfg, store=store, dispatcher=None, base_dir=KIT,
            turn_fn=ScriptedTurn.writes(("write_file", {"path": str(target), "content": "real dispatch"})),
            raise_on_error=True,
        )
        assert outcome.status == "done", outcome
        assert target.is_file() and target.read_text(encoding="utf-8") == "real dispatch"
        tool_steps = [s for s in store.list_steps(task_id) if s.kind == "tool_call"]
        assert len(tool_steps) == 1 and tool_steps[0].output["ok"] is True
        assert tool_steps[0].output["idempotency_key"]
    finally:
        target.unlink(missing_ok=True)


# ---------------------------------------------------------------------------------------------
# Dispatcher (§5.3): reap, spawn up to the limit, never blocks on the model
# ---------------------------------------------------------------------------------------------
class FakeProc:
    def __init__(self, task_id: str, worker_id: str) -> None:
        self.task_id = task_id
        self.worker_id = worker_id
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        return None

    def terminate(self) -> None:
        self.alive = False


def test_dispatcher_claims_and_respects_the_concurrency_limit(store: Store, cfg: RuntimeConfig,
                                                              tmp_path: Path) -> None:
    for i in range(3):
        make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": f"{i}.txt"}], text=f"g{i}")
    spawned: list[FakeProc] = []

    def fake_spawn(c: RuntimeConfig, task_id: str, worker_id: str) -> FakeProc:
        proc = FakeProc(task_id, worker_id)
        spawned.append(proc)
        return proc

    cfg.max_concurrent_workers = 1
    stats = dispatcher_loop(cfg, store, spawn_fn=fake_spawn, max_iterations=1, sleep_fn=lambda s: None)
    assert stats.claims == 1 and stats.spawned == 1 and len(spawned) == 1
    assert store.count_tasks("claimed") == 1 and store.count_tasks("queued") == 2

    # The worker exits; the next poll spawns the next task, still one at a time.
    spawned[0].alive = False
    stats2 = dispatcher_loop(cfg, store, spawn_fn=fake_spawn, max_iterations=1, sleep_fn=lambda s: None)
    assert stats2.claims == 1 and stats2.finished == 0  # fresh loop keeps no memory of the old proc
    assert store.count_tasks("claimed") == 2


def test_dispatcher_reaps_expired_leases_before_spawning(store: Store, cfg: RuntimeConfig,
                                                         tmp_path: Path) -> None:
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a"}])
    task = store.claim_task("w-orphan", 1)
    assert task is not None
    time.sleep(0.02)  # let the 1 ms lease fall behind now_ms()
    spawned: list[str] = []
    stats = dispatcher_loop(
        cfg, store,
        spawn_fn=lambda c, tid, wid: (spawned.append(tid), FakeProc(tid, wid))[1],
        max_iterations=1, sleep_fn=lambda s: None,
    )
    assert stats.reaped == [task_id]
    assert spawned and spawned[0] == task_id
    assert store.get_task(task_id).attempt_count == 1


def test_spawned_worker_process_writes_state_across_the_process_boundary(store: Store, cfg: RuntimeConfig,
                                                                        tmp_path: Path) -> None:
    """§5.3 isolation for real: a `spawn` child imports the package and owns the task's lease.

    The child has no turn adapter configured, so it fails the task immediately - which is exactly
    the point: the write crosses the process boundary and the parent sees a terminal, explained
    state instead of a hung task.
    """
    from auton.dispatcher import spawn_worker

    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a"}])
    claim(store, cfg, "w-spawn")
    proc = spawn_worker(cfg, task_id, "w-spawn")
    proc.join(timeout=90)
    assert proc.exitcode == 0
    row = store.get_task(task_id)
    assert row is not None and row.status == "failed"
    assert (row.result or {})["reason"] == "no_turn_adapter"
    assert store.events_of_kind(task_id, "failed") == 1


# ---------------------------------------------------------------------------------------------
# Property-style: any sequence of verdicts reaches a terminal state inside the budget
# ---------------------------------------------------------------------------------------------
def test_any_sequence_of_verdicts_terminates_within_budget(store: Store, cfg: RuntimeConfig,
                                                           tmp_path: Path, monkeypatch) -> None:
    """Monkeypatch the Judge to say `continue` forever (with a changing fingerprint).

    The only thing that can stop the worker is the budget - and it must, in `blocked`, before the
    turn counter passes max_turns.
    """
    import auton.worker as worker_mod

    counter = {"n": 0}

    def always_continue(contract, task, store_, cfg_, **kw):
        counter["n"] += 1
        return Verdict("continue", {"checks": []}, f"still failing ({counter['n']})",
                       "CONTINUE. The goal is not yet complete.", f"fp-{counter['n']}")

    monkeypatch.setattr(worker_mod.judge_mod, "evaluate", always_continue)
    tools = FakeTools(tmp_path)
    budget = dict(cfg.budgets.as_task_budget())
    budget["max_turns"] = 4
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "never"}], budget=budget)
    claim(store, cfg)
    script = ScriptedTurn([{"text": "busy", "tool_calls": [{"name": "append", "arguments": {"text": "x"}}]}
                           for _ in range(20)])
    outcome = worker_main(task_id, "w-prop", cfg=cfg, store=store, dispatcher=tools, base_dir=tmp_path,
                          turn_fn=script, raise_on_error=True)
    assert outcome.status == "blocked"
    assert (store.get_task(task_id).result or {})["reason"] == "turn_budget_exhausted"
    assert outcome.turns == 4 and counter["n"] == 4


@pytest.mark.parametrize("seed", range(8))
def test_random_verdict_streams_always_reach_terminal(seed: int, tmp_path: Path) -> None:
    """For any sequence of judge outcomes, the task ends terminal, inside its budget."""
    rng = random.Random(seed)
    db = tmp_path / f"prop-{seed}.db"
    store = Store(db)
    try:
        cfg = RuntimeConfig.from_dict({})
        cfg.budgets = Budgets(default_max_turns=6, default_max_attempts=2, default_max_wall_s=60,
                              default_max_tokens=100_000)
        cfg.no_progress_window = 3
        tools = FakeTools(tmp_path)
        budget = dict(cfg.budgets.as_task_budget())
        budget["max_turns"] = 6
        _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "target"}],
                               budget=budget)
        store.claim_task("w-prop", cfg.lease_ms)

        # The model satisfies the contract at a random turn, if ever.
        done_at = rng.choice([1, 2, 3, 4, 5, 6, 99])
        turns = []
        for i in range(1, 9):
            if i >= done_at:
                turns.append({"text": "writing", "tool_calls": [
                    {"name": "write_file", "arguments": {"path": "target"}}]})
            elif rng.random() < 0.5:
                turns.append({"text": "prose only"})
            else:
                turns.append({"text": "busy", "tool_calls": [
                    {"name": "append", "arguments": {"text": f"noise {i}"}}]})
        outcome = worker_main(task_id, "w-prop", cfg=cfg, store=store, dispatcher=tools,
                              base_dir=tmp_path, turn_fn=ScriptedTurn(turns), raise_on_error=True)
        final = store.get_task(task_id)
        assert final.status in TERMINAL_STATES, f"seed {seed}: {final.status}"
        goal = store.get_goal(final.goal_id)
        assert goal.used_turns <= 6, f"seed {seed}: spent {goal.used_turns} turns"
        assert outcome.turns <= 6
        # Every terminal state came with an event, and only one.
        kinds = [e.kind for e in store.list_events(task_id=task_id)]
        assert len(kinds) == 1, f"seed {seed}: events {kinds}"
    finally:
        store.close()


def test_goal_status_rolls_up_from_tasks(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    tools = FakeTools(tmp_path)
    ok_goal, ok_task = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a"}])
    bad_goal, bad_task = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "b"}])

    store.claim_task("w1", cfg.lease_ms)
    worker_main(ok_task, "w1", cfg=cfg, store=store, dispatcher=tools, base_dir=tmp_path,
                turn_fn=ScriptedTurn.writes(("write_file", {"path": "a"})), raise_on_error=True)
    assert store.get_goal(ok_goal).status == "done"

    store.claim_task("w2", cfg.lease_ms)
    worker_main(bad_task, "w2", cfg=cfg, store=store, dispatcher=tools, base_dir=tmp_path,
                turn_fn=ScriptedTurn.prose(*["thinking"] * 4), raise_on_error=True)
    assert store.get_goal(bad_goal).status == "blocked"


def test_archive_keeps_failures_debuggable(store: Store, cfg: RuntimeConfig, tmp_path: Path) -> None:
    _, task_id = make_goal(store, cfg, tmp_path, [{"type": "file_exists", "path": "a"}])
    store.transition(task_id, "queued", "cancelled")
    old = now_ms() - 40 * 86_400_000
    store.conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (old, task_id))
    assert store.archive_terminal(older_than_days=30) == 1
    assert store.get_task(task_id) is None
    assert store.count_archived() == 1  # archived, not deleted (rule 10)


# ---------------------------------------------------------------------------------------------
# Turn adapter (§5.4) and config (§9)
# ---------------------------------------------------------------------------------------------
def test_scripted_turn_normalises_and_exhausts() -> None:
    turn = as_turn({"text": "hi", "tool_calls": [{"tool": "write_file", "args": '{"path": "a"}'}], "tokens": "9"})
    assert turn.tokens == 9 and turn.tool_calls[0]["arguments"] == {"path": "a"}
    assert as_turn("plain prose").is_noop
    with pytest.raises(Exception):
        as_turn({"tool_calls": [{"arguments": {}}]})
    script = ScriptedTurn([{"text": "one"}])
    script({})
    with pytest.raises(ScriptedTurnExhausted):
        script({})


def test_runtime_config_matches_the_guide_defaults() -> None:
    cfg = RuntimeConfig.from_dict({})
    assert cfg.poll_interval_s == 2
    assert cfg.watcher_interval_s == 5
    assert cfg.lease_ms == 120_000
    assert cfg.heartbeat_interval_ms == 40_000
    assert cfg.max_concurrent_workers == 2
    assert cfg.no_progress_window == 3
    assert cfg.judge.model == "small-fast-model" and cfg.judge.temperature == 0 and cfg.judge.max_retries == 2
    assert cfg.budgets.default_max_turns == 30
    assert cfg.budgets.default_max_attempts == 3
    assert cfg.budgets.default_max_wall_s == 3600
    assert cfg.budgets.default_max_tokens == 200000
    assert cfg.heartbeat_ms() == 40_000
    # The §9 YAML shape, as JSON, round-trips.
    payload = {
        "runtime": {
            "store_dsn": "sqlite:///C:/tmp/x.db",
            "poll_interval_s": 1,
            "max_concurrent_workers": 4,
            "judge": {"model": "tiny", "temperature": 0, "max_retries": 1},
            "budgets": {"default_max_turns": 3},
        }
    }
    cfg2 = RuntimeConfig.from_dict(payload)
    assert cfg2.poll_interval_s == 1 and cfg2.max_concurrent_workers == 4
    assert cfg2.judge.model == "tiny" and cfg2.budgets.default_max_turns == 3
    assert cfg2.budgets.default_max_wall_s == 3600
    with pytest.raises(ValueError):
        RuntimeConfig.from_dict({"runtime": {"nonsense": 1}})


def test_config_loads_from_a_file(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"runtime": {"lease_ms": 1000, "poll_interval_s": 0.1}}), encoding="utf-8")
    cfg = RuntimeConfig.load(path)
    assert cfg.lease_ms == 1000 and cfg.poll_interval_s == 0.1
    with pytest.raises(FileNotFoundError):
        RuntimeConfig.load(tmp_path / "absent.json")
