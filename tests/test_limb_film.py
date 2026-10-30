"""The `film` limb as the AGENT reaches it: advertised, dispatchable, and honest when it refuses.

Δ9Φ963-LYGO-TEST-FILM-LIMB-v1

The module tests prove the console's side (routes, panes, buttons). This file proves the agent's
side: the limb is in both schemas, `dispatch("film", ...)` reaches it (so the console really can call
it, not merely advertise it), the alias pool fills the words a small model actually uses, a failure
comes back as a named dict rather than an exception, and a project outside this kit's workspace is
refused without consent — the one guard that stops an agent editing over the operator's own work.

Nothing here needs FilmCraft installed for the refusal cases, and nothing here starts an editor,
serves a page or renders a clip.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from limbs import ALIAS_POOL, CANON_KEYS, canonicalize  # noqa: E402
from tools import TOOLS_SCHEMA, core_schema, dispatch  # noqa: E402

CORE_NAMES = {(t.get("function") or {}).get("name") for t in core_schema()}
ALL_NAMES = {(t.get("function") or {}).get("name") for t in TOOLS_SCHEMA}


class FilmLimb(unittest.TestCase):
    def test_advertised_in_both_schemas(self):
        self.assertIn("film", CORE_NAMES, "the local brain cannot reach a limb it is not offered")
        self.assertIn("film", ALL_NAMES)

    def test_schema_says_what_it_can_do(self):
        entry = next(t for t in TOOLS_SCHEMA if (t.get("function") or {}).get("name") == "film")
        text = str(entry)
        # The words a small model routes on, and the two facts it must not get wrong.
        for word in ("action", "project", "out", "export", "render"):
            self.assertIn(word, text)
        self.assertIn("consent", text, "the schema must say that a foreign project needs consent")

    def test_arguments_are_canonicalised(self):
        args = canonicalize("film", {"op": "export", "outfile": "cut.mp4", "file": "demo"})[1]
        self.assertEqual(args.get("action"), "export")   # `op` -> action
        self.assertEqual(args.get("out"), "cut.mp4")     # `outfile` -> out
        self.assertTrue(args.get("project") or args.get("out"))
        self.assertIn("query", ALIAS_POOL, "a model that says `q` must still reach the command search")
        self.assertIn("project", CANON_KEYS["film"])
        self.assertIn("out", CANON_KEYS["film"])

    def test_a_given_value_is_never_overwritten(self):
        args = canonicalize("film", {"action": "render", "method": "engine.commands"})[1]
        self.assertEqual(args.get("action"), "render")
        self.assertEqual(args.get("method"), "engine.commands")

    def test_dispatch_reaches_the_limb(self):
        """The real path the console uses. A status call is read-only and always answerable."""
        out = dispatch("film", {"action": "status"})
        self.assertIsInstance(out, dict)
        self.assertIn("installed", out)
        self.assertIn("editor", out)

    def test_a_bad_action_is_named(self):
        out = dispatch("film", {"action": "definitely-not-an-action"})
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("error"), "unknown_action")
        self.assertIn("status", out.get("actions") or [])

    def test_read_only_calls_never_raise(self):
        """Every one of these is answerable on a kit with no payload, and none of them writes."""
        for args in ({"action": "doctor"}, {"action": "status"}, {"action": "presets"},
                     {"action": "commands", "query": "razor", "limit": 3},
                     {"action": "describe", "command": "timeline.razor"},
                     {"action": "inspect", "demo": True, "what": "sequence"}):
            out = dispatch("film", args)
            self.assertIsInstance(out, dict, f"{args} did not answer a dict")
            self.assertIn("ok", out, f"{args} answered without an ok field")

    def test_a_foreign_project_is_refused_without_consent(self):
        """An absolute project outside this kit's workspace/film must be refused - before the engine
        is even started - unless the caller passes consent=true. This is the guard that keeps an
        agent from reading or editing over the operator's own work."""
        for action in ("inspect", "run", "export", "render"):
            out = dispatch("film", {"action": action, "project": "C:/Windows/notepad.exe"})
            self.assertFalse(out.get("ok"), f"{action} accepted a foreign path: {out}")
            self.assertEqual(out.get("error"), "consent_required",
                             f"{action} refused with the wrong word: {out.get('error')}")
            self.assertTrue(str(out.get("why") or "").strip())

    def test_a_foreign_output_file_is_refused_without_consent(self):
        """`out` is a second way out of the workspace: without its own guard, a render could be
        written anywhere on the disk. Nothing is written here - the guard fires before the engine."""
        for action, name in (("export", "C:/Windows/lygo-test.mp4"), ("render", "C:/Windows/lygo-test.png")):
            out = dispatch("film", {"action": action, "out": name, "demo": True})
            self.assertFalse(out.get("ok"), f"{action} accepted a foreign output: {out}")
            self.assertEqual(str(out.get("error")), "consent_required")
            self.assertEqual(Path(str(out.get("file") or "")), Path(name))

    def test_the_strip_actions_the_page_posts_are_real(self):
        """The four buttons post their own action names; every one of them must exist in the limb's
        action table too, or a click and the agent would be asking different questions."""
        import film_tools

        panel = _ctrl_backend().PANEL_ACTIONS
        self.assertEqual(len(panel), 4)
        for a in panel:
            self.assertIn(a["action"], film_tools.ACTIONS,
                          f"the button posts {a['action']!r}, which the limb does not accept")
        # `web_stop` with nothing serving is the one panel action safe to call for real here: it is a
        # no-op that still has to answer. A suite must never boot an editor or serve a page.
        out = dispatch("film", {"action": "web_stop"})
        self.assertIsInstance(out, dict)
        self.assertIn("ok", out)


def _ctrl_backend():
    """The controls card's backend, imported by directory the way the host does."""
    import importlib.util

    path = ROOT / "src" / "modules" / "lygo.filmctrl" / "backend.py"
    spec = importlib.util.spec_from_file_location("film_limb_test_ctrl", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


if __name__ == "__main__":
    unittest.main()
