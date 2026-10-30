#!/usr/bin/env python3
"""Resolve THIS copy's port set for the batch launchers. One line per value.

Prints KEY=VALUE (console / llama / embed / colibri / bind) so
LYGO_LLM_CONSOLE.bat and LYGO_LLM_CONSOLE_STOP.bat can read it with
`for /f "usebackq tokens=1,* delims=="`.

Precedence - the one the kit documents, so nothing has to guess:
    exported LYGO_*_PORT   (what src/paths.py actually reads)
  > config/local.json      (per machine, gitignored)
  > config/console.json    (shipped defaults)
  > the app default in src/paths.py

One reader for the whole kit: the launcher, the server and the kill sweep can never
disagree about which ports this copy owns, which is how two servers end up fighting
over one port. Never raises - a copy with a torn config still starts.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

try:
    from paths import COLIBRI_PORT, DEFAULT_PORT, EMBED_PORT, LLAMA_PORT, console_cfg
    _CFG = console_cfg()
except Exception:  # never block a launcher on a broken import
    _CFG = {}
    DEFAULT_PORT, LLAMA_PORT, EMBED_PORT, COLIBRI_PORT = 9641, 11441, 11442, 11443

# (printed name, config key, env override, app default)
KEYS = (
    ("console", "port", "LYGO_CONSOLE_PORT", DEFAULT_PORT),
    ("llama", "llama_port", "LYGO_LLAMA_PORT", LLAMA_PORT),
    ("embed", "embed_port", "LYGO_EMBED_PORT", EMBED_PORT),
    ("colibri", "colibri_port", "LYGO_COLIBRI_PORT", COLIBRI_PORT),
)


def _ok(v: object) -> int | None:
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if 0 < n < 65536 else None


def resolve(env: str, key: str, default: int) -> tuple[int, str]:
    """Return (port, where it came from)."""
    n = _ok(os.environ.get(env, ""))
    if n is not None:
        return n, "env"
    n = _ok(_CFG.get(key)) if isinstance(_CFG, dict) else None
    if n is not None:
        return n, "config"
    return default, "default"


def main() -> int:
    for name, key, env, default in KEYS:
        port, _src = resolve(env, key, default)
        print(f"{name}={port}")
    bind = ""
    if isinstance(_CFG, dict):
        bind = str(_CFG.get("bind") or "").strip()
    print(f"bind={bind or '127.0.0.1'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
