"""lygo.plexoinfo - the download facts card: this kit's own engine, and the operator's Plexo.

Δ9Φ963-LYGO-PLEXOINFO-v1

WHAT THIS IS
    The read-out half of the download pair. `lygo.plexoctl` is where Plexo is actually started and
    where running downloads are cancelled; this card answers the question the operator asks first -
    what can download what, is anything running, and what has already landed.

WHY EVERY ROW HAS AN OWNER
    Nothing here is derived. `plexo_tools.state()` says what is on disk and what answered, `jobs()`
    holds the live jobs with their own byte counts and speeds, `pin()` holds the pinned upstream
    release and its hashes, `credit()` says who wrote what under which licence. A card that computed
    any of this itself could disagree with the limb the agent calls, and the operator would have two
    truths.

THE TWO HALVES ARE NAMED SEPARATELY, ALWAYS
    LYGO's own engine is code in this kit: standard library, no install, works on a stick. Plexo is a
    fetched 246 MB desktop application that adds something this kit cannot do at all (several network
    adapters on one download, and BitTorrent). A kit can have one and not the other, so each half gets
    its own rows and its own light - collapsing them is how "not fetched yet" reads as "broken".

WHAT IT WILL NOT DO
    * It never says a download is faster without that job's own numbers: every speed on this card is a
      byte count divided by the seconds that job has been running.
    * It never calls a missing Plexo a fault. A kit without it is a true reading with the fetcher
      named - amber, because the hand-off button cannot work until it runs, but never red: nothing is
      broken, something is not fetched yet.
    * It never says the bridge is up because a pid exists. Plexo's own `/ping` answering - with its
      app name and its api version - is the proof, and it says which port answered.
    * It writes nothing, owns no state and gates nothing. Reads only - its test scans this source for
      write calls.
"""
from __future__ import annotations

import time
from typing import Any

SIGNATURE = "\u03949\u03a6963-LYGO-PLEXOINFO-v1"
MID = "lygo.plexoinfo"
ROUTE = "/api/plexoinfo"
OWNER = "plexo_tools"


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


def _short(path: str, keep: int = 52) -> str:
    txt = str(path or "")
    return txt if len(txt) <= keep else "\u2026" + txt[-(keep - 1):]


def _size(mb: float) -> str:
    if not mb:
        return "0 MB"
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:g} MB"


def _rate(mbps: float) -> str:
    return f"{mbps:g} MB/s" if mbps else "not moving"


# --- the card -----------------------------------------------------------------------------------


def build(ctx: Any, cheap: bool = False) -> dict[str, Any]:
    """The whole card. Every value traced to the owner that holds it.

    `cheap=True` is the boot-check shape the module's `health()` uses: files, config and our own
    records, with no socket opened (a `/ping` at boot would cost 4.5 s of somebody's start-up for a
    card nobody has looked at yet). The route calls it the full way, because then someone is reading.
    """
    import plexo_tools as pt

    st = pt.state(cheap=cheap)
    inst = st.get("installed") or {}
    pin = st.get("pin") or {}
    eng = st.get("engine") or {}
    run = st.get("app") or {}
    reach = (st.get("bridge") or {}) if not cheap else {}
    jobs = st.get("jobs") or []
    credit = st.get("credit") or {}
    ref = credit.get("reference") or {}
    live = [j for j in jobs if j.get("state") == "running"]
    gaps: list[str] = []
    usable = bool(inst.get("app_present"))

    # ---- LYGO's own engine, which is code in this kit ----------------------------------------
    engine_rows = [
        _row("engine", "LYGO's own (standard library)", "no install, no Node, no Electron, no admin rights", "ok"),
        _row("where it writes", _short(str(eng.get("root") or "")), "inside this kit, so a copy carries it", "ok"),
        _row("block size", f"{eng.get('block_mb')} MB", "one block is one request; the fastest stream takes the next"),
        _row("streams", ", ".join(str(s) for s in (eng.get("streams") or [])), "asked for per download; never more than the file has blocks"),
        _row("at once", str(eng.get("max_jobs") or 0), "jobs, so a big download never owns the machine"),
        _row("resume", "yes, with the server's version checked",
             "ETag and Last-Modified must match, or the resume refuses rather than mixing two files"),
        _row("network paths here", ", ".join(str(a) for a in (eng.get("adapters") or [])) or "none found",
             "streams can be bound to these, one per stream, so two adapters carry one file at once "
             "(the agent asks for it: plexo action=adapters, fetch bind=[...])"),
        _row("several mirrors", "yes", "fetch urls=[...] spreads one file over every mirror that holds the same bytes, after checking they agree"),
        _row("speed ceiling", "whole job", "fetch limit_mbps=...; the ceiling is the job's, not one stream's"),
    ]

    # ---- the operator's Plexo, which is a fetched application --------------------------------
    if inst.get("app_present"):
        app_rows = [
            _row("Plexo", f"installed \u00b7 {_size(inst.get('app_mb') or 0)}", str(inst.get("app") or ""), "ok"),
            _row("found by", str(inst.get("app_from") or ""), "a value from your config wins over the kit's own folder"),
        ]
        if reach and reach.get("state") == "ok":
            app_rows.append(_row("bridge", f"answering on 127.0.0.1:{reach.get('port')}",
                                 f"Plexo {reach.get('version') or '?'} says app=plexo, api={reach.get('api')}", "ok"))
        elif reach and reach.get("state") == "stale":
            app_rows.append(_row("bridge", "answering, wrong api", str(reach.get("why") or ""), "amber"))
        elif cheap:
            app_rows.append(_row("bridge", "not checked yet", "the boot check opens no socket on purpose"))
        else:
            app_rows.append(_row("bridge", "not answering",
                                 "the window is closed - the Start button opens it", "amber"))
    else:
        app_rows = [
            _row("Plexo", "not installed here", f"nothing at {_short(str(inst.get('app') or ''))}", "amber"),
            _row("how to get it", str(st.get("fetcher") or "scripts/fetch_plexo.ps1"),
                 "the pinned release, hash-checked; this kit never fetches it by itself"),
            _row("or", str(st.get("tutorial") or "https://getplexo.app"), "the project's own download page"),
        ]
        gaps.append("Plexo is not installed here, so the multi-network hand-off is unavailable")
    app_rows.append(_row("pinned release", str(pin.get("release") or "PIN.json missing"),
                         f"{ref.get('licence') or 'MIT'} \u00b7 {ref.get('author') or 'Anmol Kapil'} \u00b7 {ref.get('upstream') or ''}"))

    # ---- what is downloading right now -------------------------------------------------------
    if live:
        job_rows = []
        for j in live[:5]:
            job_rows.append(_row(str(j.get("id")),
                                 f"{j.get('percent')}% \u00b7 {_rate(j.get('mbps') or 0)}",
                                 f"{j.get('received')} of {j.get('bytes')} bytes on {j.get('streams')} stream(s), "
                                 f"{j.get('blocks_done')}/{j.get('blocks_total')} blocks, {j.get('seconds')} s"))
        job_light = _light("green", "Downloading", f"{len(live)} job(s) running",
                           detail=f"{len(jobs)} job(s) this session", next_change="the controls card can stop them")
    else:
        done = [j for j in jobs if j.get("state") == "done"]
        job_rows = [_row("nothing running", "idle",
                         f"{len(done)} finished this session" if done else "a download starts when the agent or a button asks")]
        job_light = _light("grey", "Downloads", "idle", detail=str(eng.get("root") or ""))

    if jobs:
        hist_rows = [
            _row(str(j.get("state")), str(j.get("path") or ""),
                 f"{j.get('received')} bytes in {j.get('seconds')} s \u00b7 {j.get('why') or ''}",
                 "ok" if j.get("state") == "done" else ("amber" if j.get("state") == "running" else ""))
            for j in jobs[:6]
        ]
    else:
        hist_rows = [_row("this session", "nothing yet", "a job lives as long as this console does; its file stays")]

    # ---- what this kit will not do (read the refusals before trusting the card) ---------------
    will_not = [
        _row("fetch the installer", "never by a click",
             "scripts/fetch_plexo.ps1 is the only door, and it is yours to open"),
        _row("hand a link to Plexo", "needs consent=true",
             "that starts a download on your own connection; Plexo's own model is a human confirm"),
        _row("write outside the workspace", "needs consent=true", "a download lands in workspace/downloads otherwise"),
        _row("mix file versions", "refuses by name", "a changed ETag stops a resume instead of splicing two files"),
        _row("mix unequal mirrors", "refuses by name",
             "mirrors must agree on size and ETag; a file stitched from two versions is wrong in a way nobody can see"),
        _row("promise a speed-up", "only from this job's own numbers",
             "parallel streams help when the server or one connection is the limit; they cannot create bandwidth"),
    ]
    for what in credit.get("what_plexo_still_does_that_this_kit_does_not") or []:
        will_not.append(_row("Plexo still does", str(what)))

    if usable and not (reach or {}).get("state", "") == "ok" and not cheap:
        state, text = "green", ("ready - LYGO's engine downloads here, over this machine's own network "
                                "paths; Plexo's window is closed")
    elif usable:
        state, text = "green", "both halves ready - LYGO's engine, and Plexo answering"
    else:
        state, text = "amber", ("LYGO's engine is ready here (paths, mirrors, a speed ceiling, all its "
                                "own code); Plexo is not installed, and its fetcher is named above")

    return {
        "signature": SIGNATURE,
        "at": _at(),
        "state": state,
        "state_text": text,
        "lights": [
            _light("green", "Engine", "LYGO's own parallel downloader",
                   detail=f"blocks of {eng.get('block_mb')} MB, streams {eng.get('streams')}"),
            app_light(reach, inst, cheap),
            job_light,
        ],
        "groups": [
            _group("LYGO's own engine", engine_rows),
            _group("Plexo, the operator's own install", app_rows),
            _group("Right now", job_rows),
            _group("What the agent has asked for", hist_rows),
            _group("What this kit will not do", will_not),
        ],
        "missing": gaps,
        "engine": True,
        "plexo_installed": usable,
        "jobs": len(jobs),
        "running": len(live),
        "fetch": str(st.get("fetcher") or "scripts/fetch_plexo.ps1"),
    }


def app_light(reach: dict[str, Any], inst: dict[str, Any], cheap: bool) -> dict[str, Any]:
    """The Plexo light: the bridge answering is the proof, not a pid and not a folder."""
    if not inst.get("app_present"):
        return _light("amber", "Plexo", "not installed", detail=str(inst.get("app") or ""),
                      next_change="run scripts/fetch_plexo.ps1 to pin and install it")
    if reach.get("state") == "ok":
        return _light("green", "Plexo", f"answering on port {reach.get('port')}",
                      detail=str(reach.get("why") or ""), next_change="the hand-off button can send it a link")
    if reach.get("state") == "stale":
        return _light("amber", "Plexo", "an older bridge api", detail=str(reach.get("why") or ""),
                      next_change="update Plexo from getplexo.app")
    if cheap:
        return _light("grey", "Plexo", "not checked at boot", detail="the boot check opens no socket")
    return _light("grey", "Plexo", "installed, not running", detail=str(reach.get("why") or ""),
                  next_change="the Start button opens it and waits for the bridge")


def data(ctx: Any, req: Any) -> None:
    """GET /api/plexoinfo - the card, read-only."""
    req.json(200, build(ctx))


def register(ctx: Any) -> dict[str, Any]:
    """The route and the pane. This card owns no limb and no state: it reads, and nothing else."""
    return {
        "routes": [("GET", ROUTE, data)],
        "limbs": [],
        "panes": [
            {
                "id": "dock.plexo",
                "slot": "dock",
                "title": "Downloads",
                "data": f"GET {ROUTE}",
                "refresh_s": 15,
                "inner_scroll": False,
            }
        ],
        "health": health,
    }


def health(ctx: Any) -> dict[str, Any]:
    """Can this card read the owner it reports on, on THIS tree, right now? Cheap BY DESIGN.

    This runs inside the host's `load()`, i.e. inside the console's boot, so it asks only for local
    facts (`cheap=True`: no socket, no process). Measured: the full build opens three sockets with
    1.5 s timeouts each, and paying that at boot for a card nobody has opened yet is the same
    mistake the film card avoided by not spawning its CLI here.
    """
    try:
        import plexo_tools  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"`{OWNER}` does not import on this tree ({exc}), so this card would read empty"}
    panel = build(ctx, cheap=True)
    if not (panel.get("groups") or []):
        return {"ok": False, "detail": "the download owner imports but answered with nothing, so this card would read empty"}
    return {"ok": True, "detail": "the download owner answers", "gaps": len(panel.get("missing") or [])}
