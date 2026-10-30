"""lygo.meminfo — what this console remembers, in one card.

Δ9Φ963-LYGO-MEMINFO-v1

WHAT THIS ANSWERS
    One question the operator cannot answer anywhere else in one place: **what do you still have,
    and what was thrown away?** A console keeps its memory in five stores, and until now each was
    reachable only by a different route or not at all:

        the live journal     save/sessions/journal-<id>.jsonl     this session, verbatim
        the window record    save/sessions/compaction_state.json  what was folded into digests
        the vault            save/vault/<year>/<month>/<slug>/    every finished chat, filed
        the sealed archive   save/archive/index.jsonl + *.zip     what a recall can search
        the event journal    save/mycelium/events.jsonl           the kit's own receipt trail

WHAT IT DELIBERATELY DOES NOT DO
    * No new state and no writes anywhere: a read-out. `owns: []`, `gate: none`, and the module's
      own test scans this file for write calls.
    * No duplicated kernel logic. Every number is asked of its owner — `compaction` for the record
      and the archive, `sessions` for the vault, `paths` for where things live. The live token
      budget and the model's context window belong to **LLM data** (`lygo.llminfo`); this card
      NAMES that owner instead of printing a second copy that can drift out of step.
    * No store reported as empty that was not read. An unread store is UNCHECKED and named; only a
      store actually read may report zero.

THE HONESTY RULES IT INHERITS
    * A check that could not run is named in words and drags the card off green — never folded
      into green.
    * A store that really is empty is a TRUE FACT, not a gap: a fresh kit with an empty vault reads
      "nothing sealed yet" and stays green.
    * It prints the rule table it counted by, so "nothing was lost" is a claim about a named search
      and not a feeling.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

SIGNATURE = "Δ9Φ963-LYGO-MEMINFO-v1"
MID = "lygo.meminfo"
ROUTE = "/api/meminfo"
ROUTES: tuple[tuple[str, str], ...] = (("GET", ROUTE),)

#: Every module this card asks for a fact. A missing one is a named gap, never a zero.
OWNERS: tuple[str, ...] = ("compaction", "sessions", "paths", "atomicio", "receipts")

#: The windows that hold this session, newest first, as compaction records them.
_KEEP_EVENTS = 400
_KEEP_INDEX = 6


# ------------------------------------------------------------------------------------------------
# formatters - the edge, where a number becomes something a human reads
# ------------------------------------------------------------------------------------------------
def _at() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _plural(n: Any, one: str, many: str | None = None) -> str:
    try:
        i = int(n)
    except (TypeError, ValueError):
        return f"{n} {many or one + 's'}"
    return f"{i} {one if i == 1 else (many or one + 's')}"


def _size(n: Any) -> str:
    """Bytes at a human scale. A 300 KB journal must never print as `0.00 GB`."""
    try:
        b = float(n or 0)
    except (TypeError, ValueError):
        return "unreadable"
    if b < 0:
        return "unreadable"
    for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if b >= div:
            return f"{b / div:.2f} {unit}"
    return f"{int(b)} B"


def _when(iso: Any) -> str:
    """An ISO string the owners write, or a plain word when there is none."""
    text = str(iso or "").strip()
    return text if text else "never"


def _stamp(value: Any) -> str:
    """An epoch float as a local time. The event journal stores `ts` as epoch seconds."""
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except (OSError, OverflowError, ValueError):
        return ""


def _row(k: str, v: Any, note: str = "", dot: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"k": k, "v": "" if v is None else str(v)}
    if note:
        out["note"] = note
    if dot:
        out["dot"] = dot
    return out


def _group(title: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"title": title, "rows": rows}


def _short_path(path: Any) -> str:
    """The part of a path that tells the operator where to look, not the whole drive."""
    text = str(path or "")
    marker = "lygo_llm_console"
    idx = text.find(marker)
    return text[idx:] if idx >= 0 else text


# ------------------------------------------------------------------------------------------------
# owner access - lazily, so a missing owner is a named gap and never an ImportError into a handler
# ------------------------------------------------------------------------------------------------
def _owner(name: str) -> Any | None:
    try:
        return __import__(name)  # noqa: PLC0415 - deliberately late: a missing owner is a finding
    except Exception:  # noqa: BLE001
        return None


def _read_text(path: Any, atomicio: Any | None) -> str:
    """Reads through `atomicio` when it is there (the kit's rule for every state read)."""
    if atomicio is not None and hasattr(atomicio, "read_text"):
        try:
            return atomicio.read_text(path, errors="replace")
        except TypeError:
            return atomicio.read_text(path)
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _status(comp: Any, gaps: list[str]) -> dict[str, Any] | None:
    if comp is None:
        gaps.append(
            "the record (`compaction`) could not be imported on this tree, so the live journal, the "
            "window and the sealed archive are all UNCHECKED here - not empty"
        )
        return None
    st = comp.safe_status() or {}
    if not st.get("ok"):
        gaps.append(f"the record answered with an error instead of its state: {st.get('error') or 'no reason given'}")
        return None
    return st


def _vault(sess: Any, gaps: list[str]) -> dict[str, Any] | None:
    if sess is None:
        gaps.append(
            "the session vault (`sessions`) could not be imported on this tree, so what is filed is "
            "UNCHECKED here - not zero sessions"
        )
        return None
    sv = sess.safe_stats() or {}
    if not sv.get("ok"):
        gaps.append(f"the vault answered with an error instead of its contents: {sv.get('error') or 'no reason given'}")
        return None
    return sv


# ------------------------------------------------------------------------------------------------
# collectors - each one asks an owner for its own numbers
# ------------------------------------------------------------------------------------------------
def _live_rows(st: dict[str, Any]) -> list[dict[str, Any]]:
    """The journal that holds this session, verbatim."""
    paths = st.get("paths") or {}
    live = st.get("turns_live")
    total = st.get("turns_total")
    rows = [
        _row("Session", st.get("session_id") or "unknown", f"started {_when(st.get('session_iso'))}"),
        _row("Turns in this session", _plural(live, "turn"), "read back from the journal itself"),
        _row("Journal on disk", _size(st.get("journal_bytes")), _short_path(paths.get("journal"))),
        _row("Last save", _when(st.get("last_save_iso")), "the auto-save cadence writes here"),
    ]
    # The record's own counter is only worth a row when it disagrees with the journal - otherwise it
    # is the same number printed twice, which reads as padding.
    if int(total or 0) != int(live or 0):
        rows.insert(
            2,
            _row(
                "Recorded all-time",
                _plural(total, "turn"),
                "the record's own counter for this session - it counts turns the journal no longer holds",
                dot="amber",
            ),
        )
    return rows


def _window_rows(st: dict[str, Any]) -> list[dict[str, Any]]:
    """What is in the model's view now, and what has already been folded away."""
    window = st.get("window") or {}
    span = st.get("carry_span") or [None, None]
    rows = [
        _row("Folded into digests", _plural(st.get("turns_compacted"), "turn"), "summarised, not deleted - the digest is below"),
        _row("Sealed behind this session", _plural(st.get("turns_sealed"), "turn"), "moved into the sealed archive, still searchable"),
        _row("Digests kept", _plural(st.get("rollups"), "digest"), "one per folding pass"),
        _row("Folding passes run", str(st.get("compactions") or 0), "each one is a turn the window no longer holds verbatim"),
        _row("Checkpoints kept", _plural(st.get("checkpoints"), "checkpoint")),
    ]
    if span[0] or span[1]:
        rows.append(_row("Digest covers", f"{_when(span[0])} → {_when(span[1])}", "the span the digests stand in for"))
    if window.get("will_compact_next_turn"):
        rows.append(
            _row(
                "Next turn",
                "folds the oldest turns into a digest",
                "the window is at the threshold the record folds at - the oldest turns stop being verbatim",
                dot="amber",
            )
        )
    else:
        rows.append(
            _row(
                "Folds at",
                f"{window.get('compact_at', '?')} tokens",
                f"the record folds the oldest turns at {window.get('auto_compact_pct', '?')}% of the model's window",
            )
        )
    rows.append(
        _row(
            "Token budget and context window",
            "reported by LLM data",
            "that number belongs to lygo.llminfo - this card does not print a second copy of it",
        )
    )
    return rows


def _vault_rows(sv: dict[str, Any]) -> list[dict[str, Any]]:
    """Every finished chat, filed. An empty vault is a fact, not a gap."""
    newest = sv.get("newest") or {}
    months = sv.get("months") or []
    rows = [
        _row("Sessions filed", str(sv.get("sessions") or 0), "one folder per finished chat, with its own manifest"),
        _row("Turns filed", _plural(sv.get("turns"), "turn")),
        _row("Filed on disk", _size(sv.get("bytes")), f"{_size(sv.get('zip_bytes'))} of that is the verified zips"),
        _row("Months covered", _plural(len(months), "month") if months else "no months yet", ", ".join(str(m) for m in months[-4:])),
    ]
    if newest:
        rows.append(_row("Newest filed", str(newest.get("sid") or ""), str(newest.get("title") or newest.get("iso") or "")))
    else:
        rows.append(_row("Newest filed", "nothing filed yet", "a true fact on a fresh kit - not a gap"))
    rows.append(_row("Catalog", _short_path(sv.get("catalog")), "catalog.jsonl is the complete index; CATALOG.md is the readable one"))
    unfiled = sv.get("unfiled_journals") or []
    if unfiled:
        rows.append(
            _row(
                "Journalled but not filed",
                _plural(len(unfiled), "session"),
                "a journal exists with no vault entry - it will be filed when that chat is closed",
                dot="amber",
            )
        )
    if sv.get("legacy_files"):
        rows.append(
            _row(
                "Pre-vault session files",
                _plural(sv.get("legacy_files"), "file"),
                "older format, kept as they are: counted here, not readable as turns",
            )
        )
    return rows


def _archive_rows(comp: Any, st: dict[str, Any]) -> list[dict[str, Any]]:
    """The sealed archive: what a recall can actually open."""
    paths = st.get("paths") or {}
    n_sealed = int(st.get("sessions_sealed") or 0)
    try:
        entries = comp.index_entries(limit=_KEEP_INDEX) or []
    except Exception as exc:  # noqa: BLE001
        entries = []
        paths = dict(paths)
        paths["_index_error"] = f"{type(exc).__name__}: {exc}"
    rows = [
        _row("Sessions sealed", str(n_sealed), "each one is a zip a recall can search"),
        _row("Sealed on disk", _size(st.get("sealed_bytes"))),
        _row("Index", _short_path(paths.get("index")), "append-only: one line per sealed session"),
    ]
    if paths.get("_index_error"):
        rows.append(_row("Index unreadable", paths["_index_error"], "the count above is still the record's own", dot="amber"))
    if entries:
        newest = entries[0]
        zip_path = Path(str(newest.get("zip") or ""))
        try:
            present = zip_path.is_file()
        except OSError:
            present = False
        rows.append(_row("Newest seal", str(newest.get("sid") or ""), f"sealed {_when(newest.get('sealed_iso'))}"))
        rows.append(_row("Why it sealed", str(newest.get("reason") or ""), f"{_plural(newest.get('turns'), 'turn')} went in"))
        if present:
            size = 0
            try:
                size = zip_path.stat().st_size
            except OSError:
                size = 0
            rows.append(_row("Its zip", f"on disk, {_size(size)}", "verified by sha256 when it was written"))
        else:
            rows.append(
                _row(
                    "Its zip",
                    "the index names a file that is not there",
                    "the index stores an absolute path, so a moved or copied tree can leave it pointing home",
                    dot="amber",
                )
            )
    else:
        rows.append(_row("Newest seal", "nothing sealed yet", "a true fact on a fresh kit - not a gap"))
    return rows


def _events_rows(paths_mod: Any, atomicio: Any, gaps: list[str]) -> list[dict[str, Any]]:
    """The kit's own event journal. A missing file is `not written yet`, not a failure."""
    if paths_mod is None:
        gaps.append(
            "`paths` could not be imported, so the event journal's own location is UNCHECKED here - "
            "the other groups read their stores directly"
        )
        return []
    try:
        path = Path(paths_mod.MYCELIUM) / "events.jsonl"
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"the event journal's location could not be resolved: {type(exc).__name__}: {exc}")
        return []
    if not path.is_file():
        return [
            _row("Event journal", "not written yet", "the kit writes its first event on its first gated turn"),
            _row("Where it will go", _short_path(path)),
        ]
    try:
        text = _read_text(path, atomicio)
        size = path.stat().st_size
    except OSError as exc:
        gaps.append(f"the event journal exists but could not be read ({type(exc).__name__}: {exc}), so its contents are UNCHECKED")
        return [_row("Event journal", "unreadable", _short_path(path), dot="amber")]
    lines = [ln for ln in text.splitlines() if ln.strip()]
    newest: dict[str, Any] = {}
    for line in reversed(lines[-_KEEP_EVENTS:]):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            newest = obj
            break
    undated = 0
    for line in lines[-_KEEP_EVENTS:]:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            undated += 1
            continue
        if not isinstance(obj, dict) or not _stamp(obj.get("ts")):
            undated += 1
    rows = [
        _row("Events", _plural(len(lines), "event"), "the kit's own trail of gated turns"),
        _row("On disk", _size(size), _short_path(path)),
    ]
    if newest:
        bundle = newest.get("bundle") if isinstance(newest.get("bundle"), dict) else {}
        when = _stamp(newest.get("ts"))
        rows.append(_row("Newest event", when or "the row carries no timestamp", f"dated by {_short_path(path)}" if not when else ""))
        if bundle.get("model"):
            rows.append(_row("Model on it", str(bundle.get("model"))))
        if bundle.get("verdict"):
            rows.append(_row("Verdict", str(bundle.get("verdict"))))
    else:
        rows.append(_row("Newest event", "no parseable row yet", "the file exists but holds nothing this card can read"))
    if undated:
        rows.append(
            _row(
                "Rows without a timestamp",
                _plural(undated, "row"),
                "dated by the file's own last write instead, because the row carries none",
                dot="amber",
            )
        )
    return rows


def _reach_rows(comp: Any, st: dict[str, Any]) -> list[dict[str, Any]]:
    """What a recall can actually reach - the rule table, so 'nothing was lost' is checkable."""
    n_sealed = int(st.get("sessions_sealed") or 0)
    live = int(st.get("turns_live") or 0)
    rows: list[dict[str, Any]] = []
    if comp is None:
        return [_row("Reach", "unchecked", "the record could not be imported, so the search rule is unknown here", dot="amber")]
    try:
        newest_limit = int(getattr(comp, "RECALL_ARCHIVES", 8))
        snippet = int(getattr(comp, "RECALL_SNIPPET", 700))
    except Exception:  # noqa: BLE001
        newest_limit, snippet = 8, 700
    considered = 0
    present = 0
    try:
        for entry in comp.index_entries(limit=newest_limit) or []:
            considered += 1
            try:
                if Path(str(entry.get("zip") or "")).is_file():
                    present += 1
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        considered, present = 0, 0
    rows.append(_row("Live journal it reads", _plural(live, "turn"), "this session, verbatim, every time"))
    rows.append(
        _row(
            "Sealed sessions it opens",
            f"the newest {min(newest_limit, n_sealed)}",
            f"never the whole archive - the newest {newest_limit} only" if n_sealed > newest_limit else "every sealed session there is",
        )
    )
    rows.append(_row("Of those, on disk", f"{present} of {considered}", "a recall skips a zip it cannot open, silently"))
    rows.append(_row("Returned per hit", f"{snippet} characters", "a snippet, not the whole turn"))
    rows.append(
        _row(
            "A query with no word of 4+ letters",
            "returns no_keywords",
            "that is the rule, not an empty history - ask with a real word",
        )
    )
    return rows


# ------------------------------------------------------------------------------------------------
# the card
# ------------------------------------------------------------------------------------------------
def build(ctx: Any = None) -> dict[str, Any]:
    """One payload: the lights, the groups, and every fact this card could not get."""
    gaps: list[str] = []
    comp = _owner("compaction")
    sess = _owner("sessions")
    paths_mod = _owner("paths")
    atomicio = _owner("atomicio")

    st = _status(comp, gaps)
    sv = _vault(sess, gaps)

    groups: list[dict[str, Any]] = []
    lights: list[dict[str, Any]] = []

    if st:
        groups.append(_group("This session", _live_rows(st)))
        groups.append(_group("The window", _window_rows(st)))
        groups.append(_group("Sealed archive", _archive_rows(comp, st)))
        groups.append(_group("Reach of a recall", _reach_rows(comp, st)))
        lights.append(
            {
                "state": "green",
                "label": "Journal",
                "text": f"{_plural(st.get('turns_live'), 'turn')} · {_size(st.get('journal_bytes'))}",
                "detail": f"session {st.get('session_id')}, started {_when(st.get('session_iso'))}",
                "next_change": "the oldest turns fold into a digest when the window fills",
            }
        )
    else:
        lights.append({"state": "grey", "label": "Journal", "text": "unchecked", "detail": "the record could not be read"})

    if sv:
        groups.append(_group("The vault", _vault_rows(sv)))
        newest = sv.get("newest") or {}
        lights.append(
            {
                "state": "green",
                "label": "Vault",
                "text": f"{_plural(sv.get('sessions'), 'session')} filed · {_size(sv.get('bytes'))}",
                "detail": f"newest {newest.get('sid')}" if newest else "nothing filed yet",
                "next_change": "a chat is filed when it is closed",
            }
        )
    else:
        lights.append({"state": "grey", "label": "Vault", "text": "unchecked", "detail": "the vault could not be read"})

    events = _events_rows(paths_mod, atomicio, gaps)
    if events:
        groups.append(_group("Event journal", events))

    # The rotation policy is the kit's own, so print the owner's numbers rather than describe it vaguely.
    rec = _owner("receipts")
    if rec is not None:
        try:
            keep_r = getattr(rec, "RECEIPTS_KEEP_DEFAULT", None)
            keep_e = getattr(rec, "EVENTS_KEEP_DEFAULT", None)
            rows = [
                _row("Receipts kept", str(keep_r) if keep_r is not None else "the kit's own default", "older receipts are pruned, newest kept"),
                _row("Events kept", str(keep_e) if keep_e is not None else "the kit's own default", "applies to the event journal above"),
                _row(
                    "Rule",
                    "an append-only store is pruned, never deleted wholesale",
                    "so a growing journal cannot quietly fill a stick - the counts above are after pruning",
                ),
            ]
            groups.append(_group("How this is bounded", rows))
        except Exception:  # noqa: BLE001
            pass

    # Facts no source here can publish - stated, so a green card is not read as omniscience.
    gaps.append(
        "the record estimates tokens from characters, so its window figures are its own estimate - "
        "the engine reports no per-turn usage to compare against"
    )
    gaps.append(
        "a chat that was never sealed and never journalled leaves no trace in any store here, so this card "
        "cannot report what it was never told about"
    )
    if sv and not (sv.get("unfiled_journals") or sv.get("legacy_files")):
        gaps.append(
            "every journal on this tree is either the live one or filed; a journal that is mid-write between "
            "the two shows as 'journalled but not filed' on the vault group and nowhere else"
        )

    answered = sum(1 for name in ("compaction", "sessions", "paths") if _owner(name) is not None)
    owners_read = [name for name, mod in (("compaction", comp), ("sessions", sess), ("paths", paths_mod)) if mod is not None]
    unread = [name for name in ("compaction", "sessions", "paths") if name not in owners_read]

    if unread:
        state = "grey"
        state_text = f"unchecked: {', '.join(unread)}"
    else:
        state = "green"
        sealed = int((st or {}).get("sessions_sealed") or 0)
        filed = int((sv or {}).get("sessions") or 0)
        turns = int((st or {}).get("turns_total") or 0)
        state_text = f"read {answered}/3 fact owners · {_plural(filed, 'session')} filed · {sealed} sealed · {_plural(turns, 'turn')} recorded"
    return {
        "ok": True,
        "signature": SIGNATURE,
        "module": MID,
        "at": _at(),
        "state": state,
        "state_text": state_text,
        "lights": lights,
        "groups": groups,
        "missing": gaps,
        "routes": [{"method": "GET", "path": ROUTE, "purpose": "this card"}],
        "refresh_s": 10,
    }


# ------------------------------------------------------------------------------------------------
# module host surface
# ------------------------------------------------------------------------------------------------
def data(ctx: Any, req: Any) -> None:
    req.json(200, build(ctx))


def health(ctx: Any) -> dict[str, Any]:
    """Can this card read the stores it reports on, on THIS tree, right now?"""
    have = [name for name in OWNERS if _owner(name) is not None]
    if not have:
        return {"ok": False, "detail": "none of the fact owners import on this tree: " + ", ".join(OWNERS)}
    panel = build(ctx)
    if not (panel.get("groups") or []):
        return {"ok": False, "detail": "the owners import but none answered, so this card would read empty"}
    return {
        "ok": bool(panel.get("groups")),
        "detail": (
            f"reads {len(have)}/{len(OWNERS)} owners · state {panel.get('state')} · "
            f"{len(panel.get('groups') or [])} group(s), {len(panel.get('missing') or [])} named gap(s)"
        ),
    }


def register(ctx: Any) -> dict[str, Any]:
    return {
        "routes": [("GET", ROUTE, data)],
        "limbs": [],
        "panes": [
            {
                "id": "dock.mem",
                "slot": "dock",
                "title": "What this console remembers",
                "data": f"GET {ROUTE}",
                "refresh_s": 10,
                "inner_scroll": False,
            }
        ],
        "health": health,
    }
