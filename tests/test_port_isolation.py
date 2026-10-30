"""Port isolation between copies of the kit.

A second copy (a packaged hub, the USB stick) must be able to run beside the original. That
breaks silently if a per-machine override file pins the ORIGINAL's ports: the copy would bind
the same sockets and the two consoles would fight for them, which only shows up when both are
started at once. These tests pin the mechanism that keeps them apart.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
CONFIG = KIT / "config"
PY = sys.executable

COPY_PORTS = {"port": 9741, "llama_port": 11541, "embed_port": 11542, "colibri_port": 11543}


def _fresh_copy(tmp: Path, ports: dict, seed_local: bool = True) -> Path:
    """A throwaway copy of the config machinery, as a first boot of the kit would leave it."""
    root = tmp / "copy"
    (root / "config").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "tools").mkdir()
    shutil.copy2(KIT / "src" / "paths.py", root / "src" / "paths.py")
    shutil.copy2(KIT / "tools" / "resolve_ports.py", root / "tools" / "resolve_ports.py")
    cfg = dict(ports, bind="127.0.0.1", scan_roots=["./models"])
    (root / "config" / "console.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    if seed_local:
        # exactly what src/install.py does on first boot
        shutil.copy2(CONFIG / "local.json.example", root / "config" / "local.json")
    return root


def _lines(out: str) -> dict:
    return dict(l.split("=", 1) for l in out.strip().splitlines() if "=" in l)


class PortIsolation(unittest.TestCase):
    def test_example_does_not_pin_concrete_ports(self):
        ex = json.loads((CONFIG / "local.json.example").read_text(encoding="utf-8"))
        self.assertEqual(
            ex.get("ports") or {}, {},
            "local.json.example must not ship a concrete ports block: install.py copies it to "
            "config/local.json on first boot, and local.json beats console.json, so every copy "
            "would pin itself to the original's ports",
        )

    def test_seeded_copy_keeps_its_own_console_ports(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fresh_copy(Path(td), COPY_PORTS)
            code = (
                "import json,sys;sys.path.insert(0, %r);"
                "import paths;print(json.dumps(paths.console_cfg()))" % str(root / "src")
            )
            r = subprocess.run([PY, "-c", code], capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            cfg = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertEqual(cfg.get("port"), 9741,
                             "a fresh copy must keep its console.json port, not the seeded example's")

    def test_resolver_follows_console_json_then_env_wins(self):
        with tempfile.TemporaryDirectory() as td:
            root = _fresh_copy(Path(td), COPY_PORTS)
            script = str(root / "tools" / "resolve_ports.py")
            r = subprocess.run([PY, script], capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            got = _lines(r.stdout)
            self.assertEqual(got.get("console"), "9741")
            self.assertEqual(got.get("llama"), "11541")
            self.assertEqual(got.get("embed"), "11542")
            self.assertEqual(got.get("colibri"), "11543")
            r2 = subprocess.run([PY, script], capture_output=True, text=True, timeout=120,
                                env=dict(os.environ, LYGO_CONSOLE_PORT="9999"))
            self.assertEqual(_lines(r2.stdout).get("console"), "9999",
                             "an exported LYGO_* must beat both config files")

    def test_launchers_resolve_through_the_kit_reader(self):
        for name in ("LYGO_LLM_CONSOLE.bat", "LYGO_LLM_CONSOLE_STOP.bat"):
            t = (KIT / name).read_text(encoding="utf-8", errors="replace")
            self.assertIn("resolve_ports.py", t,
                          f"{name} must resolve its ports through tools/resolve_ports.py")
            self.assertNotIn("LYGO_PORTFILE", t,
                             f"{name} must not go back to the temp-file round trip that silently "
                             "returned an empty port set")


if __name__ == "__main__":
    unittest.main()
