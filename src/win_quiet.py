"""Keep every child process of the Forge invisible on Windows.

A console child of a windowless server opens its own terminal window. Measured 2026-10-30:
one window per opencode dispatch, landing on top of whatever Justin was doing. This module
patches ``subprocess.Popen`` once so every child process gets ``CREATE_NO_WINDOW``.

Import it before anything that spawns a process. It is a no-op off Windows and safe to call
more than once.
"""
from __future__ import annotations

import os
import subprocess

CREATE_NO_WINDOW = 0x08000000
_MARK = "_lygo_quiet_installed"


def install() -> bool:
    """Patch Popen in this process. Returns True only when the patch was applied now."""
    if os.name != "nt":
        return False
    if getattr(subprocess.Popen, _MARK, False):
        return False

    original_init = subprocess.Popen.__init__

    def quiet_init(self, *args, **kwargs):
        kwargs["creationflags"] = int(kwargs.get("creationflags") or 0) | CREATE_NO_WINDOW
        original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = quiet_init
    setattr(subprocess.Popen, _MARK, True)
    return True


if __name__ == "__main__":
    print("installed" if install() else "already installed or unsupported platform")
