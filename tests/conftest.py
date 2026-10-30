"""D9 for pytest: a full run stops the live console on purpose (the suite boots and shuts one down),
so a green run used to leave the operator's console dead with nothing saying who did it.

This restores it at the end of the session. Set LYGO_NO_ENSURE=1 to keep the suite from starting
anything (CI, or a run on a machine that must not spawn a model).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
_WAS_UP = False

#: The suite must not file its own consoles' sessions into the operator's store. `paths.SAVE` honours
#: LYGO_SAVE_DIR, and this runs at import time - before any test module can import `paths` - so a full
#: run leaves the real store exactly as it found it. MEASURED 2026-09-25: a green suite moved
#: save/sessions from 386 files to 412, which is the operator's own complaint (useless session info)
#: being written by the tests themselves. LYGO_TEST_REAL_STORE=1 opts a rare deliberate inspection of
#: the real store back in; a LYGO_SAVE_DIR already set by the caller always wins.
TEST_STORE = Path(__import__("tempfile").gettempdir()) / "lygo-test-store"


def _isolate_store() -> str:
    if os.environ.get("LYGO_TEST_REAL_STORE", "").strip().lower() in {"1", "true", "yes"}:
        return ""
    if os.environ.get("LYGO_SAVE_DIR"):
        return os.environ["LYGO_SAVE_DIR"]
    for sub in ("sessions", "vault"):
        (TEST_STORE / sub).mkdir(parents=True, exist_ok=True)
    os.environ["LYGO_SAVE_DIR"] = str(TEST_STORE)
    return str(TEST_STORE)


TEST_STORE_DIR = _isolate_store()



def pytest_sessionstart(session):  # noqa: ARG001 - pytest's hook signature
    """Remember whether a console was serving when the run began: the suite stops one on purpose, so
    only a console that was up may be put back. A run on a machine that never had one must not spend
    minutes cold-booting a 12B at the end of a green suite."""
    global _WAS_UP
    print("\nstore: a test run files its sessions under " + (TEST_STORE_DIR or "the operator's own store")
          + (" (LYGO_TEST_REAL_STORE set)" if not TEST_STORE_DIR else ""))
    scripts = str(KIT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        import ensure_console

        _WAS_UP = bool(ensure_console.health(ensure_console._read_port(KIT)[0]))
    except Exception:  # noqa: BLE001
        _WAS_UP = False


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001 - pytest's hook signature
    if os.environ.get("LYGO_NO_ENSURE", "").strip().lower() in {"1", "true", "yes"}:
        print("\nconsole: not re-checked (LYGO_NO_ENSURE set)")
        return
    scripts = str(KIT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        import ensure_console

        _ok, line = ensure_console.ensure(KIT, quiet=True, restore_only=not _WAS_UP)
        print("\nconsole: " + line)
    except Exception as exc:  # noqa: BLE001 - never fail a finished suite over the helper
        print(f"\nconsole: not checked ({type(exc).__name__}: {exc})")
