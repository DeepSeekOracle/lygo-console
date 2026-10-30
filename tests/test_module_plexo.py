"""The download pair's contract: two manifests, two routes, one strip, and a card that only reads.

A module is a manifest the host loads and an adapter it calls. Both are asserted here by name, and the
rules this kit holds modules to are asserted too: the info card writes nothing, the control card's
buttons are actions the owner actually accepts, and `health()` on either card is cheap enough to run
inside the host's own `load()` (no socket, no spawn).
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "modules" / "lygo.plexoinfo"))
sys.path.insert(0, str(ROOT / "src" / "modules" / "lygo.plexoctl"))

import plexo_tools as pt  # noqa: E402

CATALOG = ROOT / "src" / "modules" / "catalog.json"
INFO_DIR = ROOT / "src" / "modules" / "lygo.plexoinfo"
CTRL_DIR = ROOT / "src" / "modules" / "lygo.plexoctl"


class FakeCtx:
    """The host context, as far as these two adapters use it."""

    def __init__(self) -> None:
        self.receipts: list[dict] = []
        self.logs: list[str] = []

    def receipt(self, prompt: str = "", output: str = "", extra: dict | None = None) -> None:
        self.receipts.append({"prompt": prompt, "output": output, "extra": extra or {}})

    def log(self, line: str) -> None:
        self.logs.append(str(line))


class FakeReq:
    def __init__(self, body: dict | None = None) -> None:
        self._body = body or {}
        self.code = 0
        self.payload: dict = {}

    def body(self, limit: int = 0) -> dict:
        return self._body

    def json(self, code: int, payload: dict) -> None:
        self.code = code
        self.payload = payload


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_backend(path: Path, name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path / "backend.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


class TestManifests(unittest.TestCase):
    def test_both_modules_are_in_the_catalogue_and_enabled(self) -> None:
        cat = json.loads(CATALOG.read_text(encoding="utf-8"))
        ids = [row["id"] for row in cat["modules"]]
        for mid in ("lygo.plexoinfo", "lygo.plexoctl"):
            self.assertIn(mid, ids, f"{mid} is not in the catalogue, so the host never loads it")
            row = [r for r in cat["modules"] if r["id"] == mid][0]
            self.assertTrue(row.get("enabled", True), f"{mid} is disabled in the catalogue")
            self.assertTrue(row.get("order"), f"{mid} has no load order")

    def test_the_ordered_catalogue_has_no_duplicate_ids(self) -> None:
        cat = json.loads(CATALOG.read_text(encoding="utf-8"))
        ids = [row["id"] for row in cat["modules"]]
        self.assertEqual(len(ids), len(set(ids)), "two modules share an id")

    def test_each_manifest_declares_what_the_host_needs(self) -> None:
        for path, mid, family in ((INFO_DIR, "lygo.plexoinfo", "info"), (CTRL_DIR, "lygo.plexoctl", None)):
            man = load_manifest(path / "module.json")
            self.assertEqual(mid, man["id"])
            self.assertEqual("WIRED", man["lifecycle"])
            self.assertEqual("backend.py", man["adapter"])
            self.assertTrue(man["requires"]["console"].startswith(">="))
            self.assertTrue(man["docs"], "a module names the spec a reader can find")
            self.assertTrue(man["tests"], "a module names the tests that hold it")
            self.assertTrue(man["panes"], "a module with no pane is invisible")
            self.assertTrue(man["summary"] and man["notes"], "the manifest carries the doctrine")
            if family:
                self.assertEqual(family, man["family"])
            else:
                self.assertEqual("p0", man["state"]["gate"], "the actuator half must sit behind the gate")
                self.assertIn("save/plexo", man["state"]["owns"])
                self.assertEqual([{"name": "plexo", "effect": "write"}], man["limbs"])

    def test_the_two_modules_do_not_claim_the_same_pane_id(self) -> None:
        a = load_manifest(INFO_DIR / "module.json")
        b = load_manifest(CTRL_DIR / "module.json")
        ids = [p["id"] for p in a["panes"] + b["panes"]]
        self.assertEqual(len(ids), len(set(ids)))


class TestInfoCard(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = load_backend(INFO_DIR, "plexoinfo_backend")
        self.ctx = FakeCtx()

    def test_the_card_reads_and_has_every_section(self) -> None:
        panel = self.mod.build(self.ctx, cheap=True)
        self.assertEqual(self.mod.SIGNATURE, panel["signature"])
        self.assertIn(panel["state"], ("green", "amber", "red"))
        self.assertEqual(3, len(panel["lights"]))
        titles = [g["title"] for g in panel["groups"]]
        self.assertIn("LYGO's own engine", titles)
        self.assertIn("Plexo, the operator's own install", titles)
        self.assertIn("What this kit will not do", titles)

    def test_the_info_card_never_writes(self) -> None:
        src = (INFO_DIR / "backend.py").read_text(encoding="utf-8")
        for bad in ("atomic_write", "open(", ".write_text", ".write(", "mkdir"):
            self.assertNotIn(bad, src, f"a read-out card must not contain {bad!r}")

    def test_the_card_says_what_is_missing_rather_than_failing(self) -> None:
        panel = self.mod.build(self.ctx, cheap=True)
        if not panel["plexo_installed"]:
            self.assertTrue(panel["missing"], "a missing Plexo must be named, not silently omitted")
            self.assertEqual("amber", panel["state"], "not fetched yet is amber, never red")

    def test_health_is_cheap_and_honest(self) -> None:
        called: list[str] = []
        real = pt.bridge
        pt.bridge = lambda *a, **k: called.append("bridge") or {"up": None, "state": "not checked"}  # type: ignore[assignment]
        try:
            got = self.mod.health(self.ctx)
        finally:
            pt.bridge = real  # type: ignore[assignment]
        self.assertTrue(got["ok"], got)
        self.assertEqual([], called, "health() runs inside the host's load(): it must open no socket")


class TestControlCard(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = load_backend(CTRL_DIR, "plexoctl_backend")
        self.ctx = FakeCtx()

    def test_every_button_is_an_action_the_owner_accepts(self) -> None:
        for btn in self.mod.PANEL_ACTIONS:
            self.assertIn(btn["action"], self.mod.ACTIONS, f"button {btn['label']!r} posts an unknown action")
            self.assertIn(btn["action"], pt.ACTIONS, f"{btn['action']!r} is not an action of the plexo limb")
            self.assertTrue(btn["label"] and btn["title"], "a button needs a label and a reason")

    def test_the_card_publishes_its_strip(self) -> None:
        panel = self.mod.build(self.ctx, cheap=True)
        self.assertEqual(len(self.mod.PANEL_ACTIONS), len(panel["actions"]))
        self.assertIn(panel["state"], ("green", "amber", "red"))

    def test_get_answers_the_card(self) -> None:
        req = FakeReq()
        self.mod.data(self.ctx, req)
        self.assertEqual(200, req.code)
        self.assertEqual(self.mod.SIGNATURE, req.payload["signature"])

    def test_an_unknown_action_is_refused_by_name(self) -> None:
        req = FakeReq({"action": "download_everything"})
        self.mod.control(self.ctx, req)
        self.assertEqual(400, req.code)
        self.assertEqual("unknown_action", req.payload["error"])
        self.assertEqual([], self.ctx.receipts, "a refused action leaves no receipt")

    def test_a_hand_off_without_consent_is_refused_and_leaves_a_receipt(self) -> None:
        req = FakeReq({"action": "hand_off", "url": "https://example.org/x.iso", "consent": False})
        self.mod.control(self.ctx, req)
        self.assertEqual(403, req.code)
        self.assertEqual("consent_required", req.payload["error"])
        self.assertEqual(1, len(self.ctx.receipts), "a mutating action leaves a receipt")
        self.assertEqual("plexo:hand_off", self.ctx.receipts[0]["prompt"].split(" ")[0])

    def test_a_url_with_no_url_is_400_not_a_crash(self) -> None:
        req = FakeReq({"action": "fetch"})
        self.mod.control(self.ctx, req)
        self.assertEqual(400, req.code)
        self.assertEqual("no_url", req.payload["error"])

    def test_stop_refuses_when_this_console_started_nothing(self) -> None:
        req = FakeReq({"action": "stop"})
        self.mod.control(self.ctx, req)
        self.assertEqual(409, req.code)
        self.assertFalse(req.payload["ok"])
        self.assertIn("did not start", str(req.payload.get("why") or ""))

    def test_health_is_cheap_and_has_buttons(self) -> None:
        got = self.mod.health(self.ctx)
        self.assertTrue(got["ok"], got)
        self.assertGreaterEqual(got.get("buttons", 0), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
