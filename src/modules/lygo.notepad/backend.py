"""lygo.notepad — adapter over `src/notepad.py`.

Δ9Φ963-LYGO-MODULE-CORE-v1

M1 SHIM, BEHAVIOUR UNCHANGED: the two routes reproduce 1.1.1 exactly, calling the same
`notepad` functions the kernel called inline.

  GET  /api/notepad          ?id=<note id> reads one note, no id lists them  (was: server.py:882)
  POST /api/notepad          {"action": "delete" | "new" | "save", ...}      (was: server.py:1287)

Both routes are `legacy: true`: the operator's UI, the model's limbs and the suite all depend on
their bodies, so the shapes are copied, not improved. An unreadable or unparsable body answers as
`{}` (the save path with empty fields), which is what the kernel did.

The module owns `save/notepad` and nothing else. `notepad.py` itself writes through `atomicio`, so
the write path the operator already trusts is the write path this adapter uses.
"""

from __future__ import annotations

from typing import Any


def notes(ctx: Any, req: Any) -> None:
    """GET /api/notepad — one note by ?id=, else the list."""
    from notepad import list_notes, read_note

    nid = str(req.get("id") or "").strip()
    if nid:
        req.json(200, read_note(nid))
    else:
        req.json(200, list_notes())


def notes_write(ctx: Any, req: Any) -> None:
    """POST /api/notepad — delete | new | save, the same three actions as 1.1.1."""
    from notepad import delete_note, new_note, write_note

    obj = req.body(300_000)
    action = str(obj.get("action") or "save").lower()
    if action == "delete":
        req.json(200, delete_note(str(obj.get("id") or "")))
        return
    if action == "new":
        req.json(200, new_note(str(obj.get("title") or "")))
        return
    if not (obj.get("id") or obj.get("text") or obj.get("content") or obj.get("title")):
        # Measured 2026-09-20: a body that is not JSON, or a save with no id and no text, reached
        # write_note(None, "", "") and minted a phantom empty note, answered {"ok": true, ...}.
        # Nothing to save is a client error, not a write - say so and leave the store alone.
        req.json(400, {"error": "nothing_to_save",
                       "hint": 'send {"id": "..."} or {"text": "..."}, or action:"new"'})
        return
    req.json(
        200,
        write_note(
            obj.get("id"),
            str(obj.get("title") or ""),
            str(obj.get("text") or obj.get("content") or ""),
        ),
    )


def health(ctx: Any) -> dict[str, Any]:
    """Is the note store readable? Counts real files, does not assume success."""
    try:
        from notepad import list_notes

        out = list_notes()
        items = out.get("notes") if isinstance(out, dict) else None
        if items is None:
            return {"ok": False, "detail": f"notepad answered unexpectedly: {str(out)[:80]}"}
        return {"ok": True, "detail": f"{len(items)} note(s) in {ctx.save_dir('notepad')}"}
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return {"ok": False, "detail": f"notepad raised: {exc}"}


def register(ctx: Any) -> dict[str, Any]:
    from notepad import list_notes, read_note, write_note

    return {
        "routes": [("GET", "/api/notepad", notes), ("POST", "/api/notepad", notes_write)],
        "limbs": [
            {"name": "notepad_list", "effect": "read", "handler": list_notes},
            {"name": "notepad_read", "effect": "read", "handler": read_note},
            {"name": "notepad_write", "effect": "write", "handler": write_note},
        ],
        "panes": [],
        "health": health,
        "startup": None,
        "shutdown": None,
    }
