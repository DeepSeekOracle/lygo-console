"""lygo.guardwatch — the module contract, the no-secret guarantee, and the honesty gate.

Two of these tests are the whole point of the module: that it never opens a credential file, and
that a check which could not run is never folded into green.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULES_DIR = ROOT / "src" / "modules"
MID = "lygo.guardwatch"
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # the stick/PC profile helper
from _stick_profile import on_a_stick, stick_why  # noqa: E402

from modules import validate  # noqa: E402

LADDER = ("PROPOSED", "SPEC'D", "SCAFFOLDED", "WIRED", "TESTED", "PARITY", "RELEASED", "SEALED")

_WRITES = re.compile(
    r"\.write_text\(|\.write_bytes\(|atomic_write|\.mkdir\(|os\.makedirs\(|os\.remove\(|os\.unlink\("
    r"|os\.replace\(|shutil\.|\.unlink\(|tempfile\."
)

#: A finder reports; it must never open a credential. Reads of .gitignore / .git/config are allowed
#: (they are policy files), so the ban is on the FILES the card declared as credentials.
_FORBIDDEN_READS = re.compile(r"credential_pointers[^\n]*read|read_text\([^\n]*(pass|\.key|\.pem|token)")


def _load(name: str):
    path = MODULES_DIR / name / "backend.py"
    spec = importlib.util.spec_from_file_location("_mod_" + name.replace(".", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class GuardwatchContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads((MODULES_DIR / MID / "module.json").read_text(encoding="utf-8"))
        cls.backend = _load(MID)
        cls.source = (MODULES_DIR / MID / "backend.py").read_text(encoding="utf-8")

    def test_manifest_passes_the_validator(self) -> None:
        problems = validate.validate_manifest(self.manifest, MODULES_DIR / MID, MID)
        self.assertEqual(problems, [], f"the host would REFUSE this module: {problems}")

    def test_family_matches_the_id_suffix(self) -> None:
        self.assertTrue(MID.endswith(self.manifest["family"]))
        self.assertEqual(self.manifest["family"], "watch", "a finder, so its green means 'checked and clean'")

    def test_summary_is_one_line_within_the_ceiling(self) -> None:
        summary = self.manifest["summary"]
        self.assertNotIn("\n", summary)
        self.assertLessEqual(len(summary), 200, f"summary is {len(summary)} chars, the validator caps at 200")

    def test_lifecycle_is_on_the_ladder(self) -> None:
        self.assertIn(self.manifest["lifecycle"], LADDER)

    def test_surfaces_name_all_three_editions_with_a_reason_when_not_full(self) -> None:
        surfaces = self.manifest["surfaces"]
        self.assertEqual(set(surfaces), {"pc", "usb", "web"})
        for edition, value in surfaces.items():
            if value != "FULL":
                self.assertRegex(value, r"^(DEGRADED|N/A)\(.+\)$", f"{edition} must carry a written reason")

    def test_declared_routes_match_what_register_returns(self) -> None:
        reg = self.backend.register(None)
        declared = {(r["method"], r["path"]) for r in self.manifest["routes"]}
        registered = {(m, p) for m, p, _fn in reg["routes"]}
        self.assertEqual(declared, registered)
        self.assertEqual(registered, set(self.backend.ROUTES))

    def test_it_declares_no_limbs(self) -> None:
        """A finder must not hand the model a verb it did not have before."""
        self.assertEqual(self.manifest["limbs"], [])
        self.assertEqual(self.backend.register(None)["limbs"], [])

    def test_pane_id_is_slot_prefixed_and_the_slot_agrees(self) -> None:
        panes = self.manifest["panes"]
        self.assertEqual(len(panes), 1)
        self.assertEqual(panes[0]["id"], "dock.guard")
        self.assertTrue(panes[0]["id"].startswith(panes[0]["slot"] + "."))
        self.assertFalse(panes[0]["inner_scroll"])

    def test_it_owns_no_state_and_opens_no_gate(self) -> None:
        self.assertEqual(self.manifest["state"]["owns"], [])
        self.assertEqual(self.manifest["state"]["gate"], "none")


class GuardwatchNoSecretTests(unittest.TestCase):
    """The hard rule: a finder of credentials may never itself touch one."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = _load(MID)
        cls.source = (MODULES_DIR / MID / "backend.py").read_text(encoding="utf-8")

    def test_the_module_source_contains_no_write_calls(self) -> None:
        hits = [m.group(0) for m in _WRITES.finditer(self.source)]
        self.assertEqual(hits, [], f"a finder fixes nothing, found: {sorted(set(hits))}")

    def test_the_module_never_reads_a_declared_credential(self) -> None:
        hits = [m.group(0) for m in _FORBIDDEN_READS.finditer(self.source)]
        self.assertEqual(hits, [], f"it may stat and resolve a credential, never read one: {hits}")

    def test_a_credential_path_is_stat_only(self) -> None:
        """build() may look at every declared pointer without ever opening one."""
        panel = self.backend.build(None)
        blob = json.dumps(panel, ensure_ascii=False)
        self.assertTrue(panel["ok"])
        # The pointer paths themselves ARE allowed in the payload - they are pointers, not secrets.
        self.assertNotIn("lygo.pass:", blob)
        self.assertNotIn("gitea.pass:", blob)

    def test_template_files_are_not_findings(self) -> None:
        """A committed `.env.example` is meant to be in the repo; flagging one is a false alarm."""
        for name in (".env.example", "moltx.credentials.example.json", "secrets.sample.yaml", "api.template.toml"):
            self.assertEqual(self.backend._secret_rule(name), "", f"{name} is a template, not a secret")

    def test_real_secret_shapes_are_found(self) -> None:
        for name in (".env", "core_signing.key", "id_rsa", "credentials.json", "token.txt", "secrets.yaml", "x.pem"):
            self.assertNotEqual(self.backend._secret_rule(name), "", f"{name} must be reported")

    def test_a_binary_named_like_a_secret_is_not_a_finding(self) -> None:
        """The engine ships llama-tokenize.exe. A card that flags it can never be green."""
        for name in ("llama-tokenize.exe", "channel-secret-basic-runtime-BVVgDSdq.d.ts", "tokenize.py", "secrets_notes.md"):
            self.assertEqual(self.backend._secret_rule(name), "", f"{name} is not a secret")


class GuardwatchHonestyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = _load(MID)

    def test_every_check_carries_the_location_that_produced_it(self) -> None:
        panel = self.backend.build(None)
        for issue in panel["issues"]:
            self.assertIn("where", issue, "a finding without a location cannot be re-run by hand")
            self.assertIn("why", issue)
            self.assertIn(issue["severity"], ("red", "amber"))

    def test_a_check_that_could_not_run_is_never_green(self) -> None:
        with mock.patch.object(self.backend, "_owner", side_effect=lambda n: None):
            panel = self.backend.build(None)
        self.assertNotEqual(panel["state"], "green")
        self.assertTrue(panel["checks"]["unchecked"], "an unrun check must be listed as unchecked")
        self.assertTrue(panel["missing"], "and named in words")

    def test_a_cannot_tell_the_owner_of_the_deny_rule(self) -> None:
        """The deny rule is the kernel's. Copying it here would be a module duplicating kernel logic."""
        panel = self.backend.build(None)
        self.assertTrue(any("tools._denied" in s for s in panel["sources"]), "the rule must be CALLED, and named")

    def test_an_absent_drive_is_not_a_fault(self) -> None:
        """The stick does not carry the steward's I: keys; that is normal, not a finding."""
        fake = type("M", (), {"credential_pointers": staticmethod(lambda: {"ops": "Z:\\nope\\x.pass"})})
        with mock.patch.object(self.backend, "_owner", side_effect=lambda n: fake if n == "admin_map" else None):
            rows, state = self.backend._pointer_rows([])
        self.assertEqual(state["findings"], [], "a pointer on a drive that is not here is not a fault")
        self.assertTrue(any(r["v"] == "lives elsewhere" for r in rows))

    def test_a_pointer_that_disappears_from_a_present_drive_IS_a_finding(self) -> None:
        drive = Path(sys.executable).drive or "C:"
        fake = type("M", (), {"credential_pointers": staticmethod(lambda: {"ops": f"{drive}\\nope-missing-xyz.pass"})})
        with mock.patch.object(self.backend, "_owner", side_effect=lambda n: fake if n == "admin_map" else None):
            _rows, state = self.backend._pointer_rows([])
        self.assertEqual(len(state["findings"]), 1)
        self.assertEqual(state["findings"][0]["severity"], "amber")

    def test_a_remote_url_with_a_credential_is_found_without_printing_the_url(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".git").mkdir()
            (repo / ".git" / "config").write_text(
                '[remote "origin"]\n\turl = https://user:sekrit@example.test/x.git\n', encoding="utf-8"
            )
            rows, findings = self.backend._remote_rows(repo, [])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "red")
        self.assertNotIn("sekrit", json.dumps(rows), "the URL IS the secret and must never be printed")


class GuardwatchGitignoreTests(unittest.TestCase):
    """The ignore check matches patterns itself, so its own rules need pinning."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = _load(MID)

    @unittest.skipIf(on_a_stick(), "PC-build assertion: this reads the shipped PC config (config/ is synced to the stick by hand, by design) or the checkout above the kit")
    @unittest.skipIf(on_a_stick(), "PC-build assertion: this reads the shipped PC config (config/ is synced to the stick by hand, by design) or the checkout above the kit")
    def test_a_kit_checkout_is_found_above_the_kit(self) -> None:
        """The repo root of THIS kit is one level up; looking only at <kit>/.git reads as 'not a repo'."""
        root = self.backend._repo_root(MODULES_DIR)
        self.assertIsNotNone(root, "the kit sits inside a git checkout and the card must find it")

    def test_dir_pattern_and_glob_are_both_honoured(self) -> None:
        rules = [(1, "data/", False), (2, "*.pyc", False)]
        self.assertTrue(self.backend._gitignore_verdict(Path("."), "data/.lygo_llm_token", rules)[0])
        self.assertTrue(self.backend._gitignore_verdict(Path("."), "src/x.pyc", rules)[0])
        self.assertFalse(self.backend._gitignore_verdict(Path("."), "src/paths.py", rules)[0])

    def test_a_negation_un_ignores(self) -> None:
        """Pinned against the real parser, not a hand-built tuple: `_ignored_rules` is what the card uses."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gitignore").write_text("workspace/*\n!workspace/.gitkeep\n", encoding="utf-8")
            rules = self.backend._ignored_rules(root)
            self.assertEqual(rules, [(1, "workspace/*", False), (2, "workspace/.gitkeep", True)])
            ignored, _why = self.backend._gitignore_verdict(root, "workspace/.gitkeep", rules)
            self.assertFalse(ignored, "a later negation wins, as in git")
            still_ignored, _why = self.backend._gitignore_verdict(root, "workspace/notes.txt", rules)
            self.assertTrue(still_ignored, "the negation must un-ignore only the file it names")


if __name__ == "__main__":
    unittest.main()
