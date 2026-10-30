from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from chat_loop import host_prefetch  # noqa: E402
from continuity import compose_system  # noqa: E402
from skills_mod import catalog, list_skills, read_skill, seed_bundled, set_enabled  # noqa: E402
from tools import dispatch  # noqa: E402


class SkillsTests(unittest.TestCase):
    def test_fifteen_champions_seeded(self):
        seed_bundled()
        rows = catalog()
        champs = [r for r in rows if r.get("champion")]
        self.assertGreaterEqual(len(champs), 15)
        slugs = {r["slug"] for r in champs}
        self.assertIn("champion-arkos", slugs)
        self.assertIn("champion-lyra", slugs)
        self.assertIn("champion-lightfather", slugs)

    def test_read_and_toggle(self):
        r = read_skill("ARKOS")
        self.assertTrue(r.get("ok"))
        self.assertIn("Ethical Reality Architect", r.get("text") or r.get("description") or "")
        off = set_enabled("champion-arkos", False)
        self.assertTrue(off.get("ok"))
        self.assertNotIn("champion-arkos", off.get("enabled_list") or [])
        set_enabled("champion-arkos", True)

    def test_prompt_has_catalog_not_full_bodies(self):
        txt = compose_system()
        self.assertIn("ENABLED SKILLS", txt)
        self.assertTrue("champion-" in txt or "none enabled" in txt.lower())
        self.assertNotIn("Justin Helmer", Path(ROOT / "prompts" / "SOUL.md").read_text(encoding="utf-8"))

    def test_tools_and_prefetch(self):
        listed = dispatch("skill_list", {})
        self.assertTrue(listed.get("ok"))
        self.assertGreaterEqual(listed.get("n") or 0, 15)
        traces = host_prefetch("Invoke ARKOS and state the structure")
        names = [t.get("name") for t in traces]
        self.assertIn("skill_read", names)

    def test_portal_has_skills_panel(self):
        html = (ROOT / "portal" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="skills-list"', html)
        self.assertIn("lygoskillhub.html", html)
        self.assertIn("skills-hub-btn", html)

    def test_skillhub_urls(self):
        from skills_mod import SKILLHUB, SKILLHUB_CAT, SKILLHUB_FULL_CAT

        self.assertTrue(SKILLHUB.startswith("https://chatagent.ca/lygoskillhub"))
        self.assertIn("lygoskillhub_catalog.json", SKILLHUB_CAT)
        self.assertIn("lygo-full-skills/catalog.json", SKILLHUB_FULL_CAT)


if __name__ == "__main__":
    unittest.main()
