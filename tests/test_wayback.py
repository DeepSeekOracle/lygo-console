"""The Wayback harness and the archive limb: what it refuses, what it concludes, what it costs.

Two rules this file exists to pin, both found by hand before the module was written:

1.  AN EMPTY AVAILABILITY ANSWER IS NOT PROOF. Measured 2026-09-27: the availability API answered
    `{"archived_snapshots": {}}` for `url=example.com&timestamp=20060101`, while the CDX index holds
    `20060101064348` for that URL. `closest()` therefore asks the Memento resolver first and says
    which check answered, and this file proves the fall-through rather than trusting the comment.

2.  THE INDEX HAS A PRICE, AND A FAILURE IS NOT AN EMPTY RESULT. A CDX timeout is reported as
    `cdx_timeout` with the levers that narrow the ask - never as "no captures". A 503 from the
    archive's own back-pressure is retried exactly once, and the attempt count is reported.

Everything here runs OFFLINE: the transport is replaced, so the suite never depends on the Internet
Archive being up, and a test that fails is a fact about this kit rather than about archive.org.
"""
from __future__ import annotations

import json
import sys
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import limbs  # noqa: E402
import tools  # noqa: E402  (the schema and the core set: the limb has to be VISIBLE to the agent)
import wayback as wb  # noqa: E402


class FakeWire:
    """A transport that answers from a table and records every ask."""

    def __init__(self, answers: dict[str, dict] | None = None, default: dict | None = None):
        self.answers = {k: dict(v) for k, v in (answers or {}).items()}
        self.default = default if default is not None else self.ok("{}")
        self.calls: list[str] = []

    # -- builders ---------------------------------------------------------------
    @staticmethod
    def ok(body: str | bytes, ctype: str = "application/json", final: str = "") -> dict:
        raw = body.encode("utf-8") if isinstance(body, str) else body
        return {"status": 200, "body": raw, "truncated": False, "ctype": ctype,
                "final_url": final, "seconds": 0.01, "error": "", "attempts": 1}

    @staticmethod
    def status(code: int, body: str = "") -> dict:
        return {"status": code, "body": body.encode("utf-8"), "truncated": False,
                "ctype": "text/html", "final_url": "", "seconds": 0.01, "error": "", "attempts": 1}

    @staticmethod
    def broke(error: str = "timeout", why: str = "no answer within 75s") -> dict:
        return {"status": 0, "body": b"", "truncated": False, "ctype": "", "final_url": "",
                "seconds": 75.0, "error": error, "why": why, "attempts": 1}

    # -- the patch -------------------------------------------------------------
    def install(self, case: unittest.TestCase) -> None:
        self._orig = (wb._get, wb._get_retry)

        def fake_get(url, *, timeout, max_bytes=wb.SMALL_MAX_BYTES, headers=None, method="GET"):
            self.calls.append(url)
            for needle, answer in self.answers.items():
                if needle in url:
                    return dict(answer, final_url=answer.get("final_url") or url)
            return dict(self.default, final_url=self.default.get("final_url") or url)

        def fake_retry(url, *, timeout, max_bytes=wb.SMALL_MAX_BYTES, headers=None, method="GET",
                       tries=2):
            first = fake_get(url, timeout=timeout, max_bytes=max_bytes, headers=headers, method=method)
            if first.get("status") in wb._RETRY_STATUS or first.get("error") in ("refused", "net_error"):
                second = fake_get(url, timeout=timeout, max_bytes=max_bytes, headers=headers,
                                  method=method)
                second = dict(second, attempts=2)
                return second
            return first

        wb._get, wb._get_retry = fake_get, fake_retry
        case.addCleanup(self._restore)

    def _restore(self) -> None:
        wb._get, wb._get_retry = self._orig


def availability_body(available: bool, url: str = "", ts: str = "20060101064348") -> str:
    if not available:
        return json.dumps({"url": url, "archived_snapshots": {}})
    return json.dumps({"url": url, "archived_snapshots": {"closest": {
        "available": True, "status": "200", "timestamp": ts,
        "url": f"http://web.archive.org/web/{ts}/{url}"}}})


class TargetRefusalTests(unittest.TestCase):
    """Nothing is fetched for an ask the harness refuses, and the refusal names the rule."""

    def test_loopback_and_private_and_local_are_refused_by_name(self):
        for bad, name in (
            ("", "no_url"),
            ("file:///c:/secrets.txt", "scheme_not_allowed"),
            ("http://127.0.0.1:9641/", "host_not_allowed"),
            ("https://192.168.1.10/admin", "private_host"),
            ("https://169.254.169.254/latest/meta-data/", "private_host"),
            ("https://printer.local/x", "host_not_allowed"),
            ("https://api.internal/v1", "host_not_allowed"),
            ("https://a b.com/x", "bad_url"),
            ("https://exa\nmple.com/", "bad_url"),
            ("https://example.com/anything", "placeholder_url"),
            ("https://" + "x" * 2100 + ".com/", "url_too_long"),
        ):
            url, refusal = wb.clean_target(bad)
            self.assertIsNotNone(refusal, bad)
            self.assertEqual(refusal["error"], name, bad)
            self.assertTrue(refusal.get("why"), bad)
            self.assertEqual(url, "", bad)

    def test_a_refused_target_never_reaches_the_wire(self):
        wire = FakeWire()
        wire.install(self)
        for bad in ("http://127.0.0.1/", "file:///x", "https://example.com/"):
            out = wb.closest(bad)
            self.assertFalse(out.get("ok"), bad)
        self.assertEqual(wire.calls, [], "a refused ask was sent to the network")

    def test_a_bare_domain_is_a_target_and_the_scheme_is_added(self):
        url, refusal = wb.clean_target("chatagent.ca")
        self.assertIsNone(refusal)
        self.assertEqual(url, "https://chatagent.ca")
        self.assertEqual(wb.clean_target("http://chatagent.ca/")[0], "http://chatagent.ca/")

    def test_a_date_written_like_a_person_writes_it_is_refused_with_a_suggestion(self):
        ts, refusal = wb.norm_timestamp("2020-01-01")
        self.assertEqual(ts, "")
        self.assertEqual(refusal["error"], "bad_timestamp")
        self.assertEqual(refusal["did_you_mean"], "20200101")
        # ...and the digits themselves are accepted, 1 to 14 of them.
        for good in ("2010", "201005", "20100501", "20100501120000"):
            self.assertEqual(wb.norm_timestamp(good)[0], good)

    def test_unknown_parameters_are_refused_with_the_list(self):
        self.assertEqual(wb._norm_match("site")[0], "host")
        self.assertEqual(wb._norm_match("banana")[1]["error"], "unknown_match")
        self.assertEqual(wb._norm_collapse("urlkey")[0], "urlkey")
        self.assertEqual(wb._norm_filters("statuscode:200")[0], ["statuscode:200"])
        self.assertEqual(wb._norm_filters("banana:1")[1]["error"], "unknown_filter_field")
        self.assertEqual(wb._norm_filters("no-colon-here")[1]["error"], "bad_filter")
        self.assertEqual(wb._norm_sort("reverse")[0], "reverse")
        self.assertEqual(wb._norm_sort("sideways")[1]["error"], "unknown_sort")


class AvailabilityTests(unittest.TestCase):
    def test_a_snapshot_is_read_as_the_documented_shape(self):
        wire = FakeWire({"wayback/available": FakeWire.ok(availability_body(True, "chatagent.ca"))})
        wire.install(self)
        out = wb.available("chatagent.ca")
        self.assertTrue(out["ok"])
        self.assertTrue(out["available"])
        self.assertEqual(out["snapshot"]["timestamp"], "20060101064348")
        self.assertEqual(out["action"], "available")
        self.assertIn("https_note", out)  # the API reports http; the note says so

    def test_an_empty_answer_is_available_false_and_not_an_error(self):
        wire = FakeWire({"wayback/available": FakeWire.ok('{"url": "x", "archived_snapshots": {}}')})
        wire.install(self)
        out = wb.available("chatagent.ca")
        self.assertTrue(out["ok"])
        self.assertFalse(out["available"])
        self.assertEqual(out["snapshot"], {})

    def test_an_unreachable_archive_is_named_not_zeroed(self):
        wire = FakeWire({"wayback/available": FakeWire.broke("dns", "gaierror")},
                        default=FakeWire.broke("dns", "gaierror"))
        wire.install(self)
        out = wb.available("chatagent.ca")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "availability_dns")
        self.assertTrue(out["why"])


class ClosestTests(unittest.TestCase):
    """The measured lesson: the resolver answers what the availability API denies."""

    def test_the_resolver_answers_first_when_it_can(self):
        wire = FakeWire({
            "/web/": FakeWire.ok("<html>archived</html>", "text/html",
                                 final="https://web.archive.org/web/20060101064348/http://example.org/"),
            "wayback/available": FakeWire.ok(availability_body(False, "chatagent.ca")),
        })
        wire.install(self)
        out = wb.closest("chatagent.ca", "20060101")
        self.assertTrue(out["ok"])
        self.assertTrue(out["archived"])
        self.assertEqual(out["via"], "memento timegate")
        self.assertEqual(out["served_timestamp"], "20060101064348")
        self.assertEqual(out["snapshot"]["timestamp"], "20060101064348")
        self.assertIn("drift", out)
        self.assertEqual(out["checked"], ["memento timegate"])
        # The index was NOT asked: a window query there costs 7-40 s, and the resolver answered.
        self.assertFalse([c for c in wire.calls if "search/cdx" in c], wire.calls)

    def test_a_resolver_that_has_nothing_falls_through_to_availability_then_the_index(self):
        wire = FakeWire({
            "/web/": FakeWire.status(404, "<html>no memento</html>"),
            "wayback/available": FakeWire.ok(availability_body(False, "example.org")),
            "search/cdx": FakeWire.ok(json.dumps([
                ["timestamp", "original", "statuscode", "mimetype", "length", "digest"],
                ["20020120142510", "http://example.org/", "200", "text/html", "1792", "ABC"],
            ])),
        })
        wire.install(self)
        out = wb.closest("chatagent.ca", "1999")
        self.assertTrue(out["ok"])
        self.assertEqual(out["via"], "cdx index")
        self.assertTrue(out["archived"])
        self.assertEqual(out["oldest"]["timestamp"], "20020120142510")
        self.assertIn("memento timegate", out["checked"])
        self.assertIn("availability API", out["checked"])
        self.assertIn("cdx index (oldest capture)", out["checked"])
        self.assertTrue([c for c in wire.calls if "search/cdx" in c])

    def test_when_nothing_answers_it_says_so_instead_of_guessing(self):
        wire = FakeWire(default=FakeWire.broke("timeout", "no answer within 75s"))
        wire.install(self)
        out = wb.closest("chatagent.ca", "2020")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "archive_unreachable")
        self.assertFalse(out["archived"])
        self.assertIn("nothing can be said", out["why"])


class CdxTests(unittest.TestCase):
    def test_rows_are_parsed_from_the_header_the_index_sends(self):
        body = json.dumps([
            ["timestamp", "original", "statuscode", "mimetype", "length", "digest"],
            ["20100501120000", "https://chatagent.ca/a", "200", "text/html", "1200", "D1"],
            ["20110501120000", "https://chatagent.ca/b", "404", "text/html", "300", "D2"],
        ])
        wire = FakeWire({"search/cdx": FakeWire.ok(body)})
        wire.install(self)
        out = wb.captures("chatagent.ca", limit=5)
        self.assertTrue(out["ok"])
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["fields"][0], "timestamp")
        row = out["rows"][0]
        self.assertEqual(row["date"], "2010-05-01 12:00")
        self.assertEqual(row["snapshot_url"], "https://web.archive.org/web/20100501120000/https://chatagent.ca/a")
        self.assertEqual(out["rows"][1]["statuscode"], "404")

    def test_an_empty_answer_is_zero_rows_not_an_error(self):
        wire = FakeWire({"search/cdx": FakeWire.ok("[]")})
        wire.install(self)
        out = wb.captures("chatagent.ca")
        self.assertTrue(out["ok"])
        self.assertEqual(out["count"], 0)
        self.assertEqual(out["rows"], [])

    def test_the_ask_carries_the_parameters_it_claims_to(self):
        wire = FakeWire({"search/cdx": FakeWire.ok("[]")})
        wire.install(self)
        wb.captures("chatagent.ca", limit=7, match="domain", from_="2010", to="2015",
                    collapse="urlkey", sort="reverse", status="200", filter="mimetype:text/html")
        asked = urllib.parse.parse_qs(urllib.parse.urlsplit(wire.calls[0]).query)
        self.assertEqual(asked["url"], ["https://chatagent.ca"])
        self.assertEqual(asked["matchType"], ["domain"])
        self.assertEqual(asked["limit"], ["7"])
        self.assertEqual(asked["from"], ["2010"])
        self.assertEqual(asked["to"], ["2015"])
        self.assertEqual(asked["collapse"], ["urlkey"])
        self.assertEqual(asked["sort"], ["reverse"])
        self.assertEqual(asked["output"], ["json"])
        self.assertEqual(sorted(asked["filter"]), ["mimetype:text/html", "statuscode:200"])

    def test_a_timeout_is_named_with_the_levers_that_narrow_the_ask(self):
        wire = FakeWire({"search/cdx": FakeWire.broke("timeout", "no answer within 75s")})
        wire.install(self)
        out = wb.captures("chatagent.ca", match="domain")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "cdx_timeout")
        self.assertIn("narrow", out["hint"])
        # ...and it is NOT dressed up as "no captures".
        self.assertNotIn("rows", out)

    def test_the_archives_own_back_pressure_is_retried_once_and_the_count_is_reported(self):
        # A 503 on the first ask, then rows. The REAL retry helper does the retrying - the test only
        # replaces the door underneath it, so what is being tested is this kit's retry and not a
        # retry re-implemented in the test.
        real_retry = wb._get_retry
        wire = FakeWire()
        wire.install(self)
        wb._get_retry = real_retry          # keep our own helper; only the transport is fake
        calls = {"n": 0}
        originals = (wb._get, wb._get_retry)

        def flaky(url, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return dict(FakeWire.status(503, "<html>Temporarily Offline</html>"))
            return dict(FakeWire.ok('[]'))

        try:
            wb._get = flaky
            out = wb.captures("chatagent.ca")
        finally:
            wb._get, wb._get_retry = originals
        self.assertTrue(out["ok"], out)
        self.assertEqual(calls["n"], 2, "the 503 was not retried exactly once")
        self.assertEqual(out["attempts"], 2)

        # ...and a TIMEOUT is never retried: a 40 s query must not become an 80 s wait.
        timeouts = {"n": 0}

        def always_timeout(url, **kw):
            timeouts["n"] += 1
            return dict(FakeWire.broke("timeout", "no answer within 75s"))

        try:
            wb._get_retry = real_retry
            wb._get = always_timeout
            out2 = wb.captures("chatagent.ca")
        finally:
            wb._get, wb._get_retry = originals
        self.assertFalse(out2["ok"])
        self.assertEqual(out2["error"], "cdx_timeout")
        self.assertEqual(timeouts["n"], 1, "a timeout was retried - that doubles a wait the operator watches")

    def test_pages_are_pages_not_a_capture_count(self):
        wire = FakeWire({"showNumPages": FakeWire.ok("133\n", "text/plain")})
        wire.install(self)
        out = wb.pages("chatagent.ca")
        self.assertTrue(out["ok"])
        self.assertEqual(out["pages"], 133)
        self.assertEqual(out["note"], "pages of results, not a count of captures")

    def test_find_searches_the_urls_of_a_site_for_a_word(self):
        wire = FakeWire({"search/cdx": FakeWire.ok("[]")})
        wire.install(self)
        out = wb.find("chatagent.ca", "console")
        self.assertTrue(out["ok"])
        asked = urllib.parse.parse_qs(urllib.parse.urlsplit(wire.calls[0]).query)
        self.assertEqual(asked["matchType"], ["domain"])
        self.assertEqual(asked["filter"], ["original:.*console.*"])
        self.assertEqual(asked["collapse"], ["urlkey"])
        self.assertFalse(wb.find("chatagent.ca", "")["ok"])


class TimemapAndReadTests(unittest.TestCase):
    def test_the_target_travels_in_url_and_never_in_the_path(self):
        # MEASURED: a path target beside a query string answers HTTP 200 with one byte of nothing.
        wire = FakeWire({"timemap": FakeWire.ok(
            '<https://www.lygo.ai/>; rel="original", '
            '<https://web.archive.org/web/20240815235655/https://www.lygo.ai/>; '
            'rel="memento"; datetime="Thu, 15 Aug 2024 23:56:55 GMT"',
            "application/link-format")})
        wire.install(self)
        out = wb.timemap("lygo.ai", limit=5)
        self.assertTrue(out["ok"])
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["rows"][0]["timestamp"], "20240815235655")
        self.assertEqual(out["rows"][0]["datetime"], "Thu, 15 Aug 2024 23:56:55 GMT")
        self.assertEqual(out["endpoints"]["original"], "https://www.lygo.ai/")
        split = urllib.parse.urlsplit(wire.calls[0])
        self.assertEqual(split.path, "/web/timemap/link", "the target must NOT travel in the path")
        self.assertNotIn("lygo.ai", split.path)
        self.assertEqual(urllib.parse.parse_qs(split.query)["url"], ["https://lygo.ai"])

    def test_an_empty_timemap_body_is_named_not_read_as_no_history(self):
        wire = FakeWire({"timemap": FakeWire.ok("\n", "application/link-format")})
        wire.install(self)
        out = wb.timemap("lygo.ai")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "timemap_empty")

    def test_reading_a_missing_capture_is_an_answer_not_a_failure(self):
        wire = FakeWire({"/web/": FakeWire.status(404, "<html>not archived</html>")})
        wire.install(self)
        out = wb.read("never-archived-lygo-963.com")
        self.assertTrue(out["ok"])
        self.assertFalse(out["archived"])
        self.assertIn("no capture", out["why"])

    def test_reading_reports_the_snapshot_that_was_actually_served(self):
        wire = FakeWire({"/web/": FakeWire.ok("<html>Example</html>", "text/html",
                                             final="https://web.archive.org/web/20191231234501id_/http://example.org/")})
        wire.install(self)
        out = wb.read("chatagent.ca", "20200101", raw=True)
        self.assertTrue(out["ok"])
        self.assertTrue(out["archived"])
        self.assertEqual(out["served_timestamp"], "20191231234501")
        self.assertIn("id_", out["snapshot_url"])
        self.assertIn("Example", out["text"])

    def test_the_link_builder_never_touches_the_network(self):
        wire = FakeWire()
        wire.install(self)
        out = wb.link("20100501", "chatagent.ca")
        self.assertTrue(out["ok"])
        self.assertEqual(out["snapshot"], "https://web.archive.org/web/20100501/https://chatagent.ca")
        self.assertEqual(wb.link("20100501", "chatagent.ca", raw=True)["snapshot"],
                         "https://web.archive.org/web/20100501id_/https://chatagent.ca")
        self.assertEqual(wb.link("", "chatagent.ca")["snapshot"],
                         "https://web.archive.org/web/2/https://chatagent.ca")
        self.assertFalse(wb.link("yesterday", "chatagent.ca")["ok"])
        self.assertEqual(wire.calls, [])

    def test_the_live_probe_is_https_only_and_never_downloads_a_page(self):
        wire = FakeWire({"chatagent.ca": FakeWire.status(404)})
        wire.install(self)
        self.assertEqual(wb.live_status("http://chatagent.ca/")["error"], "live_https_only")
        out = wb.live_status("https://chatagent.ca/")
        self.assertTrue(out["ok"])
        self.assertTrue(out["dead"])
        self.assertEqual(wire.calls, ["https://chatagent.ca/"])


class ProbeCacheTests(unittest.TestCase):
    """One panel refresh must not become three network round trips every thirty seconds."""

    def test_the_probe_is_cached_and_forgetting_it_re_probes(self):
        wire = FakeWire({
            "wayback/available": FakeWire.ok(availability_body(True, "archive.org")),
            "/web/": FakeWire.ok("<html>x</html>", "text/html",
                                 final="https://web.archive.org/web/20260101000000/https://archive.org/"),
            "showNumPages": FakeWire.ok("12\n", "text/plain"),
        })
        wire.install(self)
        wb.forget()
        first = wb.probe()
        self.assertEqual(len(wire.calls), 3, wire.calls)
        self.assertFalse(first["cached"])
        second = wb.probe()
        self.assertTrue(second["cached"])
        self.assertEqual(len(wire.calls), 3, "a cached probe still called the archive")
        wb.forget()
        third = wb.probe()
        self.assertFalse(third["cached"])
        self.assertEqual(len(wire.calls), 6)
        self.assertEqual(wb.probe()["doors"], 3)
        self.assertEqual(wb.probe()["canary"], wb.PROBE_URL)

    def test_the_canary_is_a_real_host_because_a_health_check_must_not_report_on_a_fabricated_one(self):
        sys.path.insert(0, str(ROOT / "src"))
        try:
            from admin_map import is_placeholder_url
        except Exception:  # noqa: BLE001
            self.skipTest("admin_map is not importable on this tree")
        self.assertFalse(is_placeholder_url(wb.PROBE_URL),
                         "the probe canary is a placeholder domain: a health check would be reporting "
                         "on a domain that does not exist")


class LimbTests(unittest.TestCase):
    """`wayback` has to be VISIBLE to the local brain, and it has to understand a model's words."""

    def _schema(self) -> dict:
        for entry in limbs.EXTRA_SCHEMA:
            fn = entry.get("function") or {}
            if fn.get("name") == "wayback":
                return fn
        self.fail("the wayback limb has no schema entry")

    def test_the_limb_is_in_the_core_schema_the_local_brain_is_handed(self):
        names = [t["function"]["name"] for t in tools.core_schema()]
        self.assertIn("wayback", names,
                      "wayback is not in CORE_NAMES: the local brain cannot call it at all")
        self.assertLessEqual(len(tools.CORE_NAMES), 44, "the core cap is 44 since `film` landed")
        self.assertLess(len(str(tools.core_schema())), 26980, "the core schema is over its char budget")

    def test_the_schema_tells_a_model_the_actions_and_the_cost(self):
        fn = self._schema()
        text = fn["description"]
        for word in ("available", "closest", "captures", "site", "find", "timemap", "read", "link"):
            self.assertIn(word, text)
        self.assertIn("RESOURCE", text)
        self.assertIn("slow", text.lower())
        self.assertEqual(fn["parameters"]["required"], ["url"])

    def test_a_models_own_words_reach_the_right_arguments(self):
        _name, args = limbs.canonicalize("wayback", {"site": "chatagent.ca", "mode": "snapshots",
                                                     "date": "2024"})
        self.assertEqual(args["url"], "chatagent.ca")
        self.assertEqual(args["action"], "snapshots")
        self.assertEqual(args["timestamp"], "2024")
        # The one exception to the "a url must start with http" rule, and it is deliberate: the
        # archive's documented target may be a bare host, so a host-shaped alias fills `url`.
        args = limbs.canonicalize("wayback", {"page": "https://x.ai/", "when": "2020"})[1]
        self.assertEqual(args["url"], "https://x.ai/")
        self.assertEqual(args["timestamp"], "2020")
        self.assertEqual(limbs.canonicalize("wayback", {"site": "chatagent.ca"})[1]["url"], "chatagent.ca")
        # Prose is still not an address: an alias holding a sentence fills nothing, and an explicit
        # `url` the model wrote as prose is refused by the harness itself, by name.
        self.assertNotIn("url", limbs.canonicalize("wayback", {"page": "the old version of my site"})[1])
        self.assertNotIn("url", limbs.canonicalize("web_fetch", {"page": "just some words here"})[1])
        refused = wb.clean_target("the old version of my site")[1]
        self.assertIsNotNone(refused)
        self.assertIn(refused["error"], ("bad_url", "no_url"))

    def test_an_unknown_action_is_refused_with_the_list(self):
        out = limbs._wayback_limb(wb, {"url": "chatagent.ca", "action": "banana"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "unknown_action")
        self.assertIn("available", out["actions"])
        self.assertEqual(out["class"], "RESOURCE")

    def test_the_answers_are_shaped_for_a_small_model_and_say_they_were(self):
        out = limbs._wayback_for_model({"ok": True, "rows": [{"i": i} for i in range(40)]})
        self.assertEqual(len(out["rows"]), limbs._WAYBACK_MODEL_ROWS)
        self.assertEqual(out["rows_shown"], limbs._WAYBACK_MODEL_ROWS)
        self.assertIn("40 rows", out["rows_note"])
        text_out = limbs._wayback_for_model({"ok": True, "text": "x" * 9000})
        self.assertEqual(len(text_out["text"]), limbs._WAYBACK_MODEL_CHARS)
        self.assertIn("cut at", text_out["text_note"])

    def test_every_action_reaches_the_harness_door_it_names(self):
        seen: list[str] = []
        real = {name: getattr(wb, name) for name in
                ("available", "closest", "captures", "inventory", "find", "timemap", "read", "link",
                 "pages", "live_status")}
        try:
            for name in real:
                setattr(wb, name, (lambda n: (lambda *a, **k: (seen.append(n), {"ok": True})[1]))(name))
            for action, door in (("available", "available"), ("closest", "closest"),
                                 ("captures", "captures"), ("site", "inventory"), ("find", "find"),
                                 ("timemap", "timemap"), ("read", "read"), ("link", "link"),
                                 ("pages", "pages"), ("live", "live_status"),
                                 ("snapshots", "captures"), ("history", "captures"), ("old", "read")):
                seen.clear()
                out = limbs._wayback_limb(wb, {"url": "chatagent.ca", "action": action, "word": "x"})
                self.assertTrue(out.get("ok"), (action, out))
                self.assertEqual(seen, [door], action)
        finally:
            for name, fn in real.items():
                setattr(wb, name, fn)


if __name__ == "__main__":
    unittest.main()
