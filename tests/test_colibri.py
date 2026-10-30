from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from colibri import looks_like_colibri_model, model_card, resolve_coli  # noqa: E402


class ColibriMapTests(unittest.TestCase):
    def test_missing_launcher_is_ok(self):
        # GamePC may or may not have coli; the function must not throw.
        p = resolve_coli()
        self.assertTrue(p is None or p.is_file())

    def test_detect_hf_dir(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "config.json").write_text(
                json.dumps({"model_type": "deepseek_v3", "architectures": ["DeepseekV3ForCausalLM"]}),
                encoding="utf-8",
            )
            (d / "model.safetensors.index.json").write_text("{}", encoding="utf-8")
            self.assertTrue(looks_like_colibri_model(d))
            card = model_card(d)
            self.assertEqual(card["kind"], "colibri")
            self.assertEqual(card["engine"], "colibri")
            self.assertIn("coli-", card["id"])

    def test_plain_folder_not_colibri(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "readme.txt").write_text("no", encoding="utf-8")
            self.assertFalse(looks_like_colibri_model(d))


if __name__ == "__main__":
    unittest.main()
