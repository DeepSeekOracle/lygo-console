"""Agent input/output: the model speaks for itself, knows its runtime, and can see its limbs.

These lock in the fixes for the "generic reply" behaviour: no canned readout replacing a real
answer, no Python dict reprs in anything written to the session, a RUNTIME/LIMBS block in the
system prompt, and one regeneration when the model just echoes itself.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import chat_loop  # noqa: E402
import continuity  # noqa: E402
import runtime_facts  # noqa: E402

def isolate_workspace(case: unittest.TestCase) -> None:
    """Point `continuity` at a throwaway workspace for the duration of one test.

    These tests used to write the operator's real MEMORY.md (and memory.jsonl) and put the old text
    back in a `finally` block. Two suite runs at once then raced on one file, and a run killed
    mid-test left a probe line in the steward's memory for good (defect D30).
    """
    td = tempfile.TemporaryDirectory(prefix="lygo_ws_")
    case.addCleanup(td.cleanup)
    patcher = patch.object(continuity, "WORKSPACE", Path(td.name))
    patcher.start()
    case.addCleanup(patcher.stop)

SC = {
    "ok": True,
    "verdict": "admin_map_live",
    "github": "https://github.com/DeepSeekOracle",
    "huggingface": "https://huggingface.co/DeepSeekOracle",
    "lattice": "https://chatagent.ca/",
    "chatagent_exists": True,
    "chatagent_sample": [".well-known", "agents"],
    "n_live_mounts": 12,
    "skills": {"n": 18, "enabled": 6},
}
TRACES = [{"name": "self_check", "result": SC}]


class SanitizeTests(unittest.TestCase):
    def test_model_answer_survives_boiler_words(self):
        out = chat_loop.sanitize_assistant("Yes — the limbs answered.\nNext Steps: none", TRACES)
        self.assertIn("the limbs answered", out)
        self.assertNotIn("Next Steps", out)
        # the old code threw the whole draft away and printed the canned readout instead
        self.assertNotIn("Self-check: admin_map_live", out)

    def test_placeholder_is_rewritten_in_place(self):
        out = chat_loop.sanitize_assistant("Repo lives at github.com/user/repo today.", TRACES)
        self.assertIn("https://github.com/DeepSeekOracle", out)
        self.assertIn("Repo lives at", out)
        self.assertNotIn("github.com/user/repo", out)

    def test_empty_draft_falls_back_to_a_readable_readout(self):
        out = chat_loop.sanitize_assistant("", TRACES)
        self.assertIn("admin_map_live", out)
        self.assertNotIn("{'n'", out)  # never a dict repr into the session
        self.assertIn("18 on disk, 6 enabled", out)

    def test_fallback_humanises_skills(self):
        txt = chat_loop.fallback_from_traces(TRACES)
        self.assertIn("Live mounts: 12", txt)
        self.assertIn("skills 18 on disk, 6 enabled", txt)

    def test_boiler_only_draft_is_detected(self):
        self.assertTrue(chat_loop._is_only_boiler("Next Steps:\n---"))
        self.assertFalse(chat_loop._is_only_boiler("Here is what the map says about D:\\chatagent."))


class PrefetchMessageTests(unittest.TestCase):
    def test_instruction_is_plain_not_a_template(self):
        msg = chat_loop.prefetch_message(TRACES)
        self.assertIn("newest message", msg)
        self.assertIn("do not run them again", msg)
        self.assertNotIn("6–10 short bullets", msg)


class SameAnswerTests(unittest.TestCase):
    def test_echo_is_detected(self):
        echo = "Self-check admin_map_live. GitHub https://github.com/DeepSeekOracle " * 3
        self.assertTrue(chat_loop.same_answer(echo, echo))
        self.assertFalse(chat_loop.same_answer(echo, "Short and different."))
        # too short to judge — better to answer than to regenerate forever
        self.assertFalse(chat_loop.same_answer("ok", "ok"))


class RuntimeFactsTests(unittest.TestCase):
    def test_facts_name_the_live_model(self):
        f = runtime_facts.facts()
        self.assertTrue(str(f["model"]).strip())
        self.assertTrue(str(f["build"]).strip())
        self.assertGreater(f["n_limbs"], 20)

    def test_prompt_block_is_self_knowledge(self):
        blk = runtime_facts.prompt_block()
        self.assertIn("RUNTIME", blk)
        self.assertIn("Model answering this turn:", blk)
        self.assertIn("no reason to say you cannot tell", blk)

    def test_limb_catalog_lists_real_tools(self):
        cat = runtime_facts.limb_catalog()
        self.assertIn("web_search", cat)
        self.assertIn("whoami", cat)
        self.assertIn("LIMBS", cat)


class SystemPromptTests(unittest.TestCase):
    def test_compose_system_carries_runtime_and_limbs(self):
        from continuity import compose_system

        txt = compose_system()
        self.assertIn("=== RUNTIME", txt)
        self.assertIn("=== LIMBS", txt)
        self.assertIn("NEWEST message only", txt)

    def test_prompt_fits_the_engine_window(self):
        from continuity import compose_system

        # The engine runs 8192 tokens ≈ 30k chars. This block + a trimmed history (9k) + the host
        # readout (3k) has to leave room for the answer, so the identity block stays under ~16.5k.
        self.assertLess(len(compose_system()), 16500)

    def test_newest_memory_notes_reach_the_model(self):
        from uuid import uuid4

        from continuity import append_memory, compose_system, memory_path

        isolate_workspace(self)  # a throwaway MEMORY.md, never the operator's (defect D30)
        p = memory_path()
        marker = "agent-layer probe " + uuid4().hex[:8]
        append_memory(marker)
        sysp = compose_system()
        # MEMORY.md grows at the bottom; a head-only cap hid everything `remember` wrote
        self.assertIn(marker, sysp)

    def test_repeated_notes_do_not_flood_the_tail_window(self):
        from continuity import dedupe_notes

        text = "\n".join("- (2026-09-17 17:0" + str(i) + ") leeches have three jaws" for i in range(5))
        out = dedupe_notes("## Handshake\n" + text + "\nkeep me\n")
        self.assertEqual(out.count("leeches have three jaws"), 1)
        self.assertIn("repeated 5x", out)
        self.assertIn("## Handshake", out)
        self.assertIn("keep me", out)

    def test_remembering_the_same_note_twice_writes_once(self):
        from uuid import uuid4

        from continuity import append_memory, memory_path

        isolate_workspace(self)  # a throwaway MEMORY.md, never the operator's (defect D30)
        p = memory_path()
        marker = "dup probe " + uuid4().hex[:8]
        first = append_memory(marker)
        second = append_memory(marker)
        after = p.read_text(encoding="utf-8")
        self.assertFalse(first.get("duplicate"))
        self.assertTrue(second.get("duplicate"))
        self.assertEqual(after.count(marker), 1)


class HistoryBudgetTests(unittest.TestCase):
    def test_newest_turns_are_kept_and_old_ones_dropped(self):
        msgs = [{"role": "user", "content": "x" * 9000} for _ in range(5)]
        kept = chat_loop.trim_history(msgs, budget=14000)
        self.assertLessEqual(len(kept), 2)
        self.assertEqual(kept[-1], msgs[-1])  # the newest turn always survives

    def test_short_sessions_are_untouched(self):
        import copy

        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        self.assertEqual(chat_loop.trim_history(msgs, budget=14000), copy.deepcopy(msgs))


class AnsweringModelTests(unittest.TestCase):
    """The RUNTIME block has to name the model that actually generates this turn."""

    CLOUD_API = {
        "mode": "api",
        "model": "deepseek-chat",
        "label": "DeepSeek",
        "url": "https://api.deepseek.com/v1/chat/completions",
        "active": True,
        "ready": True,
    }
    CLOUD_LOCAL = {"mode": "local", "model": "deepseek-chat", "label": "DeepSeek", "url": "", "active": False, "ready": True}

    def _facts(self, cloud):
        from unittest import mock

        with mock.patch.object(runtime_facts, "cloud_info", lambda: cloud):
            return runtime_facts.facts()

    def test_api_brain_reports_the_cloud_model_not_the_local_gguf(self):
        f = self._facts(self.CLOUD_API)
        self.assertEqual(f["model"], "deepseek-chat")
        self.assertEqual(f["brain"], "api")
        self.assertIn("DeepSeek", f["engine"])
        self.assertTrue(f["standby_model"])  # the local GGUF is still named as the standby
        self.assertNotIn("deepseek-chat", f["local_engine"])

    def test_local_brain_reports_the_local_weights(self):
        f = self._facts(self.CLOUD_LOCAL)
        self.assertEqual(f["brain"], "local")
        self.assertEqual(f["model"], f["standby_model"])
        self.assertNotIn(" API", f["engine"])

    def test_prompt_block_promises_handoff_honesty(self):
        blk = runtime_facts.prompt_block()
        self.assertIn("standby", blk)
        self.assertIn("handoff", blk)


class SeatTests(unittest.TestCase):
    def test_architect_seat_is_installed(self):
        import skills_mod

        slugs = [c["slug"] for c in skills_mod.CHAMPIONS]
        self.assertIn("champion-lyra-architect", slugs)

    def test_most_specific_seat_name_wins(self):
        import skills_mod

        hit = skills_mod.match_invoked("summon LYRA architect")
        self.assertEqual("champion-lyra-architect", hit[0])

    def test_architect_skill_md_carries_the_protocol(self):
        import skills_mod

        skills_mod.seed_bundled()
        got = skills_mod.read_skill("champion-lyra-architect")
        self.assertTrue(got.get("ok"))
        self.assertIn("Architect protocol", got.get("text") or "")

    def test_whoami_reports_the_model_and_build(self):
        from tools import dispatch

        w = dispatch("whoami", {})
        self.assertTrue(w.get("model"))
        self.assertTrue(w.get("build"))
        self.assertIn("engine", w)
        self.assertIn("LYRA", w.get("seat") or "")


class ChatInputTests(unittest.TestCase):
    """D13: an input the console did not understand used to be answered anyway."""

    def test_messages_is_the_contract(self):
        got = chat_loop.normalise_messages({"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(got, [{"role": "user", "content": "hi"}])

    def test_shorthands_are_tolerated(self):
        for key in ("prompt", "message", "input", "text"):
            got = chat_loop.normalise_messages({key: "hi"})
            self.assertEqual(got, [{"role": "user", "content": "hi"}], key)

    def test_junk_entries_never_reach_the_prompt(self):
        got = chat_loop.normalise_messages({"messages": ["hi", None, {"role": "user", "content": "ok"}]})
        self.assertEqual(got, [{"role": "user", "content": "ok"}])

    def test_no_user_text_is_detectable(self):
        for payload in ({}, {"messages": []}, {"messages": [{"role": "assistant", "content": "x"}]},
                        {"messages": [{"role": "user", "content": "   "}]}, {"message": 42}):
            msgs = chat_loop.normalise_messages(payload)
            self.assertFalse(chat_loop.user_text_of(msgs).strip(), payload)

    def test_image_only_turn_is_not_no_input(self):
        msgs = chat_loop.normalise_messages({"messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]}]})
        self.assertTrue(chat_loop.has_image(msgs))
        self.assertEqual(chat_loop.user_text_of(msgs), "")

    def test_newest_user_turn_wins(self):
        msgs = chat_loop.normalise_messages({"messages": [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"}]})
        self.assertEqual(chat_loop.user_text_of(msgs), "second")


if __name__ == "__main__":
    unittest.main()
