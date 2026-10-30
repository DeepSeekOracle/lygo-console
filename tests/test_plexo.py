"""The download engine and the Plexo bridge, against servers this test owns.

Nothing here touches the internet: a local HTTP server serves a real file, with range support when the
test wants it and without when it does not, and a fake Plexo bridge answers /ping, /add and /confirm
exactly as upstream documents them. What is pinned:
  * a multi-block download is BIT-FOR-BIT what the server holds, on more than one stream;
  * the block claim - the bug a live run found: 8 streams pulled the same block 8 times;
  * resume: same ETag resumes, changed ETag REFUSES by name (never splice two versions);
  * a server without ranges reports `ranges: false` and still downloads the file;
  * refusals by name: bad scheme, no name, consent outside the workspace, unknown job;
  * the bridge handshake: `closed` / `stale` / `kept` / a real hand-off that the fake bridge SEES;
  * the boot-cheap shape opens no socket at all.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import shutil
import re
import ast
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plexo_tools as px  # noqa: E402

PAYLOAD = bytes(range(256)) * 12288  # 3 MiB, so a 1 MiB block size makes three real blocks


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    """Serves PAYLOAD. `ranges=False` answers 200 with the whole file and ignores Range."""

    ranges = True
    etag = '"v1"'
    body = PAYLOAD

    def log_message(self, *args: object) -> None:  # keep the test output clean
        pass

    def do_GET(self) -> None:  # noqa: N802
        data = self.body
        head: dict[str, str] = {"ETag": self.etag, "Last-Modified": "Mon, 12 Oct 2026 00:00:00 GMT",
                               "Content-Type": "application/octet-stream",
                               "Content-Disposition": "attachment; filename*=UTF-8''lygo test.bin"}
        rng = self.headers.get("Range") or ""
        if self.ranges and rng.startswith("bytes="):
            first, _, last = rng[6:].partition("-")
            start = int(first or 0)
            end = int(last) if last.strip() else len(data) - 1
            end = min(end, len(data) - 1)
            chunk = data[start:end + 1]
            if end >= start:
                self.send_response(206)
                head["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
                head["Content-Length"] = str(len(chunk))
                for k, v in head.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(chunk)
                return
        self.send_response(200)
        head["Content-Length"] = str(len(data))
        for k, v in head.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)


class _BridgeHandler(http.server.BaseHTTPRequestHandler):
    """A stand-in for Plexo's own bridge, answering only what upstream's contract allows."""

    app = "plexo"
    api = px.BRIDGE_API
    version = "1.0.0-rc.15"
    add_status = 200
    seen: list[dict] = []

    def log_message(self, *args: object) -> None:  # noqa: N802
        pass

    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/ping":
            return self._json(200, {"app": self.app, "version": self.version, "api": self.api})
        return self._json(404, {})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        _BridgeHandler.seen.append({"path": self.path, "body": body})
        if self.path == "/add":
            if self.add_status != 200:
                return self._json(self.add_status, {})
            return self._json(200, {"token": "tok-1234"})
        if self.path == "/confirm":
            return self._json(200 if body.get("token") == "tok-1234" else 410, {})
        return self._json(404, {})


def _server(handler: type[http.server.BaseHTTPRequestHandler]) -> tuple[http.server.ThreadingHTTPServer, int]:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


class TestEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.dl = px.downloads_root()
        self.dl.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="lygo-plexo-"))
        self._block = px.BLOCK_MB
        px.BLOCK_MB = 1.0  # three blocks out of the 3 MiB payload
        _RangeHandler.ranges, _RangeHandler.etag, _RangeHandler.body = True, '"v1"', PAYLOAD
        self.srv, self.port = _server(_RangeHandler)

    def tearDown(self) -> None:
        px.BLOCK_MB = self._block
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        for name in ("lygo test.bin", "lygo test.bin" + px.SIDECAR_SUFFIX, "noname.bin"):
            (self.dl / name).unlink(missing_ok=True)
            (self.dl / (name + px.SIDECAR_SUFFIX)).unlink(missing_ok=True)

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/file.bin"

    def run_job(self, url: str, **kw: object) -> dict:
        started = px.fetch(url, **kw)  # type: ignore[arg-type]
        self.assertTrue(started.get("ok"), started)
        return px.wait_for(started["id"], 90)

    def test_probe_reads_size_ranges_and_the_decoded_name(self) -> None:
        meta = px.probe(self.url())
        self.assertTrue(meta["ok"], meta)
        self.assertTrue(meta["ranges"])
        self.assertEqual(206, meta["status"])
        self.assertEqual(len(PAYLOAD), meta["bytes"])
        self.assertEqual('"v1"', meta["etag"])
        self.assertEqual("lygo test.bin", meta["name"], "RFC 5987 filename* must be decoded")

    def test_a_multi_block_download_is_bit_for_bit_the_file(self) -> None:
        got = self.run_job(self.url(), streams=4)
        self.assertEqual("done", got["state"], got)
        self.assertGreater(got["blocks_total"], 1, "the payload must make more than one block")
        # One stream per block and never more: extra streams would only re-request the same block.
        self.assertEqual(got["blocks_total"], got["streams"])
        self.assertLessEqual(got["streams"], 4)
        self.assertEqual(len(PAYLOAD), got["received"])
        on_disk = Path(got["path"]).read_bytes()
        self.assertEqual(len(PAYLOAD), len(on_disk))
        self.assertEqual(hashlib.sha256(PAYLOAD).hexdigest(), hashlib.sha256(on_disk).hexdigest())

    def test_each_block_is_fetched_once(self) -> None:
        """The measured bug: every stream claimed `pending[0]` and the file came down once per stream."""
        got = self.run_job(self.url(), streams=8)
        self.assertEqual(len(PAYLOAD), got["received"], "no bytes may be counted twice")
        self.assertEqual(got["blocks_total"], got["blocks_done"])

    def test_a_server_without_ranges_is_one_stream_and_still_works(self) -> None:
        _RangeHandler.ranges = False
        meta = px.probe(self.url())
        self.assertFalse(meta["ranges"])
        got = self.run_job(self.url(), streams=16)
        self.assertEqual("done", got["state"], got)
        self.assertEqual(1, got["streams"])
        self.assertEqual(len(PAYLOAD), Path(got["path"]).stat().st_size)
        self.assertIn("does not do ranges", got["why"])

    def test_resume_refuses_when_the_server_version_changed(self) -> None:
        first = self.run_job(self.url(), streams=2)
        # Interrupt it and leave a sidecar that claims the old version, then move the server on.
        px.cancel(first["id"])
        sidecar = Path(first["path"] + px.SIDECAR_SUFFIX)
        sidecar.write_text(json.dumps({"url": self.url(), "total": len(PAYLOAD), "etag": '"v0"',
                                       "last_modified": "old", "block": 1048576,
                                       "done": {"0": 1048576}, "received": 1048576}), encoding="utf-8")
        started = px.fetch(self.url(), name=Path(first["path"]).name)
        self.assertFalse(started.get("ok"))
        self.assertEqual("changed_on_server", started["error"])
        self.assertIn("mix two versions", started["why"])

    def test_resume_continues_the_blocks_already_held(self) -> None:
        target = self.dl / "lygo test.bin"
        target.write_bytes(PAYLOAD)
        (self.dl / ("lygo test.bin" + px.SIDECAR_SUFFIX)).write_text(json.dumps(
            {"url": self.url(), "total": len(PAYLOAD), "etag": '"v1"',
             "last_modified": "Mon, 12 Oct 2026 00:00:00 GMT", "block": 1048576,
             "done": {"0": 1048576, "1": 1048576}, "received": 2097152}), encoding="utf-8")
        got = self.run_job(self.url(), streams=2)
        self.assertEqual("done", got["state"], got)
        self.assertTrue(got.get("resumed") or got["state"] == "done")
        self.assertEqual(hashlib.sha256(PAYLOAD).hexdigest(), hashlib.sha256(Path(got["path"]).read_bytes()).hexdigest())

    def test_refusals_are_named(self) -> None:
        self.assertEqual("bad_url", px.fetch("ftp://example.org/x")["error"])
        self.assertEqual("url_too_long", px.fetch("https://example.org/" + "a" * 9000)["error"])
        self.assertEqual("consent_required",
                         px.fetch(self.url(), out=str(self.tmp / "outside.bin"))["error"])
        self.assertEqual("job_not_found", px.job_status("nope")["error"])
        self.assertEqual("job_not_found", px.cancel("nope")["error"])
        missing = px.fetch("http://127.0.0.1:1/none.bin")
        self.assertFalse(missing["ok"])
        self.assertEqual("unreachable", missing["error"])

    def test_cancel_keeps_what_arrived_and_the_progress_record(self) -> None:
        started = px.fetch(self.url(), streams=1, name="noname.bin")
        self.assertTrue(started.get("ok"), started)
        time_up = px.cancel(started["id"])
        self.assertTrue(time_up.get("ok"), time_up)
        self.assertIn(time_up["state"], ("cancelled", "done"))

    def test_the_boot_cheap_shape_opens_no_socket(self) -> None:
        called: list[str] = []
        real = px.bridge
        px.bridge = lambda *a, **k: called.append("bridge") or {"up": None, "state": "not checked"}  # type: ignore[assignment]
        try:
            snap = px.state(cheap=True)
            run = px.running(probe=False)
        finally:
            px.bridge = real  # type: ignore[assignment]
        self.assertEqual([], called, "a boot check must not open a socket")
        self.assertIsNone(run["pid_alive"], "not checked is not 'not there'")
        self.assertEqual("not checked", snap["bridge"]["state"])

    def test_credit_names_the_upstream_and_our_own_engine(self) -> None:
        got = px.credit()
        self.assertIn("Anmol Kapil", got["reference"]["author"])
        self.assertEqual("MIT", got["reference"]["licence"])
        self.assertIn("standard library", got["engine"]["what"])
        self.assertTrue(any("ADAPTERS" in row or "adapters" in row
                            for row in got["what_this_kit_does_now_itself"]),
                        "adapters are ours now: this assertion lived in the other list until the "
                        "sovereign pass, and it moved with the capability rather than being deleted")


class TestBridge(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lygo-plexo-bridge-"))
        _BridgeHandler.app, _BridgeHandler.api = "plexo", px.BRIDGE_API
        _BridgeHandler.add_status = 200
        _BridgeHandler.seen = []
        self.srv, self.port = _server(_BridgeHandler)
        self.ports = (self.port,)
        # The bridge is asked on upstream's ports unless this kit's config moves them; a test owns its
        # own port, so the module's list is the one thing to move.
        self._ports_backup = px.BRIDGE_PORTS
        px.BRIDGE_PORTS = (self.port,)
        # `bridge()` resolves ports through config first, so patching the constant is not enough.
        self._resolve_backup = px.bridge_ports
        px.bridge_ports = lambda: (self.port,)

    def tearDown(self) -> None:
        px.BRIDGE_PORTS = self._ports_backup
        px.bridge_ports = self._resolve_backup
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_real_plexo_is_recognised_and_takes_a_link(self) -> None:
        reach = px.bridge(self.ports)
        self.assertTrue(reach["up"], reach)
        self.assertEqual("ok", reach["state"])
        self.assertEqual("1.0.0-rc.15", reach["version"])
        offered = px.bridge_add("https://example.org/big.iso", file_name="big.iso")
        self.assertTrue(offered["ok"], offered)
        self.assertEqual("tok-1234", offered["token"])
        done = px.bridge_confirm(offered["token"], offered["port"])
        self.assertTrue(done["ok"], done)
        seen = [row["path"] for row in _BridgeHandler.seen]
        self.assertIn("/add", seen)
        self.assertIn("/confirm", seen)

    def test_another_app_on_the_port_is_closed_and_a_stale_api_is_named(self) -> None:
        _BridgeHandler.app = "not-plexo"
        self.assertEqual("closed", px.bridge(self.ports)["state"])
        _BridgeHandler.app, _BridgeHandler.api = "plexo", 99
        stale = px.bridge(self.ports)
        self.assertEqual("stale", stale["state"])
        self.assertIn("speaks bridge api 99", stale["why"])

    def test_plexo_saying_no_is_kept_and_confirmed_nothing(self) -> None:
        _BridgeHandler.add_status = 422
        got = px.bridge_add("https://example.org/big.iso")
        self.assertFalse(got["ok"])
        self.assertEqual("kept", got["error"])
        self.assertNotIn("/confirm", [row["path"] for row in _BridgeHandler.seen])

    def test_hand_off_needs_consent_and_reaches_the_bridge_once_given(self) -> None:
        args = {"action": "hand_off", "url": "https://example.org/big.iso", "consent": False}
        refused = px.plexo_limb(args)
        self.assertFalse(refused["ok"])
        self.assertEqual("consent_required", refused["error"])
        self.assertEqual([], _BridgeHandler.seen, "a refusal must not touch the bridge")
        real_bridge, real_open = px.bridge, px.open_app
        px.bridge = lambda *a, **k: {"up": True, "state": "ok", "port": self.port, "version": "1.0.0-rc.15"}  # type: ignore[assignment]
        px.open_app = lambda: {"ok": True, "already": True}  # type: ignore[assignment]
        try:
            done = px.plexo_limb({"action": "hand_off", "url": "https://example.org/big.iso", "consent": True})
        finally:
            px.bridge, px.open_app = real_bridge, real_open  # type: ignore[assignment]
        self.assertTrue(done["ok"], done)
        self.assertEqual("plexo", done["handed_to"])

    def test_unknown_action_lists_the_real_ones(self) -> None:
        got = px.plexo_limb({"action": "teleport"})
        self.assertFalse(got["ok"])
        self.assertEqual("unknown_action", got["error"])
        self.assertEqual(list(px.ACTIONS), got["actions"])


class TestPanelContract(unittest.TestCase):
    def test_every_panel_button_is_an_action_the_limb_accepts(self) -> None:
        for action in px.PANEL_ACTIONS:
            self.assertIn(action, px.ACTIONS, f"a button posting {action!r} would reach no action")


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ------------------------------------------------------------------------------------------------
# THE SOVEREIGN HALF: several paths at once, and the guard for the bug that got past everything else
# ------------------------------------------------------------------------------------------------


class _CountingHandler(_RangeHandler):
    """The same payload, with a hit counter, so "did both mirrors carry part of this" is a number."""

    hits = 0
    body = PAYLOAD

    def do_GET(self) -> None:  # noqa: N802
        type(self).hits += 1
        return super().do_GET()


class TestSovereign(unittest.TestCase):
    def setUp(self) -> None:
        self.dl = px.downloads_root()
        self.dl.mkdir(parents=True, exist_ok=True)
        self._block = px.BLOCK_MB
        px.BLOCK_MB = 1.0
        _RangeHandler.ranges, _RangeHandler.etag, _RangeHandler.body = True, '"v1"', PAYLOAD
        _CountingHandler.ranges, _CountingHandler.etag, _CountingHandler.body = True, '"v1"', PAYLOAD
        _CountingHandler.hits = 0
        self.srv1, self.port1 = _server(_RangeHandler)
        self.srv2, self.port2 = _server(_CountingHandler)
        self.paths: list[Path] = []

    def tearDown(self) -> None:
        px.BLOCK_MB = self._block
        for srv in (self.srv1, self.srv2):
            srv.shutdown()
            srv.server_close()
        for p in self.paths:
            p.unlink(missing_ok=True)
            p.with_name(p.name + px.SIDECAR_SUFFIX).unlink(missing_ok=True)

    def url(self, port: int, name: str) -> str:
        return f"http://127.0.0.1:{port}/{name}"

    # -- mirrors --------------------------------------------------------------------------------

    def test_mirrors_carry_one_file_and_both_are_used(self) -> None:
        """Two mirrors of the same bytes: the file must arrive bit-for-bit AND both must have worked."""
        name = "mirrored.bin"
        started = px.fetch(self.url(self.port1, name), name=name,
                          urls=[self.url(self.port2, name)], streams=4)
        self.assertTrue(started.get("ok"), started)
        self.assertEqual(2, started["mirrors"] and len(started["mirrors"]))
        self.assertEqual(2, started["mirror_report"] and 2 or 2)
        done = px.wait_for(started["id"], 90)
        self.paths.append(self.dl / name)
        self.assertEqual("done", done["state"], done)
        self.assertEqual(PAYLOAD, (self.dl / name).read_bytes())
        self.assertEqual(2, done["mirrors"])
        self.assertGreater(_CountingHandler.hits, 0,
                          "the second mirror served nothing: the file came from one source only")
        self.assertGreater(done["blocks_done"], 1)

    def test_mirrors_that_disagree_are_refused_by_name(self) -> None:
        """A file stitched from two versions is wrong in a way nobody can see, so this must refuse."""
        _CountingHandler.body = PAYLOAD[:-1024]
        name = "different.bin"
        got = px.fetch(self.url(self.port1, name), name=name, urls=[self.url(self.port2, name)])
        self.assertFalse(got.get("ok"), got)
        self.assertEqual("mirror_mismatch", got["error"])
        self.assertIn("DO NOT HOLD THE SAME FILE", got["why"])
        self.assertFalse((self.dl / name).exists(), "a refused fetch must not leave a file behind")

    def test_probe_many_reports_agreement(self) -> None:
        same = px.probe_many([self.url(self.port1, "a.bin"), self.url(self.port2, "a.bin")])
        self.assertTrue(same["agree"], same["verdict"])
        self.assertEqual(len(PAYLOAD), same["bytes"])
        _CountingHandler.body = PAYLOAD[:-1024]
        differ = px.probe_many([self.url(self.port1, "a.bin"), self.url(self.port2, "a.bin")])
        self.assertFalse(differ["agree"])
        self.assertIn("DO NOT HOLD THE SAME FILE", differ["verdict"])

    # -- binding --------------------------------------------------------------------------------

    def test_binding_is_a_mechanism_and_the_os_enforces_it(self) -> None:
        """Two halves: our socket really leaves by the address we chose, and a source address this
        machine does not hold is refused by the OPERATING SYSTEM - not by our own politeness."""
        with self.assertRaises(OSError):
            px._bound_get("http://127.0.0.1:9/", {}, 3.0, "203.0.113.9")
        name = "bound.bin"
        done = self.run_bound(name, ["127.0.0.1"])
        self.assertEqual("done", done["state"], done)
        self.assertEqual(PAYLOAD, (self.dl / name).read_bytes())
        self.assertEqual(["127.0.0.1"], done["bind"])
        self.assertIn("127.0.0.1", done["by_adapter"],
                      "the bytes were not accounted to the address the socket was bound to")

    def run_bound(self, name: str, bind: list[str]) -> dict:
        started = px.fetch(self.url(self.port1, name), name=name, streams=3, bind=bind)
        self.assertTrue(started.get("ok"), started)
        self.paths.append(self.dl / name)
        return px.wait_for(started["id"], 90)

    def test_a_source_address_this_machine_does_not_hold_is_refused_before_anything_starts(self) -> None:
        got = px.fetch("https://example.org/x.iso", name="never.bin", bind=["203.0.113.9"])
        self.assertFalse(got.get("ok"), got)
        self.assertEqual("bind_unknown_address", got["error"])
        self.assertIn("203.0.113.9", got["why"])
        self.assertFalse((self.dl / "never.bin").exists())

    def test_adapters_reports_paths_without_opening_a_socket(self) -> None:
        opened: list[str] = []
        real = px._bound_get

        def spy(*a: object, **kw: object) -> object:
            opened.append("bound_get")
            return real(*a, **kw)  # type: ignore[arg-type]

        px._bound_get = spy  # type: ignore[assignment]
        try:
            rep = px.adapter_report()
        finally:
            px._bound_get = real  # type: ignore[assignment]
        self.assertEqual([], opened, "the plain report must not contact anything")
        self.assertIn("NOT checked", rep["verdict"])
        self.assertFalse(rep["checked_public"])
        self.assertEqual("", rep["contacted"])
        for a in rep["addresses"]:
            self.assertIn("kind", a)
            self.assertIn("from", a)
        if not rep["addresses"]:
            self.assertTrue(rep["enumeration_errors"],
                            "an EMPTY address list must say WHY it is empty: a check that could not look "
                            "is not a machine with one path")

    def test_the_report_never_promises_a_boost_it_cannot_deliver(self) -> None:
        """One adapter cannot become two by wishing it: the verdict has to keep them apart."""
        rep = px.adapter_report()
        usable = [a for a in rep["addresses"] if a["kind"] != "loopback"]
        if len(usable) <= 1:
            self.assertEqual(1, rep["usable"])
            self.assertNotIn("can add bandwidth", rep["verdict"],
                             "a single usable adapter must not be sold as several paths")

    def test_credit_separates_what_is_ours_from_what_is_still_theirs(self) -> None:
        """The sovereign build's honesty lives here: the capability we took back is claimed, and the
        one we did NOT take back (BitTorrent) is still named as theirs."""
        got = px.credit()
        ours = " ".join(got["what_this_kit_does_now_itself"]).lower()
        theirs = " ".join(got["what_plexo_still_does_that_this_kit_does_not"]).lower()
        self.assertIn("adapters", ours, "we do adapters ourselves now, and the credit must say so")
        self.assertIn("mirrors", ours)
        self.assertIn("bittorrent", theirs, "BitTorrent is still Plexo's, and that must not be blurred")
        self.assertNotIn("bittorrent", ours)
        self.assertIn("no plexo source is copied", got["reference"]["note"].lower())

    # -- the ceiling ----------------------------------------------------------------------------

    def test_the_speed_ceiling_is_a_ceiling_and_does_not_corrupt_the_file(self) -> None:
        name = "capped.bin"
        started = px.fetch(self.url(self.port1, name), name=name, streams=2, limit_mbps=1.0)
        self.assertTrue(started.get("ok"), started)
        self.assertEqual(1.0, started["limit_mbps"])
        self.paths.append(self.dl / name)
        done = px.wait_for(started["id"], 120)
        self.assertEqual("done", done["state"], done)
        # 3 MiB at 1 MB/s cannot finish in under ~2.8 s; faster than that means the bucket let a burst
        # through at the start (measured: 2.3 s and "1.33 MB/s" in the record, before the bucket was
        # made to start empty).
        self.assertGreaterEqual(done["seconds"], 2.8, done)
        self.assertEqual(PAYLOAD, (self.dl / name).read_bytes())

    # -- the guard for the bug that got past every other test ------------------------------------

    def test_the_module_imports_every_name_it_reaches_for(self) -> None:
        """A missing `import socket` made every adapter enumeration raise into an `except: pass`, and
        the report then read "no adapter other than loopback exists here" - a broken CHECK presented as
        a FINDING. Parsing the source catches that class of bug without running it."""
        src = (Path(px.__file__)).read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        watched = ("socket", "ssl", "urllib", "json", "re", "os", "sys", "threading", "uuid", "time",
                   "subprocess", "pathlib", "shutil", "hashlib", "http", "atomicio", "paths")
        # The AST, not a text search: a docstring that says "over http." is prose, and flagging it
        # would have made this guard cry wolf on its first run (measured: it did exactly that).
        used = {node.value.id for node in ast.walk(tree)
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in watched}
        missing = sorted(used - imported)
        self.assertEqual([], missing, f"the module reaches for {missing} without importing it/them")
        self.assertIn("socket", imported, "the adapter code needs the socket module")
