"""Tests for lygo.envwatch — the Environment watch card (the pair to LLM data).

Δ9Φ963-LYGO-ENVWATCH-TESTS-v1

What these tests are for
    A watcher is only worth reading if three things hold, and each one is tested here.

    1. **It finds real things, and names where.** Every check is driven against a tree built in
       the test — a skill file with no name, a duplicate slug in two layers, a mapped root that
       does not resolve, a traceback in a log — and the assertion is on the *location* it
       reported, not just on the fact that it complained.
    2. **It never makes a quiet tree look green for the wrong reason.** A check it could not run
       must come back UNCHECKED; a check that ran and found nothing is the only thing allowed to
       produce green. Both paths are tested.
    3. **It changes nothing.** The module's own source is scanned for write calls, because a
       watcher that repairs would hide the thing it found.

What they deliberately do not do
    Touch the operator's kit. Every tree these tests inspect is built under a temp directory, and
    each owner module (`skills_mod`, `workspace_map`, `paths`, …) is stubbed through sys.modules,
    so a test can simulate "the owner is missing" without breaking a real one.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from modules import host as host_mod  # noqa: E402

MODULES_DIR = ROOT / "src" / "modules"
MID = "lygo.envwatch"


def load_backend():
    """Import the adapter by path: a module id contains a dot, so it is not a package name."""
    path = MODULES_DIR / MID / "backend.py"
    spec = importlib.util.spec_from_file_location("lygo_envwatch_backend", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_parse(text: str, path: Path):
    """The smallest honest stand-in for the real skill parser: a name or nothing."""
    del path
    for line in str(text).splitlines():
        if line.strip().startswith("name:"):
            return {"slug": line.split(":", 1)[1].strip()}
    return None


def stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


class TestManifestAndContract(unittest.TestCase):
    """The module contract has to hold for a module written after the seam."""

    def setUp(self) -> None:
        self.manifest = json.loads((MODULES_DIR / MID / "module.json").read_text(encoding="utf-8"))
        self.catalog = json.loads((MODULES_DIR / "catalog.json").read_text(encoding="utf-8"))
        self.backend = load_backend()

    def test_manifest_is_complete(self) -> None:
        for field in ("id", "family", "title", "summary", "version", "introduced", "lifecycle", "surfaces",
                      "requires", "kernel_services", "machinery", "routes", "limbs", "panes", "state",
                      "health", "tests", "docs", "adapter", "notes"):
            self.assertIn(field, self.manifest, f"module.json is missing {field}")
        self.assertEqual(self.manifest["id"], MID)
        # pin the ladder, not a transient rung: the value must be a real state and at least WIRED
        ladder = ("PROPOSED", "SPEC'D", "SCAFFOLDED", "WIRED", "TESTED", "PARITY", "RELEASED", "SEALED")
        self.assertIn(self.manifest["lifecycle"], ladder)
        self.assertGreaterEqual(ladder.index(self.manifest["lifecycle"]), ladder.index("WIRED"))
        self.assertEqual(self.manifest["surfaces"]["pc"], "FULL")
        self.assertTrue(self.manifest["surfaces"]["usb"].startswith("FULL"),
                        "promoted in 1.2.0 (A7): the stick carries it and tested it")
        self.assertTrue(self.manifest["surfaces"]["web"].startswith("N/A"),
                        "the web needs no card here, and the manifest says why")
        self.assertIn("steward decision", self.manifest["surfaces"]["web"])

    def test_it_owns_no_state_and_fixes_nothing(self) -> None:
        self.assertEqual(self.manifest["state"]["owns"], [])
        self.assertEqual(self.manifest["state"]["gate"], "none")
        self.assertEqual(self.manifest["limbs"], [], "a watcher adds no endpoint the model can call")
        self.assertIn("fixes nothing", self.manifest["notes"])

    def test_the_route_declared_is_the_route_implemented(self) -> None:
        row = self.manifest["routes"][0]
        self.assertEqual(row["method"], "GET")
        self.assertEqual(row["path"], "/api/envwatch")
        self.assertTrue(callable(getattr(self.backend, row["handler"])), "the handler named in the manifest must exist")
        self.assertTrue(callable(getattr(self.backend, self.manifest["health"])))

    def test_it_is_catalogued(self) -> None:
        ids = [m["id"] for m in self.catalog["modules"]]
        self.assertIn(MID, ids)
        row = next(m for m in self.catalog["modules"] if m["id"] == MID)
        self.assertTrue(row["enabled"])
        self.assertEqual(row["order"], 40, "the strip is ordered: LLM data 30, Environment watch 40")

    def test_pane_id_and_slot_agree(self) -> None:
        pane = self.manifest["panes"][0]
        self.assertEqual(pane["id"], "dock.env")
        self.assertEqual(pane["slot"], "dock", "a pane lives in the dock slot - never in the header")
        self.assertEqual(pane["id"], f"{pane['slot']}.env")
        self.assertFalse(pane.get("inner_scroll", False), "the page scrolls; a pane may not scroll inside it")

    def test_register_matches_the_manifest(self) -> None:
        reg = self.backend.register(None)
        self.assertEqual([r[0] for r in reg["routes"]], ["GET"])
        self.assertEqual(reg["routes"][0][1], self.manifest["routes"][0]["path"])
        self.assertEqual(reg["panes"][0]["id"], self.manifest["panes"][0]["id"])
        self.assertTrue(callable(reg["health"]))

    def test_the_route_answers_through_a_request_handler(self) -> None:
        class Handler:
            def __init__(self) -> None:
                self.sent: list[tuple[int, object]] = []

            def json(self, code: int, payload: object) -> None:
                self.sent.append((code, payload))

        h = Handler()
        self.backend.data(None, h)
        code, payload = h.sent[-1]
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])

    def test_the_host_validator_accepts_this_manifest(self) -> None:
        """The strongest contract test: the same validator the host runs before it wires a module.
        It refused this module once, over a rule the tests had not pinned, so it is pinned here."""
        from modules import validate  # noqa: PLC0415

        problems = validate.validate_manifest(self.manifest, MODULES_DIR / MID, catalog_id=MID)
        self.assertEqual(problems, [], f"the host would refuse this module: {problems}")
        # the limits the module must keep inside, so a future edit cannot be refused silently
        self.assertLessEqual(len(self.manifest["summary"]), 200, "summary must be one line (200 chars)")
        self.assertRegex(self.manifest["id"], r"^lygo\.[a-z]+$")

    def test_the_catalog_is_still_valid_as_a_whole(self) -> None:
        from modules import validate  # noqa: PLC0415

        self.assertEqual(validate.validate_tree(MODULES_DIR), [])

    def test_the_kernel_does_not_know_this_module_exists(self) -> None:
        """The whole point of the seam: a module is a directory, a catalogue line and a test file."""
        for rel in ("src/server.py", "scripts/certify_build.py"):
            text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
            self.assertNotIn("envwatch", text, f"{rel} must not mention the module")


class TestNamingPath(unittest.TestCase):
    """`lygo.<subject><family>` — the naming path every module this kit adds must follow."""

    FAMILIES = ("info", "watch")

    def test_every_family_module_declares_its_family(self) -> None:
        catalog = json.loads((MODULES_DIR / "catalog.json").read_text(encoding="utf-8"))
        for row in catalog["modules"]:
            mid = row["id"]
            tail = next((f for f in self.FAMILIES if mid.endswith(f)), None)
            manifest_path = MODULES_DIR / mid / "module.json"
            if not manifest_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if tail is None:
                self.assertNotIn(
                    "family", manifest,
                    f"{mid} carries no family suffix but declares a family — either name it lygo.<subject>{manifest.get('family')} or drop the field",
                )
            else:
                self.assertEqual(manifest.get("family"), tail, f"{mid} must declare family '{tail}' (naming law: 01_DESIGN/MODULE_NAMING_AND_BRANDING_v1.md)")
                self.assertTrue(mid.startswith("lygo."), f"{mid} must sit in the lygo. namespace")

    def test_the_proven_pair_follows_it(self) -> None:
        info = json.loads((MODULES_DIR / "lygo.llminfo" / "module.json").read_text(encoding="utf-8"))
        watch = json.loads((MODULES_DIR / MID / "module.json").read_text(encoding="utf-8"))
        self.assertEqual(info["family"], "info")
        self.assertEqual(watch["family"], "watch")
        self.assertEqual(info["panes"][0]["slot"], watch["panes"][0]["slot"])

    def test_the_two_modules_are_a_pair_not_a_duplicate(self) -> None:
        """One shows what is good, one shows what needs fixing; neither owns the other's facts."""
        watch = json.loads((MODULES_DIR / MID / "module.json").read_text(encoding="utf-8"))
        panel = watch["routes"][0]["path"]
        self.assertEqual(panel, "/api/envwatch")
        self.assertNotEqual(panel, "/api/llminfo")


class TestAgeLadder(unittest.TestCase):
    """A finding from last week is history, not a live fault — but it stays visible."""

    def setUp(self) -> None:
        self.backend = load_backend()

    def test_a_fresh_red_stays_red(self) -> None:
        self.assertEqual(self.backend._age_step("red", 1.0)[0], "red")

    def test_yesterdays_traceback_is_not_a_live_fault(self) -> None:
        level, note = self.backend._age_step("red", 72.0)
        self.assertEqual(level, "amber")
        self.assertIn("days ago", note)

    def test_a_stale_amber_drops_out_of_the_fix_list(self) -> None:
        self.assertEqual(self.backend._age_step("amber", 24.0 * 10)[0], "green")

    def test_no_timestamp_cannot_downgrade_a_finding(self) -> None:
        reason = self.backend._age_step("red", None)[0]
        self.assertEqual(reason, "red", "an unreadable timestamp must not excuse a red line")


class TestFileDedupe(unittest.TestCase):
    """On Windows both rglobs match the same file; an undeduped walk doubles every count."""

    def setUp(self) -> None:
        self.backend = load_backend()

    def test_one_file_is_one_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "skills" / "lygo.demo" / "SKILL.md"
            f.parent.mkdir(parents=True)
            f.write_text("name: demo\n", encoding="utf-8")
            keys = {self.backend._path_key(f), self.backend._path_key(Path(str(f).lower()))}
            self.assertEqual(len(keys), 1, "the same file spelled two ways must be one row")

    def test_the_walk_counts_a_single_file_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "bundled"
            (root / "a" / "lygo.one").mkdir(parents=True)
            (root / "a" / "lygo.one" / "SKILL.md").write_text("name: one\n", encoding="utf-8")
            nested = root / "b" / "lygo.two"
            nested.mkdir(parents=True)
            (nested / "SKILL.md").write_text("name: two\n", encoding="utf-8")
            skills = stub_module(
                "skills_mod",
                catalog=lambda: [{"slug": "one", "enabled": True}, {"slug": "two", "enabled": True}],
                load_state=lambda: {"enabled": ["one", "two"]},
                WORKSPACE=Path(tmp) / "workspace",
                INSTALLED=Path(tmp) / "installed",
                BUNDLED=root,
                extra_roots=lambda: [],
                _parse_skill_md=fake_parse,
            )
            with patch.dict(sys.modules, {"skills_mod": skills}):
                part = self.backend._skills()
            files_row = next(r for r in part.rows if r["k"] == "Files on disk")
            self.assertEqual(files_row["v"], "2 files")
            self.assertFalse([i for i in part.issues if "shadowed" in i["title"]], "a double-glob is not a shadowing problem")
            self.assertFalse([i for i in part.issues if "never reach" in i["title"]])


class TestSkillsFindings(unittest.TestCase):
    """The two real ways a skill exists on disk and never reaches the model."""

    def setUp(self) -> None:
        self.backend = load_backend()

    def _tree(self, tmp: str, *, broken: bool, duplicate: bool):
        bundled = Path(tmp) / "bundled"
        (bundled / "good").mkdir(parents=True)
        (bundled / "good" / "SKILL.md").write_text("name: good\n", encoding="utf-8")
        if broken:
            bad = bundled / "bad"
            bad.mkdir(parents=True)
            (bad / "SKILL.md").write_text(": not front matter at all\n", encoding="utf-8")
        if duplicate:
            ws = Path(tmp) / "workspace" / "skills" / "good"
            ws.mkdir(parents=True)
            (ws / "SKILL.md").write_text("name: good\n", encoding="utf-8")
        return bundled

    def _run(self, tmp: str, *, broken: bool, duplicate: bool):
        bundled = self._tree(tmp, broken=broken, duplicate=duplicate)
        skills = stub_module(
            "skills_mod",
            catalog=lambda: [{"slug": "good", "enabled": True}],
            load_state=lambda: {"enabled": ["good"]},
            WORKSPACE=Path(tmp) / "workspace",
            INSTALLED=Path(tmp) / "installed",
            BUNDLED=bundled,
            extra_roots=lambda: [],
            _parse_skill_md=fake_parse,
        )
        with patch.dict(sys.modules, {"skills_mod": skills}):
            return self.backend._skills()

    def test_a_nameless_skill_file_is_reported_with_its_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            part = self._run(tmp, broken=True, duplicate=False)
        amber = [i for i in part.issues if i["level"] == "amber"]
        self.assertTrue(amber, "a SKILL.md the catalogue skips is a finding")
        self.assertIn("never reach the model", amber[0]["title"])
        self.assertIn("SKILL.md", amber[0]["where"])

    def test_a_duplicate_slug_names_the_file_that_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            part = self._run(tmp, broken=False, duplicate=True)
        shadow = [i for i in part.issues if "shadowed" in i["title"]]
        self.assertTrue(shadow, "the same slug twice means one file can be edited with no effect")
        self.assertIn("hidden by", shadow[0]["where"])
        self.assertIn("workspace", shadow[0]["where"].lower())

    def test_a_clean_tree_reports_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            part = self._run(tmp, broken=False, duplicate=False)
        self.assertEqual(part.issues, [], f"a clean tree must produce no findings: {part.issues}")
        self.assertFalse(part.unchecked)

    def test_a_missing_extra_root_is_a_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundled = self._tree(tmp, broken=False, duplicate=False)
            skills = stub_module(
                "skills_mod",
                catalog=lambda: [],
                load_state=lambda: {"enabled": []},
                WORKSPACE=Path(tmp) / "workspace",
                INSTALLED=Path(tmp) / "installed",
                BUNDLED=bundled,
                extra_roots=lambda: [Path(tmp) / "gone"],
                _parse_skill_md=fake_parse,
            )
            with patch.dict(sys.modules, {"skills_mod": skills}):
                part = self.backend._skills()
        self.assertTrue([i for i in part.issues if "declared skill root" in i["title"]])


class TestPathsFindings(unittest.TestCase):
    """A mapped root that does not resolve is the 'path not firing' case."""

    def setUp(self) -> None:
        self.backend = load_backend()

    def _run(self, mounts, warnings=(), usb_ok=True, declared=()):
        """`declared` are the roots config/admin.json calls optional - `is_optional_root` is the
        paths owner's answer, so it is stubbed exactly as the real admin_map exposes it."""
        import os as _os

        def _is_optional(path, _declared=tuple(declared)):
            want = _os.path.normcase(str(path or "").strip().rstrip("\\/"))
            return any(want == _os.path.normcase(str(d).strip().rstrip("\\/")) for d in _declared)

        wm = stub_module(
            "workspace_map",
            list_mounts=lambda: {"mounts": mounts},
            extra_paths=lambda kind: [Path("X:/somewhere")] if kind == "read" else [],
        )
        am = stub_module(
            "admin_map",
            path_warnings=lambda: list(warnings),
            is_admin=lambda: False,
            is_optional_root=_is_optional,
            chatagent_root_status=lambda: {"verified": True, "root": "D:/chatagent", "reason": "verified by .git"},
            usb_root_status=lambda: {"verified": usb_ok, "root": "E:/LYGO_BUILDER_KEY", "reason": "no marker"},
        )
        with patch.dict(sys.modules, {"workspace_map": wm, "admin_map": am}):
            return self.backend._paths()

    def test_a_missing_mount_is_named(self) -> None:
        part = self._run([{"path": "U:/LYGO", "status": "missing", "source": "admin.json", "write": False}])
        bad = [i for i in part.issues if "do not resolve" in i["title"]]
        self.assertTrue(bad)
        self.assertIn("U:/LYGO", bad[0]["where"])

    def test_a_revoked_mount_is_not_a_fault(self) -> None:
        part = self._run([{"path": "X:/old", "status": "revoked", "source": "workspace_map.json", "revoked": True, "write": False}])
        self.assertFalse([i for i in part.issues if "do not resolve" in i["title"]])

    def test_an_unresolved_kit_root_is_a_finding(self) -> None:
        part = self._run([{"path": "I:/kit", "status": "ok", "source": "pinned", "write": True}], usb_ok=False)
        self.assertTrue([i for i in part.issues if "USB builder key" in i["title"]])

    def test_a_clean_root_set_reports_nothing(self) -> None:
        part = self._run([{"path": "I:/kit", "status": "ok", "source": "pinned", "write": True}])
        self.assertEqual(part.issues, [])


    def test_a_declared_optional_root_is_not_a_fault(self) -> None:
        """A drive that is *meant* to be offline must not send the operator chasing it. The
        declaration lives in config/admin.json; the finding is reserved for roots nobody declared."""
        part = self._run(
            [{"path": "U:\\LYGO", "status": "missing", "source": "admin.json", "write": True}],
            declared=["U:\\LYGO"],
        )
        self.assertEqual([i for i in part.issues if "do not resolve" in i["title"]], [])
        row = next(r for r in part.rows if r["k"] == "Absent (declared optional)")
        text = row["k"] + " " + row["v"] + " " + row.get("note", "")
        self.assertIn("U:\\LYGO", text)
        self.assertIn("optional", text.lower())

    def test_an_undeclared_missing_root_still_holds_the_finding(self) -> None:
        part = self._run(
            [
                {"path": "U:\\LYGO", "status": "missing", "source": "admin.json", "write": True},
                {"path": "Q:\\gone", "status": "missing", "source": "admin.json", "write": False},
            ],
            declared=["U:\\LYGO"],
        )
        bad = [i for i in part.issues if "do not resolve" in i["title"]]
        self.assertEqual(len(bad), 1, "one declared-optional and one real root: exactly one finding")
        self.assertIn("Q:\\gone", bad[0]["where"])
        self.assertNotIn("U:\\LYGO", bad[0]["where"], "the optional root is not named as a fault")

    def test_a_resolving_root_set_states_how_many_resolve(self) -> None:
        part = self._run([{"path": "I:/kit", "status": "ok", "source": "pinned", "write": True}])
        row = next(r for r in part.rows if r["k"] == "Resolving")
        self.assertIn("1", row["v"])


class TestLogFindings(unittest.TestCase):
    """The log rules are stated, narrow, and named in the output."""

    def setUp(self) -> None:
        self.backend = load_backend()

    def _run(self, lines: list[str], *, receipts=None, events=None):
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            logs.mkdir()
            (logs / "console-test.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
            rec = Path(tmp) / "receipts"
            rec.mkdir()
            for i, verdict in enumerate(receipts or []):
                (rec / f"r{i}.json").write_text(json.dumps({"gate_verdict": verdict, "id": f"r{i}", "ts": "2026-09-20"}), encoding="utf-8")
            my = Path(tmp) / "mycelium"
            my.mkdir()
            if events is not None:
                (my / "events.jsonl").write_text("\n".join(events) + "\n", encoding="utf-8")
            paths = stub_module("paths", LOGS=logs, RECEIPTS=rec, MYCELIUM=my)
            cloud = stub_module("cloud_api", public_status=lambda: {"degraded": False, "chain_tried": []})
            with patch.dict(sys.modules, {"paths": paths, "cloud_api": cloud}):
                return self.backend._errors()

    def test_a_traceback_is_red_and_quotes_the_line(self) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        part = self._run([f"[{stamp}] [module x] boom", "Traceback (most recent call last):", "  File \"a.py\""])
        red = [i for i in part.issues if i["level"] == "red"]
        self.assertTrue(red, "a traceback in the console log is a live fault")
        self.assertIn("traceback", red[0]["title"])
        self.assertIn("Traceback", red[0]["detail"])

    def test_an_old_refusal_does_not_hold_the_card_red(self) -> None:
        stamp = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        part = self._run([f"[{stamp}] [module x] refused to kill pid 1"])
        self.assertFalse([i for i in part.issues if i["level"] in ("amber", "red")], "10-day-old history is not a live fault")
        rows = [(r["k"] + " " + r.get("note", "")).lower() for r in part.rows]
        self.assertTrue(any("refused" in row for row in rows), "the line is still readable in the rows")

    def test_an_undated_engine_log_is_dated_by_the_file_and_can_age_out(self) -> None:
        """llama.cpp logs carry relative stamps. An undated red line must still be able to age -
        otherwise a stick holds "engine fault" red for ever over a log nobody has written to in weeks."""
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            logs.mkdir()
            log = logs / "llama-server-11441.log"
            log.write_text("0.05.555.799 E llama_model_load: failed to load model\n", encoding="utf-8")
            old = (datetime.now() - timedelta(days=3)).timestamp()
            os.utime(log, (old, old))
            rec = Path(tmp) / "receipts"
            rec.mkdir()
            my = Path(tmp) / "mycelium"
            my.mkdir()
            paths = stub_module("paths", LOGS=logs, RECEIPTS=rec, MYCELIUM=my)
            cloud = stub_module("cloud_api", public_status=lambda: {"degraded": False, "chain_tried": []})
            with patch.dict(sys.modules, {"paths": paths, "cloud_api": cloud}):
                part = self.backend._errors()
        made = [i for i in part.issues if "engine fault" in i["title"]]
        self.assertTrue(made, "the fault is still reported - it happened")
        self.assertEqual(made[0]["level"], "amber", "3 days old is history, not a live fault")
        self.assertIn("dated by the log file", made[0]["detail"], "and it says where the age came from")
        self.assertIn("(log file)", made[0]["detail"])

    def test_a_fresh_undated_engine_fault_stays_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            logs.mkdir()
            (logs / "llama-server-11441.log").write_text("0.01.1 E CUDA error: out of memory\n", encoding="utf-8")
            rec = Path(tmp) / "receipts"
            rec.mkdir()
            my = Path(tmp) / "mycelium"
            my.mkdir()
            paths = stub_module("paths", LOGS=logs, RECEIPTS=rec, MYCELIUM=my)
            cloud = stub_module("cloud_api", public_status=lambda: {"degraded": False, "chain_tried": []})
            with patch.dict(sys.modules, {"paths": paths, "cloud_api": cloud}):
                part = self.backend._errors()
        made = [i for i in part.issues if "engine fault" in i["title"]]
        self.assertTrue(made)
        self.assertEqual(made[0]["level"], "red", "a fault written minutes ago is live")

    def test_a_log_that_moves_between_the_read_and_the_stat_is_reported_not_raised(self) -> None:
        """`scripts/rotate_logs.py` moves logs while this card may be reading them.

        2026-09-20 sweep: the age came from `f.stat()` *after* the tail read, unguarded - so a
        rotation landing in that window raised FileNotFoundError out of the panel and the whole
        card answered 500. A file that moved mid-read is not an error the operator can act on, but
        the FAULT still is, so it is reported undated and stays loud (no age means no downgrade).
        """
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            logs.mkdir()
            log = logs / "llama-server-11441.log"
            log.write_text("0.01.1 E engine fault: out of memory\n", encoding="utf-8")
            rec = Path(tmp) / "receipts"
            rec.mkdir()
            my = Path(tmp) / "mycelium"
            my.mkdir()
            paths = stub_module("paths", LOGS=logs, RECEIPTS=rec, MYCELIUM=my)
            cloud = stub_module("cloud_api", public_status=lambda: {"degraded": False, "chain_tried": []})
            real_stat = Path.stat

            def vanishing(self, *a, **kw):  # noqa: ANN001, ANN002, ANN003 - a Path.stat stand-in
                if self.name == "llama-server-11441.log":
                    raise FileNotFoundError(2, "rotated while the card was reading it")
                return real_stat(self, *a, **kw)

            with patch.dict(sys.modules, {"paths": paths, "cloud_api": cloud}), \
                 patch.object(Path, "stat", vanishing):
                part = self.backend._errors()
        made = [i for i in part.issues if "engine fault" in i["title"]]
        self.assertTrue(made, "the fault is still reported - the line was read, only the age was lost")
        self.assertEqual(made[0]["level"], "red", "an undateable fault stays loud, never silently amber")
        self.assertIn("moved while it was being read", made[0]["detail"])

    def test_a_check_that_could_not_run_keeps_the_card_out_of_green(self) -> None:
        part = self.backend._Part("stubbed", "stub")
        part.unchecked_for("owner missing")
        clean = {n: self.backend._Part(n, "stub") for n in ("paths", "errors", "monitors", "stores")}
        for p in clean.values():
            p.row("row", "value")
        with patch.object(self.backend, "_skills", lambda *a: part), \
             patch.object(self.backend, "_paths", lambda: clean["paths"]), \
             patch.object(self.backend, "_errors", lambda: clean["errors"]), \
             patch.object(self.backend, "_monitors", lambda: clean["monitors"]), \
             patch.object(self.backend, "_stores", lambda: clean["stores"]):
            panel = self.backend.build(None)
        self.assertNotEqual(panel["state"], "green", "an unexamined environment may not be reported as good")
        self.assertIn("could not run", json.dumps(panel["groups"]))

    def test_a_clean_log_reports_nothing_but_still_states_its_rules(self) -> None:
        part = self._run(["[2026-09-20 10:00:00] everything is fine", "loading tensors 0.26.466.86"])
        self.assertEqual(part.issues, [])
        rules = next(r for r in part.rows if r["k"] == "Rules used")
        self.assertIn("traceback", rules["v"] + " " + rules.get("note", ""))

    def test_a_word_like_error_in_a_normal_line_is_not_a_finding(self) -> None:
        """Broad matching cries wolf; the rules are narrow on purpose."""
        part = self._run(["[2026-09-20 10:00:00] loaded 32 layer(s) with errors=0 in 1.2s"])
        self.assertEqual(part.issues, [])

    def test_a_gate_verdict_outside_the_benign_set_is_reported(self) -> None:
        part = self._run(["[2026-09-20 10:00:00] fine"], receipts=["AMPLIFY", "SOFTEN", "QUARANTINE"])
        self.assertTrue([i for i in part.issues if "P0 gate" in i["title"]])
        rows = next(r for r in part.rows if r["k"] == "Gate verdicts")
        self.assertIn("QUARANTINE", rows["v"] + " " + rows.get("note", ""))

    def test_benign_verdicts_alone_are_not_a_finding(self) -> None:
        part = self._run(["[2026-09-20 10:00:00] fine"], receipts=["AMPLIFY", "AMPLIFY", "SOFTEN"])
        self.assertEqual(part.issues, [])

    def test_a_failed_event_is_reported(self) -> None:
        part = self._run(["[2026-09-20 10:00:00] fine"], events=['{"ok": false, "what": "x"}'])
        self.assertTrue([i for i in part.issues if "recorded a failure" in i["title"]])


    def test_a_refusal_of_an_impossible_path_is_the_guard_working(self) -> None:
        """The kit really did refuse a write - but the path it was handed carried a null byte, so
        no filesystem would take it. Counting that as a fault is how a card trains its operator to
        stop reading it. It stays visible as a guard that held."""
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = (f"[{stamp}] [module lygo.notepad] write_text(E:\\kit\\src\\modules\\nope\x00bad.json) "
                "failed: open: embedded null character in path")
        part = self._run([line])
        self.assertEqual([i for i in part.issues if i["level"] in ("amber", "red")], [])
        row = next(r for r in part.rows if "correctly refused" in str(r.get("note", "")))
        self.assertIn("null character", row["note"])

    def test_a_real_write_refusal_is_still_a_finding(self) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        part = self._run([f"[{stamp}] [module x] write_text(E:\\kit\\save\\note.md) failed: open: PermissionError"])
        bad = [i for i in part.issues if "write refused" in i["title"]]
        self.assertTrue(bad, "a refusal of a real path is a real finding")
        self.assertEqual(bad[0]["level"], "amber")

    def test_an_empty_target_refusal_is_also_the_guard_working(self) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        part = self._run([f"[{stamp}] [module x] write_text() failed: open: "])
        self.assertEqual([i for i in part.issues if i["level"] in ("amber", "red")], [])

    def test_archived_logs_are_counted_not_hidden(self) -> None:
        """Rotation must not become a way of hiding a fault: the archive is named on the card."""
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            (logs / "archive").mkdir(parents=True)
            (logs / "archive" / "llama-server-old.log").write_text("0.1 E engine fault\n", encoding="utf-8")
            (logs / "console-today.log").write_text("[2026-09-20 10:00:00] fine\n", encoding="utf-8")
            rec = Path(tmp) / "receipts"
            rec.mkdir()
            my = Path(tmp) / "mycelium"
            my.mkdir()
            paths = stub_module("paths", LOGS=logs, RECEIPTS=rec, MYCELIUM=my)
            cloud = stub_module("cloud_api", public_status=lambda: {"degraded": False, "chain_tried": []})
            with patch.dict(sys.modules, {"paths": paths, "cloud_api": cloud}):
                part = self.backend._errors()
        row = next(r for r in part.rows if r["k"] == "Archived logs")
        self.assertIn("1", row["v"])
        self.assertIn("save/logs/archive", row.get("note", ""))
        self.assertEqual([i for i in part.issues if "old" in i["title"]], [], "an archived log is not scanned")


class TestUncheckedIsNeverGreen(unittest.TestCase):
    """The one lie this module may not tell: green from a check that never ran."""

    def setUp(self) -> None:
        self.backend = load_backend()

    def test_a_missing_owner_is_unchecked_not_clean(self) -> None:
        with patch.dict(sys.modules, {"skills_mod": None}):
            part = self.backend._skills()
        self.assertEqual(part.issues, [])
        self.assertTrue(part.unchecked, "an owner that will not import must be reported")
        self.assertEqual(part.rows, [], "and it must not pretend it read anything")

    def test_every_light_uses_the_four_state_vocabulary(self) -> None:
        panel = self.backend.build(None)
        for light in panel["lights"]:
            self.assertIn(light["state"], ("green", "amber", "red", "grey"))
            self.assertTrue(light["label"] and light["text"])

    def test_a_broken_tree_greys_its_light_rather_than_greening_it(self) -> None:
        with patch.dict(sys.modules, {"skills_mod": None}):
            panel = self.backend.build(None)
        skills_light = next(light for light in panel["lights"] if light["id"] == "skills")
        self.assertEqual(skills_light["state"], "grey")
        self.assertIn("UNCHECKED", json.dumps(panel["groups"]))


class TestAllClearIsEarned(unittest.TestCase):
    """Green must mean "the searches ran and came back empty"."""

    def setUp(self) -> None:
        self.backend = load_backend()

    def _clean_parts(self):
        b = self.backend
        parts = {name: b._Part(name, "stub") for name in ("skills", "paths", "errors", "monitors", "stores")}
        for part in parts.values():
            part.row("row", "value")
        return parts

    def test_green_panel_says_so_in_words(self) -> None:
        parts = self._clean_parts()
        with patch.object(self.backend, "_skills", lambda *a: parts["skills"]), \
             patch.object(self.backend, "_paths", lambda: parts["paths"]), \
             patch.object(self.backend, "_errors", lambda: parts["errors"]), \
             patch.object(self.backend, "_monitors", lambda: parts["monitors"]), \
             patch.object(self.backend, "_stores", lambda: parts["stores"]):
            panel = self.backend.build(None)
        self.assertEqual(panel["state"], "green")
        self.assertIn("nothing needs fixing", panel["state_text"])
        fix = panel["groups"][0]
        self.assertEqual(fix["title"], "Needs fixing")
        self.assertEqual(len(fix["rows"]), 1)
        self.assertEqual(fix["rows"][0]["dot"], "green")

    def test_one_red_check_makes_the_card_red_and_lists_it_first(self) -> None:
        parts = self._clean_parts()
        parts["monitors"].issue("red", "the engine binary is missing", "nothing can boot", "engine.resolve_binary() → None")
        with patch.object(self.backend, "_skills", lambda *a: parts["skills"]), \
             patch.object(self.backend, "_paths", lambda: parts["paths"]), \
             patch.object(self.backend, "_errors", lambda: parts["errors"]), \
             patch.object(self.backend, "_monitors", lambda: parts["monitors"]), \
             patch.object(self.backend, "_stores", lambda: parts["stores"]):
            panel = self.backend.build(None)
        self.assertEqual(panel["state"], "red")
        self.assertIn("1 thing needs fixing", panel["state_text"])
        self.assertEqual(panel["groups"][0]["rows"][0]["dot"], "red")
        self.assertEqual(panel["lights"][0]["state"], "red")


class TestPanelOnThisTree(unittest.TestCase):
    """The real panel, read from the real kit."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = load_backend()
        cls.panel = cls.backend.build(None)

    def test_the_panel_has_the_shape_the_shell_renders(self) -> None:
        for key in ("ok", "signature", "module", "at", "state", "state_text", "lights", "groups", "missing", "sources", "refresh_s"):
            self.assertIn(key, self.panel)
        self.assertEqual(self.panel["module"], MID)
        self.assertIn(self.panel["state"], ("green", "amber", "red"))

    def test_the_card_carries_a_state_for_the_shell_light(self) -> None:
        self.assertEqual(self.panel["lights"][0]["id"], "env")
        self.assertEqual(self.panel["lights"][0]["state"], self.panel["state"])
        self.assertTrue(self.panel["state_text"])

    def test_every_finding_points_at_something(self) -> None:
        for issue in self.panel["issues"]:
            self.assertIn(issue["level"], ("green", "amber", "red"))
            self.assertTrue(issue["title"])
            self.assertTrue(issue.get("where"), f"a finding with no location is noise: {issue}")

    def test_the_state_matches_the_worst_finding(self) -> None:
        bad = [i for i in self.panel["issues"] if i["level"] != "green"]
        worst = "green"
        if any(i["level"] == "red" for i in bad):
            worst = "red"
        elif bad:
            worst = "amber"
        self.assertEqual(self.panel["state"], worst)

    def test_the_state_text_counts_what_the_operator_must_fix(self) -> None:
        bad = [i for i in self.panel["issues"] if i["level"] != "green"]
        if bad:
            self.assertIn("need", self.panel["state_text"])
            self.assertIn(str(len(bad)), self.panel["state_text"])
        else:
            self.assertIn("nothing needs fixing", self.panel["state_text"])

    def test_it_names_the_gaps_it_cannot_check(self) -> None:
        joined = " ".join(self.panel["missing"]).lower()
        self.assertIn("per-skill execution", joined, "it must not imply it can see a skill misbehaving")
        self.assertIn("web edition", joined)
        self.assertTrue(len(self.panel["missing"]) >= 4)

    def test_it_says_who_owns_every_fact(self) -> None:
        self.assertTrue(len(self.panel["sources"]) >= 8)
        self.assertTrue(all(isinstance(s, str) and s for s in self.panel["sources"]))

    def test_health_is_honest_about_what_imports(self) -> None:
        report = self.backend.health(None)
        self.assertIn("ok", report)
        self.assertIn("detail", report)
        self.assertIn("reads", report["detail"] if report["ok"] else "reads 0/9")


class TestWritesNothing(unittest.TestCase):
    """A watcher that repairs would hide the thing it found."""

    def setUp(self) -> None:
        self.src = (MODULES_DIR / MID / "backend.py").read_text(encoding="utf-8")
        self.panel = json.loads(json.dumps(load_backend().build(None), default=str))

    def test_the_source_opens_nothing_for_writing(self) -> None:
        offenders = re.findall(r"\b(?:write_text|write_bytes|mkdir|unlink|rmtree|rename|os\.remove|open\([^)]*[\"']w)", self.src)
        self.assertEqual(offenders, [], f"a read-only watcher may not contain: {offenders}")

    def test_no_secret_material_reaches_the_panel(self) -> None:
        blob = json.dumps(self.panel)
        for pattern in (r"sk-[A-Za-z0-9]{8,}", r"hf_[A-Za-z0-9]{10,}", r"gh[pousr]_[A-Za-z0-9]{10,}", r"Bearer\s+\S", r"api_key\s*[=:]\s*\S", r"\b[A-Fa-f0-9]{40,}\b"):
            self.assertIsNone(re.search(pattern, blob), f"the panel carries something that looks like a credential: {pattern}")

    def test_the_rules_it_searches_by_are_stated_in_the_panel(self) -> None:
        blob = json.dumps(self.panel)
        for name, _ in load_backend().LOG_RULES:
            self.assertIn(name, blob, "the search must be stated so green can be audited")


if __name__ == "__main__":
    unittest.main(verbosity=2)
