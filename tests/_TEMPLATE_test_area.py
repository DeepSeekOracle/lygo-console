"""TEMPLATE - copy this to tests/test_<area>.py and fill it in.

Not collected by pytest on purpose (a leading underscore means "not a test file yet"), so this
template can sit in the suite without ever being run as one. Prove a copy works with:

    C:/Python313/python.exe tests/test_<area>.py          # runs the file on its own
    C:/Python313/python.exe -m pytest tests/test_<area>.py -q

WHAT A TEST IN THIS KIT PINS
    One rule, in the docstring, in the operator's words. If a later edit can break a promise the
    steward relied on (standalone, no daemon, a mapped read root, a license, an owned model), a test
    here is what makes that edit fail loudly instead of quietly changing behaviour. Name the rule in
    the assertion message: the failure has to read like a complaint, not like a diff.

THE FOUR RULES THIS KIT'S TESTS FOLLOW
    1. Never touch the network and never start a daemon. A test that needs a live engine is a
       different kind of test - put it in scripts/ as a runnable probe, not in tests/.
    2. Never write outside a temporary directory. Anything that writes (registry, save/, logs) is
       patched at the module constant it actually reads - see SandboxTests below.
    3. Never read or print a secret. Assert that a token file exists; do not open it.
    4. Read-only on the real machine. If the operator's real files matter (a picture, a file on a
       mapped root), assert on their *existence and handling*, never mutate them.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))   # before importing anything of ours


class SchemaTemplateTests(unittest.TestCase):
    """Pure assertions on what the console tells a model - no engine, no disk."""

    def test_example_the_console_offers_the_tool_you_think_it_does(self) -> None:
        from tools import TOOLS_SCHEMA

        names = [t["function"]["name"] for t in TOOLS_SCHEMA]
        self.assertIn("steward_map", names, "the drives/roots map must stay reachable by name")
        self.assertEqual(len(names), len(set(names)), "a duplicated tool name confuses the picker")


class SandboxTests(unittest.TestCase):
    """Anything that writes gets a temporary SAVE and a temporary registry.

    The kit reads `paths.SAVE` and `registry.REGISTRY_PATH` at call time, so patching both is enough
    to keep a test off the real save/ directory. Patch the module each caller imported, not the
    source module only: registry holds its own binding.
    """

    def _sandbox(self, t: Path):
        import paths
        import registry as reg_mod

        return [
            mock.patch.object(paths, "SAVE", t),
            mock.patch.object(reg_mod, "SAVE", t),
            mock.patch.object(paths, "REGISTRY_PATH", t / "registry.json"),
            mock.patch.object(reg_mod, "REGISTRY_PATH", t / "registry.json"),
        ]

    def test_example_a_write_lands_in_the_sandbox_and_not_in_save(self) -> None:
        import registry as reg_mod

        real_registry = ROOT / "save" / "registry.json"
        before = real_registry.read_bytes() if real_registry.is_file() else None
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            patches = self._sandbox(t)
            for p in patches:
                p.start()
            try:
                reg_mod.save({"models": [{"id": "t:1", "path": str(t / "x.gguf"), "kind": "chat"}]})
                written = (t / "registry.json").is_file()
                loaded = reg_mod.load()
            finally:
                for p in patches:
                    p.stop()
        self.assertTrue(written, "the write did not land in the sandbox")
        self.assertEqual("t:1", (loaded.get("models") or [{}])[0].get("id"))
        after = real_registry.read_bytes() if real_registry.is_file() else None
        self.assertEqual(before, after, "the test wrote to the console's real registry")


class SourceRuleTests(unittest.TestCase):
    """Rules that live in the text of the source, because that is where they get broken."""

    def test_example_no_source_file_launches_a_daemon(self) -> None:
        bad = []
        for path in (ROOT / "src").glob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"^.*subprocess.*ollama.*$", text, re.I | re.M):
                if "Never subprocess" in m.group(0):        # the importer's own contract comment
                    continue
                bad.append("%s: %s" % (path.name, m.group(0).strip()[:80]))
        self.assertEqual([], bad, "the kit must never launch a daemon")

    def test_example_the_operator_environment_is_read_not_assumed(self) -> None:
        """A machine fact (a vault, a root) belongs in the environment or the config, not in code."""
        text = (ROOT / "src" / "server.py").read_text(encoding="utf-8", errors="replace")
        self.assertIn("LYGO_MODELS", text, "the vault must be discoverable from the environment")


class OperatorFileTests(unittest.TestCase):
    """When the rule is about a real file on the machine, skip when there is nothing to check.

    A test that hard-codes one drive fails the other install while the other install is right. Assert
    the *rule*, and make "nothing to check on this tree" a skip rather than a failure.
    """

    def test_example_an_owned_model_lives_inside_a_root_this_kit_scans(self) -> None:
        import registry as reg_mod
        import server

        roots = [str(r).rstrip("\\/").lower() for r in server.default_scan_roots({})]
        reg = reg_mod.load()
        owned = [r for r in (reg.get("models") or []) if str(r.get("source")) == "lygo_vault"]
        if not owned:
            self.skipTest("no vault-owned models on this tree - its own model store is the vault")
        for rec in owned:
            p = Path(str(rec.get("path") or ""))
            if not p.is_file():
                continue                                     # re-added by the next scan; not our rule
            self.assertTrue(any(str(p).lower().startswith(r) for r in roots),
                            "%s lives outside every root this kit scans: %s" % (rec.get("id"), p))


if __name__ == "__main__":
    unittest.main(verbosity=2)
