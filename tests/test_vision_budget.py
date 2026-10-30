"""A photo must cost what it costs, and a turn must never be handed to the engine over its window.

Measured on this host 2026-09-21, gemma4-12b (mmproj loaded, `loaded multimodal model,
'<MODELS>\\gemma4-12b-mmproj.gguf'`), the operator's own console, 32,768-token window:

    the turn the operator asked with a photo attached came back empty, and the console printed
        [turn] blank answer: ... cur_text=0 chars
    while the engine's own log said
        E srv send_error: task id = 31, error: request (133868 tokens) exceeds the available
          context size (32768 tokens), try increasing it

So the console sent ~134k tokens into a 32,768 window and had no idea. Why: `trim_messages` bills a
message with `compaction.est_tokens`, which charges an image part a FLAT 900 tokens no matter what the
picture is (`compaction.py`, `content_tokens`), and it exempts the newest messages from the budget
entirely (`idx < keep`). A projector is charged per patch of pixels, so a 1024x1536 photo is thousands
of tokens and a phone photo is tens of thousands - the console's estimate was off by more than an
order of magnitude, and nothing could refuse. The engine answered nothing, and the operator saw an
empty bubble.

The rule this file pins: an image part is priced from its pixels, the newest picture is downscaled to
what the projector can afford, older pictures are shed to text rather than dragging the whole
conversation out of the window, and the assembled request is NEVER over the window - if it somehow
would be, the console says so in words instead of sending it.
"""
from __future__ import annotations

import base64
import json
import socket
import struct
import sys
import threading
import unittest
import zlib
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import server  # noqa: E402
import vision  # noqa: E402

_state: dict = {}


def png(size: int) -> bytes:
    """A real PNG of `size` x `size`, built without any third-party import."""
    w = h = size
    row = b"\x00" + b"".join(bytes([(x * 3) % 256, 96, 160]) for x in range(w))
    raw = row * h

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


def data_url(size: int) -> str:
    return "data:image/png;base64," + base64.b64encode(png(size)).decode("ascii")


def image_part(size: int) -> dict:
    return {"type": "image_url", "image_url": {"url": data_url(size)}}


def dims_of(part: dict) -> tuple[int, int] | None:
    url = str((part.get("image_url") or {}).get("url") or "")
    if not url.startswith("data:"):
        return None
    return vision.image_size(base64.b64decode(url.split(",", 1)[1]))


def _answer(text: str) -> tuple[int, bytes, str]:
    body = json.dumps(
        {"choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
         "timings": {"prompt_n": 100, "prompt_per_second": 3000.0, "predicted_n": 10, "predicted_per_second": 50.0}}
    ).encode("utf-8")
    return 200, body, "application/json"


def setUpModule() -> None:
    # The operator's live config/api.json is MACHINE state (gitignored), and this module measures the
    # ENGINE: with a cloud provider saved and enabled the console rightly prefers it, the mocked engine
    # is never called, and ten tests about timings and photo fitting fail on a correct working tree.
    # A test that measures the engine pins the brain to the engine, whatever the operator last chose.
    import cloud_api

    # server.py decides with `bool(st_cloud.get("enabled") and has_key)` from public_status(), so THAT
    # is the seam to pin - patching cloud_api.active() looked right and changed nothing.
    _state["cloud_mod"] = cloud_api
    _state["cloud_status"] = cloud_api.public_status
    _quiet = dict(cloud_api.public_status() or {})
    _quiet.update({"enabled": False, "has_key": False, "degraded": False})
    cloud_api.public_status = lambda: dict(_quiet)

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
    _state["cloud_mod"].public_status = _state["cloud_status"]

    _state["httpd"].shutdown()
    _state["httpd"].server_close()
    server.AUTH_REQUIRED = _state["auth"]
    server.maybe_spawn = _state["spawn"]
    server.write_receipt = _state["receipt"]
    server.save_session = _state["session"]


def post_json(path: str, payload: dict, timeout: float = 120.0) -> tuple[int, str]:
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


class SizeIsReadFromTheFileTests(unittest.TestCase):
    """No dependency on any imaging library to know how big a picture is."""

    def test_png_dimensions_come_from_the_header(self):
        self.assertEqual(vision.image_size(png(64)), (64, 64))
        self.assertEqual(vision.image_size(png(300)), (300, 300))

    def test_a_non_image_is_not_guessed_at(self):
        self.assertIsNone(vision.image_size(b"not a picture at all"))

    def test_measuring_does_not_need_pil(self):
        with patch.dict(sys.modules, {"PIL": None, "PIL.Image": None}):
            self.assertEqual(vision.image_size(png(32)), (32, 32))


class APhotoCostsWhatItCostsTests(unittest.TestCase):
    """The engine bills per patch of pixels; the estimate has to agree with that, not with 900."""

    def test_an_image_part_is_priced_from_its_pixels(self):
        small = vision.est_image_tokens(image_part(256))
        big = vision.est_image_tokens(image_part(1024))
        self.assertGreater(small, 300, "a 256x256 photo is not a free message")
        self.assertGreater(big, 5000, "a 1024x1024 photo costs thousands of tokens, not 900")
        self.assertAlmostEqual(big / small, 16.0, delta=3.0,
                               msg="cost must follow area: a 1024 square is 16 of a 256 square")

    def test_a_phone_sized_photo_is_priced_as_thousands(self):
        self.assertGreater(vision.est_image_tokens(image_part(2048)), 5000)

    def test_the_budget_sees_it(self):
        from compaction import est_tokens

        flat = est_tokens([{"type": "text", "text": "hello"}])
        with_photo = est_tokens([{"type": "text", "text": "hello"}, image_part(1024)])
        self.assertGreater(with_photo, 5000,
                           "a content list with a photo must not be billed as prose + 900")
        self.assertGreater(with_photo, flat * 100)

    def test_text_is_untouched(self):
        from compaction import est_tokens

        self.assertEqual(est_tokens("hello"), 2)


class TheRequestCanNeverBeOverTheWindowTests(unittest.TestCase):
    """The guarantee the empty bubble cost us: the console never sends what cannot fit."""

    def test_one_giant_photo_is_downscaled_into_the_window(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "what is this?"}, image_part(3000)]}]
        plan = vision.fit_turn(msgs, window=32768, system_tokens=7000)
        self.assertTrue(plan["changed"], "a 3000x3000 photo cannot go to a 32k window as-is")
        est = vision.est_prompt_tokens(plan["messages"], system_tokens=7000)
        self.assertLessEqual(est, 32768, f"assembled request still over the window: {est}")

    def test_the_newest_picture_stays_a_picture(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "what is this?"}, image_part(3000)]}]
        plan = vision.fit_turn(msgs, window=32768, system_tokens=7000)
        newest = plan["messages"][-1]["content"]
        parts = [p for p in newest if p.get("type") == "image_url"]
        self.assertEqual(len(parts), 1, "the operator's own picture must still be looked at")
        w, h = dims_of(parts[0])
        self.assertLessEqual(max(w, h), vision.MAX_IMAGE_SIDE)
        self.assertAlmostEqual(w / h, 1.0, delta=0.05, msg="aspect ratio must survive the shrink")

    def test_older_pictures_are_shed_to_text_not_dropped_from_the_conversation(self):
        msgs = []
        for n in range(4):
            msgs.append({"role": "user", "content": [
                {"type": "text", "text": f"photo {n} please look"}, image_part(1024)]})
            msgs.append({"role": "assistant", "content": f"seen {n}"})
        plan = vision.fit_turn(msgs, window=32768, system_tokens=7000)
        images = [p for m in plan["messages"] for p in (m.get("content") or [])
                  if isinstance(p, dict) and p.get("type") == "image_url"]
        self.assertEqual(len(images), 1, "only the newest picture is worth the window")
        text = json.dumps(plan["messages"])
        self.assertIn("photo 0 please look", text, "the words of older turns must survive")
        self.assertIn("seen 3", text)
        self.assertIn("[image", text)

    def test_a_pathological_turn_is_refused_in_words_not_sent(self):
        """Even after every shed and shrink, a turn that cannot fit must be answered, not attempted."""
        msgs = [{"role": "user", "content": [{"type": "text", "text": "x" * 200000}, image_part(2048)]}]
        plan = vision.fit_turn(msgs, window=8192, system_tokens=7000)
        self.assertTrue(plan["over"], "this turn cannot fit any window this size")
        self.assertTrue(plan["note"], "the operator must be told why, in words")

    def test_long_history_is_trimmed_instead_of_refused(self):
        """Defect 126: a ~10k conversation against a small window must still generate."""
        msgs = []
        for n in range(40):
            msgs.append({"role": "user", "content": f"turn {n} " + ("hello " * 80)})
            msgs.append({"role": "assistant", "content": f"ok {n} " + ("yes " * 80)})
        msgs.append({"role": "user", "content": "look at this newest question about the photo"})
        plan = vision.fit_turn(msgs, window=2048, system_tokens=400, reserve=200)
        self.assertFalse(plan["over"], "history that outgrew the window must be trimmed, not refused")
        self.assertGreater(plan.get("trimmed") or 0, 0)
        text = json.dumps(plan["messages"])
        self.assertIn("newest question", text)
        est = vision.est_prompt_tokens(plan["messages"], system_tokens=400) + 200
        self.assertLessEqual(est, 2048)

    def test_a_ten_k_turn_fits_the_raised_window(self):
        """The operator's live failure: ~9,972 tokens against 2,048. 32,768 holds it."""
        msgs = [{"role": "user", "content": "x" * int(5500 * 3.6)}]
        plan = vision.fit_turn(msgs, window=32768, system_tokens=4400, reserve=4096)
        self.assertFalse(plan["over"], "identity plus a 5.5k turn must fit the raised 32k window")
        self.assertLessEqual(plan["used_tokens"], 32768)

    def test_the_estate_is_bounded_whatever_is_thrown_at_it(self):
        cases = [
            [{"role": "user", "content": [{"type": "text", "text": "a"}, image_part(4096)]}],
            [{"role": "user", "content": [{"type": "text", "text": "a"}, image_part(1024), image_part(1024)]}],
            [{"role": "user", "content": image_part(2048)}],
            [{"role": "user", "content": [{"type": "text", "text": "y" * 50000}]}],
        ]
        for msgs in cases:
            with self.subTest(msgs=str(msgs)[:60]):
                plan = vision.fit_turn(msgs, window=32768, system_tokens=7000)
                est = vision.est_prompt_tokens(plan["messages"], system_tokens=7000)
                self.assertTrue(plan["over"] or est <= 32768, f"sent anyway: {est}")


class TheTurnTheOperatorActuallyMakesTests(unittest.TestCase):
    """Through the console's own route: what the engine is handed is what fits."""

    def _send(self, messages: list[dict], max_tokens: int = 64) -> tuple[dict, dict, int, str]:
        seen: dict = {}

        def fake(*, api_key=None, payload=None, port=None, timeout=None, **_kw):
            seen["payload"] = payload
            return _answer("I can see a striped test picture.")

        with patch("openai_proxy.llama_chat", fake):
            status, raw = post_json("/api/chat", {"messages": messages, "stream": False,
                                                  "max_tokens": max_tokens, "tools": False})
        self.assertIn("payload", seen, f"the engine was never called: {raw[:300]}")
        return seen["payload"], json.loads(raw), status, raw

    def test_a_photo_turn_goes_to_the_engine_with_the_photo_fitted(self):
        payload, obj, status, raw = self._send(
            [{"role": "user", "content": [{"type": "text", "text": "what is in this picture?"}, image_part(2600)]}])
        self.assertEqual(status, 200, raw[:400])
        parts = [p for m in payload["messages"] for p in (m.get("content") or [])
                 if isinstance(p, dict) and p.get("type") == "image_url"]
        self.assertEqual(len(parts), 1)
        w, h = dims_of(parts[0])
        self.assertLessEqual(max(w, h), vision.MAX_IMAGE_SIDE,
                             "the engine was handed the photo at its original size")

    def test_the_answer_is_not_empty_when_the_window_is_tiny(self):
        """The window is the model's; the console answers in words instead of going blank.

        This is the operator's own symptom: the console sent a turn the engine could not fit, the engine
        answered nothing, and the page printed "the engine sent no text for this turn". Now the turn is
        refused HERE, with a reason - and the engine is not asked at all.
        """
        called: dict = {}

        def fake(*, api_key=None, payload=None, port=None, timeout=None, **_kw):
            called["yes"] = True
            return _answer("this should never be reached")

        with patch("server.live_ctx", lambda *a, **k: 2048), patch("openai_proxy.llama_chat", fake):
            status, raw = post_json("/api/chat", {"messages": [{"role": "user", "content": [
                {"type": "text", "text": "what is in this picture?"}, image_part(2600)]}],
                "stream": False, "tools": False, "max_tokens": 64})
        self.assertEqual(status, 200, raw[:400])
        said = str(json.loads(raw).get("text") or "")
        self.assertTrue(said.strip(), "an unanswerable photo turn must still say something")
        self.assertIn("window", said.lower(), "the operator must be told why in plain words")
        self.assertNotIn("yes", called, "a turn the engine cannot fit was sent anyway")


class TheTailMustNotDestroyThePictureTests(unittest.TestCase):
    """The defect that broke image reading on this box: `str()` of a content LIST.

    Measured 2026-09-21 on the operator's console (gemma4-12b, projector loaded): every turn with a
    photo was assembled with `str(content)`, which renders the parts as Python repr - the picture's
    whole base64 data URL, as text. The image part was destroyed, a 200 KB JPEG became ~74,000 tokens
    of garbage, and the engine refused the turn whole ("request (133868 tokens) exceeds the available
    context size (32768 tokens)") while the console printed `[turn] blank answer ... cur_text=0 chars`.
    """

    def _send(self, messages: list[dict]) -> dict:
        seen: dict = {}

        def fake(*, api_key=None, payload=None, port=None, timeout=None, **_kw):
            seen["payload"] = payload
            return _answer("A striped test picture.")

        with patch("openai_proxy.llama_chat", fake):
            status, raw = post_json("/api/chat", {"messages": messages, "stream": False,
                                                  "tools": False, "max_tokens": 64})
        self.assertEqual(status, 200, raw[:300])
        self.assertIn("payload", seen, "the engine was never called")
        return seen["payload"]

    def test_the_picture_survives_the_tail(self):
        payload = self._send([{"role": "user", "content": [
            {"type": "text", "text": "what is in this picture?"}, image_part(512)]}])
        newest = payload["messages"][-1]
        self.assertIsInstance(newest.get("content"), list,
                              "the tail stringified the content parts and killed the picture")
        images = [p for p in newest["content"] if p.get("type") == "image_url"]
        self.assertEqual(len(images), 1, "the picture did not reach the engine as a picture")

    def test_no_base64_is_pasted_into_the_prompt_as_text(self):
        payload = self._send([{"role": "user", "content": [
            {"type": "text", "text": "what is in this picture?"}, image_part(512)]}])
        text = " ".join(str(p.get("text") or "")
                        for m in payload["messages"] for p in (m.get("content") or [])
                        if isinstance(p, dict))
        self.assertNotIn("base64", text)
        self.assertLess(len(text), 20000, "the prompt is a wall of base64 text again")

    def test_the_tail_still_reaches_a_plain_text_turn(self):
        payload = self._send([{"role": "user", "content": "hello there"}])
        newest = payload["messages"][-1]
        self.assertIsInstance(newest.get("content"), str)
        self.assertTrue(newest["content"].strip())


class TheLimbPathSharesTheRuleTests(unittest.TestCase):
    """image_see reads a file off disk: the same projector, the same limit."""

    def test_the_data_url_for_a_file_is_fitted_too(self):
        big = ROOT / "workspace" / "vision_test_big.png"
        big.write_bytes(png(1400))
        try:
            url = vision.data_url_for_file(big)
            raw = base64.b64decode(str(url).split(",", 1)[1])
            w, h = vision.image_size(raw)
            self.assertLessEqual(max(w, h), vision.MAX_IMAGE_SIDE)
        finally:
            big.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
