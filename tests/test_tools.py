from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tools import dispatch  # noqa: E402


class ToolsTests(unittest.TestCase):
    def test_source_forbid(self):
        src = (ROOT / "src" / "tools.py").read_text(encoding="utf-8")
        for bad in ("LYGO_SERVER_KEYS", "Data Vault", r"<USER>", "run_cmd"):
            self.assertNotIn(bad, src)

    def test_llama_key_denied(self):
        p = ROOT / "data" / ".llama_api_key"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("secret-key", encoding="utf-8")
        r = dispatch("read_file", {"path": str(p)})
        self.assertFalse(r.get("ok"))
        self.assertEqual(r.get("error"), "denied")

    def test_workspace_write_read(self):
        r = dispatch("write_file", {"path": "hello.txt", "content": "lattice"})
        self.assertTrue(r.get("ok"))
        r2 = dispatch("read_file", {"path": "hello.txt"})
        self.assertTrue(r2.get("ok"))
        self.assertIn("lattice", r2.get("text", ""))

    def test_alias_read(self):
        r = dispatch("read", {"path": "hello.txt"})
        self.assertTrue(r.get("ok") or r.get("error") in {"not_file", "denied"})


if __name__ == "__main__":
    unittest.main()
