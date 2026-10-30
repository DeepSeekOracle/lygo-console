"""Forever history: every conversation the console has, filed on disk, nothing asked of the model.

The operator's words: "I want Conversations to flow unlimited ... CREATE A simple system that SAVES
the whole conversation window, all text into a folder inside the console ... this will be the forever
history of full conversations ... a program that doesnt bother the AI ... triggered after the COMPLETE
response ... so we dont duplicate saves we can simply save responses from AI and sent text from the
user ... if the AI needs to see previous history its all there".

How it holds those promises
---------------------------
* **Off the hot path.** `sync()` only decides *what* is new and hands the text to a background writer
  thread. A slow, full or un-writable disk costs the turn nothing, and no failure here can raise into
  an answer - a broken archive must never break the console.
* **Never twice.** The console re-persists its whole session every turn, so filing cannot be driven by
  "on save" - and it cannot be driven by counting either, because the console TRIMS an over-long
  conversation before sending it and the window slides. A message is recognised by what it says, not by
  where it sits, and what is already filed is remembered from the blocks in the file itself, so a
  restart, a crash, a replayed turn or a slid window still files each message exactly once.
* **The sent text when it is sent; the answer when it is complete.** A payload that ends with the
  steward's message files that message. The agent's text is only ever filed when it arrives as part of
  a completed turn - a half-streamed answer is not history.
* **Verbatim.** No truncation, no summarising, no stripping: what was said is what is kept, newlines
  and fences intact.
* **Labelled.** Every block names who spoke, the turn number, whether it was sent or complete, and the
  model that answered. A `INDEX.md` carries one grep-able line per block so the agent can find a past
  conversation without loading any of them.

Layout:  <workspace>/memory/conversations/<YYYY-MM-DD>/<HHMMSS>-lygo-<session8>.md   (+ INDEX.md)
Reader:  the files are plain markdown under the workspace, so the agent's existing read_file /
         list_dir / search_corpus limbs already read them. `scripts/read_history.py` is a convenience
         front end. `src/lygo_rag.py` searches the same history by meaning-of-words.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any

SIGNATURE = "Δ9Φ963-LYGO-CONVERSATION-v1"
LICENSE = "LYGO Sovereign License v3.0"
STEWARD = "LIGHTFATHER"
LABEL_USER = "STEWARD"
LABEL_AGENT = "LYGO"

try:  # the console's own paths module; tolerated so this file can be imported stand-alone
    from paths import WORKSPACE as _WORKSPACE
except Exception:  # pragma: no cover - only when imported outside the kit
    _WORKSPACE = Path(os.environ.get("LYGO_WORKSPACE") or (Path(__file__).resolve().parent.parent / "workspace"))

ROOT: Path = _WORKSPACE / "memory" / "conversations"
STATE_NAME = ".archive_state.json"
INDEX_NAME = "INDEX.md"

_lock = threading.Lock()
_state: dict[str, Any] = {}
_q: "queue.Queue[tuple[Path, str] | None]" = queue.Queue()
_thread: threading.Thread | None = None
_logged: set[str] = set()


def _say(msg: str) -> None:
    """One honest line, at most once per distinct problem, to stderr only. Never raises."""
    if msg in _logged:
        return
    _logged.add(msg)
    try:
        print(f"[archive] {msg}", flush=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# the background writer
# ---------------------------------------------------------------------------

def _writer() -> threading.Thread | None:
    """The thread that owns the disk. Started on the first message, never shut down by a turn."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            t = threading.Thread(target=_drain, name="lygo-conversation-archive", daemon=True)
            t.start()
            _thread = t
    return _thread


def _drain() -> None:
    while True:
        item = _q.get()
        try:
            if item is None:
                return
            path, text = item
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
                    fh.flush()
            except Exception as exc:  # a disk problem is reported once and never carried into a turn
                _say(f"could not file a message to {path}: {exc!r}")
        finally:
            _q.task_done()


def flush(timeout: float = 5.0) -> bool:
    """Wait for the writer to catch up. Used by tests and by shutdown - never by a turn."""
    _writer()
    deadline = time.time() + max(0.0, timeout)
    while time.time() < deadline:
        if _q.unfinished_tasks == 0:
            return True
        time.sleep(0.01)
    return _q.unfinished_tasks == 0


def reset_for_tests() -> None:
    """Drop in-memory state so a test can point ROOT somewhere new."""
    global _thread
    with _lock:
        _state.clear()
        _logged.clear()
        _q = queue.Queue()  # type: ignore[assignment]
        _thread = None


# ---------------------------------------------------------------------------
# what a message is, and how it is written
# ---------------------------------------------------------------------------

def text_of(content: Any) -> str:
    """Everything a message says, as text. Parts, dicts, lists - flattened, never dropped."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in ("text", "content", "value"):
            if key in content:
                return text_of(content[key])
        return json.dumps(content, ensure_ascii=False)
    if isinstance(content, (list, tuple)):
        return "".join(text_of(part) for part in content)
    return str(content)


def _stamp(role: str, turn: int, meta: dict[str, Any]) -> str:
    label = LABEL_USER if role == "user" else LABEL_AGENT
    state = "sent" if role == "user" else "complete"
    when = time.strftime("%Y-%m-%d %H:%M:%S")
    bits = [when, f"turn {turn}", f"{state} by {label}"]
    if meta.get("model"):
        bits.append(str(meta["model"]))
    if meta.get("brain"):
        bits.append(str(meta["brain"]))
    if meta.get("provider") and str(meta.get("brain") or "").lower() in ("cloud", "api"):
        # The chain hands a turn on when a provider refuses it, so the configured primary is not
        # necessarily who answered. Stamped the turn nvidia/nemotron… while DeepSeek had in fact
        # served it: the label has to name the provider that produced the text.
        #
        # And only when a cloud brain produced it. Measured on the stick 2026-09-21: a turn the local
        # 1.5B model answered was filed "qwen2.5:1.5b · ready · answered by deepseek", crediting a cloud
        # provider for a local answer. An absent brain, or a mere status ("ready"), credits nobody.
        bits.append(f"answered by {meta['provider']}")
    return "## " + " · ".join(bits) + "\n"


def _header(path: Path, meta: dict[str, Any]) -> str:
    return (
        f"# LYGO conversation · started {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"{SIGNATURE} · license {LICENSE} · steward {STEWARD}\n"
        f"Filed automatically by the console. Every message below is verbatim and permanent:\n"
        f"the sent text as it was sent, the agent's text as it was completed.\n"
        f"file: {path.name}\n\n"
    )


def _one_line(s: str, n: int = 110) -> str:
    return re.sub(r"\s+", " ", s).strip()[:n]


# ---------------------------------------------------------------------------
# state / identity of the current conversation
# ---------------------------------------------------------------------------

def _fingerprint(messages: list[dict[str, Any]]) -> str:
    first = ""
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            first = text_of(m.get("content"))
            break
    return hashlib.sha1(first.encode("utf-8", "replace")).hexdigest()


def _state_path() -> Path:
    return ROOT / STATE_NAME


def _load_state() -> dict[str, Any]:
    if _state:
        return _state
    try:
        loaded = json.loads(_state_path().read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            _state.update(loaded)
    except Exception:
        pass
    return _state


def _save_state() -> None:
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        tmp = _state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(_state, indent=2), encoding="utf-8")
        os.replace(tmp, _state_path())
    except Exception as exc:
        _say(f"could not remember where history is up to ({exc!r}); the blocks already in the file still count")


def _blocks_in(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                   if line.startswith("## "))
    except Exception:
        return 0


def _key(m: dict[str, Any]) -> str:
    """What a message IS, so it can be recognised again after the window has slid.

    Content, not position: the console trims an over-long conversation before sending it, so a message
    that was the fifth of twelve is the first of six a turn later. The same words said twice in one
    conversation are one record - which is what "so we dont duplicate saves" asks for.
    """
    role = str(m.get("role") or "")
    body = text_of(m.get("content"))
    return role + ":" + hashlib.sha1(body.encode("utf-8", "replace")).hexdigest()[:20]


def _seen_from_file(path: Path) -> set[str]:
    """Rebuild the record of what is filed by reading the file. Used when the state file is gone."""
    seen: set[str] = set()
    role = ""
    body: list[str] = []

    def flush() -> None:
        if role:
            seen.add(_key({"role": role, "content": "\n".join(body).rstrip("\n")}))

    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("## "):
                flush()
                head = line[3:].upper()
                role = "user" if "STEWARD" in head else "assistant"
                body = []
            elif role:
                body.append(line)
        flush()
    except Exception as exc:
        _say(f"could not read back {path.name} ({exc!r}); a restart may re-file some messages")
    return seen


def _file_for(fingerprint: str, fresh: bool = False) -> Path:
    day = time.strftime("%Y-%m-%d")
    if not fresh and _state.get("fingerprint") == fingerprint and _state.get("file"):
        return ROOT / str(_state["file"])
    name = f"{time.strftime('%H%M%S')}-lygo-{fingerprint[:8]}.md"
    return ROOT / day / name


# ---------------------------------------------------------------------------
# the one entry point the console calls
# ---------------------------------------------------------------------------

def sync(messages: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """File every message of this conversation that is not filed yet. Returns what happened.

    Called twice per turn - once when the steward's text arrives, once when the answer is complete -
    and safe to call as often as the console likes: the cursor is the truth, not the caller.
    """
    meta = dict(meta or {})
    out: dict[str, Any] = {"filed": 0, "file": "", "cursor": 0}
    try:
        messages = [m for m in (messages or []) if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
        if not messages:
            return out
        st = _load_state()
        seen: set[str] = set(st.get("seen") or [])
        fp = _fingerprint(messages)
        keys = [_key(m) for m in messages]
        path = ROOT / str(st.get("file") or "") if st.get("file") else _file_for(fp)

        # A restart can lose the state file while the history stays on disk. The blocks themselves say
        # who spoke and what was said, so the record of what is filed is rebuilt from the file rather
        # than trusted to a sidecar that may be gone.
        if path.is_file() and not seen:
            seen = _seen_from_file(path)

        # A NEW conversation is one that shares nothing with what is already filed. Comparing the first
        # message would be wrong: the console trims an over-long conversation before sending it, so the
        # window slides and its first message changes while the conversation is the very same one.
        if path.is_file() and seen and not (set(keys) & seen):
            st.clear()
            path = _file_for(fp, fresh=True)
            seen = set()

        if set(keys) <= seen and path.is_file():
            out.update({"file": str(path), "cursor": len(seen)})
            return out

        buf: list[str] = []
        if not path.is_file():
            buf.append(_header(path, meta))
        idx_lines: list[str] = []
        # A turn is the steward's message and everything said in reply to it. Counting messages
        # would mislabel an answer whenever a turn carried more than one steward message (an
        # attachment ride-along, a correction), so the counter follows who is speaking.
        turn = 0
        for m, key in zip(messages, keys):
            role = str(m.get("role"))
            text = text_of(m.get("content"))
            if role == "user":
                turn += 1
            if key in seen:
                continue  # filed already - the console re-sends history every turn
            seen.add(key)
            buf.append(_stamp(role, max(turn, 1), meta))
            buf.append(text if (text.endswith("\n") or not text) else text + "\n")
            buf.append("\n")
            label = LABEL_USER if role == "user" else LABEL_AGENT
            idx_lines.append(f"- {time.strftime('%Y-%m-%d %H:%M:%S')} · {label} · turn {turn} · "
                             f"{_one_line(text)}  ({path.name})")
            out["filed"] += 1

        if out["filed"] == 0:
            out.update({"file": str(path), "cursor": len(seen)})
            return out
        st["seen"] = list(seen)[-4000:]
        st["file"] = str(path.relative_to(ROOT))
        st["fingerprint"] = fp
        _save_state()

        _writer()
        _q.put((path, "".join(buf)))
        idx = index_path()
        _q.put((idx, (("" if idx.is_file() else f"# {SIGNATURE} · conversation index\n\n"
                                            f"One line per filed message. Grep this to find a past "
                                            f"conversation without opening any of them.\n\n")
                      + "\n".join(idx_lines) + "\n")))
        # `seen` is the cursor: how many messages of this conversation are filed. The older
        # positional design kept a separate `cursor` key, so a state file written back then has no
        # such key - reading it raised KeyError('cursor') on every turn (measured 2026-09-21 in the
        # console log) and the return value, not the filing, was what broke. Report the truth.
        out.update({"file": str(path), "cursor": len(seen)})
    except Exception as exc:  # pragma: no cover - defensive: the turn must not care
        _say(f"sync failed ({exc!r}); the conversation carries on")
    return out


# ---------------------------------------------------------------------------
# reading it back
# ---------------------------------------------------------------------------

def index_path() -> Path:
    return ROOT / INDEX_NAME


def files() -> list[Path]:
    """Every filed conversation, oldest first."""
    try:
        return sorted(p for p in ROOT.rglob("*.md") if p.name != INDEX_NAME)
    except Exception:
        return []


def search(needle: str, limit: int = 20) -> list[dict[str, Any]]:
    """Plain substring search over the filed history, newest last. No model, no index to rot."""
    needle = str(needle or "")
    if not needle:
        return []
    low = needle.lower()
    hits: list[dict[str, Any]] = []
    for p in files():
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for n, line in enumerate(lines, 1):
            if low in line.lower():
                hits.append({"file": str(p), "line": n, "text": line.strip()})
                if len(hits) >= limit:
                    return hits
    return hits


def status() -> dict[str, Any]:
    """A small readout for the console's health pane - cheap enough to call at any time."""
    fs = files()
    total = 0
    for p in fs:
        try:
            total += p.stat().st_size
        except Exception:
            pass
    cur = _load_state()  # a fresh process has filed nothing yet, but the folder may be full
    return {
        "signature": SIGNATURE,
        "root": str(ROOT),
        "conversations": len(fs),
        "bytes": total,
        "queued": _q.unfinished_tasks,
        "current_file": str(cur.get("file") or ""),
        "filed": len(cur.get("seen") or []),
        "index": str(index_path()) if index_path().is_file() else "",
    }
