"""lygo.delegatectrl - the module contract, the honesty gate, and the no-surprise guarantees.

Three things this pins, in order of how much they matter:

1. **Every test is structurally incapable of spending a request or starting a process.** `setUp` patches
   BOTH owner functions with a recorder that raises, so the only way any test here reaches a side agent
   is by replacing that patch with its own canned answer. A module whose test can start a second agent
   loop is a test that bills the operator's metered pool and can hang the suite.
2. **The card is honest about what has not been checked.** The roster is what the CONFIG says; only a
   probe is what the engines ANSWERED. A card that goes green from the roster alone would tell the
   operator the free pool works when nothing has asked it.
3. **A refusal reaches the operator with its remedy.** "Unexpected server error" is the free tier's
   mask for a limit, a rate limit, a retired model id and a broken directory alike, so the error code
   and the hint the limb wrote must both survive the POST.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULES_DIR = ROOT / "src" / "modules"
MID = "lygo.delegatectrl"
ROUTE = "/api/delegate"
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import delegate_tools  # noqa: E402
from modules import validate  # noqa: E402

LADDER = ("PROPOSED", "SPEC'D", "SCAFFOLDED", "WIRED", "TESTED", "PARITY", "RELEASED", "SEALED")
SURFACE_WORDS = ("FULL", "DEGRADED(", "N/A(")


def _load(name: str):
    """The host imports an adapter by FILE (its id contains a dot, so it is not a package)."""
    path = MODULES_DIR / name / "backend.py"
    spec = importlib.util.spec_from_file_location("_mod_" + name.replace(".", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeCtx:
    """The host's Ctx, with the signatures this module calls. save_dir is a temp dir."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.receipts: list[dict] = []
        self.logs: list[str] = []

    def save_dir(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def read_json(self, path, default=None):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return default

    def receipt(self, spec=None, **kw):
        self.receipts.append(kw)
        return {}

    def log(self, msg: str) -> None:
        self.logs.append(str(msg))


class FakeReq:
    def __init__(self, payload=None) -> None:
        self.payload = dict(payload or {})
        self.status = None
        self.sent = None

    def body(self, limit: int = 0):
        return dict(self.payload)

    def json(self, code, payload):
        self.status = code
        self.sent = payload


class DelegateBase(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.tmp = Path(tempfile.mkdtemp(prefix="lygo-delegate-test-"))
        self.backend = _load(MID)
        self.ctx = FakeCtx(self.tmp)
        # STRUCTURAL GUARD: the real owner functions cannot be reached from any test in this file.
        self._run = mock.patch.object(delegate_tools, "subagent_run",
                                      side_effect=AssertionError("a test reached a real side agent"))
        self._pool = mock.patch.object(delegate_tools, "subagent_pool",
                                       side_effect=AssertionError("a test reached a real pool probe"))
        self.run_guard = self._run.start()
        self.pool_guard = self._pool.start()
        self.addCleanup(self._run.stop)
        self.addCleanup(self._pool.stop)
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def _post(self, payload: dict):
        req = FakeReq(payload)
        self.backend.control(self.ctx, req)
        return req


class TheTreeAcceptsIt(DelegateBase):
    def test_every_manifest_validates(self):
        problems = validate.validate_tree(MODULES_DIR, MODULES_DIR / "catalog.json")
        mine = [p for p in problems if MID in p]
        self.assertEqual(mine, [], f"the validator refuses this module: {mine}")
        self.assertEqual(problems, [], f"the tree has validation problems: {problems[:4]}")

    def test_the_manifest_keeps_every_ceiling(self):
        man = json.loads((MODULES_DIR / MID / "module.json").read_text(encoding="utf-8"))
        self.assertEqual(man["id"], MID)
        self.assertEqual(man["adapter"], "backend.py")
        self.assertLessEqual(len(man["summary"]), 200)
        self.assertNotIn("\n", man["summary"])
        self.assertIn(man["lifecycle"], LADDER)
        self.assertEqual(sorted(man["surfaces"]), ["pc", "usb", "web"])
        for surface, value in man["surfaces"].items():
            self.assertTrue(value.startswith(SURFACE_WORDS), f"{surface}={value!r} needs a fixed word")
        for route in man["routes"]:
            self.assertIsInstance(route, dict, "the host only understands the dict shape")
            self.assertTrue(route["path"].startswith("/api/"))
        self.assertEqual(man["routes"], [{"method": "GET", "path": ROUTE, "handler": "data", "legacy": False},
                                         {"method": "POST", "path": ROUTE, "handler": "control", "legacy": False}])
        self.assertEqual(man["panes"][0]["id"], "dock." + MID.split(".")[-1])

    def test_the_listing_law_holds(self):
        """`ctrl` is not a known family in the tree's own law, so no `family` field may be declared."""
        man = json.loads((MODULES_DIR / MID / "module.json").read_text(encoding="utf-8"))
        self.assertNotIn("family", man)

    def test_the_console_floor_is_the_shipped_release(self):
        man = json.loads((MODULES_DIR / MID / "module.json").read_text(encoding="utf-8"))
        shipped = (ROOT / "VERSION").read_text(encoding="utf-8").splitlines()[0].strip()
        self.assertEqual(man["requires"]["console"], ">=" + shipped)

    def test_the_catalog_lists_it_and_stays_ordered(self):
        cat = json.loads((MODULES_DIR / "catalog.json").read_text(encoding="utf-8"))
        rows = cat["modules"]
        mine = [r for r in rows if r["id"] == MID]
        self.assertEqual(len(mine), 1)
        self.assertTrue(mine[0]["enabled"])
        self.assertEqual([r["order"] for r in rows], sorted(r["order"] for r in rows),
                         "the file order must match the ascending order values")
        self.assertTrue((MODULES_DIR / MID).is_dir())

    def test_it_registers_no_limb_of_its_own(self):
        registered = self.backend.register(self.ctx)
        self.assertEqual(registered["limbs"], [],
                         "subagent_run/subagent_pool already live in limbs.py; listing them here would "
                         "register the same tools twice")
        self.assertEqual(registered["routes"], [("GET", ROUTE, self.backend.data),
                                                ("POST", ROUTE, self.backend.control)])
        self.assertEqual(registered["panes"][0]["id"], "dock.delegatectrl")
        self.assertNotIn("api/delegate", (ROOT / "src" / "server.py").read_text(encoding="utf-8"))


class TheButtonsAreTheActions(DelegateBase):
    def test_every_button_is_an_accepted_action(self):
        self.assertTrue(self.backend.PANEL_ACTIONS)
        for button in self.backend.PANEL_ACTIONS:
            self.assertIn(button["action"], self.backend.ACTIONS,
                          f"a button posts {button['action']!r}, which the action table does not accept")
            self.assertTrue(button["label"] and button["title"])

    def test_an_unknown_action_is_refused(self):
        req = self._post({"action": "launch_missiles"})
        self.assertEqual(req.status, 400)
        self.assertEqual(req.sent["error"], "unknown_action")
        self.assertEqual(req.sent["actions"], list(self.backend.ACTIONS))

    def test_the_card_publishes_the_buttons(self):
        card = self.backend.build(self.ctx)
        self.assertEqual([a["action"] for a in card["actions"]],
                         [a["action"] for a in self.backend.PANEL_ACTIONS])
        for key in ("state", "state_text", "lights", "groups", "actions", "missing"):
            self.assertIn(key, card)


class TheCardIsHonest(DelegateBase):
    def test_an_unprobed_pool_is_never_green(self):
        card = self.backend.build(self.ctx)
        pool_light = [x for x in card["lights"] if x["label"] == "Free pool"][0]
        self.assertEqual(pool_light["state"], "grey", "the roster is not evidence that an engine answers")
        self.assertIn("not checked", pool_light["text"])
        self.assertNotEqual(card["state"], "green")

    def test_a_probe_is_what_turns_it_green(self):
        (self.ctx.save_dir(self.backend.STATE_DIR) / "verified.json").write_text(
            json.dumps({"at": "2026-10-15 23:59:00",
                        "results": {"opencode/x-free": {"ok": True, "seconds": 4.1, "error": ""}},
                        "answering": ["opencode/x-free"]}), encoding="utf-8")
        with mock.patch.object(delegate_tools, "subagent_pool",
                               return_value={"free": ["opencode/x-free"], "default": "opencode/x-free",
                                             "engines": [{"engine": "opencode/x-free", "probed": True,
                                                          "ok": True, "seconds": 4.1}]}):
            card = self.backend.build(self.ctx)
        pool_light = [x for x in card["lights"] if x["label"] == "Free pool"][0]
        self.assertEqual(pool_light["state"], "green")
        self.assertEqual(card["state"], "green")

    def test_no_cli_is_an_amber_gap_that_names_the_install(self):
        with mock.patch.object(delegate_tools, "_exe", return_value=None):
            card = self.backend.build(self.ctx)
        self.assertTrue(any("opencode-ai" in gap for gap in card["missing"]), card["missing"])
        self.assertEqual([x for x in card["lights"] if x["label"] == "CLI"][0]["state"], "amber")


class ItSpendsNothingAtBoot(DelegateBase):
    def test_health_is_local_and_has_no_probe(self):
        with mock.patch.object(delegate_tools, "subagent_pool",
                               return_value={"free": ["opencode/x-free"], "default": "opencode/x-free",
                                             "engines": [{"engine": "opencode/x-free", "probed": False}]}) as pool:
            got = self.backend.health(self.ctx)
        self.assertTrue(got["ok"], got)
        self.assertFalse(got["probed_at_boot"])
        self.run_guard.assert_not_called()
        for call in pool.call_args_list:
            self.assertIs(call.kwargs.get("probe"), False, "a boot must never ask the metered pool")

    def test_health_reports_a_broken_owner_rather_than_passing(self):
        with mock.patch.object(delegate_tools, "subagent_pool", side_effect=RuntimeError("boom")):
            got = self.backend.health(self.ctx)
        # build() survives it (the card reports what it could not read), so health stays a card verdict
        self.assertTrue(got["ok"])
        card = self.backend.build(self.ctx)
        self.assertEqual([x for x in card["lights"] if x["label"] == "Free pool"][0]["state"], "grey")

    def test_the_page_refresh_never_probes(self):
        with mock.patch.object(delegate_tools, "subagent_pool",
                               return_value={"free": ["opencode/x-free"], "default": "opencode/x-free"}) as pool:
            req = FakeReq()
            self.backend.data(self.ctx, req)
        self.assertEqual(req.status, 200)
        for call in pool.call_args_list:
            self.assertIs(call.kwargs.get("probe"), False)


class TheActionsAnswerProperly(DelegateBase):
    def test_the_probe_records_what_answered(self):
        canned = {"free": ["opencode/a-free", "opencode/b-free"], "default": "opencode/a-free",
                  "engines": [{"engine": "opencode/a-free", "probed": True, "ok": True, "seconds": 3.0},
                              {"engine": "opencode/b-free", "probed": True, "ok": False, "seconds": 1.2,
                               "error": "engine_masked_error"}]}
        with mock.patch.object(delegate_tools, "subagent_pool", return_value=canned):
            req = self._post({"action": "probe"})
        self.assertEqual(req.status, 200)
        self.assertEqual(req.sent["answering"], ["opencode/a-free"])
        self.assertEqual(req.sent["probed"], 2)
        self.assertIn("card", req.sent)
        written = json.loads((self.ctx.save_dir(self.backend.STATE_DIR) / "verified.json").read_text(encoding="utf-8"))
        self.assertEqual(written["answering"], ["opencode/a-free"])
        self.assertEqual(self.ctx.receipts[-1]["extra"]["action"], "probe")

    def test_a_silent_pool_is_409_and_says_so(self):
        canned = {"free": ["opencode/a-free"], "default": "opencode/a-free",
                  "engines": [{"engine": "opencode/a-free", "probed": True, "ok": False,
                               "error": "engine_masked_error"}]}
        with mock.patch.object(delegate_tools, "subagent_pool", return_value=canned):
            req = self._post({"action": "probe"})
        self.assertEqual(req.status, 409)
        self.assertEqual(req.sent["error"], "pool_silent")
        self.assertIn("metered", req.sent["why"])

    def test_no_task_is_refused_before_anything_runs(self):
        req = self._post({"action": "run"})
        self.assertEqual(req.status, 400)
        self.assertEqual(req.sent["error"], "no_task")
        self.run_guard.assert_not_called()

    def test_a_run_is_recorded_and_a_failure_keeps_its_remedy(self):
        failed = {"ok": False, "error": "engine_masked_error", "hint": "run the CLI with --print-logs",
                  "engine": "opencode/a-free", "engine_error": {"message": "Unexpected server error"},
                  "seconds": 2.0, "tools_used": [], "dir": str(self.tmp), "answer": ""}
        with mock.patch.object(delegate_tools, "subagent_run", return_value=failed) as run:
            req = self._post({"action": "run", "task": "look at the VERSION file"})
        self.assertEqual(req.status, 409)
        self.assertEqual(req.sent["error"], "engine_masked_error")
        self.assertIn("--print-logs", req.sent["hint"], "the refusal must arrive with its remedy")
        self.assertEqual(run.call_args.kwargs["task"], "look at the VERSION file")
        self.assertIs(run.call_args.kwargs["read_only"], True, "a run is read-only unless asked otherwise")
        runs = json.loads((self.ctx.save_dir(self.backend.STATE_DIR) / "runs.json").read_text(encoding="utf-8"))
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["error"], "engine_masked_error")
        self.assertEqual(self.ctx.receipts[-1]["extra"]["action"], "run")

    def test_a_good_smoke_test_is_200_and_green(self):
        good = {"ok": True, "engine": "opencode/a-free", "seconds": 5.0, "tools_used": [],
                "dir": str(self.tmp), "answer": "SIDE_OK", "error": "", "hint": ""}
        with mock.patch.object(delegate_tools, "subagent_run", return_value=good) as run:
            req = self._post({"action": "smoke"})
        self.assertEqual(req.status, 200)
        self.assertEqual(run.call_args.kwargs["task"], self.backend.SMOKE_TASK)
        card = req.sent["card"]
        self.assertEqual([x for x in card["lights"] if x["label"] == "Last task"][0]["state"], "green")

    def test_an_empty_smoke_task_is_the_one_that_carries_its_own(self):
        with mock.patch.object(delegate_tools, "subagent_run",
                               return_value={"ok": True, "engine": "e", "seconds": 1, "tools_used": [],
                                             "dir": str(self.tmp), "answer": "SIDE_OK"}) as run:
            self._post({"action": "smoke"})
        self.assertTrue(run.call_args.kwargs["task"].strip())

    def test_status_and_doctor_answer_from_local_reads(self):
        with mock.patch.object(delegate_tools, "subagent_pool",
                               return_value={"free": ["opencode/x-free"], "default": "opencode/x-free"}):
            status = self._post({"action": "status"})
            doctor = self._post({"action": "doctor"})
        self.assertEqual(status.status, 200)
        self.assertIn("card", status.sent)
        self.assertEqual(doctor.status, 200)
        self.assertIn("cli", doctor.sent["doctor"])
        self.assertTrue(doctor.sent["doctor"]["not_checked"], "a local read must name what it did not check")

    def test_the_record_is_capped(self):
        rec = {"at": "2026-10-15", "ok": True, "task": "x"}
        for _ in range(self.backend.MAX_RUNS + 5):
            self.backend._record(self.ctx, rec)
        runs = json.loads((self.ctx.save_dir(self.backend.STATE_DIR) / "runs.json").read_text(encoding="utf-8"))
        self.assertEqual(len(runs), self.backend.MAX_RUNS)


if __name__ == "__main__":
    unittest.main()
