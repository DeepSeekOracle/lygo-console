from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from engine import binary_forbidden  # noqa: E402


class EngineTests(unittest.TestCase):
    def test_refuse_nested_ollama(self):
        p = Path(r"U:\LYGO\projects\ollama\lib\ollama\llama-server.exe")
        self.assertTrue(binary_forbidden(p))

    def test_allow_kit_engine(self):
        p = Path(r"U:\LYGO\projects\lygo-llm\engine\llama-server.exe")
        self.assertFalse(binary_forbidden(p))


if __name__ == "__main__":
    unittest.main()
