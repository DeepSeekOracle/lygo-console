"""Pin the build-line tooling: a seal must be complete and provable, a restore
must refuse an unproven seal, and neither may touch the operator's runtime.

These tests run the real CLI scripts against throwaway fake kits in a temp dir.
They never touch the real vault (<DRIVE_D>\LYGO_CANON) and never touch this kit.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parents[1]
SEAL = KIT_ROOT / "scripts" / "seal_build.py"
RESTORE = KIT_ROOT / "scripts" / "restore_build.py"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class BuildLineTests(unittest.TestCase):
    """seal_build.py / restore_build.py - the revert path."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lygo_buildline_"))
        self.vault = self.tmp / "vault"
        self.kit = self.tmp / "kit"
        (self.kit / "src").mkdir(parents=True)
        (self.kit / "config").mkdir()
        (self.kit / "save").mkdir()
        (self.kit / "data").mkdir()
        (self.kit / "models").mkdir()
        (self.kit / "VERSION").write_text("9.9.9\ntest-anchor 2026-01-01\n", encoding="utf-8")
        (self.kit / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
        (self.kit / "config" / "console.json").write_text('{"console_port": 1}\n', encoding="utf-8")
        # things a seal must never carry
        (self.kit / "config" / "api.json").write_text('{"key": "SECRET"}\n', encoding="utf-8")
        (self.kit / "config" / "local.json").write_text('{"console_port": 2}\n', encoding="utf-8")
        (self.kit / "data" / ".llama_api_key").write_text("SECRET\n", encoding="utf-8")
        (self.kit / "save" / "state.json").write_text('{"operator": "mine"}\n', encoding="utf-8")
        (self.kit / "save" / "registry.json").write_text(
            '{"signature": "reg", "selected": "gemma4-12b"}\n', encoding="utf-8")
        (self.kit / "models" / "big.gguf").write_bytes(b"weights" * 100)
        (self.kit / "workspace").mkdir()
        (self.kit / "workspace" / "SOUL.md").write_text("# soul\n", encoding="utf-8")
        (self.kit / ".gitignore").write_text("save/\n", encoding="utf-8")
        (self.kit / "empty_folder").mkdir()
        os.environ["LYGO_TEST_VAULT"] = str(self.vault)

    def tearDown(self) -> None:
        os.environ.pop("LYGO_TEST_VAULT", None)
        for p in self.vault.rglob("*"):
            try:
                p.chmod(stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers -------------------------------------------------------------
    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )

    def seal(self, name: str = "TEST", kit: Path | None = None) -> tuple[subprocess.CompletedProcess, Path]:
        res = self.run_script(SEAL, "--root", str(kit or self.kit), "--vault", str(self.vault),
                              "--name", name, "--kit", "kit")
        found = sorted(self.vault.glob("*_build-*_%s" % name))
        self.assertTrue(found, "no seal directory produced\n%s\n%s" % (res.stdout, res.stderr))
        return res, found[0]

    # tests ---------------------------------------------------------------
    def test_seal_writes_a_verifiable_hash_list(self) -> None:
        res, seal = self.seal()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("SEALED", res.stdout)
        sums = list(seal.glob("SHA256SUMS-*.txt"))[0]
        lines = [ln for ln in sums.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertGreaterEqual(len(lines), 3, sums.read_text())
        for line in lines:
            digest, rel = line.split(None, 1)
            target = seal / rel.strip()
            self.assertTrue(target.exists(), rel)
            self.assertEqual(sha256(target), digest, rel)
        self.assertTrue((seal / "CANON.md").exists())
        self.assertTrue((seal / "RESTORE.txt").exists())
        self.assertTrue((seal / "_freeze_facts.json").exists())

    def test_seal_omits_keys_runtime_and_models(self) -> None:
        _, seal = self.seal()
        kit_dir = seal / "kit"
        self.assertTrue((kit_dir / "src" / "main.py").exists())
        self.assertFalse((kit_dir / "config" / "api.json").exists(), "seal carried the API key file")
        self.assertFalse((kit_dir / "config" / "local.json").exists(), "seal carried machine-local config")
        self.assertFalse((kit_dir / "data" / ".llama_api_key").exists(), "seal carried the engine key")
        self.assertFalse((kit_dir / "save" / "state.json").exists(), "seal carried operator runtime state")
        self.assertTrue((kit_dir / "save" / "registry.json").exists(),
                        "seal dropped the build's working configuration (boot model)")
        self.assertFalse((kit_dir / "models" / "big.gguf").exists(), "seal carried model weights")

    def test_seal_marks_files_read_only(self) -> None:
        _, seal = self.seal()
        target = seal / "kit" / "src" / "main.py"
        self.assertFalse(os.access(target, os.W_OK), "sealed file is writable - it must be reference only")

    def test_seal_refuses_to_overwrite_an_existing_seal(self) -> None:
        _, seal = self.seal(name="ONCE")
        before = sha256(seal / "kit" / "src" / "main.py")
        res2, seal2 = self.seal(name="ONCE")
        self.assertEqual(seal2, seal)
        self.assertEqual(res2.returncode, 1, res2.stdout)
        self.assertIn("REFUSING", res2.stdout)
        self.assertEqual(sha256(seal / "kit" / "src" / "main.py"), before)

    def test_restore_refuses_a_tampered_seal(self) -> None:
        _, seal = self.seal()
        kit_dir = seal / "kit"
        victim = kit_dir / "src" / "main.py"
        victim.chmod(stat.S_IWRITE | stat.S_IREAD)
        victim.write_text("print('tampered')\n", encoding="utf-8")
        (self.kit / "src" / "main.py").write_text("print('live')\n", encoding="utf-8")
        res = self.run_script(RESTORE, "--from", str(kit_dir), "--root", str(self.kit))
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("REFUSING TO RESTORE", res.stdout)
        self.assertEqual((self.kit / "src" / "main.py").read_text(encoding="utf-8"), "print('live')\n",
                         "a refused restore must not have written anything")

    def test_restore_lays_the_build_back_and_keeps_the_operators_state(self) -> None:
        _, seal = self.seal()
        kit_dir = seal / "kit"
        live = self.kit / "src" / "main.py"
        live.write_text("print('broken by a bad session')\n", encoding="utf-8")
        (self.kit / "save" / "state.json").write_text('{"operator": "newer work"}\n', encoding="utf-8")

        res = self.run_script(RESTORE, "--from", str(kit_dir), "--root", str(self.kit))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("RESTORED 1 file", res.stdout)
        self.assertEqual(live.read_text(encoding="utf-8"), "print('hello')\n", "seal was not laid back")
        self.assertTrue(os.access(live, os.W_OK), "restored file must be writeable in the live tree")
        self.assertEqual((self.kit / "save" / "state.json").read_text(encoding="utf-8"),
                         '{"operator": "newer work"}\n', "restore clobbered operator runtime state")
        self.assertFalse((self.kit / "src" / "main.py").read_text(encoding="utf-8").startswith("print('broken'"))

    def test_restore_dry_run_writes_nothing(self) -> None:
        _, seal = self.seal()
        live = self.kit / "src" / "main.py"
        live.write_text("print('live')\n", encoding="utf-8")
        res = self.run_script(RESTORE, "--from", str(seal / "kit"), "--root", str(self.kit), "--dry-run")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("DRY RUN", res.stdout)
        self.assertEqual(live.read_text(encoding="utf-8"), "print('live')\n")

    def test_seal_writes_a_vault_index(self) -> None:
        self.seal(name="INDEXED")
        index = self.vault / "INDEX.md"
        self.assertTrue(index.exists(), "no vault index written")
        text = index.read_text(encoding="utf-8")
        self.assertIn("INDEXED", text)
        self.assertIn("restore_build.py", text)
        self.assertIn("never edit a sealed build", text.lower())

    def test_seal_records_the_working_configuration_it_carries(self) -> None:
        _, seal = self.seal()
        facts = json.loads((seal / "CANON.json").read_text(encoding="utf-8"))
        self.assertIn("save/registry.json", facts.get("carried_config", []))

    def test_restore_restores_the_carried_config_but_not_other_runtime(self) -> None:
        _, seal = self.seal()
        reg = self.kit / "save" / "registry.json"
        state = self.kit / "save" / "state.json"
        reg.write_text('{"signature": "reg", "selected": "some-other-model"}\n', encoding="utf-8")
        state.write_text('{"operator": "newer"}\n', encoding="utf-8")
        res = self.run_script(RESTORE, "--from", str(seal / "kit"), "--root", str(self.kit))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("gemma4-12b", reg.read_text(encoding="utf-8"),
                      "the boot model was not restored - the revert would not read images")
        self.assertEqual(state.read_text(encoding="utf-8"), '{"operator": "newer"}\n',
                         "restore clobbered runtime state that is not the carried config")
        self.assertTrue(os.access(reg, os.W_OK), "restored config must be writeable")

    def test_seal_carries_the_kits_identity_and_shape(self) -> None:
        _, seal = self.seal()
        kit_dir = seal / "kit"
        self.assertTrue((kit_dir / "workspace" / "SOUL.md").exists(),
                        "seal dropped the console's identity files (workspace/)")
        self.assertTrue((kit_dir / ".gitignore").exists(), "seal dropped the kit's dotfiles")
        self.assertTrue((kit_dir / "empty_folder").is_dir(),
                        "seal dropped an empty folder the build needs")
        self.assertTrue((kit_dir / "models").is_dir(), "seal dropped the kit's own models/ folder")

    def test_restore_rebuilds_the_shape_and_the_identity(self) -> None:
        _, seal = self.seal()
        shutil.rmtree(self.kit / "workspace")
        shutil.rmtree(self.kit / "empty_folder")
        res = self.run_script(RESTORE, "--from", str(seal / "kit"), "--root", str(self.kit))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual((self.kit / "workspace" / "SOUL.md").read_text(encoding="utf-8"), "# soul\n")
        self.assertTrue((self.kit / "empty_folder").is_dir(), "restore did not rebuild an empty folder")

    def test_the_freeze_rule_is_written_down(self) -> None:
        notes = (KIT_ROOT / "BUILD_NOTES.md").read_text(encoding="utf-8").lower()
        self.assertIn("never edit a sealed build", notes)
        self.assertIn("restore_build.py", notes)
        self.assertIn("seal_build.py", notes)


if __name__ == "__main__":
    unittest.main()
