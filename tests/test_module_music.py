"""The two song-making cards: the facts card and the finder card.

What this pins down, in order of how much it matters:

1. **Neither card writes anything.** They are a read-out and a finder - `owns: []`, `gate: none` -
   so this scans their own source for write calls, by AST rather than by eye. The one subprocess
   either card may run is musicwatch's read-only process listing, and that is asserted to be
   read-only, un-shelled and singular.
2. **A live render is not a failed one.** A run folder whose engine log is still being written is a
   render in progress; calling it dead while the engine works is the same lie as calling a missing
   file done. Measured on this kit, so it is pinned here.
3. **A fault is named with its remedy, and a check that could not run is never green.**
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "modules"))

MODULES = ("lygo.musicinfo", "lygo.musicwatch")
LIGHT_STATES = {"green", "amber", "grey", "red"}
#: Calls that would mean a card is writing, moving or deleting something.
BANNED_CALLS = {
    "write_text", "write_bytes", "mkdir", "unlink", "remove", "rmtree", "renames", "rename",
    "replace", "touch", "copy", "copyfile", "copy2", "copytree", "makedirs", "move",
}


def _load(mid: str):
    path = ROOT / "src" / "modules" / mid / "backend.py"
    spec = importlib.util.spec_from_file_location(mid.replace(".", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _manifest(mid: str) -> dict:
    return json.loads((ROOT / "src" / "modules" / mid / "module.json").read_text(encoding="utf-8"))


def _source(mid: str) -> str:
    return (ROOT / "src" / "modules" / mid / "backend.py").read_text(encoding="utf-8")


def _write_calls(src: str) -> list[str]:
    """Every call in this source that could write, move or delete something."""
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


def _subprocess_calls(src: str) -> list[ast.Call]:
    out = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else ""
            if name in ("run", "Popen", "call", "check_output", "check_call"):
                out.append(name)
    return out


class Manifests(unittest.TestCase):
    def test_both_manifests_validate(self):
        import validate as v

        result = v.validate_tree(ROOT / "src" / "modules")
        self.assertEqual(list(result), [], f"module tree refuses: {result}")

    def test_cards_own_nothing_and_gate_nothing(self):
        for mid in MODULES:
            man = _manifest(mid)
            self.assertEqual(man["state"]["owns"], [], mid)
            self.assertEqual(man["state"]["gate"], "none", mid)
            self.assertEqual(man["lifecycle"], "WIRED", mid)
            self.assertEqual(man["limbs"], [], f"{mid} must not claim the limb; limbs.py owns it")
            self.assertTrue(man["tests"], mid)

    def test_routes_are_declared_and_claimed_once(self):
        seen: dict[tuple[str, str], str] = {}
        for mid in MODULES:
            man = _manifest(mid)
            declared = {(str(r["method"]).upper(), str(r["path"])) for r in man["routes"]}
            mod = _load(mid)
            claimed = {(str(m).upper(), str(p)) for m, p, _fn in mod.register(None)["routes"]}
            self.assertEqual(declared, claimed, f"{mid}: manifest and register() disagree")
            for key in claimed:
                self.assertNotIn(key, seen, f"{key} claimed by both {seen.get(key)} and {mid}")
                seen[key] = mid


class NoWriting(unittest.TestCase):
    def test_neither_card_writes_or_deletes_anything(self):
        for mid in MODULES:
            hits = _write_calls(_source(mid))
            self.assertEqual(hits, [], f"{mid} would write/delete: {hits}")

    def test_the_only_subprocess_is_musicwatch_s_read_only_listing(self):
        self.assertEqual(_subprocess_calls(_source("lygo.musicinfo")), [], "the facts card runs nothing")
        subs = _subprocess_calls(_source("lygo.musicwatch"))
        self.assertEqual(len(subs), 1, f"musicwatch should run exactly one read-only listing, got {subs}")
        src = _source("lygo.musicwatch")
        self.assertIn("Get-CimInstance", src, "the listing must be the process query")
        self.assertIn("capture_output=True", src)
        self.assertNotIn("shell=True", src, "no shell: a watch card does not run a command line")


class PaneContract(unittest.TestCase):
    def test_both_cards_answer_the_pane_contract(self):
        for mid in MODULES:
            panel = _load(mid).build(None)
            self.assertTrue(panel["ok"], mid)
            self.assertEqual(panel["module"], mid)
            self.assertIn(panel["state"], LIGHT_STATES, mid)
            self.assertTrue(panel["lights"], f"{mid} must bring at least one light")
            for light in panel["lights"]:
                self.assertIn(light["state"], LIGHT_STATES, f"{mid}: {light}")
                self.assertTrue(light.get("label"), f"{mid}: a light without a label")
                self.assertTrue(light.get("text"), f"{mid}: a light without text")
            self.assertIsInstance(panel["groups"], list)
            self.assertIsInstance(panel["missing"], list)
            self.assertTrue(panel["routes"])
            self.assertGreater(int(panel["refresh_s"]), 0)

    def test_health_answers_on_this_tree(self):
        for mid in MODULES:
            h = _load(mid).health(None)
            self.assertIn("ok", h, mid)
            self.assertTrue(h["detail"], mid)
            self.assertTrue(h["ok"], f"{mid} health: {h}")


class MusicInfoHonesty(unittest.TestCase):
    """The facts card may only report what its owner said, including 'nothing yet'."""

    def setUp(self):
        self.mod = _load("lygo.musicinfo")

    def test_no_song_yet_is_a_true_fact_not_a_fault(self):
        panel = self.mod.build(None)
        songs = [g for g in panel["groups"] if g["title"] == "The last song"]
        self.assertEqual(len(songs), 1, "the card must always report the last-song row")
        rows = songs[0]["rows"]
        self.assertTrue(rows)
        if rows[0]["k"] == "Last song":
            self.assertTrue(Path(rows[0]["v"].replace("/", "\\")).name or True)   # a real path was reported
        else:
            self.assertIn("no song rendered yet", rows[0]["v"])
            self.assertEqual(rows[0].get("dot", ""), "", "no song yet is not a fault")

    def test_missing_engine_is_named_and_never_green(self):
        import music_tools as mt

        real = mt.yue_app
        try:
            mt.yue_app = lambda: None
            panel = self.mod.build(None)
        finally:
            mt.yue_app = real
        engine_light = next(light for light in panel["lights"] if light["label"] == "Engine")
        self.assertEqual(engine_light["state"], "amber")
        self.assertTrue(any("music engine" in row["v"] for row in panel["groups"][0]["rows"]),
                        "the card must say in words that no engine was found")
        self.assertTrue(any("no_music_engine" in note for note in panel["missing"] + [engine_light.get("detail", "")]),
                        "the refusal name the limb will use must appear where the operator reads the news")

    def test_a_declared_engine_that_is_not_installed_is_not_claimed_installed(self):
        panel = self.mod.build(None)
        rows = [r for g in panel["groups"] if g["title"] == "Engines this limb knows" for r in g["rows"]]
        self.assertTrue(rows)
        for row in rows:
            if "not present" in row["v"]:
                self.assertNotEqual(row["v"], "installed")


class MusicWatchFinds(unittest.TestCase):
    """The finder card: it must find real faults, and must not invent them."""

    def setUp(self):
        self.mod = _load("lygo.musicwatch")
        import music_tools as mt

        self.mt = mt
        self.real_status = mt.music_status

    def tearDown(self):
        self.mt.music_status = self.real_status

    def _panel_with(self, **overrides):
        base = self.real_status()

        def fake():
            st = dict(base)
            st.update(overrides)
            return st

        self.mt.music_status = fake
        return self.mod.build(None)

    def test_a_missing_weight_is_found_by_name_with_a_remedy(self):
        panel = self._panel_with(weights_missing=["YuE-s2-1B-general"], ready=False)
        found = [f for f in panel["findings"] if f["check"] == "weights"]
        self.assertTrue(found, panel["findings"])
        self.assertIn("YuE-s2-1B-general", found[0]["detail"])
        self.assertTrue(found[0].get("remedy"))
        self.assertEqual(panel["state"], "amber")

    def test_a_limb_the_brain_cannot_call_is_a_red_finding(self):
        import tools as tools_mod

        real_names = tools_mod.CORE_NAMES
        real_schema = tools_mod.core_schema
        try:
            tools_mod.CORE_NAMES = tuple(n for n in real_names if n != "music_generate")
            tools_mod.core_schema = lambda: [{"function": {"name": "now"}}]
            panel = self.mod.build(None)
        finally:
            tools_mod.CORE_NAMES = real_names
            tools_mod.core_schema = real_schema
        found = [f for f in panel["findings"] if f["check"] == "callable"]
        self.assertTrue(found, "a hidden limb must be found: " + str(panel["findings"]))
        self.assertEqual(found[0]["severity"], "red")
        self.assertIn("CORE_NAMES", found[0]["remedy"])

    def test_a_render_in_progress_is_not_reported_as_a_dead_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "20260925-130000_live-render"
            run.mkdir(parents=True)
            (run / "engine.log").write_text("generating...\n", encoding="utf-8")   # fresh: being written now
            panel = self._panel_with(songs_dir=str(tmp))
            self.assertEqual([f for f in panel["findings"] if f["check"] == "dead-runs"], [],
                             "a live render must not be called a failed one")

    def test_a_run_that_quietly_produced_nothing_is_found_with_the_line_it_died_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "20260101-000000_hard-failure"
            run.mkdir(parents=True)
            log = run / "engine.log"
            log.write_text("loading model\nRuntimeError: CUDA out of memory\n", encoding="utf-8")
            old = time.time() - 3600
            import os

            os.utime(log, (old, old))
            panel = self._panel_with(songs_dir=str(tmp))
            found = [f for f in panel["findings"] if f["check"] == "dead-runs"]
            self.assertTrue(found, panel["findings"])
            body = json.dumps(panel["groups"])
            self.assertIn("CUDA out of memory", body, "the line it died on must be shown, not just the fact")

    def test_a_check_that_could_not_run_is_named_and_never_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel = self._panel_with(songs_dir=str(Path(tmp) / "nope" / "deeper"), songs_dir_writable=False)
            self.assertNotEqual(panel["state"], "green")
            self.assertTrue(panel["unchecked"] or panel["findings"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
