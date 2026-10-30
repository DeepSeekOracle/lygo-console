from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lygo_engine import plan  # noqa: E402
import paths  # noqa: E402


class LygoEnginePlanTests(unittest.TestCase):
    def test_gguf_uses_llama_mmap(self):
        rec = {"id": "qwen", "path": "x.gguf", "kind": "chat", "architecture": "qwen2"}
        with patch("lygo_engine.probe", return_value={
            "ram_bytes": 32 * 1024**3,
            "ram_gib": 32,
            "vram_bytes": 8 * 1024**3,
            "vram_gib": 8,
            "threads": 8,
            "llama_binary": True,
            "ssd_stream": True,
        }):
            with patch.object(paths, "_CFG_CACHE", {}):  # config silent -> the hardware plan decides
                p = plan(rec)
        self.assertEqual(p["backend"], "llama")
        self.assertTrue(p["llama"]["mmap"])
        self.assertGreaterEqual(p["llama"]["ngl"], 20)
        self.assertEqual(p["port"], 11441)

    def test_kit_config_beats_the_hardware_auto_plan(self):
        """An explicit ngl/threads is a pin, 0 included — the shipped config now says "auto"."""
        rec = {"id": "qwen", "path": "x.gguf", "kind": "chat", "architecture": "qwen2"}
        with patch("lygo_engine.probe", return_value={
            "ram_bytes": 32 * 1024**3,
            "ram_gib": 32,
            "vram_bytes": 8 * 1024**3,
            "vram_gib": 8,
            "threads": 8,
            "llama_binary": True,
            "ssd_stream": True,
        }):
            with patch.object(paths, "_CFG_CACHE", {"ngl": 0, "threads": 4, "ctx_max": 8192}):
                p = plan(rec)
        self.assertEqual(p["llama"]["ngl"], 0)
        self.assertEqual(p["llama"]["threads"], 4)
        self.assertEqual(p["limits"]["source"], "console.json")

    def test_colibri_dir_uses_coli_backend(self):
        rec = {"id": "coli-glm", "path": "/models/glm", "kind": "colibri", "engine": "colibri"}
        with patch("lygo_engine.probe", return_value={
            "ram_bytes": 24 * 1024**3,
            "ram_gib": 24,
            "vram_bytes": 0,
            "vram_gib": 0,
            "threads": 6,
            "llama_binary": True,
            "ssd_stream": True,
        }):
            p = plan(rec)
        self.assertEqual(p["backend"], "colibri")
        self.assertGreaterEqual(p["colibri"]["pin_gib"], 2)
        self.assertEqual(p["port"], 11443)


if __name__ == "__main__":
    unittest.main()
