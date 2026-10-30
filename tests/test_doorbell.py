"""The page's own "Boot server" button — the launcher, reachable from the browser.

The steward, 2026-09-21: "a good feature would be a button on the console that is basically the boot bat
thats on the desktop, built into the console ... right now the server is down and closed but the browser
is up showing health failed ... the boot LLM button only switches the llm, we need a separate stand alone
button that boots the server from the browser page just in case it goes down or needs a restart".

The page is served BY the server it would be asking to start, and no browser can spawn a process, so the
button cannot work by itself. It rings a doorbell: a tiny always-on listener in `tools/doorbell.py`, on its
own port (the console's port minus one), whose only job is to run `LYGO_LLM_CONSOLE.bat` on request. The
launcher keeps its port sweep, its ownership checks and its refusal to double-start — the doorbell never
reimplements any of that, it just runs the .bat.

What these tests hold:

* the doorbell runs the real launcher, and only that: no shell string is ever assembled from input;
* a request without the right token cannot boot anything, and a foreign origin is never trusted;
* `/state` reports what is true and never prints the token;
* the console has to tell the page where the doorbell is and give it the token (same-origin, already authed);
* the page has a standalone button, and the health-failed path — the one the steward actually hit — points
  at it instead of dead-ending on "is the window still running?";
* the doorbell never opens a second tab: the browser is already open, which is the whole point.
"""

import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parent.parent
for extra in (ROOT / "src", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import doorbell  # noqa: E402


class TheDoorbellRunsTheLauncherTests(unittest.TestCase):
    def test_it_runs_the_real_launcher_and_opens_no_second_window_of_its_own(self):
        argv = doorbell.boot_argv(ROOT)
        self.assertEqual(["cmd", "/c", "start", "", "/min", str(ROOT / "LYGO_LLM_CONSOLE.bat")], argv)

    def test_the_boot_call_is_detached_and_suppresses_the_browser(self):
        seen = {}

        def spawn(argv, **kw):
            seen["argv"] = argv
            seen["kw"] = kw
            return type("P", (), {"pid": 4321})()

        out = doorbell.boot(ROOT, spawn=spawn)
        self.assertTrue(out["launched"])
        self.assertEqual(4321, out["pid"])
        self.assertEqual("1", seen["kw"]["env"]["LYGO_NO_BROWSER"])
        # a doorbell that dies with the console is useless: it must outlive the process that rang it
        self.assertNotEqual(0, seen["kw"]["creationflags"] & doorbell.DETACHED_PROCESS)

    def test_a_boot_asks_once_and_never_twice(self):
        calls = []

        def spawn(argv, **kw):
            calls.append(argv)
            return type("P", (), {"pid": 1})()

        doorbell.boot(ROOT, spawn=spawn)
        doorbell.boot(ROOT, spawn=spawn)
        self.assertEqual(2, len(calls), "every ring boots once - the launcher itself refuses a double start")


class TheTokenGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.token = doorbell.token(self.root)

    def test_a_missing_or_wrong_token_cannot_boot_anything(self):
        for bad in ("", "nope", None):
            calls = []
            status, _h, body = doorbell.handle("POST", "/boot", {"token": bad}, root=self.root,
                                               expected_token=self.token, spawn=lambda *a, **k: calls.append(a))
            self.assertEqual(403, status)
            self.assertEqual([], calls, "an unauthenticated ring must not reach the launcher")
            self.assertIn(b"token", body.lower())

    def test_the_right_token_boots(self):
        calls = []

        def spawn(argv, **kw):
            calls.append(argv)
            return type("P", (), {"pid": 99})()

        status, _h, body = doorbell.handle("POST", "/boot", {"token": self.token}, root=self.root,
                                           expected_token=self.token, spawn=spawn)
        self.assertEqual(200, status)
        self.assertEqual(1, len(calls))
        self.assertTrue(json.loads(body)["launched"])

    def test_the_token_is_stable_and_not_guessable(self):
        again = doorbell.token(self.root)
        self.assertEqual(self.token, again)
        self.assertEqual(32, len(self.token))
        self.assertTrue(all(c in "0123456789abcdef" for c in self.token))

    def test_state_never_prints_the_token(self):
        _s, _h, body = doorbell.handle("GET", "/state", None, root=self.root, expected_token=self.token,
                                       probe=lambda port, timeout=0.4: True)
        text = body.decode("utf-8")
        self.assertNotIn(self.token, text)
        self.assertIn('"console_up": true', text)


class TheStateAndCorsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_state_reports_what_is_true(self):
        _s, _h, body = doorbell.handle("GET", "/state", None, root=self.root, expected_token="x",
                                       probe=lambda port, timeout=0.4: False, console_port=9641)
        d = json.loads(body)
        self.assertTrue(d["doorbell"])
        self.assertEqual(9641, d["console_port"])
        self.assertFalse(d["console_up"])

    def test_the_console_origin_is_allowed_and_a_foreign_one_is_not(self):
        _s, h, _b = doorbell.handle("OPTIONS", "/boot", None, root=self.root, expected_token="x",
                                    console_port=9641, origin="http://127.0.0.1:9641")
        allow = h.get("Access-Control-Allow-Origin", "")
        self.assertIn("127.0.0.1:9641", allow)
        _s2, h2, _b2 = doorbell.handle("OPTIONS", "/boot", None, root=self.root, expected_token="x",
                                       console_port=9641, origin="http://evil.example")
        self.assertNotIn("evil.example", h2.get("Access-Control-Allow-Origin", ""))

    def test_an_unknown_path_is_not_an_error_to_hide(self):
        status, _h, _b = doorbell.handle("GET", "/nope", None, root=self.root, expected_token="x")
        self.assertEqual(404, status)


class ThePortAndProbeTests(unittest.TestCase):
    def test_the_doorbell_sits_one_below_the_console_unless_configured(self):
        self.assertEqual(9640, doorbell.default_port(9641))
        self.assertEqual(9650, doorbell.default_port(9651))

    def test_the_probe_tells_a_listening_port_from_a_closed_one(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            self.assertTrue(doorbell.console_is_up(port, timeout=0.5))
        finally:
            srv.close()
        self.assertFalse(doorbell.console_is_up(port, timeout=0.3))

    def test_it_only_ever_binds_loopback(self):
        self.assertEqual("127.0.0.1", doorbell.HOST)


class TheConsoleTellsThePageTests(unittest.TestCase):
    def test_the_console_exposes_the_doorbell_and_its_token_to_the_page(self):
        src = (ROOT / "src" / "server.py").read_text(encoding="utf-8")
        self.assertIn("/api/doorbell", src, "the page must be able to ask where the doorbell is")
        self.assertIn("doorbell", src)

    def test_the_console_does_not_open_a_tab_when_the_doorbell_starts_it(self):
        src = (ROOT / "src" / "server.py").read_text(encoding="utf-8")
        self.assertIn("LYGO_NO_BROWSER", src,
                      "the browser is already open on the page that rang - a second tab is noise")

    def test_the_launcher_starts_the_doorbell_so_a_closed_window_stays_recoverable(self):
        bat = (ROOT / "LYGO_LLM_CONSOLE.bat").read_text(encoding="utf-8", errors="replace")
        self.assertIn("tools\\doorbell.py", bat)


class ThePageHasTheButtonTests(unittest.TestCase):
    def test_there_is_a_standalone_boot_server_button(self):
        html = (ROOT / "portal" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="server-boot"', html)

    def test_the_button_rings_the_doorbell(self):
        js = (ROOT / "portal" / "app.js").read_text(encoding="utf-8")
        self.assertIn("server-boot", js)
        self.assertIn("doorbell", js)

    def test_the_health_failure_points_at_the_button_instead_of_dead_ending(self):
        js = (ROOT / "portal" / "app.js").read_text(encoding="utf-8")
        i = js.index("health failed")
        window = js[i:i + 700]
        self.assertIn("server-boot", window,
                      "the exact moment the steward hit: console down, page up, nothing to press")


if __name__ == "__main__":
    unittest.main()


class ThePageActuallyLearnsWhereTheDoorbellIsTests(unittest.TestCase):
    """The bug the browser found, 2026-09-21: the button was there, health said failed, and pressing it did
    nothing at all. The page had never called `refreshDoorbell()` while the server was still alive, so it had
    no doorbell port and no token and gave up silently. Asserting the strings exist is not enough - the
    wiring is the feature, so that is what these hold."""

    def setUp(self):
        self.js = (ROOT / "portal" / "app.js").read_text(encoding="utf-8")

    def test_something_outside_the_definition_actually_calls_it(self):
        defined = self.js.index("async function refreshDoorbell")
        after = [i for i in range(defined, len(self.js)) if self.js.startswith("refreshDoorbell()", i)]
        self.assertTrue(after, "defined but never called is exactly how the button went dead")

    def test_the_health_poll_is_what_teaches_the_page_about_the_doorbell(self):
        i = self.js.index("setHealth(j);")  # the call, not the definition
        self.assertIn("refreshDoorbell()", self.js[i:i + 600],
                      "the only moment it can learn is while the console still answers")

    def test_with_no_doorbell_it_names_the_standalone_page_by_position(self):
        i = self.js.index("async function bootServer")
        window = self.js[i:i + 1200]
        self.assertIn("location.port", window,
                      "the doorbell sits one below this page, so the fallback can name it offline")
        self.assertIn("Boot the console", window, "and it has to say what to press there")


class ThePageIsAllowedToReachTheDoorbellTests(unittest.TestCase):
    """The browser found this one, and a source-level test would not have: the button ran, the fetch never
    left the page, and the console's own Content-Security-Policy was why - connect-src was "'self' https:"
    so any plain-http port beside this one was refused with "Failed to fetch" while the doorbell sat there
    perfectly alive. The policy has to name the door once - it is derived, never hardcoded per copy."""

    def test_connect_src_names_the_doorbell_beside_this_console(self):
        server = (ROOT / "src" / "server.py").read_text(encoding="utf-8")
        i = server.index("connect-src")
        clause = server[i:i + 220]
        self.assertIn("http://127.0.0.1:", clause, "a bare http doorbell beside this console must be allowed")

    def test_the_doorbell_port_is_derived_from_the_console_and_not_frozen_per_copy(self):
        server = (ROOT / "src" / "server.py").read_text(encoding="utf-8")
        i = server.index("connect-src")
        window = server[max(0, i - 900):i + 260]
        self.assertIn("server_port", window, "it sits one below whichever port this copy actually bound")
        self.assertNotIn("9640", window, "and it is never the PC literal, or the USB copy breaks")


class TheDoorbellOutlivesItsConsoleTests(unittest.TestCase):
    """A doorbell that silently dies is not a doorbell. It was started from the launcher window and went away
    with it, so the button had nothing to ring - the same trap as the dead server, one layer down. It has to
    be able to leave its parent, write down what it did, and be ensured by the console that it guards."""

    def test_it_can_detach_from_the_window_that_started_it(self):
        src = (ROOT / "tools" / "doorbell.py").read_text(encoding="utf-8")
        self.assertIn("--detach", src, "a doorbell tied to a console window dies with it")
        self.assertIn("DETACHED_PROCESS", src)
        self.assertIn("CREATE_NO_WINDOW", src, "and it must not need a window of its own to survive")

    def test_it_writes_down_what_it_did(self):
        src = (ROOT / "tools" / "doorbell.py").read_text(encoding="utf-8")
        self.assertIn("doorbell.log", src, "it was invisible when it mattered - failures must be readable")

    def test_it_never_writes_the_token_into_that_log(self):
        src = (ROOT / "tools" / "doorbell.py").read_text(encoding="utf-8")
        i = src.index("def log_line")
        window = src[i:i + 700]
        self.assertNotIn("token", window.lower(), "the log must never carry the token")

    def test_the_console_makes_sure_one_is_running(self):
        src = (ROOT / "src" / "server.py").read_text(encoding="utf-8")
        self.assertIn("ensure_running", src,
                      "otherwise the button only works if the launcher happened to start one")
        self.assertIn("LYGO_NO_DOORBELL", src, "with a way out for anyone who does not want it")

    def test_the_launcher_starts_it_detached(self):
        bat = (ROOT / "LYGO_LLM_CONSOLE.bat").read_text(encoding="utf-8", errors="replace")
        self.assertIn("doorbell.py", bat)
        self.assertIn("--detach", bat, "started attached, it dies with the launcher window")


class ARefusedRingHealsItselfTests(unittest.TestCase):
    """Found live, 2026-09-21: the page rang with a token it had cached while the doorbell was broken, the
    doorbell refused it (403), and the button polled a dead server for 88 seconds without a word. A refusal
    has to be heard - the token can legitimately change, and the operator must not be left guessing."""

    def setUp(self):
        self.js = (ROOT / "portal" / "app.js").read_text(encoding="utf-8")

    def test_the_ring_response_is_actually_read(self):
        i = self.js.index("async function bootServer")
        window = self.js[i:i + 2600]
        self.assertIn("r.ok", window, "a fire-and-forget ring cannot tell refusal from success")

    def test_a_refused_token_is_forgotten_and_re_learned(self):
        i = self.js.index("async function bootServer")
        window = self.js[i:i + 2600]
        self.assertIn("removeItem", window, "a token that was refused must not be kept")
        self.assertIn("refreshDoorbell()", window, "and the page must try to learn the current one")

    def test_and_it_says_so_when_it_still_cannot_ring(self):
        i = self.js.index("async function bootServer")
        window = self.js[i:i + 2600]
        self.assertIn("refused", window, "silence is how this cost 88 seconds")


class TheTokenHasToSurviveTheWholeTripTests(unittest.TestCase):
    """Found live, 2026-09-21, by ringing the doorbell with the token from its own token file and being
    refused anyway: the handler passed only the request path to handle(), so the "?token=" part of the
    request target never reached the comparison. Everything looked right and nothing could ever ring - the
    page was blamed for a stale token it did not have. These hold the request target end to end."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.token = doorbell.token(self.root)

    def _ring(self, target, spawn):
        return doorbell.handle("GET", target, None, root=self.root, expected_token=self.token, spawn=spawn)

    def test_a_token_in_the_query_is_honoured(self):
        calls = []
        spawn = lambda argv, **kw: calls.append(argv) or type("P", (), {"pid": 7})()
        status, _h, body = self._ring("/boot?token=" + self.token + "&t=123", spawn)
        self.assertEqual(200, status, "the query carries the token on every real ring")
        self.assertEqual(1, len(calls))
        self.assertTrue(json.loads(body)["launched"])

    def test_the_handler_hands_over_the_whole_request_target(self):
        src = (ROOT / "tools" / "doorbell.py").read_text(encoding="utf-8")
        i = src.index("def _run")
        window = src[i:i + 1800]
        self.assertIn("method, self.path", window,
                      "parsed.path alone loses the token: the query never reaches the comparison")

    def test_an_injected_spawner_does_not_write_into_a_shipped_log(self):
        log = doorbell.log_path(self.root)
        try:
            log.unlink()
        except FileNotFoundError:
            pass
        doorbell.boot(self.root, spawn=lambda argv, **kw: type("P", (), {"pid": 5})())
        self.assertFalse(log.exists(), "a test double must not post fake boots into the kit's own log")


class TheDoorbellFindsThisCopysLauncherTests(unittest.TestCase):
    """The USB copy launches from LYGO_AGENT_STICK.bat, not the PC's LYGO_LLM_CONSOLE.bat. Hardcoding one
    name means the doorbell refuses to listen on the other copy ("no launcher - refusing to listen"), which
    is exactly where a hand on the stick would need it."""

    def test_it_resolves_a_launcher_that_exists_in_this_copy(self):
        self.assertTrue(doorbell.launcher_path(ROOT).exists(),
                        "the doorbell must point at a launcher this copy actually has")

    def test_the_console_launcher_wins_when_both_are_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "LYGO_LLM_CONSOLE.bat").write_text("@echo off", encoding="utf-8")
            (root / "LYGO_AGENT_STICK.bat").write_text("@echo off", encoding="utf-8")
            self.assertEqual(root / "LYGO_LLM_CONSOLE.bat", doorbell.launcher_path(root))

    def test_the_stick_launcher_is_used_when_it_is_the_only_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "LYGO_AGENT_STICK.bat").write_text("@echo off", encoding="utf-8")
            self.assertEqual(root / "LYGO_AGENT_STICK.bat", doorbell.launcher_path(root))

    def test_it_never_guesses_a_shell_string_from_input(self):
        argv = doorbell.boot_argv(ROOT)
        self.assertEqual(["cmd", "/c", "start", "", "/min"], argv[:5],
                         "the launcher path is the only variable part, and it is resolved here")
        self.assertTrue(argv[5].lower().endswith(".bat"))