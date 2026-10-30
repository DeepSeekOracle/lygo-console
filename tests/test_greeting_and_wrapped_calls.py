"""A turn that asks for nothing must not become a tool errand, and a call the model wraps in
<tools>...</tools> must still run.

Measured on this box 2026-09-21, qwen2.5-coder:7b, the console's own local schema (24 limbs), the
identity block and the volatile tail exactly as the console sends them:

    operator text          what the engine answered             how often
    "hi"                   a world_pulse call, whole reply       3 of 3
    "heloo?"               a world_pulse call                   2 of 3
    "explain recursion"    prose, no call                        3 of 3
    "what is 17 * 23?"     a calc call                          3 of 3
    "weather in Tokyo?"    a weather call - wrapped in <tools>  2 of 3
    "what time is it?"     now / world_pulse                    3 of 3

So the model routes real asks correctly and acts on greetings anyway: offered 24 limbs on a turn that
asks for nothing, it reaches for one - and the console then runs it and answers the greeting with a
city-clock report. The wording of the tail is not the lever: with the order removed and the clock left
alone, 4 of 8 turns still called `now`. What is wrong is offering the limbs at all on such a turn.

The second half of the same measurement: 2 of 3 weather calls arrived inside <tools></tools>, a
wrapper nothing parsed, so the limb the model asked for never ran and the operator was shown raw JSON.
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


def _answer(text: str, tool_calls=None) -> tuple[int, bytes, str]:
    body = json.dumps(
        {
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": text, "tool_calls": tool_calls}, "finish_reason": "stop"}
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


class TurnClassifierTests(unittest.TestCase):
    """The rule itself: strict, so a real ask that merely opens with a greeting keeps its limbs."""

    def test_greetings_and_acknowledgements_ask_for_nothing(self):
        from chat_loop import is_conversational

        for text in ("heloo?", "hi", "hello", "Hey!", "yo", "good morning", "thanks", "thank you",
                     "ok", "cool", "lol", "you there?", "  hi  "):
            self.assertTrue(is_conversational(text), f"{text!r} should be a turn that asks for nothing")

    def test_anything_with_a_task_in_it_is_not_one(self):
        from chat_loop import is_conversational

        for text in ("what time is it?", "hi, what's the weather in Tokyo?", "explain recursion in one line",
                     "17*23", "weather", "hi " * 40, "", "   "):
            self.assertFalse(is_conversational(text), f"{text!r} carries a task or is empty")


class GreetingGetsNoLimbsTest(unittest.TestCase):
    """What the engine is offered is what the engine can call."""

    def _turn(self, text: str) -> tuple[dict, dict]:
        seen: dict = {}

        def fake(*, api_key=None, payload=None, port=None, timeout=None, **_kw):
            seen["payload"] = payload
            return _answer("Hello there.")

        with patch("openai_proxy.llama_chat", fake):
            status, raw = post_json("/api/chat", {"messages": [{"role": "user", "content": text}], "stream": False})
        self.assertEqual(status, 200, raw[:400])
        return seen["payload"], json.loads(raw)

    def test_a_greeting_is_offered_no_limbs(self):
        payload, obj = self._turn("heloo?")
        self.assertNotIn(
            "tools",
            payload,
            "a greeting was offered the whole limb schema - measured: the model answers 'hi' with a world_pulse call 3 of 3",
        )
        self.assertIn("Hello", obj["text"])

    def test_a_short_acknowledgement_is_offered_no_limbs(self):
        payload, _ = self._turn("hi")
        self.assertNotIn("tools", payload)

    def test_a_real_ask_still_carries_the_limbs(self):
        payload, _ = self._turn("what's the weather in Tokyo?")
        self.assertIn("tools", payload, "a real ask must keep every limb")
        self.assertTrue(payload["tools"])


class WrappedCallTests(unittest.TestCase):
    """<tools>{...}</tools> is a shape this model really emits (2 of 3 weather calls), never parsed."""

    def test_a_call_wrapped_in_tools_is_understood(self):
        from chat_loop import extract_tool_calls

        got = extract_tool_calls({}, '<tools>\n  {"name": "weather", "arguments": {"place": "Tokyo"}}\n</tools>')
        self.assertEqual([c["name"] for c in got], ["weather"])
        self.assertEqual(got[0]["arguments"], {"place": "Tokyo"})

    def test_a_call_wrapped_in_tool_call_is_still_understood(self):
        from chat_loop import extract_tool_calls

        got = extract_tool_calls({}, '<tool_call>\n{"name": "weather", "arguments": {"place": "Tokyo"}}\n</tool_call>')
        self.assertEqual([c["name"] for c in got], ["weather"])

    def test_the_console_runs_a_call_wrapped_in_tools(self):
        def fake(*, api_key=None, payload=None, port=None, timeout=None, **_kw):
            msgs = payload.get("messages") or []
            if any("Tool results" in str(m.get("content") or "") for m in msgs):
                return _answer("It is 00:30 on the host.")
            return _answer('<tools>\n{"name": "now", "arguments": {}}\n</tools>')

        with patch("openai_proxy.llama_chat", fake):
            status, raw = post_json(
                "/api/chat", {"messages": [{"role": "user", "content": "what time is it?"}], "stream": False}
            )
        self.assertEqual(status, 200, raw[:400])
        obj = json.loads(raw)
        self.assertEqual(
            [t.get("name") for t in obj.get("traces") or []],
            ["now"],
            "the limb the model asked for never ran - the operator was shown raw JSON instead",
        )



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
