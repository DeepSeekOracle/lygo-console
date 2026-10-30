"""The self-build harness: limbs that let the console check, seal, restart and equip itself.

Written red before the implementation. Each test states the property, not the plumbing: these limbs
run the kit's own suite, rewrite its manifests, ring its own doorbell and install toolchains, so
every one that changes the system must refuse without consent and say why.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import tools  # noqa: E402

NEW = ("self_test", "self_seal", "self_restart", "toolchain_install")


def names(schema):
    return [(t.get("function") or {}).get("name") for t in schema]


class SelfBuildLimbsExist(unittest.TestCase):
    def test_all_four_are_advertised(self):
        have = set(names(tools.TOOLS_SCHEMA))
        for want in NEW:
            self.assertIn(want, have, f"{want} is not advertised at all")

    def test_all_four_reach_the_local_brain(self):
        """A limb absent from core_schema() does not exist for the on-box agent (defect 103)."""
        have = set(names(tools.core_schema()))
        for want in NEW:
            self.assertIn(want, have, f"{want} is invisible to the local brain")

    def test_the_local_schema_still_fits_its_budget(self):
        """The cap exists to protect a small model's context: measure what it really pays."""
        schema = tools.core_schema()
        chars = len(str(schema))
        # Measured 2026-09-21 with the self-build, tasking, cron and keeper limbs all in:
        # 36 tools / 13,123 chars ~ 3.3k tokens against a 32k window. The budget is the guard;
        # the count is not, because a capability costs what its description costs.
        self.assertLess(chars, 26980, f"local schema is {chars} chars - too much for the on-box brain")
        # 44 slots / 25256 chars since `film` landed; the measured arithmetic and the doctrine behind
        # the move are in tests/test_tool_battery.py, and this file re-asserts rather than re-derives.
        self.assertLessEqual(len(schema), 44)


class ConsentIsRequired(unittest.TestCase):
    def test_sealing_without_consent_refuses_and_says_why(self):
        got = tools.dispatch("self_seal", {})
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("error"), "consent_required")
        self.assertIn("consent", str(got.get("hint", "")).lower())

    def test_restarting_without_consent_refuses(self):
        got = tools.dispatch("self_restart", {})
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("error"), "consent_required")

    def test_installing_without_consent_is_a_plan_not_an_action(self):
        got = tools.dispatch("toolchain_install", {"what": "rust_gnu_target"})
        self.assertTrue(got.get("ok"), got)
        self.assertFalse(got.get("ran"), "it must not install anything without consent")
        self.assertIn("rustup", str(got.get("command", "")))

    def test_an_unknown_action_is_refused_not_executed(self):
        got = tools.dispatch("toolchain_install", {"what": "rm -rf /", "consent": True})
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("error"), "unknown_action")


class SelfTestRunsTheSuite(unittest.TestCase):
    def test_it_runs_a_real_test_file_and_reports_real_counts(self):
        got = tools.dispatch("self_test", {"target": "tests/test_tool_routing.py", "timeout": 300})
        self.assertTrue(got.get("ok"), got)
        self.assertGreaterEqual(int(got.get("passed") or 0), 1)
        self.assertEqual(int(got.get("failed") or 0), 0)
        self.assertIn("pytest", str(got.get("command", "")))

    def test_it_refuses_a_target_outside_the_suite(self):
        got = tools.dispatch("self_test", {"target": "C:/Windows/system32/calc.exe"})
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("error"), "outside_tests")


class WorkspaceIsIntact(unittest.TestCase):
    def test_the_workspace_answers(self):
        got = tools.dispatch("workspace_map", {})
        self.assertTrue(got.get("ok"), got)
        self.assertTrue(str(got).strip(), "workspace_map answered with nothing")

    def test_a_written_file_is_under_the_workspace_and_readable(self):
        wrote = tools.dispatch("write_file", {"path": "selfbuild_probe.txt", "content": "probe\n"})
        self.assertTrue(wrote.get("ok"), wrote)
        target = Path(str(wrote.get("path")))
        self.assertTrue(target.is_file(), f"{target} was reported written but is not there")
        read = tools.dispatch("read_file", {"path": str(target)})
        self.assertIn("probe", str(read), read)


if __name__ == "__main__":
    unittest.main(verbosity=2)
