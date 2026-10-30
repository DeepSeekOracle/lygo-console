from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import compaction as C  # noqa: E402

NAMES = ("SESSIONS", "ROLLUPS", "CHECKPOINTS", "ARCHIVE", "BUNDLES", "INDEX", "STATE")


def fill_turns(n: int, text: str = "the steward asked about the {i} subject") -> None:
    """Record n alternating turns with distinctive text and a file path in each."""
    for i in range(1, n + 1):
        role = "user" if i % 2 else "assistant"
        C.record(role, text.format(i=i) + f" and file C:\\work\\doc{i}.txt")


class CompactionCase(unittest.TestCase):
    """Every test runs against its own store: the live save/ tree is never touched."""

    def setUp(self):
        self._saved = {n: getattr(C, n) for n in NAMES}
        self.tmp = Path(tempfile.mkdtemp(prefix="lygo_compact_test_"))
        C.SESSIONS = self.tmp / "sessions"
        C.ROLLUPS = C.SESSIONS / "rollups"
        C.CHECKPOINTS = C.SESSIONS / "checkpoints"
        C.ARCHIVE = self.tmp / "archive"
        C.BUNDLES = C.ARCHIVE / "bundles"
        C.INDEX = C.ARCHIVE / "index.jsonl"
        C.STATE = C.SESSIONS / "compaction_state.json"
        # small budgets so a handful of turns exercises every path
        self._saved_knobs = {k: getattr(C, k) for k in
                             ("ROLLUP_TURNS", "LIVE_KEEP_TURNS", "AUTOSAVE_EVERY", "CHECKPOINT_KEEP")}
        C.ROLLUP_TURNS = 6
        C.LIVE_KEEP_TURNS = 4
        C.AUTOSAVE_EVERY = 3
        C.CHECKPOINT_KEEP = 2

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(C, n, v)
        for k, v in self._saved_knobs.items():
            setattr(C, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------------------
    def fill(self, n: int, text: str = "the steward asked about the {i} subject") -> None:
        fill_turns(n, text)

    # -- T1 journal ------------------------------------------------------------------------
    def test_journal_keeps_verbatim_text_and_stamps(self):
        body = 'line one\nline "two"\nunicode Δ9Φ963 — done'
        r = C.record("user", body)
        self.assertTrue(r.get("ok"))
        recs = C.read_journal()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["content"], body)
        self.assertEqual(recs[0]["role"], "user")
        self.assertIn("ts", recs[0])
        self.assertRegex(recs[0]["iso"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertTrue(C.journal_path().is_file())

    def test_record_is_idempotent_on_a_repeated_turn(self):
        C.record("user", "same question")
        again = C.record("user", "same question")
        self.assertTrue(again.get("duplicate"))
        self.assertEqual(len(C.read_journal()), 1)
        # a *later* different message still lands, and the same text on a new turn is not swallowed
        C.record("assistant", "an answer")
        C.record("user", "same question")
        self.assertEqual(len(C.read_journal()), 3)

    def test_index_is_monotonic_across_sessions(self):
        C.record("user", "one")
        C.record("assistant", "two")
        self.fill(6)
        recs = C.read_journal()
        idx = [r["i"] for r in recs]
        self.assertEqual(idx, sorted(idx))
        self.assertEqual(len(set(idx)), len(idx))

    def test_empty_and_bad_roles_are_refused(self):
        self.assertFalse(C.record("user", "   ").get("ok"))
        self.assertFalse(C.record("wizard", "hello").get("ok"))

    def test_torn_last_line_does_not_lose_the_journal(self):
        self.fill(5)
        p = C.journal_path()
        with p.open("a", encoding="utf-8") as f:
            f.write('{"i": 9, "role": "user", "content": "half a li')
        recs = C.read_journal()
        self.assertEqual(len(recs), 5)
        self.assertTrue(all(isinstance(r.get("content"), str) for r in recs))

    def test_image_turns_are_recorded_with_a_label(self):
        content = [
            {"type": "text", "text": "what is in this picture"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]
        r = C.record("user", content)
        self.assertTrue(r.get("ok"))
        rec = C.read_journal()[-1]
        self.assertEqual(rec.get("image"), "image")
        self.assertIn("what is in this picture", C.content_text(rec["content"]))
        self.assertIn("[image]", C.content_text(rec["content"]))

    def test_concurrent_writers_leave_only_valid_lines(self):
        errors: list[str] = []

        def worker(tag: str) -> None:
            for i in range(25):
                try:
                    C.record("user", f"{tag} message {i} with a distinctive tail")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{type(e).__name__}: {e}")

        threads = [threading.Thread(target=worker, args=(f"t{n}",)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        raw = C.journal_path().read_text(encoding="utf-8").splitlines()
        self.assertTrue(raw)
        for line in raw:
            obj = json.loads(line)  # a torn line fails here
            self.assertIn(obj.get("role"), {"user", "assistant", "system", "tool"})
        self.assertGreaterEqual(len(C.read_journal()), 4)

    # -- window sizing ---------------------------------------------------------------------
    def test_budget_leaves_room_for_prompt_and_answer(self):
        b = C.window_budget(16384)
        self.assertEqual(b["ctx"], 16384)
        self.assertEqual(b["history_tokens"], b["ctx"] - b["system_reserve"] - b["answer_reserve"] - b["safety_reserve"])
        self.assertEqual(b["compact_at"], int(b["history_tokens"] * C.AUTO_COMPACT_AT))
        self.assertGreater(b["history_tokens"], 4000)
        self.assertLess(b["history_tokens"], b["ctx"])

    def test_trim_keeps_the_newest_turns_even_over_budget(self):
        huge = [{"role": "user", "content": "x" * 200000}, {"role": "assistant", "content": "y" * 200000}]
        small = [{"role": "user", "content": "newest question"}]
        kept, dropped, info = C.trim_messages(huge + small, ctx=8192)
        self.assertEqual(kept[-1]["content"], "newest question")
        self.assertGreaterEqual(len(kept), 3)  # the newest turns are never evicted by their own size
        self.assertEqual(dropped, info["total"] - len(kept))

    def test_trim_keeps_short_history_whole(self):
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(6)]
        kept, dropped, _ = C.trim_messages(msgs, ctx=8192)
        self.assertEqual(len(kept), 6)
        self.assertEqual(dropped, 0)

    def test_window_pct_is_a_percentage(self):
        msgs = [{"role": "user", "content": "z" * 1000}]
        self.assertGreater(C.window_pct(msgs, ctx=16384), 0)

    # -- T2 rollups and carry ---------------------------------------------------------------
    def test_compact_folds_only_what_left_the_window(self):
        self.fill(30)
        res = C.compact(reason="test")
        self.assertTrue(res["ok"])
        # Only COMPLETE batches fold; a partial tail waits, still in the journal and still inside the
        # window the token budget pays for. Folding whatever happened to expire is what left 1,660
        # two-turn rollups in the real store and a carry-over block that quoted 12 earlier turns.
        left = 30 - C.LIVE_KEEP_TURNS
        self.assertEqual(res["folded"], (left // C.ROLLUP_TURNS) * C.ROLLUP_TURNS)
        self.assertEqual(res["held"], left % C.ROLLUP_TURNS)
        self.assertEqual(res["live_turns"], C.LIVE_KEEP_TURNS)
        self.assertTrue(list(C.ROLLUPS.glob("*.json")))
        self.assertTrue(list(C.ROLLUPS.glob("*.md")))
        roll = json.loads(sorted(C.ROLLUPS.glob("*.json"))[0].read_text(encoding="utf-8"))
        for key in ("digest", "highlights", "from_iso", "to_iso", "turns", "sha256", "artifacts"):
            self.assertIn(key, roll)
        self.assertIn("C:\\work\\doc1.txt", roll["digest"])

    def test_compact_is_idempotent(self):
        self.fill(20)
        first = C.compact(reason="one")
        second = C.compact(reason="two")
        self.assertEqual(second["folded"], 0)
        note = str(second.get("note") or "")
        self.assertTrue("nothing has left the live window" in note or "holding" in note, note)
        self.assertEqual(first["covered_upto"], second["covered_upto"])

    def test_a_turn_expiring_alone_never_becomes_its_own_rollup(self):
        """The guard for the 1,660-file store: EVERY rollup a fold writes is a full batch, and the
        turns that expire without completing one are held. A page of two-turn digests is what made
        the store unreadable and starved the carry-over block down to 12 earlier turns."""
        total = 0
        for n in (5, 6, 7, 8, 9, 5, 4, 3):
            self.fill(n)
            total += n
            res = C.compact(reason=f"after {total} turns")
            # The invariant: a fold writes full batches only. Whatever expired but does not complete
            # a batch is HELD - it stays in the journal, inside the window the budget pays for.
            self.assertEqual(res["folded"] % C.ROLLUP_TURNS, 0, res)
            self.assertLess(res["held"], C.ROLLUP_TURNS, res)
            # everything that has left the window is either folded already or held - nothing else
            self.assertEqual(res["covered_upto"] + res["held"], total - res["live_turns"], res)
            for f in C.ROLLUPS.glob("*.json"):
                self.assertEqual(json.loads(f.read_text(encoding="utf-8"))["turns"], C.ROLLUP_TURNS,
                                 f"{f.name} is a partial rollup")
        files = sorted(C.ROLLUPS.glob("*.json"))
        self.assertTrue(files, "a store this long must have folded something")
        self.assertGreaterEqual(len(files), 5)

    def test_consolidating_thin_rollups_is_lossless_and_idempotent(self):
        """Repair path for a store an earlier build inflated: merge thin rollups, lose no turn line,
        and a second run must be a no-op (never fuse the fat ones into one blob)."""
        self.fill(48)
        st = C._load_state()
        st["rollups"] = []
        C._write_state(st)
        thin = []
        recs = C.read_journal()
        for i in range(1, 13):                       # twelve 2-turn rollups, as the old build wrote
            thin.append(C._write_rollup(st, recs[(i - 1) * 2: i * 2]))
        st["covered_upto"] = 24
        C._write_state(st)
        before = [ln for o in thin for ln in str(o["digest"]).splitlines() if ln.strip()]
        rep = C.consolidate_rollups(apply=True)
        self.assertTrue(rep["ok"], rep)
        after = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(C.ROLLUPS.glob("*.json"))]
        self.assertLess(len(after), len(thin))
        merged = [ln for o in after for ln in str(o["digest"]).splitlines() if ln.strip()]
        for ln in before:
            self.assertIn(ln, merged)                # every turn line survived the merge
        self.assertEqual(sum(int(o["turns"]) for o in after), sum(int(o["turns"]) for o in thin))
        again = C.consolidate_rollups(apply=True)
        self.assertEqual(again["rollups_in"], 0, again)
        self.assertEqual(again["needs_consolidation"] if "needs_consolidation" in again else 0, 0)

    def test_carry_over_names_what_was_compacted(self):
        self.fill(30, text="we agreed the {i} plan for the stick")
        C.compact(reason="test")
        block = C.carry_over()
        self.assertIn("EARLIER CONVERSATION", block)
        self.assertIn("recall_history", block)
        self.assertIn("plan for the stick", block)
        self.assertLessEqual(len(block), C.CARRY_CAP + 40)

    def test_carry_over_withholds_a_quarantined_digest(self):
        self.fill(20, text="a note about the {i} thing")
        C.compact(reason="test")
        fake = type(sys)("p0_hook")
        fake.gate_prompt = lambda text: {"verdict": "QUARANTINE", "reason": "test"}
        saved = sys.modules.get("p0_hook")
        sys.modules["p0_hook"] = fake
        try:
            block = C.carry_over()
        finally:
            if saved is None:
                sys.modules.pop("p0_hook", None)
            else:
                sys.modules["p0_hook"] = saved
        self.assertIn("withheld", block)
        self.assertNotIn("a note about the", block)

    def test_auto_compact_only_fires_when_the_window_is_nearly_full(self):
        C.record("user", "small")
        quiet = C.maybe_auto_compact([{"role": "user", "content": "small"}], ctx=32768)
        self.assertFalse(quiet["compacted"])
        big = [{"role": "user", "content": "w" * 40000} for _ in range(4)]
        fill_turns(20)
        loud = C.maybe_auto_compact(big, ctx=8192)
        self.assertTrue(loud["compacted"])

    def test_auto_compact_folds_when_a_rollup_is_due_without_a_huge_request(self):
        """The portal sends at most 60 messages; the journal is the record that must fold itself."""
        self.fill(12)  # keep=4 in this case, rollup=6 → 8 pending turns, a fold is due
        out = C.maybe_auto_compact([{"role": "user", "content": "hello"}], ctx=32768)
        self.assertTrue(out["compacted"], out)
        self.assertGreater(int(out.get("folded") or 0), 0)
        self.assertIn("EARLIER CONVERSATION", C.carry_over())

    def test_auto_compact_sees_journal_tokens_not_just_the_http_payload(self):
        for i in range(10):
            C.record("user", ("topic-%s " % i) + ("w" * 3000))
            C.record("assistant", ("reply-%s " % i) + ("y" * 3000))
        out = C.maybe_auto_compact([{"role": "user", "content": "hi"}], ctx=8192)
        self.assertTrue(out["compacted"], out)

    def test_auto_compact_never_raises(self):
        saved = C.window_budget
        C.window_budget = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            out = C.maybe_auto_compact([{"role": "user", "content": "hi"}])
        finally:
            C.window_budget = saved
        self.assertFalse(out["compacted"])
        self.assertIn("boom", str(out.get("error")))

    # -- autosave --------------------------------------------------------------------------
    def test_autosave_cadence_and_rotation(self):
        C.record("user", "one")
        self.assertFalse(C.checkpoint().get("saved"))
        for i in range(C.AUTOSAVE_EVERY):
            C.record("assistant", f"answer {i}")
        first = C.checkpoint()
        self.assertTrue(first.get("saved"), first)
        self.assertTrue(Path(first["path"]).is_file())
        for i in range(4):
            C.record("user", f"more {i}")
            C.checkpoint()
        kept = sorted(C.CHECKPOINTS.glob("*.md"))
        self.assertLessEqual(len(kept), C.CHECKPOINT_KEEP)
        self.assertGreaterEqual(len(kept), 1)

    def test_save_now_is_the_manual_button(self):
        self.fill(24)
        out = C.save_now(reason="button")
        self.assertTrue(out["ok"])
        self.assertTrue(out["checkpoint"]["saved"])
        self.assertIn("compact", out)
        self.assertEqual(out["status"]["ok"], True)

    # -- T3 seal / archive / index ---------------------------------------------------------
    def test_seal_zips_verifies_indexes_and_starts_a_new_session(self):
        self.fill(30)
        C.compact(reason="test")
        res = C.seal(reason="test")
        self.assertTrue(res["ok"], res)
        with zipfile.ZipFile(res["archive"]) as z:
            self.assertIsNone(z.testzip())
            names = set(z.namelist())
            self.assertTrue({"journal.jsonl", "manifest.json", "conversation.md"} <= names)
            self.assertTrue(any(n.startswith("rollups/") for n in names))
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
            self.assertEqual(manifest["turns"], 30)
            self.assertEqual(C.digest_of(z.read("journal.jsonl").decode("utf-8")), manifest["journal_sha256"])
        entries = C.index_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["turns"], 30)
        self.assertEqual(entries[0]["sid"], res["sealed"])
        self.assertTrue(Path(entries[0]["zip"]).is_file())
        # a new live session holds only the carried tail, and the journal continues the index
        new_recs = C.read_journal()
        self.assertEqual(len(new_recs), C.LIVE_KEEP_TURNS)
        self.assertEqual(new_recs[0].get("carried_from"), res["sealed"])
        self.assertEqual(C.status()["session_id"], res["new_session"])

    def test_sealed_session_is_recallable_verbatim(self):
        C.record("user", "the vault passphrase hint was purple lantern")
        self.fill(20)
        res = C.seal(reason="test")
        live = json.dumps([r.get("content") for r in C.read_journal()])
        self.assertNotIn("purple lantern", live)  # it really did leave the live journal
        out = C.recall("purple lantern")
        self.assertTrue(out["ok"])
        self.assertTrue(out["hits"], out)
        self.assertIn("purple lantern", out["hits"][0]["snippet"])
        self.assertEqual(out["hits"][0]["source"], res["sealed"])
        self.assertIn("purple lantern", C.recall_text("purple lantern"))

    def test_read_transcript_returns_the_whole_sealed_session(self):
        self.fill(12)
        res = C.seal(reason="test")
        out = C.read_transcript(res["sealed"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["manifest"]["turns"], 12)
        self.assertIn("LYGO conversation transcript", out["text"])
        self.assertIn("doc1.txt", out["text"])
        self.assertFalse(C.read_transcript("no-such-session").get("ok"))

    def test_seal_of_an_empty_session_refuses_without_touching_the_store(self):
        out = C.seal(reason="test")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "empty_session")
        self.assertEqual(C.index_entries(), [])
        self.assertFalse(list(C.ARCHIVE.glob("*.zip")))

    def test_auto_seal_fires_only_past_the_cap(self):
        saved = C.JOURNAL_MAX_TURNS
        C.JOURNAL_MAX_TURNS = 5
        try:
            self.fill(4)
            self.assertFalse(C.auto_seal_if_big()["sealed"])
            self.fill(8)
            self.assertTrue(C.auto_seal_if_big()["sealed"])
        finally:
            C.JOURNAL_MAX_TURNS = saved

    def test_bundle_merges_old_archives_and_keeps_the_members(self):
        self.fill(10)
        a = C.seal(reason="one")
        C.record("user", "second session content")
        b = C.seal(reason="two")
        old = time.time() - 10 * 86400
        for res in (a, b):
            os.utime(res["archive"], (old, old))
        out = C.bundle_archives(older_than_days=5)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["bundled"], 2)
        month = time.strftime("%Y-%m", time.localtime(old))
        bundle = C.BUNDLES / f"{month}.zip"
        self.assertTrue(bundle.is_file(), sorted(p.name for p in C.BUNDLES.iterdir()))
        with zipfile.ZipFile(bundle) as z:
            self.assertIsNone(z.testzip())
            self.assertIn(Path(a["archive"]).name, z.namelist())
            self.assertIn(Path(b["archive"]).name, z.namelist())
        # the loose copies were moved aside into the bundle folder, not deleted from the record
        self.assertTrue((C.BUNDLES / Path(a["archive"]).name).is_file())
        self.assertFalse(Path(a["archive"]).is_file() and Path(a["archive"]).parent == C.ARCHIVE)

    # -- recall ----------------------------------------------------------------------------
    def test_keywords_drop_stopwords_and_short_words(self):
        keys = C.keywords("What did we say about the purple lantern in the vault?")
        self.assertIn("purple", keys)
        self.assertIn("lantern", keys)
        self.assertIn("vault", keys)
        self.assertNotIn("the", keys)
        self.assertNotIn("say", keys)

    def test_recall_without_keywords_explains_itself(self):
        out = C.recall("of to it")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "no_keywords")
        self.assertIn("recall: no_keywords", C.recall_text("of to it"))

    def test_recall_reports_a_clean_miss(self):
        self.fill(5)
        out = C.recall("xyzzy")
        self.assertTrue(out["ok"])
        self.assertEqual(out["hits"], [])
        self.assertIn("nothing in the saved conversation", C.recall_text("xyzzy"))

    def test_recall_scores_matches_and_prefers_the_newest(self):
        C.record("user", "the lantern plan is old")
        C.record("assistant", "noted")
        C.record("user", "the lantern plan changed twice: lantern again")
        out = C.recall("lantern plan")
        self.assertGreaterEqual(out["n_hits"], 2)
        self.assertGreaterEqual(out["hits"][0]["score"], out["hits"][-1]["score"])

    # -- status ----------------------------------------------------------------------------
    def test_the_window_report_is_what_the_engine_sees_not_the_whole_journal(self):
        """The pane asks for status() with NO messages, so the journal is all it has to go on.

        Reporting the journal as "in the window" made a 232-turn record read 3394.2% of the engine
        window while the turn actually sent was 6 messages at ~70% of it - a permanent false alarm
        that also kept promising "auto-compact next turn" when no fold was due. What the engine sees
        is the newest turns that fit the room.
        """
        for i in range(60):
            C.record("user", f"turn {i} " + "x" * 4000)
            C.record("assistant", f"answer {i} " + "y" * 4000)
        st = C.status(ctx=32768)
        w = st["window"]
        room = int(w["history_tokens"])
        self.assertGreater(w["journal_tokens"], room, "precondition: the record is bigger than one window")
        self.assertLessEqual(w["live_tokens"], room, "the window report handed over more than the history room")
        self.assertLess(w["live_tokens"], w["journal_tokens"], "the report confused the record with the request")
        self.assertLessEqual(w["used_pct"], 100.0,
                             f"a window percentage above 100 ({w['used_pct']}%) is a mislabel, not a state")

    def test_status_shape_and_consistency(self):
        self.fill(9)
        C.compact(reason="test")
        C.seal(reason="test")
        st = C.status(ctx=32768)
        self.assertTrue(st["ok"], st)
        self.assertEqual(st["window"]["ctx"], 32768)
        for key in ("session_id", "turns_live", "turns_compacted", "turns_sealed", "journal_bytes",
                    "rollups", "compactions", "sessions_sealed", "sealed_bytes", "last_save_iso",
                    "paths", "recent_sessions", "roll_due"):
            self.assertIn(key, st)
        self.assertGreaterEqual(st["sessions_sealed"], 1)
        self.assertIn("journal", st["paths"])

    def test_status_survives_a_missing_store(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        st = C.safe_status()
        self.assertIn("ok", st)

    def test_safe_wrappers_never_raise(self):
        self.assertFalse(C.safe_record("user", "").get("ok"))
        out = C.safe_pre_turn([{"role": "user", "content": "hello"}], ctx=8192)
        self.assertIn("autosave", out)
        self.assertIn("auto", out)

    def test_legacy_current_json_is_not_the_record_of_truth(self):
        """The old 40-message session file must not be what limits the record any more."""
        self.fill(60)
        recs = C.read_journal()
        self.assertEqual(len(recs), 60)
        self.assertEqual(C.status()["turns_live"], 60)


class WiringCase(CompactionCase):
    """The surfaces the console prompt, the limb registry and the portal read."""

    def test_system_reserve_reserves_the_digest_too(self):
        import continuity

        self.assertGreaterEqual(C.system_reserve(), int(continuity.PROMPT_CEILING_TOTAL / C.CHARS_PER_TOKEN))
        # The identity block keeps its own ceiling; the digest rides in a second, capped block.
        self.assertGreater(continuity.PROMPT_CEILING_TOTAL, continuity.PROMPT_CEILING)

    def test_the_digest_is_opt_in_and_capped(self):
        import continuity

        self.fill(20)
        C.compact(reason="test")
        self.assertNotIn("EARLIER CONVERSATION", continuity.compose_system(), "the identity block must not carry the digest")
        with_carry = continuity.compose_system(carry=True)
        self.assertIn("EARLIER CONVERSATION", with_carry)
        self.assertLessEqual(len(C.carry_over()), C.CARRY_CAP + 64)
        self.assertLessEqual(len(with_carry), continuity.PROMPT_CEILING_TOTAL)

    def test_the_live_system_message_opts_into_the_digest(self):
        """A local turn must actually read the digest, or auto-compact files history the model never sees."""
        import server

        self.fill(20)
        C.compact(reason="test")
        txt = str(server.local_system_message("local").get("content") or "")
        self.assertIn("EARLIER CONVERSATION", txt)

    def test_a_console_failure_never_becomes_a_prompt_error(self):
        """compose_system must degrade, never raise, when the record is unreadable."""
        import continuity

        C.STATE = self.tmp / "nope" / "state.json"
        out = continuity.compose_system(carry=True)
        self.assertIsInstance(out, str)
        self.assertGreater(len(out), 0)

    def test_recall_history_is_a_registered_limb(self):
        import limbs

        names = [t["function"]["name"] for t in limbs.EXTRA_SCHEMA]
        self.assertIn("recall_history", names)
        self.assertEqual(limbs.CANON_KEYS.get("recall_history"), ("q",))
        self.fill(12)
        out = limbs.extra("recall_history", {"query": "subject 3"})
        self.assertIsInstance(out, dict)
        self.assertTrue(out.get("ok"), out)
        self.assertTrue(out.get("hits"), out)  # the alias key resolved and the journal was searched

    def test_a_named_recall_is_run_by_the_host(self):
        """The operator naming a limb the model would not call still gets an answer."""
        import chat_loop

        self.assertEqual(chat_loop.AUTO_ONE.get("recall_history"), "q")

    def test_status_does_not_walk_the_archive(self):
        """A status call runs every turn: it must not rglob the archive on a USB stick."""
        self.fill(9)
        C.seal(reason="test")
        st = C.status()
        self.assertEqual(st["archive_bytes"], int(st["sealed_bytes"]) + int(st["journal_bytes"]))

    def test_index_reads_only_the_tail(self):
        """The index grows with the archive; a status read must be a tail read."""
        for i in range(9):
            C._index_append({"sid": f"s{i}", "turns": 1, "zip_bytes": 10, "sealed_iso": "2026-09-19T00:00:00"})
        got = C.index_entries(limit=3)
        self.assertEqual([e["sid"] for e in got], ["s8", "s7", "s6"])
        self.assertEqual(C._index_count(), 9)

    def test_the_console_still_boots_with_a_broken_record_module(self):
        """A broken record module must cost the record, never the console.

        Runs the real import in a subprocess with a sabotaged compaction.py ahead of it on the path:
        the console must come up, the window must fall back to the old trim, and the status route
        must answer rather than raise.
        """
        import subprocess

        with tempfile.TemporaryDirectory(prefix="lygo_broken_compact_") as td:
            (Path(td) / "compaction.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join([td, str(ROOT / "src")])
            code = (
                "import json, server;"
                "msgs=[{'role':'user','content':'x'*400} for _ in range(120)];"
                "kept,dropped,info=server.trim_messages(msgs,8192);"
                "print('LYGO_PROBE '+json.dumps({'available':server.COMPACTION_AVAILABLE,"
                "'kept':len(kept),'dropped':dropped,'status_ok':server.safe_status().get('ok'),"
                "'carry':server.carry_over()}))"
            )
            r = subprocess.run(
                [sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180
            )
            probe = [ln for ln in r.stdout.splitlines() if ln.startswith("LYGO_PROBE ")]
            self.assertTrue(probe, f"console did not import. stdout={r.stdout[-800:]} stderr={r.stderr[-1500:]}")
            out = json.loads(probe[-1][len("LYGO_PROBE ") :])
            self.assertFalse(out["available"])
            self.assertFalse(out["status_ok"], "the record route must answer honestly, not raise")
            self.assertEqual(out["carry"], "")
            self.assertTrue(out["kept"], "the old trim window must still bound the prompt")
            self.assertGreaterEqual(out["dropped"], 0)


if __name__ == "__main__":
    unittest.main()
