"""Gauntlet: what can the on-box brain actually DO for tasking, measured honestly.

Every task is graded on an effect checked independently on disk (or on a value that is simply true), not
on what the reply claims. That distinction is the whole point: this console exists to notice exactly this
kind of gap, so the harness must not repeat the mistake of believing a confident sentence.

Run: C:/Python313/python.exe scripts/gauntlet_agent.py
Writes docs/GAUNTLET_<date>.md and save/gauntlet/<date>.json
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "src"))
DESKTOP = Path(os.environ.get("USERPROFILE") or Path.home()) / "Desktop"  # portable, not one operator's
NOTES = KIT / "workspace" / "notes"
WS = KIT / "workspace"
STAMP = time.strftime("%Y-%m-%d")


def _base() -> str:
    """The console THIS copy talks to.

    Measured 2026-09-22: the URL was hardcoded to 127.0.0.1:9641, so this script could not measure
    the USB copy at all - run from the stick it either graded the PC console or refused with
    "console is not up" while the stick answered on 9651. The port now comes from the copy's own
    config, the same reader the launchers use, with an explicit override for a remote/odd setup.
    """
    url = os.environ.get("LYGO_CONSOLE_URL", "").strip()
    if url:
        return url.rstrip("/")
    port = os.environ.get("LYGO_CONSOLE_PORT", "").strip()
    if not port.isdigit():
        try:
            import ensure_console

            port = str(ensure_console._read_port(KIT)[0])
        except Exception:  # noqa: BLE001 - a missing helper must not stop the measurement
            port = "9641"
    return f"http://127.0.0.1:{port}"


BASE = _base()


def health():
    try:
        return json.loads(urllib.request.urlopen(f"{BASE}/api/health", timeout=10).read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"_err": type(exc).__name__}


def wait_console(seconds=240):
    """Block until the console answers, or give up. A dead port is not a model failure."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if health().get("ok"):
            return True
        time.sleep(5)
    return False


def ask(text, timeout=420):
    tok = (KIT / "data" / ".lygo_llm_token").read_text(encoding="utf-8").strip()
    body = json.dumps({"messages": [{"role": "user", "content": text}]}).encode()
    req = urllib.request.Request(f"{BASE}/api/chat?token={tok}", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    last = None
    for attempt in range(3):
        if not wait_console(240 if attempt else 60):
            raise RuntimeError("console is not up")
        try:
            raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
            break
        except Exception as exc:  # a restart mid-task: wait for it, then run the task again
            last = exc
            time.sleep(10)
    else:
        raise RuntimeError(f"console unreachable: {last}")
    reply, traces = [], []
    for ln in raw.splitlines():
        if not ln.startswith("data: "):
            continue
        try:
            f = json.loads(ln[6:])
        except Exception:
            continue
        if f.get("type") == "token":
            reply.append(f.get("delta") or "")
        for tr in f.get("traces") or []:
            traces.append({"name": tr.get("name"), "args": tr.get("arguments"),
                           "ok": (tr.get("result") or {}).get("ok") if isinstance(tr.get("result"), dict) else None})
    return round(time.time() - t0, 1), "".join(reply), traces


def wipe_scratch() -> dict:
    """D2: each run starts from an empty graded workspace. Prior files must not grade this run."""
    removed: list[str] = []
    named = [
        NOTES / "gauntlet_t4.txt",
        NOTES / "gauntlet_t6.txt",
        NOTES / "gauntlet_t8.txt",
        WS / "gauntlet_t8.txt",
        DESKTOP / "gauntlet_t5.txt",
        DESKTOP / "gauntlet_t7.txt",
    ]
    for p in named:
        if p.is_file():
            p.unlink()
            removed.append(str(p))
    for name in ("mod_a.py", "run_a.py"):
        for p in WS.rglob(name):
            try:
                p.unlink()
                removed.append(str(p))
            except OSError:
                continue
    return {"wiped": len(removed), "paths": removed}


def fresh(p: Path):
    if p.exists():
        p.unlink()


def _file_has(p: Path, needle: str) -> tuple[bool, str]:
    if not p.is_file():
        return False, f"{p} does not exist"
    body = p.read_text(encoding="utf-8", errors="replace")
    return (needle in body), f"{p} exists, {len(body)} chars, contains {needle!r}: {needle in body}"


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\b\d[\d,]{1,}\b", text.replace(",", "")))


TASKS = [
    ("T1", "single limb, value", "Use your calc limb to work out 47 * 89. Tell me only the number.",
     lambda: (True, "value task"),
     lambda reply, tr: ("4183" in reply.replace(",", ""), f"reply said 4183: {'4183' in reply.replace(',', '')}")),
    ("T2", "single limb, read", f"Read the file {KIT / 'VERSION'} on this PC and tell me what version it says.",
     lambda: (True, "read task"),
     lambda reply, tr: ("1.3.0" in reply, f"reply said 1.3.0: {'1.3.0' in reply}")),
    ("T3", "single limb, list", "List what is in your workspace folder. How many entries are there?",
     lambda: (True, "list task"),
     lambda reply, tr: (bool([n for n in tr if n["name"] in ("list_dir", "workspace_map")]),
                        f"listed with {[n['name'] for n in tr if n['name'] in ('list_dir', 'workspace_map')] or 'no listing limb'}")),
    ("T4", "write to workspace", "Create a file called gauntlet_t4.txt in your workspace notes folder with the "
     "exact text T4-OK.",
     lambda: fresh(NOTES / "gauntlet_t4.txt"),
     lambda reply, tr: _file_has(NOTES / "gauntlet_t4.txt", "T4-OK")),
    ("T5", "write to MY DESKTOP", "Create a file called gauntlet_t5.txt on my desktop with the exact text "
     "T5-OK. Use save_note with where=desktop and consent=true.",
     lambda: fresh(DESKTOP / "gauntlet_t5.txt"),
     lambda reply, tr: _file_has(DESKTOP / "gauntlet_t5.txt", "T5-OK")),
    ("T6", "two steps, chained", "Read your workspace notes file gauntlet_t4.txt, then save a new file called "
     "gauntlet_t6.txt in that same folder whose contents are what you read followed by the word CHECKED.",
     lambda: fresh(NOTES / "gauntlet_t6.txt"),
     lambda reply, tr: _file_has(NOTES / "gauntlet_t6.txt", "CHECKED")),
    ("T7", "conditional branch", "Check whether a file called gauntlet_t7.txt exists on my desktop. If it does "
     "not exist, create it with the text T7-NEW. If it already exists, do nothing and say so.",
     lambda: fresh(DESKTOP / "gauntlet_t7.txt"),
     lambda reply, tr: _file_has(DESKTOP / "gauntlet_t7.txt", "T7-NEW")),
    ("T8", "compute with code", "Use python_exec to compute the 12th Fibonacci number (1,1,2,3...) and tell me "
     "the number.",
     lambda: fresh(WS / "gauntlet_t8.txt"),
     lambda reply, tr: ("144" in reply, f"reply said 144: {'144' in reply}")),
    ("T9", "write code, run it", "Write a small Python program in your workspace that prints the sum of all "
     "whole numbers from 1 to 100, run it, and tell me the result.",
     lambda: None,
     lambda reply, tr: ("5050" in reply.replace(",", ""), f"reply said 5050: {'5050' in reply.replace(',', '')}")),
    ("T10", "deferred work", "Queue a background task that runs self_check, then tell me the task id it gave you.",
     lambda: None,
     lambda reply, tr: (bool(re.search(r"t[0-9a-f]{8,}", reply)) or any(n["name"] == "task_add" and n["ok"] for n in tr),
                        f"task_add ran: {any(n['name'] == 'task_add' for n in tr)}")),
    ("T11", "honest failure", "Read the file C:/definitely/not/here/nothing.txt and tell me exactly what it says.",
     lambda: None,
     lambda reply, tr: (bool(re.search(r"(not|no such|does ?n[o']t exist|missing|cannot|can't|unable|denied)", reply, re.I)),
                        "admitted the file is missing rather than inventing contents")),
    ("T12", "multi-file project", "Create two files in your workspace: mod_a.py defining a function answer() "
     "that returns 6*7, and run_a.py that imports it and prints the result. Then run run_a.py with python_exec "
     "and tell me what it printed.",
     lambda: None,
     lambda reply, tr: (("42" in reply) and bool(list(WS.rglob("mod_a.py"))) and bool(list(WS.rglob("run_a.py"))),
                        f"reply said 42: {'42' in reply} | mod_a.py anywhere under workspace: {bool(list(WS.rglob('mod_a.py')))}")),
]


def _leave_console_up() -> None:
    """D9: the long scripts take the console down; put it back before the operator finds it dead."""
    try:
        import ensure_console

        _ok, line = ensure_console.ensure(KIT, restore_only=True)
        print("console: " + line, flush=True)
    except Exception as exc:  # noqa: BLE001 - a helper must never fail the run it protects
        print(f"console: not checked ({type(exc).__name__})", flush=True)


def main():
    if not health().get("ok"):
        print("console is not up; ring the doorbell first")
        return 1
    wipe = wipe_scratch()
    print(f"gauntlet against the live local brain, {len(TASKS)} tasks; wiped {wipe['wiped']} scratch files", flush=True)
    results = []
    for tid, level, prompt, prepare, grade in TASKS:
        try:
            prepare()
        except Exception:
            pass
        print(f"\n[{tid}] {level}", flush=True)
        row = {"id": tid, "level": level, "prompt": prompt}
        try:
            secs, reply, traces = ask(prompt)
            ok, evidence = grade(reply, traces)
            row.update({"seconds": secs, "reply": reply[:2000], "raw_reply": reply,
                        "traces": traces,
                        "limbs_used": [t["name"] for t in traces], "pass": bool(ok), "evidence": evidence})
            print(f"   {secs}s | {'PASS' if ok else 'FAIL'} | limbs: {row['limbs_used']} | {evidence}", flush=True)
        except Exception as exc:
            row.update({"seconds": None, "error": f"{type(exc).__name__}: {exc}", "pass": False})
            print(f"   ERROR {row['error']}", flush=True)
        results.append(row)

    graded = [r for r in results]
    passed = [r for r in graded if r.get("pass")]
    verdict = {
        "tasks": len(graded),
        "passed": len(passed),
        "failed": [r["id"] for r in graded if not r.get("pass")],
        "by_level": {r["id"]: ("PASS" if r.get("pass") else "FAIL") for r in graded},
        "limbs_used_overall": sorted({n for r in graded for n in r.get("limbs_used", [])}),
    }
    (KIT / "save" / "gauntlet").mkdir(parents=True, exist_ok=True)
    (KIT / "save" / "gauntlet" / f"{STAMP}.json").write_text(
        json.dumps({"verdict": verdict, "wipe": wipe, "results": results}, indent=1), encoding="utf-8")

    lines = [f"# Gauntlet: what the on-box brain can actually do ({STAMP})", "",
             f"Local brain, live console, {len(graded)} tasks. Every task is graded on an effect checked "
             "independently on disk or on a value that is simply true - never on what the reply claims.", "",
             f"**Score: {len(passed)}/{len(graded)}**", "", "| # | level | result | secs | limbs it called | evidence |",
             "|---|-------|--------|------|-----------------|----------|"]
    for r in graded:
        lines.append(f"| {r['id']} | {r['level']} | {'PASS' if r.get('pass') else 'FAIL'} | "
                     f"{r.get('seconds') or '-'} | {', '.join(r.get('limbs_used') or []) or '(none)'} | "
                     f"{str(r.get('evidence') or r.get('error'))[:150]} |")
    lines += ["", "## Replies, verbatim", ""]
    for r in graded:
        lines += [f"### {r['id']} - {r['level']} ({'PASS' if r.get('pass') else 'FAIL'})", "",
                  "**asked:** " + r["prompt"].replace("\n", " "), "",
                  "**said:** " + (r.get("reply") or r.get("error") or "").strip()[:900].replace("\n", " "), "",
                  "**limbs:** " + (", ".join(r.get("limbs_used") or []) or "(none)"), ""]
    (KIT / "docs" / f"GAUNTLET_{STAMP}.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n" + json.dumps(verdict, indent=1))
    print(f"\nreport: {KIT / 'docs' / f'GAUNTLET_{STAMP}.md'}")
    _leave_console_up()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
