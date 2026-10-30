"""A turn that asks for nothing must be answered, not recited to - and nothing the console says to the
model may leak into the record of what the operator said.

Measured on the live console 2026-09-21 09:25 (this box, qwen2.5-coder:7b, cuda), session holding a
greeting the console had ALREADY answered with the clock - the operator's own condition after asking
"heloo?":

    newest text   shipped tail                                        3 x 4 reps
    "hi"          one clock recital behind the history                7 of 12 answers were an answer
    "thanks"      "Understood. The current time is UTC 2026-09-21T..."   - 2 recited the clock back
                  "Understood. Let's proceed with your instructions."      - 2 answered the instructions
    "you there?"  "Yes, I'm here.  NOW: - UTC: 2026-09-21T15:25:10..."     - 1 recited the clock

The volatile tail rides the newest message, so on a turn that asks nothing the model had a clock in
front of it and nothing to do with it: it handed the clock back. Given the same turns with a
conversational directive on the newest message instead: 12 of 12 answered as a person ("Hello! How can
I assist you today?"). Withholding the clock on such a turn as well measured the same 12 of 12, so the
directive is the fix and the clock is a guard that costs nothing - it cannot be recited if it is never
sent. Both live in this file's subjects; the wording of neither is an order, because a bare imperative
under a greeting is what produced the first defect (see tests/test_continuity.py).
"""
from __future__ import annotations

import json
import re
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
from chat_loop import CONVERSATIONAL_DIRECTIVE  # noqa: E402

_state: dict = {}


def _answer(text: str, tool_calls=None) -> tuple[int, bytes, str]:
    body = json.dumps(
        {
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text, "tool_calls": tool_calls},
                    "finish_reason": "stop",
                }
            ],
            "timings": {"prompt_n": 100, "prompt_per_second": 3000.0, "predicted_n": 10, "predicted_per_second": 50.0},
        }
    ).encode("utf-8")
    return 200, body, "application/json"


def setUpModule() -> None:
    _state["auth"] = server.AUTH_REQUIRED
    _state["spawn"] = server.maybe_spawn
    _state["receipt"] = server.write_receipt
    _state["session"] = server.save_session
    server.AUTH_REQUIRED = False
    server.maybe_spawn = lambda model_id=None: "ready"
    server.write_receipt = lambda **kw: {"id": "test-receipt"}
    server.save_session = lambda *a, **kw: None
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    _state["httpd"] = httpd
    _state["port"] = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def tearDownModule() -> None:
    _state["httpd"].shutdown()
    _state["httpd"].server_close()
    server.AUTH_REQUIRED = _state["auth"]
    server.maybe_spawn = _state["spawn"]
    server.write_receipt = _state["receipt"]
    server.save_session = _state["session"]


def post_json(path: str, payload: dict, timeout: float = 60.0) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = (
        f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    ).encode("ascii") + body
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


def _run_turn(text: str, extra: dict | None = None) -> tuple[dict, dict]:
    """One turn against the real handler with the engine stubbed; returns (engine payload, reply)."""
    seen: dict = {}

    def fake(*, api_key=None, payload=None, port=None, timeout=None, **_kw):
        seen.setdefault("payloads", []).append(payload)
        return _answer("Hello there.")

    body = {"messages": [{"role": "user", "content": text}], "stream": False}
    body.update(extra or {})
    with patch("openai_proxy.llama_chat", fake):
        status, raw = post_json("/api/chat", body)
    assert status == 200, raw[:400]
    return seen["payloads"][0], json.loads(raw)


class ConversationalTurnShapeTests(unittest.TestCase):
    """What the engine is told about a turn that asks nothing, and what it is NOT sent."""

    def test_a_turn_that_asks_for_nothing_is_told_to_answer_as_one_person(self):
        payload, _ = _run_turn("hi")
        newest = str(payload["messages"][-1]["content"])
        self.assertIn(
            CONVERSATIONAL_DIRECTIVE,
            newest,
            "a greeting went out with nothing telling the model to answer it as a greeting",
        )

    def test_a_turn_that_asks_for_nothing_is_not_sent_the_clock(self):
        payload, _ = _run_turn("thanks")
        joined = "\n".join(str(m.get("content") or "") for m in payload["messages"])
        self.assertNotIn(
            "NOW UTC",
            joined,
            "a turn that asks nothing still carried the clock - the model hands back what it is sent",
        )

    def test_a_turn_that_asks_for_nothing_is_still_offered_no_limbs(self):
        payload, _ = _run_turn("you there?")
        self.assertNotIn("tools", payload)

    def test_a_real_ask_still_gets_the_clock_and_no_directive(self):
        payload, _ = _run_turn("what's the weather in Tokyo?")
        newest = str(payload["messages"][-1]["content"])
        self.assertIn("NOW UTC", newest, "a real ask needs the clock")
        self.assertNotIn(
            CONVERSATIONAL_DIRECTIVE,
            newest,
            "a real ask was told it asks nothing of the model",
        )
        self.assertIn("tools", payload)

    def test_a_real_ask_with_limbs_unticked_is_not_treated_as_a_greeting(self):
        # The portal's unticked "Agent limbs" box also sets use_tools False. That must not be what
        # decides this: the directive says the turn asks nothing, which would be a lie about "17*23".
        payload, _ = _run_turn("what is 17 * 23?", {"tools": False})
        newest = str(payload["messages"][-1]["content"])
        self.assertNotIn(CONVERSATIONAL_DIRECTIVE, newest)
        self.assertIn("NOW UTC", newest)

    def test_the_flag_is_what_decides_the_shape(self):
        # The control for the two tests above: same function, one argument apart. Without this they
        # could pass on a tail that never contained a clock in the first place.
        from continuity import volatile_tail

        self.assertIn("NOW UTC", volatile_tail(with_clock=True))
        self.assertNotIn("NOW UTC", volatile_tail(with_clock=False))
        self.assertIn("world_pulse holds", volatile_tail(with_clock=False))

    def test_the_directive_names_no_limb_and_gives_no_order(self):
        from tools import core_schema

        names = {str((t.get("function") or {}).get("name") or "") for t in core_schema()}
        # WHOLE WORDS, not substrings. The check was `n in CONVERSATIONAL_DIRECTIVE`, which was sound
        # while every core limb name was long enough to be unambiguous - and silently wrong the moment
        # a short one joined: `rea` matches inside "already", "reason", "read", "create" and "are", so
        # the test reported a limb named in the directive when no limb is named (measured 2026-10-10).
        # The intent is that no limb be offered by name under a greeting (defect 30), so match the name
        # as a word and leave the directive itself alone.
        hit = sorted(n for n in names if n and re.search(rf"\b{re.escape(n)}\b", CONVERSATIONAL_DIRECTIVE))
        self.assertEqual(hit, [], "the directive names a limb - a bare order under a greeting is defect 30")


class NothingSpokenToTheModelReachesTheRecordTests(unittest.TestCase):
    """The tail and the directive are added to what the engine READS, never to what is stored."""

    def test_the_tail_and_directive_are_absent_from_the_journal_and_the_session(self):
        journalled: list[tuple] = []
        saved: list[list] = []

        def fake_engine(*, api_key=None, payload=None, port=None, timeout=None, **_kw):
            return _answer("Hello there.")

        def fake_record(role, content, meta=None):
            journalled.append((role, content))
            return {}

        def fake_save(msgs):
            saved.append([dict(m) for m in (msgs or [])])

        with patch("openai_proxy.llama_chat", fake_engine), \
                patch.object(server, "safe_record", fake_record), \
                patch.object(server, "save_session", fake_save):
            status, raw = post_json("/api/chat", {"messages": [{"role": "user", "content": "hi"}], "stream": False})
        self.assertEqual(status, 200, raw[:400])
        self.assertTrue(journalled, "no turn was journalled - this test would pass vacuously")
        self.assertEqual([c for r, c in journalled if r == "user"], ["hi"])
        self.assertTrue(saved, "the session was never written - this test would pass vacuously")
        blob = json.dumps(saved)
        self.assertNotIn(CONVERSATIONAL_DIRECTIVE, blob)
        self.assertNotIn("NOW UTC", blob)



# --- the operator's live switch must not decide this suite --------------------------------------------
# These subjects are the LOCAL turn's shape. They used to inherit whatever the operator had set in
# config/api.json, so turning the API brain on in the console made 9 of them fail while the code was
# untouched (measured both ways in one sitting: enabled true -> 9 failed, enabled false -> 16 passed).
# Pin the brain off for these files and let the cloud paths keep their own tests.
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_local_brain(monkeypatch):
    import sys
    src = Path(__file__).resolve().parent.parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    import cloud_api
    monkeypatch.setattr(cloud_api, "enabled", lambda: False, raising=False)
    _real_status = cloud_api.public_status

    def _local_only(*a, **k):
        st = dict(_real_status(*a, **k))
        st["enabled"] = False
        return st

    monkeypatch.setattr(cloud_api, "public_status", _local_only, raising=False)

if __name__ == "__main__":
    unittest.main(verbosity=2)
