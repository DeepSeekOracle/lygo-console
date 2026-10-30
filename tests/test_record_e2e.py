"""End-to-end record (compaction) tests over the real HTTP surface.

The module is unit-tested in test_compaction.py; what these cover is the *wiring*, because every
defect this system has had was a wiring defect - the record existed and the route simply did not
call it. They run the real server (`server.Handler`) on an ephemeral port with the record's
directories pointed at a temp sandbox, so a test never writes the operator's own record.

No engine is required or started: /api/chat is deliberately NOT exercised here, because a chat
turn on this console is an engine call and a test must never load a model onto the operator's card.
"""
from __future__ import annotations

import http.client
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import compaction  # noqa: E402
import continuity  # noqa: E402
import server  # noqa: E402
import sessions  # noqa: E402

_httpd = None
_port = 0
_tmp: Path | None = None
_SAVED: dict = {}
_DIRS = ("SESSIONS", "ROLLUPS", "CHECKPOINTS", "ARCHIVE", "BUNDLES", "INDEX", "STATE")


def setUpModule() -> None:  # noqa: N802
    global _httpd, _port, _tmp
    for name in _DIRS:
        _SAVED[name] = getattr(compaction, name)
    _SAVED["AUTH_REQUIRED"] = server.AUTH_REQUIRED
    _tmp = Path(tempfile.mkdtemp(prefix="lygo_record_e2e_"))
    compaction.SESSIONS = _tmp / "sessions"
    compaction.ROLLUPS = compaction.SESSIONS / "rollups"
    compaction.CHECKPOINTS = compaction.SESSIONS / "checkpoints"
    compaction.ARCHIVE = _tmp / "archive"
    compaction.BUNDLES = compaction.ARCHIVE / "bundles"
    compaction.INDEX = compaction.ARCHIVE / "index.jsonl"
    compaction.STATE = compaction.SESSIONS / "compaction_state.json"
    # The vault writes too: point it at the sandbox as well, or a test would file its own
    # sessions into the operator's real vault.
    _SAVED["sessions.VAULT"] = sessions.VAULT
    _SAVED["continuity.CURRENT"] = continuity.CURRENT
    _SAVED["continuity.SESSIONS"] = continuity.SESSIONS
    sessions.VAULT = _tmp / "vault"
    continuity.SESSIONS = compaction.SESSIONS
    continuity.CURRENT = compaction.SESSIONS / "current.json"
    server.AUTH_REQUIRED = False
    _httpd = server._StickContainment(("127.0.0.1", 0), server.Handler)
    _port = _httpd.server_address[1]
    threading.Thread(target=_httpd.serve_forever, daemon=True).start()


def tearDownModule() -> None:  # noqa: N802
    if _httpd is not None:
        _httpd.shutdown()
        _httpd.server_close()
    server.AUTH_REQUIRED = _SAVED["AUTH_REQUIRED"]  # type: ignore[assignment]
    sessions.VAULT = _SAVED["sessions.VAULT"]
    continuity.CURRENT = _SAVED["continuity.CURRENT"]
    continuity.SESSIONS = _SAVED["continuity.SESSIONS"]
    for name in _DIRS:
        setattr(compaction, name, _SAVED[name])
    if _tmp is not None:
        shutil.rmtree(_tmp, ignore_errors=True)


def _seed(sid: str, pairs: list) -> None:
    """Write a journal the way the console does, so the routes have something real to file."""
    path = Path(compaction.journal_path(sid))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for i, (role, text) in enumerate(pairs):
            ts = time.time() - 600 + i * 10
            f.write(json.dumps({"iso": compaction.iso(ts), "ts": ts, "role": role,
                                "content": text, "chars": len(text),
                                "tokens": compaction.est_tokens(text)}) + "\n")


class TestTheVaultOverTheWire(unittest.TestCase):
    """The panel's own routes: file, list, read, search, name, reopen."""

    def setUp(self) -> None:
        shutil.rmtree(compaction.SESSIONS, ignore_errors=True)
        shutil.rmtree(sessions.vault_root(), ignore_errors=True)
        st = compaction._load_state()
        st["session_id"] = "20260919-160000-e2e1"
        compaction._write_state(st)
        _seed(st["session_id"], [("user", "file this chat please"),
                                 ("assistant", "filed."),
                                 ("user", "and find it again later")])

    def test_new_session_files_the_chat_before_it_goes(self) -> None:
        status, body = call("/api/session", {"new": True}, "POST")
        self.assertEqual(status, 200, body)
        self.assertEqual(body.get("messages"), [])
        self.assertEqual((body.get("vault") or {}).get("sid"), "20260919-160000-e2e1", body)
        status, listing = call("/api/sessions")
        self.assertEqual(status, 200, listing)
        self.assertTrue(listing.get("ok"), listing)
        self.assertIn("20260919-160000-e2e1", [r.get("sid") for r in listing["sessions"]], listing)

    def test_the_vault_files_reads_and_searches(self) -> None:
        status, filed = call("/api/sessions", {"action": "vault_live"}, "POST")
        self.assertEqual(status, 200, filed)
        self.assertTrue(filed.get("ok"), filed)
        self.assertTrue(filed.get("verified"), filed)
        status, read = call("/api/sessions?sid=20260919-160000-e2e1")
        self.assertTrue(read.get("ok"), read)
        self.assertIn("file this chat please", read["transcript_text"])
        status, found = call("/api/sessions", {"action": "search", "q": "find it again"}, "POST")
        self.assertTrue(found.get("hits"), found)
        self.assertEqual(found["hits"][0]["sid"], "20260919-160000-e2e1")
        # The same search over GET, opt-in: `q` alone filters the catalog.
        status, deep = call("/api/sessions?search=1&q=find%20it%20again")
        self.assertTrue(deep.get("hits"), deep)
        status, shallow = call("/api/sessions?q=find%20it%20again")
        self.assertIn("sessions", shallow, shallow)

    def test_naming_a_session_sticks(self) -> None:
        call("/api/sessions", {"action": "vault_live"}, "POST")
        status, out = call("/api/sessions", {"action": "label", "sid": "20260919-160000-e2e1",
                                             "title": "over the wire", "tags": "vault,e2e"}, "POST")
        self.assertEqual(status, 200, out)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("tags"), ["vault", "e2e"])
        status, listing = call("/api/sessions?q=over%20the%20wire")
        self.assertEqual([r["title"] for r in listing["sessions"]], ["over the wire"], listing)

    def test_reopening_brings_the_messages_back(self) -> None:
        call("/api/sessions", {"action": "vault_live"}, "POST")
        st = compaction._load_state()
        st["session_id"] = "20260919-161000-e2e2"
        compaction._write_state(st)
        _seed("20260919-161000-e2e2", [("user", "the chat in progress")])
        status, out = call("/api/sessions", {"action": "resume", "sid": "20260919-160000-e2e1"}, "POST")
        self.assertEqual(status, 200, out)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("resumed_from"), "20260919-160000-e2e1")
        self.assertEqual(len(out.get("messages") or []), 3, out)
        for m in out["messages"]:
            self.assertIn(m["role"], ("user", "assistant"))
        status, ses = call("/api/session")
        self.assertEqual(len(ses.get("messages") or []), 3, ses)

    def test_the_stats_route_answers(self) -> None:
        status, st = call("/api/sessions?stats=1")
        self.assertEqual(status, 200, st)
        self.assertTrue(st.get("ok"), st)
        self.assertEqual(st.get("signature"), sessions.BUILD_TAG)

    def test_a_bad_action_is_refused_not_guessed(self) -> None:
        status, out = call("/api/sessions", {"action": "delete_everything"}, "POST")
        self.assertEqual(status, 400, out)
        self.assertEqual(out.get("error"), "bad_action")


def call(path: str, body=None, method: str = "GET", timeout: int = 60):
    conn = http.client.HTTPConnection("127.0.0.1", _port, timeout=timeout)
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return resp.status, raw.decode("utf-8", "replace")


def seed(n: int) -> None:
    for i in range(1, n + 1):
        compaction.record("user" if i % 2 else "assistant",
                          f"turn {i} about the lattice signature and file C:\\work\\doc{i}.txt")


class RecordRouteTests(unittest.TestCase):
    def test_status_route_reports_the_configured_window(self):
        """The window the UI shows must be the window the engine is actually opened with."""
        cfg = json.loads((ROOT / "config" / "console.json").read_text(encoding="utf-8"))
        status, data = call("/api/compaction")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"), data)
        self.assertEqual(data["window"]["ctx"], int(cfg["ctx_max"]))
        self.assertGreaterEqual(data["window"]["history_tokens"], 500)
        self.assertTrue(data["paths"]["journal"])

    def test_the_save_button_route_stamps_the_record(self):
        """POST action=save is the manual button: it must stamp, fold and answer."""
        seed(6)
        status, data = call("/api/compaction", {"action": "save"}, method="POST")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"), data)
        self.assertTrue(data["checkpoint"]["ok"], data["checkpoint"])
        self.assertTrue(data["compact"]["ok"], data["compact"])
        self.assertTrue(data["status"]["ok"], data["status"])
        self.assertTrue(data.get("at"), "a save must carry its timestamp")
        self.assertTrue(list(compaction.CHECKPOINTS.glob("*.md")), "a checkpoint must land on disk")

    def test_the_save_button_route_is_idempotent_when_nothing_changed(self):
        seed(3)
        first = call("/api/compaction", {"action": "save"}, method="POST")[1]
        second = call("/api/compaction", {"action": "save"}, method="POST")[1]
        self.assertTrue(first.get("ok") and second.get("ok"), (first, second))
        self.assertEqual(second["status"]["turns_total"], first["status"]["turns_total"])

    def test_a_rollup_folds_what_left_the_window(self):
        """With room in the window nothing is folded; asked to keep only 4 turns, the rest folds."""
        compaction.ROLLUP_TURNS = 4
        try:
            seed(12)
            untouched = call("/api/compaction", {"action": "compact"}, method="POST")[1]
            self.assertTrue(untouched.get("ok"), untouched)
            self.assertEqual(untouched["status"]["rollups"], 0, "a window with room must not churn")
            data = call("/api/compaction", {"action": "compact", "keep_turns": 4}, method="POST")[1]
            self.assertTrue(data.get("ok"), data)
            self.assertGreaterEqual(data["status"]["rollups"], 1, data["status"])
            self.assertGreater(data["status"]["turns_compacted"], 0, data["status"])
            self.assertTrue(data["status"]["carry_ready"], "the digest must be ready for the next prompt")
        finally:
            compaction.ROLLUP_TURNS = 6

    def test_the_recall_limb_route_finds_an_older_turn(self):
        seed(4)
        compaction.record("user", "the steward asked about the lattice signature D9F963 and file C:\\work\\seal.txt")
        status, data = call("/api/limb", {"name": "recall_history", "arguments": {"q": "lattice signature"}}, method="POST")
        self.assertEqual(status, 200)
        blob = json.dumps(data)
        self.assertIn("hits", blob, blob[:400])

    def test_a_bad_action_answers_instead_of_raising(self):
        status, data = call("/api/compaction", {"action": "not_a_thing"}, method="POST")
        self.assertEqual(status, 400)
        self.assertFalse(data.get("ok"))
        self.assertIn("save", data.get("actions") or [])

    def test_the_archive_route_lists_the_index(self):
        status, data = call("/api/archive")
        self.assertEqual(status, 200)
        self.assertIn("sessions", data)
        self.assertTrue(data.get("index"))

    def test_the_record_routes_survive_a_missing_store(self):
        keep = compaction.SESSIONS
        compaction.SESSIONS = Path(str(_tmp)) / "gone" / "sessions"
        try:
            status, data = call("/api/compaction")
            self.assertEqual(status, 200)
            self.assertIn("ok", data)
        finally:
            compaction.SESSIONS = keep

    def test_the_console_still_answers_after_a_broken_save(self):
        """A save that cannot write must answer, not wedge the console for the next turn."""
        seed(2)
        keep = compaction.STATE
        compaction.STATE = Path(str(_tmp)) / "gone" / "state.json"
        try:
            status, data = call("/api/compaction", {"action": "save"}, method="POST")
            self.assertEqual(status, 200)
            self.assertIn("ok", data)
        finally:
            compaction.STATE = keep
        status, data = call("/api/compaction")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"), data)


if __name__ == "__main__":
    unittest.main()
