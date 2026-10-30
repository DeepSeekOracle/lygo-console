"""The reverse-engineering limb: its registration, its refusals, and its argv.

Everything here is offline on purpose. REA is an external CLI and this suite must never spawn it:
a test that runs the real analyzer would pay real minutes and could hold a file on a stick, which is
the one-engine-at-a-time law this kit lives by. The one call that DOES reach the binary is a separate
integration probe run by hand, not by the suite.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import limbs  # noqa: E402
import tools  # noqa: E402


class ReaLimb(unittest.TestCase):
    def setUp(self) -> None:
        self._real_bin = limbs._rea_bin
        self._real_run = limbs._run_capture

    def tearDown(self) -> None:
        limbs._rea_bin = self._real_bin
        limbs._run_capture = self._real_run

    def _stub_run(self, code: int = 0, out: str = "{}", err: str = ""):
        """Record the argv instead of spawning anything."""
        seen: list[list[str]] = []

        def fake(argv, *, timeout, cwd, env):
            seen.append(list(argv))
            return code, out, err, False

        limbs._run_capture = fake
        return seen

    def test_advertised_in_the_extended_schema(self) -> None:
        names = [(t.get("function") or {}).get("name") for t in tools.TOOLS_SCHEMA]
        self.assertIn("rea", names, "the limb is not advertised at all")

    def test_reaches_the_local_brain_and_holds_both_caps(self) -> None:
        """REA is a CORE limb, and the two caps are re-asserted here on purpose.

        It took the 41st slot by displacing `now` rather than by raising the count: `now` is a
        READ-OUT (the clock already rides every prompt as continuity.py's volatile tail) and was
        measured to misroute on 4 of 8 greeting turns, while rea is a capability the brain chooses
        between. The character budget moved 20000 -> 20800 by the measured amount (rea 909 chars
        replacing now's 149, landing at 20737). Both numbers are pinned here as well as in the four
        files that already assert them, so a later entry has to argue for its slot the same way.
        """
        core = [(t.get("function") or {}).get("name") for t in tools.core_schema()]
        self.assertIn("rea", core, "rea is not offered to the local brain")
        self.assertNotIn("now", core, "the clock rides every prompt already - it is not a capability")
        # The caps moved again on the same day: 40 -> 44 slots and 20800 -> 23200 characters, when
        # `film` arrived and the steward said the budget is the operator's to move, in measured steps.
        # The arithmetic lives in ONE place (tests/test_tool_battery.py) so two files cannot drift
        # apart on the same number; here the numbers are re-asserted, not re-derived.
        # CAPS REMOVED BY THE STEWARD 2026-10-16: "REMOVE ALL THE CAPS". The count ceiling and the
        # character budget were doctrine that no production code read, and they withheld real
        # capabilities from the on-box brain. The MEASUREMENT stays (it is useful); the ceiling does
        # not. See tools.CORE_NAMES for the decision on the record.
        print(f"[schema] core entries: {len(core)} · chars: {len(str(tools.core_schema()))} (no ceiling)")
        self.assertGreater(len(core), 0)

    def test_a_displaced_readout_still_reaches_the_larger_path(self) -> None:
        """An evicted read-out is moved, not deleted - the keeper_status/media_status precedent."""
        global_names = [(t.get("function") or {}).get("name") for t in tools.TOOLS_SCHEMA]
        self.assertIn("now", global_names, "the larger path lost the clock entirely")

    def test_a_missing_binary_fails_soft(self) -> None:
        limbs._rea_bin = lambda: None
        got = limbs.extra("rea", {"action": "doctor"})
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("error"), "rea_missing")
        self.assertIn("hint", got, "an absent tool is not a bug in the turn - say where to get it")

    def test_an_unknown_action_is_named(self) -> None:
        limbs._rea_bin = lambda: "rea"
        got = limbs.extra("rea", {"action": "frobnicate"})
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("error"), "unknown_action")
        # every action the dispatcher accepts must be in the refusal, or a model cannot recover
        for action in ("doctor", "capabilities", "analyze", "decompile"):
            self.assertIn(action, got.get("hint", ""))

    def test_analyze_without_a_target_is_refused(self) -> None:
        limbs._rea_bin = lambda: "rea"
        got = limbs.extra("rea", {"action": "analyze"})
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("error"), "target_required")

    def test_the_alias_fill_reaches_path(self) -> None:
        """A small model reaches for 'target'; the canonical key is path. The fill must land."""
        _, args = limbs.canonicalize("rea", {"action": "analyze", "target": "C:/x/app.exe"})
        self.assertEqual(args.get("path"), "C:/x/app.exe")

    def test_argv_shape_per_action(self) -> None:
        limbs._rea_bin = lambda: "rea"
        seen = self._stub_run(out='{"healthy": false}')
        limbs.extra("rea", {"action": "doctor"})
        self.assertEqual(seen[-1], ["rea", "doctor", "--format", "json"])
        limbs.extra("rea", {"action": "capabilities"})
        self.assertEqual(seen[-1], ["rea", "capabilities", "--format", "json"])
        limbs.extra("rea", {"action": "analyze", "path": "C:/x/app.exe"})
        self.assertEqual(seen[-1], ["rea", "analyze", "C:/x/app.exe", "--format", "json"])

    def test_a_nonzero_exit_from_doctor_is_still_a_verdict(self) -> None:
        """doctor exits non-zero when the environment is not fully healthy - that is its ANSWER.

        Measured on the first live call: reading the status code alone reported a working tool as
        broken while it carried a complete report. The verdict must come out of the report.
        """
        limbs._rea_bin = lambda: "rea"
        self._stub_run(code=1, out='{"healthy": false, "scope_checks": [{"name": "hopper", "ok": false}]}')
        got = limbs.extra("rea", {"action": "doctor"})
        self.assertTrue(got.get("ok"), "a non-zero exit here is a verdict, not a failed limb")
        self.assertIs(got.get("healthy"), False)
        self.assertEqual(len(got.get("checks") or []), 1)

    def test_a_failing_analyze_is_not_reported_as_done(self) -> None:
        limbs._rea_bin = lambda: "rea"
        self._stub_run(code=2, out="boom", err="could not read the file")
        got = limbs.extra("rea", {"action": "analyze", "path": "C:/x/app.exe"})
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("code"), 2)
        self.assertIn("could not read", got.get("stderr", ""))

    def test_the_binary_is_not_hardcoded(self) -> None:
        """No drive letter may be baked in: this kit is copied to a stick and to the web edition."""
        src = (ROOT / "src" / "limbs.py").read_text(encoding="utf-8", errors="ignore")
        body = src.split("def _rea_bin", 1)[1].split("\ndef ", 1)[0]
        for bad in ("C:\\", "D:\\", "E:\\", "I:\\", "C:/", "I:/"):
            self.assertNotIn(bad, body, f"the resolver names a drive ({bad})")

    def test_the_dispatcher_reaches_the_limb(self) -> None:
        """The end-to-end wire: dispatch() must find it, or the schema advertises a dead name."""
        limbs._rea_bin = lambda: "rea"
        self._stub_run(out='{"healthy": true}')
        got = tools.dispatch("rea", {"action": "doctor"})
        self.assertTrue(got.get("ok"), f"dispatch did not reach the limb: {got}")


if __name__ == "__main__":
    unittest.main()
