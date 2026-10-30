"""lygo.plexoctl - the download buttons: check the bridge, start Plexo, stop ours, stop the downloads.

Δ9Φ963-LYGO-PLEXOCTL-v1

WHAT THIS IS
    The actuator half of the download pair. `lygo.plexoinfo` reads the facts; this module is the door
    the console's own buttons speak to. It edits nothing and renders nothing itself: every call goes
    through `plexo_tools`, the same harness the `plexo` limb calls, so a button and the agent can
    never disagree about what happened.

WHAT IT WILL NOT DO
    * It will not stop a Plexo it did not start. The pid it stops comes from this console's own record
      in save/plexo, and "this console did not start it" is the refusal an operator reads.
    * It will not say Plexo is up because a process was spawned. Its bridge answering on 19288-19290 -
      with app=plexo and the api version this harness speaks - is the proof, and a first launch that
      has not answered yet is reported as exactly that.
    * It will not fetch anything. The 246 MB installer is the operator's decision, taken through
      scripts/fetch_plexo.ps1; a button on a stick with no payload refuses BY NAME and names it.
    * It will not answer 200 for a thing that did not happen: a refusal is answered with the code the
      caller reads (409 for a state that cannot be changed, 403 for a gate that stopped it).
    * It will not hand a link to Plexo without `consent`. Plexo's own model is a human confirm and the
      download runs on the operator's connection, so the button says so and the agent must be told yes.
"""
from __future__ import annotations

import time
from typing import Any

SIGNATURE = "\u03949\u03a6963-LYGO-PLEXOCTL-v1"
MID = "lygo.plexoctl"
ROUTE = "/api/plexo"
ROUTES: tuple[tuple[str, str], ...] = (("GET", ROUTE), ("POST", ROUTE))
OWNER = "plexo_tools"

#: Everything the route and the limb accept. The PANEL publishes only the buttons below.
ACTIONS = ("fetch", "hand_off", "bridge", "open", "stop", "cancel", "cancel_all", "jobs", "status",
           "doctor", "pin", "credit", "probe", "verify")

#: The small strip. `label` is what the button says, `action` is what it posts.
PANEL_ACTIONS: tuple[dict[str, str], ...] = (
    {"action": "bridge", "label": "Check Plexo",
     "title": "ask Plexo's own bridge on 19288-19290 whether it is answering (a ping, not a guess)"},
    {"action": "open", "label": "Start Plexo",
     "title": "start the Plexo this kit pinned, then wait for its bridge - refuses by name when it is not installed"},
    {"action": "stop", "label": "Stop Plexo",
     "title": "stop only the Plexo THIS console started; a window you opened yourself is left alone"},
    {"action": "cancel_all", "label": "Stop downloads",
     "title": "cancel every download running in this session, keeping what arrived and its progress record"},
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


def _short(path: str, keep: int = 52) -> str:
    txt = str(path or "")
    return txt if len(txt) <= keep else "\u2026" + txt[-(keep - 1):]


# --- the card -----------------------------------------------------------------------------------


def build(ctx: Any, cheap: bool = False) -> dict[str, Any]:
    """The strip, its state, and the record behind it. Reads the harness, never re-derives it."""
    import plexo_tools as pt

    st = pt.state(cheap=cheap)
    inst = st.get("installed") or {}
    run = st.get("app") or {}
    reach = (st.get("bridge") or {}) if not cheap else {}
    jobs = st.get("jobs") or []
    live = [j for j in jobs if j.get("state") == "running"]
    ours = bool(run.get("pid")) and bool(run.get("pid_alive"))
    gaps: list[str] = []

    usable = bool(inst.get("app_present"))
    if not usable:
        gaps.append(f"Plexo is not installed here: {inst.get('fetcher')} pins and installs it")

    engine_light = _light("green", "Engine", "LYGO's own downloader is ready",
                          detail="standard library, no install; blocks fetched on several connections at once")
    if not usable:
        app_light = _light("amber", "Plexo", "not installed",
                           detail=str(inst.get("app") or ""),
                           next_change=f"run {inst.get('fetcher')} - nothing is fetched by a click")
    elif (reach or {}).get("state") == "ok":
        app_light = _light("green", "Plexo", f"answering on port {reach.get('port')}",
                           detail=f"Plexo {reach.get('version') or '?'}", next_change="a link can be handed over")
    elif cheap:
        app_light = _light("grey", "Plexo", "not checked at boot", detail="the boot check opens no socket")
    else:
        app_light = _light("grey", "Plexo", "installed, not answering",
                           detail=str((reach or {}).get("why") or ""), next_change="press Start Plexo")

    if live:
        job_light = _light("green", "Downloads", f"{len(live)} running",
                           detail=", ".join(f"{j.get('id')} {j.get('percent')}% {j.get('mbps')} MB/s" for j in live[:3]),
                           next_change="Stop downloads cancels them and keeps what arrived")
    else:
        job_light = _light("grey", "Downloads", "idle", detail="nothing is using the connection")

    rows_actions = [
        _row(a["action"], a["label"], a["title"],
             "ok" if ((a["action"] == "open" and usable) or (a["action"] == "bridge" and (reach or {}).get("state") == "ok")) else "")
        for a in PANEL_ACTIONS
    ]
    chan_rows = [
        _row("bridge ports", ", ".join(str(p) for p in (st.get("bridge") or {}).get("ports", []) or [19288, 19289, 19290]),
             "Plexo listens on the first free one; a port another app holds needs no setting"),
        _row("started by this console", "yes" if ours else "no",
             "stop only touches a process this console started"),
        _row("pid", str(run.get("pid") or "-"),
             str(run.get("started_at") or "") if ours else "nothing of ours is running"),
    ]
    if (reach or {}).get("state") == "ok" and not ours:
        chan_rows.append(_row("note", "another copy is answering",
                              "its window belongs to the operator; this console reports it and leaves it alone", "amber"))

    ws_rows = [
        _row("downloads", _short(str(st.get("root") or "")), "inside this kit, so a copy carries it"),
        _row("running now", str(len(live)), "a job lives as long as this console does; its file stays"),
        _row("hand a link to Plexo", "consent first",
             "the agent must be told yes; the button never hands one over on its own"),
        _row("needs consent", "a file outside workspace/downloads",
             "a download of your own is never written over without an explicit yes"),
    ]

    if not usable and not live:
        state, text = "amber", "LYGO's engine is ready; Plexo is not installed here"
    elif live:
        state, text = "green", f"{len(live)} download(s) running on LYGO's engine"
    elif (reach or {}).get("state") == "ok":
        state, text = "green", f"Plexo is answering on port {reach.get('port')}"
    else:
        state, text = "green", "ready - nothing is running until a button or the agent asks for it"

    return {
        "signature": SIGNATURE,
        "at": _at(),
        "state": state,
        "state_text": text,
        "lights": [engine_light, app_light, job_light],
        "groups": [
            _group("The buttons", rows_actions),
            _group("What this console started", chan_rows),
            _group("Where downloads land", ws_rows),
        ],
        "actions": [dict(a) for a in PANEL_ACTIONS],
        "missing": gaps,
    }


def data(ctx: Any, req: Any) -> None:
    """GET /api/plexo - the strip, read-only."""
    req.json(200, build(ctx))


def _gate(text: str) -> dict[str, Any] | None:
    """The kernel's p0 gate over text that is about to be acted on. None means it passed."""
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
    import plexo_tools as pt

    if action == "bridge":
        reach = pt.bridge()
        return (200 if reach.get("up") else 409), {"ok": bool(reach.get("up")), "action": "bridge", **reach}

    if action == "open":
        out = pt.open_app()
        if out.get("ok"):
            return 200, {"ok": True, "action": "open", **out,
                         "why": out.get("why") or "Plexo is up and its bridge answers"}
        return 409, {"ok": False, "action": "open", **out}

    if action == "stop":
        out = pt.stop_app()
        if out.get("ok"):
            return 200, {"ok": True, "action": "stop", **out}
        return 409, {"ok": False, "action": "stop", **out}

    if action == "cancel_all":
        out = pt.cancel_all()
        return (200 if out.get("ok") else 409), {"action": "cancel_all", **out}

    if action == "cancel":
        out = pt.cancel(str(body.get("job") or ""))
        return (200 if out.get("ok") else 409), {"action": "cancel", **out}

    if action == "jobs":
        return 200, {"ok": True, "action": "jobs", "jobs": pt.jobs(), "root": str(pt.downloads_root())}

    if action == "status":
        return 200, {"ok": True, "action": "status", "status_data": pt.state()}

    if action == "doctor":
        return 200, {"ok": True, "action": "doctor", "doctor_data": pt.doctor()}

    if action == "pin":
        return 200, {"ok": True, "action": "pin", "pin_data": pt.pin()}

    if action == "credit":
        return 200, {"ok": True, "action": "credit", "credit_data": pt.credit()}

    if action == "probe":
        got = pt.probe(str(body.get("url") or ""))
        return (200 if got.get("ok") else 400), {"action": "probe", **got}

    if action == "verify":
        got = pt.verify(str(body.get("path") or body.get("name") or ""))
        return (200 if got.get("ok") else 400), {"action": "verify", **got}

    if action in ("fetch", "hand_off"):
        url = str(body.get("url") or "")
        if not url:
            return 400, {"ok": False, "action": action, "error": "no_url", "why": "this action needs a url"}
        blocked = _gate(url)
        if blocked:
            return 403, {"ok": False, "action": action, "error": "p0_quarantine", **blocked}
        if action == "fetch":
            got = pt.fetch(url, name=str(body.get("name") or ""), out=str(body.get("out") or ""),
                           streams=int(body.get("streams") or 8), consent=bool(body.get("consent")))
            return (200 if got.get("ok") else 409), {"action": "fetch", **got}
        if not bool(body.get("consent")):
            return 403, {"ok": False, "action": "hand_off", "error": "consent_required",
                         "why": "handing a link to Plexo starts a download on the operator's own connection",
                         "hint": "the button never does it alone; the agent must be told yes"}
        boot = pt.open_app()
        if not boot.get("ok"):
            return 409, {"ok": False, "action": "hand_off", "error": boot.get("error") or "bridge_closed",
                         "why": boot.get("why"), "hint": f"install Plexo with {pt.installed().get('fetcher')}"}
        offer = pt.bridge_add(url, file_name=str(body.get("name") or ""))
        if not offer.get("ok"):
            return 409, {"action": "hand_off", **{k: offer[k] for k in ("error", "why") if k in offer}}
        done = pt.bridge_confirm(offer["token"], int(offer["port"]))
        return (200 if done.get("ok") else 409), {
            "action": "hand_off", "ok": bool(done.get("ok")), "url": url, "handed_to": "plexo",
            "port": offer.get("port"), "version": offer.get("version") or "",
            "error": "" if done.get("ok") else done.get("error"), "why": done.get("why"),
            "note": "Plexo owns it now; its queue, limits and schedule apply",
        }

    return 400, {"ok": False, "error": "unknown_action", "why": f"{action!r} is not a button or an action here",
                 "actions": list(ACTIONS)}


def control(ctx: Any, req: Any) -> None:
    """POST /api/plexo - one action per call, answered with the code the caller reads."""
    try:
        body = req.body(200_000) or {}
    except Exception as exc:  # noqa: BLE001
        req.json(400, {"ok": False, "error": "unreadable_body", "why": str(exc)[:200]})
        return
    if not isinstance(body, dict):
        body = {}
    action = str(body.get("action") or "status").strip().lower()
    if action not in ACTIONS:
        req.json(400, {"ok": False, "error": "unknown_action", "action": action, "actions": list(ACTIONS)})
        return
    code, payload = _act(action, body, ctx)
    # Every action that changes the machine gets a receipt: the same record a limb call leaves, so
    # "who started that Plexo" or "what did that download" is answerable afterwards without guessing.
    if action in ("open", "stop", "cancel", "cancel_all", "fetch", "hand_off"):
        try:
            ctx.receipt(prompt=f"plexo:{action} {str(body.get('url') or body.get('job') or '')[:180]}",
                        output=str(payload.get("path") or payload.get("url") or payload.get("port") or ""),
                        extra={"module": MID, "action": action, "ok": bool(payload.get("ok")),
                               "error": str(payload.get("error") or ""), "why": str(payload.get("why") or "")[:200]})
        except Exception as exc:  # noqa: BLE001 - a receipt may never take the action down
            ctx.log(f"receipt for plexo:{action} failed: {exc}")
    try:
        payload["card"] = build(ctx)
    except Exception as exc:  # noqa: BLE001 - the action already happened; the card is a courtesy
        payload["card_error"] = str(exc)[:200]
    req.json(code, payload)


def register(ctx: Any) -> dict[str, Any]:
    """The two routes and the pane. It registers NO limb: `plexo` already exists in limbs.py and is
    declared as owned in this module's manifest, so listing it here would register the same tool
    twice and change the tool set on load."""
    return {
        "routes": [("GET", ROUTE, data), ("POST", ROUTE, control)],
        "limbs": [],
        "panes": [
            {
                "id": "dock.plexoctl",
                "slot": "dock",
                "title": "Download controls",
                "data": f"GET {ROUTE}",
                "refresh_s": 15,
                "inner_scroll": False,
            }
        ],
        "health": health,
    }


def health(ctx: Any) -> dict[str, Any]:
    """Can this module read its owner, and does the strip still have its buttons? Cheap BY DESIGN."""
    try:
        import plexo_tools  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"`{OWNER}` does not import on this tree ({exc}), so the strip would be empty"}
    panel = build(ctx, cheap=True)
    if not (panel.get("actions") or []):
        return {"ok": False, "detail": "the download owner answers but published no buttons, so the strip would be empty"}
    if not (panel.get("groups") or []):
        return {"ok": False, "detail": "the download owner answers but the card would read empty"}
    return {"ok": True, "detail": "the download owner answers and the strip has its buttons",
            "buttons": len(panel.get("actions") or [])}
