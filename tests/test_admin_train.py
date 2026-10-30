from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from admin_map import brief, is_placeholder_url  # noqa: E402
from chat_loop import host_prefetch  # noqa: E402
from continuity import compose_system  # noqa: E402
from tools import dispatch  # noqa: E402


class AdminTrainTests(unittest.TestCase):
    def test_placeholder_urls(self):
        self.assertTrue(is_placeholder_url("https://github.com/user/repo/path/to/file"))
        self.assertTrue(is_placeholder_url("https://lattice.example.com/path/to/file"))
        self.assertTrue(is_placeholder_url("https://huggingface.co/models/transformers"))
        self.assertFalse(is_placeholder_url("https://github.com/DeepSeekOracle/chatagent"))

    def test_web_fetch_rejects_placeholder(self):
        r = dispatch("web_fetch", {"url": "https://github.com/user/repo"})
        self.assertFalse(r.get("ok"))
        self.assertEqual(r.get("error"), "placeholder_url")
        self.assertIn("DeepSeekOracle", str(r.get("map")))

    def test_steward_map(self):
        r = dispatch("steward_map", {})
        self.assertTrue(r.get("ok"))
        self.assertIn("DeepSeekOracle", str(r.get("github_org")))
        self.assertIn("huggingface.co/DeepSeekOracle", str(r.get("hf_org")))

    def test_find_soul(self):
        r = dispatch("find_files", {"pattern": "SOUL.md", "root": str(ROOT / "workspace")})
        self.assertTrue(r.get("ok"))
        self.assertTrue(any(p.endswith("SOUL.md") for p in (r.get("hits") or [])))

    def test_credential_where_redacted(self):
        r = dispatch("credential_where", {"q": "gitea"})
        self.assertTrue(r.get("ok"))
        blob = str(r)
        self.assertNotRegex(blob, r"(?i)password\s*[:=]\s*\S+")
        for row in r.get("pointers") or []:
            self.assertTrue(row.get("redacted"))
            self.assertIn("path", row)
            self.assertNotIn("secret", row)
            self.assertNotIn("content", row)

    def test_system_has_brief(self):
        txt = compose_system()
        self.assertIn("DeepSeekOracle", txt)
        self.assertIn("steward_map", txt)

    def test_host_prefetch_github(self):
        traces = host_prefetch("can you manage our lattice and webpages")
        names = [t.get("name") for t in traces]
        self.assertIn("steward_map", names)


if __name__ == "__main__":
    unittest.main()
