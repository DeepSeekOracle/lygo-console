"""The archive pair: `lygo.archiveinfo` (the read-out) and `lygo.archivectrl` (the search surface).

What this file pins, in the order the build pinned it:

* The pair rule: the read-out DECLARES family `info`, owns nothing and writes nowhere; the control
  module ends in `ctrl`, owns `save/archive` through atomicio behind the p0 gate, and declares the
  `wayback` limb that lives in the kernel rather than re-registering it.
* The card paints inside the shell's vocabulary and never claims more than it read: a door that did
  not answer is a NAMED GAP and drags the card off green, and an unreachable archive is an external
  state (amber, with the reason) rather than a console fault (red).
* THE DISTINCTION the module exists to keep: a search that could not run is NOT a search that found
  nothing. A refused target is 400, a quarantined term is 403, a door that failed is 409 - never a
  200 that looks like an empty result.
* The band: no `innerHTML`, no `confirm()`, `addEventListener` (never `.onclick =`), and every
  element the script grabs exists in the document it is served with.

Network calls are replaced, so this suite says nothing about whether archive.org is up - it says what
this kit does with whatever the archive answers.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import version  # noqa: E402
import wayback as wb  # noqa: E402
from modules import validate  # noqa: E402

INFO_DIR = ROOT / "src" / "modules" / "lygo.archiveinfo"
CTRL_DIR = ROOT / "src" / "modules" / "lygo.archivectrl"

LIGHT_STATES = ("green", "amber", "red", "grey")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


INFO = load("mod_archiveinfo_backend", INFO_DIR / "backend.py")
CTRL = load("mod_archivectrl_backend", CTRL_DIR / "backend.py")


# --- doubles ------------------------------------------------------------------------------------


class Req:
    """The request the host hands a handler. It records what the handler answered."""

    def __init__(self, body: dict | None = None):
        self._body = body or {}
        self.code: int | None = None
        self.payload: dict | None = None

    def body(self, _limit: int = 0):
        return self._body

    def json(self, code: int, payload: dict) -> None:
        self.code, self.payload = code, payload


class Ctx:
    """A context that keeps state in a temp dir, like the host's own would."""

    def __init__(self, save: Path):
        self.save = save

    def save_dir(self, *parts) -> Path:
        """The HOST's own shape: `save_dir(*parts)` joins under the save root. A double that takes no
        parts silently sent the log to the kit's real save root (defect 147)."""
        return self.save.joinpath(*[str(x) for x in parts if str(x)])

    def read_json(self, path: Path, default=None):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return default

    def write_json(self, path: Path, data) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data), encoding="utf-8")


class Wire:
    """A transport that answers from a table and counts the asks."""

    def __init__(self, answers: dict[str, dict] | None = None, default: dict | None = None):
        self.answers = answers or {}
        self.default = default or {"status": 200, "body": b"[]", "ctype": "application/json",
                                   "final_url": "", "seconds": 0.01, "error": "", "truncated": False}
        self.calls: list[str] = []

    def install(self, case: unittest.TestCase) -> None:
        self._orig = (wb._get, wb._get_retry)

        def fake(url, *, timeout, max_bytes=wb.SMALL_MAX_BYTES, headers=None, method="GET", tries=2):
            self.calls.append(url)
            for needle, answer in self.answers.items():
                if needle in url:
                    return dict(answer, final_url=answer.get("final_url") or url)
            return dict(self.default, final_url=self.default.get("final_url") or url)

        wb._get = wb._get_retry = fake
        case.addCleanup(lambda: (setattr(wb, "_get", self._orig[0]), setattr(wb, "_get_retry", self._orig[1])))


def wire_that_answers(target: str = "chatagent.ca") -> Wire:
    """A wire where the resolver serves a snapshot and the index has rows."""
    rows = json.dumps([
        ["timestamp", "original", "statuscode", "mimetype", "length", "digest"],
        ["20100501120000", f"http://{target}/", "200", "text/html", "1200", "D1"],
        ["20200501120000", f"http://{target}/", "200", "text/html", "1500", "D2"],
    ])
    return Wire({
        "/web/": {"status": 200, "body": b"<html>x</html>", "ctype": "text/html", "seconds": 0.4,
                  "error": "", "truncated": False,
                  "final_url": f"https://web.archive.org/web/20200501120000/http://{target}/"},
        "wayback/available": {"status": 200, "ctype": "application/json", "seconds": 0.3,
                              "error": "", "truncated": False,
                              "body": json.dumps({"url": target, "archived_snapshots": {"closest": {
                                  "available": True, "status": "200", "timestamp": "20200501120000",
                                  "url": f"http://web.archive.org/web/20200501120000/http://{target}/"}}}).encode()},
        "search/cdx": {"status": 200, "body": rows.encode(), "ctype": "application/json",
                       "seconds": 1.2, "error": "", "truncated": False},
    })


# --- manifests and the host ---------------------------------------------------------------------


class ManifestTests(unittest.TestCase):
    def _manifest(self, mid: str) -> dict:
        return json.loads((ROOT / "src" / "modules" / mid / "module.json").read_text(encoding="utf-8"))

    def test_both_manifests_validate(self):
        for mid in ("lygo.archiveinfo", "lygo.archivectrl"):
            problems = validate.validate_manifest(self._manifest(mid), ROOT / "src" / "modules" / mid, mid)
            self.assertEqual(list(problems), [], f"{mid}: {problems}")

    def test_the_pair_is_ordered_read_out_first(self):
        catalog = json.loads((ROOT / "src" / "modules" / "catalog.json").read_text(encoding="utf-8"))
        rows = {m["id"]: m for m in catalog["modules"]}
        self.assertIn("lygo.archiveinfo", rows)
        self.assertIn("lygo.archivectrl", rows)
        self.assertLess(rows["lygo.archiveinfo"]["order"], rows["lygo.archivectrl"]["order"])
        self.assertTrue(rows["lygo.archiveinfo"]["enabled"] and rows["lygo.archivectrl"]["enabled"])

    def test_the_pane_ids_are_the_shells_dock_names(self):
        self.assertEqual(self._manifest("lygo.archiveinfo")["panes"][0]["id"], "dock.archiveinfo")
        self.assertEqual(self._manifest("lygo.archivectrl")["panes"][0]["id"], "dock.archive")

    def test_the_read_out_owns_nothing_and_the_control_module_owns_its_log(self):
        info = self._manifest("lygo.archiveinfo")
        ctrl = self._manifest("lygo.archivectrl")
        self.assertEqual(info["state"]["owns"], [], "the read-out must own nothing")
        self.assertEqual(info["state"]["gate"], "none")
        self.assertEqual(info["limbs"], [], "the read-out asks the harness; it owns no limb")
        self.assertEqual(ctrl["state"]["owns"], ["save/wb-archive"],
                         "never save/archive: that directory belongs to the sessions archiver")
        self.assertEqual(ctrl["state"]["writes_via"], "atomicio")
        self.assertEqual(ctrl["state"]["gate"], "p0")
        self.assertEqual([l["name"] for l in ctrl["limbs"]], ["wayback"])

    def test_the_console_they_require_is_the_console_shipped(self):
        for mid in ("lygo.archiveinfo", "lygo.archivectrl"):
            need = self._manifest(mid)["requires"]["console"].lstrip(">=")
            self.assertEqual(need, version.release(),
                             f"{mid} needs {need} but this kit is {version.release()}")

    def test_the_host_loads_both_as_wired_on_this_tree(self):
        from modules.host import ModuleHost

        host = ModuleHost(ROOT).load()
        self.assertTrue(host.loaded, host.load_error)
        for mid in ("lygo.archiveinfo", "lygo.archivectrl"):
            row = host.modules.get(mid) or {}
            self.assertEqual(row.get("state"), "WIRED", f"{mid}: {row.get('error') or row}")
            self.assertTrue(row.get("panes"), mid)
        self.assertIn(("GET", "/api/webarchiveinfo"), host.routes)
        self.assertIn(("GET", "/api/webarchive"), host.routes)
        self.assertIn(("POST", "/api/webarchive"), host.routes)

    def test_the_host_answers_both_routes_over_its_own_dispatch(self):
        """Not `register()` shapes but the real path: route table -> Request -> handler -> answer."""
        from modules.host import ModuleHost

        class FakeHandler:
            def __init__(self, path: str, body: dict | None = None):
                self.path = path
                self._b = json.dumps(body or {}).encode("utf-8")
                self.answers: list[tuple[int, dict]] = []

            def _read_body(self, limit: int = 0):
                return self._b

            def _json(self, code: int, payload: dict) -> None:
                self.answers.append((code, payload))

        wire = wire_that_answers()
        wire.install(self)
        host = ModuleHost(ROOT).load()

        read_out = FakeHandler("/api/webarchiveinfo")
        self.assertTrue(host.handle(read_out, "GET", "/api/webarchiveinfo"))
        code, payload = read_out.answers[-1]
        self.assertEqual(code, 200)
        self.assertIn(payload["card"]["state"], LIGHT_STATES)
        self.assertTrue(payload["card"]["state_text"])

        search = FakeHandler("/api/webarchive", {"action": "search", "target": "chatagent.ca",
                                              "scope": "site", "limit": 3})
        self.assertTrue(host.handle(search, "POST", "/api/webarchive"))
        code2, payload2 = search.answers[-1]
        self.assertEqual(code2, 200, payload2)
        self.assertTrue(payload2["ok"])
        self.assertEqual(payload2["count"], 2)
        self.assertTrue(payload2["saved"]["saved"])

        # ...and a refusal keeps its status through the same dispatch path.
        refused = FakeHandler("/api/webarchive", {"action": "search", "target": "http://127.0.0.1:9641/"})
        self.assertTrue(host.handle(refused, "POST", "/api/webarchive"))
        self.assertEqual(refused.answers[-1][0], 400)
        self.assertEqual(refused.answers[-1][1]["error"], "host_not_allowed")

    def test_the_read_out_source_writes_nowhere(self):
        src = (INFO_DIR / "backend.py").read_text(encoding="utf-8")
        for forbidden in ("atomic_write", "write_text", "write_json", "mkdir", "open(", "unlink"):
            self.assertNotIn(forbidden, src, f"the read-out card contains {forbidden!r}")


# --- the cards ----------------------------------------------------------------------------------


class HealthIsCheapByDesignTests(unittest.TestCase):
    """The boot check must not reach the network, and the card must still do the live one.

    `health()` runs inside the host's `load()`, which is inside the console's boot and inside the
    first request a page makes. Measured on this machine before the fix: a stalled TLS read to
    `web.archive.org` cost **7 s of boot per load**, and before the host bounded a health call at all
    the console sat in `load()` for minutes - which from the operator's seat is a hung console.
    """

    def test_the_boot_check_opens_no_socket_and_claims_no_verdict(self):
        wire = Wire()  # any transport call the boot path made would land in wire.calls
        wire.install(self)
        probes = {"n": 0}
        real_probe = wb.probe

        def spy(*a, **k):
            probes["n"] += 1
            return real_probe(*a, **k)

        wb.probe = spy
        self.addCleanup(lambda: setattr(wb, "probe", real_probe))
        with tempfile.TemporaryDirectory() as tmp:
            started = time.monotonic()
            out = INFO.health(Ctx(Path(tmp)))
            elapsed = time.monotonic() - started
        self.assertEqual(wire.calls, [], "the boot check made a network call")
        self.assertEqual(probes["n"], 0, "the boot check ran a live probe")
        self.assertLess(elapsed, 1.0, f"the boot check took {elapsed:.2f}s - it is not local")
        self.assertIsNone(out.get("ok"), "no verdict at boot: reachability is the CARD's answer to give")
        self.assertIn("not at boot", str(out.get("detail") or ""), out)

    def test_the_card_still_does_the_live_check_the_boot_check_skipped(self):
        """Deferring the probe is not dropping it. The pane's own handler still asks for it - the
        shallow answer here is what keeps THIS suite off the network (the file's own rule: it says
        nothing about whether archive.org is up, only what this kit does with whatever it answers)."""
        seen: dict[str, object] = {}
        real_state = wb.state

        def spy(*a, **k):
            seen.update(k)
            return real_state(deep=False)

        wb.state = spy
        self.addCleanup(lambda: setattr(wb, "state", real_state))
        req = Req()
        with tempfile.TemporaryDirectory() as tmp:
            INFO.data(Ctx(Path(tmp)), req)
        self.assertEqual(req.code, 200, req.payload)
        self.assertTrue(seen.get("deep"), "the card stopped asking for the live probe")
        self.assertTrue((req.payload or {}).get("card"), req.payload)


class CardTests(unittest.TestCase):
    def test_the_read_out_card_paints_in_the_shells_vocabulary(self):
        st = {"probe": {"checks": [
            {"door": "availability API", "ok": True, "seconds": 0.3, "answer": "a snapshot was named"},
            {"door": "Memento resolver", "ok": True, "seconds": 1.1, "answer": "served 20200501120000"},
            {"door": "CDX index (paging)", "ok": True, "seconds": 2.0, "answer": "12 page(s)"},
        ], "doors_up": 3, "doors": 3, "canary": wb.PROBE_URL, "gaps": []}, "reachable": True}
        card = INFO.build(None, st)
        self.assertEqual(card["state"], "green")
        self.assertTrue(card["state_text"])
        self.assertTrue(card["at"])
        for light in card["lights"]:
            self.assertIn(light["state"], LIGHT_STATES, light)
            self.assertTrue(light["label"] and light["text"], light)
        for group in card["groups"]:
            self.assertTrue(group["title"])
            for row in group["rows"]:
                self.assertTrue(set(row) >= {"k", "v"}, row)

    def test_a_door_that_did_not_answer_is_a_named_gap_and_never_green(self):
        st = {"probe": {"checks": [
            {"door": "availability API", "ok": True, "seconds": 0.3, "answer": "ok"},
            {"door": "CDX index (paging)", "ok": False, "error": "cdx_http_503",
             "answer": "the index did not answer 200"},
        ], "doors_up": 1, "doors": 2, "gaps": ["CDX index (paging) did not answer: cdx_http_503"],
            "canary": wb.PROBE_URL}, "reachable": True, "gaps": ["CDX index (paging) did not answer: cdx_http_503"]}
        card = INFO.build(None, st)
        self.assertEqual(card["state"], "amber")
        self.assertNotIn("green", [l["state"] for l in card["lights"] if l["label"] == "Index"])
        self.assertTrue(any("cdx_http_503" in m for m in card["missing"]), card["missing"])

    def test_an_unreachable_archive_is_external_never_a_console_fault(self):
        st = {"probe": {"checks": [{"door": "availability API", "ok": False, "error": "availability_dns",
                                    "answer": "gaierror"}], "doors_up": 0, "doors": 1,
                        "gaps": ["availability API did not answer: availability_dns"]},
              "reachable": False, "gaps": ["availability API did not answer: availability_dns"]}
        card = INFO.build(None, st)
        # The CARD is what the shell paints on the pane header, and an unreachable archive is an
        # external state with a reason - never a console fault. The failed door's own light is red
        # and says which door it was: a door that is down is down.
        self.assertEqual(card["state"], "amber", "an unreachable archive must not paint as a fault")
        self.assertNotIn("green", [l["state"] for l in card["lights"]])
        self.assertTrue(any(l["state"] == "red" for l in card["lights"]), card["lights"])
        self.assertTrue(any("unreachable" in m for m in card["missing"]))

    def test_a_probe_that_never_ran_is_grey_with_a_reason(self):
        card = INFO.build(None, {})
        self.assertEqual(card["state"], "grey")
        self.assertTrue(card["missing"])
        self.assertEqual([l["state"] for l in card["lights"]], ["grey"])

    def test_the_search_card_says_nothing_searched_yet_and_never_claims_a_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = CTRL.build(Ctx(Path(tmp)))
        self.assertEqual(card["state"], "grey")
        self.assertIn("nothing searched", card["state_text"])
        for light in card["lights"]:
            self.assertIn(light["state"], LIGHT_STATES, light)

    def test_a_failed_search_paints_red_with_the_harnesss_own_error_name(self):
        st = {"queries": [{"at": "2026-09-27T10:00:00", "target": "https://chatagent.ca",
                           "ok": False, "error": "cdx_timeout", "count": 0, "seconds": 75.0}],
              "watched": [], "cleared": []}
        card = CTRL.build(None, st)
        self.assertEqual(card["state"], "red")
        self.assertIn("cdx_timeout", card["state_text"])

    def test_a_dead_but_archived_watched_link_is_a_finding_and_paints_amber(self):
        st = {"queries": [], "cleared": [], "watched": [
            {"url": "https://chatagent.ca/old", "finding": "dead-but-archived", "live_status": 404,
             "archived": {"timestamp": "20100501120000"}, "note": "the pricing page"}]}
        card = CTRL.build(None, st)
        self.assertEqual(card["state"], "amber")
        self.assertEqual(card["rot"], 1)
        self.assertTrue(any("dead but archived" in l["text"] for l in card["lights"]), card["lights"])


class RefusalTests(unittest.TestCase):
    """Every refusal answers a refusal status, with the reason, and writes nothing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = Ctx(Path(self.tmp.name))
        self.state_file = Path(self.tmp.name) / CTRL.STATE_DIR / CTRL.STATE_NAME

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_refused_target_is_400_with_the_harnesss_own_name_and_hint(self):
        wire = wire_that_answers()
        wire.install(self)
        req = Req({"action": "search", "target": "http://127.0.0.1:9641/"})
        CTRL.control(self.ctx, req)
        self.assertEqual(req.code, 400)
        self.assertEqual(req.payload["error"], "host_not_allowed")
        self.assertTrue(req.payload["hint"])
        self.assertFalse(self.state_file.exists(), "a refused search wrote a record")
        self.assertEqual(wire.calls, [], "a refused ask reached the network")

    def test_a_date_written_as_a_person_writes_it_is_400_with_a_suggestion(self):
        wire = wire_that_answers()
        wire.install(self)
        req = Req({"action": "search", "target": "chatagent.ca", "as_of": "2020-01-01"})
        CTRL.control(self.ctx, req)
        self.assertEqual(req.code, 400)
        self.assertEqual(req.payload["error"], "bad_timestamp")
        self.assertEqual(req.payload["did_you_mean"], "20200101")
        self.assertFalse(self.state_file.exists())

    def test_a_quarantined_term_is_403_with_the_gates_own_reason_and_writes_nothing(self):
        import p0_hook

        wire = wire_that_answers()
        wire.install(self)
        original = p0_hook.gate_prompt
        p0_hook.gate_prompt = lambda text, *a, **k: {"verdict": "QUARANTINE",
                                                     "reason": "P0: the term carries a payload"}
        try:
            req = Req({"action": "search", "target": "chatagent.ca", "word": "anything"})
            CTRL.control(self.ctx, req)
        finally:
            p0_hook.gate_prompt = original
        self.assertEqual(req.code, 403)
        self.assertEqual(req.payload["error"], "gate_refused")
        self.assertIn("payload", str(req.payload["gate"]))
        self.assertFalse(self.state_file.exists(), "a quarantined term was written anyway")

    def test_a_search_that_could_not_run_is_409_and_says_which_door_failed(self):
        wire = Wire(default={"status": 0, "body": b"", "ctype": "", "final_url": "",
                             "seconds": 75.0, "error": "timeout", "why": "no answer within 75s",
                             "truncated": False})
        wire.install(self)
        req = Req({"action": "search", "target": "chatagent.ca", "scope": "site"})
        CTRL.control(self.ctx, req)
        self.assertEqual(req.code, 409)
        self.assertFalse(req.payload["ok"])
        self.assertFalse(req.payload["found_nothing"], "a failed search must not claim it found nothing")
        self.assertTrue(req.payload["errors"])
        self.assertTrue(any("timeout" in e for e in req.payload["errors"]))

    def test_an_unknown_action_is_400_with_the_list(self):
        req = Req({"action": "banana"})
        CTRL.control(self.ctx, req)
        self.assertEqual(req.code, 400)
        self.assertEqual(req.payload["error"], "unknown_action")
        self.assertIn("search", req.payload["actions"])


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = Ctx(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def _search(self, target: str = "chatagent.ca", word: str = "", code_expected: int = 200):
        wire = wire_that_answers()
        wire.install(self)
        req = Req({"action": "search", "target": target, "word": word, "limit": 5})
        CTRL.control(self.ctx, req)
        self.assertEqual(req.code, code_expected, req.payload)
        return req.payload

    def test_a_search_is_written_through_the_context_and_reported(self):
        payload = self._search()
        self.assertTrue(payload["saved"]["saved"])
        state = json.loads((Path(self.tmp.name) / CTRL.STATE_DIR / CTRL.STATE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(state["queries"][0]["target"], "https://chatagent.ca")
        self.assertEqual(state["queries"][0]["count"], 2)
        self.assertTrue(state["updated"])

    def test_the_log_keeps_the_newest_first_and_is_capped(self):
        state = CTRL._state_default()
        state["queries"] = [{"at": f"2026-09-27T10:00:{i:02d}", "target": f"https://x{i}.ca"}
                            for i in range(CTRL.MAX_QUERIES)]
        CTRL._write_state(self.ctx, state)
        payload = self._search("chatagent.ca")
        kept = json.loads((Path(self.tmp.name) / CTRL.STATE_DIR / CTRL.STATE_NAME).read_text(encoding="utf-8"))["queries"]
        self.assertEqual(len(kept), CTRL.MAX_QUERIES)
        self.assertEqual(kept[0]["target"], "https://chatagent.ca")
        self.assertTrue(payload["saved"]["saved"])

    def test_forget_then_restore_round_trips(self):
        self._search()
        req = Req({"action": "forget"})
        CTRL.control(self.ctx, req)
        self.assertEqual(req.code, 200)
        self.assertEqual(req.payload["cleared"], 1)
        self.assertTrue(req.payload["can_restore"])
        self.assertEqual(json.loads((Path(self.tmp.name) / CTRL.STATE_DIR / CTRL.STATE_NAME).read_text(encoding="utf-8"))["queries"], [])
        req2 = Req({"action": "restore"})
        CTRL.control(self.ctx, req2)
        self.assertEqual(req2.code, 200)
        self.assertEqual(req2.payload["restored"], 1)
        self.assertEqual(len(json.loads((Path(self.tmp.name) / CTRL.STATE_DIR / CTRL.STATE_NAME).read_text(encoding="utf-8"))["queries"]), 1)

    def test_restoring_nothing_is_409_not_a_quiet_success(self):
        req = Req({"action": "restore"})
        CTRL.control(self.ctx, req)
        self.assertEqual(req.code, 409)
        self.assertEqual(req.payload["error"], "nothing_to_restore")

    def test_watch_records_what_the_live_web_and_the_archive_said(self):
        wire = Wire({
            "/web/": {"status": 200, "body": b"<html>x</html>", "ctype": "text/html", "seconds": 0.4,
                      "error": "", "truncated": False,
                      "final_url": "https://web.archive.org/web/20100501120000/http://chatagent.ca/old"},
            "chatagent.ca/old": {"status": 404, "body": b"gone", "ctype": "text/html",
                                 "seconds": 0.1, "error": "", "truncated": False},
        })
        wire.install(self)
        req = Req({"action": "watch", "target": "https://chatagent.ca/old", "note": "the pricing page"})
        CTRL.control(self.ctx, req)
        self.assertEqual(req.code, 200, req.payload)
        entry = req.payload["entry"]
        self.assertEqual(entry["finding"], "dead-but-archived",
                         "a dead link the archive holds a copy of is the finding this list exists for")
        self.assertEqual(entry["archived"]["timestamp"], "20100501120000")
        self.assertEqual(req.payload["card"]["rot"], 1)

    def test_unwatching_something_that_is_not_watched_is_409(self):
        req = Req({"action": "unwatch", "target": "https://chatagent.ca/nope"})
        CTRL.control(self.ctx, req)
        self.assertEqual(req.code, 409)
        self.assertEqual(req.payload["error"], "not_watched")

    def test_the_real_save_path_is_its_own_directory(self):
        import paths

        original = paths.SAVE
        try:
            paths.SAVE = Path(self.tmp.name)
            self.assertEqual(CTRL._state_path(None),
                             Path(self.tmp.name) / CTRL.STATE_DIR / CTRL.STATE_NAME)
            ok, why = CTRL._write_state(None, CTRL._state_default())
            self.assertTrue(ok, why)
            self.assertTrue((Path(self.tmp.name) / CTRL.STATE_DIR / CTRL.STATE_NAME).is_file())
            # The territory rule, in one line: the log must not share a name with the sessions
            # archiver's directory or sit as a bare file in the save root.
            self.assertNotEqual(CTRL.STATE_DIR, "archive")
            self.assertNotIn("/", CTRL.STATE_NAME)
        finally:
            paths.SAVE = original

    def test_a_host_context_keeps_the_log_in_its_own_directory(self):
        class HostishCtx:
            """`save_dir(*parts)` is the host's own shape: parts, joined under the save root."""

            def __init__(self, save: Path):
                self.save = save
                self.seen: list = []

            def save_dir(self, *parts):
                self.seen.append(parts)
                return self.save.joinpath(*parts)

            def read_json(self, path, default=None):
                return default

            def write_json(self, path, data) -> None:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(json.dumps(data), encoding="utf-8")

        ctx = HostishCtx(Path(self.tmp.name))
        path = CTRL._state_path(ctx)
        self.assertEqual(path, Path(self.tmp.name) / CTRL.STATE_DIR / CTRL.STATE_NAME)
        self.assertEqual(ctx.seen[-1], (CTRL.STATE_DIR,),
                         "the host's save_dir must be asked for the module's OWN directory")
        ok, why = CTRL._write_state(ctx, CTRL._state_default())
        self.assertTrue(ok, why)
        self.assertTrue(path.is_file())


class BandSourceTests(unittest.TestCase):
    """Static rules for the band - the things a browser cannot be asked in a unit test."""

    def setUp(self):
        self.js = (ROOT / "portal" / "archive.js").read_text(encoding="utf-8")
        self.html = (ROOT / "portal" / "index.html").read_text(encoding="utf-8")
        self.css = (ROOT / "portal" / "style.css").read_text(encoding="utf-8")

    def test_the_band_never_inserts_archived_text_as_markup(self):
        self.assertNotIn("innerHTML", self.js, "the band writes markup somewhere - archived pages are data")
        self.assertNotIn("outerHTML", self.js)
        self.assertNotIn("insertAdjacentHTML", self.js)
        self.assertIn("readbody.textContent", self.js, "the archived copy must land in a text node")

    def test_the_band_offers_undo_instead_of_a_modal(self):
        self.assertNotIn("confirm(", self.js)
        self.assertNotIn("alert(", self.js)
        self.assertIn("restore", self.js)

    def test_the_band_listens_rather_than_assigning_handlers(self):
        self.assertIn("addEventListener", self.js)
        for bad in (".onclick =", ".onchange =", ".onsubmit ="):
            self.assertNotIn(bad, self.js, f"{bad!r} overwrites another handler")

    def test_every_value_the_band_writes_is_announced(self):
        self.assertIn("dispatchEvent(new Event(\"input\"", self.js,
                      "a field written from a result must tell the field, like a keystroke would")
        self.assertNotIn(".value =", self.js.replace("input.value = value;", ""),
                         "a value is being set outside setValue()")

    def test_the_band_sends_the_console_token(self):
        self.assertIn("X-LYGO-LLM-Token", self.js)
        self.assertIn("/api/webarchive", self.js)

    def test_every_element_the_band_grabs_exists_in_the_document(self):
        import re

        wanted = set(re.findall(r'\$\("([a-z0-9-]+)"\)', self.js))
        self.assertTrue(wanted)
        for eid in sorted(wanted):
            self.assertIn(f'id="{eid}"', self.html, f"the band looks for #{eid}, which the page does not have")

    def test_the_warning_class_has_a_rule(self):
        self.assertIn("#wb-status.warn", self.css,
                      "a status class with no rule is not a warning, it is a class name")

    def test_the_band_is_served_and_cache_busted_with_the_release(self):
        self.assertIn("/static/archive.js?v=", self.html)
        self.assertIn("archive.js", self.css + self.html)
        for name, tag in (("style.css", "/static/style.css?v="), ("app.js", "/static/app.js?v=")):
            self.assertIn(tag, self.html, name)


if __name__ == "__main__":
    unittest.main()
