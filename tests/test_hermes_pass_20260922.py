"""Hermes pass 2026-09-22: the D3 chain over-fire, the T1 named-limb gap, the T8 escaping.

Every test here is derived from a live gauntlet reply (qwen2.5-coder:7b, 2026-09-22 10:1x), quoted in
the test body. No model is required - each one pins the host-side mechanism the reply exposed.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "src"))

import chat_loop  # noqa: E402
from limbs import canonicalize, repair_escapes  # noqa: E402
from tools import dispatch  # noqa: E402

NOTES = KIT / "workspace" / "notes"

T12_PROMPT = ("Create two files in your workspace: mod_a.py defining a function answer() that returns "
              "6*7, and run_a.py that imports it and prints the result. Then run run_a.py with "
              "python_exec and tell me what it printed.")
T6_PROMPT = ("Read your workspace notes file gauntlet_t4.txt, then save a new file called "
             "gauntlet_t6.txt in that same folder whose contents are what you read followed by the "
             "word CHECKED.")
T1_PROMPT = "Use your calc limb to work out 47 * 89. Tell me only the number."


class ChainOverFireTests(unittest.TestCase):
    """Live evidence: T12 replied 'UNKNOWN (SHADOW) - no mod_a.py file found.' and wrote neither file."""

    def test_chain_stays_silent_when_the_source_does_not_exist(self):
        for p in KIT.joinpath("workspace").rglob("mod_a.py"):
            p.unlink()
        self.assertEqual([], chat_loop.host_file_chain(T12_PROMPT))

    def test_t12_shaped_turn_gets_no_missing_file_readout(self):
        names = [t.get("name") for t in chat_loop.host_prefetch(T12_PROMPT)]
        self.assertNotIn("read_file", names, "a create turn must not carry a host read failure")

    def test_chain_still_owns_the_destination_when_the_source_exists(self):
        NOTES.mkdir(parents=True, exist_ok=True)
        src, dest = NOTES / "gauntlet_t4.txt", NOTES / "gauntlet_t6.txt"
        src.write_text("T4-OK\n", encoding="utf-8")
        dest.unlink(missing_ok=True)
        traces = chat_loop.host_prefetch(T6_PROMPT)
        names = [t.get("name") for t in traces]
        self.assertIn("read_file", names)
        self.assertIn("save_note", names)
        body = dest.read_text(encoding="utf-8")
        self.assertIn("T4-OK", body)
        self.assertIn("CHECKED", body)
        self.assertNotIn("whose contents", body, "the instruction must not be written as the body")
        dest.unlink()
        src.unlink()


class NamedLimbMathTests(unittest.TestCase):
    """Live evidence: T1 answered '4163' with traces: []."""

    def test_expression_inside_a_sentence_is_extracted(self):
        self.assertEqual("47 * 89", chat_loop.math_expr_in(T1_PROMPT))

    def test_named_calc_limb_is_run_on_the_host(self):
        traces = chat_loop.host_prefetch(T1_PROMPT)
        calc = [t for t in traces if t.get("name") == "calc"]
        self.assertTrue(calc, f"no calc trace: {[t.get('name') for t in traces]}")
        self.assertEqual("4183", str((calc[0].get("result") or {}).get("value")))

    def test_a_sentence_without_arithmetic_runs_nothing(self):
        self.assertEqual("", chat_loop.math_expr_in("use your calc limb when you need it"))


class EscapedPayloadTests(unittest.TestCase):
    """Live evidence: T8's python_exec got a literal \\n and Python said 'unexpected character after
    line continuation character'; the model then reported a syntax error instead of 144."""

    def test_double_escaped_code_becomes_real_newlines(self):
        _n, args = canonicalize("python_exec", {"code": "def f():\\n    return 1\\nprint(f())"})
        self.assertEqual("def f():\n    return 1\nprint(f())", args["code"])

    def test_a_payload_without_a_newline_escape_is_untouched(self):
        bs = chr(92)
        raw = f"x = 'a{bs}{bs}b'"
        self.assertEqual(raw, repair_escapes(raw))

    def test_pair_backslash_collapses_inside_a_repaired_payload(self):
        bs = chr(92)
        raw = f'print("a{bs}{bs}b")' + bs + "n" + "print(1)"
        self.assertEqual(f'print("a{bs}b")' + "\n" + "print(1)", repair_escapes(raw))

    def test_real_newlines_are_left_alone(self):
        code = "print(1)\nprint(2)"
        self.assertEqual(code, repair_escapes(code))

    def test_file_content_is_not_repaired(self):
        # write_file's body is data: a literal \n there can be the text the operator asked for.
        _n, args = canonicalize("write_file", {"path": "t.txt", "content": "line\\nline"})
        self.assertEqual("line\\nline", args["content"])

    def test_escaped_multiline_program_runs(self):
        prog = "total = 0\nfor i in range(1, 101):\n    total += i\nprint(total)"
        escaped = json.dumps(prog)[1:-1]          # exactly the shape T8's call arrived in
        self.assertIn("\\n", escaped)
        out = dispatch("python_exec", {"code": escaped})
        self.assertTrue(out.get("ok"), out)
        self.assertIn("5050", out.get("stdout") or "")


class PythonExecArgumentTests(unittest.TestCase):
    """Live evidence: T9 sent {'code': 'sum_of_numbers.py', 'cwd': .../workspace/scripts} -> the operator
    was told the directory does not exist."""

    def test_missing_cwd_falls_back_to_the_workspace(self):
        out = dispatch("python_exec", {"code": "print(6 * 7)", "cwd": "no_such_folder_here"})
        self.assertTrue(out.get("ok"), out)
        self.assertIn("42", out.get("stdout") or "")

    def test_a_bare_filename_that_does_not_exist_says_so(self):
        out = dispatch("python_exec", {"code": "definitely_not_written_yet.py"})
        self.assertFalse(out.get("ok"))
        self.assertEqual("filename_not_code", out.get("error"))

    def test_a_bare_filename_that_exists_is_run(self):
        NOTES.mkdir(parents=True, exist_ok=True)
        f = NOTES / "hermes_probe_script.py"
        f.write_text("print('SCRIPT-RAN')\n", encoding="utf-8")
        try:
            out = dispatch("python_exec", {"code": str(f)})
            self.assertTrue(out.get("ok"), out)
            self.assertIn("SCRIPT-RAN", out.get("stdout") or "")
            self.assertEqual(str(f), out.get("ran"))
        finally:
            f.unlink(missing_ok=True)

    def test_a_windows_path_is_never_repaired_as_an_escaped_program(self):
        # "...\workspace\notes\x.py" carries a literal backslash-n that means "notes".
        bs = chr(92)
        path = f"C:{bs}work{bs}notes{bs}probe.py"
        _n, args = canonicalize("python_exec", {"code": path})
        self.assertEqual(path, args["code"])
        self.assertNotIn("\n", args["code"])


if __name__ == "__main__":
    unittest.main()
