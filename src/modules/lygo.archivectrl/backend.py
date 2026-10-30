"""lygo.archivectrl - the operator's search surface for the Internet Archive.

Δ9Φ963-LYGO-ARCHIVECTRL-v1

WHAT THIS IS
    The actuator half of the archive pair, and the module the "Archive" band in the console talks
    to. `lygo.archiveinfo` answers "can this console reach the archive, and which door answered"; this
    one RUNS the search:

        search    look a URL up in the archive: the capture closest to a date, then the dated list
                  (one page, or a whole site), or - with a word - which of a site's archived URLs
                  contain that word
        read      open an ARCHIVED copy of a page as text. This is the one that matters for a dead
                  link, a 404, or a page that has changed since the operator last saw it
        watch     put a URL on the link-rot list: it is then checked against the live web and the
                  archive, and `check` re-runs that for every entry
        check     re-check the whole watch list: dead-but-archived is a finding, not a fault
        forget    clear the search log - and say what was cleared, so `restore` can put it back
        restore   put back what `forget` cleared

WHY IT OWNS A WATCH LIST
    "What did this page say" is a one-off question. "Is this link still alive, and does the archive
    have a copy when it dies" is a standing one, and it is the reason a person installs an archive
    client at all. The watch list is that standing question, written down.

THE ONE THING IT OWNS
    `save/archive` - the recent searches and the watch list, through atomicio. A search term is
    operator text that lands in a record, so it passes the kernel's p0 gate before anything is
    written, the same way the notepad's saves and the song studio's lyrics do. A quarantined term is
    refused BY NAME with the gate's own reason and nothing is written; the READ surface (looking a
    public URL up in the archive) is nobler than the record and is not gated - only the storing is.

WHAT IT NEVER DOES
    * It fetches nothing itself. Every lookup is a call into the `wayback` harness, so the refusal
      rules (no loopback, no private hosts, no fabricated domains), the timestamp format and the
      cost of each door live in exactly one place.
    * It never claims a page is unarchived from one empty answer: the harness asks the resolver,
      then the availability API, then the index, and names every check that ran.
    * It never says a search "found nothing" when what happened was a timeout. The harness's own
      error name (`cdx_timeout`, `timegate_dns`, ...) is carried into the record and shown.
    * It never inserts archived page text as markup. The text travels as a string and the panel
      puts it in a text node; somebody else's page is data, not code.
"""
from __future__ import annotations

import datetime as dt
import time
from typing import Any

SIGNATURE = "Δ9Φ963-LYGO-ARCHIVECTRL-v1"
MID = "lygo.archivectrl"
ROUTE = "/api/webarchive"
ROUTES: tuple[tuple[str, str], ...] = (("GET", ROUTE), ("POST", ROUTE))
OWNERS: tuple[str, ...] = ("wayback", "paths", "atomicio", "p0")

ACTIONS = ("search", "read", "watch", "unwatch", "check", "forget", "restore")

STATE_NAME = "archive.json"
#: Its own directory under save/, and NOT `save/archive`: that directory belongs to the
#: sessions archiver (its zip bundles and index.jsonl), and the kit's `Ctx.save_dir()`
#: returns the save ROOT, so a bare `archive.json` would land beside it at save/archive.json.
#: Measured 2026-09-27 (defect 147): a live probe wrote both of those before this line existed.
STATE_DIR = "wb-archive"

#: The search log keeps this many queries, newest first. A log that grows without a bound is a
#: file nobody reads; 40 is "the last working session" and the panel shows the newest few.
MAX_QUERIES = 40
#: The watch list is unbounded in principle (it is the operator's own set), but each entry costs a
#: live probe plus a resolver call on `check`, so the record says how many were checked.
WATCH_LIMIT = 200
#: Text handed to the panel from an archived page. Beyond this the panel is told it was cut - the
#: full page is one click away at the snapshot URL.
READ_CHARS = 40_000

SCOPES = ("page", "site", "prefix")


# --- formatters ---------------------------------------------------------------------------------


def _at() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _clock() -> str:
    return time.strftime("%H:%M:%S")


def _seconds(v: Any) -> str:
    try:
        return f"{float(v):.1f} s"
    except (TypeError, ValueError):
        return "?"


def _row(k: str, v: Any, note: str = "", dot: str = "") -> dict[str, Any]:
    return {"k": str(k), "v": str(v), "note": str(note), "dot": str(dot)}


def _group(title: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"title": title, "rows": rows}


def _light(state: str, label: str, text: str, detail: str = "") -> dict[str, Any]:
    return {"state": state, "label": label, "text": text, "detail": detail}


def _short(path: Any) -> str:
    s = str(path or "")
    for cut in ("\\lygo_llm_console\\", "/lygo_llm_console/", "\\archive\\", "/archive/"):
        if cut in s:
            return "…" + s.split(cut, 1)[1].replace("\\", "/")
    return s


# --- its own state ------------------------------------------------------------------------------


def _state_path(ctx: Any) -> Any:
    """`save/wb-archive/archive.json` - always inside its OWN directory, on the host and off it."""
    if ctx is not None and hasattr(ctx, "save_dir"):
        try:
            return ctx.save_dir(STATE_DIR) / STATE_NAME
        except Exception:  # noqa: BLE001
            pass
    try:
        import paths

        return paths.SAVE / STATE_DIR / STATE_NAME
    except Exception:  # noqa: BLE001
        return None


def _state_default() -> dict[str, Any]:
    return {"queries": [], "watched": [], "cleared": [], "updated": ""}


def _read_state(ctx: Any) -> dict[str, Any]:
    path = _state_path(ctx)
    if path is None:
        return _state_default()
    if ctx is not None and hasattr(ctx, "read_json"):
        data = ctx.read_json(path, None)
    else:
        try:
            import json

            import atomicio

            data = json.loads(atomicio.read_text(path) or "null")
        except Exception:  # noqa: BLE001
            data = None
    if not isinstance(data, dict):
        return _state_default()
    out = _state_default()
    out.update(data)
    for key in ("queries", "watched", "cleared"):
        if not isinstance(out.get(key), list):
            out[key] = []
    return out


def _write_state(ctx: Any, state: dict[str, Any]) -> tuple[bool, str]:
    """(written, why-not). Never claims a write it did not make."""
    state["updated"] = _at()
    path = _state_path(ctx)
    if path is None:
        return False, "no save path is reachable on this tree, so nothing was written"
    try:
        if ctx is not None and hasattr(ctx, "write_json"):
            ctx.write_json(path, state)
            return True, ""
        import json

        import atomicio

        path.parent.mkdir(parents=True, exist_ok=True)
        atomicio.atomic_write_text(path, json.dumps(state, indent=2, ensure_ascii=False))
        return True, ""
    except Exception as exc:  # noqa: BLE001 - a write that failed is reported, not swallowed
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"


def _gate(text: str) -> dict[str, Any] | None:
    """The kernel's p0 gate over what is about to be WRITTEN. None means it passed."""
    try:
        from p0_hook import gate_prompt
    except Exception:  # noqa: BLE001
        return {"verdict": "UNKNOWN", "reason": "the p0 gate could not be imported, so nothing was written"}
    try:
        v = gate_prompt(str(text or "")[:8000])
    except Exception as exc:  # noqa: BLE001 - a gate that raised is not a pass
        return {"verdict": "UNKNOWN", "reason": f"the p0 gate raised: {str(exc)[:120]}"}
    return v if v.get("verdict") == "QUARANTINE" else None


# --- the search itself --------------------------------------------------------------------------


def _scope_for(target: str, scope: Any) -> str:
    """A scope the harness understands. A bare host defaults to the whole site: on the archive a
    'page' ask for a domain is one URL, and the operator asking for a site means the site."""
    want = str(scope or "").strip().lower()
    if want in SCOPES:
        return want
    tail = str(target or "").split("//")[-1]
    return "site" if "/" not in tail.strip("/") else "page"


def _match_for(scope: str) -> str:
    return {"page": "exact", "site": "domain", "prefix": "prefix"}.get(scope, "exact")


def run_search(ctx: Any, body: dict[str, Any]) -> dict[str, Any]:
    """The one call the panel makes. Composes the harness doors and reports what each one cost."""
    import wayback as wb

    target = str(body.get("target") or body.get("url") or body.get("q") or "").strip()
    word = str(body.get("word") or "").strip()
    as_of = str(body.get("as_of") or body.get("timestamp") or "").strip()
    scope = _scope_for(target, body.get("scope"))
    try:
        limit = int(body.get("limit") or 25)
    except (TypeError, ValueError):
        limit = 25
    limit = max(1, min(limit, wb.MAX_ROWS))

    url, refusal = wb.clean_target(target)
    if refusal:
        return refusal  # the harness's own name, why and hint travel to the panel
    if as_of:
        # A date the archive cannot read is refused HERE, by the harness's own rule, before any door
        # is asked. Left to `closest()` the refusal would be buried in that one field and the search
        # would answer 200 - a successful-looking search that never asked the right question.
        as_of, bad_date = wb.norm_timestamp(as_of) if hasattr(wb, "norm_timestamp") else (as_of, None)
        if bad_date:
            return bad_date

    asked: list[dict[str, Any]] = []
    closest = wb.closest(url, as_of)
    asked.append({"door": "closest", "ok": bool(closest.get("ok")), "error": closest.get("error") or "",
                  "seconds": closest.get("seconds")})

    if word:
        found = wb.find(url, word, limit=limit)
        asked.append({"door": "find", "ok": bool(found.get("ok")), "error": found.get("error") or "",
                      "seconds": found.get("seconds")})
        rows, count = (found.get("rows") or []), int(found.get("count") or 0)
        what = f"archived URLs of {url} containing {word!r}"
    elif scope == "site":
        site = wb.inventory(url, limit=limit)
        asked.append({"door": "site", "ok": bool(site.get("ok")), "error": site.get("error") or "",
                      "seconds": site.get("seconds")})
        rows, count = (site.get("rows") or []), int(site.get("count") or 0)
        what = f"archived URLs of {url}"
    else:
        caps = wb.captures(url, limit=limit, match=_match_for(scope))
        asked.append({"door": "captures", "ok": bool(caps.get("ok")), "error": caps.get("error") or "",
                      "seconds": caps.get("seconds")})
        rows, count = (caps.get("rows") or []), int(caps.get("count") or 0)
        what = f"captures of {url}"

    failed = [a for a in asked if not a["ok"]]
    cost = sum(float(a.get("seconds") or 0) for a in asked)
    errors = [f"{a['door']}: {a['error']}" for a in failed]
    # A search that could not run is NOT a search that found nothing. The distinction is the whole
    # point of this module, so it is carried in the answer as its own field.
    out: dict[str, Any] = {
        "ok": bool(not failed),
        "action": "search",
        "signature": SIGNATURE,
        "target": url,
        "word": word,
        "scope": scope,
        "as_of": as_of,
        "what": what,
        "rows": rows,
        "count": count,
        "limit": limit,
        "closest": {k: v for k, v in closest.items() if k not in ("rows", "text")},
        "asked": asked,
        "seconds": round(cost, 2),
        "found_nothing": bool(not failed and count == 0),
        "errors": errors,
        "at": _at(),
    }
    if failed:
        out["error"] = failed[0]["error"] or "search_failed"
        out["why"] = "; ".join(errors)
        out["hint"] = ("the archive answered something other than rows for at least one door; the "
                       "per-door errors above say which, and a timeout means the index was busy, not "
                       "that the page is missing")
    return out


def _record_query(ctx: Any, out: dict[str, Any]) -> dict[str, Any]:
    """Write the query down. This is the part that passes the gate, and the part that can fail."""
    entry = {
        "at": out.get("at") or _at(),
        "target": out.get("target") or "",
        "word": out.get("word") or "",
        "scope": out.get("scope") or "",
        "as_of": out.get("as_of") or "",
        "count": int(out.get("count") or 0),
        "seconds": out.get("seconds"),
        "ok": bool(out.get("ok")),
        "error": out.get("error") or "",
        "newest": ((out.get("closest") or {}).get("snapshot") or {}).get("timestamp") or "",
    }
    state = _read_state(ctx)
    state["queries"] = ([entry] + list(state.get("queries") or []))[:MAX_QUERIES]
    written, why = _write_state(ctx, state)
    return {"saved": written, "why": why, "entry": entry}


# --- the read-out the panel polls ---------------------------------------------------------------


def build(ctx: Any = None, st: dict[str, Any] | None = None) -> dict[str, Any]:
    """The pane card: what the archive surface is doing on this console right now."""
    st = st if isinstance(st, dict) else _read_state(ctx)
    queries = list(st.get("queries") or [])
    watched = list(st.get("watched") or [])
    missing: list[str] = []

    last = queries[0] if queries else {}
    rot = [w for w in watched if isinstance(w, dict) and w.get("finding") == "dead-but-archived"]
    alive = [w for w in watched if isinstance(w, dict) and w.get("finding") == "alive"]
    unchecked = [w for w in watched if isinstance(w, dict) and w.get("finding") in ("", "unchecked")]

    if not queries:
        search_light = _light("grey", "Last search", "nothing searched yet",
                             "the band below runs a search; this pane keeps what was asked")
    elif last.get("ok"):
        search_light = _light("green", "Last search",
                              f"{last.get('count')} row(s) in {_seconds(last.get('seconds'))}",
                              f"{_clock() if not last.get('at') else str(last.get('at'))[11:19]} "
                              f"{last.get('target')} {('word ' + str(last.get('word'))) if last.get('word') else ''}")
    else:
        search_light = _light("red", "Last search", str(last.get("error") or "failed"),
                              f"the archive did not answer for {last.get('target')}: "
                              f"{last.get('error') or 'no reason recorded'}")

    if not watched:
        watch_light = _light("grey", "Watched links", "nothing watched",
                             "watch a URL to have it checked against the live web and the archive")
    elif rot:
        watch_light = _light("amber", "Watched links", f"{len(rot)} dead but archived",
                             "; ".join(str(w.get('url') or '')[:60] for w in rot[:3]))
    elif unchecked:
        watch_light = _light("grey", "Watched links", f"{len(unchecked)} not checked yet",
                             "run check to compare each watched URL with the live web")
    else:
        watch_light = _light("green", "Watched links", f"{len(alive)} still alive",
                             "every watched link answered on the live web")

    if not (queries or watched):
        state, state_text = "grey", "nothing searched or watched on this console yet"
    elif queries and not last.get("ok"):
        state, state_text = "red", f"the last search failed: {last.get('error') or 'unknown'}"
    elif rot:
        state, state_text = "amber", f"{len(rot)} watched link(s) are dead but archived"
    else:
        state, state_text = "green", f"{len(queries)} search(es) kept · {len(watched)} watched"

    recent_rows = [
        _row(str(q.get("target") or "")[:60],
             f"{q.get('count')} row(s) · {_seconds(q.get('seconds'))}",
             f"{str(q.get('at') or '')[:19]}"
             + (f" · word {q.get('word')}" if q.get("word") else "")
             + (f" · as of {q.get('as_of')}" if q.get("as_of") else "")
             + (f" · {q.get('error')}" if q.get("error") else ""),
             "green" if q.get("ok") else "red")
        for q in queries[:6]
    ] or [_row("-", "nothing kept yet", "the log keeps the last 40 searches")]

    watch_rows = [
        _row(str(w.get("url") or "")[:60],
             {"dead-but-archived": "dead, archived copy exists",
              "alive": "alive",
              "dead": "dead, no copy found",
              "unchecked": "not checked"}.get(str(w.get("finding") or "unchecked"), "not checked"),
             f"live {w.get('live_status') or '?'} · archived "
             f"{((w.get('archived') or {}).get('timestamp') or 'none')}"
             + (f" · {w.get('note')}" if w.get("note") else ""),
             {"dead-but-archived": "amber", "alive": "green", "dead": "red"}.get(str(w.get("finding") or ""), "grey"))
        for w in watched[:8]
    ] or [_row("-", "nothing watched", "watch a URL to keep an eye on it")]

    return {
        "signature": SIGNATURE,
        "at": _at(),
        "state": state,
        "state_text": state_text,
        "lights": [search_light, watch_light],
        "groups": [
            _group("The last searches", recent_rows),
            _group("Watched links (link rot)", watch_rows),
            _group("What this module owns", [
                _row("Recorded", _short(_state_path(ctx)) or "-", "through atomicio, behind the p0 gate",
                     "green"),
                _row("Kept", f"{len(queries)} of {MAX_QUERIES} searches · {len(watched)} watched",
                     "the newest first; older searches fall off the end"),
                _row("Archived text", f"{READ_CHARS} characters per read",
                     "cut text says so; the snapshot URL opens the whole page"),
            ]),
        ],
        "missing": missing,
        "queries": len(queries),
        "watched": len(watched),
        "rot": len(rot),
    }


def data(ctx: Any, req: Any) -> None:
    """GET /api/webarchive - the pane's own answer: the card plus the state it was built from."""
    st = _read_state(ctx)
    card = build(ctx, st)
    req.json(200, {"ok": True, "signature": SIGNATURE, "module": MID, "at": card["at"],
                   "state": card["state"], "state_text": card["state_text"],
                   "card": card, "missing": card["missing"], "state_file": st.get("updated") or "",
                   "queries": (st.get("queries") or [])[:10], "watched": st.get("watched") or []})


# --- the actions --------------------------------------------------------------------------------


def _watch_entry(target: str, note: str = "") -> dict[str, Any]:
    """Probe a URL the honest way: what the live web says now, and what the archive has."""
    import wayback as wb

    url, refusal = wb.clean_target(target)
    if refusal:
        return refusal
    live = wb.live_status(url)
    near = wb.closest(url)
    snap = (near.get("snapshot") or {}) if near.get("ok") else {}
    if live.get("error"):
        finding = "unchecked"
    elif live.get("reachable"):
        finding = "alive"
    elif snap:
        finding = "dead-but-archived"
    else:
        finding = "dead"
    return {
        "ok": True,
        "url": url,
        "note": str(note or "")[:200],
        "live_status": live.get("status") if live.get("ok") else "",
        "live_error": live.get("error") or "",
        "checked_at": _at(),
        "archived": {"timestamp": str(snap.get("timestamp") or ""), "url": str(snap.get("url") or "")},
        "finding": finding,
        "why": ("the live ask failed, so this URL is not checked rather than dead"
                if finding == "unchecked" else ""),
    }


def control(ctx: Any, req: Any) -> None:
    """POST /api/webarchive - run a search, read an archived copy, or work the watch list."""
    body = req.body(200_000) or {}
    if not isinstance(body, dict):
        req.json(400, {"ok": False, "error": "bad_body"})
        return
    action = str(body.get("action") or "search").strip().lower()
    if action not in ACTIONS:
        req.json(400, {"ok": False, "error": "unknown_action", "action": action,
                       "actions": list(ACTIONS)})
        return

    if action == "search":
        target = str(body.get("target") or body.get("url") or "").strip()
        word = str(body.get("word") or "").strip()
        # Only the STORING is gated: the search itself is a public lookup, the log is a record.
        gate = _gate(f"{target} {word}".strip())
        if gate:
            req.json(403, {"ok": False, "error": "gate_refused", "gate": gate})
            return
        out = run_search(ctx, body)
        if not out.get("ok") and out.get("error") in ("no_url", "bad_url", "scheme_not_allowed",
                                                      "host_not_allowed", "private_host",
                                                      "placeholder_url", "url_too_long",
                                                      "bad_timestamp"):
            # A refused ask is the operator's to fix: answer the refusal code, not a 200.
            req.json(400, out)
            return
        saved = _record_query(ctx, out) if out.get("ok") else {"saved": False, "why": "not recorded: the search did not run"}
        out["saved"] = saved
        out["card"] = build(ctx)
        req.json(200 if out.get("ok") else 409, out)
        return

    if action == "read":
        import wayback as wb

        target = str(body.get("target") or body.get("url") or "").strip()
        out = wb.read(target, body.get("timestamp") or "", raw=bool(body.get("raw")),
                      chars=READ_CHARS)
        if not out.get("ok") and str(out.get("error") or "").startswith(
                ("no_url", "bad_url", "scheme", "host_not", "private", "placeholder", "url_too", "bad_timestamp")):
            req.json(400, out)
            return
        # Nothing of the operator's is stored here, so there is nothing to gate: this is somebody
        # else's page arriving as data.
        req.json(200 if out.get("ok") else 409, out)
        return

    if action in ("watch", "unwatch"):
        state = _read_state(ctx)
        watched = [w for w in (state.get("watched") or []) if isinstance(w, dict)]
        target = str(body.get("target") or body.get("url") or "").strip()
        url, refusal = _clean_for_state(target)
        if refusal:
            req.json(400, refusal)
            return
        if action == "unwatch":
            kept = [w for w in watched if w.get("url") != url]
            if len(kept) == len(watched):
                req.json(409, {"ok": False, "error": "not_watched", "url": url,
                               "why": "that URL is not on the watch list"})
                return
            state["watched"] = kept
            written, why = _write_state(ctx, state)
            req.json(200 if written else 500, {"ok": written, "action": "unwatch", "url": url,
                                               "watched": kept, "why": why, "card": build(ctx, state)})
            return
        note = str(body.get("note") or "").strip()
        gate = _gate(f"{url} {note}".strip())
        if gate:
            req.json(403, {"ok": False, "error": "gate_refused", "gate": gate})
            return
        entry = _watch_entry(url, note)
        if not entry.get("ok"):
            req.json(400, entry)
            return
        if len([w for w in watched if w.get("url") != url]) >= WATCH_LIMIT:
            req.json(409, {"ok": False, "error": "watch_list_full", "limit": WATCH_LIMIT,
                           "why": "the watch list is full; remove an entry first"})
            return
        state["watched"] = [entry] + [w for w in watched if w.get("url") != url]
        written, why = _write_state(ctx, state)
        req.json(200 if written else 500, {"ok": written, "action": "watch", "entry": entry,
                                           "why": why, "card": build(ctx, state)})
        return

    if action == "check":
        state = _read_state(ctx)
        watched = [w for w in (state.get("watched") or []) if isinstance(w, dict)]
        checked: list[dict[str, Any]] = []
        refused: list[str] = []
        for w in watched:
            fresh = _watch_entry(str(w.get("url") or ""), str(w.get("note") or ""))
            if fresh.get("ok"):
                checked.append(fresh)
            else:
                refused.append(str(w.get("url") or ""))
        if refused and not checked:
            req.json(409, {"ok": False, "error": "nothing_checked", "refused": refused,
                           "why": "every watched URL was refused or unreadable, so nothing was checked"})
            return
        state["watched"] = checked
        written, why = _write_state(ctx, state)
        rot = [c for c in checked if c.get("finding") == "dead-but-archived"]
        req.json(200 if written else 500, {
            "ok": written, "action": "check", "checked": len(checked), "rot": len(rot),
            "refused": refused, "why": why, "findings": checked, "card": build(ctx, state)})
        return

    # forget / restore - one level of undo, so a mis-click is never final.
    state = _read_state(ctx)
    if action == "forget":
        moved = list(state.get("queries") or [])
        if not moved:
            req.json(200, {"ok": True, "action": "forget", "cleared": 0, "can_restore": False,
                           "why": "there was nothing to clear", "card": build(ctx, state)})
            return
        before = dict(state.get("cleared") or {}) if isinstance(state.get("cleared"), dict) else {}
        before.update({"at": _at(), "queries": moved})
        state["cleared"] = [before]
        state["queries"] = []
        written, why = _write_state(ctx, state)
        req.json(200 if written else 500, {"ok": written, "action": "forget", "cleared": len(moved),
                                           "can_restore": True, "why": why, "card": build(ctx, state)})
        return
    cleared = state.get("cleared") or []
    if not cleared:
        req.json(409, {"ok": False, "error": "nothing_to_restore",
                       "why": "no cleared search log is being held"})
        return
    last = cleared[0] if isinstance(cleared[0], dict) else {}
    state["queries"] = (list(last.get("queries") or []) + list(state.get("queries") or []))[:MAX_QUERIES]
    state["cleared"] = list(cleared[1:])
    written, why = _write_state(ctx, state)
    req.json(200 if written else 500, {"ok": written, "action": "restore",
                                       "restored": len(last.get("queries") or []), "why": why,
                                       "card": build(ctx, state)})


def _clean_for_state(target: str) -> tuple[str, dict[str, Any] | None]:
    """The harness's own target rules, asked without making a call. Used by the watch actions."""
    import wayback as wb

    url, refusal = wb.clean_target(target)
    return (url, None) if not refusal else ("", refusal)


def health(ctx: Any) -> dict[str, Any]:
    """Can this module read its own record, and does the harness import, on THIS tree, right now?"""
    try:
        import wayback as wb  # noqa: F401

        st = _read_state(ctx)
        card = build(ctx, st)
        return {
            "ok": True,
            "detail": (
                f"{len(st.get('queries') or [])} search(es) kept, {len(st.get('watched') or [])} watched · "
                f"state {card.get('state')} · record {_short(_state_path(ctx)) or 'not reachable'}"
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"the archive surface did not answer: {str(exc)[:180]}"}


def register(ctx: Any) -> dict[str, Any]:
    """The routes and the pane. The limb itself (`wayback`) lives in the kernel and is declared,
    not re-registered - registering it here too would list one tool twice."""
    return {
        "routes": [("GET", ROUTE, data), ("POST", ROUTE, control)],
        "limbs": [],
        "panes": [
            {
                "id": "dock.archive",
                "slot": "dock",
                "title": "Archive search",
                "data": f"GET {ROUTE}",
                "refresh_s": 20,
                "inner_scroll": True,
            }
        ],
        "health": health,
    }
