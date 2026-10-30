"""The Python and Rust limbs must let a console agent run real code in the workspace.

`python_exec` existed but was thin: no argv, no timeout control, no cwd, and it reported a run by
`ok` alone - a non-zero exit and a missing interpreter looked the same. There was no Rust limb at all,
on a machine that has rustc and cargo installed. These tests are the contract for both.
"""
import pathlib
import sys
import json
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import limbs  # noqa: E402
import tools  # noqa: E402


def denied_sample():
    """A command the console's own deny list refuses, taken from that list rather than guessed."""
    for cand in ("rm -rf /", "shutdown /s /t 0", "format C:", "del /f /s /q C:\\*", "rd /s /q C:\\"):
        if limbs._SHELL_DENY.search(cand):
            return cand
    return None


class PythonLimbCase(unittest.TestCase):
    def call(self, **args):
        return tools.dispatch("python_exec", args)

    def test_a_snippet_prints_its_result(self):
        got = self.call(code="print(6*7)")
        self.assertTrue(got.get("ok"), got)
        self.assertIn("42", got["stdout"])
        self.assertEqual(got.get("code"), 0, "a clean run must report its exit code")

    def test_argv_reaches_the_program(self):
        got = self.call(code="import sys; print(sys.argv[1:])", argv=["one", "two"])
        self.assertTrue(got.get("ok"), got)
        self.assertIn("['one', 'two']", got["stdout"], "argv never reached the program")

    def test_a_non_zero_exit_is_reported_not_raised(self):
        got = self.call(code="raise SystemExit(3)")
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("code"), 3, "the exit code is the whole point of a run")

    def test_a_timeout_kills_the_run(self):
        got = self.call(code="import time; time.sleep(30)", timeout=1)
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("error"), "timeout")

    def test_stderr_is_captured_separately(self):
        got = self.call(code="import sys; print('out'); sys.stderr.write('err')")
        self.assertIn("out", got["stdout"])
        self.assertIn("err", got["stderr"])

    def test_the_deny_list_still_refuses(self):
        bad = denied_sample()
        self.assertIsNotNone(bad, "the console's deny list is empty - check _SHELL_DENY")
        got = self.call(code=f"# {bad}\nprint('nope')")
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("error"), "p0_blocked")

    def test_an_empty_call_names_the_missing_argument(self):
        got = self.call(code="   ")
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("error"), "empty")
        self.assertIn("hint", got, "an empty call must say what is missing")


class RustLimbCase(unittest.TestCase):
    def call(self, **args):
        return tools.dispatch("rust_exec", args)

    def test_the_toolchain_is_found(self):
        rustc, cargo = limbs._find_rust()
        self.assertTrue(rustc or cargo, "no rust toolchain found (PATH or ~/.cargo/bin)")

    def test_a_real_program_compiles_and_runs(self):
        rustc, _ = limbs._find_rust()
        if not rustc:
            self.skipTest("no rustc on this machine")
        got = self.call(code='fn main() { println!("{}", 6 * 7); }', name="six_by_seven")
        self.assertTrue(got.get("file"), "the limb should say where it put the source")
        if got.get("ran") is False:
            # this machine has rustc but no linker: a type-check is the honest outcome, and it must
            # say why rather than report a broken program
            self.assertTrue(got.get("ok") and got.get("checked"), got)
            self.assertIn("linker", (got.get("note") or "").lower(), "it must say why it did not run")
        else:
            self.assertTrue(got.get("ok"), got)
            self.assertIn("42", got["stdout"])

    def test_a_compile_error_comes_back_as_diagnostics(self):
        rustc, _ = limbs._find_rust()
        if not rustc:
            self.skipTest("no rustc on this machine")
        got = self.call(code="fn main() { let x: i32 = \"not a number\"; }", name="bad_type")
        self.assertFalse(got.get("ok"))
        self.assertEqual(got.get("error"), "compile_failed")
        self.assertIn("error", got.get("stderr", "").lower(), "the compiler's own words, not a traceback")

    def test_a_missing_toolchain_fails_soft(self):
        rustc, cargo = limbs._find_rust()
        real = limbs._find_rust
        try:
            limbs._find_rust = lambda: (None, None)
            got = self.call(code="fn main() {}")
            self.assertFalse(got.get("ok"))
            self.assertEqual(got.get("error"), "rust_missing")
            self.assertIn("hint", got)
        finally:
            limbs._find_rust = real

    def test_the_code_limbs_reach_the_local_brain(self):
        """The local schema IS what gemma4 is given: a limb absent from it does not exist for it.

        Measured 2026-09-21: the local agent answered "I do not have a direct Python execution limb
        in my current configuration" and computed 6*7 in its head, because core_schema() omitted both
        code limbs. This is the regression that says so.
        """
        names = [(s.get("function") or {}).get("name") for s in tools.core_schema()]
        for want in ("python_exec", "rust_exec"):
            self.assertIn(want, names, f"{want} is not offered to the local brain")
        # The local schema is capped by a measured character budget, not a bare count: the
        # capability set grows, what a small model pays must not (defect 103 and the self-build
        # harness). 28 tools measured at ~10.3k chars is about 2.6k tokens of a 32k window.
        self.assertLessEqual(len(names), 44, "the local schema is capped - pay for every slot")
        self.assertLess(names.index("python_exec"), names.index("web_search"),
                        "the code limbs must lead, or a small model reads the web first")

    def test_the_limb_is_offered_to_the_model(self):
        names = [s["function"]["name"] for s in tools.TOOLS_SCHEMA]
        self.assertIn("rust_exec", names, "a limb the model cannot see is not a limb")
        props = None
        for s in tools.TOOLS_SCHEMA:
            if s["function"]["name"] == "python_exec":
                props = s["function"]["parameters"].get("properties") or {}
        self.assertIn("argv", props or {}, "the python limb must advertise argv to be full")


class RustBuildsOrSaysWhy(unittest.TestCase):
    def test_detection_is_measured_not_guessed(self):
        """git-bash ships a `link.exe` that is coreutils, so a name proves nothing either way."""
        self.assertFalse(limbs._is_real_linker(r"C:\\Program Files\\Git\\usr\\bin\\link.EXE"))
        self.assertFalse(limbs._is_real_linker("/usr/bin/link"))
        self.assertTrue(limbs._is_real_linker(r"C:\\Program Files\\Microsoft Visual Studio\\link.exe"))

    def test_the_probe_answers_with_evidence_not_a_guess(self):
        probe = limbs._rust_probe()
        self.assertIn("can_run", probe)
        if probe.get("can_run"):
            self.assertEqual(probe.get("stdout"), "42", "the probe must build, run and read back a real program")
        else:
            self.assertTrue(probe.get("checked") or probe.get("detail"), "a refusal must say what is missing")

    def test_the_check_reports_what_it_measured(self):
        got = limbs.extra("toolchain_install", {"what": "check"})
        self.assertTrue(got.get("ok"), got)
        self.assertEqual(got.get("linker_present"), bool((got.get("probe") or {}).get("can_run")))
        if got.get("not_a_linker"):
            self.assertTrue(got["not_a_linker"], "a git-bash link must be named as not-a-linker")
            self.assertIn("probe", got.get("note", "") + json.dumps(got.get("probe")))



    def test_files_the_agent_saved_are_importable(self):
        """Gauntlet T12: saved mod_a.py, then ModuleNotFoundError when running run_a.py."""
        import sys as _sys
        from pathlib import Path as _Path

        kroot = _Path(__file__).resolve().parents[1]
        _sys.path.insert(0, str(kroot / "src"))
        import limbs

        mod = limbs.extra("save_note", {"name": "gauntlet_mod_a.py",
                                        "text": "def answer():\n    return 6 * 7\n"})
        self.assertTrue(mod.get("ok"), mod)
        run = limbs.extra("python_exec", {"code": "import gauntlet_mod_a\nprint(gauntlet_mod_a.answer())"})
        self.assertTrue(run.get("ok"), run)
        self.assertIn("42", run.get("stdout") or "", f"could not import what it saved: {run}")

if __name__ == "__main__":
    unittest.main()
