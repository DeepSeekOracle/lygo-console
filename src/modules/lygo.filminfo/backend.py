"""lygo.filminfo - the FilmCraft facts card: what is on this kit, and what is answering right now.

Δ9Φ963-LYGO-FILMINFO-v1

WHAT THIS IS
    The read-out half of the film pair. `lygo.filmctrl` is where an editor is actually started; this
    card answers the question the operator asks first - is it here, which parts of it are here, is
    anything running, and what has it written.

WHY EVERY ROW HAS AN OWNER
    Nothing here is derived. `installed()` says what is on disk, `version()` asks the CLI for its own
    version string, `running()` asks the control port, `web_status()` asks the helper's port,
    `commands()` asks the CLI for its catalogue, and the workspace rows are read off the disk. A card
    that computed any of this itself could disagree with the limb that acts on it, and the operator
    would have two truths.

WHAT IT WILL NOT DO
    * It never claims FilmCraft is installed because a folder exists. The editor, the headless CLI
      and the WebAssembly payload are named SEPARATELY, by path and size: a kit can hold one and not
      the others, and collapsing them is how a missing engine reads as a missing card.
    * It never calls a missing payload a fault. A kit with no FilmCraft is a true reading with the
      fetcher named - amber, because the sibling card's buttons cannot work until it runs, but never
      red: nothing is broken, something is not fetched yet.
    * It never says "running" because a record exists. The control port answering is the proof; the
      pid in the record is only what this console started.
    * It writes nothing, owns no state and gates nothing. Reads only - its test scans this source
      for a write call.
"""
from __future__ import annotations

import time
from typing import Any

SIGNATURE = "\u03949\u03a6963-LYGO-FILMINFO-v1"
MID = "lygo.filminfo"
ROUTE = "/api/filminfo"
OWNER = "film_tools"


# --- small builders -----------------------------------------------------------------------------


def _at() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _light(state: str, label: str, text: str, detail: str = "", next_change: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"state": state, "label": label, "text": text}
    if detail:
        out["detail"] = detail
    if next_change:
        out["next_change"] = next_change
    return out


def _row(k: str, v: Any, note: str = "", dot: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"k": k, "v": "" if v is None else str(v)}
    if note:
        out["note"] = note
    if dot:
        out["dot"] = dot
    return out


def _group(title: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"title": title, "rows": rows}


def _short(path: str, keep: int = 46) -> str:
    txt = str(path or "")
    return txt if len(txt) <= keep else "\u2026" + txt[-(keep - 1):]


def _size(mb: float) -> str:
    return f"{mb:g} MB"


# --- the card -----------------------------------------------------------------------------------


def build(ctx: Any, cheap: bool = False) -> dict[str, Any]:
    """The whole card. Every value traced to the owner that holds it.

    `cheap=True` is the boot-check shape the module's `health()` uses: local facts only, no engine
    process. The route (`data`) calls it the full way, because then someone is reading the card.
    """
    import film_tools as ft

    st = ft.state(with_commands=not cheap, cheap=cheap)
    inst = st.get("installed") or {}
    run = st.get("editor") or {}
    web = st.get("web") or {}
    cat = st.get("commands") or {}
    gaps: list[str] = []

    # ---- the install, part by part ----------------------------------------------------------
    have_any = bool(inst.get("app_present") or inst.get("cli_present") or inst.get("web_present"))
    inst_rows = [
        _row("folder", _short(inst.get("dir") or ""), str(inst.get("dir_from") or "")),
    ]
    if inst.get("cli_present"):
        inst_rows.append(_row("headless engine", f"yes \u00b7 {_size(inst.get('cli_mb') or 0)}",
                              str(inst.get("cli") or ""), "ok"))
    else:
        inst_rows.append(_row("headless engine", "absent", f"nothing at {_short(str(inst.get('cli') or ''))}",
                              "amber"))
        gaps.append(f"the headless engine is not installed: {inst.get('cli')}")
    if inst.get("app_present"):
        inst_rows.append(_row("desktop editor", f"yes \u00b7 {_size(inst.get('app_mb') or 0)}",
                              str(inst.get("app") or ""), "ok"))
    else:
        inst_rows.append(_row("desktop editor", "absent", f"nothing at {_short(str(inst.get('app') or ''))}",
                              "amber"))
        gaps.append(f"the desktop editor is not installed: {inst.get('app')}")
    if inst.get("web_present"):
        inst_rows.append(_row("browser editor", f"yes \u00b7 {_size(inst.get('web_mb') or 0)}",
                              "the same engine and UI compiled to WebAssembly, served by this kit", "ok"))
    else:
        inst_rows.append(_row("browser editor", "absent", "fetch it with -Payload web if you want it", ""))
    inst_rows.append(_row("pinned release", str(st.get("pinned") or ""),
                          "the version this kit's PIN.json names; a different install is reported, never refused"))

    # ---- is anything answering --------------------------------------------------------------
    if run.get("answers"):
        editor_light = _light("green", "Editor", f"answering on 127.0.0.1:{run.get('port')}",
                              detail=f"started by {run.get('started_by') or 'someone'} at {run.get('started_at') or '?'}",
                              next_change="the agent can drive it over its control channel")
    else:
        editor_light = _light("grey", "Editor", "not running",
                              detail="the window is closed; the headless engine needs no window at all",
                              next_change="the Boot button on the controls card starts it")

    if web.get("serving"):
        web_light = _light("green", "Browser editor", "served here",
                           detail=str(web.get("url") or ""), next_change="open the URL, or stop it from the controls card")
    elif web.get("available"):
        web_light = _light("grey", "Browser editor", "not served",
                           detail="the payload is on disk; the controls card can serve it in one click")
    else:
        web_light = _light("grey", "Browser editor", "no payload",
                           detail="nothing to serve until the web payload is fetched")

    if inst.get("cli_present") and st.get("version"):
        engine_light = _light("green", "Engine", f"filmcraft {st.get('version')}",
                              detail=f"{cat.get('count') or 0} commands, catalogue read from {cat.get('from') or 'the CLI'}")
    elif inst.get("cli_present"):
        engine_light = _light("amber", "Engine", "installed but silent",
                              detail="the CLI is on disk and did not answer with a version",
                              next_change="run scripts/fetch_filmcraft.ps1 -Force to replace it")
    else:
        engine_light = _light("amber", "Engine", "not installed",
                              detail="nothing to drive yet", next_change="run scripts/fetch_filmcraft.ps1")

    # ---- the editor's own channel ----------------------------------------------------------
    if run.get("answers"):
        chan_rows = [
            _row("control port", str(run.get("port")), "loopback only, JSON lines, one request per line", "ok"),
            _row("started by", str(run.get("started_by") or "-"), str(run.get("started_at") or "")),
            _row("pid", str(run.get("pid") or "-"),
                 "this console's own record of what it started" if run.get("ours") else
                 "not started by this console, so this console will not stop it"),
        ]
    else:
        chan_rows = [
            _row("control port", str(run.get("port") or "-"), "closed: no editor is listening"),
            _row("how to open it", "Boot FilmCraft", "the controls card, or the film limb's `open`"),
        ]

    # ---- the catalogue ---------------------------------------------------------------------
    if cat.get("ok"):
        cat_rows = [
            _row("version", str(cat.get("version") or ""), "asked of the CLI itself, not assumed", "ok"),
            _row("commands", str(cat.get("count") or 0),
                 "run, list, describe and export all go through the same command engine"),
            _row("read from", str(cat.get("from") or ""),
                 "cached beside the kit; refresh with the film limb's `commands`"),
        ]
    else:
        cat_rows = [_row("commands", "not read yet", "the catalogue is read on first ask", "amber")]

    # ---- what the workspace holds ----------------------------------------------------------
    ws_rows = [_row("folder", _short(str(st.get("workspace") or "")), "inside this kit, so a copy carries it")]
    for label, key, note in (("projects", "projects", "every .fcproj this kit has made or been handed"),
                             ("exports", "exports", "every clip the engine has written"),
                             ("still frames", "renders", "single frames rendered from the program monitor")):
        rows = st.get(key) or []
        if rows:
            newest = rows[0]
            ws_rows.append(_row(label, f"{len(rows)}", f"newest {newest.get('name')} \u00b7 {_size(newest.get('mb') or 0)} \u00b7 {newest.get('at')}", "ok"))
        else:
            ws_rows.append(_row(label, "none yet", "a kit that has made nothing is not broken - it is empty", ""))

    state, state_text = _verdict(inst, st, gaps)

    return {
        "signature": SIGNATURE,
        "at": _at(),
        "state": state,
        "state_text": state_text,
        "lights": [engine_light, editor_light, web_light],
        "groups": [
            _group("What is installed here", inst_rows),
            _group("What the agent can drive", cat_rows),
            _group("The editor right now", chan_rows),
            _group("The film workspace", ws_rows),
        ],
        "missing": gaps,
        "installed": have_any,
        "version": st.get("version") or "",
        "fetch": st.get("fetch") or "",
    }


def _verdict(inst: dict[str, Any], st: dict[str, Any], gaps: list[str]) -> tuple[str, str]:
    """One overall verdict, and the sentence the operator reads. Never greener than the facts."""
    if not (inst.get("app_present") or inst.get("cli_present") or inst.get("web_present")):
        return "amber", "not installed on this kit - scripts/fetch_filmcraft.ps1 installs the pinned release"
    if inst.get("cli_present") and not st.get("version"):
        return "red", "installed but the engine did not answer with a version"
    if not inst.get("cli_present"):
        return "amber", "the editor is here but the headless engine is not, so nothing can be driven in the background"
    ver = st.get("version") or "?"
    if st.get("editor", {}).get("answers"):
        return "green", f"filmcraft {ver} installed; an editor is answering and the agent can drive it"
    if st.get("web", {}).get("serving"):
        return "green", f"filmcraft {ver} installed; the browser editor is served here"
    return "green", f"filmcraft {ver} installed and ready - nothing is running until something is asked for"


def data(ctx: Any, req: Any) -> None:
    """GET /api/filminfo - the card, read-only."""
    req.json(200, build(ctx))


def register(ctx: Any) -> dict[str, Any]:
    """The route and the pane. This card owns no limb and no state: it reads, and nothing else."""
    return {
        "routes": [("GET", ROUTE, data)],
        "limbs": [],
        "panes": [
            {
                "id": "dock.film",
                "slot": "dock",
                "title": "FilmCraft",
                "data": f"GET {ROUTE}",
                "refresh_s": 30,
                "inner_scroll": False,
            }
        ],
        "health": health,
    }


def health(ctx: Any) -> dict[str, Any]:
    """Can this card read the owner it reports on, on THIS tree, right now? Cheap BY DESIGN.

    This runs inside the host's `load()`, i.e. inside the console's boot, so it asks only for local
    facts (`cheap=True`: no engine process started). Measured: the full build spawns the CLI twice
    across the film pair and cost 2.9 s of boot for a version string nobody asked for yet.
    """
    try:
        import film_tools  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"`{OWNER}` does not import on this tree ({exc}), so this card would read empty"}
    panel = build(ctx, cheap=True)
    if not (panel.get("groups") or []):
        return {"ok": False, "detail": "the film owner imports but answered with nothing, so this card would read empty"}
    return {"ok": True, "detail": "the film owner answers", "gaps": len(panel.get("missing") or [])}
