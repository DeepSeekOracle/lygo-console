"""The boot banner: the engine should look like what it is when it starts.

The operator asked for a LYGO logo on boot - big, ASCII, futuristic, "fully branded". The kit's branding
convention is a `Δ9Φ963-<MODULE>-v1` signature, and the console prints three plain lines today:

    LYGO LLM Console v1.3.0  http://127.0.0.1:9641/?v=v1.3.0
    kit <EDRIVE>\\lygo-protocol-stack\\lygo_llm_console
    signature Δ9Φ963-LYGO-LLM-CONSOLE-v1  physics=True  bind=127.0.0.1

Those stay - other surfaces and tests read them. The banner goes above them.

What is tested here is that it is *branded*, *live*, *safe* and *readable*:

* branded  - the LYGO letterforms, the signature, the licence line.
* live     - the facts it prints come from the box at that moment (hardware.py), so a boot says which
             card is free and which engine build is about to serve, not a version string typed once.
* safe     - a missing card, a missing config, a hostile width: it still prints something and never
             raises. A banner must not be able to stop a boot.
* readable - no line wider than the terminal it is printed into, and colour that can be turned off
             (`NO_COLOR`, or a non-tty) because PowerShell is not always Windows Terminal.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve()
SRC = HERE.parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import banner  # noqa: E402  (path set above)


class TheLogoIsThereTests(unittest.TestCase):
    def test_the_letterforms_spell_lygo(self):
        art = banner.render(color=False)
        for piece in ("██╗", "╚██████╔╝", "██████╗", "╚══════╝"):
            self.assertIn(piece, art, "the artwork lost a LYGO letterform")

    def test_it_carries_the_signature_and_the_licence(self):
        art = banner.render(color=False)
        self.assertIn(banner.SIGNATURE, art)
        self.assertIn("Δ9Φ963", art)
        self.assertIn("LYGO Sovereign License v3.0", art)

    def test_the_kit_header_is_still_printed_under_it(self):
        art = banner.render(color=False, facts={"title": "LYGO LLM Console v9.9.9", "url": "http://127.0.0.1:9641/"})
        self.assertIn("LYGO LLM Console v9.9.9", art)


class ItSaysWhatIsActuallyTrueTests(unittest.TestCase):
    def test_the_facts_it_is_given_are_printed(self):
        art = banner.render(color=False, facts={"model": "gemma4-12b", "engine": "b11074", "backend": "cuda",
                                                "console_port": 9641, "engine_port": 11441, "ngl": 49})
        for want in ("gemma4-12b", "b11074", "cuda", "9641", "11441", "49"):
            self.assertIn(want, art)

    def test_live_facts_are_read_from_the_box_and_never_raise(self):
        facts = banner.live_facts()
        self.assertIsInstance(facts, dict)
        self.assertIn("hardware", facts)
        with mock.patch.object(banner.hardware, "snapshot", side_effect=RuntimeError("no sensors")):
            degraded = banner.live_facts()
        self.assertIsInstance(degraded, dict)
        self.assertIsInstance(banner.render(color=False, facts=degraded), str)

    def test_a_card_it_cannot_read_is_a_blank_not_a_crash(self):
        with mock.patch.object(banner.hardware, "snapshot", return_value={"available": False, "why": "no nvidia-smi",
                                                                         "gpus": [], "ram": {}, "cpu": {}}):
            art = banner.render(color=False, facts=banner.live_facts())
        self.assertIn("no nvidia-smi", art)


class ItMustNotWreckAConsoleTests(unittest.TestCase):
    def test_colour_is_optional_and_off_when_asked(self):
        self.assertIn("\x1b[", banner.render(color=True))
        self.assertNotIn("\x1b[", banner.render(color=False))

    def test_no_color_in_the_environment_means_no_escapes(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            self.assertNotIn("\x1b[", banner.render())
        with mock.patch.dict(os.environ, {"NO_COLOR": ""}):
            self.assertTrue(banner.wants_color(stream=_FakeTty()))

    def test_no_line_is_wider_than_the_terminal(self):
        for width in (60, 78, 120):
            art = banner.render(color=False, width=width)
            widest = max(len(line) for line in art.splitlines())
            self.assertLessEqual(widest, width, "line wrapped at width %d" % width)

    def test_a_narrow_terminal_still_gets_the_name_and_the_signature(self):
        art = banner.render(color=False, width=24)
        self.assertIn("LYGO", art)
        self.assertIn("Δ9Φ963", art)

    def test_print_returns_what_it_wrote(self):
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            text = banner.print_banner(color=False)
        self.assertEqual(buf.getvalue().strip(), text.strip())
        self.assertIn(banner.SIGNATURE, text)


class ItIsActuallyWiredIntoTheBootTests(unittest.TestCase):
    """A banner nobody calls is decoration that rots: the module and its tests can be green while the
    boot still prints three plain lines. These read the boot site itself."""

    def test_live_facts_accepts_exactly_what_the_boot_site_passes_it(self):
        """The boot site called live_facts(url=..., kit=..., console_port=..., engine_port=...) and
        `kit` did not exist, so the first real boot printed "[banner] degraded: TypeError". The guard
        held (a boot survived), but the branding did not appear - which is the whole point of it. A
        test that calls the same thing the boot calls is what catches that, not a test of the render."""
        facts = banner.live_facts(url="http://127.0.0.1:9651/", kit="I:/kit", console_port=9651, engine_port=11441)
        art = banner.render(color=False, facts=facts)
        self.assertIn("9651/11441", art)
        self.assertIn("I:/kit", art)

    def test_the_console_boot_prints_the_banner_and_brands_the_engine_line(self):
        src = (HERE.parent.parent / "src" / "server.py").read_text(encoding="utf-8")
        self.assertIn("_banner.print_banner(", src)
        self.assertIn("print_engine_line(", src)

    def test_the_window_title_says_lygo(self):
        class Recorder(_FakeTty):
            def __init__(self):
                self.said = ""

            def write(self, s):
                self.said += s

        rec = Recorder()
        with mock.patch.object(banner.sys, "stdout", rec):
            ok = banner.set_window_title("LYGO LLM CONSOLE  Δ9Φ963")
        self.assertTrue(ok)
        self.assertTrue(rec.said.startswith("\x1b]0;"), rec.said[:20])
        self.assertIn("LYGO", rec.said)

    def test_a_closed_stdout_does_not_break_the_title_or_the_engine_line(self):
        closed = mock.Mock(isatty=mock.Mock(side_effect=OSError("stdout is closed")))
        with mock.patch.object(banner.sys, "stdout", closed):
            self.assertFalse(banner.set_window_title("x"))
            self.assertIsInstance(banner.print_engine_line("m", color=False), str)

    def test_the_engine_line_names_the_model_the_build_the_offload_and_the_port(self):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            line = banner.print_engine_line("gemma4-12b", backend="cuda", tag="b11074", ngl=49,
                                            port=11441, color=False)
        for want in ("gemma4-12b", "b11074", "11441", "49", "Δ9Φ963"):
            self.assertIn(want, line)
        self.assertIn("gemma4-12b", buf.getvalue())

    def test_the_engine_line_reads_the_build_from_the_backend_store_when_not_told(self):
        import contextlib
        import io

        store = HERE.parent.parent / "engine" / "backends" / "cuda" / "backend.json"
        if not store.is_file():
            self.skipTest("no cuda backend store on this checkout")
        tag = json.loads(store.read_text(encoding="utf-8")).get("tag") or ""
        self.assertTrue(tag, "the store carries no tag")
        with contextlib.redirect_stdout(io.StringIO()):
            line = banner.print_engine_line("x", backend="cuda", port=11441, color=False)
        self.assertIn(str(tag), line)


class _FakeTty:
    def isatty(self):
        return True

    def write(self, s):
        pass

    def flush(self):
        pass


if __name__ == "__main__":
    unittest.main()
