#!/usr/bin/env python
"""Command line for the autonomous task runtime (`auton`).

This is the operator door: create goals, run a dispatcher tick, read the outbox, inspect state, and
run the offline demo that proves the continuation loop works without a model or a network.

    python tools/auton.py goal add --text "write A, then B, then C" --checks '[{"type":"file_exists","path":"a.txt"}]'
    python tools/auton.py dispatch --seconds 30
    python tools/auton.py watch --once
    python tools/auton.py status
    python tools/auton.py demo --db "%LOCALAPPDATA%/Temp/auton_demo.db"

Additive by design: this file is new, it imports only `src/auton/*`, and it never touches the
console's own config, models or ComfyUI. The store defaults to `workspace/auton/runtime.db`
(gitignored) and `--db` overrides it everywhere.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

KIT = Path(__file__).resolve().parents[1]
if str(KIT / "src") not in sys.path:
    sys.path.insert(0, str(KIT / "src"))

from auton import contract as contract_mod  # noqa: E402
from auton.config import RuntimeConfig  # noqa: E402
from auton.dispatcher import dispatcher_loop  # noqa: E402
from auton.model import ScriptedTurn  # noqa: E402
from auton.store import Store  # noqa: E402
from auton.watcher import watcher_loop  # noqa: E402
from auton.worker import worker_main  # noqa: E402

DEFAULT_DB = KIT / "workspace" / "auton" / "runtime.db"


# ---------------------------------------------------------------------------------------------
def build_config(args: argparse.Namespace) -> RuntimeConfig:
    cfg = RuntimeConfig.load(getattr(args, "config", None))
    db = getattr(args, "db", None) or DEFAULT_DB
    return cfg.with_db(db)


def open_store(cfg: RuntimeConfig) -> Store:
    return Store(cfg.sqlite_path)


def _print_goal(goal) -> None:
    print(f"{goal.id}  {goal.status:<9} turns {goal.used_turns:<3} {goal.text[:70]}")


# ---------------------------------------------------------------------------------------------
# goal
# ---------------------------------------------------------------------------------------------
def cmd_goal_add(args: argparse.Namespace, cfg: RuntimeConfig, store: Store) -> int:
    if args.contract:
        raw = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    elif args.checks:
        raw = {"objective": args.text, "checks": json.loads(args.checks)}
    else:
        raw = contract_mod.fallback_contract(args.text, "no --contract or --checks supplied")
    errors = contract_mod.validate_contract(raw)
    if errors:
        print("contract is not runnable:")
        for err in errors:
            print("  -", err)
        return 2
    budget = cfg.budgets.as_task_budget()
    if args.max_turns:
        budget["max_turns"] = int(args.max_turns)
    if args.max_attempts:
        budget["max_attempts"] = int(args.max_attempts)
    goal_id, task_id = contract_mod.create_goal(
        store,
        args.text,
        raw,
        budget=budget,
        payload={"cwd": str(Path(args.cwd).resolve())} if args.cwd else {},
        defaults=cfg.budgets.as_task_budget(),
    )
    print(f"goal {goal_id}")
    print(f"task {task_id}")
    print(f"checks {len(raw['checks'])}  requires_human_review={bool(raw.get('requires_human_review'))}")
    return 0


def cmd_goal_list(args: argparse.Namespace, cfg: RuntimeConfig, store: Store) -> int:
    goals = store.list_goals(status=args.status)
    if not goals:
        print("(no goals)")
        return 0
    for goal in goals:
        _print_goal(goal)
    print(f"\n{len(goals)} goal(s) in {store.path}")
    return 0


def cmd_goal_show(args: argparse.Namespace, cfg: RuntimeConfig, store: Store) -> int:
    goal = store.get_goal(args.goal_id)
    if goal is None:
        print(f"no such goal: {args.goal_id}")
        return 2
    print(f"goal      {goal.id}")
    print(f"status    {goal.status}")
    print(f"text      {goal.text}")
    print(f"budget    {json.dumps(goal.budget, sort_keys=True)}")
    print(f"used      turns={goal.used_turns} tokens={goal.used_tokens}")
    print("contract  " + json.dumps(goal.contract, indent=2, sort_keys=True).replace("\n", "\n          "))
    for task in store.list_tasks(goal_id=goal.id):
        print(f"\ntask      {task.id}  {task.status}  attempt {task.attempt_count}/{task.max_attempts}")
        if task.result:
            print(f"result    {json.dumps(task.result, sort_keys=True)[:800]}")
        for step in store.list_steps(task.id):
            out = step.output if isinstance(step.output, Mapping) else {"output": step.output}
            if step.kind == "llm_call":
                calls = out.get("tool_calls") or []
                label = "tools=" + ",".join(c.get("name", "?") for c in calls) if calls else "prose-only"
            elif step.kind == "judgment":
                label = f"verdict={out.get('status')} ({out.get('reason')})"
            elif step.kind == "tool_call":
                label = f"ok={out.get('ok')} {str(out.get('path') or out.get('note') or '')[:60]}"
            else:
                label = str(out)[:120]
            print(f"  seq {step.seq:<3} {step.kind:<10} {label}")
    return 0


def cmd_goal_cancel(args: argparse.Namespace, cfg: RuntimeConfig, store: Store) -> int:
    goal = store.get_goal(args.goal_id)
    if goal is None:
        print(f"no such goal: {args.goal_id}")
        return 2
    cancelled = 0
    for task in store.list_tasks(goal_id=goal.id):
        if task.status in {"done", "failed", "blocked", "cancelled"}:
            continue
        if store.transition(task.id, task.status, "cancelled", result={"reason": "user_cancelled"}):
            cancelled += 1
    store.set_goal_status(goal.id, "cancelled")
    print(f"goal {goal.id} cancelled ({cancelled} task(s))")
    return 0


# ---------------------------------------------------------------------------------------------
# dispatch / watch / status
# ---------------------------------------------------------------------------------------------
def cmd_dispatch(args: argparse.Namespace, cfg: RuntimeConfig, store: Store) -> int:
    if args.max_workers:
        cfg.max_concurrent_workers = int(args.max_workers)
    if not cfg.turn_cmd:
        print("note: no turn command configured (runtime.turn_cmd); spawned workers will fail with")
        print("      no_turn_adapter until one is set in the config file.")
    stats = dispatcher_loop(cfg, store, duration_s=args.seconds, max_iterations=args.max_iterations)
    print(json.dumps(stats.to_dict(), indent=2, sort_keys=True))
    return 0


def cmd_watch(args: argparse.Namespace, cfg: RuntimeConfig, store: Store) -> int:
    if args.notify_cmd:
        cfg.notify_cmd = args.notify_cmd
    stats = watcher_loop(cfg, store, once=args.once, duration_s=args.seconds)
    print(json.dumps(stats.to_dict(), indent=2, sort_keys=True))
    return 0


def cmd_status(args: argparse.Namespace, cfg: RuntimeConfig, store: Store) -> int:
    print(f"store          {store.path}")
    print(f"goals          {len(store.list_goals(limit=10000))}")
    print(f"tasks          {store.count_tasks()}")
    for status in ("queued", "claimed", "running", "retrying", "blocked", "done", "failed", "cancelled"):
        n = store.count_tasks(status)
        if n:
            print(f"  {status:<10} {n}")
    print(f"unconsumed     {store.count_unconsumed_events()} event(s)")
    print(f"archived       {store.count_archived()} task(s)")
    events = store.list_events(limit=10)
    if events:
        print("\nrecent events")
        for event in events:
            state = "read" if event.consumed_at else "unread"
            print(f"  #{event.id:<4} {event.kind:<14} {state:<6} task={event.task_id} {json.dumps(event.payload)[:70]}")
    return 0


# ---------------------------------------------------------------------------------------------
# demo: the M3 acceptance proof, fully offline
# ---------------------------------------------------------------------------------------------
def demo_tools(root: Path) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    """A tiny, safe tool layer for the demo. Writes only inside `root`, never touches the network."""
    root = root.resolve()

    def resolve(raw: str) -> Path:
        p = (root / raw).resolve()
        if root not in p.parents and p != root:
            raise ValueError(f"path escapes the demo directory: {raw}")
        return p

    def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "write_file":
            p = resolve(str(args.get("path") or ""))
            p.parent.mkdir(parents=True, exist_ok=True)
            text = str(args.get("content") or "")
            p.write_text(text, encoding="utf-8")
            return {"ok": True, "path": str(p), "bytes": len(text.encode('utf-8'))}
        if name == "append_note":
            p = resolve("notes.log")
            with p.open("a", encoding="utf-8") as fh:
                fh.write(str(args.get("note") or "") + "\n")
            return {"ok": True, "path": str(p), "note": args.get("note")}
        return {"ok": False, "error": f"unknown_tool:{name}"}

    return dispatch


def cmd_demo(args: argparse.Namespace, cfg: RuntimeConfig, store: Store) -> int:
    """One self-contained run that proves M3 (and M4's requeue) with no network and no model."""
    root = Path(args.workdir).resolve() if args.workdir else Path(cfg.sqlite_path).parent / "demo"
    if root.exists() and args.clean:
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    # The demo must be repeatable without --clean: its own artifacts are disposable, nothing else is.
    for name in ("a.txt", "b.txt", "c.txt", "report.md", "notes.log"):
        (root / name).unlink(missing_ok=True)
    dispatch = demo_tools(root)

    print("auton demo: offline, scripted turns, no network")
    print(f"store    {store.path}")
    print(f"workdir  {root}\n")

    # ---- goal 1: write A, then B, then C -> done across three turns -------------------------
    checks = [{"type": "file_exists", "path": p} for p in ("a.txt", "b.txt", "c.txt")]
    contract = {"objective": "Create a.txt, then b.txt, then c.txt", "checks": checks,
                "requires_human_review": False}
    budget = dict(cfg.budgets.as_task_budget())
    goal1, task1 = contract_mod.create_goal(store, "Write A, then B, then C", contract, budget=budget,
                                            payload={"cwd": str(root)})
    print(f"[1] goal {goal1} (3-step): checks A, B, C exist")
    task = store.claim_task("demo-worker-1", cfg.lease_ms)
    assert task is not None and task.id == task1, "demo claim did not return the queued task"
    script = ScriptedTurn(
        [
            {"text": "Writing A now.", "tool_calls": [
                {"name": "write_file", "arguments": {"path": "a.txt", "content": "step A\n"}}], "tokens": 12},
            {"text": "A exists; moving to B.", "tool_calls": [
                {"name": "write_file", "arguments": {"path": "b.txt", "content": "step B\n"}}], "tokens": 12},
            {"text": "B exists; moving to C.", "tool_calls": [
                {"name": "write_file", "arguments": {"path": "c.txt", "content": "step C\n"}}], "tokens": 12},
        ],
        name="demo-3-step",
    )
    outcome1 = worker_main(task1, "demo-worker-1", cfg=cfg, turn_fn=script, store=store,
                           dispatcher=dispatch, base_dir=root, raise_on_error=True)
    goal1_row = store.get_goal(goal1)
    print(f"    model turns used: {script.calls}")
    judgments = [s for s in store.list_steps(task1) if s.kind == "judgment"]
    for i, step in enumerate(judgments, 1):
        verdict = step.output or {}
        print(f"    after turn {i}: judge -> {verdict.get('status')} ({verdict.get('reason')})")
    print(f"    outcome: {outcome1.status}  goal status: {goal1_row.status}")
    for name in ("a.txt", "b.txt", "c.txt"):
        print(f"    artifact {name}: {(root / name).is_file()}")
    ok1 = outcome1.status == "done" and script.calls >= 3 and goal1_row.status == "done"
    print(f"    M3 3-step goal: {'PASS' if ok1 else 'FAIL'} ({script.calls} turns >= 3)\n")

    # ---- goal 2: busy but never satisfies the contract -> blocked/no_progress ----------------
    contract2 = {"objective": "Write report.md that summarises the work",
                 "checks": [{"type": "file_exists", "path": "report.md"}],
                 "requires_human_review": False}
    goal2, task2 = contract_mod.create_goal(store, "Produce report.md", contract2, budget=dict(budget),
                                            payload={"cwd": str(root)})
    print(f"[2] goal {goal2} (no-progress): checks report.md exists")
    task = store.claim_task("demo-worker-2", cfg.lease_ms)
    assert task is not None and task.id == task2
    busy = ScriptedTurn(
        [
            {"text": "I will start the report right after this.", "tokens": 8,
             "tool_calls": [{"name": "append_note", "arguments": {"note": f"planning pass {i}"}}]}
            for i in range(1, 9)
        ],
        name="demo-no-progress",
    )
    outcome2 = worker_main(task2, "demo-worker-2", cfg=cfg, turn_fn=busy, store=store,
                           dispatcher=dispatch, base_dir=root, raise_on_error=True)
    reason = (store.get_task(task2).result or {}).get("reason")
    print(f"    model turns used: {busy.calls}")
    print(f"    outcome: {outcome2.status}  reason: {reason}")
    print(f"    K = {cfg.no_progress_window} identical fingerprints (failing set + progress note)")
    ok2 = outcome2.status == "blocked" and reason == "no_progress"
    print(f"    M3 no-progress goal: {'PASS' if ok2 else 'FAIL'}\n")

    print(f"goal 1 {goal1} -> {store.get_goal(goal1).status}")
    print(f"goal 2 {goal2} -> {store.get_goal(goal2).status} ({(store.get_task(task2).result or {}).get('reason')})")
    print(f"events: {[e.kind for e in store.list_events(limit=10)][::-1]}")
    print("\nBoth goals are in the store; inspect them with:")
    print(f"  python tools/auton.py goal list --db {store.path}")
    return 0 if (ok1 and ok2) else 1


# ---------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="auton", description="LYGO autonomous task runtime")

    def common(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
        """`--db`/`--config` are accepted before *and* after the subcommand.

        SUPPRESS keeps the top-level value when the subcommand does not repeat the flag, so both
        `auton --db X goal list` and `auton goal list --db X` work.
        """
        sp.add_argument("--db", default=argparse.SUPPRESS, help=f"SQLite store path (default: {DEFAULT_DB})")
        sp.add_argument("--config", default=argparse.SUPPRESS, help="optional runtime JSON config")
        return sp

    common(p)
    sub = p.add_subparsers(dest="command", required=True)

    goal = common(sub.add_parser("goal", help="create and inspect goals"))
    gsub = goal.add_subparsers(dest="goal_command", required=True)

    add = common(gsub.add_parser("add", help="create a goal and its first task"))
    add.add_argument("--text", required=True, help="natural-language objective")
    add.add_argument("--contract", help="path to a contract JSON file")
    add.add_argument("--checks", help="JSON array of checks (used with --text)")
    add.add_argument("--cwd", help="base directory relative checks resolve against")
    add.add_argument("--max-turns", type=int)
    add.add_argument("--max-attempts", type=int)
    add.set_defaults(func=cmd_goal_add)

    listing = common(gsub.add_parser("list", help="list goals"))
    listing.add_argument("--status", choices=["active", "done", "blocked", "paused", "cancelled", "failed"])
    listing.set_defaults(func=cmd_goal_list)

    show = common(gsub.add_parser("show", help="show a goal, its tasks and checkpoints"))
    show.add_argument("goal_id")
    show.set_defaults(func=cmd_goal_show)

    cancel = common(gsub.add_parser("cancel", help="cancel a goal and its open tasks"))
    cancel.add_argument("goal_id")
    cancel.set_defaults(func=cmd_goal_cancel)

    d = common(sub.add_parser("dispatch", help="run the dispatcher loop"))
    d.add_argument("--seconds", type=float, default=None, help="stop after N seconds")
    d.add_argument("--max-iterations", type=int, default=None)
    d.add_argument("--max-workers", type=int, default=None)
    d.set_defaults(func=cmd_dispatch)

    w = common(sub.add_parser("watch", help="read the events outbox"))
    w.add_argument("--once", action="store_true", help="drain what is there and exit")
    w.add_argument("--seconds", type=float, default=None)
    w.add_argument("--notify-cmd", help="external notifier; receives the event JSON on stdin")
    w.set_defaults(func=cmd_watch)

    s = common(sub.add_parser("status", help="counts, recent events"))
    s.set_defaults(func=cmd_status)

    demo = common(sub.add_parser("demo", help="offline end-to-end M3 demo (ScriptedTurn, no network)"))
    demo.add_argument("--workdir", help="where the demo writes its artifacts")
    demo.add_argument("--clean", action="store_true", help="delete the workdir first")
    demo.set_defaults(func=cmd_demo)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = build_config(args)
    store = open_store(cfg)
    try:
        return int(args.func(args, cfg, store) or 0)
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
