"""lygo.musicinfo — what this console can sing, in one card.

Δ9Φ963-LYGO-MUSICINFO-v1

WHAT THIS ANSWERS
    The third of the making cards, beside the picture engine and the voice engine: **what can this
    machine make a song WITH, and what did it last make?** Two questions the operator could not
    answer anywhere in one place:

        the engine      which music engine this tree reaches, where its install is, whether its own
                        entry script and its own python are there
        its weights     every declared weight file, by name and by size, or the names of the ones
                        that are not on disk
        the route       which route a song would take right now (card free, or offload), and where
                        the finished song lands
        the last song   the newest song this kit really wrote, with what it recorded about itself

WHY IT EXISTS AS A CARD AND NOT A LIMB
    The limb (`music_generate`) makes a song; this card is the read-out that says whether a song is
    possible BEFORE one is asked for, and whether the last one is really on disk. An operator who
    asks for a song on a machine with no weights should learn that from a card, not from a failure.

WHAT IT DELIBERATELY DOES NOT DO
    * No new state and no writes anywhere: `owns: []`, `gate: none`, and its own test scans this
      file for write calls. The songs it reports on were written by the limb.
    * No re-derivation. The engine, the weights, the route and the last song are all asked of their
      owner (`music_tools`) - this card prints what it is told and invents nothing.
    * No claim that a song was made when none was. "No song rendered yet on this kit" is a TRUE
      fact on a fresh kit and stays green, exactly as an empty vault does on the memory card.
    * A weight file that is declared and absent is reported as ABSENT WITH ITS NAME, never as ready
      and never folded into a percentage that reads as fine.

THE HONESTY RULES IT INHERITS
    * A check that could not run is named in `missing` and drags the card off green.
    * The card never reaches into the engine's own folders to write, clean or repair.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

SIGNATURE = "Δ9Φ963-LYGO-MUSICINFO-v1"
MID = "lygo.musicinfo"
ROUTE = "/api/musicinfo"
ROUTES: tuple[tuple[str, str], ...] = (("GET", ROUTE),)

#: Every module this card asks for a fact. A missing one is a named gap, never a zero.
OWNERS: tuple[str, ...] = ("music_tools", "paths", "hardware")


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
    """Bytes at a human scale. The stage-1 weights are 12.4 GB and a codec is 1.36 GB, so both
    units matter here and neither may print as `0.00 GB`."""
    try:
        b = float(n or 0)
    except (TypeError, ValueError):
        return "unreadable"
    if b <= 0:
        return "nothing on disk"
    for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if b >= div:
            return f"{b / div:.2f} {unit}"
    return f"{int(b)} B"


def _row(k: str, v: Any, note: str = "", dot: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"k": k, "v": "" if v is None else str(v)}
    if note:
        out["note"] = note
    if dot:
        out["dot"] = dot
    return out


def _group(title: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"title": title, "rows": rows}


def _short(path: Any) -> str:
    """The part of a path that tells the operator where to look, not the whole drive."""
    text = str(path or "")
    for marker in ("lygo_llm_console", "yue.git", "pinokio", "LYGO_MEDIA"):
        idx = text.find(marker)
        if idx >= 0:
            return text[idx:]
    return text


def _light(state: str, label: str, text: str, detail: str = "", next_change: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"state": state, "label": label, "text": text}
    if detail:
        out["detail"] = detail
    if next_change:
        out["next_change"] = next_change
    return out


# ------------------------------------------------------------------------------------------------
# owner access - lazily, so a missing owner is a named gap and never an ImportError into a handler
# ------------------------------------------------------------------------------------------------
def _owner(name: str) -> Any | None:
    try:
        return __import__(name)  # noqa: PLC0415 - deliberately late: a missing owner is a finding
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------------------------------------
# the collectors - each one asks its owner for the fact
# ------------------------------------------------------------------------------------------------
def _engine_rows(mt: Any, st: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        _row("Engine", st.get("engine_label") or st.get("engine") or "unknown",
             f"the config names `{st.get('engine')}`"),
    ]
    if st.get("app"):
        rows.append(_row("Installed at", _short(st.get("app")), "the app the console drives; it is not copied into the kit"))
    else:
        rows.append(_row("Installed at", "no music engine found on this machine",
                         "checked the configured root, the media root and the standard app folders",
                         dot="amber"))
    if st.get("entry_script"):
        rows.append(_row("Its entry", Path(str(st["entry_script"])).name if st.get("entry_present") else "missing",
                         "" if st.get("entry_present") else str(st["entry_script"]), dot="" if st.get("entry_present") else "amber"))
    if st.get("python"):
        rows.append(_row("Its own python", "found" if st.get("python_present") else "missing",
                         _short(st.get("python")), dot="" if st.get("python_present") else "amber"))
    if st.get("hub"):
        rows.append(_row("Model cache", _short(st.get("hub")), "the weights are resolved from this cache, never re-downloaded"))
    cli = st.get("infer_cli") or {}
    if cli.get("path"):
        rows.append(_row("Its command line", "cannot run" if not cli.get("usable") else "usable",
                         str(cli.get("why") or ""), dot="amber" if not cli.get("usable") else ""))
    if st.get("resolve"):
        rows.append(_row("Where it looked", " → ".join(str(x) for x in st["resolve"]),
                         "the search order, so 'not found' is a claim about a named search"))
    return rows


def _weight_rows(st: dict[str, Any], gaps: list[str]) -> list[dict[str, Any]]:
    weights = st.get("weights") or []
    if not weights:
        return [_row("Weights", "unchecked", "no engine resolved, so no weight list could be read", dot="amber")]
    rows: list[dict[str, Any]] = []
    for w in weights:
        present = bool(w.get("present"))
        rows.append(_row(
            str(w.get("id") or "?"),
            _size(w.get("bytes")) if present else "not on disk",
            str(w.get("note") or ""),
            dot="" if present else "amber",
        ))
    missing = list(st.get("weights_missing") or [])
    if missing:
        gaps.append(
            "the engine is installed but " + _plural(len(missing), "weight file") + " is not on disk: "
            + ", ".join(str(m) for m in missing) + " - a song cannot render until it is fetched"
        )
    return rows


def _route_rows(mt: Any, hw: Any, st: dict[str, Any], gaps: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        route = mt.route_for_a_song()
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"the route a song would take could not be worked out: {type(exc).__name__}: {exc}")
        return [_row("Route", "unchecked", "the route owner raised", dot="amber")]
    where = str(route.get("route") or route.get("where") or "unknown")
    rows.append(_row("Route a song would take", where, str(route.get("why") or ""),
                     dot="" if where in ("cuda", "offload") else "amber"))
    free = route.get("free_mib")
    need = route.get("need_mib")
    if isinstance(free, (int, float)):
        rows.append(_row("Card free now", f"{int(free)} MiB", "read from the driver at the moment this card was refreshed"))
    if isinstance(need, (int, float)):
        rows.append(_row("A render wants", f"{int(need)} MiB", str(route.get("need_note") or "the declared requirement for this engine's quantized profile")))
    if route.get("profile"):
        rows.append(_row("Profile", str(route["profile"]), str(route.get("profile_note") or "")))
    if route.get("holder"):
        rows.append(_row("Card held by", str(route["holder"]), "the engine waits for a free card, or renders with offload", dot="amber"))
    if hw is None:
        gaps.append("`hardware` could not be imported, so the live card reading on this card is UNCHECKED - the route above still comes from its owner")
    rows.append(_row("Songs land in", _short(st.get("songs_dir")),
                     "writable" if st.get("songs_dir_writable") else "NOT writable - a render would fail at the last step",
                     dot="" if st.get("songs_dir_writable") else "amber"))
    have = [n for n, p in (("ffmpeg", st.get("ffmpeg")), ("ffprobe", st.get("ffprobe"))) if p]
    rows.append(_row("Audio tools", ", ".join(have) if have else "neither ffmpeg nor ffprobe found",
                     "ffprobe reads a song's real duration; ffmpeg converts a wav to mp3" if have else "a song still renders, but its duration cannot be read",
                     dot="" if len(have) == 2 else "amber"))
    rows.append(_row("A render may run", f"{int(st.get('timeout_s') or 0)} s",
                     "the limb's ceiling: first run also loads a ~20 GB model, so it is generous by design"))
    return rows


def _song_rows(last: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not last:
        return [_row("Last song", "no song rendered yet on this kit",
                     "a true fact on a fresh kit - not a gap: ask for one and this row becomes a path")]
    rows = [
        _row("Last song", _short(last.get("path")), f"written {last.get('when') or 'at an unrecorded time'}"),
        _row("On disk", _size(last.get("bytes")), "measured from the file itself, not from a record of it"),
    ]
    if last.get("audio_seconds"):
        rows.append(_row("Length", f"{float(last['audio_seconds']):.1f} s", "read back from the audio with ffprobe"))
    if last.get("render_seconds"):
        rows.append(_row("Render took", f"{float(last['render_seconds']):.1f} s", "wall clock, engine start included"))
    if last.get("engine"):
        rows.append(_row("Made by", str(last.get("engine")), str(last.get("model") or "")))
    bits = [f"seed {last['seed']}" if last.get("seed") is not None else "",
            f"profile {last['profile']}" if last.get("profile") is not None else "",
            f"{last['segments']} segment(s)" if last.get("segments") else "",
            f"route {last['route']}" if last.get("route") else ""]
    bits = [b for b in bits if b]
    if bits:
        rows.append(_row("Its settings", " · ".join(bits), "from the sidecar the limb wrote beside the song"))
    if last.get("lyrics"):
        rows.append(_row("Words it sang", "recorded beside the song", str(last.get("path")).rsplit(".", 1)[0] + ".txt"))
    if last.get("sidecar_unreadable"):
        rows.append(_row("Sidecar", "unreadable", str(last["sidecar_unreadable"]), dot="amber"))
    return rows


def _declared_rows(st: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for e in st.get("engines") or []:
        rows.append(_row(
            str(e.get("label") or e.get("id")),
            "installed" if e.get("installed") else "declared, not present on this machine",
            f"{e.get('license') or ''} · {e.get('page') or ''}".strip(" ·"),
            dot="" if e.get("installed") else "",
        ))
    rows.append(_row("Choosing one", "config `music_engine`",
                     "the limb renders with the engine the config names; a declared engine that is not installed is never silently swapped in"))
    return rows


# ------------------------------------------------------------------------------------------------
# the card
# ------------------------------------------------------------------------------------------------
def build(ctx: Any = None) -> dict[str, Any]:
    """One payload: the lights, the groups, and every fact this card could not get."""
    gaps: list[str] = []
    mt = _owner("music_tools")
    hw = _owner("hardware")
    _owner("paths")

    groups: list[dict[str, Any]] = []
    lights: list[dict[str, Any]] = []

    if mt is None:
        gaps.append("the music owner (`music_tools`) could not be imported on this tree, so what this "
                    "machine can sing is UNCHECKED here - not 'no music engine'")
        lights.append(_light("grey", "Engine", "unchecked", "the music owner could not be imported"))
        return {
            "ok": True, "signature": SIGNATURE, "module": MID, "at": _at(),
            "state": "grey", "state_text": "unchecked: music_tools", "lights": lights, "groups": [],
            "missing": gaps, "routes": [{"method": "GET", "path": ROUTE, "purpose": "this card"}],
            "refresh_s": 20,
        }

    try:
        st = mt.music_status()
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"the music owner answered with an error instead of its state: {type(exc).__name__}: {exc}")
        lights.append(_light("grey", "Engine", "unchecked", "music_tools raised"))
        return {
            "ok": True, "signature": SIGNATURE, "module": MID, "at": _at(),
            "state": "grey", "state_text": "unchecked: music_tools raised", "lights": lights,
            "groups": [], "missing": gaps,
            "routes": [{"method": "GET", "path": ROUTE, "purpose": "this card"}], "refresh_s": 20,
        }

    groups.append(_group("The engine", _engine_rows(mt, st)))
    groups.append(_group("Its weights", _weight_rows(st, gaps)))
    groups.append(_group("A song's route", _route_rows(mt, hw, st, gaps)))
    groups.append(_group("The last song", _song_rows(st.get("last_song"))))
    groups.append(_group("Engines this limb knows", _declared_rows(st)))

    ready = bool(st.get("ready"))
    missing = list(st.get("weights_missing") or [])

    if st.get("app"):
        lights.append(_light(
            "green" if st.get("entry_present") and st.get("python_present") else "amber",
            "Engine",
            str(st.get("engine_label") or st.get("engine") or "music engine"),
            f"{_short(st.get('app'))}"
            + ("" if st.get("entry_present") else " · its entry script is missing")
            + ("" if st.get("python_present") else " · its own python is missing"),
        ))
    else:
        lights.append(_light("amber", "Engine", "no engine on this machine",
                             "the limb will refuse by name (`no_music_engine`) until one is installed or `music_root` points at one"))

    if missing:
        lights.append(_light("amber", "Weights", f"{len(missing)} of {len(st.get('weights') or [])} not on disk",
                             ", ".join(str(m) for m in missing)))
    elif st.get("weights"):
        total = sum(int(w.get("bytes") or 0) for w in st["weights"])
        lights.append(_light("green", "Weights", f"{len(st['weights'])} of {len(st['weights'])} on disk", _size(total)))
    else:
        lights.append(_light("grey", "Weights", "unchecked", "no engine resolved, so no weights could be listed"))

    route = st.get("route_now") or {}
    where = str(route.get("route") or route.get("where") or "unknown")
    lights.append(_light(
        "green" if where in ("cuda", "offload") else "amber",
        "Route",
        where,
        str(route.get("why") or "the route owner gave no reason, which is itself the finding"),
        "the card reading is taken at each refresh, so a busy card changes this light",
    ))

    last = st.get("last_song")
    if last:
        lights.append(_light("green", "Last song", _size(last.get("bytes")),
                             f"{_short(last.get('path'))} · written {last.get('when') or 'at an unrecorded time'}"))
    else:
        lights.append(_light("green", "Last song", "none yet",
                             "a true fact on a fresh kit, not a fault - the first song this kit writes fills this row"))

    if ready and not gaps:
        state, state_text = "green", (
            f"engine ready · {len(st.get('weights') or [])} weight file(s) on disk · route {where}"
            + (" · a song has been made" if last else " · no song made yet")
        )
    elif not st.get("app"):
        state, state_text = "amber", "no music engine on this machine, so this card can only report the search"
    else:
        state, state_text = "amber", (
            f"engine {_short(st.get('app'))} · {len(missing)} weight file(s) missing"
            if missing else f"engine present but {len(gaps)} fact(s) could not be read"
        )
    if gaps and state == "green":
        state, state_text = "amber", f"read with {len(gaps)} named gap(s)"

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
        "sources": [
            "music_tools.music_status() — the engine, its weights, the route, the last song",
            "music_tools.engine_state() — the install resolution and the declared engine table",
            "hardware — the live card reading, which this card never re-derives",
            "paths — where the kit's own folders are, for the short paths",
        ],
        "routes": [{"method": "GET", "path": ROUTE, "purpose": "this card"}],
        "refresh_s": 20,
    }


# ------------------------------------------------------------------------------------------------
# module host surface
# ------------------------------------------------------------------------------------------------
def data(ctx: Any, req: Any) -> None:
    req.json(200, build(ctx))


def health(ctx: Any) -> dict[str, Any]:
    """Can this card read the music owner it reports on, on THIS tree, right now?"""
    mt = _owner("music_tools")
    if mt is None:
        return {"ok": False, "detail": "`music_tools` does not import on this tree, so this card would read empty"}
    panel = build(ctx)
    if not (panel.get("groups") or []):
        return {"ok": False, "detail": "the music owner imports but answered with nothing, so this card would read empty"}
    return {
        "ok": True,
        "detail": (
            f"state {panel.get('state')} · {len(panel.get('groups') or [])} group(s), "
            f"{len(panel.get('missing') or [])} named gap(s)"
        ),
    }


def register(ctx: Any) -> dict[str, Any]:
    """The routes and the pane this card claims. It claims no limb: the limb lives in limbs.py."""
    return {
        "routes": [("GET", ROUTE, data)],
        "limbs": [],
        "panes": [
            {
                "id": "dock.music",
                "slot": "dock",
                "title": "Song making",
                "data": f"GET {ROUTE}",
                "refresh_s": 20,
                "inner_scroll": False,
            }
        ],
        "health": health,
    }
