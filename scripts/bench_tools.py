"""Score a model on THIS console's real tool surface.

The console hands the engine 63 OpenAI-style function schemas at once (server.py sends
tools=TOOLS_SCHEMA), so "can this model run the tools" is a measurable question: ask it 20 operator
requests, one per tool, and count how many calls name the right tool with schema-valid arguments.

Run:  python scripts/bench_tools.py --models gemma4:12b,qwen2.5-coder:7b [--limit N]

Writes save/bench/tools_<model>_<stamp>.json and prints a table. A model is booted with the kit's own
engine.spawn_runner (the same call the console makes) and stopped again, so nothing is left resident.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "src"))

import engine  # noqa: E402
import image_tools  # noqa: E402
import lygo_engine  # noqa: E402
from chat_loop import extract_tool_calls  # noqa: E402  the console's OWN parser
from tools import TOOLS_SCHEMA  # noqa: E402

PHOTO = r"<EDRIVE>\YOUTUBE LYGO VIDEOS\Pictures\cc988550-5734-423d-b972-64adb03897e6.png"

# One request per tool, in the operator's own voice. Unambiguous on purpose: a model that answers
# "what time is it" with calc is not being tested fairly, it is being asked badly.
CASES: list[tuple[str, str]] = [
    ("image_see", "check this photo " + PHOTO),
    ("image_info", "what are the dimensions of this image? " + PHOTO),
    ("steward_map", "what drives do I have on this pc?"),
    ("now", "what time is it right now?"),
    ("calc", "what is 1847 * 293?"),
    ("find_files", "find every *.gguf file on the I: drive"),
    ("list_dir", "list the files in <EDRIVE>\\lygo-protocol-stack"),
    ("read_file", "read the file <DESKTOP>\\notes.txt"),
    ("write_file", "save the text hello steward into a file called plan.txt in the workspace"),
    ("shell", "list the running processes on this pc"),
    ("web_search", "search the web for the latest llama.cpp release"),
    ("web_fetch", "fetch the text of https://example.com"),
    ("weather", "what is the weather in Denver right now?"),
    ("geocode", "what are the coordinates of Boulder, Colorado?"),
    ("github_search", "search github for lygo protocol"),
    ("arxiv_search", "find arxiv papers about lattice theory"),
    ("todo_add", "add a todo: call the bank tomorrow"),
    ("todo_list", "what is on my todo list?"),
    ("remember", "remember that the USB stick is drive E"),
    ("stack_health", "is the stack healthy?"),
    ("kernel_status", "what is the kernel status?"),
    ("skill_list", "which skills do I have?"),
    ("whoami", "who am I?"),
    ("session_search", "search my past sessions for the word license"),
]
# Where the kit ships two schemas for one intent, either name is a correct answer: scoring one of
# them wrong measures the labeller, not the model. Keep this list short and justified - it is only for
# genuine duplicates, never for "close enough" (a drives question answered with list_dir stays a miss).
ALSO_OK: dict[str, set[str]] = {
    "remember": {"memory_append", "remember"},
    "whoami": {"whoami", "identity_read"},
}

REQUIRED = {t["function"]["name"]: set(t["function"].get("parameters", {}).get("required") or [])
            for t in TOOLS_SCHEMA}


def registry_record(model_id: str) -> dict | None:
    reg = json.loads((KIT / "save" / "registry.json").read_text(encoding="utf-8"))
    for rec in reg.get("models") or []:
        if str(rec.get("id")) == model_id:
            return rec
    return None


def ask(port: int, alias: str, key: str, text: str, timeout: int = 180) -> dict:
    body = {
        "model": alias,
        "messages": [{"role": "user", "content": text}],
        "tools": TOOLS_SCHEMA,
        "max_tokens": 512,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(
        "http://127.0.0.1:%d/v1/chat/completions" % port,
        data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            out = json.loads(fh.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return {"seconds": time.time() - t0, "http": exc.code, "error": exc.read()[:200].decode("utf-8", "replace")}
    except Exception as exc:
        return {"seconds": time.time() - t0, "error": type(exc).__name__}
    choice = (out.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    # Score with the console's OWN extractor: a model whose GGUF template has no tool tokens answers
    # with fenced JSON instead of native tool_calls, and chat_loop.parse_fence_tool already dispatches
    # that. Counting only native calls measured the model wrong (qwen2.5-coder read as 0/24 while it was
    # naming the right tool every time in text).
    calls = extract_tool_calls(msg, content)
    first = calls[0] if calls else {}
    name = str(first.get("name") or "")
    args = first.get("arguments") if isinstance(first.get("arguments"), dict) else {}
    req = REQUIRED.get(name, set())
    return {
        "seconds": time.time() - t0,
        "calls": len(calls),
        "via": "native" if (msg.get("tool_calls") or []) else ("text" if calls else ""),
        "name": name,
        "args_ok": bool(name) and req.issubset(set(args.keys())),
        "missing_keys": sorted(req - set(args.keys())),
        "text": content[:120],
        "finish": choice.get("finish_reason"),
    }


def bench(model_id: str, port: int = 11471) -> dict:
    rec = registry_record(model_id)
    if not rec:
        return {"model": model_id, "error": "not_in_registry"}
    gguf = Path(str(rec.get("path") or ""))
    if not gguf.is_file():
        return {"model": model_id, "error": "gguf_missing", "path": str(gguf)}
    try:
        lg = lygo_engine.plan(rec) or {}
        lg = lg.get("llama") if isinstance(lg.get("llama"), dict) else {}
    except Exception:
        lg = {}
    alias = model_id
    key = image_tools._engine_key()
    engine.spawn_runner(
        port=port, gguf=gguf, kind="chat",
        mmproj=Path(str(rec["mmproj"])) if rec.get("mmproj") else None,
        ctx=min(int(lg.get("ctx") or 16384), 16384), ngl=int(lg.get("ngl") or 99), alias=alias,
        api_key=key, threads=lg.get("threads"), mmap=bool(lg.get("mmap", True)),
        flash_attn=bool(lg.get("flash_attn")), kv_type=str(lg.get("kv_type") or "q8_0"),
        batch=2048, ubatch=4096)
    results = []
    try:
        t0 = time.time()
        while time.time() - t0 < 240 and not image_tools._engine_up(port):
            time.sleep(3)
        if not image_tools._engine_up(port):
            return {"model": model_id, "error": "boot_timeout"}
        load_s = time.time() - t0
        for want, text in CASES:
            got = ask(port, alias, key, text)
            got["want"] = want
            accept = ALSO_OK.get(want) or {want}
            got["ok"] = (got.get("name") or "") in accept
            if got["ok"] and got.get("name") != want:
                got["accepted_as"] = want
            req_keys = REQUIRED.get(got.get("name") or "", set())
            got["required_keys"] = sorted(req_keys)
            results.append(got)
            print("   %-16s -> %-16s %s  %4.1fs" % (
                want, got.get("name") or "-", "OK" if got.get("ok") else "MISS", got.get("seconds", 0)), flush=True)
    finally:
        try:
            engine.stop_port(port)
        except Exception:
            pass
    hit = sum(1 for r in results if r.get("ok"))
    called = sum(1 for r in results if r.get("name"))
    picked = sum(1 for r in results if r.get("ok") and r.get("args_ok"))
    out = {
        "model": model_id, "load_seconds": round(load_s, 1), "cases": len(results),
        "called_something": called, "right_tool": hit, "right_tool_with_valid_args": picked,
        "accuracy": round(hit / max(1, len(results)), 3),
        "mean_seconds": round(sum(r.get("seconds", 0) for r in results) / max(1, len(results)), 1),
        "results": results,
    }
    (KIT / "save" / "bench").mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    (KIT / "save" / "bench" / ("tools_%s_%s.json" % (model_id.replace(":", "-").replace("/", "-"), stamp))
     ).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="gemma4:12b,qwen2.5-coder:7b")
    ap.add_argument("--port", type=int, default=11471)
    args = ap.parse_args()
    summary = []
    for mid in [m.strip() for m in args.models.split(",") if m.strip()]:
        print("== %s" % mid, flush=True)
        try:
            r = bench(mid, args.port)
        except Exception as exc:
            r = {"model": mid, "error": "%s: %s" % (type(exc).__name__, exc)}
        print("   => %s" % json.dumps({k: v for k, v in r.items() if k != "results"}), flush=True)
        summary.append(r)
    print()
    print("%-24s %-6s %-6s %-8s %s" % ("model", "right", "args", "mean_s", "error"))
    for r in summary:
        print("%-24s %-6s %-6s %-8s %s" % (
            r.get("model"), r.get("right_tool"), r.get("right_tool_with_valid_args"),
            r.get("mean_seconds"), r.get("error", "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
