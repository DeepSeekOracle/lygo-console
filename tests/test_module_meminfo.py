"""lygo.meminfo — the module contract, the honesty gate, and the read-only guarantee.

Runs against the shipped tree (not a copy), because two of the things that can go wrong here are
things a copy cannot show: a manifest that the HOST refuses, and a module that writes state.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULES_DIR = ROOT / "src" / "modules"
MID = "lygo.meminfo"
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from modules import validate  # noqa: E402  (after sys.path, deliberately)

LADDER = ("PROPOSED", "SPEC'D", "SCAFFOLDED", "WIRED", "TESTED", "PARITY", "RELEASED", "SEALED")

#: Calls that change the filesystem. A read-out module owns no state, so it may use none of them.
_WRITES = re.compile(
    r"\.write_text\(|\.write_bytes\(|atomic_write|\.mkdir\(|os\.makedirs\(|os\.remove\(|os\.unlink\("
    r"|os\.replace\(|shutil\.|\.unlink\(|tempfile\."
)


def _load(name: str):
    """The module host imports an adapter by FILE (its id contains a dot, so it is not a package)."""
    path = MODULES_DIR / name / "backend.py"
    spec = importlib.util.spec_from_file_location("_mod_" + name.replace(".", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MeminfoContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads((MODULES_DIR / MID / "module.json").read_text(encoding="utf-8"))
        cls.backend = _load(MID)
        cls.source = (MODULES_DIR / MID / "backend.py").read_text(encoding="utf-8")

    # --- the manifest the host actually enforces ---------------------------------------------
    def test_manifest_passes_the_validator(self) -> None:
        problems = validate.validate_manifest(self.manifest, MODULES_DIR / MID, MID)
        self.assertEqual(problems, [], f"the host would REFUSE this module: {problems}")

    def test_family_matches_the_id_suffix(self) -> None:
        """An id that says one family while declaring another mis-signals severity to the reader."""
        self.assertTrue(MID.endswith(self.manifest["family"]), f"{MID} must end in {self.manifest['family']}")
        self.assertEqual(self.manifest["family"], "info", "a read-out, not a finder")

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
                self.assertRegex(
                    value, r"^(DEGRADED|N/A)\(.+\)$", f"{edition} must carry a written reason, got {value!r}"
                )

    # --- routes, limbs, panes: declared == registered ----------------------------------------
    def test_declared_routes_match_what_register_returns(self) -> None:
        reg = self.backend.register(None)
        declared = {(r["method"], r["path"]) for r in self.manifest["routes"]}
        registered = {(m, p) for m, p, _fn in reg["routes"]}
        self.assertEqual(declared, registered, "a declared route the adapter does not register refuses the module")
        self.assertEqual(registered, set(self.backend.ROUTES), "ROUTES is the declared table, and must agree")

    def test_it_declares_no_limbs(self) -> None:
        """A read-out offers the model no new verb."""
        self.assertEqual(self.manifest["limbs"], [])
        self.assertEqual(self.backend.register(None)["limbs"], [])

    def test_pane_id_is_slot_prefixed_and_the_slot_agrees(self) -> None:
        panes = self.manifest["panes"]
        self.assertEqual(len(panes), 1)
        self.assertEqual(panes[0]["id"], "dock.mem")
        self.assertTrue(panes[0]["id"].startswith(panes[0]["slot"] + "."))
        self.assertEqual(panes[0]["slot"], "dock")
        self.assertFalse(panes[0]["inner_scroll"], "the page scrolls, not the pane")

    def test_it_owns_no_state_and_opens_no_gate(self) -> None:
        state = self.manifest["state"]
        self.assertEqual(state["owns"], [], "a read-out owns no state")
        self.assertEqual(state["gate"], "none")

    # --- the read-only guarantee, scanned from its own source ---------------------------------
    def test_the_module_source_contains_no_write_calls(self) -> None:
        hits = [m.group(0) for m in _WRITES.finditer(self.source)]
        self.assertEqual(hits, [], f"a read-out may write nothing, found: {sorted(set(hits))}")

    def test_it_does_not_reimplement_the_token_budget(self) -> None:
        """The window/context budget belongs to lygo.llminfo; this card must NAME that owner."""
        panel = self.backend.build(None)
        blob = json.dumps(panel, ensure_ascii=False)
        self.assertIn("lygo.llminfo", blob, "the card must name the owner of the token budget")
        self.assertNotIn("ctx_native", blob)


class MeminfoPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = _load(MID)

    def test_the_card_reads_on_this_tree(self) -> None:
        panel = self.backend.build(None)
        self.assertTrue(panel["ok"])
        self.assertIn(panel["state"], ("green", "amber", "red", "grey"))
        self.assertTrue(panel["groups"], "an empty card reads as 'no data'")
        for group in panel["groups"]:
            self.assertTrue(group["title"])
            for row in group["rows"]:
                self.assertIn("k", row)
                self.assertIn("v", row)
                self.assertNotIn("\n", str(row["v"]))
        for light in panel["lights"]:
            self.assertIn(light["state"], ("green", "amber", "red", "grey"))
            self.assertIn("label", light)

    def test_green_requires_every_fact_owner_to_have_answered(self) -> None:
        with mock.patch.object(self.backend, "_owner", side_effect=lambda n: None):
            panel = self.backend.build(None)
        self.assertNotEqual(panel["state"], "green", "a card that could not read its sources must not read green")
        self.assertIn(panel["state"], ("grey", "amber"))
        self.assertTrue(panel["missing"], "an unread source is a NAMED gap")
        named = " ".join(panel["missing"]).lower()
        self.assertIn("compaction", named)
        self.assertIn("unchecked", named)

    def test_a_health_failure_is_never_filled_in_with_a_zero(self) -> None:
        """The failure that matters: an unread store must not read as '0 sessions'."""
        with mock.patch.object(self.backend, "_owner", side_effect=lambda n: None):
            panel = self.backend.build(None)
        blob = json.dumps(panel, ensure_ascii=False)
        self.assertNotIn("0 sessions filed", blob)
        self.assertNotIn("nothing sealed yet", blob)

    def test_a_genuinely_empty_store_is_a_fact_not_a_gap(self) -> None:
        """The other half of the rule: zero read IS zero, and must not be reported as unchecked."""
        empty = {
            "ok": True, "sessions": 0, "turns": 0, "bytes": 0, "zip_bytes": 0, "months": [],
            "newest": None, "catalog": "x", "unfiled_journals": [], "legacy_files": 0,
        }
        with mock.patch.object(self.backend, "_owner", side_effect=lambda n: object()), mock.patch.object(
            self.backend, "_vault", return_value=empty
        ):
            rows = self.backend._vault_rows(empty)
        values = {r["k"]: r["v"] for r in rows}
        self.assertEqual(values["Sessions filed"], "0")
        self.assertIn("nothing filed yet", values["Newest filed"])
        self.assertNotIn("dot", values.get("Newest filed", ""), "an empty vault is not a finding")

    def test_a_broken_record_is_named_and_not_swallowed(self) -> None:
        broken = type("M", (), {"safe_status": staticmethod(lambda *a, **k: {"ok": False, "error": "boom"})})
        with mock.patch.object(self.backend, "_owner", side_effect=lambda n: broken if n == "compaction" else None):
            panel = self.backend.build(None)
        self.assertNotEqual(panel["state"], "green")
        self.assertTrue(any("boom" in m for m in panel["missing"]), "the owner's own error must reach the reader")


class MeminfoFormatterTests(unittest.TestCase):
    """The edge: a number becoming something a human reads."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = _load(MID)

    def test_bytes_never_render_as_zero_GB(self) -> None:
        self.assertEqual(self.backend._size(288432), "281.67 KB")
        self.assertEqual(self.backend._size(1024), "1.00 KB")
        self.assertEqual(self.backend._size(0), "0 B")
        self.assertNotIn("GB", self.backend._size(300_000))

    def test_an_epoch_float_renders_as_a_time(self) -> None:
        stamp = self.backend._stamp(1789506052.569)
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertEqual(self.backend._stamp(None), "")
        self.assertEqual(self.backend._stamp("not a number"), "")

    def test_never_reads_as_a_time(self) -> None:
        self.assertEqual(self.backend._when(None), "never")
        self.assertEqual(self.backend._when(""), "never")


if __name__ == "__main__":
    unittest.main()
