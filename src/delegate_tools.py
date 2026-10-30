"""Side agents, for free: hand a task to an external agent loop and read its receipt.

WHY THIS SHAPE. OpenCode's free model pool (`deepseek-v4.1-flash-free` and the other
`-free` ids) is deliberately locked to the OpenCode client: the same inference endpoint
called from our own code answers 403 `FreeTierError` - "OpenCode's free tier can only be
used from within OpenCode". So the console cannot reach those models as a cloud provider
the way it reaches deepseek/groq/gemini; the only route is to DRIVE THE CLI, which is
what this file does. Nothing here needs an API key, an account, or a cent.

WHAT IT COSTS. The free tier is metered (community reports: ~200 requests/day, a per-model
lockout that can outlive 24h, and a `Retry-After` pointing at 00:00 UTC). Its limit is
MASKED: an exhausted free model answers "Unexpected server error. Check server logs for
details." with a ref, not a 429. `subagent_pool(probe=True)` is how an operator finds out
which engine is answering NOW instead of guessing from a failed task.

TWO PITFALLS THIS FILE ABSORBS (both met live, on first contact):
1. OpenCode resolves its own working directory and CRASHES on a POSIX-style path from this
   shell. Launched from an MSYS `/tmp` directory it was handed `C:\\tmp\\<dir>`, could not
   `lstat` it, and died inside `SystemPrompt.environment` - surfacing as the same masked
   "Unexpected server error". Every directory this module hands the CLI is a real, absolute
   Windows path (`_run_dir`), and the CLI's own cwd is the kit root.
2. That same opaque error is ALSO what a wrong or retired model id returns. So it is never
   reported as "the free tier is down": the ref travels back and the caller is told to probe
   or switch engines.

DATA. The free ids are shared promotional endpoints with per-model terms - some log requests
for evaluation (NVIDIA's free endpoints are trial and logged), some train on prompts, some
(Space Bunny, LongCat) publish zero retention. SIDE TASKS ONLY: never hand a free engine a
private operator context. `config/delegates.json` carries the roster and says so.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from paths import CONFIG, WORKSPACE, under_workspace

ROSTER_PATH = CONFIG / "delegates.json"
SIDE_ROOT = WORKSPACE / "subagents"
VERIFIED_PATH = WORKSPACE / "delegates_verified.json"
MASKED = "unexpected server error"
PROBE_PROMPT = "Reply with exactly: POOL_OK"

DEFAULT_ROSTER: dict[str, Any] = {
    "default": "opencode/deepseek-v4.1-flash-free",
    "free": [
        "opencode/deepseek-v4.1-flash-free",
        "opencode/nemotron-3-ultra-free",
        "opencode/nemotron-3.5-lightning-free",
        "opencode/longcat-2.5-preview-free",
        "opencode/cat-jam-free",
        "opencode/mimo-v2.6-flash-free",
        "opencode/space-bunny-free",
    ],
    "byo": {},
}


def _roster() -> dict[str, Any]:
    """config/delegates.json when it parses, else the shipped defaults."""
    try:
        data = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and (data.get("free") or data.get("byo")):
            return data
    except Exception:
        pass
    return json.loads(json.dumps(DEFAULT_ROSTER))


def _exe() -> str | None:
    for cand in ("opencode", "opencode.cmd", "opencode.exe"):
        found = shutil.which(cand)
        if found:
            return found
    for cand in (
        Path(os.environ.get("APPDATA", "")) / "npm" / "opencode.cmd",
        Path(os.environ.get("USERPROFILE", "")) / ".opencode" / "bin" / "opencode.exe",
    ):
        try:
            if cand.exists():
                return str(cand)
        except OSError:
            continue
    return None


def _run_dir(d: str | None, consent: bool = False) -> tuple[Path, str]:
    """A REAL absolute Windows directory for the CLI (pitfall 1), created if needed.

    `under_workspace` keeps an absolute path exactly as given - right for the read/write limbs, and
    WRONG here: a side agent RUNS wherever it is handed, so an absolute folder outside the kit is a
    `consent=true` decision, the same rule the film and plexo limbs already follow. (Measured: without
    this check the limb happily started a side agent in C:/Windows/System32.)
    """
    try:
        target = under_workspace(d) if d else SIDE_ROOT / time.strftime("%Y%m%d-%H%M%S")
    except Exception as exc:
        return WORKSPACE, f"dir_refused:{type(exc).__name__}"
    try:
        resolved = Path(target).resolve()
    except OSError as exc:
        return WORKSPACE, f"dir_unresolved:{type(exc).__name__}"
    root = WORKSPACE.resolve()
    if resolved != root and not resolved.is_relative_to(root) and not consent:
        return WORKSPACE, "dir_outside_needs_consent"
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return WORKSPACE, f"dir_unwritable:{type(exc).__name__}"
    text = str(resolved)
    # never hand the CLI a /c/... or /tmp path: it resolves those itself and crashes
    if text.startswith("/") or re.match(r"^/[a-zA-Z]/", text):
        return WORKSPACE, "posix_path"
    return resolved, ""


def _capture(argv: list[str], *, timeout: int, cwd: str, env: dict[str, str]) -> tuple[int | None, str, str, bool]:
    """Run the CLI, capture both streams, never raise at the caller."""
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        return proc.returncode, proc.stdout or "", proc.stderr or "", False
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return None, out, err, True
    except OSError as exc:
        return None, "", f"spawn_failed:{type(exc).__name__}", False


def _parse(raw: str) -> dict[str, Any]:
    """Read OpenCode's `--format json` event stream defensively.

    The event shape is the vendor's to change, so nothing here trusts a single key: text is
    collected from wherever it appears, errors are collected WITH their ref (which is the
    only handle on the masked failures), and an unparsable stream still yields the raw tail.
    """
    text: list[str] = []
    tools: list[str] = []
    errors: list[dict[str, str]] = []
    models: list[str] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "error":
            err = ev.get("error") if isinstance(ev.get("error"), dict) else {}
            data = err.get("data") if isinstance(err.get("data"), dict) else {}
            errors.append({
                "name": str(err.get("name") or ""),
                "message": str(data.get("message") or err.get("message") or "")[:240],
                "ref": str(data.get("ref") or ""),
            })
        part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
        for holder in (ev, part):
            val = holder.get("text")
            if isinstance(val, str) and val.strip():
                text.append(val)
            tool = holder.get("tool")
            if isinstance(tool, str) and tool.strip():
                tools.append(tool.strip())
        if isinstance(ev.get("model"), str) and ev["model"].strip():
            models.append(ev["model"].strip())
    return {"text": text, "tools": tools, "errors": errors, "models": models}


def _read_only_env(env: dict[str, str]) -> dict[str, str]:
    """Deny the tools that change the machine. A side agent reads and answers by default.

    The shape is OpenCode's documented OBJECT MAP (`{"edit": "deny"}`), per
    https://opencode.ai/docs/permissions/ - NOT the list-of-{permission,pattern,action} the CLI stores
    in its own config, which is also what it logs. Passing the list shape is silently IGNORED: measured
    live, a read_only=True run still used the `write` tool and left a file on disk. Verify this control
    with a real task that tries to write, never by reading the env back.
    """
    out = dict(env)
    out["OPENCODE_PERMISSION"] = json.dumps({
        "edit": "deny",
        "bash": "deny",
        "webfetch": "deny",
    })
    return out


def _env_for(engine: str, roster: dict[str, Any]) -> dict[str, str]:
    """The child environment, plus a BYO provider key when the roster maps one.

    Free engines need nothing - that is the point of them. A BYO entry names which key in
    config/api.json to hand this one child process; the value is never logged, echoed or
    returned, and it is the same key the console already uses for its own cloud turns.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    prefix = engine.split("/", 1)[0]
    slot = (roster.get("byo") or {}).get(prefix)
    if isinstance(slot, str) and slot:
        try:
            keys = json.loads((CONFIG / "api.json").read_text(encoding="utf-8")).get("keys") or {}
            value = keys.get(slot)
            if isinstance(value, str) and value:
                env[prefix.upper() + "_API_KEY"] = value
        except Exception:
            pass
    return env


def subagent_run(task: str = "", engine: str = "", dir: str = "", timeout: int = 300,
                 read_only: bool = True, consent: bool = False) -> dict[str, Any]:
    """Hand one side task to an external agent loop; return a receipt, never raise."""
    task = (task or "").strip()
    if not task:
        return {"ok": False, "error": "empty_task", "hint": "pass the side task as `task`"}
    roster = _roster()
    eng = (engine or str(roster.get("default") or "")).strip()
    if not eng:
        return {"ok": False, "error": "no_engine",
                "hint": "name one (subagent_pool lists them) or set `default` in config/delegates.json"}
    exe = _exe()
    if not exe:
        return {"ok": False, "error": "opencode_missing",
                "hint": "one free install, no account and no key: npm i -g opencode-ai@latest"}
    run_dir, dir_error = _run_dir(dir, consent=bool(consent))
    if dir_error:
        hint = ("the side agent works in a directory inside the workspace; give `dir` as a folder "
                "under it, or omit it for a fresh one")
        if dir_error == "dir_outside_needs_consent":
            hint = ("that folder is outside the workspace: a side agent RUNS where it is put, so it "
                    "needs consent=true - the same rule the film and plexo limbs follow")
        return {"ok": False, "error": dir_error, "hint": hint}
    seconds = max(30, min(900, int(timeout or 300)))
    env = _env_for(eng, roster)
    if read_only:
        env = _read_only_env(env)
    argv = [exe, "run", "--dir", str(run_dir), "-m", eng, "--format", "json", task]
    started = time.time()
    code, out, errout, timed_out = _capture(argv, timeout=seconds, cwd=str(WORKSPACE), env=env)
    elapsed = time.time() - started
    parsed = _parse(out)
    blob = (out + "\n" + errout).lower()
    masked = MASKED in blob
    result: dict[str, Any] = {
        "ok": code == 0 and not timed_out and not parsed["errors"],
        "engine": eng,
        "dir": str(run_dir),
        "read_only": bool(read_only),
        "seconds": round(elapsed, 1),
        "code": code,
        "answer": (parsed["text"][-1][-6000:] if parsed["text"] else ""),
        "tools_used": sorted(set(parsed["tools"]))[:12],
    }
    if parsed["errors"]:
        result["engine_error"] = parsed["errors"][:2]
    if timed_out:
        result["ok"] = False
        result["error"] = "timeout"
        result["hint"] = f"the side agent did not finish inside {seconds}s; raise `timeout` or narrow the task"
    elif masked:
        result["error"] = "engine_masked_error"
        result["hint"] = ("OpenCode masks a rate limit, an exhausted free cap and a retired model id as this "
                          "one message. Run subagent_pool(probe=true) to see which engine answers now, or "
                          "pass another `engine`.")
    if not parsed["text"] and not parsed["errors"] and not timed_out:
        result["raw_tail"] = (out or errout)[-1200:]
    return result


def subagent_pool(probe: bool = False, engines: str = "", timeout: int = 90) -> dict[str, Any]:
    """The side-engine roster - and, with probe=True, which of them answer right now."""
    roster = _roster()
    free = [str(x) for x in (roster.get("free") or [])]
    byo = dict(roster.get("byo") or {})
    out: dict[str, Any] = {
        "default": roster.get("default"),
        "free": free,
        "byo": byo,
        "note": ("The free ids are client-locked (403 from outside OpenCode), metered, and their limit is masked "
                 "as an opaque server error. Side tasks only - never route private operator context to a free "
                 "engine; per-model data terms differ."),
    }
    want = [e.strip() for e in engines.split(",") if e.strip()] if engines else []
    if probe:
        exe = _exe()
        if not exe:
            out["probe"] = {"ok": False, "error": "opencode_missing",
                            "hint": "npm i -g opencode-ai@latest"}
            return out
        run_dir, _ = _run_dir(None)
        live: list[str] = []
        dead: dict[str, str] = {}
        for eng in (want or free):
            code, raw, errraw, timed_out = _capture(
                [exe, "run", "--dir", str(run_dir), "-m", eng, "--format", "json", PROBE_PROMPT],
                timeout=max(30, min(240, int(timeout or 90))), cwd=str(WORKSPACE), env=_env_for(eng, roster))
            blob = raw + errraw
            if code == 0 and "POOL_OK" in blob and MASKED not in blob.lower():
                live.append(eng)
            else:
                hit = _parse(raw)["errors"]
                dead[eng] = (hit[0].get("name") or "masked_error") if hit else (
                    "timeout" if timed_out else f"exit_{code}")
        out["probe"] = {"live": live, "not_answering": dead,
                        "probed": len(live) + len(dead), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        if live:
            out["default_verified"] = live[0]
            try:
                VERIFIED_PATH.write_text(json.dumps(out["probe"], indent=2), encoding="utf-8")
            except OSError:
                pass
    return out
