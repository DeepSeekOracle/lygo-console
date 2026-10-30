"""The vault: a conversation that ends is filed, labelled, found again, and can be picked back up.

Everything here runs in a sandbox. The operator's own sessions, journals and vault are never
touched by a test - only the shape of the work is tested, not the history.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import compaction  # noqa: E402
import sessions  # noqa: E402


def _seed_journal(sid: str, pairs: list[tuple[str, str]], start: float | None = None) -> list[dict]:
    """Write a journal the way the console does: one JSON object per turn, stamped."""
    base = start if start is not None else time.time() - 600
    recs = []
    path = Path(compaction.journal_path(sid))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for i, (role, text) in enumerate(pairs):
            ts = base + i * 12
            rec = {"iso": compaction.iso(ts), "ts": ts, "role": role, "content": text,
                   "chars": len(text), "tokens": compaction.est_tokens(text)}
            recs.append(rec)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return recs


class VaultCase(unittest.TestCase):
    """Redirects every path either module writes to a throwaway tree."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="lygo_vault_test_")
        self.root = Path(self.tmp.name)
        self._saved: dict[tuple[object, str], object] = {}

        for name, path in list(vars(compaction).items()):
            if isinstance(path, Path):
                self._swap(compaction, name, self.root / "save" / name.lower())
        self._swap(sessions, "VAULT", self.root / "save" / "vault")
        self._swap(sessions, "CATALOG", self.root / "save" / "vault" / "catalog.jsonl")
        self._swap(sessions, "CATALOG_MD", self.root / "save" / "vault" / "CATALOG.md")

        import continuity

        self._swap(continuity, "CURRENT", self.root / "save" / "sessions" / "current.json")
        self._swap(continuity, "SESSIONS", self.root / "save" / "sessions")
        self.continuity = continuity
        self.live = "20260919-141200-ccec"
        st = compaction._load_state()
        st["session_id"] = self.live
        compaction._write_state(st)
        _seed_journal(self.live, [
            ("user", "we need a session history module with a needled phrase in it"),
            ("assistant", "Filed: the vault keeps every turn and the catalog names it."),
            ("user", "and the new session button must not throw the chat away"),
            ("assistant", "It files the conversation before it clears the window."),
        ])

    def tearDown(self) -> None:
        for (mod, name), val in self._saved.items():
            setattr(mod, name, val)
        self.tmp.cleanup()

    def _swap(self, mod, name: str, value) -> None:
        self._saved[(mod, name)] = getattr(mod, name)
        setattr(mod, name, value)

    # -- helpers ---------------------------------------------------------------------------------
    def folder_of(self, sid: str, title_hint: str = "") -> Path:
        info = sessions.find_session(sid)
        self.assertTrue(info and info.get("ok"), f"{sid} did not resolve: {info}")
        return Path(info["folder"])

    def catalog_rows(self) -> list[dict]:
        path = sessions.vault_root() / "catalog.jsonl"
        if not path.is_file():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


class TestFiling(VaultCase):
    def test_a_conversation_is_filed_before_it_is_wiped(self) -> None:
        out = sessions.vault_live(reason="new_session")
        self.assertTrue(out.get("ok"), out)
        folder = Path(out["folder"])
        self.assertTrue(folder.is_dir(), folder)
        for name in ("session.json", "transcript.md", "turns.jsonl", "digest.md", "session.zip"):
            self.assertTrue((folder / name).is_file(), f"{name} missing from {folder}")
        self.assertEqual(out["turns"], 4)
        self.assertTrue(out.get("verified"), out)

    def test_the_folder_is_dated_and_named_after_the_session(self) -> None:
        out = sessions.vault_live(reason="new_session")
        folder = Path(out["folder"])
        rel = folder.relative_to(sessions.vault_root())
        self.assertEqual(len(rel.parts), 3, rel.parts)
        # Compared against the vault's OWN naming source, not a literal. This asserted "September"
        # and went red the moment the calendar left September: the folder is named from
        # sessions.MONTHS[when.tm_mon - 1], so the assertion has to be named from it too.
        when = time.localtime()
        self.assertEqual(rel.parts[0], f"{when.tm_year:04d}",
                         f"the folder must be dated this year: {rel.parts}")
        self.assertEqual(rel.parts[1], sessions.MONTHS[when.tm_mon - 1],
                         f"the folder must be named for this month: {rel.parts}")
        self.assertTrue(rel.parts[2].startswith(self.live), rel.parts[2])
        self.assertIn("session-history-module", rel.parts[2])

    def test_filing_the_same_session_twice_does_not_duplicate_the_catalog(self) -> None:
        first = sessions.vault_live(reason="new_session")
        second = sessions.vault_live(reason="manual")
        self.assertTrue(second.get("ok"), second)
        rows = [r for r in self.catalog_rows() if r.get("sid") == self.live]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(Path(first["folder"]), Path(second["folder"]))

    def test_the_transcript_is_readable_by_a_human(self) -> None:
        out = sessions.vault_live(reason="new_session")
        text = (Path(out["folder"]) / "transcript.md").read_text(encoding="utf-8")
        self.assertIn("OPERATOR", text)
        self.assertIn("AGENT", text)
        self.assertIn("needled phrase", text)
        self.assertIn(self.live, text)

    def test_the_zip_holds_what_the_folder_holds(self) -> None:
        import zipfile

        out = sessions.vault_live(reason="new_session")
        with zipfile.ZipFile(Path(out["folder"]) / "session.zip") as z:
            names = set(z.namelist())
            self.assertTrue({"session.json", "transcript.md", "turns.jsonl", "digest.md"} <= names, names)
            self.assertIn("needled phrase", z.read("transcript.md").decode("utf-8"))

    def test_an_empty_conversation_is_not_filed(self) -> None:
        st = compaction._load_state()
        st["session_id"] = "20260919-999999-none"
        compaction._write_state(st)
        out = sessions.vault_live(reason="new_session")
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("skipped"), "empty_session")

    def test_the_readable_catalog_names_the_session(self) -> None:
        sessions.vault_live(reason="new_session")
        md = (sessions.vault_root() / "CATALOG.md").read_text(encoding="utf-8")
        self.assertIn("# Session vault", md)
        self.assertIn(self.live, md)
        self.assertIn("sessions filed: **1**", md)

    def test_the_console_can_tell_where_the_last_chat_went(self) -> None:
        # Regression: the state schema keeps only the keys it declares, so this report used to be
        # written and then silently dropped on the next read.
        sessions.vault_live(reason="new_session")
        st = compaction._load_state()
        self.assertTrue(st.get("last_vault"), "the filed-session report did not survive the write")
        self.assertEqual(st["last_vault"]["sid"], self.live)
        self.assertEqual(st["last_vault"]["reason"], "new_session")

    def test_the_digest_carries_the_thread_not_a_stub(self) -> None:
        # A session filed out of a live conversation has no rollups yet. The digest must be built
        # from the turns anyway, or a thread reopened from the vault arrives with no context.
        out = sessions.vault_live(reason="new_session")
        digest = (Path(out["folder"]) / "digest.md").read_text(encoding="utf-8")
        self.assertIn("digest of session", digest)
        self.assertIn("opening question: we need a session history module", digest)
        self.assertGreater(len(digest), 120, digest)

    def test_a_recovered_session_also_carries_a_digest(self) -> None:
        legacy = Path(compaction.SESSIONS) / "session-1700000000.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({"messages": [{"role": "user", "content": "an old thread"}]}),
                          encoding="utf-8")
        sessions.adopt()
        info = sessions.find_session("legacy-1700000000")
        self.assertTrue(info and info.get("ok"), info)
        digest = (Path(info["folder"]) / "digest.md").read_text(encoding="utf-8")
        self.assertIn("an old thread", digest)


class TestFinding(VaultCase):
    def setUp(self) -> None:
        super().setUp()
        sessions.vault_live(reason="new_session")

    def test_the_catalog_lists_it_newest_first(self) -> None:
        out = sessions.catalog()
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["sessions"][0]["sid"], self.live)

    def test_a_prefix_id_does_not_answer_for_a_longer_one(self) -> None:
        # A second session whose id begins with the first one's id must not shadow it.
        other = self.live + "-extra"
        _seed_journal(other, [("user", "a later session that shares a prefix")])
        sessions.vault_session(sid=other, reason="test")
        self.assertIn(self.live, sessions.find_session(self.live)["folder"])
        self.assertIn(other, sessions.find_session(other)["folder"])
        self.assertNotEqual(sessions.find_session(self.live)["folder"],
                            sessions.find_session(other)["folder"])

    def test_label_names_a_session(self) -> None:
        out = sessions.label(self.live, title="Session vault build", tags=["lygo", "vault"],
                             note="built and tested")
        self.assertTrue(out.get("ok"), out)
        man = json.loads((self.folder_of(self.live) / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(man["title"], "Session vault build")
        self.assertEqual(man["tags"], ["lygo", "vault"])
        self.assertEqual(man["note"], "built and tested")
        row = [r for r in self.catalog_rows() if r.get("sid") == self.live][0]
        self.assertEqual(row["title"], "Session vault build")
        md = (sessions.vault_root() / "CATALOG.md").read_text(encoding="utf-8")
        self.assertIn("Session vault build", md)

    def test_pinning_is_recorded(self) -> None:
        sessions.label(self.live, pinned=True)
        self.assertIn(self.live, sessions.stats()["pinned"])

    def test_search_finds_a_phrase_inside_the_transcript(self) -> None:
        out = sessions.search("needled phrase")
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out["hits"], out)
        self.assertEqual(out["hits"][0]["sid"], self.live)
        self.assertIn("needled phrase", out["hits"][0]["snippet"])

    def test_search_finds_a_session_by_tag(self) -> None:
        sessions.label(self.live, tags=["compaction", "usb"])
        out = sessions.search("usb")
        self.assertTrue(out["hits"], out)

    def test_searching_nothing_says_so(self) -> None:
        self.assertFalse(sessions.search("")["ok"])

    def test_open_reads_the_session_back(self) -> None:
        out = sessions.open_session(self.live)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out["turns"], 4)
        self.assertIn("needled phrase", out["transcript_text"])
        self.assertEqual(len(out["messages"]), 4)
        self.assertEqual(out["messages"][0]["role"], "user")

    def test_open_bounds_a_long_transcript_without_losing_either_end(self) -> None:
        long_sid = "20260919-140000-long"
        _seed_journal(long_sid, [("user", "START-of-the-thread " + "x" * 4000),
                                 ("assistant", "y" * 4000),
                                 ("user", "END before it clears the window")])
        sessions.vault_session(sid=long_sid, reason="test")
        out = sessions.open_session(long_sid, chars=2000)
        self.assertTrue(out.get("truncated"), out.get("chars_returned"))
        self.assertIn("START-of-the-thread", out["transcript_text"])
        self.assertIn("END before it clears the window", out["transcript_text"])
        self.assertLessEqual(out["chars_returned"], 2200)

    def test_an_unknown_id_does_not_resolve(self) -> None:
        self.assertFalse(sessions.open_session("20260101-000000-nope")["ok"])
        info = sessions.find_session("none-at-all")
        self.assertFalse(info and info.get("ok"), info)


class TestAdoptingOlderHistory(VaultCase):
    def test_a_legacy_backup_is_imported_and_left_in_place(self) -> None:
        legacy = Path(compaction.SESSIONS) / "session-1700000000.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({"messages": [
            {"role": "user", "content": "an old conversation about the lattice"},
            {"role": "assistant", "content": "logged."},
        ]}), encoding="utf-8")
        out = sessions.adopt()
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out["count"], 1, out)
        self.assertTrue(legacy.is_file(), "adopt moved the original - it must never move it")
        self.assertIn("legacy-1700000000", out["adopted"])
        row = sessions.catalog()["sessions"][0]
        self.assertEqual(row["sid"], "legacy-1700000000")
        self.assertIn("recovered", row["tags"])
        self.assertTrue((sessions.vault_root() / row["folder"] / "transcript.md").is_file())

    def test_a_dormant_journal_is_adopted_but_the_live_one_is_left_alone(self) -> None:
        _seed_journal("20260919-130000-abcd", [("user", "an older journal nobody filed")])
        out = sessions.adopt()
        self.assertIn("20260919-130000-abcd", out["adopted"])
        self.assertNotIn(self.live, out["adopted"])
        self.assertTrue(Path(compaction.journal_path("20260919-130000-abcd")).is_file())

    def test_adopting_twice_is_stable(self) -> None:
        _seed_journal("20260919-130000-abcd", [("user", "an older journal nobody filed")])
        sessions.adopt()
        again = sessions.adopt()
        self.assertEqual(again["count"], 0, again)
        self.assertEqual(sessions.catalog()["count"], 1)

    def test_stats_reports_what_is_still_unfiled(self) -> None:
        _seed_journal("20260919-130000-abcd", [("user", "not filed yet")])
        st = sessions.stats()
        self.assertTrue(st.get("ok"), st)
        self.assertIn("20260919-130000-abcd", st["unfiled_journals"])
        self.assertEqual(st["live_session"], self.live)


class TestPickingAThreadBackUp(VaultCase):
    def test_resume_loads_the_messages_back_and_carries_the_thread(self) -> None:
        filed = sessions.vault_live(reason="new_session")
        self.assertTrue(filed.get("ok"), filed)
        # a different conversation is now live
        st = compaction._load_state()
        st["session_id"] = "20260919-150000-new1"
        compaction._write_state(st)
        _seed_journal("20260919-150000-new1", [("user", "the conversation in progress")])

        out = sessions.resume(self.live)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out["resumed_from"], self.live)
        self.assertEqual(out["turns"], 4)
        self.assertNotEqual(out["session_id"], self.live)

        current = json.loads(Path(self.continuity.CURRENT).read_text(encoding="utf-8"))
        self.assertEqual(len(current["messages"]), 4)
        self.assertIn("needled phrase", current["messages"][0]["content"])
        self.assertEqual(current["resumed_from"], self.live)

        st = compaction._load_state()
        self.assertEqual(st["resumed_from"], self.live)
        self.assertEqual(st["session_id"], out["session_id"])
        self.assertTrue(st.get("carry"), "resuming must install a carry so the agent keeps the thread")
        self.assertEqual(st["carry_turns"], 4)
        self.assertEqual(out["lineage_error"], "", "the resume lineage did not survive the state write")
        self.assertGreater(out["carry_chars"], 100, "the reopened thread must arrive with context")

    def test_resuming_files_the_conversation_it_interrupts(self) -> None:
        sessions.vault_live(reason="new_session")
        st = compaction._load_state()
        st["session_id"] = "20260919-150000-new1"
        compaction._write_state(st)
        _seed_journal("20260919-150000-new1", [("user", "the conversation in progress")])
        out = sessions.resume(self.live)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out["filed_previous"], "20260919-150000-new1")
        self.assertIn("20260919-150000-new1", [r["sid"] for r in self.catalog_rows()])

    def test_resuming_something_that_is_not_there_says_so(self) -> None:
        out = sessions.resume("20260101-000000-nope")
        self.assertFalse(out["ok"])
        self.assertIn("not_found", out["error"])


class TestItCannotCostTheConsole(VaultCase):
    def test_a_broken_vault_answers_instead_of_raising(self) -> None:
        self._swap(sessions, "VAULT", self.root / "no" / "such" / "vault")
        original = sessions.vault_root

        def _boom():
            raise OSError("the vault disk was pulled")

        sessions.vault_root = _boom  # type: ignore[assignment]
        try:
            for fn, args in ((sessions.safe_stats, ()), (sessions.safe_catalog, ()),
                             (sessions.safe_search, ("anything",)), (sessions.safe_open, ("x",)),
                             (sessions.safe_resume, ("x",)), (sessions.safe_vault_live, ())):
                out = fn(*args)
                self.assertFalse(out.get("ok"), (fn, out))
                self.assertIn("error", out)
        finally:
            sessions.vault_root = original  # type: ignore[assignment]

    def test_new_session_still_wipes_the_window_when_the_vault_is_broken(self) -> None:
        Path(self.continuity.CURRENT).parent.mkdir(parents=True, exist_ok=True)
        self.continuity.save_session([{"role": "user", "content": "hi"}])
        real = sessions.vault_live
        sessions.vault_live = lambda *a, **k: (_ for _ in ()).throw(OSError("vault offline"))  # type: ignore
        try:
            self.continuity.new_session()
        finally:
            sessions.vault_live = real  # type: ignore[assignment]
        self.assertEqual(self.continuity.load_session(), [],
                         "a broken vault must not stop the operator starting a new session")


class TestTheAgentsOwnLimbs(VaultCase):
    """The agent reaches the vault through the same functions the operator's panel uses."""

    def setUp(self) -> None:
        super().setUp()
        import limbs

        self.limbs = limbs
        sessions.vault_live(reason="new_session")
        sessions.label(self.live, title="Session vault build", tags=["lygo", "vault"])

    def test_the_agent_can_list_the_vault(self) -> None:
        out = self.limbs.extra("session_list", {})
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["sessions"][0]["title"], "Session vault build")
        self.assertIn("lygo", out["sessions"][0]["tags"])

    def test_the_agent_can_find_a_session_by_phrase(self) -> None:
        out = self.limbs.extra("session_search", {"q": "needled phrase"})
        self.assertTrue(out.get("hits"), out)
        self.assertEqual(out["hits"][0]["sid"], self.live)

    def test_the_agent_can_read_a_session_back(self) -> None:
        out = self.limbs.extra("session_open", {"sid": self.live, "chars": 4000})
        self.assertTrue(out.get("ok"), out)
        self.assertIn("needled phrase", out["transcript_text"])
        self.assertNotIn("messages", out, "the transcript is the form the model should read")

    def test_the_agent_can_label_what_it_found(self) -> None:
        out = self.limbs.extra("session_label", {"sid": self.live, "note": "found this again",
                                                 "tags": "compaction, vault"})
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out["tags"], ["compaction", "vault"])
        self.assertEqual(out["note"], "found this again")

    def test_the_agent_can_reopen_an_older_session(self) -> None:
        st = compaction._load_state()
        st["session_id"] = "20260919-150000-new1"
        compaction._write_state(st)
        _seed_journal("20260919-150000-new1", [("user", "the live one")])
        out = self.limbs.extra("session_resume", {"sid": self.live})
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out["resumed_from"], self.live)
        self.assertNotIn("messages", out)

    def test_the_schemas_are_registered_so_the_model_can_ask(self) -> None:
        found: set[str] = set()
        for val in vars(self.limbs).values():
            if isinstance(val, list) and val and isinstance(val[0], dict) and "function" in val[0]:
                found |= {t["function"].get("name") for t in val
                          if isinstance(t, dict) and "function" in t}
        for name in ("session_list", "session_open", "session_search", "session_label",
                     "session_resume"):
            self.assertIn(name, found, f"{name} is not offered to the model")


if __name__ == "__main__":
    unittest.main(verbosity=2)
