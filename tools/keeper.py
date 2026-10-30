"""Keeper: the daemon that keeps this console alive and its schedule running.

The console is a single process holding a model in VRAM. When it dies the page dies with it and
nothing restarts it; when the doorbell dies, even the operator's Boot button goes quiet. The keeper
is the smallest thing that fixes both: a sweep loop that checks the two ports, brings back what is
missing (with a cooldown, so a crash loop cannot turn into a relaunch storm), and drives the cron
schedule.

Ported from the Hermes periodic scheduler (`agent/periodic_scheduler.py`) and the cron runtime
(`cron/scheduler.py`): the loop is a sweep, not a pile of timers, and every sweep is idempotent
because the state it reads is the record it writes.

Testability is deliberate: `sweep()` takes its probes as arguments, so the whole supervision policy
can be exercised without a console, a doorbell or a model.

Usage:
    python tools/keeper.py --once                 # one sweep, print it, exit
    python tools/keeper.py --interval 20          # sweep every 20s (foreground)
    python tools/keeper.py --detach               # sweep every 20s, detached, logged
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
DATA = ROOT / "data"
STATE = DATA / "keeper.json"
LOCK = DATA / "keeper.lock"
LOGDIR = ROOT / "save" / "logs"
RING_COOLDOWN_S = 90.0      # never relaunch more often than this
DEFAULT_INTERVAL = 20.0


# --------------------------------------------------------------------------- probes
def console_port() -> int:
    try:
        cfg = json.loads((ROOT / "config" / "console.json").read_text(encoding="utf-8"))
        return int(cfg.get("port") or cfg.get("console_port") or 9641)
    except Exception:
        return 9641


def probe_console(port: int = 0, timeout: float = 6.0) -> dict[str, Any]:
    port = port or console_port()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as fh:
            h = json.loads(fh.read().decode("utf-8", "replace"))
        return {"up": True, "port": port, "brain": h.get("brain"), "build": h.get("release")}
    except Exception as exc:
        return {"up": False, "port": port, "why": type(exc).__name__}


def probe_doorbell(port: int = 0, timeout: float = 6.0) -> dict[str, Any]:
    port = port or (console_port() - 1)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/state", timeout=timeout) as fh:
            s = json.loads(fh.read().decode("utf-8", "replace"))
        return {"up": bool(s.get("doorbell")), "port": port, "console_up": s.get("console_up")}
    except Exception as exc:
        return {"up": False, "port": port, "why": type(exc).__name__}


# --------------------------------------------------------------------------- actions
def ring_doorbell(timeout: float = 60.0) -> dict[str, Any]:
    port = console_port() - 1
    tokf = DATA / ".lygo_doorbell_token"
    tok = tokf.read_text(encoding="utf-8").strip() if tokf.is_file() else ""
    if not tok:
        return {"ok": False, "error": "no_doorbell_token"}
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/boot?token={tok}", timeout=timeout) as fh:
            return {"ok": True, "answer": fh.read(200).decode("utf-8", "replace")}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def start_doorbell() -> dict[str, Any]:
    """Start the doorbell detached, so it outlives whatever started the keeper."""
    py = sys.executable
    cmd = ["powershell", "-NoProfile", "-Command",
           f"Start-Process -FilePath '{py}' -ArgumentList '{ROOT / 'tools' / 'doorbell.py'}','--detach' "
           f"-WorkingDirectory '{ROOT}' -WindowStyle Hidden"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return {"ok": r.returncode == 0, "code": r.returncode, "err": (r.stderr or "")[:200]}


def start_console() -> dict[str, Any]:
    """Start the whole stack detached (launcher), the way the operator's desktop icon does."""
    launcher = ROOT / "LYGO_LLM_CONSOLE.bat"
    if not launcher.is_file():
        return {"ok": False, "error": "no_launcher", "looked_for": str(launcher)}
    cmd = ["powershell", "-NoProfile", "-Command",
           f"Start-Process -FilePath '{launcher}' -WorkingDirectory '{ROOT}' -WindowStyle Normal"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return {"ok": r.returncode == 0, "code": r.returncode, "err": (r.stderr or "")[:200]}


# --------------------------------------------------------------------------- state
def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE)


def log_line(text: str) -> None:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with (LOGDIR / "keeper.log").open("a", encoding="utf-8") as fh:
        fh.write(f"[keeper] {stamp} {text}\n")


# --------------------------------------------------------------------------- the sweep
def sweep(console: Callable[[], dict[str, Any]] | None = None,
          doorbell: Callable[[], dict[str, Any]] | None = None,
          ring: Callable[[], dict[str, Any]] | None = None,
          start_db: Callable[[], dict[str, Any]] | None = None,
          start_console_fn: Callable[[], dict[str, Any]] | None = None,
          run_due: Callable[..., list] | None = None,
          now: float | None = None, cooldown: float = RING_COOLDOWN_S) -> dict[str, Any]:
    """One supervision pass. Every probe and action is injectable so this is testable without a stack."""
    now = time.time() if now is None else now
    console = console or probe_console
    doorbell = doorbell or probe_doorbell
    ring = ring or ring_doorbell
    start_db = start_db or start_doorbell
    start_console_fn = start_console_fn or start_console
    if run_due is None:
        import crons

        run_due = crons.run_due

    state = load_state()
    c = console()
    d = doorbell()
    actions: list[str] = []
    last_ring = float(state.get("last_ring") or 0)

    if not d.get("up"):
        got = start_db()
        actions.append("doorbell_started" if got.get("ok") else f"doorbell_start_failed:{got.get('error') or got.get('code')}")
    if not c.get("up"):
        if (now - last_ring) >= cooldown:
            if d.get("up") or any(a.startswith("doorbell_started") for a in actions):
                got = ring()
                state["last_ring"] = now
                actions.append(f"rung_doorbell:{got.get('ok')}")
            else:
                got = start_console_fn()
                state["last_ring"] = now
                actions.append(f"started_console:{got.get('ok')}")
        else:
            actions.append(f"restart_on_cooldown:{int(cooldown - (now - last_ring))}s")

    crons_run: list = []
    try:
        crons_run = run_due(now)
    except Exception as exc:
        actions.append(f"cron_error:{type(exc).__name__}")

    state.update({"last_sweep": now, "last_sweep_human": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                  "console_up": bool(c.get("up")), "console_port": c.get("port"),
                  "doorbell_up": bool(d.get("up")), "doorbell_port": d.get("port"),
                  "actions": actions[-8:], "crons_run": crons_run[-8:],
                  "sweeps": int(state.get("sweeps") or 0) + 1})
    save_state(state)
    if actions or crons_run:
        log_line(f"console={state['console_up']} doorbell={state['doorbell_up']} "
                 f"actions={actions} crons={[c['name'] for c in crons_run]}")
    return {"ok": True, "console": c, "doorbell": d, "actions": actions, "crons_run": crons_run,
            "state": {k: state.get(k) for k in ("sweeps", "console_up", "doorbell_up", "last_ring")}}


def status() -> dict[str, Any]:
    state = load_state()
    c = probe_console()
    d = probe_doorbell()
    pid, alive = _lock_holder()
    return {"ok": True, "console": c, "doorbell": d, "state": state, "keeper_pid": pid, "keeper_alive": alive,
            "interval_s": state.get("interval_s") or DEFAULT_INTERVAL, "log": str(LOGDIR / "keeper.log")}


# --------------------------------------------------------------------------- one instance
def _lock_holder() -> tuple[int, bool]:
    try:
        pid = int(LOCK.read_text(encoding="utf-8").strip().split()[0])
    except Exception:
        return 0, False
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                             timeout=60).stdout
        return pid, str(pid) in out
    except Exception:
        return pid, False


def acquire_lock() -> bool:
    pid, alive = _lock_holder()
    if alive and pid != os.getpid():
        return False
    DATA.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(f"{os.getpid()} {time.strftime('%Y-%m-%d %H:%M:%S')}", encoding="utf-8")
    return True


def _wait_for_port(port: int, timeout: float = 180.0) -> bool:
    """True once something answers on 127.0.0.1:port. MEASURED 2026-10-16: a keeper started at the same
    moment as the console sees console_up=False for its first sweeps and rings the doorbell, which is how
    a boot could race itself into a second copy. Waiting for the port removes the race instead of tuning
    it."""
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=2):
                return True
        except Exception:
            time.sleep(2.0)
    return False


def stop() -> dict[str, Any]:
    """Stop the keeper that holds the lock, and verify it went rather than assuming."""
    pid, alive = _lock_holder()
    if not (pid and alive):
        return {"ok": True, "stopped": False,
                "why": f"no keeper alive (the lock names pid {pid or 'nothing'})"}
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, text=True, timeout=60)
    except Exception as exc:
        return {"ok": False, "stopped": False, "keeper_pid": pid, "why": f"taskkill failed: {exc}"}
    gone = not _lock_holder()[1]
    return {"ok": True, "stopped": gone, "keeper_pid": pid,
            "why": "" if gone else "the keeper still answers after taskkill"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Keep the LYGO console alive and its cron schedule running")
    ap.add_argument("--once", action="store_true", help="one sweep, print the JSON, exit")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    ap.add_argument("--detach", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--stop", action="store_true",
                    help="stop the keeper that holds the lock, and say whether it actually went")
    ap.add_argument("--wait-port", type=int, default=0,
                    help="wait for this port to answer before the first sweep (a booting console is not a dead one)")
    ap.add_argument("--ensure", action="store_true",
                    help="start a keeper ONLY if none is alive, then report which one is running")
    ap.add_argument("--log", default="")
    args = ap.parse_args(argv)

    if args.stop:
        print(json.dumps(stop(), ensure_ascii=False))
        return 0
    if args.status:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    if args.once:
        print(json.dumps(sweep(), ensure_ascii=False, indent=2))
        return 0

    if args.ensure:
        # The one call a launcher or another agent can make safely: idempotent, and it routes through the
        # verified detach path below so "ensured" means a lock was actually taken, never a bare rc=0.
        if args.wait_port:
            t0 = time.time()
            up = _wait_for_port(int(args.wait_port))
            log_line(f"ensure: console port {args.wait_port} "
                     f"{'answered' if up else 'never answered'} after {round(time.time() - t0, 1)}s")
        pid, alive = _lock_holder()
        if alive and pid != os.getpid():
            print(json.dumps({"ok": True, "started": False, "keeper_pid": pid,
                              "why": "a keeper is already alive"}))
            return 0
        args.detach = True

    if args.detach:
        log = args.log or str(LOGDIR / "keeper.out.log")
        LOGDIR.mkdir(parents=True, exist_ok=True)
        # No -RedirectStandardOutput here: PowerShell refuses that together with -WindowStyle, and it
        # failed silently the first time. The keeper writes its own keeper.log anyway.
        # MEASURED 2026-10-16: the PowerShell Start-Process route returns rc=0 and the child does not
        # survive, and it keeps the reason to itself (that was the 2026-09-21 mystery, still true today).
        # Launch the keeper the way this tree already launches long-lived processes instead: a native
        # detached process with its OUTPUT ATTACHED, so a crash on the way up leaves a traceback in
        # save/logs/keeper.out.log rather than silence.
        out_log = LOGDIR / "keeper.out.log"
        flags = 0
        if hasattr(subprocess, "DETACHED_PROCESS"):
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        with out_log.open("ab") as fh:
            child = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--interval", str(args.interval)],
                cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                creationflags=flags, close_fds=True)
        r = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        detail = f"launched detached, pid {child.pid}, log {out_log}"
        # MEASURED 2026-10-16 (first noted 2026-09-21): Start-Process returned 0 and the keeper never
        # existed. A launcher's exit code is not evidence that the process lives - the LOCK is. Wait for
        # the child to take it and answer honestly when nothing comes up, or the operator is told a
        # supervisor is running while nothing drives the crons (measured: the queue idle for weeks).
        pid, alive = _lock_holder()
        deadline = time.time() + 15.0
        while time.time() < deadline:
            pid, alive = _lock_holder()
            if alive and pid != os.getpid():
                break
            time.sleep(1.0)
        if alive and pid != os.getpid():
            log_line(f"detached keeper alive pid={pid}")
            print(json.dumps({"detached": True, "code": r.returncode, "keeper_pid": pid,
                              "log": str(LOGDIR / "keeper.log"), "interval_s": args.interval,
                              "detail": detail}))
            return 0
        why = ("no keeper took the lock within 15 s (Start-Process rc=%s, detail: %s)"
               % (r.returncode, detail or "no output"))
        log_line("detach failed: " + why)
        print(json.dumps({"detached": False, "code": r.returncode, "keeper_pid": pid, "why": why,
                          "log": str(LOGDIR / "keeper.log"),
                          "hint": "run it in the foreground instead: python tools/keeper.py --interval 20"}))
        return 1

    if not acquire_lock():
        pid, _ = _lock_holder()
        print(f"a keeper is already running (pid {pid})", file=sys.stderr)
        return 3

    # Seed the crons every console carries, now that this process is the one that will run them. Losing
    # this must never cost the supervisor its job, so it cannot raise.
    try:
        import crons

        seeded = crons.ensure_defaults()
        if seeded.get("seeded"):
            log_line(f"seeded default crons: {[s['name'] for s in seeded['seeded']]}")
    except Exception as exc:
        log_line(f"seeding default crons failed: {exc}")
    log_line(f"keeper up: interval={args.interval}s console={console_port()}")
    state = load_state()
    state["interval_s"] = args.interval
    save_state(state)
    try:
        while True:
            s = sweep()
            print(f"[keeper] {time.strftime('%H:%M:%S')} console={s['console'].get('up')} "
                  f"doorbell={s['doorbell'].get('up')} actions={s['actions']} crons={[c['name'] for c in s['crons_run']]}",
                  flush=True)
            time.sleep(max(5.0, args.interval))
    except KeyboardInterrupt:
        log_line("keeper down (keyboard interrupt)")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
