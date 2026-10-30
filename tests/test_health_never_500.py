"""The route the operator reads state from may degrade, never 500.

Measured on this tree 2026-09-21, two boots: `GET /api/health -> 500` once while the engine was still
loading (five polled 200s, one 500, then 200s again - the 500 body went to a startup poll that threw it
away) and once as the very last line the console logged before the process went away. The operator's own
console ended its log the same way at 00:16 that morning, which is why it read as "the console died".

The body carries the exception and the handler only sends it to a loopback caller, so neither cause was
ever readable. It does not need to be: the answer already advertises an `error` field, and every probe
that reads the machine (session state, drives, RAM, backend layer, perf, registry, cloud) is one call
away from raising on a box where the stick can be unplugged mid-read. A partial answer is what the
operator is looking at this route for.

So: one failing probe blanks its own field and is named in `degraded`; the route answers 200 either way.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import server  # noqa: E402

_state: dict = {}


def setUpModule() -> None:
    _state["auth"] = server.AUTH_REQUIRED
    _state["spawn"] = server.maybe_spawn
    server.AUTH_REQUIRED = False
    server.maybe_spawn = lambda model_id=None: "ready"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    _state["httpd"] = httpd
    _state["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def tearDownModule() -> None:
    _state["httpd"].shutdown()
    _state["httpd"].server_close()
    server.AUTH_REQUIRED = _state["auth"]
    server.maybe_spawn = _state["spawn"]


def get_json(path: str, timeout: float = 60.0) -> tuple[int, str]:
    req = (
        f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: application/json\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    with socket.create_connection(("127.0.0.1", _state["port"]), timeout=timeout) as s:
        s.sendall(req)
        out = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            out += chunk
    head, _, rest = out.partition(b"\r\n\r\n")
    return int(head.split(b" ")[1]), rest.decode("utf-8", "replace")


class HealthNeverFailsTests(unittest.TestCase):
    def test_a_healthy_answer_names_nothing_degraded(self):
        status, body = get_json("/api/health")
        self.assertEqual(status, 200, body[:300])
        obj = json.loads(body)
        for key in ("ok", "build", "brain", "system", "ram_avail", "perf", "tools", "engine_present"):
            self.assertIn(key, obj, f"the health answer lost its {key!r} field")
        self.assertEqual(obj.get("degraded"), [], "a healthy boot reported a degraded probe")

    def test_one_failing_probe_blanks_only_its_own_field(self):
        import surface

        with patch.object(surface, "report", side_effect=RuntimeError("drive vanished mid-read")):
            status, body = get_json("/api/health")
        self.assertEqual(status, 200, f"one failing probe took the whole route down: {body[:300]}")
        obj = json.loads(body)
        self.assertTrue(obj.get("ok"))
        self.assertEqual(obj.get("system"), {}, "the failing field kept a value it could not read")
        self.assertIn("brain", obj, "the rest of the answer must survive one bad probe")
        named = [d for d in obj.get("degraded") or [] if d.get("field") == "system"]
        self.assertTrue(named, "the failing probe was not named in `degraded`")
        self.assertIn("RuntimeError", json.dumps(named))

    def test_every_live_probe_can_fail_without_a_500(self):
        import backends
        import cloud_api
        import engine
        import paths
        import perf
        import registry
        import surface
        import version

        cases = [
            (surface, "report"),
            (backends, "report"),
            (perf, "report"),
            (engine, "available_ram_bytes"),
            (registry, "prefer_by_ram"),
            (registry, "ram_choice"),
            (registry, "selected_vision"),
            (cloud_api, "public_status"),
            (version, "release"),
            (version, "tag"),
            (paths, "console_limits"),
            (paths, "engine_dir"),
        ]
        for mod, attr in cases:
            with self.subTest(probe=f"{mod.__name__}.{attr}"):
                with patch.object(mod, attr, side_effect=OSError(f"{attr} unavailable")):
                    status, body = get_json("/api/health")
                self.assertEqual(
                    status, 200, f"{mod.__name__}.{attr} raised and the route answered {status}"
                )
                self.assertTrue(json.loads(body).get("ok"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
