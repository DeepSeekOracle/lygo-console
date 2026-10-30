"""LYGO RAG - recall over the console's own filed history, so a conversation can flow unlimited.

The window cannot hold a long conversation: the console measured its own session at ~5,428% of a 32,768
context and used to answer that by refusing the turn (`quarantine`, HTTP 451). Nothing was wrong with
the conversation - only with insisting the whole of it fit in the window.

This is the recall limb the archive was built for. Every message the console files
(`transcript_archive`) is indexed here, and when a conversation outgrows the window the console hands
the turn the handful of past passages that actually match what is being asked, labelled with where
they came from. Retrieval is pure Python (BM25, no model, no network, no embedding server needed), so
it costs a turn nothing and cannot fail because something else is down.

Nothing here is called by the model: the console runs the search, the agent only ever sees the result
if it is asking about something the window had to drop.

    Δ9Φ963-LYGO-RAG-v1
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

import transcript_archive as archive

SIGNATURE = "Δ9Φ963-LYGO-RAG-v2"
INDEX_NAME = ".lygo-rag.json"
K1, B = 1.5, 0.75
# A filed turn is stored verbatim, so a passage can carry an attached picture's data URL. Recall
# hands passages over as TEXT: those bytes would become tens of thousands of characters of base64 in
# the prompt - the exact thing vision.fit_turn exists to prevent - and the engine would bill them as
# tokens. A filed picture is indexed as a marker instead. Caught live by the vision budget suite.
def _plain(text: str) -> str:
    """Filed text with an attached picture's bytes reduced to a marker - never the bytes themselves."""
    t = text or ""
    return _PICTURE.sub("[image filed with this turn]", t) if "base64," in t.lower() else t
_WRITE_COMPLAINED = False
#: One writer at a time. Readers never take this - they read the destination directly - so recall
#: is never blocked by a rebuild; only the temp-write-and-rename section is serialised.
_WRITE_LOCK = threading.Lock()
_PICTURE = re.compile(
r"data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]{64,}", re.I)
_WORD = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]", re.I)
STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "that", "this", "for", "on", "with",
    "as", "at", "be", "by", "from", "was", "were", "are", "i", "you", "we", "he", "she", "they", "do",
    "did", "does", "but", "if", "then", "so", "not", "no", "yes", "my", "your", "our", "me", "us",
    "what", "which", "who", "when", "where", "how", "why", "can", "could", "would", "should", "will",
    "just", "about", "into", "out", "up", "down", "over", "under", "again", "there", "here", "all",
}


def tokens(text: str) -> list[str]:
    out = [t.lower() for t in _WORD.findall(text or "")]
    return [t for t in out if t not in STOP and len(t) > 1 or (len(t) == 1 and not t.isascii())]


def index_path() -> Path:
    return archive.ROOT / INDEX_NAME


STATE_NAME = ".lygo-rag-state.json"


def state_path() -> Path:
    return archive.ROOT / STATE_NAME


def _empty_record() -> dict[str, Any]:
    return {"runs": 0, "empty": 0, "chars": 0, "chars_total": 0, "hits": 0, "terms": [],
            "last_at": "", "last_query": "", "top": 0.0}


def recall_record() -> dict[str, Any]:
    """What recall has done on the operator's behalf. Never raises, always a complete record."""
    try:
        rec = json.loads(state_path().read_text(encoding="utf-8"))
        return {**_empty_record(), **rec} if isinstance(rec, dict) else _empty_record()
    except Exception:
        return _empty_record()


def _record_recall(query: str, terms: list[str], hits: list[dict[str, Any]], chars: int) -> None:
    """Count every recall, including the ones that found nothing.

    A limb that works invisibly is one the operator cannot leave alone: the window percentage says the
    conversation has outgrown its budget, and nothing says recall is why the turn still works. `empty`
    is counted apart from `runs` so \"asked, and the history holds nothing\" reads differently from
    \"asked, and handed over N passages\".
    """
    rec = recall_record()
    rec["runs"] = int(rec.get("runs") or 0) + 1
    if not hits:
        rec["empty"] = int(rec.get("empty") or 0) + 1
    rec["chars"] = int(chars or 0)
    rec["chars_total"] = int(rec.get("chars_total") or 0) + int(chars or 0)
    rec["hits"] = len(hits or [])
    rec["terms"] = [str(t) for t in (terms or [])][:8]
    rec["top"] = float((hits or [{}])[0].get("score") or 0.0)
    rec["last_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rec["last_query"] = str(query or "").strip()[:160]
    archive.ROOT.mkdir(parents=True, exist_ok=True)
    tmp = state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(rec), encoding="utf-8")
    tmp.replace(state_path())


def _blocks() -> list[dict[str, Any]]:
    """Every filed message, in order: the header line is the block's label, the body is its text."""
    out: list[dict[str, Any]] = []
    for p in archive.files():
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        cur: dict[str, Any] | None = None
        for n, line in enumerate(lines, 1):
            if line.startswith("## "):
                if cur:
                    out.append(cur)
                cur = {"file": str(p), "line": n, "header": line[3:].strip(), "text": []}
            elif cur is not None:
                cur["text"].append(line)
        if cur:
            out.append(cur)
    for b in out:
        b["text"] = _plain("\n".join(b["text"]).strip())
        b["label"] = "STEWARD" if "STEWARD" in b["header"].upper() else "LYGO"
    return out


def _source_fingerprint() -> str:
    h = hashlib.sha1(SIGNATURE.encode())   # a version bump must drop a cached index
    for p in archive.files():
        try:
            st = p.stat()
            h.update(f"{p.name}:{st.st_size}:{int(st.st_mtime)}".encode())
        except Exception:
            continue
    return h.hexdigest()


def build(force: bool = False) -> dict[str, Any]:
    """(Re)build the index if the filed history changed. Cheap, idempotent, safe to call at will."""
    src = _source_fingerprint()
    idx = _load()
    if not force and idx.get("source") == src and idx.get("blocks"):
        return stats(idx)
    blocks = _blocks()
    postings: dict[str, list[list[int]]] = {}
    meta: list[dict[str, Any]] = []
    df: Counter = Counter()
    lengths: list[int] = []
    for i, b in enumerate(blocks):
        tf = Counter(tokens(b["text"] + " " + b["header"]))
        lengths.append(sum(tf.values()) or 1)
        meta.append({"file": b["file"], "line": b["line"], "header": b["header"], "label": b["label"],
                     "text": b["text"]})
        for term, n in tf.items():
            postings.setdefault(term, []).append([i, n])
        for term in tf:
            df[term] += 1
    idx = {"signature": SIGNATURE, "built_at": time.time(), "source": src, "blocks": meta,
           "postings": postings, "df": dict(df), "len": lengths}
    try:
        archive.ROOT.mkdir(parents=True, exist_ok=True)
        # The index lives under workspace/memory/conversations/, and a FRESH INSTALL has no
        # such folder: every file in it is operator history, excluded from the installers on
        # purpose, and Inno Setup only creates a directory for a file it actually copies. So the
        # first boot of an install raised FileNotFoundError here and printed the "could not write
        # the index" line. Create the parent before writing, which also covers a hand-copied kit.
        # ONE WRITER AT A TIME. A per-write temp name stops two writers sharing a temp path, but the
        # os.replace onto the SAME destination still collides under load: with ten threads rebuilding,
        # a writer can lose all six retries to "Access is denied" and report a fault for an index that
        # is in fact current. Serialising only the write section - readers keep reading the
        # destination directly - removes the contention instead of retrying through it.
        index_path().parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK:
            tmp = index_path().with_name(f".lygo-rag.{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                tmp.write_text(json.dumps(idx), encoding="utf-8")
                for attempt in range(6):
                    try:
                        os.replace(tmp, index_path())
                        break
                    except FileNotFoundError:
                        # another writer already renamed its temp into place: the index on disk is
                        # current, so this writer's job is done. Not a moment and not a fault.
                        break
                    except PermissionError:
                        if attempt == 5:
                            raise
                        time.sleep(0.15 * (attempt + 1))
            finally:
                try:
                    tmp.unlink(missing_ok=True)   # a temp that lost its race is not left behind
                except OSError:
                    pass
    except Exception as exc:
        global _WRITE_COMPLAINED
        if not _WRITE_COMPLAINED:      # one line per process, not one per collision
            _WRITE_COMPLAINED = True
            print(f"[rag] could not write the index ({exc!r}); recall still works from memory. "
                  f"A moment, not a fault: the index is rewritten when the history next changes.",
                  flush=True)
    return stats(idx)


def _load() -> dict[str, Any]:
    try:
        got = json.loads(index_path().read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except Exception:
        return {}


def _ensure() -> dict[str, Any]:
    """The index, ready to use. `build()` reports *stats*, so it is the rebuilt file that is read."""
    idx = _load()
    if not idx.get("blocks") or idx.get("source") != _source_fingerprint():
        build()
        idx = _load()
    return idx


def stats(idx: dict[str, Any] | None = None) -> dict[str, Any]:
    idx = _ensure() if idx is None else idx
    return {
        "signature": SIGNATURE,
        "root": str(archive.ROOT),
        "conversations": len(archive.files()),
        "blocks": len(idx.get("blocks") or []),
        "terms": len(idx.get("df") or {}),
        "built_at": idx.get("built_at"),
        "index": str(index_path()) if index_path().is_file() else "",
        "recall": recall_record(),
    }


def query(text: str, k: int = 5) -> list[dict[str, Any]]:
    """The k passages of filed history that best match `text`. Pure BM25, newest first on a tie."""
    idx = _ensure()
    blocks = idx.get("blocks") or []
    if not blocks:
        return []
    terms = tokens(text)
    if not terms:
        return []
    avgdl = (sum(idx.get("len") or [1]) / max(1, len(idx.get("len") or [1]))) or 1.0
    n = len(blocks)
    scores: dict[int, float] = {}
    for term in set(terms):
        posts = (idx.get("postings") or {}).get(term)
        if not posts:
            continue
        idf = math.log(1 + (n - len(posts) + 0.5) / (len(posts) + 0.5))
        for i, tf in posts:
            dl = (idx.get("len") or [1] * n)[i] or 1
            scores[i] = scores.get(i, 0.0) + idf * (tf * (K1 + 1)) / (tf + K1 * (1 - B + B * dl / avgdl))
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], -kv[0]))[: max(1, int(k))]
    out = []
    for i, score in ranked:
        b = dict(blocks[i])
        b["text"] = _plain(b["text"])   # guards an index built before the sanitizer
        b["score"] = round(score, 4)
        out.append(b)
    return out


def recall(text: str, budget_chars: int = 1800, k: int = 6) -> str:
    """A labelled block of the best-matching history, sized to a budget. "" when nothing matches.

    This is what the console injects when a conversation has outgrown the window: not a summary of the
    whole thing (which is what the model would otherwise be asked to produce, badly), but the passages
    that bear on what is being asked right now, with their date, speaker and file so the agent can go
    and read more.
    """
    budget = max(400, int(budget_chars or 0))
    hits = query(text, k=k)
    if not hits:
        try:
            # "asked, and the history holds nothing" must be recorded too - otherwise silence here is
            # indistinguishable from the RAG never having run at all.
            _record_recall(text, tokens(text), [], 0)
        except Exception:
            pass
        return ""
    head = (f"[{SIGNATURE}] recall from the filed history - the passages that match. The whole "
            f"conversation is filed under {archive.ROOT} (grep {archive.index_path().name}); read a file "
            f"directly when you need more of it.\n")
    # A recalled passage is half a conversation: the steward's ask without the answer that settled it,
    # or the answer without the question it answered. Pull in the other block of the same turn.
    blocks = _ensure().get("blocks") or []
    by_turn: dict[tuple[str, str], list[int]] = {}
    for i, b in enumerate(blocks):
        key = (str(b.get("file")), str(b.get("header", "").split("·")[1].strip() if "·" in b.get("header", "") else ""))
        by_turn.setdefault(key, []).append(i)
    picked: list[dict[str, Any]] = []
    for h in hits:
        picked.append(h)
        for i, b in enumerate(blocks):
            if b is h or (b.get("file") == h.get("file") and b.get("text") == h.get("text")):
                for j in (i - 1, i + 1):
                    if 0 <= j < len(blocks) and blocks[j].get("file") == h.get("file"):
                        mate = dict(blocks[j])
                        if mate.get("text") and mate["text"] != h.get("text"):
                            mate["score"] = h.get("score", 0)
                            picked.append(mate)
                        break
                break

    parts = [head]
    used = len(head)
    for h in picked:
        header = f"\n--- {h['header']} · {Path(h['file']).name}\n"
        room = budget - used - len(header) - 2
        if room < 120:
            break
        body = _plain(h["text"])
        clipped = body if len(body) <= room else body[:room].rsplit("\n", 1)[0] + "\n[…truncated here; the whole passage is filed]"
        parts.append(header + clipped + "\n")
        used += len(header) + len(clipped) + 1
    note = "".join(parts) if len(parts) > 1 else ""
    try:
        # Recorded whether or not anything came back, and never at the turn's expense.
        _record_recall(text, tokens(text), hits if note else [], len(note))
    except Exception:
        pass
    return note


def status() -> dict[str, Any]:
    """For the console's health pane. Never raises, never scans the disk twice."""
    try:
        return stats()
    except Exception as exc:
        return {"signature": SIGNATURE, "error": repr(exc)}
