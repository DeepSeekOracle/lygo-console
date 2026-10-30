"""The film pair's module contract: manifests valid, routes wired, panes published, buttons real.

Δ9Φ963-LYGO-TEST-FILM-MODULES-v1

What this proves, in the order the console proves it at boot: both manifests pass the same validator
the host and the certifier use; the catalog loads them; the host wires their routes and panes; the
buttons card publishes a SMALL strip whose actions are all in its own action table; and the action
table answers a dict with a readable code for every action, including the ones it refuses - because a
console button that answers nothing is worse than no button.

It does NOT need FilmCraft to be installed: a kit without the payload must still pass every one of
these, and the refusals are checked with the payload present or absent.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from modules import validate  # noqa: E402
from modules.host import ModuleHost  # noqa: E402

MODULES_DIR = ROOT / "src" / "modules"
INFO = "lygo.filminfo"
CTRL = "lygo.filmctrl"


class FakeCtx:
    """The smallest ctx the action table touches: a receipt sink and a log. No console needed."""

    def __init__(self) -> None:
        self.receipts: list[dict] = []
        self.logs: list[str] = []

    def receipt(self, spec=None, **kw):
        data = dict(spec or {})
        data.update(kw)
        self.receipts.append(data)
        return {"ok": True}

    def log(self, msg: str) -> None:
        self.logs.append(str(msg))


class FilmManifests(unittest.TestCase):
    def test_both_manifests_valid(self):
        problems = [p for p in validate.validate_tree(MODULES_DIR) if "film" in p]
        self.assertEqual(problems, [], f"the film manifests did not validate: {problems}")

    def test_catalog_lists_both(self):
        cat = validate.load_catalog(MODULES_DIR)
        ids = [e.get("id") for e in cat.get("modules") or []]
        self.assertIn(INFO, ids)
        self.assertIn(CTRL, ids)


class FilmWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = ModuleHost()
        cls.host.load()
        cls.table = cls.host.table()
        cls.rows = {m["id"]: m for m in cls.table.get("modules") or []}

    def test_both_wired_not_refused(self):
        for mid in (INFO, CTRL):
            self.assertIn(mid, self.rows, f"{mid} is not in the module table at all")
            row = self.rows[mid]
            self.assertNotIn(row.get("state"), ("REFUSED", "DISABLED"), f"{mid}: {row.get('error')}")
            self.assertEqual(row.get("error"), "")

    def test_routes_are_really_registered(self):
        routes = getattr(self.host, "routes", {})
        self.assertIn(("GET", "/api/filminfo"), routes)
        self.assertIn(("GET", "/api/film"), routes)
        self.assertIn(("POST", "/api/film"), routes)

    def test_panes_are_dock_panes(self):
        """The strip only renders panes whose id starts with `dock.` - a pane named otherwise would
        be wired and invisible, which is the failure this catches."""
        self.assertEqual(self.rows[INFO].get("panes"), ["dock.film"])
        self.assertEqual(self.rows[CTRL].get("panes"), ["dock.filmctl"])

    def test_filmctrl_declares_the_film_limb_but_registers_none(self):
        """The manifest says which limb this module OWNS; register() must not re-register it, or
        loading a card would change the tool set."""
        manifest = (MODULES_DIR / CTRL / "module.json").read_text(encoding="utf-8")
        self.assertIn('"film"', manifest)
        backend = _backend(CTRL)
        reg = backend.register(None)
        self.assertEqual(reg.get("limbs"), [])
        self.assertEqual(len(reg.get("routes") or []), 2)

    def test_filminfo_is_read_only(self):
        """The facts card owns no state and writes nothing: its manifest says so and its source
        contains no write call."""
        backend = _backend(INFO)
        self.assertEqual(backend.register(None).get("limbs"), [])
        src = (MODULES_DIR / INFO / "backend.py").read_text(encoding="utf-8")
        for forbidden in ("atomic_write", "open(", ".write_text", ".mkdir", "save_state"):
            self.assertNotIn(forbidden, src, f"the read-only card calls {forbidden}")

    def test_health_answers_for_both(self):
        for mid in (INFO, CTRL):
            row = self.rows[mid]
            health = row.get("health") or {}
            self.assertTrue(health.get("ok"), f"{mid} health: {health}")

    def test_the_boot_check_starts_no_engine_process_and_opens_no_socket(self):
        """`health()` runs inside the host's `load()`, i.e. inside the console's boot, so it must not
        spawn the CLI and must not open a socket. Measured before this was fixed: the full card asks
        `filmcraft --version` (and, for the read-out, its 693-command list) and probes two closed
        ports, which cost 2.9 s of boot across the pair for facts nobody had asked for yet. The
        panel's own route still builds the full card, probes and all."""
        for mid in (INFO, CTRL):
            backend = _backend(mid)
            with mock.patch("film_tools._run") as run, \
                    mock.patch("film_tools.port_open") as probe, \
                    mock.patch("film_tools.alive") as alive:
                run.return_value = {"ok": True, "stdout": "", "stderr": "", "code": 0}
                out = backend.health(FakeCtx())
            self.assertTrue(out.get("ok"), f"{mid} health: {out}")
            self.assertEqual(run.call_count, 0,
                             f"{mid} health started an engine process: {run.call_args_list}")
            self.assertEqual(probe.call_count, 0,
                             f"{mid} health opened a socket: {probe.call_args_list}")
            self.assertEqual(alive.call_count, 0,
                             f"{mid} health asked the OS about a pid: {alive.call_args_list}")


class FilmButtons(unittest.TestCase):
    """The strip: small, real, and answerable."""

    @classmethod
    def setUpClass(cls):
        cls.ctrl = _backend(CTRL)

    def test_strip_is_small_and_actions_exist(self):
        panel = self.ctrl.build(FakeCtx())
        acts = panel.get("actions") or []
        self.assertTrue(acts, "the controls card published no buttons")
        self.assertLessEqual(len(acts), 4, "the strip is meant to stay small")
        for a in acts:
            self.assertIn(a.get("action"), self.ctrl.ACTIONS)
            self.assertTrue(str(a.get("label") or "").strip())

    def test_read_only_actions_answer(self):
        """ONLY the read-only actions are CALLED here: a test suite must never boot an editor, serve
        a server or write a clip on the operator's machine. The mutating ones are exercised through
        their mapping below, with the harness stubbed."""
        ctx = FakeCtx()
        for action in ("doctor", "commands", "describe", "presets"):
            code, payload = self.ctrl._act(action, {"action": action, "command": "timeline.razor",
                                                    "query": "razor"}, ctx)
            self.assertIsInstance(code, int)
            self.assertIsInstance(payload, dict)
            self.assertLess(code, 600)

    def test_unknown_action_is_named_not_guessed(self):
        code, payload = self.ctrl._act("not-a-real-action", {}, FakeCtx())
        self.assertEqual(code, 400)
        self.assertEqual(payload.get("error"), "unknown_action")
        self.assertIn("boot", payload.get("actions") or [], "the refusal must list what IS accepted")

    def test_a_refusal_maps_to_409_not_200(self):
        """A harness that refuses must reach the caller as a refusal. The harness is stubbed so no
        real editor is started or stopped by the suite."""
        refusal = {"ok": False, "error": "not_ours", "why": "another copy is running"}
        for action, patched in (("boot", "boot"), ("stop", "stop"), ("web", "web_start")):
            with mock.patch(f"film_tools.{patched}", return_value=dict(refusal)):
                code, payload = self.ctrl._act(action, {"action": action}, FakeCtx())
            self.assertEqual(code, 409, f"{action} did not report the refusal")
            self.assertFalse(payload.get("ok"))
            self.assertTrue(str(payload.get("why") or "").strip())

    def test_a_successful_boot_reports_the_port(self):
        ok = {"ok": True, "pid": 4242, "port": 9642, "why": "the editor is up"}
        with mock.patch("film_tools.boot", return_value=dict(ok)):
            code, payload = self.ctrl._act("boot", {"action": "boot"}, FakeCtx())
        self.assertEqual(code, 200)
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("port"), 9642)

    def test_the_web_button_answers_with_a_url(self):
        """The page opens whatever `url` the answer names - so that field is part of the contract."""
        with mock.patch("film_tools.web_start", return_value={"ok": True, "port": 9643,
                                                             "url": "http://127.0.0.1:9643/"}):
            code, payload = self.ctrl._act("web", {"action": "web"}, FakeCtx())
        self.assertEqual(code, 200)
        self.assertEqual(payload.get("url"), "http://127.0.0.1:9643/")

    def test_the_p0_gate_is_honoured(self):
        """A quarantined write is refused with the gate's own reason, before anything is written."""
        quarantined = {"verdict": "QUARANTINE", "reason": "test gate said no"}
        with mock.patch("p0_hook.gate_prompt", return_value=quarantined):
            with mock.patch("film_tools.export") as export:
                code, payload = self.ctrl._act("export", {"out": "x.mp4"}, FakeCtx())
        self.assertEqual(code, 403)
        self.assertEqual(payload.get("error"), "gate_refused")
        self.assertEqual((payload.get("gate") or {}).get("reason"), "test gate said no")
        export.assert_not_called()


def _backend(mid: str):
    """Import one module's backend by directory, the way the host does."""
    import importlib.util

    path = MODULES_DIR / mid / "backend.py"
    spec = importlib.util.spec_from_file_location(f"film_test_{mid.replace('.', '_')}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


if __name__ == "__main__":
    unittest.main()
