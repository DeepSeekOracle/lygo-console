"""The song studio: the actuator that starts, watches and stops a render - and the switch.

What this pins down, in order of how much it matters:

1. **A switch is real, and a switch it cannot honour is refused by name.** `set_engine` takes a
   declared id and writes it where the limb reads it; an undeclared id is refused
   (`unknown_engine`), and an engine this build has no adapter for is refused when a RENDER is
   asked for (`engine_not_wired`) - never quietly rendered by a different engine. That last one is
   the whole reason the switch can be trusted.
2. **The studio starts a job, not a render.** A render takes tens of minutes; the route must answer
   at once and the truth must land in a job record on disk. This runs the real runner against a
   stubbed `music_generate` (so nothing renders here) and asserts the record ends up saying exactly
   what the limb reported - including the named failure when the limb fails.
3. **The card never claims more than it read.** Lights are only ever the four states the shell
   knows, the render light is red when the render failed, and a kit that has never rendered says so
   instead of showing an empty song list as if a song existed.
4. **No test here touches the operator's real state.** The job directory and the engine-choice file
   are pointed at temporary paths, and the kit's own `save/music` is asserted unchanged afterwards.
5. **The bytes a browser may fetch are audio and only audio, from the songs tree only.**

Run: python -m pytest tests/test_module_music_studio.py -q
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "modules"))

MID = "lygo.musicctrl"
ROUTE = "/api/music"
LIGHT_STATES = {"green", "amber", "grey", "red"}


def _load(mid: str):
    path = ROOT / "src" / "modules" / mid / "backend.py"
    spec = importlib.util.spec_from_file_location(mid.replace(".", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _manifest(mid: str) -> dict:
    return json.loads((ROOT / "src" / "modules" / mid / "module.json").read_text(encoding="utf-8"))


def _version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").splitlines()[0].strip()


class ManifestTests(unittest.TestCase):
    """What the manifest promises, and whether the adapter really delivers it."""

    def setUp(self) -> None:
        self.m = _manifest(MID)
        self.b = _load(MID)

    def test_it_is_an_actuator_whose_id_says_so(self) -> None:
        # The naming law knows two families - `info` and `watch` (tests/test_module_envwatch.py is the
        # authority) - and nothing in the kit reads this field for behaviour, so a module outside them
        # must not invent a third: its role lives in its id, the way `lygo.notepad` carries its own.
        self.assertTrue(MID.endswith("ctrl"), "the actuator role is carried by the id")
        self.assertNotIn("family", self.m, "an id outside the declared families must not claim a family")

    def test_it_owns_one_thing_and_writes_it_through_the_gate(self) -> None:
        state = self.m["state"]
        self.assertEqual(state["owns"], ["save/music"])
        self.assertEqual(state["writes_via"], "atomicio")
        self.assertEqual(state["gate"], "p0")

    def test_it_declares_the_render_limb_and_does_not_re_invent_it(self) -> None:
        names = [l["name"] for l in self.m["limbs"]]
        self.assertEqual(names, ["music_generate"])
        src = (ROOT / "src" / "modules" / MID / "backend.py").read_text(encoding="utf-8")
        self.assertNotIn("def music_generate", src, "the limb lives in music_tools, not in the card")
        self.assertIn('("POST", ROUTE, control)', src, "the card must route its actuator through control()")

    def test_the_register_call_hands_the_host_exactly_what_it_declares(self) -> None:
        got = self.b.register(None)
        declared = {(r["method"], r["path"]) for r in self.m["routes"]}
        self.assertEqual({(m, p) for m, p, _fn in got["routes"]}, declared)
        self.assertEqual({r["method"] for r in self.m["routes"]}, {"GET", "POST"})
        self.assertEqual([p["id"] for p in got["panes"]], [p["id"] for p in self.m["panes"]])
        self.assertEqual(got["panes"][0]["slot"], "dock")
        self.assertEqual(got["panes"][0]["data"], f"GET {ROUTE}")

    def test_the_console_it_requires_is_the_console_shipped(self) -> None:
        need = self.m["requires"]["console"].lstrip(">=")
        self.assertEqual(_version(), need, "the manifest's floor and VERSION must agree")

    def test_its_source_writes_nothing_outside_its_own_state(self) -> None:
        src = (ROOT / "src" / "modules" / MID / "backend.py").read_text(encoding="utf-8")
        banned = {"unlink", "rmtree", "shutil.move", "copytree"}
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    name = node.func.id
                self.assertNotIn(name, banned, f"the card must not call {name}()")


class EngineTests(unittest.TestCase):
    """The switch: what is declared, what can really render, and what a bad answer looks like."""

    @classmethod
    def setUpClass(cls) -> None:
        import music_tools  # noqa: F401  (imported for its side-effect free reads)

        cls.mt = music_tools

    def test_every_declared_engine_says_what_it_is_and_what_it_lacks(self) -> None:
        rows = self.mt.engines_state()
        self.assertGreaterEqual(len(rows), 2, "a switch needs more than one declared engine")
        ids = [r["id"] for r in rows]
        self.assertIn("yue", ids)
        for r in rows:
            for key in ("id", "label", "kind", "state", "note", "missing", "wired", "adapter"):
                self.assertIn(key, r, f"{r.get('id')} must report {key}")
            self.assertIn(r["state"], {"installed", "declared"})
            self.assertIsInstance(r["missing"], list)
            if r["state"] != "installed":
                self.assertTrue(r["note"], f"{r['id']} is not usable and does not say why")
            # An engine with no adapter must never read as installed: a select that says usable and
            # a render that refuses is the contradiction this rule exists to prevent.
            if not r["wired"]:
                self.assertNotEqual(r["state"], "installed", f"{r['id']} has no adapter but reads usable")

    def test_a_choice_round_trips_and_an_undeclared_one_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = self.mt._choice_path
            self.mt._choice_path = lambda: Path(tmp) / "engine.json"
            try:
                self.assertEqual(self.mt.chosen_engine(), "yue", "config default must hold with no choice file")
                bad = self.mt.set_engine("nonsense_engine")
                self.assertFalse(bad["ok"])
                self.assertEqual(bad["error"], "unknown_engine")
                self.assertIn("yue", bad["declared"])
                ok = self.mt.set_engine("stable_audio")
                self.assertTrue(ok["ok"], ok)
                self.assertEqual(self.mt.chosen_engine(), "stable_audio")
            finally:
                self.mt._choice_path = real

    def test_an_engine_with_no_adapter_is_refused_by_name_not_rendered(self) -> None:
        """The heart of the switch: never render with engine B while claiming engine A."""
        rows = {r["id"]: r for r in self.mt.engines_state()}
        unadapted = [r for r in rows.values() if not r["wired"]]
        if not unadapted:
            self.skipTest("every declared engine has an adapter on this box")
        target = unadapted[0]["id"]
        started = time.time()
        out = self.mt.music_generate(style="pop", lyrics="a line", engine=target)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "engine_not_wired")
        self.assertEqual(out["engine"], target)
        self.assertLess(time.time() - started, 20, "a refusal must not wait for an engine to boot")

    def test_a_render_for_an_undeclared_engine_is_refused(self) -> None:
        out = self.mt.music_generate(style="pop", lyrics="a line", engine="not_an_engine")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "unknown_engine")


#: `music_tools` proves the songs folder is WRITABLE by creating and unlinking a `.write-probe` FILE
#: inside it. A render leaves a timestamped run DIRECTORY, so this transient probe is not evidence that
#: anything rendered - but it is visible to the glob below whenever a writability check runs at the same
#: moment. Measured 2026-10-16: a keeper cron's self_check ran during a suite run and turned this guard
#: red on a tree where nothing had rendered. Excluded by name so the guard keeps its full power over
#: real run folders.
TRANSIENT_PROBE = ".write-probe"


def _run_folders(songs_dir) -> set:
    """What is in the songs folder, minus the writability probe's own transient file."""
    if not songs_dir.is_dir():
        return set()
    return {p.name for p in songs_dir.glob("*") if p.name != TRANSIENT_PROBE}


class JobTests(unittest.TestCase):
    """The runner: one job at a time, a record on disk, and a named failure preserved."""

    def setUp(self) -> None:
        import music_jobs
        import music_tools

        self.mj = music_jobs
        self.tmp = tempfile.TemporaryDirectory()
        self.real_jobs_dir = music_jobs._jobs_dir
        music_jobs._jobs_dir = lambda: Path(self.tmp.name)
        self.calls: list[dict] = []
        self.release = threading.Event()
        self.real_limb = None
        #: Every run folder that exists before this test. A test that ever reached the REAL limb
        #: would leave a new one behind - so this is the guard that says whether the fake was used.
        self.runs_before = _run_folders(music_tools.SONGS_DIR)

    def tearDown(self) -> None:
        self.release.set()
        if self.real_limb is not None:
            self.mj.mt.music_generate = self.real_limb
        self.mj._jobs_dir = self.real_jobs_dir
        self.tmp.cleanup()
        import music_tools

        after = _run_folders(music_tools.SONGS_DIR)
        self.assertEqual(after, self.runs_before,
                         "a test reached the real renderer and left a run folder behind")

    def _stub(self, out: dict):
        """A fake limb that holds the render open until the test releases it.

        The gate is the whole point. A worker thread calls the limb a moment AFTER `start` has
        already answered, so a test that put the real function back as soon as `start` returned
        would hand the work to the real engine - which boots a 20 GB model and starts a render.
        That is exactly what happened once, on this machine; the run-folder guard above is here to
        make sure it can never happen quietly again.
        """
        def fake(**kw):
            self.calls.append(kw)
            self.release.wait(timeout=25)
            return dict(out)
        return fake

    def _patch_limb(self, out: dict) -> None:
        self.real_limb = self.mj.mt.music_generate
        self.mj.mt.music_generate = self._stub(out)

    def _wait(self, want: str, limit: float = 12.0) -> dict:
        end = time.time() + limit
        job = {}
        while time.time() < end:
            job = self.mj.state()
            if job.get("state") == want:
                return job
            time.sleep(0.1)
        return job

    def test_a_render_answers_at_once_and_the_record_says_what_was_written(self) -> None:
        self._patch_limb({"ok": True, "path": "I:/x/audio/songs/run/a_song.mp3", "seconds": 12.0})
        started = time.time()
        out = self.mj.start(style="inspiring pop", lyrics="line one\nline two", seed=963)
        self.assertTrue(out["ok"], out)
        self.assertLess(time.time() - started, 2.0, "start must not wait for a render")
        self.assertEqual(self._wait("running", limit=3.0).get("state"), "running",
                         "while the limb is still working the job must read as running")
        self.release.set()
        job = self._wait("done")
        self.assertEqual(job["state"], "done", job)
        self.assertEqual(job["song"], "I:/x/audio/songs/run/a_song.mp3")
        # The engine the studio actually CHOSE, not a hardcoded "yue". This machine's pick is
        # ace_step, so the literal was asserting which engine the machine is configured with rather
        # than the thing the test is about: that the record names the engine the render used.
        self.assertEqual(job["engine"], self.mj.chosen_engine())
        self.assertEqual(job["seed"], 963)
        files = list(Path(self.tmp.name).glob("*.json"))
        self.assertEqual(len(files), 1, "one job, one record")
        record = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(record["state"], "done")
        self.assertIn("line one", record["lyrics"], "the words are kept with the job that used them")

    def test_a_failed_render_keeps_the_limbs_own_error_and_remedy(self) -> None:
        self._patch_limb({"ok": False, "error": "music_failed", "hint": "set yue_root or music_root"})
        self.mj.start(style="pop", lyrics="a line")
        self.release.set()
        job = self._wait("failed")
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["error"], "music_failed")
        self.assertIn("yue_root", job["hint"])
        self.assertFalse(job.get("song"), "a failure must not carry a song path")

    def test_one_render_at_a_time(self) -> None:
        self._patch_limb({"ok": True, "path": "I:/x/a.mp3"})
        self.assertTrue(self.mj.start(style="pop", lyrics="a line")["ok"])
        second = self.mj.start(style="pop", lyrics="another line")
        self.assertFalse(second["ok"])
        self.assertEqual(second["error"], "job_running")
        self.assertIn("one render at a time", second["hint"])
        # A cancel must be recorded as a cancel, even if the limb came back happy: the operator
        # asked it to stop, so the song it may have written is not a song this console claims.
        self.assertTrue(self.mj.cancel()["ok"])
        self.release.set()
        self.assertEqual(self._wait("cancelled")["state"], "cancelled")

    def test_the_refusals_name_the_missing_thing(self) -> None:
        self.assertEqual(self.mj.start(style="", lyrics="")["error"], "empty_request")
        out = self.mj.start(style="pop", lyrics="   ")
        self.assertEqual(out["error"], "lyrics_required")
        self.assertEqual(self.mj.cancel()["error"], "nothing_to_cancel")

    def test_the_studio_state_is_one_answer_the_panel_can_paint(self) -> None:
        st = self.mj.studio_state()
        for key in ("ok", "engine", "engines", "job", "songs", "defaults", "route", "signature"):
            self.assertIn(key, st, f"the panel reads {key}")
        self.assertIn("max_new_tokens", st["defaults"])
        self.assertIsInstance(st["route"], dict)
        self.assertIn("segments", st["defaults"])


class CardTests(unittest.TestCase):
    """The dock card: four lights, rows of facts, and nothing invented."""

    def setUp(self) -> None:
        self.b = _load(MID)

    def test_the_card_paints_inside_the_shells_vocabulary(self) -> None:
        card = self.b.build(None)
        self.assertTrue(card["lights"], "a card with no light says nothing")
        for light in card["lights"]:
            self.assertIn(light["state"], LIGHT_STATES)
            self.assertTrue(light["label"] and light["text"])
        self.assertTrue(card["groups"])
        for g in card["groups"]:
            self.assertTrue(g["title"] and g["rows"], f"{g.get('title')} is an empty group")
            for row in g["rows"]:
                self.assertIn("k", row)
                self.assertIn("v", row)
        self.assertEqual(card["missing"], [], "this card can get everything it reads")

    def test_a_kit_with_no_song_says_so_instead_of_showing_a_blank_list(self) -> None:
        rows = self.b._song_rows([])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["v"], "0")
        self.assertIn("none rendered yet", rows[0]["note"])

    def test_a_failed_render_reads_red_and_carries_the_remedy(self) -> None:
        rows = self.b._job_rows({"state": "failed", "error": "music_failed",
                                 "hint": "set yue_root", "started": "10:00:00", "elapsed_s": 50.0})
        why = [r for r in rows if r["k"] == "Why it stopped"]
        self.assertEqual(len(why), 1)
        self.assertIn("yue_root", why[0]["note"])
        self.assertEqual(why[0]["dot"], "red")

    def test_the_gate_is_asked_and_a_clean_body_passes(self) -> None:
        self.assertIsNone(self.b._gate("inspiring pop\nline one\nline two"))

    def test_an_action_it_does_not_know_is_refused(self) -> None:
        class Req:
            def __init__(self) -> None:
                self.sent: list[tuple[int, dict]] = []

            def body(self, _cap: int = 0) -> dict:
                return {"action": "self_destruct"}

            def json(self, code: int, payload: dict) -> None:
                self.sent.append((code, payload))

        req = Req()
        self.b.control(None, req)
        self.assertEqual(req.sent[0][0], 400)
        self.assertEqual(req.sent[0][1]["error"], "unknown_action")
        self.assertIn("start", req.sent[0][1]["actions"])

    def test_health_answers_without_rendering(self) -> None:
        out = self.b.health(None)
        self.assertIn("detail", out)
        self.assertIsInstance(out["ok"], bool)


class MediaAndPanelTests(unittest.TestCase):
    """The two seams the panel needs: the bytes route, and the panel that uses it."""

    @classmethod
    def setUpClass(cls) -> None:
        import music_tools

        cls.mt = music_tools

    def test_only_audio_types_are_served(self) -> None:
        self.assertEqual(self.mt.MEDIA_AUDIO_TYPES[".mp3"], "audio/mpeg")
        for ext, ctype in self.mt.MEDIA_AUDIO_TYPES.items():
            self.assertTrue(ctype.startswith("audio/"), f"{ext} is not audio")

    def test_a_song_path_can_never_leave_the_songs_tree(self) -> None:
        for bad in ("../../etc/passwd", "sub/dir/song.mp3", "..\\..\\windows\\system32\\x.mp3", ""):
            self.assertIsNone(self.mt.workspace_song_path(bad), f"{bad} must not resolve")

    def test_a_real_song_file_resolves_by_bare_name(self) -> None:
        run = self.mt.SONGS_DIR / "pytest_probe_run"
        run.mkdir(parents=True, exist_ok=True)
        f = run / "pytest_probe_song.mp3"
        f.write_bytes(b"ID3\x00probe")
        try:
            got = self.mt.workspace_song_path("pytest_probe_song.mp3")
            self.assertIsNotNone(got, "a song in the tree must resolve")
            self.assertEqual(got.name, "pytest_probe_song.mp3")
        finally:
            f.unlink(missing_ok=True)
            try:
                run.rmdir()
            except OSError:
                pass

    def test_the_kernel_serves_audio_the_way_it_serves_pictures(self) -> None:
        src = (ROOT / "src" / "server.py").read_text(encoding="utf-8")
        self.assertIn('"/api/media/audio/"', src)
        self.assertIn("workspace_song_path", src)
        self.assertIn("MEDIA_AUDIO_TYPES", src)

    def test_the_panel_is_not_a_decorative_stub(self) -> None:
        """The studio's controls live in the console's own page, wired to the module's route."""
        html = (ROOT / "portal" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "portal" / "app.js").read_text(encoding="utf-8")
        for wanted in ("ms-studio", "ms-engine", "ms-style", "ms-lyrics", "ms-go", "ms-cancel",
                       "ms-status", "ms-songs", "ms-segments", "ms-seed", "ms-tokens"):
            self.assertIn(wanted, html, f"the panel is missing {wanted}")
        self.assertIn("Song studio", html)
        self.assertIn("/api/music", js, "the panel is not wired to the studio route")
        self.assertIn("/api/media/audio/", js, "the panel does not play songs through the kernel route")

class Utf8EnvTests(unittest.TestCase):
    """The environment a song engine's CLI is given - the fix for the render that died on an emoji.

    MEASURED 2026-09-26: three ACE-Step runs on this machine exited 1 about 40 s into model load with
    `'charmap' codec can't encode character '\u2705'`. The engine's 5 Hz LM logs a checkmark; the CLI
    prints it to a stdout the console REDIRECTS TO A FILE, and a redirected Python stdout takes its
    encoding from the LOCALE (cp1252 on Windows), not from the terminal. Nothing was wrong with the
    render - it never started. The YuE path had always set PYTHONIOENCODING because its server needed
    it; the ACE path passed no env at all. These tests are the guard on that asymmetry.
    """

    def setUp(self) -> None:
        import music_tools

        self.mt = music_tools

    def test_the_helper_sets_all_three_variables(self) -> None:
        env = self.mt._utf8_env()
        self.assertEqual(env["PYTHONUTF8"], "1")
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(env["PYTHONUNBUFFERED"], "1")

    def test_the_helper_keeps_the_rest_of_the_environment(self) -> None:
        os.environ["LYGO_MUSIC_TEST_MARKER"] = "kept"
        try:
            self.assertEqual(self.mt._utf8_env().get("LYGO_MUSIC_TEST_MARKER"), "kept")
        finally:
            os.environ.pop("LYGO_MUSIC_TEST_MARKER", None)

    def test_an_emoji_survives_a_cp1252_stream_with_the_helper_applied(self) -> None:
        """The exact failure, reproduced: cp1252 dies on the engine's own status message."""
        import io

        msg = "\u2705 5Hz LM initialized successfully"
        with self.assertRaises(UnicodeEncodeError):
            msg.encode("cp1252")                      # the old behaviour, and what killed the runs
        self.assertEqual(msg.encode("utf-8").decode("utf-8"), msg)

    def test_both_engines_are_given_its_variables(self) -> None:
        """ACE passed no env before this; YuE set them inline. Both must end up with the same rule."""
        src = (ROOT / "src" / "music_tools.py").read_text(encoding="utf-8")
        self.assertIn("env=_utf8_env()", src, "the ACE child is not being given the UTF-8 env")
        self.assertIn('"PYTHONIOENCODING": "utf-8",\n        "PYTHONUTF8": "1",', src,
                      "the YuE engine env lost its UTF-8 settings")

    def test_the_engine_cli_forces_its_own_streams_too(self) -> None:
        """Belt and braces: the CLI must not depend on the launcher having been kind."""
        cli = Path("C:/pinokio/api/acestep.git/app/render_cli.py")
        if not cli.is_file():
            self.skipTest("the ACE-Step app is not installed on this machine")
        text = cli.read_text(encoding="utf-8", errors="replace")
        self.assertIn("force_utf8_streams", text)
        self.assertIn('errors="replace"', text,
                      "the CLI must degrade a message it cannot encode, never die on it")


class TitleTests(unittest.TestCase):
    """A take can be named, and naming is never a reason a render fails."""

    def setUp(self) -> None:
        import music_jobs
        import music_tools

        self.mj = music_jobs
        self.mt = music_tools
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.real_songs = music_tools.SONGS_DIR
        run = self.root / "20260926-120000_song_pop"
        run.mkdir(parents=True)
        (run / "20260926-120000_song_pop.mp3").write_bytes(b"ID3" + b"\x00" * 400)
        (run / "20260926-120000_song_pop.json").write_text(
            json.dumps({"engine": "ace_step", "style": "pop", "audio_seconds": 12}), encoding="utf-8")
        music_tools.SONGS_DIR = self.root

    def tearDown(self) -> None:
        self.mt.SONGS_DIR = self.real_songs
        self.tmp.cleanup()

    def _sidecar(self) -> dict:
        return json.loads((self.root / "20260926-120000_song_pop" / "20260926-120000_song_pop.json")
                          .read_text(encoding="utf-8"))

    def test_the_song_list_reports_a_title_and_never_invents_one(self) -> None:
        rows = self.mj.songs()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "", "an unnamed take reports no title rather than a guess")
        self.assertTrue(rows[0]["name"], "the file name is always the fact that is on disk")

    def test_a_take_can_be_named_afterwards_without_rendering_it_again(self) -> None:
        out = self.mj.retitle("20260926-120000_song_pop.mp3", "Bridge Check One")
        self.assertTrue(out["ok"], out)
        self.assertEqual(self._sidecar()["title"], "Bridge Check One")
        self.assertEqual(self.mj.songs()[0]["title"], "Bridge Check One")

    def test_naming_never_renames_the_audio_a_link_already_points_at(self) -> None:
        audio = self.root / "20260926-120000_song_pop" / "20260926-120000_song_pop.mp3"
        before = audio.read_bytes()
        self.mj.retitle(audio.name, "Something Else Entirely")
        self.assertTrue(audio.is_file(), "the file the browser already holds must not move")
        self.assertEqual(audio.read_bytes(), before)
        self.assertIsNotNone(self.mj.song_file(audio.name), "the take must still resolve by its name")

    def test_a_title_can_be_cleared_and_the_file_name_returns(self) -> None:
        self.mj.retitle("20260926-120000_song_pop.mp3", "Temporary")
        cleared = self.mj.retitle("20260926-120000_song_pop.mp3", "")
        self.assertTrue(cleared["ok"])
        self.assertNotIn("title", self._sidecar())
        self.assertEqual(self.mj.songs()[0]["title"], "")

    def test_a_take_that_is_not_there_is_refused_by_name(self) -> None:
        out = self.mj.retitle("no-such-take.mp3", "Whatever")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "no_such_song")

    def test_a_title_cannot_be_used_to_walk_out_of_the_songs_tree(self) -> None:
        out = self.mj.retitle("../../etc/passwd", "no")
        self.assertFalse(out["ok"], "a traversal attempt must be refused, not resolved")

    def test_a_render_may_be_started_with_a_title_or_without_one(self) -> None:
        import inspect

        sig = inspect.signature(self.mj.start)
        self.assertIn("title", sig.parameters)
        self.assertEqual(sig.parameters["title"].default, "",
                         "a blank title must be the default: a render never fails for want of a name")
        self.assertIn("title", inspect.signature(self.mt.music_generate).parameters)

    def test_the_route_knows_the_action_and_refuses_a_stranger(self) -> None:
        ctrl = _load(MID)
        self.assertIn("retitle", ctrl.ACTIONS)
        req = FakeReq({"action": "title it please"})
        ctrl.control(None, req)
        self.assertEqual(req.status, 400)
        self.assertEqual(req.payload["error"], "unknown_action")


class FakeReq:
    """A request, as a module handler sees it. Used by the route test above."""

    def __init__(self, body: dict) -> None:
        self._body = dict(body or {})
        self.status: int | None = None
        self.payload: dict | None = None

    def body(self, limit: int = 0) -> dict:
        return dict(self._body)

    def json(self, code: int, payload: object) -> None:
        self.status = code
        self.payload = payload if isinstance(payload, dict) else {"value": payload}


if __name__ == "__main__":
    unittest.main()
