"""The three systems must stay named, distinct, and read from the machine.

The label is a fact about the deployment, not a decoration: health, status and the model's own
runtime block all quote it, and a stick booted on a stranger's PC must not look like a desktop
install. These tests pin the vocabulary, the measuring rule, and the wiring.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import surface  # noqa: E402


class VocabularyTests(unittest.TestCase):
    def test_three_systems_exactly(self):
        self.assertEqual(len(surface.ORDER), 3)
        self.assertEqual(list(surface.ORDER), [surface.USB_LOCAL, surface.PC_LOCAL, surface.API_ONLY])

    def test_labels_are_distinct_and_say_what_they_are(self):
        labels = [surface.LABELS[i] for i in surface.ORDER]
        self.assertEqual(len(set(labels)), 3)
        self.assertEqual(surface.LABELS[surface.USB_LOCAL], "USB LOCAL")
        self.assertEqual(surface.LABELS[surface.PC_LOCAL], "PC LOCAL")
        self.assertIn("API ONLY", surface.LABELS[surface.API_ONLY])
        self.assertIn("PORTAL", surface.LABELS[surface.API_ONLY])

    def test_every_system_explains_what_answers(self):
        for i in surface.ORDER:
            self.assertTrue(surface.ANSWERS[i].strip(), i)
            self.assertTrue(surface.LAUNCHERS[i].strip(), i)


class MeasuringTests(unittest.TestCase):
    def test_declaration_beats_the_probe(self):
        for declared, want in (("usb", "usb"), ("stick", "usb"), ("portable", "usb"), ("pc", "pc"), ("desktop", "pc")):
            with mock.patch.dict(os.environ, {"LYGO_SURFACE": declared}):
                kind, why = surface.media(Path("C:/some/kit"))
            self.assertEqual(kind, want, declared)
            self.assertIn("LYGO_SURFACE", why)

    def test_removable_media_reads_as_usb_and_fixed_as_pc(self):
        with mock.patch.dict(os.environ, {"LYGO_SURFACE": ""}), mock.patch.object(
            surface, "drive_type", lambda d: surface.DRIVE_REMOVABLE
        ):
            self.assertEqual(surface.media(Path("E:/kit"))[0], "usb")
        with mock.patch.dict(os.environ, {"LYGO_SURFACE": ""}), mock.patch.object(
            surface, "drive_type", lambda d: surface.DRIVE_FIXED
        ):
            self.assertEqual(surface.media(Path("I:/kit"))[0], "pc")

    def test_unreadable_volume_falls_back_to_the_launchers(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.dict(os.environ, {"LYGO_SURFACE": ""}), mock.patch.object(surface, "drive_type", lambda d: 0):
                self.assertEqual(surface.media(root)[0], "pc")
                (root / "LYGO_AGENT_STICK.bat").write_text("rem", encoding="utf-8")
                kind, why = surface.media(root)
                self.assertEqual(kind, "usb")
                self.assertIn("inferred", why)

    def test_no_local_engine_means_the_api_only_portal(self):
        self.assertEqual(surface.here(False), surface.API_ONLY)
        self.assertEqual(surface.report(local_ready=False)["id"], surface.API_ONLY)

    def test_local_ready_reports_the_media_system(self):
        with mock.patch.dict(os.environ, {"LYGO_SURFACE": "usb"}):
            self.assertEqual(surface.here(True), surface.USB_LOCAL)
            self.assertEqual(surface.report(True)["id"], surface.USB_LOCAL)
        with mock.patch.dict(os.environ, {"LYGO_SURFACE": "pc"}):
            self.assertEqual(surface.here(True), surface.PC_LOCAL)


class ReportShapeTests(unittest.TestCase):
    def test_report_lists_all_three_with_exactly_one_here(self):
        for ready in (True, False):
            r = surface.report(local_ready=ready)
            self.assertEqual([s["id"] for s in r["systems"]], list(surface.ORDER))
            self.assertEqual([s["here"] for s in r["systems"]].count(True), 1)
            flagged = next(s for s in r["systems"] if s["here"])
            self.assertEqual(flagged["id"], r["id"])
            self.assertEqual(flagged["label"], r["label"])
            self.assertTrue(r["media_why"])

    def test_banner_lines_name_the_current_system(self):
        out = surface.lines(surface.report(local_ready=False))
        self.assertIn(surface.LABELS[surface.API_ONLY], out[0])
        joined = "\n".join(out)
        for i in surface.ORDER:
            self.assertIn(surface.LABELS[i], joined)


class WiringTests(unittest.TestCase):
    """The label is only real if the surfaces quote it. Source guards, because a health payload
    needs a live console to fetch and these must fail fast in the suite."""

    def test_health_quotes_the_system(self):
        src = (ROOT / "src/server.py").read_text(encoding="utf-8")
        # surface still owns the label; the answer reads it through health_payload's safe() wrapper
        # (defect 34), so a hardcoded system string still fails this guard.
        self.assertIn('"system": safe("system", lambda: _surface.report(', src)

    def test_runtime_facts_quote_the_system(self):
        src = (ROOT / "src/runtime_facts.py").read_text(encoding="utf-8")
        self.assertIn('"system": __import__("surface").report(', src)
        self.assertIn("System:", src)



class BriefRoleTests(unittest.TestCase):
    """The prompt carries a compact form of the same three roles - and it must stay compact."""

    def test_brief_roles_cover_the_same_three_systems(self):
        import surface

        self.assertEqual(set(surface.ROLES_BRIEF), set(surface.ROLES))
        for i in surface.ORDER:
            self.assertIn(surface.LABELS[i], " ".join(surface.LABELS.values()))
            self.assertTrue(surface.ROLES_BRIEF[i].strip())
            self.assertLess(len(surface.ROLES_BRIEF[i]), len(surface.ROLES[i]))

    def test_brief_roles_keep_the_point_of_each_system(self):
        import surface

        self.assertIn("stand-alone", surface.ROLES_BRIEF[surface.USB_LOCAL])
        self.assertIn("admin", surface.ROLES_BRIEF[surface.PC_LOCAL])
        self.assertIn("api-only", surface.ROLES_BRIEF[surface.API_ONLY].lower())

    def test_prompt_states_the_roles_and_still_fits(self):
        from continuity import compose_system
        import surface

        txt = compose_system()
        for i in surface.ORDER:
            self.assertIn(surface.ROLES_BRIEF[i], txt)
        self.assertLess(len(txt), 16500)


if __name__ == "__main__":
    unittest.main(verbosity=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class RoleTests(unittest.TestCase):
    """Each system says what it *is*, in the operator's own terms - three separate systems that
    work as one, and a label that only names them does not say which one a human is looking at."""

    def test_every_system_has_a_role(self):
        for i in surface.ORDER:
            self.assertTrue(surface.ROLES[i].strip(), i)

    def test_roles_say_what_the_operator_said(self):
        usb = surface.ROLES[surface.USB_LOCAL].lower()
        pc = surface.ROLES[surface.PC_LOCAL].lower()
        web = surface.ROLES[surface.API_ONLY].lower()
        self.assertIn("stand-alone", usb)
        self.assertIn("onboard", usb)
        self.assertIn("mobile", usb)
        self.assertIn("admin console", pc)
        self.assertIn("public version", pc)
        self.assertIn("api-only", web)
        self.assertIn("online", web)
        self.assertIn("already built", web)

    def test_report_and_lines_carry_the_role(self):
        r = surface.report(local_ready=True)
        self.assertEqual(r["role"], surface.ROLES[r["id"]])
        for s in r["systems"]:
            self.assertEqual(s["role"], surface.ROLES[s["id"]])
        joined = "\n".join(surface.lines(r))
        for i in surface.ORDER:
            self.assertIn(surface.ROLES[i], joined)

    def test_runtime_facts_quote_the_roles(self):
        src = (ROOT / "src/runtime_facts.py").read_text(encoding="utf-8")
        self.assertIn("system_roles()", src)
        self.assertIn('__import__("surface")', src)

    def test_portals_state_the_roles(self):
        for rel in ("web_portal/index.html", "portal/index.html"):
            html = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("stand-alone", html, rel)
            self.assertIn("admin console", html, rel)


class PortalLabelTests(unittest.TestCase):
    """Every surface a human reads must name the system it is. The console page is server-rendered
    (a JS-only label can be blanked by a CSP), and the public portal ships a strict CSP, so its label
    is static markup."""

    def test_console_page_carries_the_server_rendered_token(self):
        html = (ROOT / "portal/index.html").read_text(encoding="utf-8")
        self.assertIn("/*LYGO_SYSTEM*/", html)

    def test_server_renders_that_token(self):
        src = (ROOT / "src/server.py").read_text(encoding="utf-8")
        self.assertIn('html.replace("/*LYGO_SYSTEM*/"', src)

    def test_public_portal_states_it_is_api_only(self):
        html = (ROOT / "web_portal/index.html").read_text(encoding="utf-8")
        self.assertIn("WEB PORTAL (API ONLY)", html)
        for other in ("USB LOCAL", "PC LOCAL"):
            self.assertIn(other, html, "the public portal must name the other two systems")
