"""lygo.filmctrl - the FilmCraft controls card: boot it, serve it, stop it, and the record of each.

Δ9Φ963-LYGO-FILMCTRL-v1

WHAT THIS IS
    The actuator half of the film pair. `lygo.filminfo` reads the facts; this module is where
    FilmCraft is actually started, served and stopped from the console - and it is the one place the
    console's own buttons speak to. It edits nothing and renders nothing itself: every call goes
    through `film_tools`, which is the same harness the `film` limb uses, so the button and the agent
    cannot disagree about what happened.

A SMALL STRIP OF BUTTONS, ON PURPOSE
    The console is not an editor and this build has no intention of becoming one. The pane publishes
    four buttons - boot the editor, serve the offline browser editor, stop either - and the full
    action table (inspect, run, export, render, describe, presets) is reachable behind the same route
    and through the `film` limb, where the agent uses it. A card that grew an editing UI here would
    be a second, worse editor.

WHAT IT WILL NOT DO
    * It will not stop an editor or a helper it did not start. A window the operator launched belongs
      to the operator; `stop` says so by name instead of taking their app down.
    * It will not claim a process came up because it spawned one. The control port answering is the
      proof; a boot that dies on start is reported with its log tail, not as success.
    * It will not download anything. A kit with no payload refuses by name and names
      `scripts/fetch_filmcraft.ps1` - fetching is the operator's decision, never a click's side effect.
    * It will not export or edit the operator's own project without consent. Anything outside this
      kit's `workspace/film` refuses unless the caller passes consent=true.
    * It will not answer 200 for a thing that did not happen: a refusal is answered with the code the
      caller reads (403 gate, 409 refused action, 400 unknown action).
"""
from __future__ import annotations

import time
from typing import Any

SIGNATURE = "\u03949\u03a6963-LYGO-FILMCTRL-v1"
MID = "lygo.filmctrl"
ROUTE = "/api/film"
ROUTES: tuple[tuple[str, str], ...] = (("GET", ROUTE), ("POST", ROUTE))
OWNER = "film_tools"

#: Everything the route and the limb accept. The PANEL publishes only the four buttons below.
ACTIONS = ("boot", "stop", "web", "web_stop", "doctor", "inspect", "commands", "describe",
           "run", "export", "render", "presets", "refresh")

#: The small strip. `label` is what the button says, `action` is what it posts.
PANEL_ACTIONS: tuple[dict[str, str], ...] = (
    {"action": "boot", "label": "Boot FilmCraft",
     "title": "start the desktop editor with its control channel open on loopback"},
    {"action": "web", "label": "Browser editor",
     "title": "serve the offline WebAssembly editor from this kit and open it"},
    {"action": "stop", "label": "Stop editor",
     "title": "stop the editor THIS console started - another window is left alone"},
    {"action": "web_stop", "label": "Stop web",
     "title": "stop the browser editor this console is serving"},
)


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


# --- the card -----------------------------------------------------------------------------------


def build(ctx: Any, cheap: bool = False) -> dict[str, Any]:
    """The card the strip polls. Reads only - every action has to be asked for.

    `cheap=True` is the boot-check shape `health()` uses: local facts, no engine process started.
    """
    import film_tools as ft

    st = ft.state(cheap=cheap)
    inst = st.get("installed") or {}
    run = st.get("editor") or {}
    web = st.get("web") or {}
    gaps: list[str] = []

    usable = bool(inst.get("cli_present") or inst.get("app_present"))
    if not inst.get("web_present"):
        gaps.append("the browser editor payload is not installed here; scripts/fetch_filmcraft.ps1 -Payload web fetches it")
    if not usable:
        gaps.append("nothing is installed here to start; scripts/fetch_filmcraft.ps1 installs the pinned release")

    if run.get("answers"):
        editor_light = _light("green", "Editor", f"up on port {run.get('port')}",
                              detail=f"started by {run.get('started_by') or 'someone'} \u00b7 this console may drive it",
                              next_change="ask it something: the film limb's `editor` action")
    elif not inst.get("app_present"):
        editor_light = _light("amber", "Editor", "no editor to start",
                              detail="the desktop payload is not in this kit", next_change="run scripts/fetch_filmcraft.ps1")
    else:
        editor_light = _light("grey", "Editor", "not running",
                              detail="the Boot button starts it and waits for its control channel",
                              next_change="one click")

    if web.get("serving"):
        web_light = _light("green", "Browser editor", "served", detail=str(web.get("url") or ""))
    elif web.get("available"):
        web_light = _light("grey", "Browser editor", "ready to serve", detail="the payload is on disk")
    else:
        web_light = _light("amber", "Browser editor", "no payload", detail="nothing to serve yet")

    last_exports = st.get("exports") or []
    if last_exports:
        newest = last_exports[0]
        job_light = _light("green", "Last write", str(newest.get("name")),
                           detail=f"{newest.get('mb')} MB \u00b7 {newest.get('at')} \u00b7 {_short(str(newest.get('path') or ''))}",
                           next_change="the file is on disk - it is not a claim")
    else:
        job_light = _light("grey", "Last write", "nothing written yet",
                           detail="no clip has been exported from this kit",
                           next_change="the film limb's `export`, or the agent asking for a cut")

    rows_actions = [_row(a["label"], a["action"], a["title"]) for a in PANEL_ACTIONS]
    rows_actions.append(_row("everything else", "film limb",
                             "inspect, run, export, render, describe, presets, commands - for the agent"))
    rows_actions.append(_row("nothing is downloaded", "by a click",
                             "a kit with no payload refuses and names scripts/fetch_filmcraft.ps1"))

    chan_rows = [
        _row("control port", str(run.get("port") or "-"),
             "opened by the Boot button, loopback only, never left open behind your back", "ok" if run.get("answers") else ""),
        _row("started by this console", "yes" if run.get("ours") else "no",
             "stop only touches a process this console started"),
    ]
    if run.get("answers") and not run.get("ours"):
        chan_rows.append(_row("note", "another copy is running",
                             "its window belongs to the operator; this console reports it and leaves it alone", "amber"))

    ws_rows = [
        _row("film workspace", _short(str(st.get("workspace") or "")), "inside this kit: projects, media, exports, renders"),
        _row("exports", str(len(st.get("exports") or [])), "clips the engine has written"),
        _row("still frames", str(len(st.get("renders") or [])), "single frames from the program monitor"),
        _row("needs consent", "anything outside the workspace",
             "a project of your own is never edited by an agent without an explicit yes"),
    ]

    if not usable:
        state, text = "amber", "nothing to start - the pinned release is not in this kit"
    elif run.get("answers"):
        state, text = "green", f"an editor is up on port {run.get('port')} and this console can drive it"
    elif web.get("serving"):
        state, text = "green", "the browser editor is served here; the desktop editor is not running"
    else:
        state, text = "green", "ready - nothing is running until a button or the agent asks for it"

    return {
        "signature": SIGNATURE,
        "at": _at(),
        "state": state,
        "state_text": text,
        "lights": [editor_light, web_light, job_light],
        "groups": [
            _group("The buttons", rows_actions),
            _group("What this console started", chan_rows),
            _group("Where its work lands", ws_rows),
        ],
        "actions": [dict(a) for a in PANEL_ACTIONS],
        "missing": gaps,
    }


def data(ctx: Any, req: Any) -> None:
    """GET /api/film - the card, read-only."""
    req.json(200, build(ctx))


# --- the gate -----------------------------------------------------------------------------------


def _gate(text: str) -> dict[str, Any] | None:
    """The kernel's p0 gate over text that is about to be written or acted on. None means it passed."""
    try:
        from p0_hook import gate_prompt
    except Exception:
        return {"verdict": "UNKNOWN", "reason": "the p0 gate could not be imported, so nothing was sent"}
    try:
        v = gate_prompt(str(text)[:8000])
    except Exception as exc:  # noqa: BLE001 - a gate that raised is not a pass
        return {"verdict": "UNKNOWN", "reason": f"the p0 gate raised: {str(exc)[:120]}"}
    if v.get("verdict") == "QUARANTINE":
        return v
    return None


# --- the actions --------------------------------------------------------------------------------


def _act(action: str, body: dict[str, Any], ctx: Any) -> tuple[int, dict[str, Any]]:
    """Run one action, answer (http code, payload). Never raises: a failure is a named answer."""
    import film_tools as ft

    if action == "boot":
        out = ft.boot(by="console")
        if out.get("ok"):
            return 200, {"ok": True, "action": "boot", **out,
                         "why": out.get("why") or "the editor is up"}
        return 409, {"ok": False, "action": "boot", **out}

    if action == "stop":
        out = ft.stop(by="console")
        if out.get("ok"):
            return 200, {"ok": True, "action": "stop", **out}
        return 409, {"ok": False, "action": "stop", **out}

    if action == "web":
        out = ft.web_start()
        if out.get("ok"):
            return 200, {"ok": True, "action": "web", **out}
        return 409, {"ok": False, "action": "web", **out}

    if action == "web_stop":
        out = ft.web_stop()
        if out.get("ok"):
            return 200, {"ok": True, "action": "web_stop", **out}
        return 409, {"ok": False, "action": "web_stop", **out}

    if action == "doctor":
        return 200, ft.film_limb({"action": "doctor"})

    if action == "refresh":
        got = ft.commands(refresh=True)
        return (200 if got.get("ok") else 409), {"ok": bool(got.get("ok")), "action": "refresh",
                                                 "commands": len(got.get("rows") or []),
                                                 "version": got.get("version") or "",
                                                 "why": got.get("why") or ""}

    if action == "commands":
        q = str(body.get("query") or "")
        got = ft.find_commands(q, int(body.get("limit") or 40))
        return (200 if got.get("ok") else 409), {"action": "commands", **got}

    if action == "describe":
        cmd = str(body.get("command") or body.get("id") or "").strip()
        if not cmd:
            return 400, {"ok": False, "error": "no_command", "action": "describe",
                         "why": "name the command, or use `commands` with a query first"}
        got = ft.describe(cmd)
        return (200 if got.get("ok") else 404), {"action": "describe", **got}

    if action == "inspect":
        got = ft.inspect(project=body.get("project"), what=str(body.get("what") or "both"),
                         demo=bool(body.get("demo")))
        return (200 if got.get("ok") else 409), {"action": "inspect", **got}

    if action == "presets":
        got = ft.presets(str(body.get("query") or ""))
        return (200 if got.get("ok") else 409), {"action": "presets", **got}

    if action == "run":
        out_name = str(body.get("project") or "")
        gate = _gate(f"{out_name} {' '.join(str(s) for s in (body.get('steps') or []))}")
        if gate:
            return 403, {"ok": False, "error": "gate_refused", "action": "run", "gate": gate}
        steps = body.get("steps") or []
        if isinstance(steps, str):
            steps = [{"id": steps}]
        got = ft.run(steps if isinstance(steps, list) else [], project=body.get("project"),
                     save=bool(body.get("save")), demo=bool(body.get("demo")),
                     consent=bool(body.get("consent")))
        return (200 if got.get("ok") else 409), {"action": "run", **got}

    if action == "export":
        out_name = str(body.get("out") or body.get("name") or "")
        gate = _gate(f"{out_name} {str(body.get('project') or '')}")
        if gate:
            return 403, {"ok": False, "error": "gate_refused", "action": "export", "gate": gate}
        got = ft.export(project=body.get("project"), out_name=out_name,
                        preset=str(body.get("preset") or ""), start=body.get("start"), end=body.get("end"),
                        settings=body.get("settings") if isinstance(body.get("settings"), dict) else None,
                        allow_offline=bool(body.get("allow_offline")), demo=bool(body.get("demo")))
        return (200 if got.get("ok") else 409), {"action": "export", **got}

    if action == "render":
        gate = _gate(str(body.get("out") or "frame"))
        if gate:
            return 403, {"ok": False, "error": "gate_refused", "action": "render", "gate": gate}
        got = ft.render_frame(project=body.get("project"), seconds=float(body.get("seconds") or 0),
                              out_name=str(body.get("out") or ""), scale=body.get("scale"),
                              demo=bool(body.get("demo")))
        return (200 if got.get("ok") else 409), {"action": "render", **got}

    return 400, {"ok": False, "error": "unknown_action", "action": action, "actions": list(ACTIONS)}


def control(ctx: Any, req: Any) -> None:
    """POST /api/film - one action per call, answered with the code the caller reads."""
    try:
        body = req.body(200_000) or {}
    except Exception as exc:  # noqa: BLE001
        req.json(400, {"ok": False, "error": "unreadable_body", "why": str(exc)[:200]})
        return
    action = str(body.get("action") or "").strip().lower()
    if action not in ACTIONS:
        req.json(400, {"ok": False, "error": "unknown_action", "action": action, "actions": list(ACTIONS)})
        return
    code, payload = _act(action, body, ctx)
    # Every action that changes the machine gets a receipt: the same record a limb call leaves, so
    # "who started that editor" is answerable afterwards without guessing.
    if action in ("boot", "stop", "web", "web_stop", "export", "render", "run"):
        try:
            ctx.receipt(prompt=f"film:{action} {str(body.get('out') or body.get('project') or '')[:180]}",
                        output=str(payload.get("file") or payload.get("url") or payload.get("pid") or ""),
                        extra={"module": MID, "action": action, "ok": bool(payload.get("ok")),
                               "port": payload.get("port"), "why": str(payload.get("why") or "")[:200]})
        except Exception as exc:  # noqa: BLE001 - a receipt may never take the action down
            ctx.log(f"receipt for film:{action} failed: {exc}")
    payload["card"] = build(ctx)
    req.json(code, payload)


def health(ctx: Any) -> dict[str, Any]:
    """Can this module reach its owner and build a card on THIS tree, right now? Cheap BY DESIGN -
    see the read-out card's `health`: this runs inside the host's boot, so no engine process starts."""
    try:
        import film_tools  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"`{OWNER}` does not import on this tree ({exc}), so this card would read empty"}
    panel = build(ctx, cheap=True)
    if not (panel.get("actions") or []):
        return {"ok": False, "detail": "the film owner answers but published no buttons, so the strip would be empty"}
    return {"ok": True, "detail": "the film owner answers and the strip has its buttons"}


def register(ctx: Any) -> dict[str, Any]:
    """The two routes and the pane. It registers NO limb: `film` already exists in limbs.py and is
    declared as owned in this module's manifest, so listing it here would register the same tool
    twice and change the tool set on load. The limb is the agent's door; this route is the
    console's, and both call the same harness."""
    return {
        "routes": [("GET", ROUTE, data), ("POST", ROUTE, control)],
        "limbs": [],
        "panes": [
            {
                "id": "dock.filmctl",
                "slot": "dock",
                "title": "FilmCraft controls",
                "data": f"GET {ROUTE}",
                "refresh_s": 15,
                "inner_scroll": False,
            }
        ],
        "health": health,
    }
