#!/usr/bin/env python3
"""D9: make sure the console answers, starting it if it is down. Idempotent, never kills anything.

    python scripts/ensure_console.py                 # PC copy, its own port
    python scripts/ensure_console.py --quiet         # one line, for the end of a long script
    python scripts/ensure_console.py --kit E:/LYGO_BUILDER_KEY/lygo_llm_console --wait 180

Why it exists (handoff D9, observed twice by Grok and again 2026-09-22): the full pytest suite and the
long validation scripts take 9641 down on purpose, and nothing brought it back. A pass then ended with
the operator's console dead and no note saying who left it that way.

Port precedence is the kit's own, read from the target copy - never guessed from the machine:
    LYGO_CONSOLE_PORT env  >  <kit>/config/local.json "port"  >  <kit>/config/console.json "port"  >  9641

Exit codes: 0 the console answers, 1 it did not come up in time (the reason is printed).
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

DEFAULT_PORT = 9641
DEFAULT_WAIT = 150
# The stick has its own launcher and owns it (rule: stick launchers stay stick-owned - this helper
# only *calls* it, and only when asked for that kit).
STICK_LAUNCHER = "LYGO_AGENT_STICK.bat"
PC_LAUNCHER = "LYGO_LLM_CONSOLE.bat"

HERE = Path(__file__).resolve().parent
DEFAULT_KIT = HERE.parent


def _read_port(kit: Path) -> tuple[int, str]:
    env = os.environ.get("LYGO_CONSOLE_PORT", "").strip()
    if env.isdigit():
        return int(env), "env"
    for name in ("local.json", "console.json"):
        path = kit / "config" / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a torn config must not stop the helper
            continue
        port = data.get("port") if isinstance(data, dict) else None
        if isinstance(port, int) and 0 < port < 65536:
            return port, name
        if isinstance(port, str) and port.strip().isdigit():
            return int(port.strip()), name
    return DEFAULT_PORT, "default"


def health(port: int, timeout: float = 4.0) -> dict | None:
    """The console's own health document, or None when it is not answering."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            if r.status != 200:
                return None
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - down, busy and not-yet-listening all mean "not answering"
        return None


def launcher_for(kit: Path) -> Path | None:
    for name in (STICK_LAUNCHER, PC_LAUNCHER):
        p = kit / name
        if p.is_file():
            return p
    return None


def start(kit: Path, launcher: Path) -> str:
    """Start the launcher detached, so this helper can exit without taking the console with it."""
    if os.name == "nt":
        os.startfile(str(launcher))  # noqa: S606 - the kit's own .bat, detached from this process
        return f"started {launcher.name}"
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, str(kit / "src" / "server.py"), "serve", "--no-browser"],
        cwd=str(kit),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return f"started server.py serve (pid {proc.pid})"


def ensure(kit: Path = DEFAULT_KIT, port: int | None = None, wait: int = DEFAULT_WAIT,
           quiet: bool = False, start_it: bool = True, restore_only: bool = False) -> tuple[bool, str]:
    """Return (answers, one-line report). Waits for `ready` after starting, because a console that is
    up but still loading its model is not usable and must not be reported as a pass.

    `restore_only` is for the callers that run *beside* a console rather than instead of one
    (certification, the gauntlet, a test session): they put back a console they took down, but they
    must never cold-boot one that was not running. Measured 2026-09-22: certify called this on the
    stick copy while the stick was deliberately stopped, so it launched a 12B cold boot inside a
    certification and spent the full wait on it — the run looked hung and the operator learned nothing.
    """
    port = port or _read_port(kit)[0]
    doc = health(port)
    if doc and doc.get("brain"):
        line = f"console {port}: already up ({doc.get('brain')} / {doc.get('selected')})"
        return True, line
    if restore_only:
        return False, f"console {port}: not running - left as found (no console to restore)"
    if not start_it:
        return False, f"console {port}: down (not started: --no-start)"
    launcher = launcher_for(kit)
    if launcher is None:
        return False, f"console {port}: down and no launcher in {kit}"
    note = start(kit, launcher)
    deadline = time.time() + max(10, wait)
    while time.time() < deadline:
        doc = health(port)
        if doc and doc.get("ok"):
            brain = doc.get("brain")
            tail = "" if brain == "ready" else f" (brain {brain})"
            return True, f"console {port}: relaunched via {launcher.name} - ok{tail} - {note}"
        time.sleep(2)
    return False, f"console {port}: still not answering after {wait}s ({note})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ensure the LYGO console is up; start it when it is not.")
    ap.add_argument("--kit", default=str(DEFAULT_KIT), help="kit root of the copy to check")
    ap.add_argument("--port", type=int, default=None, help="override the port this copy carries")
    ap.add_argument("--wait", type=int, default=DEFAULT_WAIT, help="seconds to wait for ready")
    ap.add_argument("--quiet", action="store_true", help="print one line")
    ap.add_argument("--no-start", action="store_true", help="report only; never launch anything")
    args = ap.parse_args(argv)
    if os.environ.get("LYGO_NO_ENSURE", "").strip() in {"1", "true", "yes"}:
        print("ensure_console: skipped (LYGO_NO_ENSURE set)")
        return 0
    ok, line = ensure(Path(args.kit), args.port, args.wait, args.quiet, not args.no_start)
    print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
