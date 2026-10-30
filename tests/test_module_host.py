"""Tests for the module host and the module manifests (WO-0001, Phase M1).

Δ9Φ963-LYGO-MODULE-CORE-v1

What these tests are for
    They are the acceptance evidence for the kernel seam: the shipped manifests are valid, the host
    wires the declared routes, a colliding or broken module is refused **by name** without taking the
    console down, a raising handler degrades only its own module, and the two legacy routes answer
    exactly what the kernel answered before the seam existed.

What they deliberately do not do
    Write into the operator's save/ tree. The parity checks are read-only (they compare the module's
    answer against the same function the kernel called), and the one write-path test patches the
    notepad function instead of letting a test note land in the live store (defect D30).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from modules import host as host_mod  # noqa: E402
from modules import validate  # noqa: E402

MODULES_DIR = ROOT / "src" / "modules"


class FakeHandler:
    """The little bit of the console's request handler a module route can see."""

    def __init__(self, path: str = "/", body: bytes = b"") -> None:
        self.path = path
        self._raw = body
        self.sent: list[tuple[int, object]] = []

    def _json(self, code: int, payload: object) -> None:
        self.sent.append((code, payload))

    def _read_body(self, limit: int = 300_000) -> bytes:
        del limit
        return self._raw

    @property
    def last(self) -> object:
        return self.sent[-1][1] if self.sent else None

    @property
    def code(self) -> int:
        return self.sent[-1][0] if self.sent else 0


def write_module(tmp: Path, mid: str, manifest: dict, backend: str) -> Path:
    mdir = tmp / mid
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "module.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (mdir / "backend.py").write_text(backend, encoding="utf-8")
    return mdir


def manifest(mid: str, **over: object) -> dict:
    base = {
        "id": mid,
        "title": mid,
        "summary": "A module that exists to be tested.",
        "version": "1.0.0",
        "introduced": "1.2.0",
        "lifecycle": "WIRED",
        "surfaces": {"pc": "FULL", "usb": "FULL", "web": "N/A(need a reason)"},
        "requires": {"console": ">=1.1.1", "python": ["3.11"], "modules": [], "env": []},
        "kernel_services": ["paths"],
        "machinery": ["notepad"],
        "routes": [],
        "limbs": [],
        "panes": [],
        "state": {"owns": [], "writes_via": "atomicio", "gate": "none"},
        "health": "health",
        "tests": ["tests/test_module_host.py"],
        "docs": "04_MODULES/x/MODULE_SPEC.md",
        "adapter": "backend.py",
        "notes": "test fixture",
    }
    base.update(over)
    return base


ECHO_ROUTE = [{"method": "GET", "path": "/api/echo", "handler": "echo", "legacy": False}]
BOOM_ROUTE = [{"method": "GET", "path": "/api/boom", "handler": "boom", "legacy": False}]

ECHO_BACKEND = """
def echo(ctx, req):
    req.json(200, {"ok": True, "who": "echo"})

def health(ctx):
    return {"ok": True, "detail": "echo"}

def register(ctx):
    return {"routes": [("GET", "/api/echo", echo)], "limbs": [], "panes": [], "health": health}
"""

BOOM_BACKEND = """
def boom(ctx, req):
    raise RuntimeError("backend exploded on purpose")

def health(ctx):
    return {"ok": True, "detail": "boom"}

def register(ctx):
    return {"routes": [("GET", "/api/boom", boom)], "limbs": [], "panes": [], "health": health}
"""


def build_tree(tmp: Path, modules: list[tuple[str, dict, str]]) -> Path:
    ids = []
    for mid, man, backend in modules:
        write_module(tmp, mid, man, backend)
        ids.append({"id": mid, "order": 10 + len(ids), "enabled": True})
    (tmp / "catalog.json").write_text(
        json.dumps({"schema": 1, "modules": ids}, indent=2), encoding="utf-8"
    )
    return tmp


class ManifestValidationTests(unittest.TestCase):
    def test_shipped_tree_validates(self) -> None:
        problems = validate.validate_tree(MODULES_DIR)
        self.assertEqual(problems, [], f"shipped module manifests must validate: {problems}")

    def test_catalog_lists_three_enabled_modules(self) -> None:
        """The catalog and the module directories must agree, in the order written down.

        The M1 trio is a floor here, not the whole list: a new module is a catalog entry plus a
        directory — that is the point of the seam — so a hard-coded list would go red on every
        honest addition instead of catching a dishonest one.
        """
        cat = validate.load_catalog(MODULES_DIR)
        self.assertTrue(cat["ok"], cat.get("error"))
        ids = [m["id"] for m in cat["modules"]]
        self.assertEqual(len(ids), len(set(ids)), "a module is listed twice")
        on_disk = sorted(p.name for p in MODULES_DIR.glob("lygo.*") if (p / "module.json").exists())
        self.assertEqual(sorted(ids), on_disk, "catalog and module directories disagree")
        for core in ("lygo.health", "lygo.world", "lygo.notepad"):
            self.assertIn(core, ids)
        self.assertTrue(all(m.get("enabled", True) for m in cat["modules"]))
        orders = [m.get("order", 0) for m in cat["modules"]]
        self.assertEqual(orders, sorted(orders), "load order must be the order written down")

    def test_missing_field_is_reported(self) -> None:
        man = manifest("lygo.x")
        del man["tests"]
        problems = validate.validate_manifest(man, MODULES_DIR / "lygo.world")
        self.assertTrue(any("missing required field: tests" in p for p in problems), problems)

    def test_bad_id_state_surface_and_gate_are_reported(self) -> None:
        man = manifest("Lygo.Bad Name", lifecycle="WORKING", surfaces={"pc": "FULL", "usb": "MAYBE", "web": "N/A(ok)"})
        man["state"] = {"owns": ["/absolute/path"], "writes_via": "tmp", "gate": "sometimes"}
        problems = validate.validate_manifest(man, MODULES_DIR / "lygo.world")
        blob = " | ".join(problems)
        self.assertIn("id must look like", blob)
        self.assertIn("lifecycle 'WORKING' is not one of", blob)
        self.assertIn("surfaces.usb must be", blob)
        self.assertIn("state.owns[0] must be kit-relative", blob)
        self.assertIn("state.writes_via must be atomicio", blob)
        self.assertIn("state.gate must be p0 or none", blob)

    def test_duplicate_route_and_bad_effect_are_reported(self) -> None:
        man = manifest(
            "lygo.x",
            routes=[
                {"method": "GET", "path": "/api/x", "handler": "a"},
                {"method": "GET", "path": "/api/x", "handler": "b"},
            ],
            limbs=[{"name": "t", "effect": "maybe"}, {"name": "t", "effect": "read"}],
        )
        problems = " | ".join(validate.validate_manifest(man, MODULES_DIR / "lygo.world"))
        self.assertIn("repeats GET /api/x", problems)
        self.assertIn("effect must be read or write", problems)
        self.assertIn("repeats limb name", problems)

    def test_missing_adapter_file_is_reported(self) -> None:
        man = manifest("lygo.world", adapter="backend.py")
        problems = validate.validate_manifest(man, MODULES_DIR)
        self.assertTrue(any("adapter file" in p for p in problems), problems)


class ConsoleRequirementTests(unittest.TestCase):
    def test_ranges(self) -> None:
        self.assertTrue(validate.console_satisfies(">=1.1.1", "1.1.1"))
        self.assertTrue(validate.console_satisfies(">=1.0.0", "1.1.1"))
        self.assertTrue(validate.console_satisfies("*", "1.1.1"))
        self.assertTrue(validate.console_satisfies("1.1.1", "1.1.1"))
        self.assertFalse(validate.console_satisfies(">=1.2.0", "1.1.1"))
        self.assertFalse(validate.console_satisfies("==1.0.0", "1.1.1"))
        self.assertFalse(validate.console_satisfies("whenever", "1.1.1"))
        self.assertFalse(validate.console_satisfies("", "1.1.1"))


class HostLoadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host = host_mod.ModuleHost(kit_root=ROOT, modules_dir=MODULES_DIR).load()

    def test_three_modules_wired(self) -> None:
        """Every catalogued module loads and is WIRED. The expected count comes from the catalog,
        so a module that fails to load still fails this test."""
        catalog = json.loads((MODULES_DIR / "catalog.json").read_text(encoding="utf-8"))
        declared = {m["id"] for m in catalog["modules"] if m.get("enabled", True)}
        table = self.host.table()
        self.assertTrue(table["ok"])
        self.assertEqual(table["counts"]["modules"], len(declared))
        self.assertEqual(table["counts"]["refused"], 0)
        by_id = {m["id"]: m for m in table["modules"]}
        self.assertEqual(set(by_id), declared)
        for mid in declared:
            # a module may sit at any rung from WIRED up (PARITY once every edition that should
            # carry it does, verified); refused modules are already caught by counts["refused"]
            ladder = ("PROPOSED", "SPEC'D", "SCAFFOLDED", "WIRED", "TESTED", "PARITY", "RELEASED", "SEALED")
            self.assertIn(by_id[mid]["state"], ladder)
            self.assertGreaterEqual(ladder.index(by_id[mid]["state"]), ladder.index("WIRED"),
                                    f"{mid} is not wired: {by_id[mid]['error']}")

    def test_declared_routes_are_owned(self) -> None:
        self.assertIn(("GET", "/api/world"), self.host.routes)
        self.assertIn(("GET", "/api/notepad"), self.host.routes)
        self.assertIn(("POST", "/api/notepad"), self.host.routes)
        self.assertEqual(self.host.routes[("GET", "/api/world")]["module"], "lygo.world")
        self.assertTrue(self.host.routes[("GET", "/api/notepad")]["legacy"])

    def test_limbs_are_declared_by_the_notepad_module(self) -> None:
        # The shipped set is the notepad's three plus the song limb the studio's card declares
        # (`lygo.musicctrl` manifest `limbs`). The host lists every limb the build declares, so this
        # list grows with the build - what the test is really pinning is that each one is listed with
        # the effect it declared, and that no limb is listed twice.
        self.assertEqual(
            sorted(self.host.limbs),
            ["music_generate", "notepad_list", "notepad_read", "notepad_write"],
        )
        self.assertEqual(self.host.limbs["notepad_write"]["effect"], "write")
        self.assertEqual(self.host.limbs["music_generate"]["effect"], "write")

    def test_health_functions_answer(self) -> None:
        for mid in ("lygo.health", "lygo.world", "lygo.notepad"):
            row = [m for m in self.host.table()["modules"] if m["id"] == mid][0]
            self.assertIsInstance(row["health"], dict)
            self.assertIn("ok", row["health"])

    def test_table_hash_is_stable_and_hex(self) -> None:
        first = self.host.table_hash()
        self.assertEqual(first, self.host.table_hash())
        self.assertEqual(len(first), 64)
        int(first, 16)

    def test_health_digest_reports_no_problem_modules(self) -> None:
        out = self.host.health()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["problem_modules"], [])


class DispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host = host_mod.ModuleHost(kit_root=ROOT, modules_dir=MODULES_DIR).load()

    def test_unknown_path_is_not_claimed(self) -> None:
        self.assertFalse(self.host.handle(FakeHandler(), "GET", "/api/definitely_not_a_route"))
        self.assertFalse(self.host.handle(FakeHandler(), "GET", "/api/health"))

    def test_modules_endpoint_answers_the_table(self) -> None:
        fake = FakeHandler()
        self.assertTrue(self.host.handle(fake, "GET", "/api/modules"))
        self.assertEqual(fake.code, 200)
        catalog = json.loads((MODULES_DIR / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(fake.last["counts"]["modules"], len(catalog["modules"]))

    def test_world_route_matches_the_engine_it_wraps(self) -> None:
        """Same shape and the same instant (within a second — the two calls are not simultaneous)."""
        from datetime import datetime

        from world_clock import pulse

        fake = FakeHandler(path="/api/world")
        self.assertTrue(self.host.handle(fake, "GET", "/api/world"))
        expected = pulse()
        self.assertEqual(fake.code, 200)
        self.assertIsInstance(fake.last, dict)
        self.assertEqual(set(fake.last), set(expected))
        for key in ("signature", "utc_iso", "local_tz", "weekday"):
            if key in expected:
                if key == "utc_iso":
                    got = datetime.fromisoformat(str(fake.last[key]))
                    want = datetime.fromisoformat(str(expected[key]))
                    self.assertLessEqual(abs((got - want).total_seconds()), 2.0)
                else:
                    self.assertEqual(fake.last.get(key), expected.get(key))

    def test_notepad_get_matches_the_engine_it_wraps(self) -> None:
        from notepad import list_notes

        fake = FakeHandler(path="/api/notepad")
        self.assertTrue(self.host.handle(fake, "GET", "/api/notepad"))
        self.assertEqual(fake.last, list_notes())

    def test_notepad_get_with_id_reads_one_note(self) -> None:
        from notepad import list_notes, read_note

        items = list_notes().get("notes") or []
        if not items:
            self.skipTest("no notes in this kit to read (nothing to compare against)")
        nid = str(items[0].get("id"))
        fake = FakeHandler(path=f"/api/notepad?id={nid}")
        self.assertTrue(self.host.handle(fake, "GET", "/api/notepad"))
        self.assertEqual(fake.last, read_note(nid))

    def test_notepad_post_delete_reaches_the_engine(self) -> None:
        """The write path is proven with the engine patched: no test note hits the live store."""
        with patch("notepad.delete_note", return_value={"ok": True, "deleted": "nope"}) as spy:
            fake = FakeHandler(body=json.dumps({"action": "delete", "id": "nope"}).encode())
            self.assertTrue(self.host.handle(fake, "POST", "/api/notepad"))
            self.assertEqual(fake.code, 200)
            self.assertEqual(fake.last, {"ok": True, "deleted": "nope"})
            spy.assert_called_once_with("nope")

    def test_a_save_with_nothing_to_save_is_refused_and_writes_nothing(self) -> None:
        """A junk or empty body must not mint a phantom note.

        2026-09-20 sweep: `POST /api/notepad` with a body that was not JSON - or with no id and no
        text - reached `write_note(None, "", "")` and answered `{"ok": true, "id": ...}` for a note
        nobody asked for. One empty note per junk request, and the caller was told a write had
        happened. The parse still behaves like the kernel (a junk body reads as `{}`, never a 500);
        the refusal lands where there is demonstrably nothing to save.
        """
        for body in (b"{not json at all", b"{}", b'{"action": "save"}'):
            with patch("notepad.write_note", return_value={"ok": True, "id": "x"}) as spy:
                fake = FakeHandler(body=body)
                self.assertTrue(self.host.handle(fake, "POST", "/api/notepad"))
                self.assertEqual(fake.code, 400, body)
                self.assertEqual(fake.last["error"], "nothing_to_save", body)
                spy.assert_not_called()

    def test_clearing_a_note_is_still_a_save(self) -> None:
        """The rule is 'nothing to save', not 'empty text': emptying a note must keep working."""
        with patch("notepad.write_note", return_value={"ok": True, "id": "scratch"}) as spy:
            fake = FakeHandler(body=json.dumps({"id": "scratch", "text": ""}).encode())
            self.assertTrue(self.host.handle(fake, "POST", "/api/notepad"))
            self.assertEqual(fake.code, 200)
            spy.assert_called_once_with("scratch", "", "")

    def test_every_row_reports_the_lifecycle_its_manifest_declares(self) -> None:
        """`state` is the effective status; `lifecycle` is what the manifest itself declared.

        2026-09-20 sweep: the row carried only `state` - the same name a PANE uses for its health
        colour - so a report reading row["lifecycle"] saw None for five healthy modules.
        """
        by_id = {m["id"]: m for m in self.host.table()["modules"]}
        self.assertIn("lygo.envwatch", by_id)
        for mid in sorted(by_id):
            declared = json.loads((ROOT / "src" / "modules" / mid / "module.json").read_text(encoding="utf-8"))
            self.assertEqual(by_id[mid]["lifecycle"], declared["lifecycle"], mid)
            self.assertEqual(by_id[mid]["state"], declared["lifecycle"], mid)


class RefusalAndDegradationTests(unittest.TestCase):
    def test_a_refused_module_still_reports_what_its_manifest_declared(self) -> None:
        """`state` says REFUSED; `lifecycle` still says what the manifest claimed. Two facts."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = build_tree(
                Path(raw),
                [("lygo.one", manifest("lygo.one", routes=ECHO_ROUTE), ECHO_BACKEND),
                 ("lygo.two", manifest("lygo.two", routes=ECHO_ROUTE), ECHO_BACKEND)],
            )
            host = host_mod.ModuleHost(kit_root=ROOT, modules_dir=tmp).load()
            by_id = {m["id"]: m for m in host.table()["modules"]}
            self.assertEqual(by_id["lygo.two"]["state"], "REFUSED")
            self.assertEqual(by_id["lygo.two"]["lifecycle"], "WIRED")

    def test_route_collision_refuses_the_second_module_and_names_both(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = build_tree(
                Path(raw),
                [("lygo.one", manifest("lygo.one", routes=ECHO_ROUTE), ECHO_BACKEND),
                 ("lygo.two", manifest("lygo.two", routes=ECHO_ROUTE), ECHO_BACKEND)],
            )
            host = host_mod.ModuleHost(kit_root=ROOT, modules_dir=tmp).load()
            by_id = {m["id"]: m for m in host.table()["modules"]}
            self.assertIn(by_id["lygo.one"]["state"], ("WIRED", "TESTED"))
            self.assertEqual(by_id["lygo.two"]["state"], "REFUSED")
            self.assertIn("route collision", by_id["lygo.two"]["error"])
            self.assertIn("lygo.one", by_id["lygo.two"]["error"])
            # the console still answers the module that won
            fake = FakeHandler()
            self.assertTrue(host.handle(fake, "GET", "/api/echo"))
            self.assertEqual(fake.last, {"ok": True, "who": "echo"})

    def test_bad_manifest_refuses_only_that_module(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bad = manifest("lygo.bad")
            bad["lifecycle"] = "WHATEVER"
            tmp = build_tree(
                Path(raw),
                [("lygo.good", manifest("lygo.good", routes=ECHO_ROUTE), ECHO_BACKEND), ("lygo.bad", bad, ECHO_BACKEND)],
            )
            host = host_mod.ModuleHost(kit_root=ROOT, modules_dir=tmp).load()
            by_id = {m["id"]: m for m in host.table()["modules"]}
            self.assertEqual(by_id["lygo.bad"]["state"], "REFUSED")
            self.assertIn("invalid manifest", by_id["lygo.bad"]["error"])
            self.assertNotEqual(by_id["lygo.good"]["state"], "REFUSED")
            self.assertTrue(host.handle(FakeHandler(), "GET", "/api/echo"))

    def test_missing_console_requirement_is_named_not_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            man = manifest("lygo.future")
            man["requires"]["console"] = ">=99.0.0"
            tmp = build_tree(Path(raw), [("lygo.future", man, ECHO_BACKEND)])
            host = host_mod.ModuleHost(kit_root=ROOT, modules_dir=tmp).load()
            row = host.table()["modules"][0]
            self.assertEqual(row["state"], "REFUSED")
            self.assertIn("needs console >=99.0.0", row["error"])

    def test_raising_handler_degrades_one_module_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = build_tree(
                Path(raw),
                [("lygo.boom", manifest("lygo.boom", routes=BOOM_ROUTE), BOOM_BACKEND),
                 ("lygo.good", manifest("lygo.good", routes=ECHO_ROUTE), ECHO_BACKEND)],
            )
            host = host_mod.ModuleHost(kit_root=ROOT, modules_dir=tmp).load()
            fake = FakeHandler()
            self.assertTrue(host.handle(fake, "GET", "/api/boom"))
            self.assertEqual(fake.code, 500)
            self.assertEqual(fake.last["error"], "module_error")
            self.assertEqual(fake.last["module"], "lygo.boom")
            self.assertIn("backend exploded on purpose", fake.last["detail"])
            rows = {m["id"]: m for m in host.table()["modules"]}
            self.assertEqual(rows["lygo.boom"]["state"], "DEGRADED")
            self.assertNotIn("lygo.good", host.health()["problem_modules"])
            self.assertIn("lygo.boom", host.health()["problem_modules"])
            # the healthy module keeps answering in the same process
            good = FakeHandler()
            self.assertTrue(host.handle(good, "GET", "/api/echo"))
            self.assertEqual(good.code, 200)

    def test_unknown_module_id_in_catalog_is_refused_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            (tmp / "catalog.json").write_text(
                json.dumps({"schema": 1, "modules": [{"id": "lygo.absent", "order": 1, "enabled": True}]}),
                encoding="utf-8",
            )
            host = host_mod.ModuleHost(kit_root=ROOT, modules_dir=tmp).load()
            row = host.table()["modules"][0]
            self.assertEqual(row["state"], "REFUSED")
            self.assertIn("module.json is missing", row["error"])

    def test_undeclared_route_is_refused(self) -> None:
        """A route the adapter registers but module.json never declared is not allowed to exist."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = build_tree(Path(raw), [("lygo.sneaky", manifest("lygo.sneaky"), ECHO_BACKEND)])
            host = host_mod.ModuleHost(kit_root=ROOT, modules_dir=tmp).load()
            row = host.table()["modules"][0]
            self.assertEqual(row["state"], "REFUSED")
            self.assertIn("undeclared route", row["error"])
            self.assertNotIn(("GET", "/api/echo"), host.routes)
            self.assertFalse(host.handle(FakeHandler(), "GET", "/api/echo"))

    def test_declared_route_that_is_not_registered_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = build_tree(
                Path(raw),
                [("lygo.promise", manifest("lygo.promise", routes=ECHO_ROUTE), "def register(ctx):\n    return {'routes': []}\n")],
            )
            host = host_mod.ModuleHost(kit_root=ROOT, modules_dir=tmp).load()
            row = host.table()["modules"][0]
            self.assertEqual(row["state"], "REFUSED")
            self.assertIn("declared route not registered", row["error"])

    def test_modules_can_be_switched_off_by_env(self) -> None:
        with patch.dict("os.environ", {"LYGO_MODULES": "off"}):
            host = host_mod.ModuleHost(kit_root=ROOT, modules_dir=MODULES_DIR)
            table = host.load(force=True).table()
            self.assertEqual(table["counts"]["routes"], 0)
            self.assertEqual(table["load_error"], "modules_disabled_by_env")
            fake = FakeHandler()
            self.assertTrue(host.handle(fake, "GET", "/api/modules"))
            self.assertEqual(fake.code, 200)


class CtxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host = host_mod.ModuleHost(kit_root=ROOT, modules_dir=MODULES_DIR).load()
        self.ctx = self.host.ctx_for("lygo.notepad")

    def test_edition_is_derived_and_sane(self) -> None:
        from modules.host import detect_edition

        self.assertIn(self.ctx.edition, ("pc", "usb", "web"))
        # Derive the expected answer from THIS kit's own layout, never hard-code one edition:
        # the same test file ships in every kit (the stick carries LYGO_AGENT_STICK.bat).
        expected = "usb" if (ROOT / "LYGO_AGENT_STICK.bat").is_file() else "pc"
        self.assertEqual(detect_edition(ROOT), expected)
        self.assertEqual(detect_edition(ROOT), self.ctx.edition)

    def test_release_and_build_come_from_the_version_module(self) -> None:
        from version import release, stamp

        self.assertEqual(self.ctx.release, release())
        self.assertEqual(self.ctx.build, stamp())

    def test_save_dir_is_under_the_kit_save_tree(self) -> None:
        from paths import SAVE

        self.assertEqual(self.ctx.save_dir("notepad"), SAVE / "notepad")

    def test_p0_gate_answers_a_verdict(self) -> None:
        verdict = self.ctx.p0.check("the module gate is being tested")
        self.assertIn(
            str(verdict.get("verdict")).upper(), ("ALLOW", "AMPLIFY", "QUARANTINE")
        )

    def test_write_json_is_refused_for_a_bad_path_instead_of_raising(self) -> None:
        self.assertFalse(self.ctx.write_json(ROOT / "src" / "modules" / "nope\0bad.json", {"a": 1}))

    def test_bus_delivers_and_survives_a_bad_subscriber(self) -> None:
        seen: list[object] = []

        def bad(_payload: object) -> None:
            raise RuntimeError("subscriber exploded")

        self.host.bus.subscribe("test.topic", bad)
        self.host.bus.subscribe("test.topic", seen.append)
        self.assertEqual(self.host.bus.publish("test.topic", {"n": 1}), 1)
        self.assertEqual(seen, [{"n": 1}])


if __name__ == "__main__":
    unittest.main()
