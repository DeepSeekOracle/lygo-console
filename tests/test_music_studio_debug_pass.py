"""Regression tests from the studio's first debug pass: four bugs, each pinned by a real assertion.

Every test here failed before its fix, and each one names the mechanism rather than the symptom:

1. **A song's name is percent-encoded on the wire.** `my song (final).mp3` and `café-sång.mp3`
   were both 404 - the file was on disk, the route never decoded the name. A song is a file a human
   may drop in the folder, so this is the difference between "the studio plays it" and "the studio
   lists a song it cannot play". Decoding happens at the HTTP layer; the traversal refusal is
   asserted AFTER decoding, because that is the order that matters.
2. **One poll read the card six times.** The route handler, the strip card and the module's own
   health each asked for the same reading, and each reading is an nvidia-smi probe: six subprocess
   spawns every three seconds, forever. The reading is now held for a moment (`READING_TTL_S`), so a
   poll costs one probe - and this test counts probes, not milliseconds.
3. **A refused engine switch answered HTTP 200.** The panel reads the status code; a refusal that
   arrives as a success invites a UI to show a switch that never happened.
4. **A stop that landed while the engine was still booting killed nothing.** There was no pid yet,
   so the record said `cancelled` while the engine went on to hold the card and render for an hour.
   The limb now reads the stop flag before it posts any work.
5. **The status line contradicted the playlist.** Found by loading the live console in a browser after
   a full song landed: the studio's status said "no song rendered yet" directly above two finished
   songs with working players. The wording reported on the absence of JOB RECORDS while claiming
   something about SONGS - the limb, a script or an earlier console can write a song here with no
   studio record at all. A job record is not a song, and the status now counts the songs.

Run: python -m pytest tests/test_music_studio_debug_pass.py -q
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "modules"))

import music_tools as mt  # noqa: E402


class _FakeProc:
    """An engine process that does not exist. Nothing in these tests may touch a real process."""

    def __init__(self) -> None:
        self.pid = 2147483646          # an impossible pid, so even a stray kill hits nothing
        self.terminated = False
        self.killed = False

    def poll(self):
        return 0 if (self.terminated or self.killed) else None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout=None):  # noqa: ANN001
        return 0


class LiveRouteTests(unittest.TestCase):
    """The real routes, over real HTTP, with the songs tree pointed at a temp folder."""

    @classmethod
    def setUpClass(cls) -> None:
        import server

        server.AUTH_REQUIRED = False
        server.TOKEN = ""
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True, name="lygo-test-console").start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.real_songs, self.real_choice = mt.SONGS_DIR, mt._choice_path
        mt.SONGS_DIR = Path(self.tmp.name)
        mt._choice_path = lambda: Path(self.tmp.name) / "engine.json"
        mt.forget_reading()

    def tearDown(self) -> None:
        mt.SONGS_DIR, mt._choice_path = self.real_songs, self.real_choice
        mt.forget_reading()
        self.tmp.cleanup()

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _get(self, path: str) -> tuple[int, bytes, str]:
        try:
            with urllib.request.urlopen(self._url(path), timeout=60) as r:
                return r.status, r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers.get("Content-Type", "")

    def _post(self, payload: dict) -> tuple[int, dict]:
        req = urllib.request.Request(self._url("/api/music"), data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                return e.code, json.loads(body or b"{}")
            except ValueError:
                return e.code, {"raw": body[:200].decode("utf-8", "replace")}

    def _place(self, name: str) -> Path:
        run = Path(self.tmp.name) / "20260101-000000-debug-pass"
        run.mkdir(parents=True, exist_ok=True)
        fp = run / name
        fp.write_bytes(b"ID3\x00debug-pass")
        return fp

    # 1. the name on the wire ---------------------------------------------------------------------
    def test_a_song_name_a_human_would_type_is_served(self) -> None:
        for name in ("my song (final).mp3", "café-sång.mp3", "plain_probe.mp3"):
            self._place(name)
            code, body, ctype = self._get("/api/media/audio/" + urllib.parse.quote(name))
            self.assertEqual(code, 200, f"{name!r} is on disk and must be served, got {code}")
            self.assertTrue(body.startswith(b"ID3"), name)
            self.assertEqual(ctype, "audio/mpeg", name)

    def test_a_decoded_traversal_is_still_refused(self) -> None:
        """Decoding first is exactly why this must be asserted: the name is refused, not joined."""
        for name in ("../../secret.mp3", "..%2f..%2fsecret.mp3", "%2e%2e%2f%2e%2e%2fsystem32"):
            code, _body, _ct = self._get("/api/media/audio/" + name)
            self.assertEqual(code, 404, f"a name carrying a path must be refused: {name!r}")
        self.assertIsNone(mt.workspace_song_path("../../secret.mp3"))

    # 2. what one poll costs ----------------------------------------------------------------------
    def test_one_poll_reads_the_card_once(self) -> None:
        probes = []
        real = mt.route_for_a_song
        mt.route_for_a_song = lambda *a, **k: (probes.append(1), real(*a, **k))[1]
        try:
            mt.forget_reading()
            code, body, _ct = self._get("/api/music")
            self.assertEqual(code, 200)
            self.assertIn("card", json.loads(body))
            first = len(probes)
            self._get("/api/music")
            self.assertEqual(len(probes), first, "a second poll inside the hold must not probe again")
            self.assertLessEqual(first, 1, f"one poll must cost one card probe, cost {first}")
            mt.forget_reading()
            mt.music_status()
            self.assertEqual(len(probes), first + 1, "a dropped reading is read again, on demand")
        finally:
            mt.route_for_a_song = real
            mt.forget_reading()

    def test_the_reading_held_is_a_copy_not_the_held_object(self) -> None:
        first = mt.music_status()
        first["ready"] = "a caller must not be able to edit the held reading"
        self.assertNotEqual(mt.music_status().get("ready"), "a caller must not be able to edit the held reading")

    # 3. the status code of a refusal -------------------------------------------------------------
    def test_a_refused_switch_answers_with_a_refusal_status(self) -> None:
        code, out = self._post({"action": "engine", "engine": "not_an_engine"})
        self.assertGreaterEqual(code, 400, f"a refusal answered {code}, which a panel reads as success")
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error"), "unknown_engine")

    def test_an_accepted_switch_still_answers_200(self) -> None:
        chosen = mt.chosen_engine()
        code, out = self._post({"action": "engine", "engine": chosen})
        self.assertEqual(code, 200, out)
        self.assertTrue(out.get("ok"), out)


class StopDuringBootTests(unittest.TestCase):
    """The stop that used to kill nothing: no pid existed yet, and the render went on regardless."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.real_songs = mt.SONGS_DIR
        mt.SONGS_DIR = Path(self.tmp.name)
        self.proc = _FakeProc()
        self.stopped: list = []
        self.real_start, self.real_post, self.real_stop = mt._start_server, mt._post_job, mt._stop_server
        mt._start_server = lambda *a, **k: (self.proc, "")
        mt._post_job = self._never_post
        mt._stop_server = lambda p: self.stopped.append(p)

    def tearDown(self) -> None:
        mt._start_server, mt._post_job, mt._stop_server = self.real_start, self.real_post, self.real_stop
        mt.SONGS_DIR = self.real_songs
        mt.forget_stopped("job-stop-during-boot")
        mt.forget_reading()
        self.tmp.cleanup()

    def _never_post(self, *a, **k):
        raise AssertionError("a stopped render must never post work to the engine")

    def test_a_stop_while_the_engine_is_starting_stops_the_render(self) -> None:
        mt.mark_stopped("job-stop-during-boot")
        # engine="yue" NAMED, not `mt.chosen_engine()`. This test is about the server lifecycle - it
        # stubs `_start_server`/`_post_job`/`_stop_server`, which only YuE uses. ACE-Step is a
        # one-shot CLI with no server to start or stop, so once this machine's chosen engine became
        # ace_step the stubs missed every call, a REAL engine subprocess started, and the test hung
        # instead of failing. A named engine is what these stubs describe.
        out = mt.music_generate(style="pop", lyrics="a line to sing", engine="yue",
                                job_id="job-stop-during-boot")
        self.assertFalse(out.get("ok"), out)
        self.assertEqual(out.get("error"), "cancelled", out)
        self.assertEqual(self.stopped, [self.proc], "the engine must be shut down again, not left holding the card")
        # `_stop_server` is a recorder here (nothing in this file may touch a real process), so what
        # is asserted is that the shutdown path was asked for - exactly once - and that the pid
        # registry was cleared. The real kill is covered by the live cancel path, not by a fake pid.
        self.assertEqual(len(self.stopped), 1, "one engine, stopped once")
        self.assertEqual(mt.live_pid("job-stop-during-boot"), 0, "the pid registry is left clean")
        self.assertIn("never sang", str(out.get("hint") or ""))

    def test_the_same_call_without_a_stop_does_post_work(self) -> None:
        """The control: without the flag the very same path reaches the engine."""
        posted: list = []
        mt._post_job = lambda *a, **k: (posted.append(a), {"ok": True, "detail": {}})[1]
        try:
            mt.music_generate(style="pop", lyrics="a line to sing", engine="yue",
                              job_id="job-not-stopped")
        except AssertionError:
            self.fail("nothing stopped this render, so it had to reach the engine")
        self.assertEqual(len(posted), 1, "an unstoppped render posts its job exactly once")

    def test_a_stop_belongs_to_one_render_and_is_not_inherited(self) -> None:
        mt.mark_stopped("job-old")
        self.assertTrue(mt.is_stopped("job-old"))
        mt.forget_stopped("job-old")
        self.assertFalse(mt.is_stopped("job-old"), "a cleared stop must not stop a later render")


class NoJobRecordIsNotNoSong(unittest.TestCase):
    """A song with no job record is a real state, and the status line has to say so.

    Found by loading the live console in a browser: the studio's status read "no song rendered yet"
    immediately above a playlist holding two finished songs with working players. The wording was
    reporting on the absence of JOB RECORDS while claiming something about SONGS - the pane
    contradicting its own list, which is the class of lie this build exists to remove.
    """

    def _state_with(self, songs_):  # noqa: ANN001
        import music_jobs as mj

        real_songs, real_running, real_jobs = mj.songs, mj._running_job, mj._jobs_dir
        try:
            mj._running_job = lambda: None
            mj.songs = lambda: songs_
            with tempfile.TemporaryDirectory() as tmp:
                empty = Path(tmp)
                mj._jobs_dir = lambda: empty
                try:
                    return mj.state()
                finally:
                    mj._jobs_dir = real_jobs
        finally:
            mj.songs, mj._running_job = real_songs, real_running

    def test_songs_on_disk_with_no_job_record_are_reported_as_such(self) -> None:
        out = self._state_with([{"name": "made-elsewhere.mp3", "seconds": 180.0},
                                {"name": "made-earlier.mp3", "seconds": 15.0}])
        self.assertEqual(out["state"], "none")
        self.assertNotIn("no song rendered yet", out["status"],
                         "two songs are on disk: the status may not say none was rendered")
        self.assertIn("2 songs on disk", out["status"])

    def test_one_song_is_not_reported_as_two(self) -> None:
        out = self._state_with([{"name": "only.mp3", "seconds": 180.0}])
        self.assertIn("1 song on disk", out["status"])

    def test_with_no_songs_at_all_the_old_sentence_is_true(self) -> None:
        out = self._state_with([])
        self.assertEqual(out["state"], "none")
        self.assertIn("no song rendered yet", out["status"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
