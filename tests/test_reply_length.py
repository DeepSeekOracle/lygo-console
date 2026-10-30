"""The reply cap: one number, read from one place, and the window that has to reserve it.

The defect these pin: the cap lived in THREE places at once — a hardcoded 768 in the portal, 1024 in
the chat handler, and 512 in `config/console.json` — and only the portal's was live, because the
config key had no reader at all. Every long answer was cut mid-sentence at exactly 768 tokens.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # the stick/PC profile helper
from _stick_profile import on_a_stick, stick_why  # noqa: E402

import compaction  # noqa: E402
import paths  # noqa: E402


class ConfigIsTheSourceOfTruthTests(unittest.TestCase):
    """The config key must be READ. It shipped documented-but-unwired, which is worse than absent."""

    def setUp(self) -> None:
        self._saved = getattr(paths, "_CFG_CACHE", None)

    def tearDown(self) -> None:
        paths._CFG_CACHE = self._saved

    def test_console_limits_returns_a_max_tokens_key(self) -> None:
        self.assertIn("max_tokens", paths.console_limits(),
                      "the documented config key has no reader again - the portal's constant wins")

    def test_the_key_follows_the_config(self) -> None:
        for cap in (1024, 2048, 4096, 8192):
            paths._CFG_CACHE = {"max_tokens": cap}
            self.assertEqual(cap, paths.console_limits()["max_tokens"], f"config said {cap}")

    def test_junk_config_reads_as_the_default_not_as_zero(self) -> None:
        for junk in ({"max_tokens": None}, {"max_tokens": "big"}, {}):
            paths._CFG_CACHE = junk
            cap = paths.console_limits()["max_tokens"]
            self.assertIsInstance(cap, int)
            self.assertGreater(cap, 0, f"{junk} produced a cap of {cap} - that is a 0-token reply")

    @unittest.skipIf(on_a_stick(), "PC-build assertion: this reads the shipped PC config (config/ is synced to the stick by hand, by design) or the checkout above the kit")
    @unittest.skipIf(on_a_stick(), "PC-build assertion: this reads the shipped PC config (config/ is synced to the stick by hand, by design) or the checkout above the kit")
    def test_the_shipped_config_does_not_regress_below_the_fixed_floor(self) -> None:
        """4096 is the number this change was measured at. Lowering it silently is the old bug."""
        shipped = json.loads((ROOT / "config" / "console.json").read_text(encoding="utf-8"))
        self.assertIn("max_tokens", shipped)
        self.assertGreaterEqual(shipped["max_tokens"], 4096)

    @unittest.skipIf(on_a_stick(), "PC-build assertion: this reads the shipped PC config (config/ is synced to the stick by hand, by design) or the checkout above the kit")
    @unittest.skipIf(on_a_stick(), "PC-build assertion: this reads the shipped PC config (config/ is synced to the stick by hand, by design) or the checkout above the kit")
    def test_the_shipped_config_documents_the_key(self) -> None:
        shipped = json.loads((ROOT / "config" / "console.json").read_text(encoding="utf-8"))
        note = (shipped.get("_notes") or {}).get("other_keys", {}).get("max_tokens", "")
        self.assertTrue(note, "an undocumented key is how this one went unwired in the first place")


class ReserveTracksTheCapTests(unittest.TestCase):
    """Raising the cap without reserving it evicts the conversation the answer belongs to."""

    def setUp(self) -> None:
        self._saved = getattr(paths, "_CFG_CACHE", None)

    def tearDown(self) -> None:
        paths._CFG_CACHE = self._saved

    def test_answer_reserve_equals_the_configured_cap(self) -> None:
        for cap in (1024, 2048, 4096, 8192):
            paths._CFG_CACHE = {"max_tokens": cap}
            self.assertEqual(cap, compaction.answer_reserve(), f"reserve must follow a cap of {cap}")

    def test_the_window_reserves_the_cap(self) -> None:
        paths._CFG_CACHE = {"ctx_default": 32768, "ctx_max": 32768, "max_tokens": 4096}
        b = compaction.window_budget(ctx=32768)
        self.assertEqual(4096, b["answer_reserve"])
        # ctx - system - answer - safety, and nothing else, may be handed to the history.
        expected = 32768 - b["system_reserve"] - 4096 - b["safety_reserve"]
        self.assertEqual(expected, b["history_tokens"])

    def test_a_bigger_reply_shrinks_the_history_by_exactly_that_much(self) -> None:
        paths._CFG_CACHE = {"max_tokens": 1024}
        small = compaction.window_budget(ctx=32768)["history_tokens"]
        paths._CFG_CACHE = {"max_tokens": 4096}
        big = compaction.window_budget(ctx=32768)["history_tokens"]
        self.assertEqual(small - big, 4096 - 1024,
                         "the history must give up exactly the extra tokens the reply was given")

    def test_the_history_floor_holds_even_when_the_cap_is_absurd(self) -> None:
        """A cap larger than the window must not produce a negative budget or a dead console."""
        paths._CFG_CACHE = {"max_tokens": 400000}
        b = compaction.window_budget(ctx=8192)
        self.assertGreaterEqual(b["history_tokens"], 512)
        self.assertGreater(b["history_chars"], 0)
        self.assertGreater(b["compact_at"], 0)


class ThePortalCannotDriftTests(unittest.TestCase):
    """The portal carried the live number. It must ask, not assume."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.js = (ROOT / "portal" / "app.js").read_text(encoding="utf-8")

    def test_the_hardcoded_cap_is_gone(self) -> None:
        self.assertIsNone(re.search(r"const\s+MAX_TOKENS\s*=\s*\d+", self.js),
                          "a hardcoded reply cap is what cut every long answer at exactly that many tokens")

    def test_the_cap_is_seeded_from_health(self) -> None:
        self.assertIn("MAX_TOKENS = Number(j.max_tokens)", self.js,
                      "the page must take the console's configured cap, not its own")

    def test_it_still_sends_the_cap_as_a_hint(self) -> None:
        self.assertIn("max_tokens: MAX_TOKENS", self.js)

    def test_the_page_does_not_time_out_a_chat_turn(self) -> None:
        """A clock on the page aborted long answers while the engine was still working."""
        self.assertNotIn("capWindowMs()", self.js)
        self.assertNotIn("idleWindowMs()", self.js)
        self.assertNotIn("STREAM_IDLE_LOCAL_MS", self.js)
        self.assertNotIn("engine silent", self.js)


class TheServerClampsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.src = (ROOT / "src" / "server.py").read_text(encoding="utf-8")

    def test_the_chat_handler_reads_the_cap_from_config(self) -> None:
        self.assertIn('console_limits().get("max_tokens")', self.src)

    def test_a_request_may_lower_the_cap_but_never_raise_it(self) -> None:
        self.assertRegex(
            self.src,
            r"min\(int\(obj\.get\(\"max_tokens\"\) or _cap\), _cap\)",
            "without the clamp a client can ask for more than the operator configured",
        )

    def test_health_reports_the_cap_the_page_must_obey(self) -> None:
        # Still the live config, now through health_payload's safe() wrapper (defect 34). A hardcoded
        # number fails this guard exactly as before.
        self.assertIn(
            '"max_tokens": safe("max_tokens", lambda: _paths.console_limits().get("max_tokens"), None)',
            self.src,
        )

    def test_the_old_hardcoded_fallbacks_are_gone(self) -> None:
        self.assertNotIn('max_tokens = int(obj.get("max_tokens") or 1024)', self.src)
        self.assertRegex(self.src, r'_cap = int\(console_limits\(\)\.get\("max_tokens"\) or \d+\)',
                         "the cap must come from config, with only a last-resort integer behind it")


class TheEngineCanReachTheCapTests(unittest.TestCase):
    """The window must be able to hold the configured reply, or the cap is fiction."""

    @unittest.skipIf(on_a_stick(), "PC-build assertion: this reads the shipped PC config (config/ is synced to the stick by hand, by design)")
    def test_the_shipped_window_can_hold_the_shipped_cap_with_room_for_a_conversation(self) -> None:
        shipped = json.loads((ROOT / "config" / "console.json").read_text(encoding="utf-8"))
        cap = shipped["max_tokens"]
        ctx_max = shipped["ctx_max"]
        self.assertGreaterEqual(ctx_max, 32768, "defect 126: a 2k/16k window emptied ~10k photo turns")
        self.assertLess(cap, ctx_max, "a reply longer than the whole window can never be generated")
        budget = compaction.window_budget(ctx=ctx_max, answer_tokens=cap)
        self.assertGreater(budget["history_tokens"], 4096,
                           "the reply ate the conversation: not enough window left for history")


if __name__ == "__main__":
    unittest.main()
