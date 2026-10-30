"""The `plexo` limb's registration sites - the four places a tool is wired, not one.

A limb registered in only some of them appears in the schema while the call does nothing, or is
invisible to the local brain because it never reached CORE_NAMES. Each site is asserted here by name,
and the caps are asserted against the arithmetic's owner (`tests/test_tool_battery.py`).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import limbs  # noqa: E402
import tools  # noqa: E402


class TestLimbWiring(unittest.TestCase):
    def test_the_limb_is_in_the_core_schema_the_local_brain_reads(self) -> None:
        names = [t["function"]["name"] for t in tools.core_schema()]
        self.assertIn("plexo", names)

    def test_the_schema_entry_advertises_every_action_the_dispatcher_accepts(self) -> None:
        import plexo_tools

        entry = [t for t in tools.TOOLS_SCHEMA if t["function"]["name"] == "plexo"]
        self.assertEqual(1, len(entry), "exactly one plexo entry, in the core or extended schema")
        documented = entry[0]["function"]["parameters"]["properties"]["action"]["description"]
        for action in plexo_tools.ACTIONS:
            self.assertIn(action, documented, f"{action} is accepted but not advertised to the model")

    def test_the_canonical_keys_and_aliases_exist(self) -> None:
        keys = limbs.CANON_KEYS["plexo"]
        self.assertIn("action", keys)
        self.assertIn("url", keys)
        self.assertIn("consent", keys)
        # A consent word a stray synonym could fill is not a consent: `consent` must be reachable ONLY
        # by its own name, so the alias pool must not carry it.
        self.assertNotIn("consent", limbs.ALIAS_POOL,
                         "an aliased consent is a consent a small model can invent")
        for key in ("url", "out", "streams", "job", "seconds"):
            self.assertIn(key, limbs.ALIAS_POOL, f"{key} has no alias entry, so only its exact name works")
        got, args = limbs.canonicalize("plexo", {"link": "https://example.org/x.bin", "action": "fetch"})
        self.assertEqual("plexo", got)
        self.assertEqual("https://example.org/x.bin", args.get("url"),
                         "`link` must fill the canonical `url` key or a small model cannot reach it")
        _, args2 = limbs.canonicalize("plexo", {"action": "fetch", "connection": "16", "dest": "x.bin"})
        self.assertEqual("x.bin", args2.get("out"))

    def test_the_dispatcher_reaches_the_harness(self) -> None:
        got = limbs.extra("plexo", {"action": "credit"})
        self.assertIsNotNone(got, "the dispatcher has no branch for plexo")
        self.assertEqual("credit", got.get("action"))

    def test_the_limb_is_listed_once_by_the_host(self) -> None:
        declared = [row for row in limbs.EXTRA_SCHEMA if row["function"]["name"] == "plexo"]
        self.assertEqual(1, len(declared))


class TestCaps(unittest.TestCase):
    """The arithmetic lives in test_tool_battery.py; this file re-asserts the numbers it produced."""

    def test_the_core_set_is_within_its_reviewed_budget(self) -> None:
        count = len(tools.CORE_NAMES)
        chars = len(str(tools.core_schema()))
        # CAPS REMOVED BY THE STEWARD 2026-10-16: "REMOVE ALL THE CAPS". The count ceiling and the
        # character budget were doctrine that no production code read, and they withheld real
        # capabilities from the on-box brain. The MEASUREMENT stays (it is useful); the ceiling does
        # not. See tools.CORE_NAMES for the decision on the record.
        print(f"[schema] core slots used: {count} · core schema chars: {chars} (no ceiling)")
        self.assertGreater(count, 0, "the core set must offer the brain something")
        self.assertIn("plexo", tools.CORE_NAMES)
        # Recorded for the next reader: what this capability actually cost the window.
        print(f"[schema] core slots {count}, schema {chars} chars (no ceilings)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
