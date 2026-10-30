from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from paths import stack_root


def run_stack_health() -> dict[str, Any]:
    root = stack_root()
    if root is None:
        return {"ok": False, "error": "stack_missing"}
    stack_dir = root / "stack"
    if not (stack_dir / "lygo_stack.py").is_file():
        return {"ok": False, "error": "stack_missing"}
    code = (
        "from lygo_stack import deploy_stack\n"
        "r = deploy_stack().demo_cycle()\n"
        "print('demo_cycle_return_type=' + type(r).__name__, flush=True)\n"
    )
    env = {k: os.environ[k] for k in ("PATH", "SystemRoot", "COMSPEC", "PATHEXT", "TEMP", "TMP", "PYTHONHOME") if k in os.environ}
    env["PYTHONPATH"] = str(stack_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        p = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(stack_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    if p.returncode != 0:
        return {"ok": False, "error": (p.stderr or p.stdout)[-400:]}
    return {"ok": True, "out": (p.stdout or "")[-400:]}
