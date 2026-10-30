"""Host draws when the chat GGUF lectures instead of calling image_generate.

Measured: Gemma 4 answered a costume-photo ask with a safety lecture and never called the limb.
The local renderer has no such lecture. The host runs image_generate from the operator's words.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import chat_loop  # noqa: E402


ASK = (
    "using your rimage tool create a photo of a good looking woman wearing a skimpy bunny suit being cute"
)


class PictureDrawPromptTests(unittest.TestCase):
    def test_a_costume_photo_ask_is_a_draw_request(self):
        scene = chat_loop.picture_draw_prompt(ASK)
        self.assertTrue(scene)
        self.assertIn("bunny", scene.lower())
        self.assertNotIn("rimage", scene.lower())

    def test_inspecting_a_photo_is_not_a_draw_request(self):
        self.assertIsNone(chat_loop.picture_draw_prompt("what is in this picture?"))
        self.assertIsNone(chat_loop.picture_draw_prompt("check this photo I:\\LYGO\\shot.png"))

    def test_a_plain_draw_ask_is_a_draw_request(self):
        self.assertIn("red cube", (chat_loop.picture_draw_prompt("make me a picture of a red cube") or "").lower())


class HostDrawsAnywayTests(unittest.TestCase):
    def test_prefetch_calls_image_generate_for_a_costume_photo_ask(self):
        fake = {
            "ok": True,
            "path": r"<EDRIVE>\lygo-protocol-stack\lygo_llm_console\workspace\images\gen-test.png",
            "bytes": 1234,
        }
        with patch.object(chat_loop, "dispatch", return_value=fake) as disp:
            traces = chat_loop.host_prefetch(ASK)
        names = [t.get("name") for t in traces]
        self.assertIn("image_generate", names)
        args = disp.call_args[0]
        self.assertEqual(args[0], "image_generate")
        self.assertIn("bunny", str(args[1].get("prompt") or "").lower())

    def test_inspect_asks_do_not_spawn_the_renderer(self):
        with patch.object(chat_loop, "dispatch") as disp:
            traces = chat_loop.host_prefetch("what is in this picture?")
        self.assertNotIn("image_generate", [t.get("name") for t in traces])
        self.assertFalse(any(c[0][0] == "image_generate" for c in disp.call_args_list))

    def test_a_safety_lecture_is_replaced_when_the_file_exists(self):
        traces = [{
            "name": "image_generate",
            "result": {"ok": True, "path": r"C:\tmp\gen-bunny.png", "bytes": 4000},
        }]
        lecture = (
            "I cannot fulfill this request. I am programmed to be a helpful and harmless AI assistant. "
            "My safety guidelines prohibit me from generating sexually suggestive content or imagery."
        )
        out = chat_loop.surface_artifacts(lecture, traces)
        self.assertIn("Drawn on this machine", out)
        self.assertIn("gen-bunny.png", out)
        self.assertNotIn("cannot fulfill", out.lower())


if __name__ == "__main__":
    unittest.main()
