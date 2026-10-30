"""Conversation compaction: a small live window, a complete record.

The problem this solves: an engine has one fixed context window, and a chat that outgrows it loses the
oldest turns silently. Three separate throttles used to cut the record - the portal sent only its last
24 messages, `trim_history()` capped history at 9,000 characters, and `save_session()` kept only the
last 40 messages on disk. Anything older than that was gone: not summarised, not archived, gone.

This module keeps three tiers instead of a cliff:

* **T1 journal** - `save/sessions/journal-<sid>.jsonl`, append-only, one JSON line per message, verbatim,
  stamped with both a unix time and a readable local time. Every turn goes in. Nothing is ever trimmed
  out of it, so the transcript of record is complete regardless of what the engine sees.
* **T2 rollups** - `save/sessions/rollups/<sid>-NNNN.json` + `.md`. When turns leave the live window
  they are folded into a deterministic digest: per turn, its stamp, its role, its opening text, and the
  paths/URLs/tools it touched. No model call, so compaction can never stall a turn, hallucinate a
  fact, or enter a summarise-the-summary drift loop. `carry_over()` returns the bounded block of the
  newest rollups that goes into the system prompt, so the model always knows what was said before.
* **T3 archive** - `save/archive/<sid>.zip` + `save/archive/index.jsonl`. A sealed session (journal,
  rollups, readable transcript, manifest with hashes) is compressed and indexed once the conversation
  is done or the journal passes its size/turn cap. `recall()` searches the live journal *and* the
  archives, so any turn can be pulled back verbatim on demand - which is what makes a huge context
  window unnecessary rather than merely unaffordable.

Design rules, each one learned the hard way elsewhere in this kit:

* **No stall is allowed.** Compaction is deterministic and bounded; it never calls the engine. A turn
  that overflows triggers `compact()` before the prompt is built, and that call is cheap enough to sit
  inside a request.
* **Nothing raises into a request handler.** Every entry point is wrapped: a compaction failure must
  cost a digest, never a turn. Callers use `safe_*` helpers.
* **Idempotent.** `covered_upto` records how far the rollups have folded the journal, so re-running
  compaction rewrites nothing and never double-counts.
* **Writes are atomic and unique-temp** (`atomicio.atomic_write_text`), because a fixed `.tmp` name is
  what killed this console under two concurrent readers.
* **Verify before delete.** A sealed zip is read back (`testzip()` + journal hash) before the live
  journal is trimmed; an archive is never the only copy of something that has been discarded.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import zipfile
from hashlib import sha256
from pathlib import Path
from typing import Any

from atomicio import atomic_write_text
from atomicio import read_text as read_text_locked
from paths import SAVE, ensure_dirs

SESSIONS = SAVE / "sessions"
ROLLUPS = SESSIONS / "rollups"
CHECKPOINTS = SESSIONS / "checkpoints"
ARCHIVE = SAVE / "archive"
BUNDLES = ARCHIVE / "bundles"
INDEX = ARCHIVE / "index.jsonl"
STATE = SESSIONS / "compaction_state.json"

BUILD_TAG = "Δ9Φ963-LYGO-COMPACTION-v1"

# --- window sizing ---------------------------------------------------------------------------
# Chars-per-token is deliberately pessimistic (English + code prose runs ~3.6-4). The engine also
# counts its own tokens; this estimate only decides when to compact, and over-estimating costs a
# little earlier compaction rather than an overflowed prompt.
CHARS_PER_TOKEN = 3.6


def answer_reserve() -> int:
    """Tokens held back for the reply.

    Read from the operator's own `max_tokens`, not a constant: when the two were separate numbers
    (a 1024 constant against a portal that sent 768, then against a config that said something else
    again) the window reserved room for a reply nobody was asking for. Raising the reply length and
    leaving this behind is how a long answer ends up overflowing the window and evicting the
    conversation it was answering.
    """
    try:
        from paths import console_limits

        return max(256, int(console_limits().get("max_tokens") or ANSWER_RESERVE_FALLBACK))
    except Exception:  # noqa: BLE001 - a bad config must not break a turn
        return ANSWER_RESERVE_FALLBACK


ANSWER_RESERVE_FALLBACK = 1024  # only used when the config cannot be read at all
ANSWER_RESERVE = ANSWER_RESERVE_FALLBACK  # kept for callers that imported the old name
SAFETY_RESERVE = 640           # tokens of slack for tool traces and the per-turn host notes
RECALL_RESERVE = 420           # tokens held back so a recalled passage always fits the turn
# An ask that refers to the past is the one that wants the archive. Scoring cannot decide this and was
# measured trying: on the live 541-block index "what is 17 times 23" scored 13.12 and 7.42 of IDF match
# mass against the best passage, while a genuine "what did we say about the seal button" scored 7.97 and
# 4.62 - the noise outscored the question in both currencies, because in an archive of everything, almost
# every word appears somewhere. A reference to earlier conversation is what a turn needs the past for,
# and that is something the ask states.
RECALL_PAST_MARKERS = (
    "what did we", "what did you", "what did i", "remind me", "we said", "you said", "i said",
    "earlier", "last time", "we decided", "you decided", "as discussed", "previously", "before that",
    "go back to", "the conversation so far", "we talked about", "mentioned", "recall", "remember when",
    "what was the", "where did we leave", "you told me",
)
AUTO_COMPACT_AT = 0.78         # compact when the live window passes this fraction of its budget

# --- how much stays live ---------------------------------------------------------------------
LIVE_KEEP_TURNS = 12           # newest turns always sent verbatim, whatever the token budget says
LIVE_KEEP_MAX_TURNS = 400      # hard ceiling on messages the portal may hand over per turn
ROLLUP_TURNS = 24              # turns folded into one digest
DIGEST_LINE = 220              # chars kept of each turn inside a digest
CARRY_CAP = 1400               # chars of compacted history injected into the system prompt
CARRY_ROLLUPS = 6              # newest rollups the carry-over block quotes
CHECKPOINT_KEEP = 3            # readable autosaves kept in save/sessions/checkpoints
AUTOSAVE_EVERY = 8             # turns between checkpoints
JOURNAL_MAX_BYTES = 4 * 1024 * 1024
JOURNAL_MAX_TURNS = 600

# --- recall ----------------------------------------------------------------------------------
RECALL_K = 5
RECALL_SNIPPET = 700
RECALL_ARCHIVES = 8            # newest archives searched by a recall
RECALL_SCAN_BYTES = 8 * 1024 * 1024

STOPWORDS = frozenset(
    """a an and are as at be but by can did do does for from had has have how i if in into is it its
    me my no not of on or our so that the their them then there these they this to was we were what
    when where which who why will with you your""".split()
)

_LOCK = threading.RLock()


# ------------------------------------------------------------------------------------------------
# time / hashing helpers
# ------------------------------------------------------------------------------------------------
def iso(ts: float | None = None) -> str:
    """Readable local stamp: `2026-09-19 13:49:37`. Every record carries one."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts if ts is not None else time.time()))


def compact_id(ts: float | None = None) -> str:
    """Session id shape: `20260919-134937-4f2a` - sorts by time, still unique."""
    t = ts if ts is not None else time.time()
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(t)) + "-" + sha256(f"{t}".encode()).hexdigest()[:4]


def digest_of(text: Any) -> str:
    if isinstance(text, (bytes, bytearray)):
        return sha256(bytes(text)).hexdigest()
    return sha256(str(text or "").encode("utf-8", "replace")).hexdigest()


def est_tokens(text: Any) -> int:
    """Pessimistic token estimate. A non-string (image parts) is charged a flat cost."""
    if isinstance(text, str):
        return int(len(text) / CHARS_PER_TOKEN) + 1
    if content_parts(text):
        # A picture is charged per patch of PIXELS, never as a flat 900-token message. Measured on this
        # host 2026-09-21 (gemma4-12b with its projector): a 1024x1024 photo cost the engine 8,998
        # tokens more than a 256x256 one, over 983,040 more pixels. The flat number is how a turn came
        # to be sent into a window it could not fit - the engine refused it whole ("request (133868
        # tokens) exceeds the available context size (32768 tokens)") and the operator got an empty
        # bubble. `vision` owns the rule so the boot, the limbs, the budget and the gate agree.
        try:
            import vision

            return int(vision.est_text_tokens(text) + vision.est_image_tokens(text))
        except Exception:
            return 900
    return 1


def content_text(content: Any) -> str:
    """Flatten an OpenAI-style content (str, or a list with text/image_url parts) to prose."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        bits = []
        for p in content:
            if not isinstance(p, dict):
                continue
            if p.get("type") == "image_url":
                bits.append("[image]")
            elif p.get("text"):
                bits.append(str(p["text"]))
        return "\n".join(bits)
    if content is None:
        return ""
    return str(content)


def content_parts(content: Any) -> bool:
    return isinstance(content, list) and any(
        isinstance(p, dict) and p.get("type") == "image_url" for p in content
    )


def image_name(content: Any) -> str:
    """A short label for an attached image so a digest records that something was shown."""
    if isinstance(content, list):
        for p in content:
            if isinstance(p, dict) and p.get("type") == "image_url":
                url = str((p.get("image_url") or {}).get("url") or "")
                head = url.split(",", 1)[0]
                return "image" if head.startswith("data:") else (url.split("/")[-1][:60] or "image")
    return ""


# ------------------------------------------------------------------------------------------------
# state
# ------------------------------------------------------------------------------------------------
def _dirs() -> None:
    ensure_dirs()
    for d in (SESSIONS, ROLLUPS, CHECKPOINTS, ARCHIVE, BUNDLES):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def _default_state() -> dict[str, Any]:
    now = time.time()
    return {
        "signature": BUILD_TAG,
        "session_id": compact_id(now),
        "created": now,
        "created_iso": iso(now),
        "next_i": 1,
        "turns": 0,
        "covered_upto": 0,
        "compactions": 0,
        "last_compact": None,
        "last_compact_iso": None,
        "last_save": None,
        "last_save_iso": None,
        "last_checkpoint_turn": 0,
        "checkpoints": [],
        "rolled": [],
        "rollups": [],
        "carry": "",
        "carry_turns": 0,
        "carry_from": None,
        "carry_to": None,
        "sealed_carry": None,
        "session_seq": 0,
        "sealed_turns": 0,
        "sealed_bytes": 0,
        # Set by sessions.py (the vault). _load_state() keeps ONLY the keys listed here, so a key
        # that is written but not declared is silently dropped on the next read - which is how the
        # resume lineage and the "where did that chat go" report went missing the first time.
        "last_vault": None,
        "resumed_from": None,
        "resumed_iso": None,
    }


def _load_state() -> dict[str, Any]:
    _dirs()
    if not STATE.is_file():
        st = _default_state()
        _write_state(st)
        return st
    try:
        raw = json.loads(read_text_locked(STATE) or "{}")
    except (OSError, json.JSONDecodeError):
        raw = {}
    st = _default_state()
    if isinstance(raw, dict):
        st.update({k: v for k, v in raw.items() if k in st})
    if not isinstance(st.get("rollups"), list):
        st["rollups"] = []
    if not isinstance(st.get("checkpoints"), list):
        st["checkpoints"] = []
    return st


def _write_state(st: dict[str, Any]) -> None:
    st["signature"] = BUILD_TAG
    st["updated"] = time.time()
    try:
        atomic_write_text(STATE, json.dumps(st, indent=1, ensure_ascii=False))
    except OSError:
        pass


# ------------------------------------------------------------------------------------------------
# T1 - the journal
# ------------------------------------------------------------------------------------------------
def journal_path(sid: str | None = None) -> Path:
    _dirs()
    sid = sid or _load_state().get("session_id") or "session"
    return SESSIONS / f"journal-{sid}.jsonl"


def _read_lines(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL journal, skipping a torn final line instead of losing the whole file."""
    out: list[dict[str, Any]] = []
    if not path.is_file():
        return out
    try:
        blob = read_text_locked(path, errors="replace")
    except OSError:
        return out
    for line in blob.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("role"):
            out.append(rec)
    return out


def read_journal(sid: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    recs = _read_lines(journal_path(sid))
    return recs[-limit:] if limit else recs


def record(role: str, content: Any, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Append one message to the journal. Idempotent on a repeated (role, text) pair.

    The exact last record is compared, so a retried turn, a double-posted payload or a resumed
    session cannot write the same message twice - and a *different* message with identical text on a
    later turn still lands, because only the immediately preceding record is checked.
    """
    role = str(role or "").strip().lower()
    if role not in {"user", "assistant", "system", "tool"}:
        return {"ok": False, "error": "bad_role", "role": role}
    text = content_text(content)
    if not text.strip() and not content_parts(content):
        return {"ok": False, "error": "empty"}
    with _LOCK:
        st = _load_state()
        recs = _read_lines(journal_path(st["session_id"]))
        fp = role + ":" + digest_of(text)[:16]
        if recs and recs[-1].get("fp") == fp:
            return {"ok": True, "duplicate": True, "i": recs[-1].get("i")}
        now = time.time()
        i = int(st.get("next_i") or 1)
        rec = {
            "i": i,
            "ts": round(now, 3),
            "iso": iso(now),
            "role": role,
            "chars": len(text),
            "tokens": est_tokens(content),
            "sha": digest_of(text)[:16],
            "fp": fp,
            "content": content if isinstance(content, str) else content,
            "meta": dict(meta or {}),
        }
        if content_parts(content):
            rec["image"] = image_name(content)
        path = journal_path(st["session_id"])
        try:
            with path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
        except OSError as e:
            return {"ok": False, "error": "journal_write_failed", "detail": str(e)}
        st["next_i"] = i + 1
        st["turns"] = len(recs) + 1
        st["last_save"] = now
        st["last_save_iso"] = iso(now)
        _write_state(st)
        return {"ok": True, "i": i, "path": str(path), "turns": st["turns"]}


def journal_bytes(sid: str | None = None) -> int:
    p = journal_path(sid)
    try:
        return p.stat().st_size if p.is_file() else 0
    except OSError:
        return 0


# ------------------------------------------------------------------------------------------------
# T2 - digests, rollups, carry-over
# ------------------------------------------------------------------------------------------------
_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"'|<>]+|/(?:[\w.\-]+/){2,}[\w.\-]+")
_URL_RE = re.compile(r"https?://[^\s\"'<>)]+")
_TOOLISH = re.compile(r"^(web_search|web_fetch|calc|read_file|write_file|shell|python_exec|recall_history)\b", re.I)


def _artifacts(text: str) -> dict[str, list[str]]:
    paths = [p[:120] for p in _PATH_RE.findall(text or "")][:4]
    urls = [u[:160] for u in _URL_RE.findall(text or "")][:4]
    return {"paths": paths, "urls": urls}


def turn_line(rec: dict[str, Any], width: int = DIGEST_LINE) -> str:
    """One deterministic line for one turn: stamp, role, opening text, artifacts."""
    text = content_text(rec.get("content")).strip().replace("\r", " ")
    text = re.sub(r"\s+", " ", text)
    line = f"[{rec.get('i')}] {rec.get('iso')} {rec.get('role')}"
    if rec.get("image"):
        line += f" (image: {rec['image']})"
    if text:
        line += ": " + (text[:width] + ("…" if len(text) > width else ""))
    art = _artifacts(text)
    if art["paths"]:
        line += " · files " + " ".join(art["paths"][:2])
    if art["urls"]:
        line += " · urls " + " ".join(art["urls"][:2])
    return line


def digest_block(recs: list[dict[str, Any]]) -> str:
    """The per-turn digest of a run of turns. Deterministic: same records, same text, always."""
    return "\n".join(turn_line(r) for r in recs)


def _highlights(recs: list[dict[str, Any]], n: int = 6) -> list[str]:
    """The lines worth carrying into the prompt: newest first, with files/urls preferred."""
    scored: list[tuple[int, dict[str, Any]]] = []
    for r in recs:
        text = content_text(r.get("content"))
        score = 0
        if _artifacts(text)["paths"]:
            score += 2
        if _artifacts(text)["urls"]:
            score += 2
        if r.get("role") == "assistant":
            score += 1
        if _TOOLISH.match(text.strip()):
            score += 1
        score += min(3, len(text) // 400)
        scored.append((score, r))
    scored.sort(key=lambda pair: (pair[0], pair[1].get("i") or 0), reverse=True)
    return [turn_line(r, width=170) for _, r in scored[:n]]


def _write_rollup(st: dict[str, Any], covered: list[dict[str, Any]]) -> dict[str, Any]:
    sid = st["session_id"]
    # Monotonic per session: consolidation moves superseded files to the archive, so counting the
    # state list alone would hand out a number that a previously-moved file already had.
    n = 0
    for path in st.get("rollups") or []:
        m = re.search(r"-(\d+)\.json$", str(path))
        if m:
            n = max(n, int(m.group(1)))
    if not n:
        n = len(st.get("rollups") or [])
    n += 1
    text = digest_block(covered)
    first, last = covered[0], covered[-1]
    obj = {
        "signature": BUILD_TAG,
        "sid": sid,
        "n": n,
        "created_iso": iso(),
        "first_i": first.get("i"),
        "last_i": last.get("i"),
        "turns": len(covered),
        "from_iso": first.get("iso"),
        "to_iso": last.get("iso"),
        "chars": sum(len(content_text(r.get("content"))) for r in covered),
        "tokens": sum(int(r.get("tokens") or 0) for r in covered),
        "sha256": digest_of(text),
        "digest": text,
        "highlights": _highlights(covered),
        "artifacts": {
            "paths": sorted({p for r in covered for p in _artifacts(content_text(r.get("content")))["paths"]})[:12],
            "urls": sorted({u for r in covered for u in _artifacts(content_text(r.get("content")))["urls"]})[:12],
        },
    }
    jp = ROLLUPS / f"{sid}-{n:04d}.json"
    atomic_write_text(jp, json.dumps(obj, indent=1, ensure_ascii=False))
    # A human-readable twin: the operator can read what the engine was actually told.
    lines = [f"# rollup {n} · session {sid}", f"{obj['from_iso']} → {obj['to_iso']} · {obj['turns']} turns", ""]
    lines += [turn_line(r, width=400) for r in covered]
    atomic_write_text(ROLLUPS / f"{sid}-{n:04d}.md", "\n".join(lines) + "\n")
    st.setdefault("rollups", []).append(str(jp))
    return obj


def _rollup_objects(st: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for p in st.get("rollups") or []:
        try:
            out.append(json.loads(read_text_locked(Path(p)) or "{}"))
        except (OSError, json.JSONDecodeError):
            continue
    return [o for o in out if isinstance(o, dict) and o.get("digest")]


def build_carry(sid: str | None = None) -> str:
    """The bounded 'what was said before it left the window' block for the system prompt."""
    with _LOCK:
        st = _load_state()
        rolls = _rollup_objects(st)
        if not rolls:
            return ""
        total_turns = sum(int(r.get("turns") or 0) for r in rolls)
        head = (
            f"=== EARLIER CONVERSATION (compacted) ===\n"
            f"{total_turns} earlier turns ({rolls[0].get('from_iso')} → {rolls[-1].get('to_iso')}) were "
            f"compacted out of this window and are kept verbatim in the session archive. "
            f"Treat the notes below as things that were already said; call recall_history with keywords "
            f"to pull any of those turns back word for word.\n"
        )
        body: list[str] = []
        for r in rolls[-CARRY_ROLLUPS:]:
            body.append(f"-- {r.get('from_iso')} → {r.get('to_iso')} ({r.get('turns')} turns)")
            for h in (r.get("highlights") or [])[:4]:
                body.append("   " + str(h))
        text = head + "\n".join(body)
        if len(text) > CARRY_CAP:
            text = text[:CARRY_CAP] + "\n…[older compacted notes trimmed]…"
        return text


def carry_over(cap: int = CARRY_CAP) -> str:
    """The carry-over block, P0-gated.

    Compaction text re-enters the prompt as if the host wrote it, so it is judged by the same gate as
    any other input: a quarantined digest is replaced by a pointer, never pasted in.
    """
    try:
        with _LOCK:
            st = _load_state()
            text = st.get("carry") or build_carry()
            if not text:
                return ""
            if len(text) > cap:
                text = text[:cap]
    except Exception as e:  # noqa: BLE001 - a compaction block must never cost a turn
        return f"(compacted history unavailable this turn: {type(e).__name__})"
    try:
        from p0_hook import gate_prompt

        if gate_prompt(text).get("verdict") == "QUARANTINE":
            return (
                "=== EARLIER CONVERSATION (compacted) ===\n"
                "Earlier turns were compacted out of this window; the digest was withheld by the P0 "
                "gate. Call recall_history with keywords to read the original turns from the archive."
            )
    except Exception:  # noqa: BLE001
        pass
    return text


# ------------------------------------------------------------------------------------------------
# window sizing
# ------------------------------------------------------------------------------------------------
def system_reserve() -> int:
    """Tokens to hold back for the composed identity block (measured, not guessed).

    Measured against the TOTAL prompt ceiling - identity block plus the compacted-conversation digest -
    because both ride on a real turn. Reserving only the identity block would let the digest eat into
    the history budget it was sized for.
    """
    try:
        from continuity import PROMPT_CEILING_TOTAL

        return int(PROMPT_CEILING_TOTAL / CHARS_PER_TOKEN) + 200
    except Exception:  # noqa: BLE001
        return 5000


def live_ctx() -> int:
    """The context the engine will actually run: chat-model native, clamped by config ctx_max.

    An embedding record (nomic 2,048) must never size the chat turn: that is how a ~10k
    photo request was billed against a 2k window and came back empty.
    """
    try:
        from engine import clamp_ctx
        from paths import console_limits

        lim = console_limits()
        rec = _chat_window_record(_selected_record())
        return int(
            clamp_ctx(
                (rec or {}).get("ctx"),
                lim.get("ctx_max"),
                architecture=(rec or {}).get("architecture"),
                kind=(rec or {}).get("kind"),
                model_id=(rec or {}).get("id"),
            )
        )
    except Exception:  # noqa: BLE001
        try:
            from paths import console_limits as _lim

            return int(_lim().get("ctx_max") or 32768)
        except Exception:  # noqa: BLE001
            return 32768


def _chat_window_record(rec: dict[str, Any] | None) -> dict[str, Any] | None:
    """The chat brain's record, even if the registry pin currently names an embedder."""
    try:
        from engine import is_embed_model
    except Exception:  # noqa: BLE001
        is_embed_model = lambda *a, **k: False  # noqa: E731
    if rec and not is_embed_model(rec.get("kind"), rec.get("architecture"), rec.get("id")):
        return rec
    try:
        import registry
        from paths import console_cfg

        data = registry.load() if hasattr(registry, "load") else {}
        models = list((data or {}).get("models") or [])
        prefer = list((console_cfg() or {}).get("prefer_ids") or [])
        ordered: list[dict[str, Any]] = []
        for pid in prefer:
            hit = next((m for m in models if str(m.get("id")) == str(pid)), None)
            if hit:
                ordered.append(hit)
        ordered.extend(models)
        for m in ordered:
            if not isinstance(m, dict):
                continue
            if is_embed_model(m.get("kind"), m.get("architecture"), m.get("id")):
                continue
            if str(m.get("kind") or "chat").lower() in ("", "chat"):
                return m
    except Exception:  # noqa: BLE001
        pass
    return rec


def _selected_record() -> dict[str, Any] | None:
    try:
        import registry

        rec = registry.get_selected() if hasattr(registry, "get_selected") else None
        if isinstance(rec, dict):
            return rec
        data = registry.load() if hasattr(registry, "load") else {}
        mid = (data or {}).get("selected")
        for m in (data or {}).get("models") or []:
            if m.get("id") == mid:
                return m
    except Exception:  # noqa: BLE001
        pass
    try:
        from paths import SAVE as _S

        data = json.loads(read_text_locked(_S / "registry.json") or "{}")
        mid = data.get("selected")
        for m in data.get("models") or []:
            if m.get("id") == mid:
                return m
    except Exception:  # noqa: BLE001
        pass
    return None


def window_budget(ctx: int | None = None, answer_tokens: int | None = None) -> dict[str, Any]:
    """How many tokens of conversation the live window may carry.

    `answer_tokens` defaults to the operator's configured reply cap rather than a constant, so the
    window reserves room for the reply that is actually about to be asked for.
    """
    if answer_tokens is None:
        answer_tokens = answer_reserve()
    ctx = int(ctx or live_ctx() or 8192)
    reserve = system_reserve()
    room = ctx - reserve - int(answer_tokens) - SAFETY_RESERVE
    room = max(512, room)
    return {
        "ctx": ctx,
        "system_reserve": reserve,
        "answer_reserve": int(answer_tokens),
        "safety_reserve": SAFETY_RESERVE,
        "recall_reserve": RECALL_RESERVE,
        "history_tokens": room,
        "history_chars": int(room * CHARS_PER_TOKEN),
        "compact_at": int(room * AUTO_COMPACT_AT),
        "chars_per_token": CHARS_PER_TOKEN,
    }


def history_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(int(est_tokens(m.get("content"))) for m in messages or [] if isinstance(m, dict))


def window_pct(messages: list[dict[str, Any]], ctx: int | None = None) -> float:
    budget = window_budget(ctx)
    return round(100.0 * history_tokens(messages) / max(1, budget["history_tokens"]), 1)


def recall_for(ask: str, messages: list[dict[str, Any]] | None = None, ctx: int | None = None) -> str:
    """The filed history this turn needs, or "". Autonomous, bounded, and silent when unsure.

    Two reasons to speak up, and only two:

    * the live window had to shed turns - the conversation outgrew it, and whatever it dropped is
      filed, so the passages bearing on this ask come back with the turn;
    * nothing was shed, but the ask clearly matches something filed that this turn is not already
      carrying - a detail from earlier in the session, or from an earlier session.

    The second is what makes the archive useful on a short turn. It is gated on score and on the
    passage not already being in the window, because an always-on recap would spend the window on
    noise and call it memory. `lygo_rag.recall` is pure local BM25 - no model, no network - so
    asking costs a turn nothing, and this never raises.
    """
    try:
        from lygo_rag import query as _rag_query
        from lygo_rag import recall as _rag_recall

        budget_chars = int(round(RECALL_RESERVE * CHARS_PER_TOKEN))
        shed = 0
        if messages is not None:
            try:
                _kept, shed, _info = trim_messages(messages, ctx)
            except Exception:  # noqa: BLE001
                shed = 0
        if not shed:
            low = str(ask or "").lower()
            if not any(marker in low for marker in RECALL_PAST_MARKERS):
                return ""  # an ask that does not refer back is not asking for the archive
            # No score floor here. BM25 scores shrink with the index, so a floor tuned on the live
            # 541-block archive silenced the same question in a two-block test one - the reference to
            # the past is the gate, and a passage that matches nothing returns "" on its own below.
            hits = _rag_query(str(ask or ""), k=1)
            if not hits:
                return ""
            live = " ".join(str(m.get("content") or "") for m in (messages or []) if isinstance(m, dict))
            top = str(hits[0].get("text") or "").strip()
            if top and top[:120] in live:
                return ""  # the window is already carrying it; saying it twice is noise
        return _rag_recall(str(ask or ""), budget_chars=budget_chars)
    except Exception:  # noqa: BLE001
        return ""


def trim_messages(
    messages: list[dict[str, Any]],
    ctx: int | None = None,
    keep_turns: int | None = None,
) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    """Trim the history to the live budget. Returns (kept, dropped_count, info).

    Newest-first within the budget, but the newest `keep_turns` messages are never dropped - a single
    huge message used to be able to evict the rest of the conversation, including the question just
    asked. The system prompt is not this function's business: it is prepended after the trim.

    `keep_turns` is resolved at call time on purpose: a default argument would freeze the module
    constant at import, so a knob changed at runtime (or in a test) would be silently ignored.
    """
    keep = max(2, int(keep_turns if keep_turns is not None else LIVE_KEEP_TURNS))
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    budget = window_budget(ctx)
    room = budget["history_tokens"]
    # The live history leaves room for the recall note that a long conversation rides on, so a recap
    # can never be the reason the engine refuses the turn.
    room = max(512, room - int(budget.get("recall_reserve") or 0))
    kept: list[dict[str, Any]] = []
    used = 0
    for idx, m in enumerate(reversed(msgs)):
        cost = int(est_tokens(m.get("content")))
        if idx < keep or used + cost <= room or not kept:
            kept.append(m)
            used += cost
            continue
        break
    kept.reverse()
    dropped = len(msgs) - len(kept)
    info = {"budget": budget, "used_tokens": used, "kept": len(kept), "dropped": dropped, "total": len(msgs),
            "keep_turns": keep}
    return kept, dropped, info


# ------------------------------------------------------------------------------------------------
# compaction
# ------------------------------------------------------------------------------------------------
def compact(
    reason: str = "manual",
    keep_turns: int | None = None,
    sid: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Fold every turn that has left the live window into rollups. Never calls the model.

    Deterministic and idempotent: `covered_upto` records the last folded index, so a second call with
    nothing new to fold writes nothing and says so. `keep_turns` is resolved at call time, never as a
    default argument: a default would freeze the module constant at import.
    """
    keep = max(2, int(keep_turns if keep_turns is not None else LIVE_KEEP_TURNS))
    with _LOCK:
        st = _load_state()
        sid = sid or st["session_id"]
        recs = _read_lines(journal_path(sid))
        if not recs:
            return {"ok": True, "folded": 0, "reason": reason, "note": "nothing in the journal yet"}
        frontier = max(0, len(recs) - keep)
        covered_upto = int(st.get("covered_upto") or 0)
        pending = _pending_to_fold(st, recs, keep)
        if not pending and not force:
            return {
                "ok": True,
                "folded": 0,
                "reason": reason,
                "covered_upto": covered_upto,
                "live_turns": len(recs) - frontier,
                "note": "nothing has left the live window yet",
            }
        # Only COMPLETE batches fold. A turn that leaves the live window one at a time used to become
        # its own two-turn rollup: 1,660 near-duplicate files in this store, and a carry-over block
        # that quoted 12 earlier turns where the design intends ~144. The partial tail stays pending
        # and goes in with the next batch - it is still in the journal, and still inside the window the
        # token budget pays for, so nothing is lost by waiting. `force` (a seal, or an explicit
        # maintenance call) folds the tail now.
        batch = pending if force else pending[: (len(pending) // ROLLUP_TURNS) * ROLLUP_TURNS]
        if not batch:
            return {
                "ok": True,
                "folded": 0,
                "reason": reason,
                "held": len(pending),
                "covered_upto": covered_upto,
                "live_turns": len(recs) - frontier,
                "note": f"holding {len(pending)} turn(s) until a full batch of {ROLLUP_TURNS} is ready",
            }
        folded: list[dict[str, Any]] = []
        for start in range(0, len(batch), ROLLUP_TURNS):
            chunk = batch[start : start + ROLLUP_TURNS]
            if not chunk:
                continue
            folded.append(_write_rollup(st, chunk))
        st["covered_upto"] = int(batch[-1].get("i") or covered_upto)
        st["compactions"] = int(st.get("compactions") or 0) + 1
        st["last_compact"] = time.time()
        st["last_compact_iso"] = iso()
        st["carry"] = build_carry_locked(st)
        if folded:
            first, last = folded[0], folded[-1]
            st["carry_turns"] = int(st.get("carry_turns") or 0) + sum(int(f.get("turns") or 0) for f in folded)
            st["carry_from"] = first.get("from_iso")
            st["carry_to"] = last.get("to_iso")
        _write_state(st)
        return {
            "ok": True,
            "folded": sum(int(f.get("turns") or 0) for f in folded),
            "rollups": len(folded),
            "held": max(0, len(pending) - len(batch)),
            "covered_upto": st["covered_upto"],
            "reason": reason,
            "live_turns": len(recs) - frontier,
            "carry_turns": st.get("carry_turns"),
            "carry_span": [st.get("carry_from"), st.get("carry_to")],
            "carry": st.get("carry") or "",
        }


# ------------------------------------------------------------------------------------------------
# maintenance: consolidating what an earlier build inflated, and moving debris out of the live store
# ------------------------------------------------------------------------------------------------
def _rollup_files() -> dict[str, list[Path]]:
    """Every live rollup file, grouped by session id, oldest name first."""
    out: dict[str, list[Path]] = {}
    if not ROLLUPS.is_dir():
        return out
    for p in sorted(ROLLUPS.glob("*.json")):
        sid = p.name.rsplit("-", 1)[0]
        out.setdefault(sid, []).append(p)
    return out


def _load_rollup(p: Path) -> dict[str, Any] | None:
    obj = None
    try:
        raw = read_text_locked(p) or "{}"
        obj = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) and obj.get("digest") else None


def _merge_rollups(sid: str, objs: list[dict[str, Any]], n: int) -> dict[str, Any]:
    """One fatter rollup from several thin ones - lossless, because a rollup's payload is the digest
    text of its turns. Concatenating the digests in order carries every turn line, unchanged."""
    text = "\n".join(str(o.get("digest") or "").strip("\n") for o in objs).strip("\n")
    highlights: list[str] = []
    paths: list[str] = []
    urls: list[str] = []
    for o in objs:
        for h in o.get("highlights") or []:
            if h not in highlights:
                highlights.append(str(h))
        art = o.get("artifacts") if isinstance(o.get("artifacts"), dict) else {}
        paths += [str(x) for x in (art.get("paths") or [])]
        urls += [str(x) for x in (art.get("urls") or [])]
    return {
        "signature": BUILD_TAG, "sid": sid, "n": n, "created_iso": iso(),
        "first_i": objs[0].get("first_i"), "last_i": objs[-1].get("last_i"),
        "turns": sum(int(o.get("turns") or 0) for o in objs),
        "from_iso": objs[0].get("from_iso"), "to_iso": objs[-1].get("to_iso"),
        "chars": sum(int(o.get("chars") or 0) for o in objs),
        "tokens": sum(int(o.get("tokens") or 0) for o in objs),
        "sha256": digest_of(text), "digest": text, "highlights": highlights[:8],
        "artifacts": {"paths": sorted(set(paths))[:12], "urls": sorted(set(urls))[:12]},
        "consolidated_from": [o.get("n") for o in objs],
        "consolidated_count": len(objs),
    }


def _write_rollup_pair(obj: dict[str, Any], stem: str) -> list[Path]:
    """The .json and its readable .md twin, under one name stem."""
    jp = ROLLUPS / (stem + ".json")
    atomic_write_text(jp, json.dumps(obj, indent=1, ensure_ascii=False))
    lines = [f"# rollup {obj.get('n')} · session {obj.get('sid')}",
             f"{obj.get('from_iso')} → {obj.get('to_iso')} · {obj.get('turns')} turns", ""]
    lines += [ln for ln in str(obj.get("digest") or "").splitlines() if ln.strip()]
    mp = ROLLUPS / (stem + ".md")
    atomic_write_text(mp, "\n".join(lines) + "\n")
    return [jp, mp]


def _archive_dir(kind: str) -> Path:
    d = ARCHIVE / f"{kind}-{time.strftime('%Y%m%d-%H%M%S', time.localtime())}"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def consolidate_rollups(apply: bool = False, batch: int | None = None) -> dict[str, Any]:
    """Merge thin rollups into full batches, per session; superseded files are archived, not deleted.

    A build that folded on every turn left one 2-turn rollup per turn - 1,660 files in this store. The
    only reader (`build_carry`) quotes the newest CARRY_ROLLUPS of them, so the carry-over block the
    model actually sees covered ~12 earlier turns where the layout intends ~144, and the rest was
    write-only. Merging is safe because a rollup's payload is just the per-turn lines.
    """
    size = max(2, int(batch or ROLLUP_TURNS))
    with _LOCK:
        st = _load_state()
        live = str(st.get("session_id") or "")
        report: dict[str, Any] = {
            "ok": True, "apply": bool(apply), "batch": size, "sessions": [], "files_in": 0,
            "files_out": 0, "rollups_in": 0, "rollups_out": 0, "bytes_in": 0, "bytes_out": 0,
            "archived_to": None, "thin_already": [], "skipped": [],
        }
        plan: list[tuple[str, list[Path], list[dict[str, Any]], list[str]]] = []
        for sid, files in sorted(_rollup_files().items()):
            objs = [(p, _load_rollup(p)) for p in files]
            good = [(p, o) for p, o in objs if o]
            if len(good) <= 1:
                report["thin_already"].append(sid)
                continue
            bad = [str(p.name) for p, o in objs if not o]
            if bad:
                report["skipped"] += bad
            # Only THIN rollups are packed, and only into each other: a rollup that is already a
            # full batch passes through untouched, so running this twice cannot fuse the fat ones
            # into one blob (which the 1400-char carry cap would then truncate - worse than the
            # inflation it was meant to fix).
            thin = [(p, o) for p, o in good if int(o.get("turns") or 0) < size]
            if len(thin) < 2:
                report["thin_already"].append(sid)
                continue
            merged: list[dict[str, Any]] = []
            cur: list[dict[str, Any]] = []
            have = 0

            def flush() -> None:
                nonlocal cur, have
                if len(cur) > 1:
                    merged.append(_merge_rollups(sid, cur, len(merged) + 1))
                elif cur:
                    merged.append(cur[0])
                cur, have = [], 0

            for _p, o in good:
                t = int(o.get("turns") or 0)
                if t >= size:
                    flush()
                    merged.append(o)
                    continue
                if cur and have + t > size:
                    flush()
                cur.append(o)
                have += t
                if have >= size:
                    flush()
            flush()
            if len(merged) >= len(good):
                report["thin_already"].append(sid)
                continue
            plan.append((sid, [p for p, _ in good], merged, bad))

        for sid, old_files, merged, _bad in plan:
            report["files_in"] += len(old_files)
            report["rollups_in"] += len(old_files)
            report["bytes_in"] += sum(p.stat().st_size for p in old_files if p.is_file())
            report["files_out"] += len(merged) * 2
            report["rollups_out"] += len(merged)
            report["bytes_out"] += len(json.dumps(merged, ensure_ascii=False).encode("utf-8"))
            report["sessions"].append({"sid": sid, "files": len(old_files), "rollups": len(old_files),
                                       "merged": len(merged),
                                       "turns": sum(int(m.get("turns") or 0) for m in merged)})
        if not apply:
            report["note"] = "dry run: nothing written, nothing moved"
            report["needs_consolidation"] = len(plan)
            return report

        archived = _archive_dir("rollup-consolidation")
        moved: list[dict[str, Any]] = []
        report["archived_to"] = str(archived)
        for sid, old_files, merged, _bad in plan:
            fresh: list[str] = []
            for i, obj in enumerate(merged, start=1):
                stem = f"{sid}-c{i:04d}"
                paths = _write_rollup_pair(obj, stem)
                fresh.append(str(paths[0]))
            for p in old_files:
                twin = p.with_suffix(".md")
                for f in (p, twin):
                    if f.is_file():
                        try:
                            dest = archived / f.name
                            shutil.move(str(f), str(dest))
                            moved.append({"from": str(f), "to": str(dest)})
                        except (OSError, shutil.Error):
                            pass
            if sid == live:
                st["rollups"] = fresh
                st["carry"] = build_carry_locked(st)
        if moved:
            try:
                atomic_write_text(archived / "index.json",
                                  json.dumps({"moved": moved, "at": iso(), "batch": size}, indent=1))
            except OSError:
                pass
        st["consolidated_at"] = iso()
        st["consolidated_rollups"] = report["rollups_out"]
        _write_state(st)
        report["carry_chars"] = len(str(st.get("carry") or ""))
        report["carry_rollups_quoted"] = min(CARRY_ROLLUPS, report["rollups_out"])
        return report


def tidy_store(apply: bool = False) -> dict[str, Any]:
    """Move debris out of the live session store: journals that hold nothing but test turns, and
    legacy backups that have already been filed. Nothing is deleted - everything lands in save/archive
    with an index, which is the same rule the vault follows."""
    with _LOCK:
        out: dict[str, Any] = {"ok": True, "apply": bool(apply), "test_journals": [],
                               "legacy_filed": [], "kept": [], "archived_to": None, "bytes": 0}
        for path in sorted(Path(SESSIONS).glob("journal-*.jsonl")):
            sid = path.name[len("journal-"):-len(".jsonl")]
            recs = _read_lines(path)
            if not recs:
                out["kept"].append(path.name)
                continue
            turns = [r for r in recs if r.get("role") in ("user", "assistant")]
            if turns and all(_is_test_record(r) for r in turns):
                out["test_journals"].append({"name": path.name, "turns": len(turns),
                                             "bytes": path.stat().st_size})
            else:
                out["kept"].append(path.name)
        filed = set()
        try:
            import sessions as _sess  # local import: sessions imports this module

            filed = {str(r.get("sid")) for r in _sess._catalog_lines()}
        except Exception:  # noqa: BLE001
            filed = set()
        for path in sorted(Path(SESSIONS).glob("session-*.json")):
            try:
                stamp = int(float(path.stem.split("-")[-1]))
            except (ValueError, IndexError):
                continue
            if f"legacy-{stamp}" in filed:
                out["legacy_filed"].append({"name": path.name, "bytes": path.stat().st_size})
        out["bytes"] = sum(int(x.get("bytes") or 0) for x in out["test_journals"] + out["legacy_filed"])
        if not apply:
            out["note"] = "dry run: nothing moved"
            return out
        archived = _archive_dir("session-store-tidy")
        out["archived_to"] = str(archived)
        moved: list[dict[str, Any]] = []
        for item in out["test_journals"] + out["legacy_filed"]:
            src = Path(SESSIONS) / item["name"]
            for f in [src] + list(ROLLUPS.glob(src.stem.split("journal-")[-1] + "-*")):
                if f.is_file():
                    try:
                        dest = archived / f.name
                        shutil.move(str(f), str(dest))
                        moved.append({"from": str(f), "to": str(dest), "bytes": f.stat().st_size if f.exists() else 0})
                    except (OSError, shutil.Error):
                        pass
        try:
            atomic_write_text(archived / "index.json", json.dumps({"moved": moved, "at": iso()}, indent=1))
        except OSError:
            pass
        out["moved"] = len(moved)
        return out


def _is_test_record(rec: dict[str, Any]) -> bool:
    meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}
    return str(meta.get("receipt") or rec.get("receipt") or "") == "test-receipt"


def build_carry_locked(st: dict[str, Any]) -> str:
    """`build_carry()` for a caller that already holds the lock and the state."""
    rolls = _rollup_objects(st)
    sealed = st.get("sealed_carry") if isinstance(st.get("sealed_carry"), dict) else None
    if not rolls and not sealed:
        return ""
    head = "=== EARLIER CONVERSATION (compacted) ===\n"
    body: list[str] = []
    if sealed:
        head += (
            f"Session {sealed.get('sid')} ({sealed.get('turns')} turns, {sealed.get('from_iso')} → "
            f"{sealed.get('to_iso')}) was sealed into the archive. "
        )
    if rolls:
        total = sum(int(r.get("turns") or 0) for r in rolls)
        head += (
            f"{total} earlier turns of the current session ({rolls[0].get('from_iso')} → "
            f"{rolls[-1].get('to_iso')}) left this window. "
        )
    head += (
        "Everything named below was already said and is kept verbatim in the session archive. "
        "Treat it as context you already have, and call recall_history with keywords to pull any of "
        "those turns back word for word.\n"
    )
    if sealed and sealed.get("highlights"):
        body.append(f"-- sealed session {sealed.get('sid')} ({sealed.get('from_iso')} → {sealed.get('to_iso')})")
        for h in list(sealed.get("highlights") or [])[:3]:
            body.append("   " + str(h))
    for r in rolls[-CARRY_ROLLUPS:]:
        body.append(f"-- {r.get('from_iso')} → {r.get('to_iso')} ({r.get('turns')} turns)")
        for h in (r.get("highlights") or [])[:4]:
            body.append("   " + str(h))
    text = head + "\n".join(body)
    if len(text) > CARRY_CAP:
        text = text[:CARRY_CAP] + "\n…[older compacted notes trimmed]…"
    return text


def _sent_from_journal(recs: list[dict[str, Any]], budget: dict[str, Any]) -> int:
    """What the engine will actually see: the newest journal turns that fit the history room.

    `status()` is called by the panes with no messages, so the journal is all it has. Summing the
    whole journal and calling that "in the window" is what produced a standing 3394% figure.
    """
    room = int(budget.get("history_tokens") or 0)
    if not recs:
        return 0
    used = 0
    for r in reversed(recs):
        t = max(0, int(r.get("tokens") or 0))
        if used and used + t > room:
            break
        used += t
    return used or max(0, int(recs[-1].get("tokens") or 0))


def _pending_to_fold(
    st: dict[str, Any],
    recs: list[dict[str, Any]],
    keep: int | None = None,
) -> list[dict[str, Any]]:
    """Journal turns that have left the live keep window and are not yet in a rollup."""
    keep_n = max(2, int(keep if keep is not None else LIVE_KEEP_TURNS))
    frontier = max(0, len(recs) - keep_n)
    covered_upto = int(st.get("covered_upto") or 0)
    return [r for r in recs[:frontier] if int(r.get("i") or 0) > covered_upto]


def compact_is_due(
    messages: list[dict[str, Any]] | None = None,
    ctx: int | None = None,
) -> dict[str, Any]:
    """Whether this turn should fold the journal. Uses the record, not only the HTTP payload.

    The portal sends at most 60 messages. Gating auto-compact on that payload alone left a long
    journal unfolded until someone pressed the stamp button. A fold is due when anything has left
    the live keep window AND (the request is near the budget, the pending turns fill a rollup, or
    the journal itself is near the budget).
    """
    budget = window_budget(ctx)
    used_req = history_tokens(messages) if messages is not None else 0
    st = _load_state()
    recs = _read_lines(journal_path(st.get("session_id")))
    pending = _pending_to_fold(st, recs)
    journal_tokens = sum(max(0, int(r.get("tokens") or 0)) for r in recs)
    pending_tokens = sum(max(0, int(r.get("tokens") or 0)) for r in pending)
    used = max(used_req, journal_tokens)
    cap = int(budget["compact_at"])
    due = bool(pending) and (
        used_req >= cap
        or pending_tokens >= cap
        or journal_tokens >= cap
        or len(pending) >= ROLLUP_TURNS
    )
    return {
        "due": due,
        "used": used,
        "used_request": used_req,
        "journal_tokens": journal_tokens,
        "pending_turns": len(pending),
        "pending_tokens": pending_tokens,
        "compact_at": cap,
        "history_tokens": int(budget["history_tokens"]),
    }


def maybe_auto_compact(messages: list[dict[str, Any]], ctx: int | None = None) -> dict[str, Any]:
    """Compact before a turn when the live window is nearly full. Cheap, never raises."""
    try:
        decision = compact_is_due(messages, ctx)
        if not decision["due"]:
            return {
                "ok": True,
                "compacted": False,
                "used": decision["used"],
                "compact_at": decision["compact_at"],
                "pending_turns": decision["pending_turns"],
            }
        res = compact(reason="auto")
        res["compacted"] = bool(res.get("folded"))
        res["used"] = decision["used"]
        res["compact_at"] = decision["compact_at"]
        res["pending_turns"] = decision["pending_turns"]
        if res.get("compacted"):
            print(
                "[compact] auto folded=%s pending=%s used=%s compact_at=%s"
                % (res.get("folded"), decision["pending_turns"], decision["used"], decision["compact_at"]),
                flush=True,
            )
        return res
    except Exception as e:  # noqa: BLE001 - never let this cost a turn
        return {"ok": False, "compacted": False, "error": f"{type(e).__name__}: {e}"}


# ------------------------------------------------------------------------------------------------
# autosave (the "auto-save on a video game" checkpoint)
# ------------------------------------------------------------------------------------------------
def checkpoint(force: bool = False, sid: str | None = None) -> dict[str, Any]:
    """Write a readable stamped snapshot of the newest turns. Bounded work, no model call."""
    with _LOCK:
        st = _load_state()
        sid = sid or st["session_id"]
        recs = _read_lines(journal_path(sid))
        if not recs:
            return {"ok": True, "saved": False, "note": "nothing to save yet"}
        last = int(st.get("last_checkpoint_turn") or 0)
        if not force and len(recs) - last < AUTOSAVE_EVERY:
            return {"ok": True, "saved": False, "turns": len(recs), "next_at": last + AUTOSAVE_EVERY}
        idx = len(st.get("checkpoints") or []) + 1
        body = [f"# autosave {idx} · session {sid}", f"saved {iso()} · {len(recs)} turns in the journal", ""]
        body += [turn_line(r, width=400) for r in recs[-AUTOSAVE_EVERY * 2 :]]
        path = CHECKPOINTS / f"{sid}-{idx:04d}.md"
        try:
            atomic_write_text(path, "\n".join(body) + "\n")
        except OSError as e:
            return {"ok": False, "error": "checkpoint_write_failed", "detail": str(e)}
        st.setdefault("checkpoints", []).append(str(path))
        st["last_checkpoint_turn"] = len(recs)
        st["last_save"] = time.time()
        st["last_save_iso"] = iso()
        keep = [Path(p) for p in st["checkpoints"]][-CHECKPOINT_KEEP:]
        for old in [Path(p) for p in st["checkpoints"]][:-CHECKPOINT_KEEP]:
            try:
                old.unlink()
            except OSError:
                pass
        st["checkpoints"] = [str(p) for p in keep if p.is_file()]
        _write_state(st)
        return {"ok": True, "saved": True, "path": str(path), "index": idx, "turns": len(recs)}


def save_now(reason: str = "manual") -> dict[str, Any]:
    """The one button: stamp a checkpoint, fold what left the window, compact if it is nearly full."""
    out: dict[str, Any] = {"ok": True, "reason": reason, "at": iso()}
    try:
        out["checkpoint"] = checkpoint(force=True)
    except Exception as e:  # noqa: BLE001
        out["checkpoint"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    try:
        out["compact"] = compact(reason=reason)
    except Exception as e:  # noqa: BLE001
        out["compact"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    try:
        out["status"] = status()
    except Exception as e:  # noqa: BLE001
        out["status"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    out["ok"] = bool(out["checkpoint"].get("ok")) and bool(out["compact"].get("ok"))
    return out


# ------------------------------------------------------------------------------------------------
# T3 - seal, zip, index
# ------------------------------------------------------------------------------------------------
def _index_lines(limit: int | None = None) -> list[dict[str, Any]]:
    """Parsed index entries, NEWEST FIRST. Reads only the tail when a limit is given.

    The index is append-only and grows with the archive, so a status call must not parse the whole
    file every turn - on a USB stick that is a real stall.
    """
    if not INDEX.is_file():
        return []
    try:
        lines = read_text_locked(INDEX, errors="replace").splitlines()
    except OSError:
        return []
    lines = [ln for ln in lines if ln.strip()]
    if limit is not None:
        lines = lines[-max(1, int(limit)) :]
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    out.reverse()
    return out


def _index_count() -> int:
    """How many sessions are sealed - a line count, not a parse."""
    if not INDEX.is_file():
        return 0
    try:
        return sum(1 for ln in read_text_locked(INDEX, errors="replace").splitlines() if ln.strip())
    except OSError:
        return 0


def index_entries(limit: int = 200) -> list[dict[str, Any]]:
    """Every sealed session, newest first, from the append-only index."""
    return _index_lines(limit)


def _index_append(entry: dict[str, Any]) -> None:
    _dirs()
    with INDEX.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()


def _transcript(recs: list[dict[str, Any]]) -> str:
    lines = ["# LYGO conversation transcript", ""]
    for r in recs:
        lines.append(f"## [{r.get('i')}] {r.get('iso')} · {r.get('role')}")
        lines.append(content_text(r.get("content")))
        lines.append("")
    return "\n".join(lines)


def seal(reason: str = "manual", sid: str | None = None, keep_live: int | None = None) -> dict[str, Any]:
    """Zip the session, index it, then (and only then) start a fresh live journal.

    Order matters: the zip is written and verified with its entry CRCs *and* the journal's own hash
    before anything is trimmed from the live file. A failed seal leaves the live journal untouched.
    """
    with _LOCK:
        st = _load_state()
        sid = sid or st["session_id"]
        jp = journal_path(sid)
        recs = _read_lines(jp)
        if not recs:
            return {"ok": False, "error": "empty_session", "sid": sid}
        keep_live = max(2, int(keep_live if keep_live is not None else LIVE_KEEP_TURNS))
        _dirs()
        journal_sha = digest_of(jp.read_text(encoding="utf-8", errors="replace")) if jp.is_file() else ""
        roll_paths = [Path(p) for p in (st.get("rollups") or []) if Path(p).is_file()]
        manifest = {
            "signature": BUILD_TAG,
            "sid": sid,
            "sealed_iso": iso(),
            "reason": reason,
            "turns": len(recs),
            "first_i": recs[0].get("i"),
            "last_i": recs[-1].get("i"),
            "from_iso": recs[0].get("iso"),
            "to_iso": recs[-1].get("iso"),
            "chars": sum(len(content_text(r.get("content"))) for r in recs),
            "tokens": sum(int(r.get("tokens") or 0) for r in recs),
            "journal_sha256": journal_sha,
            "rollups": [p.name for p in roll_paths],
            "carried_over": min(int(keep_live), len(recs)),
            "kv_note": "verbatim journal + deterministic digests; no model-written summary is stored",
        }
        zip_path = ARCHIVE / f"{sid}.zip"
        tmp = ARCHIVE / f".{sid}.zip.part"
        try:
            with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
                z.writestr("journal.jsonl", jp.read_text(encoding="utf-8", errors="replace"))
                z.writestr("manifest.json", json.dumps(manifest, indent=1, ensure_ascii=False))
                z.writestr("conversation.md", _transcript(recs))
                for p in roll_paths:
                    try:
                        z.writestr(f"rollups/{p.name}", p.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        continue
            tmp.replace(zip_path)
        except Exception as e:  # noqa: BLE001
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            return {"ok": False, "error": "zip_failed", "detail": f"{type(e).__name__}: {e}", "sid": sid}

        # Verify before discarding anything: CRCs readable, manifest matches, journal hash matches.
        try:
            with zipfile.ZipFile(zip_path) as z:
                bad = z.testzip()
                names = set(z.namelist())
                inner_manifest = json.loads(z.read("manifest.json").decode("utf-8"))
                inner_journal = z.read("journal.jsonl").decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "zip_verify_failed", "detail": f"{type(e).__name__}: {e}", "archive": str(zip_path)}
        if bad or inner_manifest.get("journal_sha256") != digest_of(inner_journal):
            return {"ok": False, "error": "zip_hash_mismatch", "archive": str(zip_path), "entry": bad}
        if "journal.jsonl" not in names or "manifest.json" not in names:
            return {"ok": False, "error": "zip_incomplete", "archive": str(zip_path), "names": sorted(names)}

        entry = {
            **{k: manifest[k] for k in ("sid", "sealed_iso", "reason", "turns", "from_iso", "to_iso", "tokens", "chars")},
            "zip": str(zip_path),
            "zip_bytes": zip_path.stat().st_size,
            "sha256_zip": digest_of(zip_path.read_bytes()),
            "journal_sha256": inner_manifest["journal_sha256"],
            "rollups": inner_manifest["rollups"],
            "carried_over": manifest["carried_over"],
            "session_label": f"{manifest['from_iso']} → {manifest['to_iso']}",
            "note": "sealed session; recall_history searches it",
        }
        try:
            _index_append(entry)
        except OSError as e:
            return {"ok": False, "error": "index_write_failed", "detail": str(e), "archive": str(zip_path)}

        # Fresh session for the live window; the sealed turns stay in the archive only.
        carry_turns = min(int(keep_live), len(recs))
        tail = recs[-carry_turns:]
        # The sealed session stays *referenceable*: its span and a few highlights ride along as the
        # lineage of the next session, so sealing does not silently erase what the conversation was.
        st["sealed_carry"] = {
            "sid": sid,
            "turns": len(recs),
            "from_iso": manifest["from_iso"],
            "to_iso": manifest["to_iso"],
            "sealed_iso": manifest["sealed_iso"],
            "highlights": _highlights(recs[-ROLLUP_TURNS * 2 :], n=3),
        }
        new_sid = compact_id()
        st["session_id"] = new_sid
        st["session_seq"] = int(st.get("session_seq") or 0) + 1
        st["sealed_turns"] = int(st.get("sealed_turns") or 0) + len(recs)
        st["sealed_bytes"] = int(st.get("sealed_bytes") or 0) + int(entry["zip_bytes"])
        st.setdefault("rolled", []).append(
            {"sid": sid, "sealed_iso": iso(), "turns": len(recs), "zip": str(zip_path), "reason": reason}
        )
        st["checkpoints"] = []
        st["last_checkpoint_turn"] = 0
        st["rollups"] = []
        st["turns"] = len(tail)
        st["covered_upto"] = int(tail[-1].get("i") or st.get("covered_upto") or 0) if tail else 0
        body = "".join(json.dumps({**r, "carried_from": sid}, ensure_ascii=False) + "\n" for r in tail)
        try:
            atomic_write_text(journal_path(new_sid), body)
        except OSError as e:
            return {"ok": False, "error": "new_journal_failed", "detail": str(e), "archive": str(zip_path)}
        st["carry"] = build_carry_locked(st)
        _write_state(st)
        return {
            "ok": True,
            "sealed": sid,
            "archive": str(zip_path),
            "zip_bytes": entry["zip_bytes"],
            "turns": entry["turns"],
            "span": entry["session_label"],
            "new_session": new_sid,
            "carried_over": carry_turns,
            "reason": reason,
        }


def should_roll() -> dict[str, Any]:
    st = _load_state()
    recs = _read_lines(journal_path(st["session_id"]))
    size = journal_bytes(st["session_id"])
    why = ""
    if len(recs) >= JOURNAL_MAX_TURNS:
        why = f"turns {len(recs)} >= {JOURNAL_MAX_TURNS}"
    elif size >= JOURNAL_MAX_BYTES:
        why = f"bytes {size} >= {JOURNAL_MAX_BYTES}"
    return {"roll": bool(why), "why": why, "turns": len(recs), "bytes": size}


def auto_seal_if_big() -> dict[str, Any]:
    """Seal a session that has outgrown the journal caps. Never raises into a caller."""
    try:
        d = should_roll()
        if not d["roll"]:
            return {"ok": True, "sealed": False, **d}
        res = seal(reason="auto: " + d["why"])
        res["sealed_auto"] = True
        return res
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "sealed": False, "error": f"{type(e).__name__}: {e}"}


def bundle_archives(older_than_days: int = 30) -> dict[str, Any]:
    """Merge sealed zips older than N days into one monthly bundle, freeing the loose copies.

    Verified after write (each member's CRC is read back out of the bundle), and the loose zips are
    moved aside rather than deleted, so a bad bundle cannot destroy a record.
    """
    _dirs()
    cutoff = time.time() - max(1, int(older_than_days)) * 86400
    moved: list[str] = []
    by_month: dict[str, list[Path]] = {}
    for p in sorted(ARCHIVE.glob("*.zip")):
        try:
            if p.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        by_month.setdefault(time.strftime("%Y-%m", time.localtime(p.stat().st_mtime)), []).append(p)
    if not by_month:
        return {"ok": True, "bundled": 0, "note": f"no sealed session older than {older_than_days} days"}
    out: list[dict[str, Any]] = []
    for month, paths in by_month.items():
        dest = BUNDLES / f"{month}.zip"
        try:
            existing: dict[str, bytes] = {}
            if dest.is_file():
                with zipfile.ZipFile(dest) as z:
                    existing = {n: z.read(n) for n in z.namelist()}
            with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
                for n, blob in existing.items():
                    z.writestr(n, blob)
                for p in paths:
                    z.writestr(p.name, p.read_bytes())
            with zipfile.ZipFile(dest) as z:
                if z.testzip() is not None:
                    out.append({"ok": False, "bundle": str(dest), "error": "verify_failed"})
                    continue
                have = set(z.namelist())
            missing = [p.name for p in paths if p.name not in have]
            if missing:
                out.append({"ok": False, "bundle": str(dest), "error": "missing_members", "missing": missing})
                continue
            for p in paths:
                moved.append(p.name)
                try:
                    p.replace(BUNDLES / p.name)
                except OSError:
                    pass
            out.append({"ok": True, "bundle": str(dest), "members": [p.name for p in paths],
                        "bytes": dest.stat().st_size})
        except Exception as e:  # noqa: BLE001
            out.append({"ok": False, "bundle": str(dest), "error": f"{type(e).__name__}: {e}"})
    return {"ok": all(o.get("ok") for o in out), "bundled": len(moved), "moved": moved, "months": out}


# ------------------------------------------------------------------------------------------------
# recall
# ------------------------------------------------------------------------------------------------
def keywords(q: str, limit: int = 8) -> list[str]:
    words = re.findall(r"[A-Za-z0-9_]{4,}", (q or "").lower())
    seen: list[str] = []
    for w in words:
        if w in STOPWORDS or w in seen:
            continue
        seen.append(w)
        if len(seen) >= limit:
            break
    return seen


def _score(text: str, keys: list[str]) -> int:
    low = (text or "").lower()
    return sum(1 for k in keys if k in low)


def recall(q: str, k: int = RECALL_K, sid: str | None = None, archives: bool = True) -> dict[str, Any]:
    """Find the turns that talked about `q` - live journal first, then sealed sessions."""
    keys = keywords(q)
    if not keys:
        return {"ok": False, "error": "no_keywords", "q": q, "hint": "give at least one word of four letters or more"}
    hits: list[dict[str, Any]] = []

    def consider(rec: dict[str, Any], source: str) -> None:
        text = content_text(rec.get("content"))
        sc = _score(text, keys)
        if sc <= 0:
            return
        hits.append(
            {
                "score": sc,
                "source": source,
                "i": rec.get("i"),
                "iso": rec.get("iso"),
                "role": rec.get("role"),
                "ts": rec.get("ts") or 0,
                "chars": len(text),
                "snippet": text[:RECALL_SNIPPET] + ("…" if len(text) > RECALL_SNIPPET else ""),
            }
        )

    scanned_live = 0
    for rec in read_journal(sid):
        scanned_live += 1
        consider(rec, "live")
    scanned_arch = 0
    if archives:
        for entry in index_entries(limit=RECALL_ARCHIVES):
            zp = Path(str(entry.get("zip") or ""))
            if not zp.is_file():
                continue
            try:
                with zipfile.ZipFile(zp) as z:
                    if "journal.jsonl" not in z.namelist():
                        continue
                    blob = z.read("journal.jsonl")
                    if len(blob) > RECALL_SCAN_BYTES:
                        blob = blob[-RECALL_SCAN_BYTES:]
            except (OSError, zipfile.BadZipFile):
                continue
            for line in blob.decode("utf-8", "replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict) or not rec.get("role"):
                    continue
                scanned_arch += 1
                consider(rec, str(entry.get("sid") or zp.name))
    hits.sort(key=lambda h: (h["score"], h.get("ts") or 0), reverse=True)
    top = hits[: max(1, int(k))]
    return {
        "ok": True,
        "q": q,
        "keywords": keys,
        "hits": top,
        "n_hits": len(hits),
        "scanned": {"live": scanned_live, "archived": scanned_arch},
    }


def recall_text(q: str, k: int = 3) -> str:
    """`recall` as a block an operator or the model can read."""
    res = recall(q, k=k)
    if not res.get("ok"):
        return f"recall: {res.get('error')} — {res.get('hint')}"
    if not res.get("hits"):
        return (
            f"recall: nothing in the saved conversation matches {res.get('keywords')} "
            f"({res['scanned']['live']} live turns, {res['scanned']['archived']} archived turns searched)"
        )
    lines = [f"recall {res['keywords']} — {res['n_hits']} matching turn(s), newest first:"]
    for h in res["hits"]:
        lines.append(f"[{h['iso']} · {h['role']} · {h['source']} · score {h['score']}] {h['snippet']}")
    return "\n".join(lines)


def read_transcript(sid: str) -> dict[str, Any]:
    """The full transcript of a SEALED session, out of its archive (read-only)."""
    zp = ARCHIVE / f"{sid}.zip"
    if not zp.is_file():
        hits = [e for e in index_entries(limit=500) if str(e.get("sid")) == sid]
        if not hits:
            return {"ok": False, "error": "not_found", "sid": sid}
        zp = Path(str(hits[0].get("zip") or ""))
    if not zp.is_file():
        return {"ok": False, "error": "archive_missing", "sid": sid, "zip": str(zp)}
    try:
        with zipfile.ZipFile(zp) as z:
            names = set(z.namelist())
            text = z.read("conversation.md").decode("utf-8", "replace") if "conversation.md" in names else ""
            manifest = json.loads(z.read("manifest.json").decode("utf-8")) if "manifest.json" in names else {}
    except (OSError, zipfile.BadZipFile) as e:
        return {"ok": False, "error": "read_failed", "detail": str(e), "zip": str(zp)}
    return {"ok": True, "sid": sid, "zip": str(zp), "manifest": manifest, "text": text, "chars": len(text)}


# ------------------------------------------------------------------------------------------------
# status
# ------------------------------------------------------------------------------------------------
def status(ctx: int | None = None, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Everything the UI and a post-mortem need, in one call. Never raises."""
    try:
        st = _load_state()
        sid = st["session_id"]
        recs = _read_lines(journal_path(sid))
        budget = window_budget(ctx)
        journal_tokens = sum(int(r.get("tokens") or 0) for r in recs)
        live_tokens = history_tokens(messages) if messages is not None else _sent_from_journal(recs, budget)
        entries = index_entries(limit=5)
        sealed_bytes = int(st.get("sealed_bytes") or 0)
        pct = round(100.0 * live_tokens / max(1, budget["history_tokens"]), 1)
        rec = _selected_record() or {}
        try:
            import lygo_rag as _rag  # deferred: the RAG reads the archive; compaction must not need it

            _rag_stats: dict[str, Any] = _rag.status()
        except Exception:
            _rag_stats = {}
        return {
            "ok": True,
            "signature": BUILD_TAG,
            "session_id": sid,
            "session_iso": st.get("created_iso"),
            "turns_live": len(recs),
            "turns_total": int(st.get("turns") or 0),
            "turns_compacted": int(st.get("carry_turns") or 0),
            "turns_sealed": int(st.get("sealed_turns") or 0),
            "journal_bytes": journal_bytes(sid),
            "covered_upto": int(st.get("covered_upto") or 0),
            "rollups": len(st.get("rollups") or []),
            "compactions": int(st.get("compactions") or 0),
            "checkpoints": len(st.get("checkpoints") or []),
            "sessions_sealed": _index_count(),
            "sealed_bytes": sealed_bytes,
            "archive_bytes": int(sealed_bytes) + int(journal_bytes(sid)),
            "last_save_iso": st.get("last_save_iso"),
            "last_compact_iso": st.get("last_compact_iso"),
            "carry_span": [st.get("carry_from"), st.get("carry_to")],
            "rag": _rag_stats,
            "recall": _rag_stats.get("recall") or {},
            "window": {
                **budget,
                "live_tokens": live_tokens,
                # The record and the request are two different things. The engine only ever sees the
                # newest turns that fit the room; everything older stays filed and searchable.
                # Reporting the record as "in the window" produced a standing 3394.2% that no
                # operator could act on, and kept promising a fold that was not due.
                "journal_tokens": journal_tokens,
                "journal_turns": len(recs),
                "journal_pct": round(100.0 * journal_tokens / max(1, budget["history_tokens"]), 1),
                "used_pct": pct,
                "auto_compact_pct": int(AUTO_COMPACT_AT * 100),
                "will_compact_next_turn": bool(
                    compact_is_due(messages, ctx).get("due")
                ),
                "model": rec.get("id"),
                "model_ctx_native": rec.get("ctx"),
                "kv_mib_estimate": _kv_mib(rec, budget["ctx"]),
            },
            "paths": {
                "journal": str(journal_path(sid)),
                "rollups": str(ROLLUPS),
                "checkpoints": str(CHECKPOINTS),
                "archive": str(ARCHIVE),
                "index": str(INDEX),
            },
            "carry_ready": bool(st.get("carry")),
            "recent_sessions": entries[:5],
            "roll_due": should_roll(),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _kv_mib(rec: dict[str, Any], ctx: int) -> int | None:
    try:
        import gguf_header
        import perf

        p = Path(str(rec.get("path") or ""))
        if not p.is_file():
            return None
        dims = (gguf_header.parse_gguf_header(p) or {}).get("found") or {}
        from paths import console_limits

        kv_type = perf.sanitize_kv_type(console_limits().get("kv_type"))
        return int(perf.kv_cache_mib(perf.kv_bytes_per_token(dims), ctx, kv_type))
    except Exception:  # noqa: BLE001
        return None


def _dir_bytes(d: Path) -> int:
    total = 0
    try:
        for p in d.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


# ------------------------------------------------------------------------------------------------
# safe wrappers for the request path (nothing here may raise into a handler)
# ------------------------------------------------------------------------------------------------
def safe_record(role: str, content: Any, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return record(role, content, meta)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def safe_pre_turn(messages: list[dict[str, Any]], ctx: int | None = None) -> dict[str, Any]:
    """Run before a turn: checkpoint on its own cadence, compact if the window is nearly full."""
    out: dict[str, Any] = {"ok": True}
    try:
        out["autosave"] = checkpoint()
    except Exception as e:  # noqa: BLE001
        out["autosave"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    out["auto"] = maybe_auto_compact(messages, ctx)
    try:
        out["seal"] = auto_seal_if_big()
    except Exception as e:  # noqa: BLE001
        out["seal"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return out


def safe_status(ctx: int | None = None, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    try:
        return status(ctx, messages)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
