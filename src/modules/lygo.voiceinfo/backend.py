"""lygo.voiceinfo - the read-out card for the voice bridge.

Δ9Φ963-LYGO-VOICEINFO-v1

WHAT THIS ANSWERS
    The voice card, beside the picture engine and the song studio: **is VoiceStudio here, is its
    runtime staged, is its service answering, and what can it actually say right now?** Four
    questions the operator cannot answer anywhere else in one place:

        the application   whether the vendor's application is installed at all, where, and which
                          upstream project and licence it is
        the runtime       where the vendor's shell staged its own project + interpreter, and whether
                          that is finished, still installing, or absent
        the service       whether the local OpenAI-compatible speech platform is answering on its
                          port, what it calls itself, and which engines it advertises
        the boundary      the licence fact that explains why this is a bridge and not an embed

WHY IT EXISTS AS A CARD AND NOT A LIMB
    An operator about to ask for a voice should learn from a card whether a voice is possible on
    this machine - not from a failure. The actuator (`lygo.voicectrl`) is where a voice is asked
    for; this card is the read-out that says whether asking is worth it yet.

WHAT IT DELIBERATELY DOES NOT DO
    * No state, no writes anywhere: `owns: []`, `gate: none`, and its own test scans this source for
      write calls. Everything here is a read.
    * No re-derivation. Every fact is asked of its owner - `voice_harness` for the install, the
      runtime, the service and the licence boundary - and printed as it was told.
    * No write, ever, into the VoiceStudio install or runtime. They are the operator's application.
    * It never claims the service is up because a process exists: the harness probes /api/health,
      which is the route the vendor's own shell polls.

THE LICENCE FACT IT MUST KEEP SAYING
    VoiceStudio is AGPL-3.0-only. This console is LYGO Sovereign License v3.0, which forbids
    relicensing to GPL and forbids publishing a modified core. The two cannot be combined into one
    shippable work, so the console bridges to a copy the operator installs. That is a fact about
    what this kit may ship, so the card states it rather than leaving it in a README.

THE HONESTY RULES IT INHERITS
    * A check that could not run is named in `missing` and drags the card off green.
    * "Not installed yet" and "still installing" are TRUE states on a fresh machine and are
      reported as states with the reason - never as a fault, and never as ready.
"""
from __future__ import annotations

import time
from typing import Any

SIGNATURE = "Δ9Φ963-LYGO-VOICEINFO-v1"
MID = "lygo.voiceinfo"
ROUTE = "/api/voiceinfo"
ROUTES: tuple[tuple[str, str], ...] = (("GET", ROUTE),)

#: Every owner this card asks for a fact. A missing one is a named gap, never a zero.
OWNERS: tuple[str, ...] = ("voice_harness", "paths")


# --- formatters - the edge, where a number becomes something a human reads ----------------------


def _at() -> str:
    return time.strftime("%H:%M:%S")


def _size(n: Any) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" or n >= 100 else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def _row(k: str, v: Any, note: str = "", dot: str = "") -> dict[str, Any]:
    return {"k": str(k), "v": str(v), "note": str(note), "dot": str(dot)}


def _group(title: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"title": title, "rows": rows}


def _light(state: str, label: str, text: str, detail: str = "", next_change: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"state": state, "label": label, "text": text, "detail": detail}
    if next_change:
        out["next_change"] = next_change
    return out


def _short(path: Any) -> str:
    s = str(path or "")
    for cut in ("\\lygo_llm_console\\", "/lygo_llm_console/", "\\Programs\\", "/Programs/"):
        if cut in s:
            return "…" + s.split(cut, 1)[1].replace("\\", "/")
    return s


def _yes_no(flag: Any) -> str:
    return "yes" if flag else "no"


# --- the card -----------------------------------------------------------------------------------


def build(ctx: Any = None, st: dict[str, Any] | None = None) -> dict[str, Any]:
    """The facts this card can stand behind, gathered from the harness that owns them."""
    import voice_harness as vh

    st = st if st is not None else vh.state(deep=True, with_voices=False)
    install = st.get("install") or {}
    runtime = st.get("runtime") or {}
    service = st.get("service") or {}
    gaps: list[str] = list(st.get("gaps") or [])

    # --- light one: is the application here?
    if install.get("present"):
        app_light = _light(
            "green", "Installed", _short(install.get("root")),
            detail=f"the vendor's application is installed, {_size(install.get('exe_bytes'))} bundle",
            next_change="nothing - the application is present",
        )
    else:
        app_light = _light(
            "grey", "Not installed",
            "VoiceStudio is not installed on this machine",
            detail=str(install.get("reason") or ""),
            next_change="when the operator installs it from the upstream repository",
        )

    # --- light two: is the service answering?
    if service.get("up"):
        svc_light = _light(
            "green", "Answering", f"{service.get('base')}",
            detail=f"/api/health answered 200 · {service.get('checks_run', 0)} check(s) run",
            next_change="nothing - the service is answering",
        )
    elif install.get("present"):
        svc_light = _light(
            "amber", "Not running",
            f"nothing is answering on {service.get('base')}",
            detail=str(service.get("error") or "")[:180],
            next_change="when the service is started (the voice studio panel can start it)",
        )
    else:
        svc_light = _light(
            "grey", "No service", "there is nothing here to serve",
            detail="a service is only meaningful once the application is installed",
        )

    # --- rows
    app_rows = [
        _row("Installed", _yes_no(install.get("present")), str(install.get("reason") or ""),
             "green" if install.get("present") else "grey"),
        _row("Where", _short(install.get("root")) or "-",
             f"{_size(install.get('exe_bytes'))} executable" if install.get("present") else ""),
        _row("Its backend entry", _yes_no(bool(install.get("backend_main"))),
             _short(install.get("backend_main")) or "not found in the bundle"),
        _row("Upstream project", st.get("upstream"), "the published source, never vendored here"),
        _row("Its licence", st.get("license"),
             "models bundled inside it carry their own licences; review before commercial use"),
    ]

    if runtime:
        loc = runtime.get("location") or {}
        runtime_rows = [
            _row("Recorded root", _short(runtime.get("root")) or "-",
                 "written by the vendor's own shell on its first run",
                 "green" if loc.get("read") else "grey"),
            _row("Owned by the operator", _yes_no(loc.get("owned")),
                 "a user-owned copy - the reason this console may bridge to it at all"),
            _row("Its project", _short(runtime.get("project")) or "-",
                 "the vendor stages its backend and its interpreter here"),
            _row("Its interpreter", _yes_no(runtime.get("python_present")),
                 _short(runtime.get("python")) or "-",
                 "green" if runtime.get("python_present") else "grey"),
            _row("Dependencies complete", _yes_no(runtime.get("ready_marker")),
                 "the vendor's own ready marker" if runtime.get("ready_marker")
                 else "the marker is absent - the runtime is not finished",
                 "green" if runtime.get("ready_marker") else "amber"),
            _row("Still installing", _yes_no(runtime.get("installing")),
                 "a state, not a fault" if runtime.get("installing") else "",
                 "amber" if runtime.get("installing") else "grey"),
        ]
        if runtime.get("reason"):
            runtime_rows.append(_row("Why not ready", runtime.get("reason"), "", "amber"))
    else:
        runtime_rows = [_row("Runtime", "not read", "the harness could not be asked", "amber")]

    svc_rows = [
        _row("Address", service.get("base") or "-", "loopback only; nothing leaves this machine"),
        _row("Answering", _yes_no(service.get("up")),
             str(service.get("error") or "")[:170] or "the service answered /api/health",
             "green" if service.get("up") else "grey"),
        _row("Calls itself", service.get("version") or "not stated",
             "read from its own health or discovery answer"),
        _row("Engines it lists", len(service.get("models") or []),
             ", ".join((service.get("models") or [])[:5]) or "none read",
             "green" if service.get("models") else "grey"),
        _row("Checks that ran", service.get("checks_run", 0),
             ("could not run: " + ", ".join(service.get("checks_failed") or []))
             if service.get("checks_failed") else "every check answered",
             "amber" if service.get("checks_failed") else "green"),
        _row("Port free here", _yes_no(st.get("port_free")),
             "a bind test, not a guess from a process list"),
    ]

    boundary_rows = [
        _row("This console", "LYGO Sovereign License v3.0",
             "source-available; forbids relicensing to GPL and publishing a modified core"),
        _row("VoiceStudio", str(st.get("license") or ""),
             "requires the whole combined work be offered under AGPL-3.0"),
        _row("Verdict", "bridge, not embed",
             "the two licences cannot be satisfied at once, so no VoiceStudio code is in this kit",
             "green"),
        _row("What this kit ships", "a pointer + a bridge",
             "the operator installs their own copy; this console only calls it over loopback HTTP"),
        _row("Why", str(st.get("bridge_reason") or "")[:300]),
    ]

    missing = list(gaps)
    if not st.get("upstream"):
        missing.append("the harness did not report which upstream project this bridges to")
    if not service.get("checks_run"):
        missing.append("no service check ran at all - the card cannot judge the service")

    return {
        "signature": SIGNATURE,
        "harness": st.get("signature") or "",
        "built_at": _at(),
        "lights": [app_light, svc_light],
        "groups": [
            _group("The application", app_rows),
            _group("The runtime it staged", runtime_rows),
            _group("The service", svc_rows),
            _group("The licence boundary", boundary_rows),
        ],
        "missing": missing,
        "upstream": st.get("upstream") or "",
        "license": st.get("license") or "",
        "installed": bool(install.get("present")),
        "up": bool(service.get("up")),
    }


def _owner_gap(name: str, exc: Exception) -> str:
    return f"{name} could not be asked: {type(exc).__name__}: {str(exc)[:140]}"


def data(ctx: Any, req: Any) -> None:
    """GET /api/voiceinfo - the whole read-out in one answer, which is what the panel polls."""
    st: dict[str, Any] = {}
    gaps: list[str] = []
    try:
        import voice_harness as vh

        st = vh.state(deep=True, with_voices=False)
        gaps = list(st.get("gaps") or [])
    except Exception as exc:  # noqa: BLE001 - a card that cannot read is a named gap, not a 500
        gaps = [_owner_gap("voice_harness", exc)]
        st = {"gaps": gaps}

    card = build(ctx, st)
    card["missing"] = list(dict.fromkeys(list(card.get("missing") or []) + gaps))
    req.json(200, {**st, "card": card, "missing": card["missing"]})


def health(ctx: Any) -> dict[str, Any]:
    """What this card can prove about itself without reading anything expensive."""
    try:
        import voice_harness as vh

        install = vh.install_state()
        probed = vh.probe()
        return {
            "ok": True,
            "detail": (
                f"VoiceStudio {'installed' if install.get('present') else 'not installed'}; "
                f"service on {vh.service_base()} "
                f"{'answering' if probed.get('up') else 'not answering'}"
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"the harness did not answer: {str(exc)[:180]}"}


def register(ctx: Any) -> dict[str, Any]:
    """The route and the pane. This card has no limb and no state."""
    return {
        "routes": [("GET", ROUTE, data)],
        "limbs": [],
        "panes": [
            {
                "id": "dock.voiceinfo",
                "slot": "dock",
                "title": "Voice studio",
                "data": f"GET {ROUTE}",
                "refresh_s": 20,
                "inner_scroll": False,
            }
        ],
        "health": health,
    }
