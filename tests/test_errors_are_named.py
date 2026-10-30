"""A 500 that says nothing costs a pass; the log must name it.

`Handler._contain` answers 500 when a handler raises, and for two passes that was all it did: the access
log said "500" and nothing anywhere said why. The health route's boot-window 500 (ledger row 40) survived
that silence: reproduced twice in one boot, then never again with the same traffic, so there was nothing to
attribute it to. It could be reached by hammering /api/health through a boot and nothing more.

The traceback goes to stderr, which the console tees into save/logs/console-<date>.log.
"""

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve()
SRC = HERE.parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import server  # noqa: E402  (path set above)


class _Stub:
    """Enough of a handler for `_contain`: what it answers, what it read, where it was."""

    def __init__(self, loopback=True):
        self.command = "GET"
        self.path = "/api/health"
        self.answered = []
        self._loopback = lambda: loopback

    def _json(self, code, obj):
        self.answered.append((code, obj))


class A500IsNamedInTheLogTests(unittest.TestCase):
    def _contain(self, exc, loopback=True):
        stub = _Stub(loopback)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            server.Handler._contain(stub, exc)
        return stub, err.getvalue()

    def test_the_traceback_names_the_exception_and_the_route(self):
        stub, logged = self._contain(ValueError("boom: the wide window could not be built"))
        self.assertIn("ValueError", logged)
        self.assertIn("boom: the wide window could not be built", logged)
        self.assertIn("/api/health", logged, "the log must say WHICH route failed: " + logged[:400])

    def test_the_caller_still_gets_its_500_with_the_detail(self):
        stub, _ = self._contain(RuntimeError("something specific"))
        self.assertEqual(stub.answered[0][0], 500)
        self.assertEqual(stub.answered[0][1]["error"], "handler_failed")
        self.assertIn("something specific", stub.answered[0][1]["detail"])

    def test_a_remote_caller_is_still_not_told_the_internals_but_the_log_is(self):
        stub, logged = self._contain(RuntimeError("I:\\secret\\path\\file.json"), loopback=False)
        self.assertNotIn("secret", stub.answered[0][1]["detail"])
        self.assertIn("I:\\secret\\path\\file.json", logged, "the operator's own log keeps the detail")

    def test_a_log_that_fails_is_still_a_500_and_not_a_crash(self):
        stub = _Stub()
        with mock.patch("sys.stderr", side_effect=OSError("the log device is gone")), \
                contextlib.redirect_stderr(io.StringIO()):
            server.Handler._contain(stub, ValueError("boom"))
        self.assertEqual(stub.answered[0][0], 500)


class AClientThatLeftIsNotAServerFaultTests(unittest.TestCase):
    """A page that goes away mid-answer must not paint a 500 into the operator's log.

    Measured live: closing the console tab while an answer was streaming produced
    `[500] POST /api/chat` with `ConnectionAbortedError [WinError 10053]` out of `emit_sse`.
    The browser hung up; the server did nothing wrong, and the operator's log called it a failure.
    """

    def test_a_socket_the_client_closed_is_not_answered_with_500(self):
        stub = _Stub()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            server.Handler._contain(stub, ConnectionAbortedError("[WinError 10053] the client went away"))
        self.assertEqual([c for c, _ in stub.answered], [],
                         "nobody is listening: do not answer a client that left")
        logged = err.getvalue()
        self.assertIn("client-gone", logged, "the log must say the client left: " + logged[:300])
        self.assertNotIn("Traceback", logged, "a client leaving is not an error: " + logged[:300])

    def test_a_broken_pipe_is_the_same_story(self):
        stub = _Stub()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            server.Handler._contain(stub, BrokenPipeError("the pipe is gone"))
        self.assertEqual([c for c, _ in stub.answered], [])
        self.assertIn("client-gone", err.getvalue())

    def test_a_real_server_fault_is_still_named_and_still_a_500(self):
        stub, logged = self._real_fault()
        self.assertEqual(stub.answered[0][0], 500)
        # the log names the route and the exception; it formats its own traceback text,
        # so assert the facts (route + cause), not one literal word
        self.assertIn("/api/health", logged)
        self.assertIn("RuntimeError", logged)
        self.assertIn("a real fault", logged)

    def _real_fault(self):
        stub = _Stub()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            server.Handler._contain(stub, RuntimeError("a real fault"))
        return stub, err.getvalue()


if __name__ == "__main__":

    unittest.main()
