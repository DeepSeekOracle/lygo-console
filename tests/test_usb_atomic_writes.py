"""USB CLAW: persistence and request handling must survive a stick.

Regression for the live failure: two request threads wrote the same fixed temp name
(foo.json.tmp), os.replace raised PermissionError [WinError 32] inside the request handler,
and the client saw a reset connection.
"""
from __future__ import annotations

import http.client
import http.server
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from atomicio import atomic_write_text, read_text  # noqa: E402


def _run(workers: int, each, work) -> list[str]:
    errors: list[str] = []

    def runner(n: int) -> None:
        for i in range(each):
            try:
                work(n, i)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=runner, args=(n,)) for n in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


class TestAtomicWrite(unittest.TestCase):
    def test_concurrent_writers_never_collide(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "index.json"
            errors = _run(8, 25, lambda n, i: atomic_write_text(target, json.dumps({"n": n, "i": i})))
            self.assertEqual([], errors)
            self.assertIsInstance(json.loads(target.read_text(encoding="utf-8")), dict)
            self.assertEqual([], sorted(p.name for p in Path(td).glob("*.tmp")))

    def test_locked_target_is_retried_not_fatal(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "index.json"
            target.write_text('{"old": true}', encoding="utf-8")
            release = threading.Event()

            def hold() -> None:
                with open(target, "rb"):
                    release.wait(2.0)

            holder = threading.Thread(target=hold, daemon=True)
            holder.start()
            release.wait(0.05)
            try:
                atomic_write_text(target, '{"new": true}')
            finally:
                release.set()
                holder.join(timeout=3)
            self.assertEqual({"new": True}, json.loads(target.read_text(encoding="utf-8")))
            self.assertEqual([], sorted(p.name for p in Path(td).glob("*.tmp")))


class TestNotepadIndexUnderThreads(unittest.TestCase):
    def test_concurrent_rebuild_index_does_not_raise(self):
        import notepad

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            notes = root / "notes"
            notes.mkdir()
            (notes / "scratch.txt").write_text("scratch", encoding="utf-8")
            (notes / "alpha.txt").write_text("alpha body", encoding="utf-8")
            saved = (notepad.NOTEPAD_ROOT, notepad.NOTES_DIR, notepad.INDEX_PATH)
            notepad.NOTEPAD_ROOT, notepad.NOTES_DIR = root, notes
            notepad.INDEX_PATH = root / "index.json"
            try:
                errors = _run(8, 10, lambda n, i: notepad._rebuild_index())
                self.assertEqual([], errors)
                data = json.loads((root / "index.json").read_text(encoding="utf-8"))
                ids = sorted(n["id"] for n in data["notes"])
                self.assertIn("alpha", ids)
            finally:
                notepad.NOTEPAD_ROOT, notepad.NOTES_DIR, notepad.INDEX_PATH = saved


class TestRequestContainment(unittest.TestCase):
    """A failing request must answer 500 and leave the console serving."""

    @classmethod
    def setUpClass(cls):
        import server

        cls.server = server
        cls.handler = next(
            v for v in vars(server).values()
            if isinstance(v, type) and v is not http.server.BaseHTTPRequestHandler
            and issubclass(v, http.server.BaseHTTPRequestHandler) and v.__module__ == server.__name__
        )

    def test_handle_error_never_raises_on_non_ascii(self):
        srv = self.server._StickContainment(("127.0.0.1", 0), self.handler)
        try:
            try:
                raise ValueError("\u03949\u03a6963 \u2014 stick console")
            except ValueError:
                srv.handle_error(None, ("127.0.0.1", 4242))
        finally:
            srv.server_close()

    def test_dispatch_exception_answers_500_and_server_survives(self):
        original = self.handler._dispatch_GET

        def boom(self):  # noqa: ANN001
            raise RuntimeError("simulated handler failure \u0394")

        self.handler._dispatch_GET = boom
        srv = self.server._StickContainment(("127.0.0.1", 0), self.handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            for _ in range(2):  # second call proves the server survived the first failure
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
                try:
                    conn.request("GET", "/api/health")
                    resp = conn.getresponse()
                    body = resp.read().decode("utf-8", "replace")
                    self.assertEqual(500, resp.status, body)
                    self.assertIn("handler_failed", body)
                finally:
                    conn.close()
        finally:
            self.handler._dispatch_GET = original
            self.server.STATE["shutdown"] = True
            srv.shutdown()
            srv.server_close()


class TestIndexIsNotRewrittenWhenUnchanged(unittest.TestCase):
    def test_unchanged_notes_do_not_rewrite_the_index(self):
        import notepad

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            notes = root / "notes"
            notes.mkdir()
            (notes / "a.txt").write_text("A", encoding="utf-8")
            saved = (notepad.NOTEPAD_ROOT, notepad.NOTES_DIR, notepad.INDEX_PATH)
            notepad.NOTEPAD_ROOT, notepad.NOTES_DIR = root, notes
            notepad.INDEX_PATH = root / "index.json"
            try:
                # production path: list_notes() -> ensure() makes scratch.txt before any rebuild
                notepad.ensure()
                notepad._rebuild_index()
                first = (notepad.INDEX_PATH.stat().st_mtime_ns,
                         notepad.INDEX_PATH.read_text(encoding="utf-8"))
                for _ in range(5):
                    notepad._rebuild_index()
                second = (notepad.INDEX_PATH.stat().st_mtime_ns,
                          notepad.INDEX_PATH.read_text(encoding="utf-8"))
                self.assertEqual(first, second, "unchanged notes must not rewrite the index")
                (notes / "b.txt").write_text("B", encoding="utf-8")
                notepad._rebuild_index()
                self.assertNotEqual(second[0], notepad.INDEX_PATH.stat().st_mtime_ns,
                                    "a new note must rewrite the index")
            finally:
                notepad.NOTEPAD_ROOT, notepad.NOTES_DIR, notepad.INDEX_PATH = saved


class TestWritersSurviveReaders(unittest.TestCase):
    def test_writer_is_not_broken_by_concurrent_readers(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "index.json"
            target.write_text("{}", encoding="utf-8")
            stop = threading.Event()
            read_errors: list[str] = []

            # The console never calls Path.read_text on a state file: a bare open() can be
            # denied by the OS for the instant a swap is in flight (measured), so reads go
            # through atomicio.read_text, which waits out a transient denial.
            def reader() -> None:
                while not stop.is_set():
                    try:
                        read_text(target)
                    except Exception as exc:  # noqa: BLE001
                        read_errors.append(f"{type(exc).__name__}: {exc}")
                    time.sleep(0.02)  # a person refreshing, not a hot loop

            readers = [threading.Thread(target=reader, daemon=True) for _ in range(2)]
            for t in readers:
                t.start()
            try:
                write_errors = _run(4, 20, lambda n, i: atomic_write_text(
                    target, json.dumps({"n": n, "i": i})))
            finally:
                stop.set()
                for t in readers:
                    t.join(timeout=5)
            self.assertEqual([], write_errors)
            self.assertEqual([], read_errors, "the console's read path must never fail")
            json.loads(target.read_text(encoding="utf-8"))



class TestReadTextRetriesTransientDenials(unittest.TestCase):
    """The read side of the same defect: a denial in flight must not reach the caller."""

    def test_read_text_waits_out_a_transient_denial(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "index.json"
            target.write_text('{"notes": []}', encoding="utf-8")
            calls = {"n": 0}
            real = Path.read_text

            def flaky(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
                calls["n"] += 1
                if calls["n"] == 1:
                    raise PermissionError(13, "Permission denied")
                return real(self, *args, **kwargs)

            Path.read_text = flaky
            try:
                self.assertEqual('{"notes": []}', read_text(target))
            finally:
                Path.read_text = real
            self.assertGreaterEqual(calls["n"], 2, "a transient denial must be retried")

    def test_read_text_does_not_stall_on_a_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            started = time.monotonic()
            with self.assertRaises(FileNotFoundError):
                read_text(Path(td) / "absent.json")
            self.assertLess(time.monotonic() - started, 0.5,
                            "missing is not transient: no backoff may be burned")


if __name__ == "__main__":
    unittest.main()
