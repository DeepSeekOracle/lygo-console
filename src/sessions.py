"""Session history: the vault. A conversation that ends is filed, labelled and findable again.

`compaction.py` keeps the live record of the conversation in progress. This module is the layer
above it: when a conversation ends - the operator pressing New session, a seal, or an explicit
vault action - its turns are filed into a dated, labelled, indexed folder with a readable
transcript and a verified zip. Both the operator and the agent can then find it again by date,
by title, by tag or by phrase.

Rules this module holds to:

* **Nothing is ever deleted.** Filing *copies*. The journal stays where it was, the original
  legacy file stays where it was, and a session can be filed twice without harm.
* **A session is addressable by its id.** The folder is `<sid>-<slug>`: the id (the stable key)
  comes first so every lookup is a prefix match, the slug (the readable part) follows so a human
  browsing the folder tree can see what it is.
* **The catalog is append-only, the readable catalog is regenerated.** `catalog.jsonl` is the
  machine truth and is only appended to; `CATALOG.md` is rendered from it and may be rebuilt.
* **Everything has a safe wrapper.** A vault failure is never allowed to cost the operator a turn
  or a console: the `safe_*` entry points answer `{"ok": false, ...}` instead of raising.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

from atomicio import atomic_write_text

import compaction

BUILD_TAG = "Δ9Φ963-LYGO-SESSIONS-v1"

VAULT = Path(compaction.SAVE) / "vault"
CATALOG = VAULT / "catalog.jsonl"
CATALOG_MD = VAULT / "CATALOG.md"

MONTHS = (
    "01-January", "02-February", "03-March", "04-April", "05-May", "06-June",
    "07-July", "08-August", "09-September", "10-October", "11-November", "12-December",
)

SLUG_LEN = 48
TITLE_LEN = 72
CATALOG_MD_ROWS = 400  # a readable catalog is bounded; catalog.jsonl is the complete one
_LOCK = threading.RLock()

# Legacy file names written by the console before this module existed. They are imported, never
# moved: an unknown history is worth more than a tidy folder.
LEGACY_GLOB = "session-*.json"


# ------------------------------------------------------------------------------------------------
# paths and labels
# ------------------------------------------------------------------------------------------------
def _dirs() -> None:
    with _LOCK:
        VAULT.mkdir(parents=True, exist_ok=True)


def vault_root() -> Path:
    """Where the vault lives - resolved at call time so a test can point it at a sandbox."""
    return VAULT if isinstance(VAULT, Path) else Path(VAULT)


def _month_dir(when: float | None = None) -> Path:
    t = time.localtime(when if when is not None else time.time())
    return vault_root() / f"{t.tm_year:04d}" / MONTHS[t.tm_mon - 1]


def slugify(text: str, n: int = SLUG_LEN) -> str:
    """A folder-safe, readable slug: lowercase words joined by dashes."""
    keep: list[str] = []
    last_dash = False
    for ch in str(text or "").strip().lower():
        if ch.isalnum():
            keep.append(ch)
            last_dash = False
        elif not last_dash:
            keep.append("-")
            last_dash = True
    out = "".join(keep).strip("-")
    if len(out) > n:
        out = out[:n].rstrip("-")
    return out or "session"


TEST_RECEIPT = "test-receipt"


def is_test_turn(rec: dict[str, Any]) -> bool:
    """True for a turn the test suite wrote into the live store.

    `SAVE` is a fixed path, so a test run that does not redirect it appends its own turns to the same
    journal the operator is talking in - "hi" / "Hello there." under `meta.receipt: test-receipt`.
    Those turns belong in the journal (nothing is ever deleted) but they must never reach the vault's
    transcript, or a filed session reads as a conversation with a test harness.
    """
    if str(rec.get("role")) == "system":
        return False
    meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}
    if str(meta.get("receipt") or "") == TEST_RECEIPT:
        return True
    return str(rec.get("receipt") or "") == TEST_RECEIPT


def title_from(recs: list[dict[str, Any]], fallback: str = "session") -> str:
    """A title the operator will recognise: the opening of the first thing they said."""
    for rec in recs or []:
        if str(rec.get("role")) != "user":
            continue
        text = _as_text(rec.get("content"))
        text = " ".join(text.split())
        if not text:
            continue
        if len(text) > TITLE_LEN:
            text = text[:TITLE_LEN].rsplit(" ", 1)[0] + "…"
        return text or fallback
    return fallback


def _as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                out.append(str(part.get("text") or ""))
            elif isinstance(part, dict) and part.get("type") == "image_url":
                out.append("[image]")
        return " ".join(out)
    return str(content or "")


def session_dir(sid: str, when: float | None = None, title: str = "") -> Path:
    return _month_dir(when) / f"{sid}-{slugify(title)}"


# ------------------------------------------------------------------------------------------------
# catalog
# ------------------------------------------------------------------------------------------------
def _catalog_lines() -> list[dict[str, Any]]:
    path = vault_root() / "catalog.jsonl"
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in raw:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _catalog_append(entry: dict[str, Any]) -> None:
    _dirs()
    path = vault_root() / "catalog.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()


def _catalog_replace(sid: str, entry: dict[str, Any]) -> bool:
    """Rewrite the catalog with one session's line replaced (label changes). Returns True on edit."""
    lines = _catalog_lines()
    hit = False
    for i, row in enumerate(lines):
        if str(row.get("sid")) == str(sid):
            lines[i] = entry
            hit = True
    if not hit:
        return False
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in lines) + "\n"
    try:
        atomic_write_text(vault_root() / "catalog.jsonl", body)
    except OSError:
        return False
    return True


def catalog(limit: int = 200, q: str = "", tag: str = "", month: str = "") -> dict[str, Any]:
    """Newest first. `q` matches title/tags/note/sid, `month` matches 'YYYY-MM' or 'YYYY/MM-Month'."""
    rows = _catalog_lines()
    q = (q or "").strip().lower()
    tag = (tag or "").strip().lower()
    month = (month or "").strip()
    out = []
    for row in rows:
        if q:
            hay = " ".join([str(row.get("title") or ""), str(row.get("sid") or ""),
                            " ".join(row.get("tags") or []), str(row.get("note") or "")]).lower()
            if q not in hay:
                continue
        if tag and tag not in [str(t).lower() for t in (row.get("tags") or [])]:
            continue
        if month and not (_row_month(row).startswith(month) or month in _row_month(row)):
            continue
        out.append(row)
    out.sort(key=lambda r: str(r.get("filed_iso") or r.get("created_iso") or ""), reverse=True)
    tags: dict[str, int] = {}
    for row in rows:
        for t in row.get("tags") or []:
            tags[str(t)] = tags.get(str(t), 0) + 1
    return {
        "ok": True,
        "vault": str(vault_root()),
        "catalog": str(vault_root() / "catalog.jsonl"),
        "sessions": out[: max(1, int(limit))],
        "count": len(rows),
        "shown": len(out[: max(1, int(limit))]),
        "tags": dict(sorted(tags.items(), key=lambda kv: -kv[1])),
        "bytes": int(sum(int(r.get("bytes") or 0) for r in rows)),
        "turns": int(sum(int(r.get("turns") or 0) for r in rows)),
    }


def _row_month(row: dict[str, Any]) -> str:
    iso = str(row.get("created_iso") or row.get("filed_iso") or "")
    return iso[:7].replace("-", "-") if len(iso) >= 7 else ""


# ------------------------------------------------------------------------------------------------
# filing a session
# ------------------------------------------------------------------------------------------------
def _digest_for(sid: str, recs: list[dict[str, Any]]) -> str:
    """The thread of a session, in the operator's own words. Deterministic, no LLM call.

    `compaction.build_carry()` reads the session's rollups - and those only exist once a
    conversation has been folded. A conversation filed straight out of the live window has none,
    which left an EMPTY digest: a session reopened from the vault would have arrived with no
    context at all. So when the carry is empty or a stub, this builds the thread from the turns.
    """
    carry = ""
    try:
        carry = compaction.build_carry(sid) or ""
    except Exception:  # noqa: BLE001
        carry = ""
    if len(" ".join(str(carry).split())) > 40:
        return str(carry)
    asked = [" ".join(_as_text(r.get("content")).split()) for r in recs if r.get("role") == "user"]
    asked = [t for t in asked if t]
    span = f"{recs[0].get('iso') if recs else '—'} → {recs[-1].get('iso') if recs else '—'}"
    lines = [f"digest of session {sid} · {len(recs)} turns · {span}"]
    if asked:
        lines.append(f"opening question: {asked[0][:300]}")
        if len(asked) > 1:
            lines.append("what the operator asked, in order:")
            for t in asked[:40]:
                lines.append(f"- {t[:200]}")
            if len(asked) > 40:
                lines.append(f"- …and {len(asked) - 40} further turns")
    else:
        lines.append("(no operator turns in this session)")
    return "\n".join(lines)


def _transcript(recs: list[dict[str, Any]], title: str, sid: str, tags: tuple[str, ...] = ()) -> str:
    body = [
        f"# {title}",
        "",
        f"- session: `{sid}`",
        f"- turns: {len(recs)}",
        f"- span: {recs[0].get('iso') if recs else '—'} → {recs[-1].get('iso') if recs else '—'}",
        f"- filed: {compaction.iso()} by LYGO LLM Console {BUILD_TAG}",
    ]
    if tags:
        body.append(f"- tags: {', '.join(str(t) for t in tags)}")
    body += ["", "---", ""]
    for rec in recs:
        who = "OPERATOR" if str(rec.get("role")) == "user" else "AGENT"
        body.append(f"**{who}** · {rec.get('iso') or ''}")
        body.append("")
        body.append(_as_text(rec.get("content")))
        body.append("")
    return "\n".join(body)


def _zip_files(folder: Path, names: list[str]) -> dict[str, Any]:
    """Zip the listed files and read the zip back before believing it."""
    zip_path = folder / "session.zip"
    tmp = folder / "session.zip.part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name in names:
            p = folder / name
            if p.is_file():
                z.write(p, arcname=name)
    os.replace(tmp, zip_path)
    # verify: reopen, check every member's CRC, and confirm the turns file is byte-identical
    try:
        with zipfile.ZipFile(zip_path) as z:
            bad = z.testzip()
            if bad:
                return {"ok": False, "error": "zip_crc_failed", "member": bad}
            inside = z.read("turns.jsonl") if "turns.jsonl" in z.namelist() else b""
        outside = (folder / "turns.jsonl").read_bytes()
        if inside != outside:
            return {"ok": False, "error": "zip_content_mismatch"}
    except (OSError, zipfile.BadZipFile, KeyError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "zip": str(zip_path), "zip_bytes": zip_path.stat().st_size,
            "zip_digest": compaction.digest_of((folder / "turns.jsonl").read_bytes())}


def vault_session(
    sid: str | None = None,
    title: str | None = None,
    tags: tuple[str, ...] | list[str] = (),
    note: str = "",
    reason: str = "manual",
    source: str = "journal",
) -> dict[str, Any]:
    """File the named (or live) session into the vault. Idempotent: filing twice replaces the copy."""
    _dirs()
    with _LOCK:
        st = compaction._load_state()
        sid = str(sid or st.get("session_id") or "")
        if not sid:
            return {"ok": False, "error": "no_session"}
        recs = compaction.read_journal(sid)
        if not recs:
            return {"ok": False, "error": "empty_session", "sid": sid}
        # The suite writes into this same store, so a filed session used to read as a conversation
        # with a test harness. Filtering is on the FILED COPY only; the journal keeps every turn.
        kept = [r for r in recs if not is_test_turn(r)]
        test_turns = len(recs) - len(kept)
        if not kept:
            return {"ok": False, "error": "test_only_session", "sid": sid, "turns": len(recs)}
        recs = kept
        when = float(recs[0].get("ts") or time.time())
        # A journal from a tool that stamped ts=1 would file the session into 1969/12-December. Any
        # stamp before this console existed is a broken record; the file date is the honest answer.
        if when < 946684800:  # 2000-01-01
            when = time.time()
        title = (title or "").strip() or title_from(recs)
        tags = [str(t).strip() for t in (tags or []) if str(t).strip()]
        folder = session_dir(sid, when, title)
        folder.mkdir(parents=True, exist_ok=True)

        turns = "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n"
        atomic_write_text(folder / "turns.jsonl", turns)
        atomic_write_text(folder / "transcript.md", _transcript(recs, title, sid, tuple(tags)))
        manifest = {
            "signature": BUILD_TAG,
            "sid": sid,
            "title": title,
            "slug": slugify(title),
            "tags": tags,
            "note": note,
            "pinned": False,
            "created_iso": recs[0].get("iso"),
            "ended_iso": recs[-1].get("iso"),
            "created_ts": when,
            "filed_iso": compaction.iso(),
            "reason": reason,
            "source": source,
            "turns": len(recs),
            "chars": sum(int(r.get("chars") or 0) for r in recs),
            "roles": {"user": sum(1 for r in recs if r.get("role") == "user"),
                      "assistant": sum(1 for r in recs if r.get("role") == "assistant")},
            "test_turns_dropped": test_turns,
            "first_turn": _as_text(recs[0].get("content"))[:400],
            "folder": str(folder),
        }
        atomic_write_text(folder / "digest.md", f"# digest · {title}\n\n{_digest_for(sid, recs)}\n")
        atomic_write_text(folder / "session.json", json.dumps(manifest, indent=2, ensure_ascii=False))

        zipped = _zip_files(folder, ["session.json", "transcript.md", "turns.jsonl", "digest.md"])
        manifest["zip"] = zipped.get("zip")
        manifest["zip_bytes"] = zipped.get("zip_bytes")
        manifest["zip_digest"] = zipped.get("zip_digest")
        manifest["ok"] = bool(zipped.get("ok"))
        atomic_write_text(folder / "session.json", json.dumps(manifest, indent=2, ensure_ascii=False))

        entry = dict(manifest)
        entry["folder"] = str(folder.relative_to(vault_root())) if folder.is_relative_to(vault_root()) else str(folder)
        entry["month"] = f"{time.localtime(when).tm_year:04d}/{MONTHS[time.localtime(when).tm_mon - 1]}"
        _catalog_replace(sid, entry) or _catalog_append(entry)
        rebuild_catalog_md()
        return {"ok": bool(zipped.get("ok")), "sid": sid, "title": title, "turns": len(recs),
                "folder": str(folder), "zip": zipped.get("zip"), "zip_bytes": zipped.get("zip_bytes"),
                "tags": tags, "reason": reason, "verified": bool(zipped.get("ok")),
                "test_turns_dropped": test_turns, "error": zipped.get("error")}


def vault_live(reason: str = "new_session", title: str | None = None) -> dict[str, Any]:
    """File the conversation that is ending. Called before the chat is wiped."""
    try:
        st = compaction._load_state()
        sid = str(st.get("session_id") or "")
        if not sid:
            return {"ok": False, "error": "no_session"}
        if not compaction.read_journal(sid):
            return {"ok": True, "skipped": "empty_session", "sid": sid}
        out = vault_session(sid=sid, title=title, reason=reason, source="live")
        # A filed session leaves a carry so the next conversation keeps the thread of continuity.
        if out.get("ok"):
            try:
                st = compaction._load_state()
                st["last_vault"] = {"sid": sid, "iso": compaction.iso(), "reason": reason,
                                    "title": out.get("title")}
                compaction._write_state(st)
            except Exception:  # noqa: BLE001
                pass
        return out
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ------------------------------------------------------------------------------------------------
# bringing older history in
# ------------------------------------------------------------------------------------------------
def adopt(limit: int = 300) -> dict[str, Any]:
    """File everything that is not filed yet: dormant journals and legacy session-*.json files.

    Read-only towards its sources: the originals are left exactly where they were.
    """
    _dirs()
    filed = {str(row.get("sid")) for row in _catalog_lines()}
    live = str(compaction._load_state().get("session_id") or "")
    adopted: list[dict[str, Any]] = []
    skipped: list[str] = []
    tests: list[str] = []  # journals that hold nothing but the suite's own turns

    sessions_dir = Path(compaction.SESSIONS)
    for path in sorted(sessions_dir.glob("journal-*.jsonl")):
        sid = path.name[len("journal-"):-len(".jsonl")]
        if sid == live or sid in filed:
            continue
        res = vault_session(sid=sid, reason="adopt", source="journal")
        if res.get("error") == "test_only_session":
            tests.append(sid)
            continue
        (adopted if res.get("ok") else skipped).append(res.get("sid") or sid)

    for path in sorted(sessions_dir.glob(LEGACY_GLOB))[: max(1, int(limit))]:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            skipped.append(path.name)
            continue
        msgs = data.get("messages") if isinstance(data, dict) else data
        if not isinstance(msgs, list) or not msgs:
            skipped.append(path.name)
            continue
        stamp = 0.0
        try:
            stamp = float(path.stem.split("-")[-1])
        except (ValueError, IndexError):
            stamp = path.stat().st_mtime
        sid = f"legacy-{int(stamp)}"
        if sid in filed:
            continue
        res = _vault_legacy(path, sid, msgs, stamp)
        (adopted if res.get("ok") else skipped).append(res.get("sid") or res.get("error") or path.name)

    return {"ok": True, "adopted": adopted, "count": len(adopted), "skipped": skipped,
            "test_only": tests, "test_only_count": len(tests), "vault": str(vault_root())}


def _vault_legacy(path: Path, sid: str, msgs: list[Any], stamp: float) -> dict[str, Any]:
    """File one legacy 40-message backup as a session, marked as recovered."""
    recs = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        text = _as_text(m.get("content"))
        recs.append({
            "iso": compaction.iso(stamp),
            "ts": stamp,
            "role": "user" if m.get("role") == "user" else "assistant",
            "content": text,
            "chars": len(text),
            "tokens": compaction.est_tokens(text),
            "legacy": path.name,
        })
    if not recs:
        return {"ok": False, "error": "empty_legacy", "sid": sid}
    title = title_from(recs, fallback=f"recovered {compaction.iso(stamp)}")
    folder = session_dir(sid, stamp, title)
    folder.mkdir(parents=True, exist_ok=True)
    atomic_write_text(folder / "turns.jsonl", "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n")
    atomic_write_text(folder / "transcript.md", _transcript(recs, title, sid, ("recovered",)))
    manifest = {
        "signature": BUILD_TAG, "sid": sid, "title": title, "slug": slugify(title),
        "tags": ["recovered"], "note": f"imported from {path.name}", "pinned": False,
        "created_iso": recs[0]["iso"], "ended_iso": recs[-1]["iso"], "created_ts": stamp,
        "filed_iso": compaction.iso(), "reason": "adopt", "source": "legacy",
        "turns": len(recs), "chars": sum(r["chars"] for r in recs),
        "roles": {"user": sum(1 for r in recs if r["role"] == "user"),
                  "assistant": sum(1 for r in recs if r["role"] == "assistant")},
        "first_turn": _as_text(recs[0].get("content"))[:400], "folder": str(folder),
        "truncated": True,
    }
    atomic_write_text(folder / "digest.md", f"# digest · {title}\n\n{_digest_for(sid, recs)}\n")
    zipped = _zip_files(folder, ["session.json", "transcript.md", "turns.jsonl", "digest.md"])
    manifest.update({"zip": zipped.get("zip"), "zip_bytes": zipped.get("zip_bytes"),
                     "zip_digest": zipped.get("zip_digest"), "ok": bool(zipped.get("ok"))})
    atomic_write_text(folder / "session.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    entry = dict(manifest)
    entry["folder"] = str(folder.relative_to(vault_root())) if folder.is_relative_to(vault_root()) else str(folder)
    entry["month"] = f"{time.localtime(stamp).tm_year:04d}/{MONTHS[time.localtime(stamp).tm_mon - 1]}"
    _catalog_replace(sid, entry) or _catalog_append(entry)
    rebuild_catalog_md()
    return {"ok": True, "sid": sid, "turns": len(recs), "title": title, "folder": str(folder)}


# ------------------------------------------------------------------------------------------------
# finding a session again
# ------------------------------------------------------------------------------------------------
def find_session(sid: str) -> dict[str, Any] | None:
    """Resolve an id to its filed folder, or to an unfiled journal / legacy file.

    The folder name is `<sid>-<slug>`, so the id is matched as a prefix and the candidates are
    filtered to those that really start with `<sid>-` (or equal it) - a shorter id must not answer
    for a longer one.
    """
    sid = str(sid or "").strip()
    if not sid:
        return None
    root = vault_root()
    if root.is_dir():
        for month in sorted((p for p in root.glob("*/*")), reverse=True):
            if not month.is_dir():
                continue
            for folder in sorted((p for p in month.glob(f"{sid}*")), reverse=True):
                name = folder.name
                if not folder.is_dir() or not (name == sid or name.startswith(f"{sid}-")):
                    continue
                manifest: dict[str, Any] = {}
                man = folder / "session.json"
                if man.is_file():
                    try:
                        manifest = json.loads(man.read_text(encoding="utf-8", errors="replace"))
                    except (OSError, json.JSONDecodeError):
                        manifest = {}
                zp = folder / "session.zip"
                return {"ok": True, "sid": sid, "filed": True, "folder": str(folder),
                        "manifest": manifest, "zip": str(zp) if zp.is_file() else None,
                        "transcript": str(folder / "transcript.md")}
    jp = Path(compaction.journal_path(sid))
    if jp.is_file():
        return {"ok": True, "sid": sid, "filed": False, "folder": str(jp.parent),
                "manifest": {}, "journal": str(jp)}
    if sid.startswith("legacy-"):
        leg = Path(compaction.SESSIONS) / f"session-{sid.split('-')[-1]}.json"
        if leg.is_file():
            return {"ok": True, "sid": sid, "filed": False, "folder": str(leg.parent),
                    "manifest": {}, "legacy": str(leg)}
    return {"ok": False, "error": "not_found", "sid": sid}


def _turns_for(info: dict[str, Any]) -> list[dict[str, Any]]:
    folder = Path(str(info.get("folder") or ""))
    turns = folder / "turns.jsonl"
    if turns.is_file():
        out = []
        for line in turns.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out
    if info.get("journal"):
        return compaction.read_journal(str(info["sid"]))
    if info.get("legacy"):
        try:
            data = json.loads(Path(str(info["legacy"])).read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return []
        msgs = data.get("messages") if isinstance(data, dict) else data
        return [{"role": m.get("role"), "content": _as_text(m.get("content"))}
                for m in (msgs or []) if isinstance(m, dict)]
    return []


def open_session(sid: str, chars: int = 24000) -> dict[str, Any]:
    """Read a session back: manifest, transcript (bounded head+tail) and where it lives on disk."""
    info = find_session(sid)
    if not info or not info.get("ok"):
        return info or {"ok": False, "error": "not_found", "sid": sid}
    recs = _turns_for(info)
    body = ""
    tpath = Path(str(info.get("transcript") or ""))
    if tpath.is_file():
        body = tpath.read_text(encoding="utf-8", errors="replace")
    elif recs:
        body = _transcript(recs, title_from(recs), str(info.get("sid") or sid),
                           tuple(info.get("manifest", {}).get("tags") or ()))
    limit = max(2000, int(chars or 24000))
    truncated = len(body) > limit
    if truncated:
        head = int(limit * 0.7)
        body = body[:head] + "\n\n…[middle of the transcript omitted — read the file below]…\n\n" + body[-(limit - head):]
    man = dict(info.get("manifest") or {})
    return {
        "ok": True, "sid": info.get("sid"), "filed": info.get("filed"),
        "title": man.get("title") or title_from(recs, fallback=str(info.get("sid"))),
        "tags": man.get("tags") or [], "note": man.get("note") or "",
        "turns": len(recs), "chars": sum(int(r.get("chars") or 0) for r in recs),
        "first_turn": _as_text(recs[0].get("content"))[:400] if recs else "",
        "created_iso": (recs[0].get("iso") if recs else man.get("created_iso")),
        "ended_iso": (recs[-1].get("iso") if recs else man.get("ended_iso")),
        "folder": info.get("folder"), "zip": info.get("zip"), "transcript": info.get("transcript"),
        "chars_returned": len(body), "truncated": truncated, "transcript_text": body,
        "messages": [{"role": r.get("role"), "content": _as_text(r.get("content"))} for r in recs],
    }


def label(sid: str, title: str | None = None, tags: tuple[str, ...] | list[str] | None = None,
          note: str | None = None, pinned: bool | None = None) -> dict[str, Any]:
    """Name, tag or annotate a session. An unfiled session is filed first, then labelled."""
    info = find_session(sid)
    if info and info.get("filed"):
        pass
    elif info and info.get("journal"):
        filed = vault_session(sid=str(info.get("sid")), reason="label", source="journal")
        if not filed.get("ok"):
            return filed
        info = find_session(sid)
    elif info and info.get("legacy"):
        return {"ok": False, "error": "legacy_not_filed", "sid": sid, "hint": "run adopt"}
    else:
        return info or {"ok": False, "error": "not_found", "sid": sid}
    folder = Path(str(info.get("folder") or ""))
    man = dict(info.get("manifest") or {})
    if title is not None and str(title).strip():
        man["title"] = str(title).strip()
        man["slug"] = slugify(man["title"])
    if tags is not None:
        man["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    if note is not None:
        man["note"] = str(note)
    if pinned is not None:
        man["pinned"] = bool(pinned)
    man["labelled_iso"] = compaction.iso()
    try:
        atomic_write_text(folder / "session.json", json.dumps(man, indent=2, ensure_ascii=False))
    except OSError as e:
        return {"ok": False, "error": f"write_failed: {e}"}
    entry = dict(man)
    entry["folder"] = (str(folder.relative_to(vault_root())) if folder.is_relative_to(vault_root())
                       else str(folder))
    entry["month"] = man.get("month") or f"{str(man.get('created_iso') or '')[:4]}"
    _catalog_replace(str(man.get("sid") or sid), entry)
    rebuild_catalog_md()
    return {"ok": True, "sid": man.get("sid") or sid, "title": man.get("title"),
            "tags": man.get("tags") or [], "note": man.get("note") or "",
            "pinned": bool(man.get("pinned")), "folder": str(folder)}


def search(q: str, k: int = 8, chars_per_hit: int = 260) -> dict[str, Any]:
    """Find a session by phrase: titles, tags and notes first, then the transcripts themselves."""
    q = (q or "").strip()
    if not q:
        return {"ok": False, "error": "empty_query"}
    needle = q.lower()
    hits: list[dict[str, Any]] = []
    rows = _catalog_lines()
    rows.sort(key=lambda r: str(r.get("filed_iso") or ""), reverse=True)
    for row in rows:
        title = str(row.get("title") or "")
        hay = " ".join([title, str(row.get("sid") or ""), " ".join(row.get("tags") or []),
                        str(row.get("note") or "")]).lower()
        if needle in hay:
            hits.append({"where": "catalog", "sid": row.get("sid"), "title": title,
                         "iso": row.get("created_iso"), "turns": row.get("turns"),
                         "folder": row.get("folder"), "snippet": title})
    for row in rows:
        if len(hits) >= max(1, int(k)) * 3:
            break
        folder_rel = str(row.get("folder") or "")
        folder = vault_root() / folder_rel if folder_rel else None
        if not folder or not folder.is_dir():
            continue
        tp = folder / "transcript.md"
        if not tp.is_file():
            continue
        try:
            text = tp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = text.lower()
        pos = low.find(needle)
        if pos < 0:
            continue
        start = max(0, pos - int(chars_per_hit) // 3)
        snippet = " ".join(text[start:start + int(chars_per_hit)].split())
        if any(h.get("sid") == row.get("sid") and h.get("where") == "transcript" for h in hits):
            continue
        hits.append({"where": "transcript", "sid": row.get("sid"), "title": row.get("title"),
                     "iso": row.get("created_iso"), "turns": row.get("turns"),
                     "folder": row.get("folder"), "snippet": snippet})
    hits = hits[: max(1, int(k))]
    return {"ok": True, "q": q, "hits": hits, "n_hits": len(hits), "scanned": len(rows)}


# ------------------------------------------------------------------------------------------------
# picking a thread back up
# ------------------------------------------------------------------------------------------------
def resume(sid: str, file_current: bool = True) -> dict[str, Any]:
    """Reopen a filed session in the chat.

    The conversation in progress is filed first (never lost to a button press), then the chosen
    session's messages are loaded back into the console's session file - so the portal shows them
    again - and its digest is installed as the carry, which is what gives the agent continuity
    without re-sending every old turn.
    """
    info = find_session(sid)
    if not info or not info.get("ok"):
        return info or {"ok": False, "error": "not_found", "sid": sid}
    recs = _turns_for(info)
    if not recs:
        return {"ok": False, "error": "empty_session", "sid": sid}

    filed_now: dict[str, Any] = {"ok": True, "skipped": "not_requested"}
    st = compaction._load_state()
    live = str(st.get("session_id") or "")
    if file_current and live and live != str(info.get("sid")):
        filed_now = vault_live(reason="resume")

    messages = [{"role": ("user" if r.get("role") == "user" else "assistant"),
                 "content": _as_text(r.get("content"))} for r in recs]
    written = False
    try:
        from continuity import CURRENT, HISTORY_MAX

        keep = messages[-max(HISTORY_MAX, len(messages)) :]
        atomic_write_text(CURRENT, json.dumps({"updated": time.time(), "messages": keep,
                                               "resumed_from": info.get("sid")}, indent=2))
        written = True
    except Exception:  # noqa: BLE001
        written = False

    import contextlib

    carry = ""
    digest_path = Path(str(info.get("folder") or "")) / "digest.md"
    with contextlib.suppress(OSError):
        if digest_path.is_file():
            carry = digest_path.read_text(encoding="utf-8", errors="replace")
    if not carry:
        with contextlib.suppress(Exception):
            carry = compaction.build_carry(str(info.get("sid")))

    new_sid = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.urandom(2).hex()}"
    lineage_error = ""
    try:
        st = compaction._load_state()
        st["session_id"] = new_sid
        st["session_seq"] = int(st.get("session_seq") or 0) + 1
        st["resumed_from"] = info.get("sid")
        st["resumed_iso"] = compaction.iso()
        st["carry"] = carry
        st["carry_from"] = 1
        st["carry_to"] = len(recs)
        st["carry_turns"] = len(recs)
        st["covered_upto"] = len(recs)
        compaction._write_state(st)
        # read it back: a lineage that does not survive the write is not a lineage
        st = compaction._load_state()
        if str(st.get("resumed_from")) != str(info.get("sid")):
            lineage_error = "lineage_not_persisted"
    except Exception as e:  # noqa: BLE001
        lineage_error = f"{type(e).__name__}: {e}"
    return {"ok": True, "resumed_from": info.get("sid"), "session_id": new_sid,
            "title": info.get("manifest", {}).get("title") or title_from(recs),
            "turns": len(recs), "messages": messages, "session_file_written": written,
            "carry_chars": len(carry), "filed_previous": filed_now.get("sid") or filed_now.get("skipped"),
            "folder": info.get("folder"), "lineage_error": lineage_error}


# ------------------------------------------------------------------------------------------------
# the readable catalog, and the state of the vault
# ------------------------------------------------------------------------------------------------
def rebuild_catalog_md() -> dict[str, Any]:
    rows = _catalog_lines()
    rows.sort(key=lambda r: str(r.get("created_iso") or r.get("filed_iso") or ""), reverse=True)
    total_turns = sum(int(r.get("turns") or 0) for r in rows)
    total_bytes = sum(int(r.get("bytes") or r.get("zip_bytes") or 0) for r in rows)
    out = [
        "# Session vault — catalog",
        "",
        f"- sessions filed: **{len(rows)}**",
        f"- turns vaulted: **{total_turns}**",
        f"- measured bytes: **{total_bytes}**",
        f"- built: {compaction.iso()} by {BUILD_TAG}",
        f"- machine index: `catalog.jsonl` (append-only, complete) · this file is regenerated",
        "",
        "Folders are `vault/<year>/<month>/<session-id>-<title>/` with `transcript.md`, `turns.jsonl`,",
        "`session.json` (the manifest) and `session.zip` (verified on write).",
        "",
    ]
    by_month: dict[str, list[dict[str, Any]]] = {}
    for row in rows[:CATALOG_MD_ROWS]:
        month = str(row.get("month") or f"{str(row.get('created_iso') or '')[:7]}")
        by_month.setdefault(month, []).append(row)
    for month in sorted(by_month, reverse=True):
        out += [f"## {month}", "", "| session | title | turns | started | tags |", "| --- | --- | --- | --- | --- |"]
        for row in by_month[month]:
            title = str(row.get("title") or "").replace("|", "/")
            tags = ", ".join(str(t) for t in (row.get("tags") or []))
            started = str(row.get("created_iso") or "")[:16]
            out.append(f"| `{row.get('sid')}` | {title} | {row.get('turns')} | {started} | {tags} |")
        out.append("")
    try:
        atomic_write_text(vault_root() / "CATALOG.md", "\n".join(out) + "\n")
    except OSError as e:
        return {"ok": False, "error": f"write_failed: {e}"}
    return {"ok": True, "sessions": len(rows), "path": str(vault_root() / "CATALOG.md")}


def stats() -> dict[str, Any]:
    """What is in the vault, and what is not filed yet."""
    rows = _catalog_lines()
    root = vault_root()
    months = sorted({str(r.get("month") or "") for r in rows if r.get("month")})
    filed = {str(r.get("sid")) for r in rows}
    sessions_dir = Path(compaction.SESSIONS)
    live = str(compaction._load_state().get("session_id") or "")
    journals = [p.name[len("journal-"):-len(".jsonl")] for p in sessions_dir.glob("journal-*.jsonl")]
    legacy = [p.name for p in sessions_dir.glob(LEGACY_GLOB)]
    return {
        "ok": True,
        "signature": BUILD_TAG,
        "vault": str(root),
        "sessions": len(rows),
        "turns": sum(int(r.get("turns") or 0) for r in rows),
        "bytes": sum(int(r.get("bytes") or r.get("zip_bytes") or 0) for r in rows),
        "zip_bytes": sum(int(r.get("zip_bytes") or 0) for r in rows),
        "months": months,
        "tags": sorted({str(t) for r in rows for t in (r.get("tags") or [])}),
        "pinned": [str(r.get("sid")) for r in rows if r.get("pinned")],
        "newest": rows[0] if rows else None,
        "catalog": str(root / "catalog.jsonl"),
        "catalog_md": str(root / "CATALOG.md"),
        "unfiled_journals": [sid for sid in journals if sid not in filed and sid != live],
        "live_session": live,
        "live_turns": len(compaction.read_journal(live)) if live else 0,
        "legacy_files": len(legacy),
    }


# ------------------------------------------------------------------------------------------------
# safe wrappers - the console and the agent both come through here
# ------------------------------------------------------------------------------------------------
def _safe(fn, *a, **k) -> dict[str, Any]:
    try:
        return fn(*a, **k)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def safe_stats() -> dict[str, Any]:
    return _safe(stats)


def safe_catalog(limit: int = 200, q: str = "", tag: str = "", month: str = "") -> dict[str, Any]:
    return _safe(catalog, limit, q, tag, month)


def safe_vault(sid: str | None = None, title: str | None = None, reason: str = "manual") -> dict[str, Any]:
    return _safe(vault_session, sid=sid, title=title, reason=reason)


def safe_vault_live(reason: str = "new_session") -> dict[str, Any]:
    return _safe(vault_live, reason)


def safe_adopt(limit: int = 300) -> dict[str, Any]:
    return _safe(adopt, limit)


def safe_open(sid: str, chars: int = 24000) -> dict[str, Any]:
    return _safe(open_session, sid, chars)


def safe_label(sid: str, **kw) -> dict[str, Any]:
    return _safe(label, sid, **kw)


def safe_search(q: str, k: int = 8) -> dict[str, Any]:
    return _safe(search, q, k)


def safe_resume(sid: str, file_current: bool = True) -> dict[str, Any]:
    return _safe(resume, sid, file_current)


def safe_rebuild() -> dict[str, Any]:
    return _safe(rebuild_catalog_md)
