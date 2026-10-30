"""A file path in the operator's message must never select a policy limb.

Measured 2026-09-19: "check this photo <path>" where the path was drive I:, folder "E Drive",
YOUTUBE LYGO VIDEOS, Pictures, cc988550....png matched STEWARD_HINT on the word "Drive", so
host_prefetch ran steward_map and injected a drives/policy recital before the model was called. The
steward asked about a picture and got a read_roots dump. Paths are data, not intent: mask them for
every hint test, then read the real path back for the image limbs.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import chat_loop  # noqa: E402

PHOTO = os.sep.join(
    ["I:", "E Drive", "YOUTUBE LYGO VIDEOS", "Pictures", "cc988550-5734-423d-b972-64adb03897e6.png"]
)
NOTE = os.sep.join(["I:", "E Drive", "notes", "plan.md"])
ASK = "check this photo "


class MaskPathTests(unittest.TestCase):
    def test_a_path_is_blanked_before_the_hint_regexes_see_it(self):
        self.assertNotIn("Drive", chat_loop.mask_paths(ASK + PHOTO))
        self.assertEqual(chat_loop.mask_paths(ASK + PHOTO).strip(), ASK.strip())

    def test_the_real_path_is_still_readable_after_masking(self):
        self.assertEqual(chat_loop.paths_in(ASK + PHOTO), [PHOTO])
        self.assertEqual(chat_loop.image_paths_in(ASK + PHOTO), [PHOTO])
        self.assertEqual(chat_loop.image_paths_in("open " + NOTE), [])

    def test_a_quoted_path_keeps_its_quotes_out_of_the_result(self):
        self.assertEqual(chat_loop.paths_in('look at "' + PHOTO + '"'), [PHOTO])


class PrefetchTests(unittest.TestCase):
    def setUp(self):
        self.seen = []

        def fake_dispatch(name, args, extra=None):
            self.seen.append(name)
            if name == "image_info":
                return {"ok": True, "kind": "png", "bytes": 1234, "width": 8, "height": 8}
            if name == "image_see":
                return {"ok": True, "text": "a red square", "model": "stub"}
            return {"ok": True}

        patcher = mock.patch.object(chat_loop, "dispatch", fake_dispatch)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_photo_path_runs_the_image_limbs_and_not_the_drives_limb(self):
        traces = chat_loop.host_prefetch(ASK + PHOTO)
        self.assertEqual([t["name"] for t in traces], ["image_info", "image_see"])
        self.assertNotIn("steward_map", self.seen)
        self.assertTrue(all(t.get("host") for t in traces))

    def test_a_real_drives_question_still_reaches_the_steward_map(self):
        names = [t["name"] for t in chat_loop.host_prefetch("what drives do I have on this pc?")]
        self.assertIn("steward_map", names)

    def test_an_unreadable_photo_is_reported_not_described(self):
        with mock.patch.object(chat_loop, "dispatch", lambda name, args, extra=None: {"ok": False, "error": "outside_read_roots"}):
            names = [t["name"] for t in chat_loop.host_prefetch(ASK + PHOTO)]
        self.assertEqual(names, ["image_info"])


if __name__ == "__main__":
    unittest.main()
