from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gguf_header import parse_gguf_header, write_tiny_gguf  # noqa: E402
from cas_import import import_cas_tree  # noqa: E402
from scanner import scan_roots  # noqa: E402


class GgufTests(unittest.TestCase):
    def setUp(self):
        self.fix = ROOT / "tests" / "fixtures"
        self.fix.mkdir(parents=True, exist_ok=True)
        self.tiny = self.fix / "tiny.gguf"
        write_tiny_gguf(self.tiny)

    def test_tiny_header(self):
        h = parse_gguf_header(self.tiny)
        self.assertEqual(h["name"], "tiny")
        self.assertEqual(h["architecture"], "llama")
        self.assertFalse(h["meta_truncated"])
        self.assertEqual(h["kind"], "chat")
        self.assertEqual(h["status"], "ok")

    def test_cas_qwen_id(self):
        tree = self.fix / "ollama_tree"
        man_dir = tree / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5"
        man_dir.mkdir(parents=True, exist_ok=True)
        digest = "sha256:5ee4f07cdb9beadbbb293e85803c569b01bd37ed059d2715faa7bb405f31caa6"
        blob_name = digest.replace("sha256:", "sha256-")
        blobs = tree / "blobs"
        blobs.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.tiny, blobs / blob_name)
        man = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "config": {"mediaType": "application/vnd.docker.container.image.v1+json", "digest": "sha256:dead", "size": 1},
            "layers": [
                {"mediaType": "application/vnd.ollama.image.model", "digest": digest, "size": self.tiny.stat().st_size}
            ],
        }
        (man_dir / "3b").write_text(json.dumps(man), encoding="utf-8")
        recs = import_cas_tree(tree)
        ids = [r["id"] for r in recs]
        self.assertIn("qwen2.5:3b", ids)
        rec = next(r for r in recs if r["id"] == "qwen2.5:3b")
        self.assertTrue(rec["runnable"])

    def test_blob_missing(self):
        tree = self.fix / "ollama_missing"
        man_dir = tree / "manifests" / "registry.ollama.ai" / "library" / "ghost"
        man_dir.mkdir(parents=True, exist_ok=True)
        (tree / "blobs").mkdir(parents=True, exist_ok=True)
        man = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "config": {"mediaType": "application/vnd.docker.container.image.v1+json", "digest": "sha256:x", "size": 1},
            "layers": [
                {"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:00" * 32, "size": 99}
            ],
        }
        (man_dir / "latest").write_text(json.dumps(man), encoding="utf-8")
        recs = import_cas_tree(tree)
        self.assertEqual(recs[0]["status"], "blob_missing")
        self.assertFalse(recs[0]["runnable"])

    def test_scan_tiny(self):
        r = scan_roots([str(self.tiny.parent)])
        self.assertTrue(any(m.get("id") == "tiny" for m in r["models"]))


if __name__ == "__main__":
    unittest.main()
