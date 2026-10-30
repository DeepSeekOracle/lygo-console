"""The side-agent limbs: a task handed out really runs, and the refusals are by NAME.

`subagent_run` drives an EXTERNAL agent loop (OpenCode) instead of calling a chat endpoint,
because the free model pool it aims at is client-locked - the same endpoint answers 403
`FreeTierError` from outside that client. Four things this pins, in order of how much they matter:

1. **The read-only control actually denies.** It is set through `OPENCODE_PERMISSION` as the
   documented OBJECT MAP. A list-of-`{permission,pattern,action}` passes a JSON round-trip and looks
   right in the source, and is SILENTLY IGNORED by the CLI - measured live: a `read_only=True` run
   still used the `write` tool and left a file on disk. The shape is pinned here so the control
   cannot quietly stop working again; the attempt-on-disk is proven by the live probe, not by a unit
   test, and the code says so.
2. **Every refusal is by NAME**, with a remedy: no task, no CLI, a refused directory, an engine that
   answers with the masked error, a timeout. Never a silent prose answer.
3. **The masked error is translated, not echoed.** An exhausted free cap, a rate limit and a retired
   model id all arrive as "Unexpected server error" - the limb says exactly that and points at the
   probe, instead of reporting "the free tier is down" (a diagnosis the evidence does not support).
4. **The directory handed to the CLI is a real absolute Windows path.** Launched from a POSIX-style
   path the CLI resolves it wrongly, fails to lstat it, and dies with that same masked error.

Nothing here touches the network, needs the CLI installed, or spends a request.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import delegate_tools  # noqa: E402
import limbs  # noqa: E402
from tools import TOOLS_SCHEMA  # noqa: E402


class LimbIsReachable(unittest.TestCase):
    def test_the_brain_is_offered_both_limbs(self):
        names = {(t.get("function") or {}).get("name") for t in TOOLS_SCHEMA}
        self.assertIn("subagent_run", names)
        self.assertIn("subagent_pool", names)

    def test_the_dispatcher_has_a_branch_for_each(self):
        src = Path(limbs.__file__).read_text(encoding="utf-8")
        self.assertIn('name == "subagent_run"', src)
        self.assertIn('name == "subagent_pool"', src)

    def test_the_limb_is_offered_to_the_on_box_brain(self):
        """Side agents exist so the agent can take a second opinion or split work - a limb the on-box
        brain cannot call defeats that. They were held out to protect a core budget the steward removed
        on 2026-10-16 ("REMOVE ALL THE CAPS"); a capability the operator asks for belongs in the set the
        brain actually sees."""
        from tools import CORE_NAMES

        self.assertIn("subagent_run", tuple(CORE_NAMES))
        self.assertIn("subagent_pool", tuple(CORE_NAMES))


class ReadOnlyIsTheObjectMap(unittest.TestCase):
    def test_the_read_only_env_is_the_documented_shape(self):
        env = delegate_tools._read_only_env({})
        parsed = json.loads(env["OPENCODE_PERMISSION"])
        self.assertIsInstance(parsed, dict, "the CLI ignores the list shape - see the module docstring")
        self.assertEqual(parsed.get("edit"), "deny")
        self.assertEqual(parsed.get("bash"), "deny")
        self.assertEqual(parsed.get("webfetch"), "deny")

    def test_a_write_run_leaves_the_env_alone(self):
        env = delegate_tools._env_for("opencode/deepseek-v4.1-flash-free", delegate_tools._roster())
        self.assertNotIn("OPENCODE_PERMISSION", env)


class RefusalsAreByName(unittest.TestCase):
    def test_empty_task(self):
        out = delegate_tools.subagent_run(task="   ")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "empty_task")

    def test_missing_cli_says_how_to_get_it(self):
        original = delegate_tools._exe
        delegate_tools._exe = lambda: None
        try:
            out = delegate_tools.subagent_run(task="look at this")
        finally:
            delegate_tools._exe = original
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "opencode_missing")
        self.assertIn("npm i -g opencode-ai", out["hint"])

    def test_a_directory_outside_the_workspace_needs_consent(self):
        out = delegate_tools.subagent_run(task="look at this", dir="C:/Windows/System32")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "dir_outside_needs_consent")
        self.assertIn("consent=true", out["hint"])

    def test_consent_lets_it_out_of_the_workspace(self):
        import shutil
        import tempfile

        outside = Path(tempfile.gettempdir()) / "lygo_side_outside_probe"
        try:
            path, err = delegate_tools._run_dir(str(outside), consent=True)
            self.assertEqual(err, "")
            self.assertEqual(str(path).lower(), str(outside.resolve()).lower())
        finally:
            shutil.rmtree(outside, ignore_errors=True)


class MaskedErrorsAreTranslated(unittest.TestCase):
    def _with_capture(self, code, stdout, stderr=""):
        original = delegate_tools._capture
        delegate_tools._capture = lambda *a, **k: (code, stdout, stderr, False)
        try:
            return delegate_tools.subagent_run(task="do a thing", engine="opencode/some-free-model")
        finally:
            delegate_tools._capture = original

    def test_the_opaque_error_is_named_and_not_misdiagnosed(self):
        stream = json.dumps({"type": "error", "error": {
            "name": "UnknownError",
            "data": {"message": "Unexpected server error. Check server logs for details.",
                     "ref": "err_deadbeef"}}})
        out = self._with_capture(1, stream)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "engine_masked_error")
        self.assertIn("err_deadbeef", json.dumps(out["engine_error"]))
        self.assertIn("probe", out["hint"].lower())
        self.assertNotIn("free tier is down", json.dumps(out).lower())

    def test_a_good_run_returns_the_answer_and_its_tools(self):
        stream = "\n".join([
            json.dumps({"type": "text", "part": {"text": "I read the file and it says hello."}}),
            json.dumps({"type": "tool", "part": {"tool": "read"}}),
            json.dumps({"type": "tool", "part": {"tool": "grep"}}),
        ])
        out = self._with_capture(0, stream)
        self.assertTrue(out["ok"], out)
        self.assertIn("hello", out["answer"])
        self.assertEqual(sorted(out["tools_used"]), ["grep", "read"])
        self.assertFalse(out["read_only"] is False)
        self.assertTrue(out["read_only"], "read_only must default TRUE")
        self.assertTrue(str(out["dir"]).startswith(str(delegate_tools.WORKSPACE)), out["dir"])

    def test_no_output_at_all_still_reports_something(self):
        out = self._with_capture(0, "", "")
        self.assertTrue(out["ok"])
        self.assertEqual(out["answer"], "")
        self.assertIn("raw_tail", out)


class TheWorkingDirectoryIsNative(unittest.TestCase):
    def test_a_fresh_dir_is_absolute_and_windows_shaped(self):
        path, err = delegate_tools._run_dir(None)
        self.assertEqual(err, "")
        text = str(path)
        self.assertFalse(text.startswith("/"), f"a POSIX path kills the CLI: {text}")
        self.assertIn("subagents", text)
        self.assertTrue(path.exists())
        try:
            path.rmdir()
        except OSError:
            pass


class ThePoolIsKnown(unittest.TestCase):
    def test_the_roster_reads_without_costing_anything(self):
        out = delegate_tools.subagent_pool()
        self.assertTrue(out["free"], "the shipped roster must name at least one engine")
        self.assertIn(out["default"], out["free"])
        self.assertIn("side task", out["note"].lower())
        self.assertNotIn("probe", out)

    def test_the_roster_on_disk_is_what_is_read(self):
        path = delegate_tools.ROSTER_PATH
        self.assertTrue(path.exists(), "config/delegates.json ships with the kit")
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        roster = delegate_tools._roster()
        self.assertEqual(roster.get("default"), on_disk.get("default"))
        self.assertEqual(roster["free"], on_disk["free"])


if __name__ == "__main__":
    unittest.main()
