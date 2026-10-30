"""lygo.delegatectrl - the side-agent buttons: which free engines answer, and one task handed out.

\u03949\u03a6963-LYGO-DELEGATECTRL-v1

WHAT THIS IS
    The card and the buttons for the side-agent pair that lives in `delegate_tools` - `subagent_run`
    and `subagent_pool` - which are ALREADY registered as limbs in limbs.py. So this module registers
    no limb and declares none: every call goes through the same two functions the brain's own tools
    call, which is what stops a button and the agent disagreeing about what happened.

WHAT IT WILL NOT DO
    * It will not call the pool available because the roster lists it. `Probe pool` asks each engine
      to answer once, and the card reads "not checked" until it has. Only the button spends a request.
    * It will not spend one at boot: `health` is a local read, so a console start never pays for a
      metered service and never waits on a socket.
    * It will not turn a masked error into a diagnosis. "Unexpected server error" is reported as
      that, with its ref, never as "the model is down" - the pool, a rate limit, a retired model id
      and a broken directory all look identical from here.
    * It will not hand private operator context to a free tier, whose per-model data terms differ.
      The card says so where the operator reads it, next to the button that would do it.
    * It will not claim the switch worked. `brain_on` re-reads `cloud_api.public_status()` after the
      save and reports the provider, model and url it actually found there.

THE SWITCH - the console's own turns, on the free pool
    `Use free brain` points the console's brain (not a side task: the turns YOU send) at the free
    model pool. It rides cloud_api's existing `custom` row - "OpenAI-compatible URL" - pointed at
    the in-process adapter in `free_brain`, which answers each turn by driving the OpenCode CLI,
    the one client the free tier accepts. No core file changes and no new brain path exists.
    `Back to paid / local` restores the provider, model and enabled flag that were there before
    (snapshotted at switch time) and then stops the adapter.
    A turn on the free brain SPAWNS THE CLI: seconds per answer, no token streaming, metered by
    the free tier and masked when exhausted. That is stated on the card, not buried here.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

SIGNATURE = "\u03949\u03a6963-LYGO-DELEGATECTRL-v1"
MID = "lygo.delegatectrl"
ROUTE = "/api/delegate"
ROUTES: tuple[tuple[str, str], ...] = (("GET", ROUTE), ("POST", ROUTE))
OWNER = "delegate_tools"
STATE_DIR = "delegate"
MAX_RUNS = 25

#: One tiny task the operator can press without typing anything, and the words that make the answer
#: checkable: the point is to prove the wiring, not to ask the model for an opinion.
SMOKE_TASK = "Reply with exactly this text and nothing else: SIDE_OK"

#: Everything the route and the limb accept. The PANEL publishes only the buttons below.
ACTIONS = ("status", "pool", "probe", "run", "smoke", "last", "doctor",
           "brain", "brain_on", "brain_off")

#: The small strip. `label` is what the button says, `action` is what it posts, `title` is the cost.
PANEL_ACTIONS: tuple[dict[str, str], ...] = (
    {"action": "probe", "label": "Probe pool",
     "title": "ask each free engine to answer once - the free tier is metered, so this costs one request per engine and is the only way to know which are answering"},
    {"action": "smoke", "label": "Smoke test",
     "title": "hand one tiny task to the default engine and record exactly what it did, so the wiring is proven rather than assumed"},
    {"action": "brain_on", "label": "Use free brain",
     "title": "run the console's OWN turns on a free engine: they go through the local adapter and the OpenCode CLI - seconds per answer, no streaming, metered - and never carry private operator context"},
    {"action": "brain_off", "label": "Back to paid / local",
     "title": "put the brain back exactly where it was before the switch (provider, model, on/off), then stop the adapter"},
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


def _short(text: Any, keep: int = 60) -> str:
    txt = str(text or "")
    return txt if len(txt) <= keep else "\u2026" + txt[-(keep - 1):]


# --- state: save/delegate, through atomicio ------------------------------------------------------


def _runs_path(ctx: Any) -> Path:
    return Path(ctx.save_dir(STATE_DIR)) / "runs.json"


def _verified_path(ctx: Any) -> Path:
    return Path(ctx.save_dir(STATE_DIR)) / "verified.json"


def _read(ctx: Any, path: Path, default: Any) -> Any:
    """Read a state file through the host. A file that cannot be read is the default, never a raise."""
    try:
        got = ctx.read_json(path, default)
    except Exception:  # noqa: BLE001 - an unreadable state file is not an action failure
        return default
    return default if got is None else got


def _write(path: Path, payload: Any) -> str:
    """Write through atomicio the way the rest of the kit does. Returns an error string, or ""."""
    try:
        from atomicio import atomic_write_text

        atomic_write_text(path, json.dumps(payload, indent=2))
        return ""
    except Exception as exc:  # noqa: BLE001 - the action already happened; the record is a courtesy
        return f"{type(exc).__name__}: {exc}"


def _brain_path(ctx: Any) -> Path:
    """The switch's own record. Inside this module's owned folder - never a new state root."""
    return Path(ctx.save_dir(STATE_DIR)) / "free_brain.json"


def _brain_rows(ctx: Any) -> list[dict[str, Any]]:
    """What the brain is doing NOW - read back from cloud_api, not remembered from the press."""
    try:
        import free_brain as fb
        st = fb.status(_brain_path(ctx))
    except Exception as exc:  # noqa: BLE001 - the card reports what it could not read
        return [_row("adapter", "unavailable", f"{type(exc).__name__}: {_short(exc, 50)}", "amber")]
    cloud = st.get("cloud") or {}
    shim = st.get("shim") or {}
    on_free = bool(st.get("enabled") and cloud.get("points_at_shim"))
    if on_free:
        brain = _row("brain", f"{st.get('model') or '?'}  (free)",
                     "the console's own turns answer through the local adapter", "ok")
    elif cloud.get("enabled"):
        brain = _row("brain", f"{cloud.get('label') or cloud.get('provider') or '?'}",
                     f"paid API - {cloud.get('model') or '?'}", "ok")
    else:
        brain = _row("brain", "local engine", "the API side is off: turns go to llama-server", "off")
    rows = [
        brain,
        _row("adapter", (f"listening on {shim.get('port')}" if shim.get("listening") else "not running"),
             f"{shim.get('answers')} answer(s), {shim.get('errors')} error(s) since it started",
             "ok" if shim.get("listening") else ""),
        _row("what it costs", "20-65s per turn",
             "each turn spawns the CLI, so no token-by-token streaming - and the free tier is metered", "amber"),
        _row("a long turn", f"over {int(getattr(fb, 'ARGV_PROMPT_CHARS', 7000))} chars -> a file it reads",
             "the CLI's own command line stops near 8191 through the npm wrapper, so a big prompt is written "
             "to a file and the CLI reads it (measured working at 17.8k chars, 12 s)", "amber"),
        _row("what it must not carry", "private context",
             "free tiers log or train on prompts; per-model terms differ, so private work stays on the paid provider or the engine", "amber"),
    ]
    if on_free:
        prev = (st.get("previous") or {}).get("provider") or "the previous brain"
        rows.append(_row("to go back", "one press", f"Back to paid / local restores {prev}"))
    return rows


def _record(ctx: Any, rec: dict[str, Any]) -> str:
    runs = _read(ctx, _runs_path(ctx), [])
    if not isinstance(runs, list):
        runs = []
    runs.append(rec)
    return _write(_runs_path(ctx), runs[-MAX_RUNS:])


# --- the card ------------------------------------------------------------------------------------


def build(ctx: Any, cheap: bool = False) -> dict[str, Any]:
    """The strip, its state, and the record behind it. Reads the harness; never re-derives it."""
    import delegate_tools as dt

    pool: dict[str, Any] = {}
    try:
        pool = dt.subagent_pool(probe=False) or {}
    except Exception as exc:  # noqa: BLE001 - the card reports what it could not read
        pool = {"error": f"{type(exc).__name__}: {exc}"}
    free = [str(e) for e in (pool.get("free") or [])]
    default = str(pool.get("default") or "")
    roster = str(pool.get("roster") or "")

    cli = ""
    getter = getattr(dt, "cli_path", None) or getattr(dt, "_exe", None)
    if getter is not None:
        try:
            cli = str(getter() or "")
        except Exception:  # noqa: BLE001
            cli = ""

    verified = _read(ctx, _verified_path(ctx), {})
    if not isinstance(verified, dict):
        verified = {}
    results = verified.get("results") if isinstance(verified.get("results"), dict) else {}
    runs = _read(ctx, _runs_path(ctx), [])
    if not isinstance(runs, list):
        runs = []
    last = runs[-1] if runs else {}
    gaps: list[str] = []

    if not cli:
        gaps.append("the OpenCode CLI is not on this machine: npm i -g opencode-ai@latest (no account, "
                    "no key) - the free pool is reached through it and through nothing else")

    # The three lights. A check that has not run is GREY and says so; green is earned.
    if cli:
        engine_light = _light("green", "CLI", "installed", detail=_short(cli, 70))
    else:
        engine_light = _light("amber", "CLI", "not installed",
                              next_change="npm i -g opencode-ai@latest")

    probed = [e for e in (pool.get("engines") or []) if isinstance(e, dict)]
    answering = [e for e in probed if e.get("ok")]
    if not verified:
        pool_light = _light("grey", "Free pool", "not checked",
                            detail=f"{len(free)} engine(s) in the roster; none has been asked yet",
                            next_change="press Probe pool - it costs one request per engine")
    elif answering:
        pool_light = _light("green", "Free pool", f"{len(answering)} answering",
                            detail=f"probed {verified.get('at') or '?'}")
    else:
        pool_light = _light("amber", "Free pool", "none answering",
                            detail=f"probed {verified.get('at') or '?'}",
                            next_change="the free tier is metered; try again later or set a BYO engine")

    if not last:
        run_light = _light("grey", "Last task", "none yet", detail="nothing has been handed out from here")
    elif last.get("ok"):
        run_light = _light("green", "Last task", "done",
                           detail=f"{_short(last.get('engine'), 34)} in {last.get('seconds')}s",
                           next_change="press Smoke test, or ask the agent for a real side task")
    else:
        run_light = _light("amber", "Last task", "failed",
                           detail=f"{last.get('error') or 'failed'}: {_short(last.get('why') or last.get('hint'), 50)}")

    rows_actions = [_row(a["action"], a["label"], a["title"]) for a in PANEL_ACTIONS]
    rows_pool = [
        _row("default engine", default or "-", "the one a press uses when no engine is named"),
        _row("in the roster", str(len(free)), _short(roster, 56) if roster else "the built-in list"),
        _row("last probe", str(verified.get("at") or "never"),
             "the roster is what the CONFIG says; the probe is what the engines ANSWERED"),
    ]
    eng_rows = []
    for e in (pool.get("engines") or []):
        if not isinstance(e, dict):
            continue
        name = str(e.get("engine") or "?")
        if e.get("probed"):
            dot = "ok" if e.get("ok") else "amber"
            note = (f"answered in {e.get('seconds')}s" if e.get("ok")
                    else str(e.get("error") or e.get("note") or "no answer"))
        else:
            dot, note = "", "not checked"
        eng_rows.append(_row(name, "free", str(note), dot))
    if not eng_rows:
        eng_rows.append(_row("(no engines)", "-", "the roster could not be read: see the note above", "amber"))
    rows_runs = [
        _row("task", _short(last.get("task")), str(last.get("at") or "")),
        _row("engine", str(last.get("engine") or "-"), "free pool" if last else ""),
        _row("result", "ok" if last.get("ok") else ("failed" if last else "-"),
             (str(last.get("error") or "") + " " + str(last.get("why") or last.get("hint") or "")).strip(),
             "ok" if last.get("ok") else ("amber" if last else "")),
        _row("tools it used", ", ".join(last.get("tools_used") or []) or "-",
             "what the side agent actually did, from its own event stream"),
        _row("where it worked", _short(last.get("dir"), 56) if last else "-",
             "its own folder, deleted or kept by you"),
    ]
    rows_wont = [
        _row("private context", "never",
             "the free tier is metered and its per-model data terms differ; side tasks only"),
        _row("a request at boot", "never",
             "health is a local read; only the Probe button spends anything"),
        _row("a guess at a masked error", "never",
             "'Unexpected server error' is reported as that, with its ref - not as a diagnosis"),
        _row("a write run by default", "denied",
             "a run is read-only unless you say otherwise: edit, bash and webfetch are denied"),
    ]
    free_on, free_model = False, ""
    try:
        import free_brain as _fb
        _bst = _fb.status(_brain_path(ctx))
        free_on = bool(_bst.get("enabled") and ((_bst.get("cloud") or {}).get("points_at_shim")))
        free_model = str(_bst.get("model") or "")
    except Exception:  # noqa: BLE001 - the switch's state is never the card's failure
        free_on = False

    if free_on:
        state, text = "green", f"the console's own turns run on {free_model or 'a free engine'} (free)"
    elif not cli:
        state, text = "amber", "the free pool needs the OpenCode CLI, which is not installed here"
    elif verified and answering:
        state, text = "green", f"{len(answering)} free engine(s) answering - ready for a side task"
    elif not verified:
        state, text = "grey", "wired; the pool has not been checked yet (that is a button, not a guess)"
    else:
        state, text = "amber", "the pool answered nothing at the last probe"

    return {
        "signature": SIGNATURE,
        "at": _at(),
        "state": state,
        "state_text": text,
        "lights": [engine_light, pool_light, run_light],
        "groups": [
            _group("The console's brain", _brain_rows(ctx)),
            _group("The buttons", rows_actions),
            _group("The pool", rows_pool),
            _group("Engines in the roster", eng_rows),
            _group("The last task handed out", rows_runs),
            _group("What this will not do", rows_wont),
        ],
        "actions": [dict(a) for a in PANEL_ACTIONS],
        "missing": gaps,
    }


def data(ctx: Any, req: Any) -> None:
    """GET /api/delegate - the strip, read-only. Never probes: a page refresh costs nothing."""
    req.json(200, build(ctx))


def _gate(text: str) -> dict[str, Any] | None:
    """The kernel's p0 gate over text about to be acted on. None means it passed."""
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


# --- the actions ---------------------------------------------------------------------------------


def _act(action: str, body: dict[str, Any], ctx: Any) -> tuple[int, dict[str, Any]]:
    """Run one action, answer (http code, payload). Never raises: a failure is a named answer."""
    import delegate_tools as dt

    if action == "status":
        return 200, {"ok": True, "action": "status", "card": build(ctx)}

    if action == "pool":
        return 200, {"ok": True, "action": "pool", "pool": dt.subagent_pool(probe=False),
                     "roster": str(getattr(dt, "ROSTER_PATH", ""))}

    if action == "last":
        runs = _read(ctx, _runs_path(ctx), [])
        return 200, {"ok": True, "action": "last", "runs": (runs if isinstance(runs, list) else [])[-MAX_RUNS:]}

    if action == "doctor":
        return 200, {"ok": True, "action": "doctor", "doctor": _diagnose(ctx)}

    if action == "probe":
        got = dt.subagent_pool(probe=True)
        engines = [e for e in (got.get("engines") or []) if isinstance(e, dict) and e.get("probed")]
        results = {str(e.get("engine")): {"ok": bool(e.get("ok")), "seconds": e.get("seconds"),
                                          "error": str(e.get("error") or "")} for e in engines}
        note = _write(_verified_path(ctx), {"at": _at(), "results": results,
                                            "answering": [k for k, v in results.items() if v["ok"]]})
        answered = [k for k, v in results.items() if v["ok"]]
        payload = {"action": "probe", "ok": bool(answered), "probed": len(results),
                   "answering": answered, "results": results, "at": _at(),
                   "error": "" if answered else "pool_silent",
                   "why": (f"{len(answered)} of {len(results)} engine(s) answered"
                           if answered else
                           "no engine in the roster answered; the free tier is metered and best-effort"),
                   "hint": "the roster is what the config says - only this probe is what they answer",
                   "note": note or "recorded in save/delegate/verified.json"}
        return (200 if answered else 409), payload

    if action in ("run", "smoke"):
        task = str(body.get("task") or "").strip() or (SMOKE_TASK if action == "smoke" else "")
        if not task:
            return 400, {"ok": False, "action": action, "error": "no_task",
                         "why": "a side task needs a task; smoke is the one that carries its own",
                         "hint": "the agent calls subagent_run with a task, or press Smoke test"}
        blocked = _gate(task)
        if blocked:
            return 403, {"ok": False, "action": action, "error": "p0_quarantine", **blocked}
        out = dt.subagent_run(task=task,
                              engine=str(body.get("engine") or ""),
                              dir=str(body.get("dir") or ""),
                              timeout=int(body.get("timeout") or 300),
                              read_only=bool(body.get("read_only", True)),
                              consent=bool(body.get("consent")))
        rec = {"at": _at(), "action": action, "task": _short(task, 300),
               "engine": str(out.get("engine") or body.get("engine") or ""),
               "ok": bool(out.get("ok")), "seconds": out.get("seconds"),
               "tools_used": out.get("tools_used") or [], "dir": out.get("dir"),
               "error": str(out.get("error") or ""), "why": str(out.get("engine_error") or ""),
               "hint": str(out.get("hint") or ""), "answer": _short(out.get("answer"), 400)}
        note = _record(ctx, rec)
        payload = {"action": action, **{k: out[k] for k in out},
                   "record": note or f"recorded in {_runs_path(ctx)}"}
        return (200 if out.get("ok") else 409), payload

    if action in ("brain", "brain_on", "brain_off"):
        import free_brain as fb

        path = _brain_path(ctx)
        if action == "brain":
            return 200, {"ok": True, "action": "brain", "brain": fb.status(path)}
        if action == "brain_on":
            which = str(body.get("model") or "").strip()
            if not which:
                # the engine that actually answered last time, else the roster's default: never a guess
                verified = _read(ctx, _verified_path(ctx), {})
                answers = [str(x) for x in ((verified or {}).get("answering") or [])]
                which = answers[0] if answers else str(
                    (dt.subagent_pool(probe=False).get("default") or ""))
            got = fb.on(which, path)
            return (200 if got.get("ok") else 409), got
        got = fb.off(path)
        return (200 if got.get("ok") else 409), got

    return 400, {"ok": False, "error": "unknown_action",
                 "why": f"{action!r} is not a button or an action here", "actions": list(ACTIONS)}


def _diagnose(ctx: Any) -> dict[str, Any]:
    """A local read: what this card knows, and what it could not check. No request, no process."""
    import delegate_tools as dt

    facts: dict[str, Any] = {}
    getter = getattr(dt, "cli_path", None) or getattr(dt, "_exe", None)
    try:
        facts["cli"] = str(getter() or "") if getter else ""
    except Exception as exc:  # noqa: BLE001
        facts["cli"] = f"could not be resolved: {type(exc).__name__}"
    try:
        facts["roster"] = dt._roster()
    except Exception as exc:  # noqa: BLE001
        facts["roster"] = f"could not be read: {type(exc).__name__}"
    facts["verified"] = _read(ctx, _verified_path(ctx), {})
    facts["runs"] = len(_read(ctx, _runs_path(ctx), []) or [])
    facts["not_checked"] = ["live pool availability (that is the Probe button)", "a real side task (that is a run)"]
    return facts


def control(ctx: Any, req: Any) -> None:
    """POST /api/delegate - one action per call, answered with the code the caller reads."""
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
    # Every action that spends a request or changes the machine gets a receipt - the same record a
    # limb call leaves, so "who asked for that side task" is answerable afterwards without guessing.
    if action in ("probe", "run", "smoke", "brain_on", "brain_off"):
        try:
            ctx.receipt(prompt=f"delegate:{action} {str(body.get('task') or '')[:180]}",
                        output=str(payload.get("dir") or payload.get("at") or ""),
                        extra={"module": MID, "action": action, "ok": bool(payload.get("ok")),
                               "error": str(payload.get("error") or ""),
                               "why": str(payload.get("why") or "")[:200]})
        except Exception as exc:  # noqa: BLE001 - a receipt may never take the action down
            ctx.log(f"receipt for delegate:{action} failed: {exc}")
    if action not in ("status", "doctor"):
        try:
            payload["card"] = build(ctx)
        except Exception as exc:  # noqa: BLE001 - the action already happened; the card is a courtesy
            payload["card_error"] = str(exc)[:200]
    req.json(code, payload)


def register(ctx: Any) -> dict[str, Any]:
    """The two routes and the pane. It registers NO limb: `subagent_run` and `subagent_pool` already
    exist in limbs.py and are declared in this module's notes, so listing them here would register the
    same tools twice and change the tool set on load."""
    return {
        "routes": [("GET", ROUTE, data), ("POST", ROUTE, control)],
        "limbs": [],
        "panes": [
            {
                "id": "dock.delegatectrl",
                "slot": "dock",
                "title": "Side agents",
                "data": f"GET {ROUTE}",
                "refresh_s": 20,
                "inner_scroll": False,
            }
        ],
        "health": health,
    }


def health(ctx: Any) -> dict[str, Any]:
    """Can this module read its owner, and does the strip still have its buttons? Cheap BY DESIGN:
    local reads only - no subprocess, no request to a metered service (a console start must not be
    billed). ONE exception, and it is a local loopback bind: when the free-brain switch is already ON,
    the adapter is rebound here so a restart does not cost every turn a failed hop."""
    try:
        import delegate_tools  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"`{OWNER}` does not import on this tree ({exc}), so the strip would be empty"}
    # If the switch is already ON, the adapter is rebound here: the operator's choice survives the
    # restart, and a configured shim that is not listening costs every turn one failed hop.
    adapter = ""
    try:
        import free_brain as fb

        got = fb.ensure_if_enabled(_brain_path(ctx))
        if got.get("healed"):
            adapter = f"adapter rebound on {got.get('port')}"
        elif not got.get("ok"):
            adapter = f"adapter could not be rebound: {got.get('why')}"
    except Exception as exc:  # noqa: BLE001 - a heal that raised is never the module's failure
        adapter = f"adapter heal skipped: {type(exc).__name__}"

    panel = build(ctx, cheap=True)
    if not (panel.get("actions") or []):
        return {"ok": False, "detail": "the side-agent owner answers but published no buttons, so the strip would be empty"}
    if not (panel.get("groups") or []):
        return {"ok": False, "detail": "the side-agent owner answers but the card would read empty"}
    detail = "the side-agent owner answers and the strip has its buttons"
    if adapter:
        detail += " - " + adapter
    return {"ok": True, "detail": detail,
            "buttons": len(panel.get("actions") or []),
            "probed_at_boot": False,
            "adapter": adapter or "not needed"}
