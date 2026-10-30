"""Port env overrides — the USB CLAW runs beside a desktop console, never on top of it.

Before this, DEFAULT_PORT was a frozen 9641: the stick's LYGO_AGENT_STICK.bat set
LYGO_CONSOLE_PORT=9651 and was silently ignored, so a plugged-in stick fought the desktop
console for 9641 *and* its engine for 11441. Probed in a subprocess so no module reload can
leak into the rest of the suite.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
# The probe names src/ explicitly on purpose. Under an *embedded* distribution the stick ships,
# `python312._pth` disables the `-c` cwd entry in sys.path, so `import paths` only works from the
# directory that actually holds it: a cwd-relative probe passes under C:\Python313 and fails on the
# stick, which is the one place the suite has to hold.
PROBE = (
    f"import sys;sys.path.insert(0,{str(SRC)!r});"
    "import paths;print(paths.DEFAULT_PORT, paths.LLAMA_PORT, paths.EMBED_PORT, paths.COLIBRI_PORT)"
)


def probe(extra: dict[str, str]) -> tuple[str, int]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LYGO_")}
    env.update(extra)
    r = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=str(SRC),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    return (r.stdout or "").strip(), r.returncode


class PortEnvTests(unittest.TestCase):
    def test_defaults_are_the_desktop_ports(self) -> None:
        out, code = probe({})
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "9641 11441 11442 11443")

    def test_stick_env_moves_every_port(self) -> None:
        out, code = probe(
            {
                "LYGO_CONSOLE_PORT": "9651",
                "LYGO_LLAMA_PORT": "11451",
                "LYGO_EMBED_PORT": "11452",
                "LYGO_COLIBRI_PORT": "11453",
            }
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "9651 11451 11452 11453")

    def test_junk_or_out_of_range_falls_back_to_the_default(self) -> None:
        out, code = probe({"LYGO_CONSOLE_PORT": "nine", "LYGO_LLAMA_PORT": "0", "LYGO_EMBED_PORT": "99999"})
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "9641 11441 11442 11443")


if __name__ == "__main__":
    unittest.main()
