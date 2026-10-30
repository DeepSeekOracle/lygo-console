"""Tool routing on the shipped local brain: the fix must stay fixed.

Measured before the fix (qwen2.5-coder:7b, ctx 8192): five of five arithmetic probes routed to
web_search/web_fetch and one answered 53 for 17 * 23. `calc` sat 18th of 20 in the local schema,
described only as "Evaluate a numeric Python expression", while the web tools came first.

These tests lock the two levers that were changed. They cannot prove routing (that needs a live
turn and is measured separately); they can prove the levers were not quietly reverted.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import tools  # noqa: E402


def _fn(t: dict) -> dict:
    return t.get("function") or {}


class CoreSchemaRoutingTests(unittest.TestCase):
    def test_priority_tool_leads_the_local_schema(self):
        names = [_fn(t).get("name") for t in tools.core_schema()]
        self.assertTrue(names, "local schema is empty")
        self.assertEqual(names[0], tools.CORE_PRIORITY[0])
        self.assertIn("calc", names)
        self.assertIn("web_search", names)

    def test_calc_is_described_for_arithmetic(self):
        calc = next(_fn(t) for t in tools.core_schema() if _fn(t).get("name") == "calc")
        desc = (calc.get("description") or "").lower()
        self.assertIn("arithmetic", desc)
        self.assertIn("web", desc, "calc's description no longer steers away from web search")

    def test_web_tools_rule_arithmetic_out(self):
        for name in ("web_search", "web_fetch"):
            spec = next(_fn(t) for t in tools.core_schema() if _fn(t).get("name") == name)
            desc = (spec.get("description") or "").lower()
            self.assertIn("calc", desc, f"{name}'s description no longer points arithmetic at calc")

    def test_cloud_schema_keeps_the_full_tool_order(self):
        # the cloud path is a bigger model with more tools; only the LOCAL order was changed
        self.assertEqual([_fn(t).get("name") for t in tools.TOOLS_SCHEMA][:2], ["steward_map", "self_check"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
