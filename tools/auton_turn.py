#!/usr/bin/env python3
"""auton_turn.py - one model turn for the autonomous runtime, produced by this kit's own model.

This is the production `turn_cmd` for `tools/auton.py`: the dispatcher-spawned worker cannot inherit
a Python callable, so it shells out here with the runtime's context on stdin and reads one turn back
on stdout, in the shape `auton.model.Turn` accepts:

    stdin   {"goal": {...}, "task": {...}, "steps": [...], "continuation": "...", "control_tools": [...]}
    stdout  {"text": "...", "tool_calls": [{"name": "write_file", "arguments": {...}}], "tokens": 123}

Three rules are enforced here rather than hoped for:

1.  A turn that only talks is a no-op turn. The prompt says so, and the runtime counts it against
    the budget, so the model is told plainly: either call a tool or call task.block.
2.  Completion is never the model's to declare. `task.complete` / `task.done` are not offered, and
    if a model emits one anyway it is stripped before the worker sees it.
3.  Every tool name is checked against the kit's own advertised schema, so a hallucinated limb is
    answered with the list of limbs that exist instead of a mysterious dispatch failure.

The model is called through `cloud_api.chat`, the kit's own key-chain walker - no new key, no new
provider, and the same failure text the console already shows when the chain is down.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "src"))

#: The limbs a worker may reach by default. Kept small on purpose: the full 93-entry schema costs
#: ~10k tokens per turn, and a budget that spends a third of itself on tool prose cannot afford 30
#: turns. `--tools a,b,c` overrides it; `--all-tools` sends everything.
DEFAULT_TOOLS = (
    "read_file", "write_file", "edit_file", "list_dir", "find_files", "glob_files",
    "shell", "python_exec", "web_search", "web_fetch", "search_corpus", "calc",
    "music_video",
    "image_generate", "video_generate", "music_generate",
)

FORBIDDEN = {"task.complete", "task.done", "task.finish", "goal.complete"}

SYSTEM = """You are the worker for a durable goal inside the LYGO console. You do not chat: you take
the single next action that moves a failing check toward passing, then stop.

HOW YOU ARE JUDGED
The runtime checks a machine-readable completion contract against the filesystem, commands and tool
outputs after every turn. Your words are never evidence. Never say the goal is done - if every check
passes, the Judge ends the task by itself.

THE TURN
Call at least one tool. A turn with prose only is recorded as a no-op, still spends a turn of the
budget, and two of them in a row stop the task as blocked.

If a required input is genuinely missing, call task.block with a short reason instead of guessing.
Use task.note to record one line of progress that a human could act on. goal.status reads the
contract and the remaining budget.

WHEN CONTINUING
The continuation block names the checks that are still failing, with the evidence observed. Do not
restate the plan. Do the next concrete thing: write the file, run the command, fetch the page.

STYLE
Absolute paths on this machine. Report what a tool actually returned, not what you expected. Keep
each turn small enough that a failure is easy to place."""


def tool_schema(allow: tuple[str, ...] | None, everything: bool) -> tuple[list[dict[str, Any]], list[str]]:
    """The OpenAI tool schema this worker offers, plus the names it advertises in prose."""
    import tools

    want = None if everything else set(allow or DEFAULT_TOOLS)
    picked: list[dict[str, Any]] = []
    known: list[str] = []
    for entry in tools.TOOLS_SCHEMA:
        fn = entry.get("function") or {}
        name = str(fn.get("name") or "")
        if not name:
            continue
        known.append(name)
        if want is None or name in want:
            picked.append(entry)
    return picked, known


def messages(context: dict[str, Any], known: list[str]) -> list[dict[str, Any]]:
    goal = context.get("goal") or {}
    task = context.get("task") or {}
    steps = context.get("steps") or []
    continuation = context.get("continuation") or ""

    brief = {
        "objective": goal.get("text"),
        "contract": goal.get("contract"),
        "turn_budget": {"max_turns": (goal.get("budget") or {}).get("max_turns"),
                        "used_turns": goal.get("used_turns")},
        "recent_steps": steps[-6:],
    }
    body = [
        "GOAL",
        json.dumps(brief, indent=1, ensure_ascii=False),
        "",
        "WORKING DIRECTORY: the base directory named by the task payload, or the kit root.",
        "TOOLS AVAILABLE BY NAME (schemas for the ones you may call are attached):",
        ", ".join(known),
    ]
    if continuation:
        body += ["", continuation.strip()]
    else:
        body += ["", "This is the first turn. Take one concrete action toward the contract."]
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "\n".join(body)},
    ]


def normalise(raw: dict[str, Any]) -> dict[str, Any]:
    message = ((raw.get("choices") or [{}])[0] or {}).get("message") or {}
    text = str(message.get("content") or "")
    calls: list[dict[str, Any]] = []
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        name = str(fn.get("name") or "")
        if not name or name in FORBIDDEN:
            continue
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except ValueError:
                args = {"_raw": args}
        if not isinstance(args, dict):
            args = {}
        calls.append({"name": name, "arguments": args})
    usage = raw.get("usage") or {}
    return {"text": text, "tool_calls": calls, "tokens": int(usage.get("total_tokens") or 0)}


def _chat_tools(payload: dict, timeout: float = 180.0):
    """Walk the key chain, skipping text-only bridges.

    MEASURED 2026-10-17: the console's primary brain can be a CLI shim (provider `custom`, the
    free-brain bridge). It answers in prose, ignores `tools` entirely and reports 0 usage - so a
    worker that lands on it emits prose-only turns and the runtime correctly blocks the goal after
    three of them, with the work never attempted. Tool work has to go to a provider that speaks the
    OpenAI tool-call shape. LYGO_AUTON_ALLOW_BRIDGE=1 puts the shim back in the chain for someone who
    wants a text-only reviewer.
    """
    import os

    import cloud_api

    allow_bridge = str(os.environ.get("LYGO_AUTON_ALLOW_BRIDGE") or "").strip() == "1"
    last = (401, b'{"error":"no_api_key"}', "application/json")
    tried: list[str] = []
    for cand in cloud_api.chain_for(cloud_api._load()):
        provider = str(cand.get("provider") or "")
        if provider == "custom" and not allow_bridge:
            tried.append(f"{provider}:skipped-bridge")
            continue
        body = dict(payload)
        body.update(cand.get("body") or {})
        body["model"] = cand.get("model") or payload.get("model")
        body["stream"] = False
        code, data, ctype = cloud_api._post(
            str(cand.get("url") or ""), str(cand.get("key") or ""), body,
            cand.get("extra") or {}, timeout,
        )
        tried.append(f"{provider}:{code}")
        last = (code, data, ctype)
        if code < 400:
            return code, data, ctype
    print(f"auton_turn: no tool-capable provider answered; chain tried {tried}", file=sys.stderr)
    return last


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    allow: tuple[str, ...] | None = None
    everything = "--all-tools" in argv
    if "--tools" in argv:
        i = argv.index("--tools")
        allow = tuple(x.strip() for x in (argv[i + 1] if i + 1 < len(argv) else "").split(",") if x.strip())

    try:
        context = json.loads(sys.stdin.read() or "{}")
    except ValueError as exc:
        print(f"auton_turn: stdin is not JSON: {exc}", file=sys.stderr)
        return 2

    schema, known = tool_schema(allow, everything)
    payload: dict[str, Any] = {
        "messages": messages(context, known),
        "temperature": 0.2,
        "max_tokens": 1200,
    }
    if schema:
        payload["tools"] = schema
        payload["tool_choice"] = "auto"

    import cloud_api

    code, raw, _ctype = _chat_tools(payload, timeout=180.0)
    if code >= 400:
        detail = (raw or b"").decode("utf-8", "replace")[:600]
        print(f"auton_turn: the model chain answered HTTP {code}: {detail}", file=sys.stderr)
        return 3
    try:
        reply = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        print(f"auton_turn: the model returned non-JSON: {exc}", file=sys.stderr)
        return 4

    # A hallucinated limb is answered with the truth instead of a dispatch failure.
    turn = normalise(reply)
    keep = [c for c in turn["tool_calls"] if c["name"] in known or c["name"].startswith("task.") or c["name"].startswith("goal.")]
    if len(keep) != len(turn["tool_calls"]):
        missing = sorted({c["name"] for c in turn["tool_calls"]} - {c["name"] for c in keep})
        turn["text"] = (turn["text"] + f"\n[no such tool: {', '.join(missing)}]").strip()
        turn["tool_calls"] = keep
    print(json.dumps(turn, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
