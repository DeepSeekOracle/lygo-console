from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT.parents[0] / "tools"))

# Portable kits (the USB stick, someone else's machine) have no stack folder next to them, so
# the packer is looked up in every place it can legitimately live before skipping the check.
PACKER_CANDIDATES = [
    ROOT.parents[0] / "tools" / "pack_lygo_llm_console_public.py",
    ROOT.parents[0] / "stack" / "lygo-protocol-stack" / "tools" / "pack_lygo_llm_console_public.py",
    ROOT / "tools" / "make_public_build.py",
]


class PublicInstallTests(unittest.TestCase):
    def test_bat_is_portable(self):
        bat = (ROOT / "LYGO_LLM_CONSOLE.bat").read_text(encoding="utf-8")
        self.assertIn("%~dp0", bat)
        self.assertNotIn(r"<EDRIVE>", bat)
        self.assertTrue((ROOT / "INSTALL.bat").is_file())

    def test_prompts_are_public_seeds(self):
        soul = (ROOT / "prompts" / "SOUL.md").read_text(encoding="utf-8")
        self.assertIn("light math", soul.lower())
        self.assertNotIn("GamePC", soul)
        self.assertNotIn("LYGO_SERVER_KEYS", soul)
        ident = (ROOT / "prompts" / "IDENTITY.md").read_text(encoding="utf-8")
        self.assertIn("the operator at this machine", ident.lower())
        self.assertNotIn("LYGO_SERVER_KEYS", ident)

    def test_install_skips_admin_tree(self):
        import install

        self.assertTrue(install.is_admin_tree())
        r = install.seed_identity()
        self.assertEqual(r, ["skip_admin_tree"])

    def test_pack_script_skips_admin(self):
        packs = [p for p in PACKER_CANDIDATES if p.is_file()]
        if not packs:
            self.skipTest("no public packer next to this kit (portable stick without a stack folder)")
        text = "\n".join(p.read_text(encoding="utf-8") for p in packs)
        self.assertIn("admin.json", text)
        self.assertTrue((ROOT / "INSTALL.bat").is_file())
        self.assertIn("seed_identity", (ROOT / "src" / "install.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
