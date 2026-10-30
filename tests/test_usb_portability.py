# -*- coding: utf-8 -*-
"""USB CLAW portability contract.

Three defects this pins down, all of which hurt a stick that walks to another machine:
  1. hard-coded desktop ports (a 9651 stick advertised the 9641 portal)
  2. console.json limits ignored at launch (ngl/threads/ctx came only from hardware auto-detect)
  3. model records for weights that are not on this machine kept being advertised to RAM-auto
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1]
SRC = KIT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import engine  # noqa: E402
import paths  # noqa: E402
import registry  # noqa: E402

HOST_PORT_RE = re.compile(r"(?:127\.0\.0\.1|localhost):(\d{4,5})")
# ollama's own default and the studio's 8080 web apps are not kit ports.
FOREIGN_PORTS = {"11434", "8080"}
SHIPPED = (
    "install.py",
    "lygo_engine.py",
    "runtime_facts.py",
    "public_gateway.py",
    "server.py",
    "paths.py",
)


class PortsHaveOneSourceTest(unittest.TestCase):
    def test_shipped_modules_hardcode_no_host_port(self):
        offenders = []
        for name in SHIPPED:
            p = SRC / name
            if not p.is_file():
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                for m in HOST_PORT_RE.finditer(line):
                    if m.group(1) not in FOREIGN_PORTS:
                        offenders.append(f"{name}:{i}: {line.strip()[:70]}")
        self.assertEqual([], offenders, "hard-coded host:port outside paths.py")

    def test_engine_status_reports_the_stick_portal(self):
        """The kit's own status feed must speak the port the stick is bound to."""
        env = {**os.environ, "LYGO_CONSOLE_PORT": "9651", "LYGO_LLAMA_PORT": "11451"}
        code = "import json,lygo_engine;print(json.dumps(lygo_engine.status()['ports']))"
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(SRC),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(0, out.returncode, out.stderr[-500:])
        ports = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(9651, ports["portal"])
        self.assertEqual(11451, ports["llama"])


class ConfigLimitsTest(unittest.TestCase):
    def setUp(self):
        self._cache = paths._CFG_CACHE

    def tearDown(self):
        paths._CFG_CACHE = self._cache

    def test_console_limits_reads_config_even_at_zero(self):
        paths._CFG_CACHE = {"ngl": 0, "threads": 4, "ctx_default": 4096, "ctx_max": 8192}
        lim = paths.console_limits()
        self.assertEqual(0, lim["ngl"], "CPU-only is a real choice, not an absent value")
        self.assertEqual(4, lim["threads"])
        self.assertEqual(8192, lim["ctx_max"])
        self.assertEqual("console.json", lim["source"])

    def test_plan_lets_config_beat_the_hardware_auto_plan(self):
        import lygo_engine

        paths._CFG_CACHE = {"ngl": 0, "threads": 4, "ctx_default": 4096, "ctx_max": 8192}
        pl = lygo_engine.plan({"id": "qwen2.5:3b", "path": "x.gguf", "bytes": 1000, "kind": "chat"})
        self.assertEqual(0, pl["llama"]["ngl"])
        self.assertEqual(4, pl["llama"]["threads"])
        self.assertEqual(8192, pl["limits"]["ctx_max"])
        self.assertEqual("console.json", pl["limits"]["source"])

    def test_ctx_and_threads_clamps_follow_config(self):
        paths._CFG_CACHE = {"ctx_default": 2048, "ctx_max": 4096, "threads": 4}
        self.assertEqual(2048, engine.clamp_ctx(None))
        self.assertEqual(4096, engine.clamp_ctx(32768))
        self.assertEqual(4096, engine.clamp_ctx(32768, ctx_max=4096))
        self.assertEqual(4, engine.clamp_threads(4))
        self.assertEqual(4, engine.clamp_threads(None))
        self.assertEqual(2, engine.clamp_threads(1))

    def test_a_chat_brain_with_a_tiny_native_window_is_raised_to_ctx_default(self):
        """Defect 126: nomic/2048 as the chat window emptied a ~10k photo turn."""
        paths._CFG_CACHE = {"ctx_default": 32768, "ctx_max": 32768}
        self.assertEqual(
            32768,
            engine.clamp_ctx(2048, architecture="qwen35", kind="chat", model_id="MiMo V2.6 Distill Qwen 9B"),
        )
        self.assertEqual(32768, engine.clamp_ctx(8192, kind="chat"))
        self.assertEqual(
            2048,
            engine.clamp_ctx(2048, architecture="nomic-bert", kind="embed", model_id="nomic-embed-text:latest"),
        )
        self.assertEqual(32768, engine.clamp_ctx(262144, architecture="qwen35", kind="chat"))

    def test_live_ctx_does_not_use_an_embedder_pin(self):
        """The chat turn window is the chat brain, even when registry.selected is nomic."""
        import compaction

        embed = {
            "id": "nomic-embed-text:latest",
            "kind": "embed",
            "architecture": "nomic-bert",
            "ctx": 2048,
        }
        chat = {
            "id": "MiMo V2.6 Distill Qwen 9B",
            "kind": "chat",
            "architecture": "qwen35",
            "ctx": 262144,
        }
        paths._CFG_CACHE = {"ctx_default": 32768, "ctx_max": 32768}

        class _Reg:
            @staticmethod
            def load():
                return {"selected": embed["id"], "models": [embed, chat]}

        with patch.object(compaction, "_selected_record", return_value=embed), patch.dict(
            sys.modules, {"registry": _Reg}
        ):
            self.assertEqual(32768, compaction.live_ctx())


class RegistryPortabilityTest(unittest.TestCase):
    def _isolate(self, td: str):
        self._saved = (registry.REGISTRY_PATH, registry.SAVE, registry.prefer_by_ram)
        registry.REGISTRY_PATH = Path(td) / "registry.json"
        registry.SAVE = Path(td)
        registry.prefer_by_ram = lambda: False

    def _restore(self):
        registry.REGISTRY_PATH, registry.SAVE, registry.prefer_by_ram = self._saved

    def test_weights_that_left_the_machine_are_not_advertised(self):
        with tempfile.TemporaryDirectory() as td:
            here = Path(td) / "qwen2.5-3b.gguf"
            here.write_bytes(b"GGUF" + b"\0" * 128)
            self._isolate(td)
            try:
                registry.upsert(
                    [
                        {"id": "usb:qwen2.5:3b", "path": str(here), "bytes": 100, "kind": "chat", "runnable": True},
                        {
                            "id": "host:phi4:14b",
                            "path": str(Path(td) / "gone.gguf"),
                            "bytes": 200,
                            "kind": "chat",
                            "runnable": True,
                        },
                    ]
                )
                ids = {m["id"] for m in registry.load()["models"]}
            finally:
                self._restore()
            self.assertIn("usb:qwen2.5:3b", ids)
            self.assertNotIn("host:phi4:14b", ids)

    def test_pinned_brain_that_left_the_machine_is_repicked(self):
        with tempfile.TemporaryDirectory() as td:
            here = Path(td) / "qwen2.5-3b.gguf"
            here.write_bytes(b"GGUF" + b"\0" * 128)
            self._isolate(td)
            try:
                registry.upsert(
                    [
                        {"id": "usb:qwen2.5:3b", "path": str(here), "bytes": 100, "kind": "chat", "runnable": True},
                        {
                            "id": "host:phi4:14b",
                            "path": str(Path(td) / "gone.gguf"),
                            "bytes": 200,
                            "kind": "chat",
                            "runnable": True,
                        },
                    ],
                    selected="host:phi4:14b",
                )
                data = registry.load()
            finally:
                self._restore()
            self.assertEqual("usb:qwen2.5:3b", data["selected"])
            self.assertNotEqual("host:phi4:14b", data["selected"])


if __name__ == "__main__":
    unittest.main()
