from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tools import TOOLS_SCHEMA, core_schema, dispatch  # noqa: E402


class ToolBattery(unittest.TestCase):
    def test_core_schema_small(self):
        names = {(t.get("function") or {}).get("name") for t in core_schema()}
        self.assertIn("steward_map", names)
        self.assertIn("self_check", names)
        self.assertIn("web_fetch", names)
        # A bare count was a proxy for context cost. Characters are what a small model actually pays,
# and the count grew when the self-build limbs joined (each one is a capability, not a
# convenience), so the assertion moved to the budget it was protecting. Measured 2026-09-21:
# 28 tools, ~11.8k chars of schema - about 3k tokens against a 32k window.
# Raised 18000 -> 20000 on 2026-10-09 when the video limb took a core slot. The old figure was
# self-imposed doctrine, not a mechanism (no production code reads it), and the honest arithmetic
# says the window can afford it: the schema alone is ~19.8k chars (~5k tok at ~4 chars/token) and
# the whole fixed prefix is a minority of the 32768-token window the shipped 7B actually has. The
# number moved by a measured amount, not abandoned, and the headroom left is deliberately thin so
# the next capability still has to argue for itself.
        # CAPS REMOVED BY THE STEWARD 2026-10-16: "REMOVE ALL THE CAPS". This file already recorded
        # the doctrine that the budget is the OPERATOR's to move; the operator moved it to nothing.
        # The measurement is reported because it is useful, and no ceiling is enforced: the ceilings
        # were withholding real capabilities from the on-box brain (tasking read-outs, side agents).
        print(f"[schema] core tools: {len(names)} (no ceiling)")
        self.assertGreater(len(names), 0)
        # ------------------------------------------------- 2026-10-12: THE DOCTRINE MOVED (steward)
        # The two numbers above and below moved for the `film` limb, on the steward's own
        # instruction: "increase cap, increase slots, and change the doctrine to be able to make it
        # flexible". Until today both were treated as a wall, with a standing rule that a 41st
        # capability had to take a slot back from a read-out. The rule is now: a capability still has
        # to argue for its slot, and a read-out still gives way first - but the BUDGET IS THE
        # OPERATOR'S TO MOVE, deliberately and by a measured amount, instead of a number nobody may
        # touch. `keeper_status` and `now` were NOT put back; nothing was taken from anyone.
        #
        # The arithmetic, measured on this tree rather than asserted: `film`'s schema entry is 2026
        # chars, and the schema came out at 22765 with 41 core tools (was 20737 with 40), so the
# Every cap move on this tree is the same sentence: the number IS what len(str(core_schema()))
# MEASURED, plus the headroom the last move kept.
#   22,974 -> 23,200  the `film` consent property (226 chars of headroom)
#   23,200 -> 25,308  advertising `cancel_all`, which the console's own strip needs
#   25,308 -> 26,754  the `plexo` entry, then the sovereign verbs it advertises
#                     (`adapters`, `urls`, `bind`, `limit_mbps`) - 42 core tools, measured
#   26,754 -> 26,980  this pass: the ceiling must EXCEED the measurement, because every assertion
#                     here is `assertLess`. Seven sites had been written with the measurement itself
#                     as the ceiling, which made 26,754 < 26,754 false in six files at once - the one
#                     failure a careful re-read does not find and running the suite does.
# The COUNT cap stays 44 (42 used): the headroom was bought deliberately, and a new capability still
# argues for its slot in front of the steward.
# 2026-10-15: `subagent_run` + `subagent_pool` join the EXTENDED schema - NOT a core slot. Still 42 of
# 44 core tools, and the core schema measured 26,754 both before and after, because a side agent is a
# capability the operator calls rather than a read-out anyone gives up. The pair costs 2126 chars of
# EXTRA_SCHEMA, which no cap in this file asserts against: no cap moved, so nothing above changed.
        # character cap moved 20800 -> 23000 - the measured amount plus 235 chars of headroom. The
        # count cap moved 40 -> 44 for three reasons: (1) the character budget is the number that
        # actually costs the window, and it is asserted independently just below; (2) at ~1000-2000
        # chars per capability the next three entries cannot arrive silently - each one moves the
        # char cap again; (3) an EXACT count cap is what forced the earlier eviction of a working
        # read-out (`keeper_status`), and the operator has now said that is not a trade a number
        # should make on its own. What did NOT change: the schema still has to fit the shipped 7B's
        # 32768-token window, still costs ~5.7k tokens (~17%), and warm_prefix() still primes it into
        # the KV cache once at boot rather than re-sending it every turn.
        #
        # Later the same day, closing the consent hole in the film limb (a foreign `out` was a way to
        # write a render anywhere on the disk) added the `consent` property to that entry - 2026 ->
        # 2235 chars, schema 22765 -> 22974. 26 chars of headroom is not headroom, so the character
        # cap moved again, by the measured amount: 23000 -> 23200. Same rule, applied twice.
        # Raised 20000 -> 20800 on 2026-10-10 when `rea` took the slot `now` gave up. The arithmetic,
        # measured rather than asserted: rea's entry costs 909 chars, the `now` entry it replaced was
        # 149, and the schema came out at 20737 - so the cap moved by the measured amount (to 20800)
        # and no further. `now` left the set as a READ-OUT, not to buy space: the clock already rides
        # every prompt as continuity.py's volatile tail, and the local 7b was measured reaching for the
        # limb on 4 of 8 greeting turns with that clock in front of it. It stays advertised in the
        # global schema for the larger path, the way keeper_status and media_status do. The headroom
        # here is deliberately thin so the next capability still has to argue for itself.
        # no ceiling - see the note above
        print(f"[schema] core schema chars: {len(str(core_schema()))} (no ceiling)")
        self.assertGreater(len(str(core_schema())), 0)

    def test_film_limb_is_advertised(self):
        """The film limb is real in BOTH schemas, and its harness imports on this tree."""
        core = {(t.get("function") or {}).get("name") for t in core_schema()}
        self.assertIn("film", core)
        self.assertIn("film", {t.get("function", {}).get("name") for t in TOOLS_SCHEMA})
        sys.path.insert(0, str(ROOT / "src"))
        import film_tools  # noqa: F401 - a limb that cannot import is a limb that cannot act

    def test_film_limb_answers_without_raising(self):
        """A read-only call answers a dict even on a kit with no FilmCraft payload."""
        from tools import dispatch

        out = dispatch("film", {"action": "status"})
        self.assertIsInstance(out, dict)
        self.assertIn("installed", out)
        bad = dispatch("film", {"action": "no-such-action"})
        self.assertFalse(bad.get("ok"))
        self.assertEqual(bad.get("error"), "unknown_action")
        self.assertIn("status", bad.get("actions") or [], "the refusal must list what IS accepted")

    def test_dispatch_core(self):
        checks = [
            ("steward_map", {}),
            ("self_check", {}),
            ("whoami", {}),
            ("kernel_status", {}),
            ("now", {}),
            ("calc", {"expr": "2+2"}),
            ("hash_text", {"text": "lygo"}),
            ("list_dir", {"path": ""}),
            ("workspace_map", {}),
            ("skill_list", {}),
        ]
        for name, args in checks:
            r = dispatch(name, args)
            self.assertTrue(r.get("ok"), f"{name} {r}")

    def test_placeholder_blocked(self):
        r = dispatch("web_fetch", {"url": "https://github.com/user/repo"})
        self.assertEqual(r.get("error"), "placeholder_url")


if __name__ == "__main__":
    unittest.main()
