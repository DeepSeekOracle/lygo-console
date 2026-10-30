"""lygo.musicctrl — the engine room: pick an engine, start a render, watch it, stop it.

Δ9Φ963-LYGO-MUSICCTRL-v1

WHAT THIS IS
    The actuator half of the song pair. `lygo.musicinfo` reads the facts and `lygo.musicwatch`
    finds the faults; this module is where a song is actually STARTED from the console. It renders
    nothing itself: every render leaves through the `music_generate` limb, which is the one place
    that knows how to drive an engine, and comes back as a job record that says what really
    happened - the path the limb wrote, or the limb's own named failure.

WHY A JOB AND NOT A CALL
    A render on this class of box takes tens of minutes (measured: a single 1500-token segment was
    still generating after 50 minutes on an 8 GB card with the heavy weights streamed from RAM). A
    turn that blocked that long would look like a hung console, so `start` answers at once and the
    render runs in a worker thread whose progress is a file. The panel polls that file, and
    `cancel` kills the engine process - not the thread, the process, which is the thing holding the
    card.

THE GATE
    Lyrics and the engine choice pass the kernel's p0 gate before anything is written or spawned,
    the same way the notepad's saves do. A quarantined body is refused by name with the gate's own
    reason. Nothing is ever silently accepted.

THE ONE THING IT OWNS
    `save/music` - the operator's engine choice and one record per render, through atomicio. The
    audio itself belongs to music_tools (workspace/audio/songs) and is served by the kernel's media
    route, so a page reload can never start a render.
"""
from __future__ import annotations

import time
from typing import Any

SIGNATURE = "Δ9Φ963-LYGO-MUSICCTRL-v1"
MID = "lygo.musicctrl"
ROUTE = "/api/music"
ROUTES: tuple[tuple[str, str], ...] = (("GET", ROUTE), ("POST", ROUTE))
OWNERS: tuple[str, ...] = ("music_tools", "music_jobs", "paths")

ACTIONS = ("start", "cancel", "engine", "convert", "check", "retitle")


# --- formatting -------------------------------------------------------------------------------


def _at() -> str:
    return time.strftime("%H:%M:%S")


def _plural(n: Any, one: str, many: str | None = None) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return one
    return one if n == 1 else (many or one + "s")


def _size(n: Any) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return (f"{n:.0f} {unit}" if unit == "B" or n >= 100 else f"{n:.1f} {unit}")
        n /= 1024.0
    return f"{n:.1f} GB"


def _row(k: str, v: Any, note: str = "", dot: str = "") -> dict[str, Any]:
    return {"k": str(k), "v": str(v), "note": str(note), "dot": str(dot)}


def _group(title: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"title": title, "rows": rows}


def _light(state: str, label: str, text: str, detail: str = "", next_change: str = "") -> dict[str, Any]:
    out = {"state": state, "label": label, "text": text, "detail": detail}
    if next_change:
        out["next_change"] = next_change
    return out


def _short(path: Any) -> str:
    s = str(path or "")
    for cut in ("\\lygo_llm_console\\", "/lygo_llm_console/", "\\app\\", "/app/"):
        if cut in s:
            return "…" + s.split(cut, 1)[1].replace("\\", "/")
    return s


def _ago(seconds: Any) -> str:
    try:
        s = float(seconds or 0)
    except (TypeError, ValueError):
        return "?"
    if s < 90:
        return f"{s:.0f} s"
    if s < 5400:
        return f"{s / 60:.0f} min"
    return f"{s / 3600:.1f} h"


# --- rows -------------------------------------------------------------------------------------


def _chosen_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    eng = str(state.get("engine") or "")
    row = next((e for e in (state.get("engines") or []) if e.get("id") == eng), None)
    if row is None:
        return [_row("Engine in use", eng or "none", "this build declares no engine by that id")]
    rows = [
        _row("Engine in use", row.get("label") or eng,
             ("can render here" if row.get("wired") else "NO ADAPTER in this build - a render is refused"),
             "green" if row.get("state") == "installed" and row.get("wired") else "amber"),
        _row("Its state", row.get("state") or "?", row.get("note") or ""),
        _row("Its app", _short(row.get("app") or "-"), "entry %s %s" % (
            row.get("entry") or "?", "present" if row.get("entry_present") else "MISSING")),
    ]
    if row.get("missing"):
        rows.append(_row("Missing", ", ".join(str(m) for m in row["missing"])[:150],
                         "the switch is real; the render is not, until these are on disk"))
    return rows


def _engine_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for e in (state.get("engines") or []):
        installed = e.get("state") == "installed"
        dot = "green" if installed else "grey"
        note = str(e.get("note") or "")
        if e.get("chosen"):
            note = "IN USE - " + note
        rows.append(_row(f"{e.get('id')}", ("installed" if installed else "declared"), note[:170], dot))
    return rows


def _job_rows(job: dict[str, Any]) -> list[dict[str, Any]]:
    st = str(job.get("state") or "none")
    if st == "none":
        return [_row("Render", "none yet",
                     "no song has been rendered on this kit - press the studio's Generate when you are ready",
                     "grey")]
    rows = [
        _row("State", st, str(job.get("status") or "")[:170],
             {"done": "green", "running": "amber"}.get(st, "red")),
        _row("Engine", job.get("engine") or "?", "seed %s · %s segment(s) · %s tokens" % (
            job.get("seed"), job.get("segments"), job.get("max_new_tokens"))),
        _row("Started", str(job.get("started") or "?"),
             ("running %s" % _ago(job.get("elapsed_s")) if st == "running" else
              "took %s" % _ago(job.get("elapsed_s")))),
    ]
    if job.get("song"):
        rows.append(_row("Wrote", _short(job["song"]), "the file the limb really wrote", "green"))
    if job.get("error"):
        rows.append(_row("Why it stopped", job.get("error"), str(job.get("hint") or "")[:150], "red"))
    return rows


def _song_rows(songs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not songs:
        return [_row("Songs", 0, "none rendered yet - a true fact, not a fault", "grey")]
    rows = [_row("Songs", len(songs), "newest first; the studio panel plays them in place", "green")]
    for s in songs[:4]:
        bits = [str(s.get("when") or "")]
        if s.get("seconds"):
            bits.append(f"{float(s['seconds']):.0f}s")
        if s.get("engine"):
            bits.append(str(s["engine"]))
        if s.get("seed"):
            bits.append("seed " + str(s["seed"]))
        rows.append(_row(s.get("name") or "?", _size(s.get("bytes")), " · ".join(bits)))
    if len(songs) > 4:
        rows.append(_row("…", f"{len(songs) - 4} older", "all of them are in the studio panel"))
    return rows


# --- the card ---------------------------------------------------------------------------------


def build(ctx: Any = None, st: dict[str, Any] | None = None) -> dict[str, Any]:
    """The facts this card can stand behind, gathered from the limb's own machinery.

    Pass `st` when the caller already holds the studio state: the card and the state are the same
    facts, and reading them twice made one poll read the card twice.
    """
    from music_jobs import STUDIO_SIGNATURE, studio_state
    import music_tools as mt

    st = st if st is not None else studio_state()
    job = st.get("job") or {"state": "none"}
    songs = st.get("songs") or []
    routes = st.get("route") or {}
    chosen = st.get("engine") or "?"

    if job.get("state") == "running":
        engine_light = _light("amber", "Rendering", str(job.get("status") or "a render is in flight"),
                              detail="one render at a time - the card it holds is why nothing else can render now",
                              next_change="when the render writes its song or fails by name")
    elif not any(e.get("state") == "installed" for e in (st.get("engines") or [])):
        engine_light = _light("red", "No engine", "no declared engine can render on this box",
                              detail="the studio's switch still works; every engine names what it is missing",
                              next_change="when one engine's app, python and weights are all on disk")
    else:
        engine_light = _light("green", "Ready", f"{chosen} can render here",
                              detail=str(routes.get("why") or "the card is free or can be made free"))

    if job.get("state") in ("failed", "cancelled"):
        job_light = _light("red" if job["state"] == "failed" else "grey",
                           "Stopped" if job["state"] == "failed" else "Cancelled",
                           str(job.get("status") or job["state"]),
                           detail=str(job.get("hint") or "the limb named why; the log tail is on the job record"))
    elif job.get("state") == "done":
        job_light = _light("green", "Song written", _short(job.get("song")),
                           detail="claimed and written are the same thing: the file is on disk")
    elif job.get("state") == "running":
        job_light = _light("amber", "In flight", f"{_ago(job.get('elapsed_s'))} so far")
    else:
        job_light = _light("grey", "Idle", "no render has been run from the studio yet")

    return {
        "signature": SIGNATURE,
        "studio": STUDIO_SIGNATURE,
        "built_at": _at(),
        "lights": [engine_light, job_light],
        "groups": [
            _group("The engine in use", _chosen_rows(st)),
            _group("Every declared engine", _engine_rows(st)),
            _group("The render", _job_rows(job)),
            _group("Songs", _song_rows(songs)),
        ],
        "missing": [],
        "route": routes.get("route") or "",
        "engine": chosen,
        "actions": list(ACTIONS),
    }


def data(ctx: Any, req: Any) -> None:
    """GET /api/music - the whole studio state in one answer, which is what the panel polls."""
    from music_jobs import studio_state

    st = studio_state()
    st["card"] = build(ctx, st)
    st["sheet"] = _sheet()
    req.json(200, st)


def _sheet() -> dict[str, Any]:
    """The fill-in template and the engine's own tag vocabulary, so the page never invents either."""
    try:
        import music_lyrics as ml
        import music_tools as mt

        return {
            "template": ml.template_text(),
            "format": ml.WRITE_FORMAT,
            "brief_fallback": ml.BRIEF_FALLBACK,
            "vocab": ml.vocabulary(str(mt.yue_app() or "")),
            "tags": list(ml.SECTION_TAGS),
            "max_sections": int(ml.MAX_SECTIONS),
        }
    except Exception as exc:
        return {"error": f"the sheet could not be assembled: {str(exc)[:160]}"}


def _gate(text: str) -> dict[str, Any] | None:
    """The kernel's p0 gate over what is about to be written or acted on. None means it passed."""
    try:
        from p0_hook import gate_prompt
    except Exception:
        return {"verdict": "UNKNOWN", "reason": "the p0 gate could not be imported, so nothing was written"}
    try:
        v = gate_prompt(text[:8000])
    except Exception as exc:  # a gate that raised is not a pass
        return {"verdict": "UNKNOWN", "reason": f"the p0 gate raised: {str(exc)[:120]}"}
    if v.get("verdict") == "QUARANTINE":
        return v
    return None


def control(ctx: Any, req: Any) -> None:
    """POST /api/music - start a render, stop the one in flight, or switch engines."""
    import music_tools as mt
    from music_jobs import cancel, retitle, start

    try:
        body = req.body(400_000) or {}
    except Exception as exc:
        req.json(400, {"ok": False, "error": "unreadable_body", "why": str(exc)[:200]})
        return
    action = str(body.get("action") or "").strip().lower()
    if action not in ACTIONS:
        req.json(400, {"ok": False, "error": "unknown_action", "action": action,
                       "actions": list(ACTIONS)})
        return

    if action == "engine":
        want = str(body.get("engine") or "")
        gate = _gate(want)
        if gate:
            req.json(403, {"ok": False, "error": "gate_refused", "gate": gate})
            return
        out = mt.set_engine(want)
        # A refused switch is a refusal, with the code the panel reads. Answering 200 with ok:false
        # invites a UI to show a switch that never happened - the sibling `cancel` already answers 409.
        req.json(200 if out.get("ok") else 409, out)
        return

    if action == "retitle":
        # A title is operator text that lands in a record other people may read, so it passes the
        # same gate the lyrics do - and the action writes NOTHING but the take's sidecar: no
        # re-render, and the audio keeps the name already handed out to the browser.
        name = str(body.get("name") or body.get("song") or "")
        title = str(body.get("title") or "")
        gate = _gate(title)
        if gate:
            req.json(403, {"ok": False, "error": "gate_refused", "gate": gate})
            return
        out = retitle(name, title)
        out["card"] = build(ctx)
        req.json(200 if out.get("ok") else 409, out)
        return

    if action == "cancel":
        out = cancel(str(body.get("job_id") or ""))
        out["card"] = build(ctx)
        req.json(200 if out.get("ok") else 409, out)
        return

    if action in ("convert", "check"):
        # Neither one writes anything and neither one touches the card: they answer about the words on
        # the page, so a refusal or a typo costs nothing. No gate, no render, no state.
        import music_lyrics as ml

        text = str(body.get("lyrics") or "")
        if action == "convert":
            req.json(200, {"ok": True, "convert": ml.convert(text)})
            return
        req.json(200, {"ok": True, "check": ml.validate(text)})
        return

    style = str(body.get("style") or "").strip()
    lyrics = str(body.get("lyrics") or "")
    title = str(body.get("title") or "").strip()[:120]
    gate = _gate(f"{style}\n{title}\n{lyrics}")
    if gate:
        req.json(403, {"ok": False, "error": "gate_refused", "gate": gate})
        return
    out = start(
        style=style,
        lyrics=lyrics,
        engine=str(body.get("engine") or ""),
        segments=body.get("segments"),
        seed=body.get("seed"),
        max_new_tokens=body.get("max_new_tokens"),
        title=title,
    )
    out["card"] = build(ctx)
    req.json(200 if out.get("ok") else 409, out)


def health(ctx: Any) -> dict[str, Any]:
    """What this module can prove about itself without rendering anything."""
    try:
        from music_jobs import studio_state
        import music_tools as mt

        st = studio_state()
        eng = mt.chosen_engine()
        return {
            "ok": True,
            "detail": f"{len(st.get('engines') or [])} engine(s) declared, {eng} in use, "
                      f"{len(st.get('songs') or [])} song file(s) on disk, "
                      f"render {((st.get('job') or {}).get('state') or 'none')}",
        }
    except Exception as exc:
        return {"ok": False, "detail": f"the studio's machinery did not answer: {str(exc)[:180]}"}


def register(ctx: Any) -> dict[str, Any]:
    """The routes, the pane, and the limb this module owns the console-side of."""
    return {
        "routes": [
            ("GET", ROUTE, data),
            ("POST", ROUTE, control),
        ],
        "limbs": [{"name": "music_generate", "effect": "write"}],
        "panes": [
            {
                "id": "dock.musicstudio",
                "slot": "dock",
                "title": "Song studio",
                "data": f"GET {ROUTE}",
                "refresh_s": 10,
                "inner_scroll": False,
            }
        ],
        "health": health,
    }
