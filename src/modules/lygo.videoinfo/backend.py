"""lygo.videoinfo — what this console can make a moving picture WITH, in one card.

Δ9Φ963-LYGO-VIDEOINFO-v1

WHAT THIS ANSWERS
    The fourth of the making cards, beside the picture engine, the voice engine and the song engine:
    **what can this machine make a CLIP with, and what did it last make?** Questions the operator
    could not answer anywhere in one place:

        the engine      which video engine this tree reaches, where its install is, whether its own
                        entry script and its own python are there
        the adapter     whether the running engine actually registered the GGUF loader class, which
                        is a DIFFERENT fact from the engine existing
        its weights     every declared weight file - diffusion, text encoder, VAE - by name and by
                        size, or the names of the ones that are not on disk
        the recipes     which models are drivable right now, their frame and resolution ceilings on
                        this card, and the measured cost of the one that has been timed
        the last clip   the newest clip this kit really wrote, with what it recorded about itself

WHY IT EXISTS AS A CARD AND NOT A LIMB
    The limb (`video_generate`) makes a clip; this card is the read-out that says whether a clip is
    possible BEFORE one is asked for, and whether the last one is really on disk. A render is minutes
    on this hardware, so an operator who asks for one on a machine with no engine or no weights
    should learn that from a card, not by waiting.

WHY THE THREE FACTS ARE KEPT APART
    A video engine can be unusable in three separate ways - the app missing, the GGUF loader not
    registered, a weight file absent - and they fail differently, are fixed differently, and are
    owned by different people. Collapsing them into one `installed` flag is how a missing weight
    reads as a missing engine, and how a select says "usable" while a render refuses.

WHAT IT DELIBERATELY DOES NOT DO
    * No new state and no writes anywhere: `owns: []`, `gate: none`, and its own test scans this
      file for write calls. The clips it reports on were written by the limb.
    * No re-derivation. The engine, the adapter, the weights, the recipes and the card reading are
      all asked of their owner (`video_harness`) - this card prints what it is told.
    * No claim that a clip was made when none was. "No clip rendered yet on this kit" is a TRUE
      fact on a fresh kit and stays green, exactly as an empty vault does on the memory card.
    * Nothing is started, stopped or interrupted from here. Reporting a stopped engine is not a
      reason to start one: the card reads, the limb runs.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

SIGNATURE = "Δ9Φ963-LYGO-VIDEOINFO-v1"
MID = "lygo.videoinfo"
ROUTE = "/api/videoinfo"

#: The card's own name for the owner it reports on. It never imports the engine itself: the
#: first question is always whether its owner imports on THIS tree.
OWNER = "video_harness"


def _at() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _size(n: Any) -> str:
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.2f} {unit}"
        n /= 1024.0
    return "-"


def _short(path: Any) -> str:
    text = str(path or "")
    if not text:
        return "-"
    try:
        parts = Path(text).parts
    except (TypeError, ValueError):
        return text
    return str(Path(*parts[-2:])) if len(parts) > 2 else text


def _row(k: str, v: Any, note: str = "", dot: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {"k": str(k), "v": "" if v is None else str(v)}
    if note:
        row["note"] = str(note)
    if dot:
        row["dot"] = str(dot)
    return row


def _group(title: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"title": str(title), "rows": [r for r in rows if r]}


def _light(state: str, label: str, text: str, detail: str = "",
           next_change: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"state": state, "label": label, "text": text}
    if detail:
        out["detail"] = detail
    if next_change:
        out["next_change"] = next_change
    return out


def _owner(name: str) -> Any | None:
    """Import the module that owns the facts. A card never reaches past its owner."""
    try:
        import importlib

        return importlib.import_module(name)
    except Exception:
        return None


def _engine_rows(vh: Any, app: dict[str, Any], adapter: dict[str, Any],
                 gaps: list[str]) -> list[dict[str, Any]]:
    root = app.get("root") or ""
    if not root:
        gaps.append("engine: no app_root is declared")
    main_ok = bool(app.get("present"))
    py_ok = bool(app.get("python_present"))
    if root and not main_ok:
        gaps.append(f"engine: no main.py under {root}")
    if root and main_ok and not py_ok:
        gaps.append("engine: the install has no .venv python")
    loader = adapter.get("present")
    if root and main_ok and not loader:
        gaps.append("adapter: the GGUF loader class is not registered")
    return [
        _row("install", _short(root) if root else "not declared",
             root if root else "set video.app_root in config/local.json or LYGO_VIDEO_APP",
             "ok" if main_ok else "bad"),
        _row("entry", "main.py" if main_ok else "absent",
             "" if main_ok else "that folder is not a ComfyUI install",
             "ok" if main_ok else "bad"),
        _row("python", "present" if py_ok else "absent",
             "" if py_ok else "the install's own .venv python",
             "ok" if py_ok else "bad"),
        _row("gguf loader", "registered" if loader else "not registered",
             "" if loader else "install the ComfyUI-GGUF plugin",
             "ok" if loader else "bad"),
    ]


def _weight_rows(states: list[dict[str, Any]], gaps: list[str]) -> list[dict[str, Any]]:
    """One row per weight file, ACROSS every recipe, deduped by name.

    The text encoder and the VAE are shared by both recipes; printing them twice would read as
    two different missing files.
    """
    seen: dict[str, dict[str, Any]] = {}
    for state in states:
        for entry in (state.get("weights") or {}).get("files") or []:
            name = str(entry.get("name") or "")
            if not name or name in seen:
                continue
            seen[name] = entry
    rows = []
    for name, entry in seen.items():
        ok = bool(entry.get("present"))
        if not ok:
            gaps.append(f"weights: {name} is not on disk")
        rows.append(_row(entry.get("role") or "weight", name,
                         _size(entry.get("bytes")) if ok else "not on disk",
                         "ok" if ok else "bad"))
    return rows


def _recipe_rows(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for s in states:
        lim = s.get("limits") or {}
        d = s.get("defaults") or {}
        mea = s.get("measured") or {}
        drivable = bool(s.get("drivable"))
        note = f"{s.get('license', '?')} · {d.get('width')}x{d.get('height')} max · "
        note += f"{d.get('steps')} steps · <= {lim.get('max_length')} frames"
        if mea.get("wall_s_49f"):
            note += f" · measured {mea['wall_s_49f']}s for 49 frames"
        elif mea.get("wall_s_49f") is None and drivable:
            note += " · not yet timed on this kit"
        rows.append(_row(s.get("id"), "drivable" if drivable else "not drivable", note,
                         "ok" if drivable else "bad"))
    return rows


def _last_clip(vh: Any, gaps: list[str]) -> dict[str, Any] | None:
    """The newest clip this kit really wrote. Proven present and non-empty, or None."""
    try:
        out = Path(vh.workspace_dir())
        files = [p for p in out.glob("*.mp4") if p.is_file()]
    except OSError as exc:
        gaps.append(f"clips: the output folder could not be read ({exc})")
        return None
    if not files:
        return None
    newest = max(files, key=lambda p: p.stat().st_mtime)
    size = newest.stat().st_size
    if size <= 0:
        gaps.append(f"clips: {newest.name} is on disk but empty")
        return None
    return {"name": newest.name, "path": str(newest), "bytes": size,
            "when": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(newest.stat().st_mtime))}


def build(ctx: Any = None) -> dict[str, Any]:
    """The whole card. Every fact asked of its owner; nothing re-derived, nothing invented."""
    del ctx  # read-only card: the request context carries nothing this needs
    vh = _owner(OWNER)
    if vh is None:
        return {"ok": False, "groups": [], "missing": [OWNER], "generated": _at(),
                "detail": f"`{OWNER}` does not import on this tree, so this card would read empty"}

    gaps: list[str] = []
    try:
        app = vh.app_state()
    except Exception as exc:  # noqa: BLE001 - a card must not take the console down
        gaps.append(f"engine state unreadable: {exc}")
        app = {}
    try:
        adapter = vh.adapter_state()
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"adapter state unreadable: {exc}")
        adapter = {}
    try:
        states = vh.models()
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"model state unreadable: {exc}")
        states = []
    try:
        live = vh.probe()
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"the engine could not be probed: {exc}")
        live = {}

    groups = [
        _group("Engine", _engine_rows(vh, app, adapter, gaps)),
        _group("Weights", _weight_rows(states, gaps)),
        _group("Recipes", _recipe_rows(states)),
    ]

    last = _last_clip(vh, gaps)
    if last:
        clip_rows = [
            _row("clip", last["name"], f"{_size(last['bytes'])} · {last['when']}", "ok"),
            _row("path", _short(last["path"]), last["path"]),
        ]
    else:
        # A kit that has made no clip yet is not broken, and saying so is a TRUE fact. Falling
        # through to a subscript here is how a card raises on a fresh tree instead of answering.
        clip_rows = [_row("clip", "no clip rendered yet on this kit",
                          "nothing has been rendered here - that is not a fault", "ok")]
    groups.append(_group("Last clip", clip_rows))

    if live.get("up"):
        live_rows = [
            _row("serving", "yes", f"{live.get('base')} · {live.get('version')}", "ok"),
            _row("device", live.get("device") or "-",
                 f"{live.get('vram_free_gb')} of {live.get('vram_total_gb')} GB VRAM free"),
        ]
        groups.append(_group("Live engine", live_rows))
        state, label = "ok", "ready"
        text = "an engine is serving and the weights for the recipes above are on disk"
        detail = ""
        next_change = "a clip can be asked for now"
    else:
        drivable = [s.get("id") for s in states if s.get("drivable")]
        state = "warn" if drivable else "bad"
        label = "engine stopped" if drivable else "not usable"
        text = ("the engine is not running - a render starts it on demand and it takes a few "
                "minutes to come up on this machine"
                if drivable else
                "this machine cannot make a clip right now; the named gaps above say why")
        detail = str(live.get("reason") or "")
        next_change = "the limb starts the engine itself" if drivable else ""

    return {
        "ok": not gaps,
        "generated": _at(),
        "light": _light(state, label, text, detail, next_change),
        "groups": groups,
        "missing": gaps,
    }


def data(ctx: Any, req: Any) -> None:
    req.json(200, build(ctx))


def health(ctx: Any) -> dict[str, Any]:
    """Can this card read the owner it reports on, on THIS tree, right now?"""
    vh = _owner(OWNER)
    if vh is None:
        return {"ok": False,
                "detail": f"`{OWNER}` does not import on this tree, so this card would read empty"}
    panel = build(ctx)
    if not (panel.get("groups") or []):
        return {"ok": False,
                "detail": "the video owner imports but answered with nothing, so this card would read empty"}
    return {"ok": True, "detail": "the video owner answers", "gaps": len(panel.get("missing") or [])}


def register(ctx: Any) -> dict[str, Any]:
    """The route and the pane this card claims. It claims no limb: the limb lives in limbs.py.

    The shape is the host's contract, not decoration: `routes` is a list of (method, path, handler)
    triples and the method+path must appear in module.json's own `routes`, because the manifest is
    the contract and a route it does not declare does not exist. Returning anything else - a
    `{"ok": True}` receipt, say - reads as "registered nothing" and the module is refused by name.
    """
    del ctx  # a read-only card needs nothing from the request context to register
    return {
        "routes": [("GET", ROUTE, data)],
        "limbs": [],
        "panes": [
            {
                "id": "dock.video",
                "slot": "dock",
                "title": "Video making",
                "data": f"GET {ROUTE}",
                "refresh_s": 30,
                "inner_scroll": False,
            }
        ],
        "health": health,
    }
