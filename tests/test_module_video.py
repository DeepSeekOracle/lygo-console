"""Tests for the video making card (lygo.videoinfo).

Δ9Φ963-LYGO-VIDEOINFO-TESTS-v1

What these are for
    The video card is a READ-OUT: it reports whether this machine can make a clip and what it last
    made. So the tests are about honesty and containment rather than about pixels:
      * the manifest validates and the card owns nothing and gates nothing;
      * its own source is scanned, by AST rather than by eye, for any call that could write, move
        or delete something - the card reports on clips, it never makes or touches one;
      * the three ways a video engine can be unusable stay SEPARATE and are named separately,
        because collapsing them is how a missing weight file reads as a missing engine;
      * no clip yet is a TRUE fact and stays green, exactly as an empty vault does.

What they deliberately do not do
    Render anything, or reach a live engine. Every test that needs engine state patches
    `video_harness` instead of starting one: a test suite must not take the operator's card, and a
    render is minutes.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "modules"))

MID = "lygo.videoinfo"

#: Calls that would mean a card is writing, moving or deleting something.
BANNED_CALLS = {
    "write_text", "write_bytes", "mkdir", "unlink", "remove", "rmtree", "renames", "rename",
    "replace", "touch", "copy", "copyfile", "copy2", "copytree", "makedirs", "move",
}


def _load(mid: str = MID):
    path = ROOT / "src" / "modules" / mid / "backend.py"
    spec = importlib.util.spec_from_file_location(mid.replace(".", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _manifest(mid: str = MID) -> dict:
    return json.loads((ROOT / "src" / "modules" / mid / "module.json").read_text(encoding="utf-8"))


def _source(mid: str = MID) -> str:
    return (ROOT / "src" / "modules" / mid / "backend.py").read_text(encoding="utf-8")


def _write_calls(src: str) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
        if name in BANNED_CALLS:
            hits.append(name)
        if name == "open":
            mode = ""
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            if any(ch in mode for ch in "wax+"):
                hits.append(f"open(mode={mode!r})")
    return hits


def _subprocess_calls(src: str) -> list[str]:
    out = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else ""
            if name in ("run", "Popen", "call", "check_output", "check_call"):
                out.append(name)
    return out


def _vh():
    import video_harness

    return video_harness


class Manifests(unittest.TestCase):
    def test_the_module_tree_validates(self):
        import validate as v

        result = v.validate_tree(ROOT / "src" / "modules")
        self.assertEqual(list(result), [], f"module tree refuses: {result}")

    def test_the_card_owns_nothing_and_gates_nothing(self):
        man = _manifest()
        self.assertEqual(man["state"]["owns"], [])
        self.assertEqual(man["state"]["gate"], "none")
        self.assertEqual(man["lifecycle"], "WIRED")
        self.assertEqual(man["limbs"], [], "the card must not claim the limb; limbs.py owns it")
        self.assertTrue(man["tests"])

    def test_it_is_catalogued_once(self):
        cat = json.loads((ROOT / "src" / "modules" / "catalog.json").read_text(encoding="utf-8"))
        ids = [m["id"] for m in cat["modules"]]
        self.assertEqual(ids.count(MID), 1, "the card must appear exactly once in the catalog")

    def test_the_route_is_declared_and_claimed_once(self):
        man = _manifest()
        declared = {(r["method"].upper(), r["path"]) for r in man["routes"]}
        self.assertEqual(declared, {("GET", "/api/videoinfo")})
        mod = _load()
        reg = mod.register(None)
        claimed = {(m.upper(), p) for m, p, _fn in reg["routes"]}
        self.assertEqual(claimed, declared, "what register() claims must equal what the manifest declares")

    def test_register_returns_the_host_contract(self):
        """The host reads routes/limbs/panes/health. A receipt-shaped return reads as
        'registered nothing', which is how a correctly written card gets REFUSED."""
        reg = _load().register(None)
        for key in ("routes", "limbs", "panes", "health"):
            self.assertIn(key, reg, f"register() must carry {key!r} for the host")
        self.assertTrue(callable(reg["health"]))
        self.assertEqual(reg["limbs"], [])
        self.assertTrue(reg["panes"][0]["id"])


class NoWriting(unittest.TestCase):
    def test_the_card_writes_or_deletes_nothing(self):
        self.assertEqual(_write_calls(_source()), [], "the card must never write, move or delete")

    def test_the_card_shells_out_to_nothing(self):
        self.assertEqual(_subprocess_calls(_source()), [], "the card must not run a process")


class PaneContract(unittest.TestCase):
    def test_the_pane_is_answered(self):
        with mock.patch.object(_vh(), "probe", return_value={"up": False, "reason": "not running"}):
            panel = _load().build()
        self.assertIn("groups", panel)
        self.assertIn("light", panel)
        self.assertIn("missing", panel)
        self.assertTrue(panel["groups"], "an empty card is not an answer")

    def test_health_answers_on_this_tree(self):
        with mock.patch.object(_vh(), "probe", return_value={"up": False, "reason": "not running"}):
            got = _load().health(None)
        self.assertIn("ok", got)


class Honesty(unittest.TestCase):
    """The rules that make this card worth reading."""

    def test_no_clip_yet_is_a_true_fact_and_stays_green(self):
        """A fresh kit has made no clip. That is true, not a fault."""
        empty = Path(tempfile.mkdtemp())
        with mock.patch.object(_vh(), "workspace_dir", return_value=empty), \
                mock.patch.object(_vh(), "probe", return_value={"up": True, "version": "x",
                                                                "device": "d", "base": "b"}):
            panel = _load().build()
        titles = [g["title"] for g in panel["groups"]]
        self.assertIn("Last clip", titles)
        self.assertTrue(panel["ok"], "an empty clip folder is not a fault")

    def test_a_missing_engine_is_named_and_never_green(self):
        with mock.patch.object(_vh(), "app_state",
                               return_value={"present": False, "root": "", "python_present": False,
                                             "reason": "no app_root is declared"}), \
                mock.patch.object(_vh(), "adapter_state",
                                  return_value={"present": False, "reason": "no install"}), \
                mock.patch.object(_vh(), "models", return_value=[]), \
                mock.patch.object(_vh(), "probe", return_value={"up": False, "reason": "down"}):
            panel = _load().build()
        self.assertFalse(panel["ok"])
        self.assertNotEqual(panel["light"]["state"], "ok", "a missing engine must not read green")
        self.assertTrue(any(str(m).startswith("engine") for m in panel["missing"]),
                        f"the missing engine must be named: {panel['missing']}")

    def test_a_missing_weight_is_named_by_file_and_not_folded_away(self):
        with mock.patch.object(_vh(), "weights_state",
                               return_value={"present": False, "files": [
                                   {"role": "diffusion", "name": "gone.gguf", "present": False,
                                    "bytes": 0}]}), \
                mock.patch.object(_vh(), "probe", return_value={"up": False, "reason": "down"}):
            panel = _load().build()
        self.assertTrue(any("gone.gguf" in str(m) for m in panel["missing"]),
                        f"the absent weight must be named: {panel['missing']}")

    def test_the_three_ways_to_be_unusable_are_kept_apart(self):
        """App missing / loader not registered / weights absent are three different facts with
        three different remedies. One `installed` boolean is how they get confused."""
        vh = _vh()
        base_app = {"present": True, "root": "R", "python_present": True}
        base_w = {"present": True, "files": [], "missing": []}
        cases = [
            ({"present": False, "root": "", "reason": "no app_root"}, {"present": True},
             base_w, "engine_not_installed"),
            (base_app, {"present": False, "reason": "loader abs"}, base_w, "adapter_not_installed"),
            (base_app, {"present": True}, {"present": False, "files": [], "missing": ["x.gguf"],
                                           "reason": "x.gguf missing"}, "weights_missing"),
        ]
        for app, ad, w, expected in cases:
            with mock.patch.object(vh, "app_state", return_value=app), \
                    mock.patch.object(vh, "adapter_state", return_value=ad), \
                    mock.patch.object(vh, "weights_state", return_value=w):
                state = vh.model_state("wan22-5b")
            self.assertFalse(state["drivable"], expected)
            self.assertTrue(str(state.get("reason", "")).startswith(expected),
                            f"expected {expected!r}, got {state.get('reason')!r}")


if __name__ == "__main__":
    unittest.main()
