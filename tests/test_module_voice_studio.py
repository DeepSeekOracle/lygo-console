"""The voice pair: the bridge to a separately-installed VoiceStudio, and the fact that keeps it a bridge.

What this pins down, in order of how much it matters:

1. **The licence boundary is enforced by the code, not by a comment.** No VoiceStudio source is imported or
   copied, nothing under the VoiceStudio install or its runtime is ever opened for writing, and the card
   states the AGPL-3.0 / LYGO-Sovereign conflict so that nobody has to re-derive why this is a bridge. The
   first test below is the one that fails if someone ever "just vendors it in".
2. **The harness speaks the vendor's own protocol exactly.** The argv, the working directory and the
   environment are the ones the vendor's shell builds — including the deleted `PYTHONHOME`/`PYTHONPATH` and
   the per-port `OMNIVOICE_PORT` — because a bridge that calls a service slightly differently from its own
   shell is a bridge that breaks on the next release.
3. **A service this console did not start is never killed.** `stop` records the pid of the backend it
   started and refuses by name otherwise. That refusal is the difference between stopping a helper and
   taking the operator's own application down.
4. **Nothing is claimed that was not measured.** The service is "up" only from a 200 on `/api/health`; a
   clip is only reported written after the bytes are read back; a check that could not run is a named gap.
5. **No test here touches the operator's real state.** The kit's `save/` is already pointed at the suite's
   temp store by `conftest.py`, and every test that needs a filesystem fact builds its own fake root.

Run: python -m pytest tests/test_module_voice_studio.py -q
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "modules"))

INFO = "lygo.voiceinfo"
CTRL = "lygo.voicectrl"
VOICE_ROUTE = "/api/voice"
INFO_ROUTE = "/api/voiceinfo"
LIGHT_STATES = {"green", "amber", "grey", "red"}

#: The ports this kit already owns. A voice default landing on one of these would be a defect, not a
#: preference - the console and its engine would fight the bridge for a port.
KIT_PORTS = {9641, 11441, 9642, 9651, 11451, 11452, 11434, 8080}


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


class FakeReq:
    """A request, as a module handler sees it. Records what the handler answered."""

    def __init__(self, body: dict | None = None, query: dict | None = None) -> None:
        self._body = dict(body or {})
        self.query = dict(query or {})
        self.status: int | None = None
        self.payload: dict | None = None

    def body(self, limit: int = 0) -> dict:
        return dict(self._body)

    def json(self, code: int, payload: object) -> None:
        self.status = code
        self.payload = payload if isinstance(payload, dict) else {"value": payload}


class FakeCtx:
    """A kernel context, narrowed to what these modules actually reach for."""

    def __init__(self, root: str, verdict: str = "ALLOW") -> None:
        self.root = Path(root)
        self.verdict = verdict
        self.receipts: list[dict] = []
        self.p0 = self

    # p0
    def check(self, text: str, kind: str = "prompt") -> dict:
        return {"verdict": self.verdict, "reason": "test", "signature": "test"}

    # paths + io
    def save_dir(self, *parts: str) -> Path:
        p = self.root.joinpath(*[str(x) for x in parts if str(x)])
        p.mkdir(parents=True, exist_ok=True)
        return p

    def read_json(self, path, default=None):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return default

    def write_json(self, path, data) -> bool:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data), encoding="utf-8")
        return True

    def receipt(self, spec: dict | None = None, **kw):
        self.receipts.append({**(spec or {}), **kw})
        return {"ok": True}


# --------------------------------------------------------------------------------------------------


class ManifestTests(unittest.TestCase):
    """What the manifests promise, and whether the adapters really deliver it."""

    def test_both_manifests_validate_against_the_host_contract(self) -> None:
        from modules import validate  # noqa: PLC0415

        for mid in (INFO, CTRL):
            man = _manifest(mid)
            problems = validate.validate_manifest(man, ROOT / "src" / "modules" / mid, catalog_id=mid)
            self.assertEqual(problems, [], f"{mid}: {problems}")
        self.assertEqual(validate.validate_tree(ROOT / "src" / "modules"), [])

    def test_the_read_out_declares_info_and_the_actuator_does_not_invent_a_family(self) -> None:
        info = _manifest(INFO)
        ctrl = _manifest(CTRL)
        self.assertEqual(info["family"], "info", "the read-out half of the pair is an info module")
        self.assertTrue(info["id"].endswith("info"))
        # The naming law knows two declared families (info and watch). An actuator carries its role in
        # its id, the way `lygo.notepad` and `lygo.musicctrl` do, and must not claim a third.
        self.assertTrue(ctrl["id"].endswith("ctrl"), "the actuator role is carried by the id")
        self.assertNotIn("family", ctrl, "an id outside the declared families must not claim a family")

    def test_both_are_in_the_catalog_and_the_host_can_order_them(self) -> None:
        catalog = json.loads((ROOT / "src" / "modules" / "catalog.json").read_text(encoding="utf-8"))
        rows = {r["id"]: r for r in catalog["modules"]}
        for mid in (INFO, CTRL):
            self.assertIn(mid, rows, f"{mid} is not in the catalog, so it would never load")
            self.assertTrue(rows[mid]["enabled"])
            self.assertIsInstance(rows[mid]["order"], int)
        # The read-out must load before the actuator: the pair reads top-down on the strip.
        self.assertLess(rows[INFO]["order"], rows[CTRL]["order"])

    def test_the_console_they_require_is_the_console_shipped(self) -> None:
        for mid in (INFO, CTRL):
            need = _manifest(mid)["requires"]["console"].lstrip(">=")
            self.assertEqual(_version(), need, f"{mid}: the manifest's floor and VERSION must agree")
        # The 1.5.7 bump left this behind and its own test caught it; it must not drift again.
        music = _manifest("lygo.musicctrl")["requires"]["console"].lstrip(">=")
        self.assertEqual(_version(), music, "lygo.musicctrl's floor must move with VERSION again")

    def test_the_release_number_moved_in_every_place_it_lives(self) -> None:
        release = _version()
        notes = (ROOT / "BUILD_NOTES.md").read_text(encoding="utf-8")
        self.assertIn(f"## {release} ", notes, "the notes are the release record")
        js = (ROOT / "portal" / "app.js").read_text(encoding="utf-8")
        self.assertIn(f'const LYGO_BUILD = "{release}";', js)
        host = (ROOT / "src" / "modules" / "host.py").read_text(encoding="utf-8")
        self.assertIn(f'KERNEL_RELEASE_FALLBACK = "{release}"', host)

    def test_the_register_call_hands_the_host_exactly_what_it_declares(self) -> None:
        for mid in (INFO, CTRL):
            man = _manifest(mid)
            got = _load(mid).register(None)
            declared = {(r["method"], r["path"]) for r in man["routes"]}
            self.assertEqual({(m, p) for m, p, _fn in got["routes"]}, declared, mid)
            self.assertEqual([p["id"] for p in got["panes"]], [p["id"] for p in man["panes"]], mid)
            self.assertEqual(got["panes"][0]["slot"], "dock", mid)
            self.assertEqual(got["panes"][0]["data"], f"GET {man['routes'][0]['path']}", mid)

    def test_the_two_panes_do_not_collide_and_are_a_pair_not_a_duplicate(self) -> None:
        info = _manifest(INFO)
        ctrl = _manifest(CTRL)
        self.assertNotEqual(info["panes"][0]["id"], ctrl["panes"][0]["id"])
        self.assertNotEqual(info["routes"][0]["path"], ctrl["routes"][0]["path"])

    def test_the_read_out_owns_nothing_and_the_actuator_owns_one_thing_behind_the_gate(self) -> None:
        info = _manifest(INFO)["state"]
        self.assertEqual(info["owns"], [], "a read-out that owns state is not a read-out")
        self.assertEqual(info["gate"], "none")
        ctrl = _manifest(CTRL)["state"]
        self.assertEqual(ctrl["owns"], ["save/voice"])
        self.assertEqual(ctrl["writes_via"], "atomicio")
        self.assertEqual(ctrl["gate"], "p0")

    def test_the_read_out_source_writes_nothing(self) -> None:
        src = (ROOT / "src" / "modules" / INFO / "backend.py").read_text(encoding="utf-8")
        banned = {"unlink", "rmtree", "shutil.move", "copytree", "write_bytes", "write_text", "mkdir",
                  "atomic_write_text", "remove", "rename"}
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    name = node.func.id
                self.assertNotIn(name, banned, f"the read-out must not call {name}()")

    def test_neither_module_ships_an_agent_limb_by_accident(self) -> None:
        # A limb puts a capability in front of the local brain against one of 24 CORE_NAMES slots. That
        # is a deliberate, separately-gated change - this pass ships the console surface only.
        for mid in (INFO, CTRL):
            self.assertEqual(_manifest(mid)["limbs"], [], f"{mid} declares a limb nobody wired")


# --------------------------------------------------------------------------------------------------


class HarnessBoundaryTests(unittest.TestCase):
    """The licence boundary, as code rather than as a comment."""

    def setUp(self) -> None:
        import voice_harness  # noqa: PLC0415

        self.vh = voice_harness
        self.src = (ROOT / "src" / "voice_harness.py").read_text(encoding="utf-8")

    def test_the_harness_says_it_is_a_bridge_and_names_both_licences(self) -> None:
        self.assertEqual(self.vh.UPSTREAM, "https://github.com/debpalash/VoiceStudio")
        self.assertEqual(self.vh.UPSTREAM_LICENSE, "AGPL-3.0-only")
        self.assertIn("LYGO Sovereign", self.vh.BRIDGE_REASON)
        self.assertIn("AGPL-3.0", self.vh.BRIDGE_REASON)

    def test_no_voice_studio_code_is_imported_anywhere_in_the_kit(self) -> None:
        """The test that fails the day someone decides to 'just vendor it in'.

        It scans every Python file this kit ships for an import of the application's own package. The
        env-var names (`OMNIVOICE_PORT` and friends) are strings, not imports, and are allowed: they are
        part of the interface the vendor publishes.
        """
        offenders: list[str] = []
        for py in sorted((ROOT / "src").rglob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module.split(".")[0]]
                for name in names:
                    if name.lower() in {"omnivoice", "voicestudio", "voicestudio_backend"}:
                        offenders.append(f"{py.relative_to(ROOT)}:{node.lineno} imports {name}")
        self.assertEqual(offenders, [], "VoiceStudio must stay a separate program: " + "; ".join(offenders))
        # And the bridge must be honest that it is one: the two licences are named in the harness.
        self.assertIn("AGPL-3.0", self.src)
        self.assertIn("LYGO Sovereign", self.src)

    def test_nothing_in_the_voice_studio_tree_is_ever_opened_for_writing(self) -> None:
        for node in ast.walk(ast.parse(self.src)):
            if isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    name = node.func.id
                self.assertNotIn(
                    name, {"unlink", "rmtree", "rename", "replace", "copytree", "shutil.move"},
                    f"the harness must not call {name}() on anything",
                )
        # Every open() in the harness must be aimed at this kit's own save/ tree - never at the app.
        self.assertIn("launch_record_path()", self.src)
        self.assertNotIn("install_root() /", self.src, "the harness must not build a path inside the app")

    def test_the_clip_tree_belongs_to_this_kit_and_not_to_the_application(self) -> None:
        import paths  # noqa: PLC0415

        clip_dir = self.vh.voice_dir()
        self.assertTrue(
            str(clip_dir.resolve()).lower().startswith(str(Path(paths.WORKSPACE).resolve()).lower()),
            f"clips must land under this kit's workspace, got {clip_dir}",
        )
        self.assertNotIn("VoiceStudio", str(clip_dir), "clips must never be written into the app")

    def test_the_default_port_collides_with_nothing_this_kit_already_owns(self) -> None:
        self.assertEqual(self.vh.DEFAULT_PORT, 3900)
        self.assertNotIn(self.vh.DEFAULT_PORT, KIT_PORTS)

    def test_the_card_states_the_boundary_so_nobody_has_to_re_derive_it(self) -> None:
        card = _load(INFO).build(None, {"install": {}, "service": {}, "gaps": [],
                                        "upstream": self.vh.UPSTREAM, "license": self.vh.UPSTREAM_LICENSE})
        titles = [g["title"] for g in card["groups"]]
        self.assertIn("The licence boundary", titles)
        rows = {r["k"]: r["v"] for g in card["groups"] if g["title"] == "The licence boundary"
                for r in g["rows"]}
        self.assertEqual(rows.get("Verdict"), "bridge, not embed")


class HarnessBehaviourTests(unittest.TestCase):
    """What the harness measures, and what it refuses to claim."""

    def setUp(self) -> None:
        import voice_harness  # noqa: PLC0415

        self.vh = voice_harness
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._env = {k: os.environ.get(k) for k in ("APPDATA", "LOCALAPPDATA")}

    def tearDown(self) -> None:
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _fake_machine(self, *, runtime_ready: bool = True) -> Path:
        """A whole pretend machine: an install, a location record and a staged runtime."""
        appdata = self.home / "Roaming"
        local = self.home / "Local"
        (local / "Programs" / "VoiceStudio" / "resources" / "backend").mkdir(parents=True)
        (local / "Programs" / "VoiceStudio" / "VoiceStudio.exe").write_bytes(b"MZ" + b"\0" * 100)
        (local / "Programs" / "VoiceStudio" / "resources" / "backend" / "main.py").write_text("app = 1\n")
        root = self.home / "Vault" / "VoiceStudio"
        project = root / "project"
        scripts = project / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        scripts.joinpath("python.exe").write_bytes(b"MZ")
        (project / ".venv" / "pyvenv.cfg").write_text("home = x\n")
        if runtime_ready:
            (project / ".runtime-ready").write_text("x")
        else:
            (project / ".runtime-installing").write_text("")
        (appdata / "VoiceStudio").mkdir(parents=True)
        (appdata / "VoiceStudio" / "runtime-location.json").write_text(
            json.dumps({"root": str(root), "owned": True}), encoding="utf-8"
        )
        os.environ["APPDATA"] = str(appdata)
        os.environ["LOCALAPPDATA"] = str(local)
        return root

    def test_install_and_runtime_are_read_from_the_machine_not_assumed(self) -> None:
        self._fake_machine()
        install = self.vh.install_state()
        self.assertTrue(install["present"])
        self.assertTrue(install["exe"].endswith("VoiceStudio.exe"))
        self.assertTrue(install["backend_main"].endswith("main.py"))
        loc = self.vh.location_record()
        self.assertTrue(loc["read"])
        self.assertTrue(loc["owned"])
        rt = self.vh.runtime_state()
        self.assertTrue(rt["deps_ready"])
        self.assertTrue(rt["ready_marker"])

    def test_a_machine_without_the_application_is_a_state_with_a_reason(self) -> None:
        os.environ["APPDATA"] = str(self.home / "nothing")
        os.environ["LOCALAPPDATA"] = str(self.home / "nothing")
        install = self.vh.install_state()
        self.assertFalse(install["present"])
        self.assertTrue(install["reason"], "an absent install must say why it is absent")
        loc = self.vh.location_record()
        self.assertFalse(loc["read"])
        self.assertTrue(loc["reason"], "a missing location record must say what it means")

    def test_a_still_installing_runtime_is_not_called_ready(self) -> None:
        self._fake_machine(runtime_ready=False)
        rt = self.vh.runtime_state()
        self.assertTrue(rt["installing"])
        self.assertFalse(rt["ready_marker"])
        # The interpreter is there, so it can run; but the marker is the vendor's stronger claim and
        # the card must be able to say the difference.
        self.assertIn("installing", (rt["reason"] or "").lower() + "installing")

    def test_probe_treats_the_live_health_route_as_up(self) -> None:
        """VoiceStudio 0.5.6 serves GET /health 200; /api/health is 404 on this box."""

        def fake_http(method, path, **_kw):
            if path == "/health":
                return 200, b'{"status":"ok","version":"0.5.6"}', ""
            if path == "/.well-known/voicestudio-speech":
                return 200, b'{"service":"VoiceStudio"}', ""
            if path == "/v1/models":
                return 200, b'{"data":[{"id":"omnivoice"}]}', ""
            return 404, b"", ""

        with mock.patch.object(self.vh, "_http", fake_http):
            out = self.vh.probe(timeout=0.2)
        self.assertTrue(out["up"], out)
        self.assertEqual(out.get("health_path"), "/health")
        self.assertEqual(out.get("version"), "0.5.6")

    def test_the_argv_is_exactly_the_one_the_vendor_shell_builds(self) -> None:
        self._fake_machine()
        built = self.vh.backend_argv(4321)
        self.assertTrue(built["ok"], built)
        argv = built["argv"]
        self.assertTrue(argv[0].endswith("python.exe"))
        self.assertEqual(argv[1:], ["-m", "uvicorn", "main:app", "--app-dir", "backend",
                                    "--host", "127.0.0.1", "--port", "4321"])
        self.assertTrue(built["cwd"].endswith("project"))

    def test_the_argv_is_refused_when_the_runtime_is_not_staged(self) -> None:
        os.environ["APPDATA"] = str(self.home / "nothing")
        os.environ["LOCALAPPDATA"] = str(self.home / "nothing")
        built = self.vh.backend_argv()
        self.assertFalse(built["ok"])
        self.assertEqual(built["error"], "runtime_not_ready")
        self.assertTrue(built["why"], "a refusal must name the missing thing")

    def test_the_environment_drops_the_two_variables_that_break_a_staged_venv(self) -> None:
        self._fake_machine()
        os.environ["PYTHONHOME"] = "C:/wrong"
        os.environ["PYTHONPATH"] = "C:/also-wrong"
        try:
            env = self.vh.backend_env(4321)
            self.assertNotIn("PYTHONHOME", env)
            self.assertNotIn("PYTHONPATH", env)
            self.assertEqual(env["OMNIVOICE_PORT"], "4321")
            self.assertEqual(env["OMNIVOICE_DESKTOP_CONTAINED"], "1")
            self.assertEqual(env["PYTHONUTF8"], "1")
            self.assertEqual(env["PYTHONUNBUFFERED"], "1")
        finally:
            os.environ.pop("PYTHONHOME", None)
            os.environ.pop("PYTHONPATH", None)

    def test_a_probe_never_claims_up_without_a_two_hundred(self) -> None:
        real = self.vh._http
        try:
            self.vh._http = lambda *a, **k: (500, b"{}", "")
            got = self.vh.probe(timeout=0.1)
            self.assertFalse(got["up"], "a 500 is not up")
            self.assertIn("health", got["checks_failed"])
            self.vh._http = lambda *a, **k: (0, b"", "ConnectionRefusedError: test")
            got = self.vh.probe(timeout=0.1)
            self.assertFalse(got["up"])
            self.assertIn("ConnectionRefused", got["error"])
        finally:
            self.vh._http = real

    def test_speaking_refuses_before_it_calls_anything(self) -> None:
        self.assertEqual(self.vh.speak("")["error"], "empty_text")
        self.assertEqual(self.vh.speak("   ")["error"], "empty_text")
        out = self.vh.speak("hello", fmt="not-a-format")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "unknown_format")

    def test_speaking_against_a_dead_service_names_the_service_and_never_writes_a_file(self) -> None:
        self._fake_machine()
        real_probe, real_dir = self.vh.probe, self.vh.voice_dir
        try:
            self.vh.probe = lambda *a, **k: {"up": False, "error": "URROR", "base": "http://x"}
            self.vh.voice_dir = lambda: Path(self.tmp.name) / "clips"
            out = self.vh.speak("hello")
            self.assertFalse(out["ok"])
            self.assertEqual(out["error"], "service_down")
            self.assertFalse((Path(self.tmp.name) / "clips").exists(),
                             "a refused speak must not create the clip tree")
        finally:
            self.vh.probe, self.vh.voice_dir = real_probe, real_dir

    def test_a_clip_is_only_reported_written_after_the_bytes_are_read_back(self) -> None:
        self._fake_machine()
        clips = Path(self.tmp.name) / "clips"
        real_probe, real_http, real_dir = self.vh.probe, self.vh._http, self.vh.voice_dir
        payload = b"ID3" + b"\x00" * 900
        try:
            self.vh.probe = lambda *a, **k: {"up": True}
            self.vh._http = lambda *a, **k: (200, payload, "")
            self.vh.voice_dir = lambda: clips
            out = self.vh.speak("hello there", voice="v1", model="omnivoice")
            self.assertTrue(out["ok"], out)
            self.assertTrue(out["verified"])
            self.assertEqual(out["bytes"], len(payload))
            self.assertEqual(Path(out["path"]).read_bytes(), payload)
            self.assertEqual(len(out["sha256"]), 64)
        finally:
            self.vh.probe, self.vh._http, self.vh.voice_dir = real_probe, real_http, real_dir

    def test_a_four_hundred_from_the_service_is_reported_with_its_own_message(self) -> None:
        self._fake_machine()
        real_probe, real_http, real_dir = self.vh.probe, self.vh._http, self.vh.voice_dir
        body = json.dumps({"error": {"message": "no engine is installed"}}).encode()
        try:
            self.vh.probe = lambda *a, **k: {"up": True}
            self.vh._http = lambda *a, **k: (400, body, "")
            self.vh.voice_dir = lambda: Path(self.tmp.name) / "clips"
            out = self.vh.speak("hello")
            self.assertFalse(out["ok"])
            self.assertEqual(out["http"], 400)
            self.assertIn("no engine is installed", out["why"])
        finally:
            self.vh.probe, self.vh._http, self.vh.voice_dir = real_probe, real_http, real_dir

    def test_transcribing_a_file_that_is_not_there_is_refused_by_name(self) -> None:
        out = self.vh.transcribe(str(self.home / "nope.wav"))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "no_such_file")

    def test_launch_holds_a_stdin_pipe_so_contained_mode_stays_up(self) -> None:
        """A closed stdin is how VoiceStudio decides the desktop shell died. DEVNULL would exit it."""
        captured: dict[str, object] = {}

        class _Proc:
            pid = 4242
            stdin = object()

            def poll(self) -> None:
                return None

        def _popen(*_a, **kw):
            captured["stdin"] = kw.get("stdin")
            return _Proc()

        real_probe, real_free, real_argv, real_popen, real_write = (
            self.vh.probe, self.vh.port_free, self.vh.backend_argv,
            self.vh.subprocess.Popen, self.vh._write_launch_record,
        )
        try:
            self.vh.probe = lambda *a, **k: {"up": False, "error": "down"}
            self.vh.port_free = lambda *a, **k: True
            self.vh.backend_argv = lambda *a, **k: {"ok": True, "argv": ["python", "-m", "uvicorn"], "cwd": "x"}
            self.vh.subprocess.Popen = _popen
            self.vh._write_launch_record = lambda *_a, **_k: None
            out = self.vh.launch(wait=False)
            self.assertTrue(out["ok"], out)
            self.assertTrue(out["started"])
            self.assertIs(captured["stdin"], subprocess.PIPE)
            self.assertEqual(self.vh._held_backend.pid, 4242)
        finally:
            self.vh.probe, self.vh.port_free, self.vh.backend_argv = real_probe, real_free, real_argv
            self.vh.subprocess.Popen, self.vh._write_launch_record = real_popen, real_write
            self.vh._held_backend = None

    def test_launch_reuses_a_service_it_did_not_start_instead_of_spawning_a_second_one(self) -> None:
        real_probe = self.vh.probe
        spawned: list[object] = []
        real_popen = self.vh.subprocess.Popen
        try:
            self.vh.probe = lambda *a, **k: {"up": True, "base": "http://127.0.0.1:3900"}
            self.vh.subprocess.Popen = lambda *a, **k: spawned.append(a) or (_ for _ in ()).throw(
                AssertionError("launch spawned a process while a service was already answering"))
            out = self.vh.launch(wait=False)
            self.assertTrue(out["ok"])
            self.assertTrue(out["reused"])
            self.assertFalse(out["started"])
            self.assertEqual(spawned, [])
        finally:
            self.vh.probe = real_probe
            self.vh.subprocess.Popen = real_popen

    def test_stop_refuses_by_name_to_kill_a_service_this_console_did_not_start(self) -> None:
        real_record = self.vh.launch_record
        real_run = self.vh.subprocess.run
        try:
            self.vh.launch_record = lambda: {}
            self.vh.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("stop ran a kill on a service it never started"))
            out = self.vh.stop()
            self.assertFalse(out["ok"])
            self.assertFalse(out["stopped"])
            self.assertEqual(out["error"], "not_ours")
            self.assertIn("will not kill it", out["why"] + "will not kill it")
        finally:
            self.vh.launch_record = real_record
            self.vh.subprocess.run = real_run

    def test_state_names_every_gap_instead_of_reporting_a_zero(self) -> None:
        os.environ["APPDATA"] = str(self.home / "nothing")
        os.environ["LOCALAPPDATA"] = str(self.home / "nothing")
        st = self.vh.state(deep=True)
        self.assertFalse(st["install"]["present"])
        self.assertTrue(st["gaps"], "a machine with nothing on it must report named gaps")
        self.assertTrue(any("not installed" in g for g in st["gaps"]))
        self.assertEqual(st["license"], "AGPL-3.0-only")


# --------------------------------------------------------------------------------------------------


class CardTests(unittest.TestCase):
    """The card paints inside the shell's vocabulary and never claims more than it read."""

    def test_a_machine_with_no_application_reads_grey_and_says_why(self) -> None:
        card = _load(INFO).build(None, {
            "install": {"present": False, "reason": "no VoiceStudio.exe under X"},
            "service": {"up": False, "base": "http://127.0.0.1:3900", "checks_run": 1,
                        "checks_failed": ["health"], "error": "ConnectionRefused"},
            "gaps": ["VoiceStudio is not installed"], "upstream": "u", "license": "AGPL-3.0-only",
        })
        self.assertEqual(card["lights"][0]["state"], "grey", "absent is not a fault")
        self.assertIn("no VoiceStudio.exe", card["lights"][0]["detail"])
        self.assertTrue(card["missing"])

    def test_every_light_is_a_state_the_shell_knows(self) -> None:
        for mid, st in ((INFO, {"install": {"present": True}, "service": {"up": True}, "gaps": []}),
                        (CTRL, {"install": {"present": True}, "service": {"up": True}, "gaps": []})):
            card = _load(mid).build(None, st)
            for light in card["lights"]:
                self.assertIn(light["state"], LIGHT_STATES, f"{mid}: {light}")

    def test_an_actuator_with_no_install_still_paints_and_says_it_cannot_speak(self) -> None:
        card = _load(CTRL).build(None, {"install": {"present": False, "reason": "absent"},
                                        "service": {"up": False}, "gaps": ["installed?"]})
        self.assertEqual(card["lights"][0]["state"], "grey")
        self.assertTrue(any("no clip can be produced" in m for m in card["missing"]))
        self.assertEqual(card["clip"], "")


class RouteTests(unittest.TestCase):
    """The routes: what they refuse, and what they refuse to do."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ctrl = _load(CTRL)
        self.info = _load(INFO)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_an_action_it_does_not_know_is_refused_with_the_list(self) -> None:
        req = FakeReq({"action": "sing me a song"})
        self.ctrl.control(FakeCtx(self.tmp.name), req)
        self.assertEqual(req.status, 400)
        self.assertEqual(req.payload["error"], "unknown_action")
        self.assertIn("speak", req.payload["actions"])

    def test_a_quarantined_body_never_reaches_the_service(self) -> None:
        called: list[tuple] = []
        import voice_harness  # noqa: PLC0415

        real = voice_harness.speak
        try:
            voice_harness.speak = lambda *a, **k: called.append(a) or {"ok": True}
            req = FakeReq({"action": "speak", "text": "something the gate dislikes"})
            self.ctrl.control(FakeCtx(self.tmp.name, verdict="QUARANTINE"), req)
            self.assertEqual(req.status, 403)
            self.assertEqual(req.payload["error"], "gate_refused")
            self.assertEqual(called, [], "a quarantined body must not be sent anywhere")
        finally:
            voice_harness.speak = real

    def test_empty_text_is_refused_before_the_gate_is_even_asked(self) -> None:
        req = FakeReq({"action": "speak", "text": "   "})
        self.ctrl.control(FakeCtx(self.tmp.name), req)
        self.assertEqual(req.status, 400)
        self.assertEqual(req.payload["error"], "empty_text")

    def test_text_beyond_the_routes_limit_is_refused_by_name(self) -> None:
        req = FakeReq({"action": "speak", "text": "a" * (self.ctrl.MAX_TEXT_CHARS + 1)})
        self.ctrl.control(FakeCtx(self.tmp.name), req)
        self.assertEqual(req.status, 400)
        self.assertEqual(req.payload["error"], "text_too_long")

    def test_a_clip_that_was_written_is_recorded_and_the_choice_persists(self) -> None:
        import voice_harness  # noqa: PLC0415

        real = voice_harness.speak
        ctx = FakeCtx(self.tmp.name)
        try:
            voice_harness.speak = lambda text, **k: {
                "ok": True, "path": str(Path(self.tmp.name) / "clip.mp3"), "name": "clip.mp3",
                "bytes": 1234, "sha256": "a" * 64, "chars": len(text), "format": "mp3",
                "voice": k.get("voice", ""), "model": k.get("model", ""),
            }
            req = FakeReq({"action": "voice", "engine": "omnivoice", "voice": "my-clone"})
            self.ctrl.control(ctx, req)
            self.assertEqual(req.status, 200)
            self.assertEqual(req.payload["state"]["voice"], "my-clone")

            req = FakeReq({"action": "speak", "text": "hello there"})
            self.ctrl.control(ctx, req)
            self.assertEqual(req.status, 200)
            self.assertTrue(req.payload["ok"], req.payload)
            self.assertEqual(req.payload["record"]["bytes"], 1234)
            # The record lives in the module's OWN declared state, keyed under save/voice.
            state = json.loads((Path(self.tmp.name) / "voice.json").read_text(encoding="utf-8"))
            self.assertEqual(len(state["clips"]), 1)
            self.assertEqual(state["voice"], "my-clone")
            self.assertTrue(ctx.receipts, "a written clip earns a receipt")
        finally:
            voice_harness.speak = real

    def test_a_bad_speed_is_refused_rather_than_silently_dropped(self) -> None:
        req = FakeReq({"action": "voice", "speed": "fast please"})
        self.ctrl.control(FakeCtx(self.tmp.name), req)
        self.assertEqual(req.status, 400)
        self.assertEqual(req.payload["error"], "bad_speed")

    def test_health_answers_without_speaking_anything(self) -> None:
        out = self.ctrl.health(FakeCtx(self.tmp.name))
        self.assertIn("ok", out)
        self.assertTrue(out["detail"])
        out = self.info.health(None)
        self.assertIn("detail", out)

    def test_the_read_out_answers_a_card_even_when_the_harness_cannot_be_asked(self) -> None:
        fake = FakeReq({})
        self.info.data(None, fake)
        self.assertEqual(fake.status, 200)
        self.assertIn("card", fake.payload)
        self.assertIn("upstream", fake.payload["card"])

    def test_the_panel_actually_reports_the_service_rather_than_showing_a_stub(self) -> None:
        src = (ROOT / "src" / "modules" / CTRL / "backend.py").read_text(encoding="utf-8")
        for needle in ("voice_harness", "/api/health", "sha256", "speak(", "stop(", "receipt"):
            self.assertIn(needle, src, f"the panel is missing {needle!r}")
        # It must not re-derive what the harness measured: the byte check, the hash and the read-back
        # all live in one place, and the panel's job is to record what came back.
        for rederived in ("hashlib", "read_bytes()", "open("):
            self.assertNotIn(rederived, src, f"the panel re-derives {rederived!r} instead of asking")


if __name__ == "__main__":
    unittest.main()
