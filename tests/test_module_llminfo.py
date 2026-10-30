"""Tests for lygo.llminfo — the LLM data panel (the first NEW module after the core).

Δ9Φ963-LYGO-LLMINFO-TESTS-v1

What these tests are for
    Two things, in this order.

    1. The panel must never be prettier than the truth. So the rate-window logic is a pure
       function and is tested at the four hours an operator cannot arrange by hand — inside a
       peak block, outside one, the weekend, and a provider that publishes no window at all —
       and the payload is tested for the facts that must NOT be there (a key, an invented
       token limit) as much as for the facts that must.
    2. The module contract must hold for a module written AFTER the seam: it is declared,
       catalogued, its route is wired, and its pane reaches the shell through the table alone.

What they deliberately do not do
    Write anything. This module owns no state; the tests read only, and the "owner is missing"
    cases are simulated with sys.modules rather than by breaking the operator's tree.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from modules import host as host_mod  # noqa: E402

MODULES_DIR = ROOT / "src" / "modules"
MID = "lygo.llminfo"


def load_backend():
    """Import the adapter by path: a module id contains a dot, so it is not a package name."""
    path = MODULES_DIR / MID / "backend.py"
    spec = importlib.util.spec_from_file_location("lygo_llminfo_backend", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeHandler:
    """The little bit of the request handler a module route can see."""

    def __init__(self, path: str = "/") -> None:
        self.path = path
        self.sent: list[tuple[int, object]] = []

    def _json(self, code: int, payload: object) -> None:
        self.sent.append((code, payload))

    def _read_body(self, limit: int = 300_000) -> bytes:
        del limit
        return b""

    @property
    def last(self) -> object:
        return self.sent[-1][1] if self.sent else None


class TestPeakWindow(unittest.TestCase):
    """The red/green light, at every hour we cannot wait for."""

    def setUp(self) -> None:
        self.backend = load_backend()
        self.monday = datetime(2026, 9, 21, tzinfo=timezone.utc)  # a Monday, 00:00 UTC

    def at(self, hours: int, minutes: int = 0, days: int = 0):
        return self.monday + timedelta(days=days, hours=hours, minutes=minutes)

    def test_inside_each_published_block_is_peak(self) -> None:
        for hour in (1, 2, 3, 6, 7, 8, 9):
            with self.subTest(hour=hour):
                out = self.backend.peak_state("deepseek", now_utc=self.at(hour, 30))
                self.assertEqual(out["state"], "red", f"{hour}:30 UTC must read as peak")
                self.assertIn("PEAK", out["text"])

    def test_outside_the_blocks_is_off_peak(self) -> None:
        for hour in (0, 4, 5, 10, 11, 23):
            with self.subTest(hour=hour):
                out = self.backend.peak_state("deepseek", now_utc=self.at(hour, 30))
                self.assertEqual(out["state"], "green")
                self.assertIn("off-peak", out["text"])

    def test_block_boundaries_are_exclusive_at_the_edges(self) -> None:
        self.assertEqual(self.backend.peak_state("deepseek", now_utc=self.at(1, 0))["state"], "red")
        self.assertEqual(self.backend.peak_state("deepseek", now_utc=self.at(3, 59))["state"], "red")
        self.assertEqual(self.backend.peak_state("deepseek", now_utc=self.at(4, 0))["state"], "green")
        self.assertEqual(self.backend.peak_state("deepseek", now_utc=self.at(6, 0))["state"], "red")
        self.assertEqual(self.backend.peak_state("deepseek", now_utc=self.at(10, 0))["state"], "green")

    def test_the_weekend_never_bills_at_peak(self) -> None:
        saturday = self.at(2, 0, days=5)  # Sat 02:00 UTC — inside the block, wrong day
        sunday = self.at(7, 0, days=6)
        self.assertEqual(saturday.weekday(), 5)
        self.assertEqual(self.backend.peak_state("deepseek", now_utc=saturday)["state"], "green")
        self.assertEqual(self.backend.peak_state("deepseek", now_utc=sunday)["state"], "green")

    def test_a_provider_with_no_published_window_says_so(self) -> None:
        for provider in ("groq", "openai", "gemini", "xai", "openrouter", "custom", None):
            with self.subTest(provider=provider):
                out = self.backend.peak_state(provider, now_utc=self.at(2, 0))
                self.assertEqual(out["state"], "grey", "no window must never render as green")
                self.assertIn("no window published", out["text"])
                self.assertIn("nothing to warn about", out["detail"])

    def test_the_local_brain_is_named_in_the_detail(self) -> None:
        local = self.backend.peak_state("deepseek", api_active=False, now_utc=self.at(2, 0))
        api = self.backend.peak_state("deepseek", api_active=True, now_utc=self.at(2, 0))
        self.assertIn("local brain", local["detail"])
        self.assertIn("API brain", api["detail"])

    def test_the_next_boundary_is_named_and_in_the_future(self) -> None:
        out = self.backend.peak_state("deepseek", now_utc=self.at(2, 0))
        self.assertIn("peak ends in 120 min", out["next_change"])
        out = self.backend.peak_state("deepseek", now_utc=self.at(12, 0))
        self.assertIn("peak starts in", out["next_change"])
        # Friday evening: the next peak is Monday 01:00 UTC — a weekend away, still named, not "0 min"
        friday = self.at(20, 0, days=4)
        out = self.backend.peak_state("deepseek", now_utc=friday)
        self.assertEqual(friday.weekday(), 4)
        self.assertIn("peak starts in 3180 min", out["next_change"])  # Fri 20:00 → Mon 01:00 UTC
        self.assertIn("Mon", out["next_change"])

    def test_windows_are_rendered_on_the_local_clock_and_keep_the_utc_truth(self) -> None:
        out = self.backend.peak_state("deepseek", now_utc=self.at(2, 0))
        self.assertEqual(len(out["windows_local"]), 2)
        self.assertEqual(out["windows_utc"], ["01:00–04:00 UTC", "06:00–10:00 UTC"])
        for span in out["windows_local"]:
            self.assertRegex(span, r"^\d{2}:\d{2}–\d{2}:\d{2}$")

    def test_the_source_url_travels_with_the_claim(self) -> None:
        out = self.backend.peak_state("deepseek", now_utc=self.at(2, 0))
        self.assertIn("deepseek.com", out["source"])

    def test_the_light_is_decided_without_touching_disk(self) -> None:
        """A rate decision must not depend on the tree: a broken owner still leaves a light."""
        with patch.dict(sys.modules, {"registry": None, "compaction": None, "world_clock": None}):
            out = self.backend.build(None)
        self.assertTrue(out["ok"])
        self.assertTrue(any(lig["id"] == "peak" for lig in out["lights"]))


class TestPanel(unittest.TestCase):
    """The payload: the facts that must be there, and the facts that must not."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = load_backend()
        cls.panel = cls.backend.build(None)

    def test_shape_and_signature(self) -> None:
        self.assertEqual(self.panel["signature"], "Δ9Φ963-LYGO-LLMINFO-v1")
        self.assertEqual(self.panel["module"], MID)
        self.assertIn(self.panel["lights"][0]["id"], ("peak", "tokens"))
        for group in self.panel["groups"]:
            self.assertTrue(group["title"])
            self.assertTrue(group["rows"], f"{group['title']} carries no rows")
            for row in group["rows"]:
                self.assertIn("k", row)
                self.assertIn("v", row)

    def test_it_reports_the_engine_window_and_the_room_left(self) -> None:
        group = next(g for g in self.panel["groups"] if g["title"] == "Tokens")
        keys = [r["k"] for r in group["rows"]]
        self.assertIn("Engine window", keys)
        self.assertIn("In the window now", keys)
        self.assertIn("Room before auto-compact", keys)
        self.assertIn("Next turn", keys)
        tokens_light = next(lig for lig in self.panel["lights"] if lig["id"] == "tokens")
        self.assertIn(tokens_light["state"], ("green", "amber", "red"))

    def test_it_reports_the_rate_window_group_with_its_source(self) -> None:
        group = next(g for g in self.panel["groups"] if g["title"] == "Rate window")
        rows = {r["k"]: r for r in group["rows"]}
        self.assertIn("Window (UTC)", rows)
        self.assertIn("never shifts for DST", rows["Window (UTC)"]["note"])
        self.assertIn("derived from the UTC window", rows["Window (local)"]["note"])

    def test_it_names_what_no_provider_publishes(self) -> None:
        blob = json.dumps(self.panel)
        self.assertIn("do not return that model's token limits", blob)
        self.assertIn("remaining quota", blob)
        self.assertIn("no per-turn token usage", blob)

    def test_it_carries_no_key_material(self) -> None:
        """The strongest available check: whatever the operator's key file holds must not appear."""
        cfg = ROOT / "config" / "api.json"
        if not cfg.exists():
            self.skipTest("no config/api.json on this tree")
        try:
            raw = json.loads(cfg.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a broken config is another test's problem
            self.skipTest("config/api.json is not readable JSON")
        secrets = [v for v in str(raw).split('"') if len(v) > 20 and " " not in v and any(c.isdigit() for c in v)]
        blob = json.dumps(self.panel)
        for secret in secrets:
            self.assertNotIn(secret, blob, "a key-shaped value reached the panel")
        self.assertIn("never shown", json.dumps(self.panel))

    def test_a_missing_owner_becomes_a_named_gap_not_a_zero(self) -> None:
        with patch.dict(sys.modules, {"compaction": None, "registry": None}):
            panel = self.backend.build(None)
        self.assertTrue(panel["ok"])
        blob = json.dumps(panel)
        self.assertIn("unavailable", blob)
        self.assertNotIn("Tokens", [g["title"] for g in panel["groups"]])

    def test_every_answer_names_its_sources(self) -> None:
        self.assertGreaterEqual(len(self.panel["sources"]), 4)
        self.assertTrue(any("registry" in s for s in self.panel["sources"]))
        self.assertTrue(any("compaction" in s for s in self.panel["sources"]))

    def test_health_reports_how_many_owners_it_could_read(self) -> None:
        out = self.backend.health(None)
        self.assertTrue(out["ok"])
        self.assertIn("fact owners", out["detail"])
        self.assertIn("named gap", out["detail"])


class TestContract(unittest.TestCase):
    """A module written after the seam must still be declared, wired and visible."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.host = host_mod.ModuleHost(kit_root=ROOT, modules_dir=MODULES_DIR).load()

    def test_it_is_catalogued_and_loaded(self) -> None:
        row = next((m for m in self.host.table()["modules"] if m["id"] == MID), None)
        self.assertIsNotNone(row, "lygo.llminfo is not in the table")
        # the table publishes the manifest's `lifecycle` under the row key `state` (host._load_one)
        # pin the ladder, not a transient rung (the row publishes the manifest's `lifecycle`)
        ladder = ("PROPOSED", "SPEC'D", "SCAFFOLDED", "WIRED", "TESTED", "PARITY", "RELEASED", "SEALED")
        self.assertIn(row["state"], ladder)
        self.assertGreaterEqual(ladder.index(row["state"]), ladder.index("WIRED"))

    def test_the_catalog_and_the_tree_agree(self) -> None:
        catalog = json.loads((MODULES_DIR / "catalog.json").read_text(encoding="utf-8"))
        declared = [m["id"] for m in catalog["modules"]]
        on_disk = sorted(p.name for p in MODULES_DIR.glob("lygo.*") if (p / "module.json").exists())
        self.assertEqual(sorted(declared), on_disk, "catalog and module directories disagree")

    def test_its_route_is_wired_and_nobody_else_owns_it(self) -> None:
        self.assertIn(("GET", "/api/llminfo"), self.host.routes)
        self.assertEqual(self.host.routes[("GET", "/api/llminfo")]["module"], MID)
        self.assertFalse(self.host.routes[("GET", "/api/llminfo")]["legacy"])

    def test_its_pane_reaches_the_shell_through_the_table(self) -> None:
        row = next(m for m in self.host.table()["modules"] if m["id"] == MID)
        panes = [p if isinstance(p, str) else p.get("id") for p in row["panes"]]
        self.assertIn("dock.llm", panes, "the pane id is how the shell places it without an edit")
        # A pane id is slot-prefixed and the manifest's own `slot` field has to agree with that
        # prefix, because the shell places a pane by the prefix alone - a mismatch would put the
        # pane somewhere the module never declared. 'dock' is the console page's bottom strip:
        # full length, on card per module, with the PAGE scrolling to it.
        for p in row["panes"]:
            if isinstance(p, str):
                continue
            pid = str(p.get("id") or "")
            self.assertIn(pid.split(".")[0], ("dock", "status", "side", "main"))
            self.assertEqual(pid.split(".")[0], p.get("slot"), "the pane id prefix and its slot disagree")
        self.assertFalse(any(isinstance(p, dict) and p.get("inner_scroll", False) for p in row["panes"]),
                         "the strip is a page-scroll host: a pane must not scroll inside itself as well")

    def test_the_host_answers_the_route(self) -> None:
        fake = FakeHandler("/api/llminfo")
        self.assertTrue(self.host.handle(fake, "GET", "/api/llminfo"))
        payload = fake.last
        self.assertEqual(payload["signature"], "Δ9Φ963-LYGO-LLMINFO-v1")

    def test_it_declares_its_surfaces_truthfully(self) -> None:
        mf = json.loads((MODULES_DIR / MID / "module.json").read_text(encoding="utf-8"))
        self.assertEqual(mf["surfaces"]["pc"], "FULL")
        # promoted in 1.2.0 (A7): carried byte-identical to the stick and tested there
        self.assertTrue(mf["surfaces"]["usb"].startswith("FULL"), "the stick carries this panel now")
        self.assertTrue(mf["surfaces"]["web"].startswith("N/A("), "and the web says why it does not")
        self.assertIn("steward decision", mf["surfaces"]["web"])

    def test_it_declares_no_state_it_does_not_own(self) -> None:
        mf = json.loads((MODULES_DIR / MID / "module.json").read_text(encoding="utf-8"))
        self.assertEqual(mf["state"]["owns"], [])
        self.assertEqual(mf["limbs"], [])

    def test_the_console_version_floor_is_stated(self) -> None:
        mf = json.loads((MODULES_DIR / MID / "module.json").read_text(encoding="utf-8"))
        self.assertTrue(mf["requires"]["console"].startswith(">="))


class LocalModelAvailability(unittest.TestCase):
    """The panel must show what this PC can run AND what it can see but cannot run.

    The operator asked for oversized models to be visible, so a record that cannot load is asserted
    to be present in the panel and asserted NOT to be counted as runnable. The verdicts come from
    the shipped `model_fit.annotate` at fixed VRAM/RAM, so these say the same thing on any host.
    """

    VRAM, RAM = 7075, 32581

    def _record(self, mid: str, gib: float, kind: str = "chat") -> dict:
        import model_fit

        rec = {
            "id": mid,
            "path": "--",
            "kind": kind,
            "ctx": 8192,
            "bytes": int(gib * 1024**3),
            "runnable": True,
        }
        model_fit.annotate(rec, dims=None, vram_free_mib=self.VRAM, ram_total=self.RAM)
        return rec

    def _build(self, records: list) -> dict:
        import registry

        mod = load_backend()
        payload = {"models": records, "selected": records[0]["id"]}
        with patch.object(registry, "load", return_value=payload):
            return mod.build(ctx=None)

    def _group(self, out: dict) -> dict:
        return next(g for g in out["groups"] if g["title"] == "Models here")

    def test_a_model_too_large_to_run_is_still_named(self):
        out = self._build([self._record("small-3b", 2.0), self._record("huge-300b", 300.0)])
        row = next(r for r in self._group(out)["rows"] if r["k"] == "huge-300b")
        self.assertIn("NOT run", row["v"], "a model that cannot run must still be visible and honest")

    def test_a_model_that_cannot_run_is_never_counted_as_runnable(self):
        out = self._build([self._record("small-3b", 2.0), self._record("huge-300b", 300.0)])
        counts = {r["k"]: r["v"] for r in self._group(out)["rows"]}
        self.assertEqual(counts["Runnable now"], "1")
        self.assertEqual(counts["Visible, not runnable"], "1")
        self.assertEqual(counts["Visible here"], "2 model(s)")

    def test_the_projector_is_not_counted_as_a_model(self):
        out = self._build([self._record("small-3b", 2.0), self._record("mm", 0.18, kind="mmproj")])
        counts = {r["k"]: r["v"] for r in self._group(out)["rows"]}
        self.assertEqual(counts["Visible here"], "1 model(s)")

    def test_no_verdicts_yet_is_admitted_rather_than_rendered_blank(self):
        # A pre-existing registry has no fit blocks; the panel must name that, not look empty.
        unjudged = {"id": "unjudged", "path": "--", "kind": "chat", "bytes": 1024, "runnable": True}
        out = self._build([unjudged])
        self.assertFalse([g for g in out["groups"] if g["title"] == "Models here"])
        self.assertTrue(any("placement verdict" in m for m in out["missing"]), out["missing"])

    def test_when_nothing_can_run_the_card_goes_amber_not_green(self):
        out = self._build([self._record("huge-300b", 300.0)])
        light = next(l for l in out["lights"] if l["id"] == "models")
        self.assertEqual(light["state"], "amber")
        self.assertIn("0 runnable", light["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
