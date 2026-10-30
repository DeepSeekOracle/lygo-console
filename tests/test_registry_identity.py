"""One weights file is ONE record - even when the same weights arrive by two routes.

Measured on this machine 2026-09-20: the choice box listed the same 7.38 GB of gemma-4-12B weights
twice, because the legacy CAS names a blob by its content hash (sha256-1278394b...) while the plain
store copy carries that same hash in its ``sha256`` field. Path-equality could not see it.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import registry  # noqa: E402

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "tests"))
try:
    from _stick_profile import on_a_stick
except Exception:  # noqa: BLE001
    def on_a_stick() -> bool:
        return False

H = "1278394b693672ac2799eadc9a83fd98259a6a88a40acfb1dcaa6c6fc895a606"


class FileIdentityTests(unittest.TestCase):
    def test_a_cas_blob_and_a_plain_copy_of_one_file_are_one_model(self):
        recs = [
            {"id": "gemma4-12b", "path": r"<MODELS>\gemma4-12b.gguf", "sha256": H, "caps": ["text"]},
            {"id": "gemma4:12b", "path": r"D:\VAULT\cas\blobs\sha256-" + H, "source": "legacy_cas"},
        ]
        out = registry.dedupe_by_file(recs)
        self.assertEqual(len(out), 1, "one file must not be two records")
        self.assertIn("gemma4:12b", out[0].get("also_known_as") or [],
                      "the other name must survive as an alias")

    def test_two_different_files_stay_two_models(self):
        recs = [
            {"id": "gemma4-12b", "path": r"I:\M\gemma4-12b.gguf", "sha256": H},
            {"id": "gemma-4-12B-it-qat-UD-Q4_K_XL", "path": r"D:\V\runnable\qat.gguf",
             "sha256": "ff1d1fc78170d787ee1201778e2dd65ea211654ca5fb7d69b5a2e7b123a50373"},
        ]
        self.assertEqual(len(registry.dedupe_by_file(recs)), 2)

    def test_a_record_with_no_hash_falls_back_to_its_path(self):
        a = {"id": "x", "path": r"I:\M\x.gguf"}
        b = {"id": "y", "path": r"I:\M\x.gguf"}
        self.assertEqual(len(registry.dedupe_by_file([a, b])), 1)


class VisionPairingTests(unittest.TestCase):
    def test_qat_stem_mmproj_is_found_beside_the_weights(self):
        """Unsloth QAT Gemma ships mmproj-F32.gguf; the console only attaches <stem>-mmproj.gguf."""
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            weights = folder / "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf"
            proj = folder / "gemma-4-12B-it-qat-UD-Q4_K_XL-mmproj.gguf"
            weights.write_bytes(b"GGUF")
            proj.write_bytes(b"GGUF")
            (folder / "mmproj-F32.gguf").write_bytes(b"GGUF")
            rec = {"id": "gemma-4-12B-it-qat-UD-Q4_K_XL", "path": str(weights), "kind": "chat"}
            got = registry.mmproj_for(rec)
            self.assertEqual(got, proj)

    def test_mimo_stem_mmproj_is_found_beside_the_weights(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            weights = folder / "MiMo-V2.6-Distill-Qwen-9B-Q4_K_M.gguf"
            proj = folder / "MiMo-V2.6-Distill-Qwen-9B-Q4_K_M-mmproj.gguf"
            weights.write_bytes(b"GGUF")
            proj.write_bytes(b"GGUF")
            rec = {"id": "MiMo V2.6 Distill Qwen 9B", "path": str(weights), "kind": "chat"}
            self.assertEqual(registry.mmproj_for(rec), proj)


@unittest.skipIf(on_a_stick(), "PC prefer_ids: stick console.json is owned by the stick")
class ShippedVisionBrainsTests(unittest.TestCase):
    def test_pc_prefer_ids_list_both_stable_vision_builds(self):
        shipped = json.loads((KIT / "config" / "console.json").read_text(encoding="utf-8"))
        ids = shipped.get("prefer_ids") or []
        self.assertIn("gemma-4-12B-it-qat-UD-Q4_K_XL", ids)
        self.assertIn("MiMo V2.6 Distill Qwen 9B", ids)
        self.assertIn("gemma4-12b", ids)


if __name__ == "__main__":
    unittest.main()
