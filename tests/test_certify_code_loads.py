"""D1: a certificate must refuse a tree whose src cannot import."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "scripts"))

from certify_build import check_code_loads  # noqa: E402


class CertifyCodeLoadsTests(unittest.TestCase):
    def test_live_src_compiles(self):
        ok, notes = check_code_loads(KIT)
        self.assertTrue(ok, notes)

    def test_broken_src_is_refused(self):
        tmp = Path(tempfile.mkdtemp(prefix="lygo_cert_d1_"))
        try:
            src = tmp / "src"
            src.mkdir()
            (src / "chat_loop.py").write_text("def broken(\n", encoding="utf-8")
            ok, notes = check_code_loads(tmp)
            self.assertFalse(ok, "D1: certify must not pass unimportable code")
            blob = " ".join(notes)
            self.assertTrue("compile" in blob.lower() or "import" in blob.lower(), notes)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
