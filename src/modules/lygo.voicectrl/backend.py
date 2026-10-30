"""lygo.voicectrl - the actuator: start the voice service, say something, read a file back as text.

Δ9Φ963-LYGO-VOICECTRL-v1

WHAT THIS IS
    The actuator half of the voice pair. `lygo.voiceinfo` reads the facts; this module is where a
    voice is actually ASKED FOR. It says nothing itself and writes no audio itself - every clip
    leaves through the harness (`voice_harness.speak`), which is the one place that knows how to
    call the separate application, and comes back with a receipt that says what really landed on
    disk: the absolute path, the byte count and the sha256 read BACK off the disk.

WHY A BRIDGE AND NOT AN EMBED
    VoiceStudio is AGPL-3.0-only; this console is LYGO Sovereign License v3.0. AGPL-3.0 section 5(c)
    requires the entire combined work be offered under AGPL-3.0, and the LYGO licence forbids
    relicensing to GPL and publishing a modified core. Both cannot hold, so no VoiceStudio code is
    in this kit. The operator installs their own copy from the upstream repository and this module
    calls it over loopback HTTP. `voice_harness` carries the full reasoning.

THE GATE
    The text to be spoken passes the kernel's p0 gate before the service is called, the same way the
    notepad's saves and the song studio's lyrics do. A quarantined body is refused BY NAME with the
    gate's own reason, and nothing is sent. Nothing is ever silently accepted.

THE ONE THING IT OWNS
    `save/voice` - the operator's engine and voice choice plus one record per clip, written through
    atomicio. The audio itself belongs to the harness (`workspace/audio/voice/`), never to the
    application's own folders: this module does not write into VoiceStudio, ever.

WHAT IT WILL NOT DO
    * It will not kill a service it did not start. A backend started by VoiceStudio's own window
      belongs to the operator; `stop` refuses that case by name rather than taking their app down.
    * It will not claim a clip exists because the service said 200. The harness reads the bytes back
      and the record carries "claimed and written are the same thing" as a measured fact.
    * It will not pretend a refused call succeeded. A 4xx from the service is reported with the
      service's own message.
"""
from __future__ import annotations

import time
from typing import Any

SIGNATURE = "Δ9Φ963-LYGO-VOICECTRL-v1"
MID = "lygo.voicectrl"
ROUTE = "/api/voice"
ROUTES: tuple[tuple[str, str], ...] = (("GET", ROUTE), ("POST", ROUTE))
OWNERS: tuple[str, ...] = ("voice_harness", "paths", "atomicio", "p0")

ACTIONS = ("start", "stop", "speak", "transcribe", "probe", "voice")

#: The inline body limit this module accepts. The clip limit is separate and lives in the harness.
MAX_TEXT_CHARS = 20000

STATE_NAME = "voice.json"


# --- formatters ---------------------------------------------------------------------------------


def _at() -> str:
    return time.strftime("%H:%M:%S")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


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
    for cut in ("\\lygo_llm_console\\", "/lygo_llm_console/", "\\voice\\", "/voice/"):
        if cut in s:
            return "…" + s.split(cut, 1)[1].replace("\\", "/")
    return s


# --- its own state ------------------------------------------------------------------------------


def _state_path(ctx: Any) -> Any:
    if ctx is not None and hasattr(ctx, "save_dir"):
        try:
            return ctx.save_dir() / STATE_NAME
        except Exception:  # noqa: BLE001
            pass
    try:
        import paths

        return paths.SAVE / "voice" / STATE_NAME
    except Exception:  # noqa: BLE001
        return None


def _state_default() -> dict[str, Any]:
    return {"engine": "", "voice": "", "speed": None, "clips": [], "updated": ""}


def _read_state(ctx: Any) -> dict[str, Any]:
    path = _state_path(ctx)
    if path is None:
        return _state_default()
    if ctx is not None and hasattr(ctx, "read_json"):
        data = ctx.read_json(path, None)
    else:
        try:
            import atomicio
            import json

            data = json.loads(atomicio.read_text(path) or "null")
        except Exception:  # noqa: BLE001
            data = None
    if not isinstance(data, dict):
        return _state_default()
    out = _state_default()
    out.update(data)
    if not isinstance(out.get("clips"), list):
        out["clips"] = []
    return out


def _write_state(ctx: Any, state: dict[str, Any]) -> bool:
    state["updated"] = _now()
    path = _state_path(ctx)
    if path is None:
        return False
    if ctx is not None and hasattr(ctx, "write_json"):
        return bool(ctx.write_json(path, state))
    try:
        import atomicio
        import json

        target = getattr(path, "parent", None)
        if target is not None:
            target.mkdir(parents=True, exist_ok=True)
        atomicio.atomic_write_text(path, json.dumps(state, indent=2, ensure_ascii=False))
        return True
    except Exception:  # noqa: BLE001
        return False


def _record_clip(ctx: Any, clip: dict[str, Any]) -> dict[str, Any]:
    """One record per clip, newest first, capped so the file cannot grow without bound."""
    state = _read_state(ctx)
    entry = {
        "name": clip.get("name") or "",
        "path": clip.get("path") or "",
        "bytes": int(clip.get("bytes") or 0),
        "sha256": clip.get("sha256") or "",
        "format": clip.get("format") or "",
        "chars": int(clip.get("chars") or 0),
        "voice": clip.get("voice") or "",
        "model": clip.get("model") or "",
        "when": _now(),
    }
    clips = [entry] + [c for c in state["clips"] if isinstance(c, dict)]
    state["clips"] = clips[:25]
    _write_state(ctx, state)
    return entry


# --- the gate -----------------------------------------------------------------------------------


def _gate(ctx: Any, text: str) -> dict[str, Any] | None:
    """The kernel's p0 gate over what is about to be said. None means it passed."""
    gate = getattr(ctx, "p0", None)
    if gate is not None and hasattr(gate, "check"):
        try:
            verdict = gate.check(text[:8000])
        except Exception as exc:  # noqa: BLE001 - a gate that raised is not a pass
            return {"verdict": "UNKNOWN", "reason": f"the p0 gate raised: {str(exc)[:120]}"}
    else:
        try:
            import p0_hook

            verdict = p0_hook.gate_prompt(text[:8000])
        except Exception:  # noqa: BLE001
            return {"verdict": "UNKNOWN",
                    "reason": "the p0 gate could not be imported, so nothing was sent"}
    if not isinstance(verdict, dict):
        return {"verdict": "UNKNOWN", "reason": "the p0 gate gave an answer that cannot be read"}
    if str(verdict.get("verdict") or "").upper() == "QUARANTINE":
        return verdict
    return None


# --- the card -----------------------------------------------------------------------------------


def build(ctx: Any = None, st: dict[str, Any] | None = None) -> dict[str, Any]:
    """The facts this panel can stand behind."""
    import voice_harness as vh

    st = st if st is not None else vh.state(deep=True, with_voices=False)
    install = st.get("install") or {}
    service = st.get("service") or {}
    state = _read_state(ctx)
    clips = [c for c in state.get("clips", []) if isinstance(c, dict)]

    if service.get("up"):
        if not install.get("present"):
            svc_light = _light(
                "amber", "Answering, unaccounted for",
                f"a service is on {service.get('base')} but no install was found here",
                detail="it may be a remote backend or a copy installed elsewhere - read the cards",
            )
        else:
            svc_light = _light(
                "green", "Ready", f"{service.get('base')} is answering",
                detail=f"{len(service.get('models') or [])} engine(s) listed by the service",
                next_change="nothing - a clip can be asked for now",
            )
    elif install.get("present"):
        svc_light = _light(
            "amber", "Not running", "the service is not answering",
            detail=str(service.get("error") or "")[:170],
            next_change="when the panel starts it, or VoiceStudio is opened",
        )
    else:
        svc_light = _light(
            "grey", "No application", "VoiceStudio is not installed here",
            detail=str(install.get("reason") or "")[:180],
            next_change="when the operator installs it from the upstream repository",
        )

    last = clips[0] if clips else None
    if last is None:
        clip_light = _light("grey", "Nothing spoken",
                            "no clip has been asked for from this console yet")
    else:
        clip_light = _light(
            "green", "Last clip", f"{last.get('chars')} chars → {_size(last.get('bytes'))}",
            detail=f"{last.get('when')} · {_short(last.get('path'))}",
        )

    svc_rows = [
        _row("Address", service.get("base") or "-", "loopback only; nothing leaves this machine"),
        _row("Answering", "yes" if service.get("up") else "no",
             str(service.get("error") or "")[:170] or "it answered /api/health",
             "green" if service.get("up") else "grey"),
        _row("Calls itself", service.get("version") or "not stated", ""),
        _row("Engines listed", len(service.get("models") or []),
             ", ".join((service.get("models") or [])[:6]) or "none read",
             "green" if service.get("models") else "grey"),
        _row("Port free here", "yes" if st.get("port_free") else "no",
             "a bind test, not a guess from a process list"),
    ]

    choice_rows = [
        _row("Engine in use", state.get("engine") or "the service's own active engine",
             "an empty choice means the service decides"),
        _row("Voice in use", state.get("voice") or "the engine's default voice",
             "a saved clone id, an engine preset, or an OpenAI voice name"),
        _row("Speed", state.get("speed") if state.get("speed") is not None else "engine default", ""),
        _row("Recorded", _short(_state_path(ctx)) or "-", "through atomicio", "green"),
    ]

    if last is None:
        clip_rows = [_row("Clips", 0,
                          "none asked for yet - a true fact on a fresh console, not a fault", "grey")]
    else:
        clip_rows = [_row("Clips", len(clips), "newest first; the newest is offered to play in place",
                          "green")]
        for c in clips[:4]:
            bits = [str(c.get("when") or "")]
            if c.get("chars"):
                bits.append(f"{c.get('chars')} chars")
            if c.get("format"):
                bits.append(str(c["format"]))
            if c.get("voice"):
                bits.append(str(c["voice"]))
            clip_rows.append(_row(c.get("name") or "?", _size(c.get("bytes")), " · ".join(bits)))
        if len(clips) > 4:
            clip_rows.append(_row("…", f"{len(clips) - 4} older", "all of them are in the panel"))

    boundary_rows = [
        _row("This console", "LYGO Sovereign License v3.0", "no relicensing, no modified core"),
        _row("VoiceStudio", str(st.get("license") or "AGPL-3.0-only"),
             "the operator's own copy, installed from the upstream project"),
        _row("What crosses the boundary", "HTTP to loopback only",
             "no VoiceStudio code, model file or data file is copied into this kit", "green"),
        _row("Upstream", st.get("upstream") or ""),
    ]

    missing = list(st.get("gaps") or [])
    if not install.get("present"):
        missing.append("no VoiceStudio install was found, so no clip can be produced on this machine")

    return {
        "signature": SIGNATURE,
        "harness": st.get("signature") or "",
        "built_at": _at(),
        "lights": [svc_light, clip_light],
        "groups": [
            _group("The service", svc_rows),
            _group("What it will say with", choice_rows),
            _group("Clips asked for from here", clip_rows),
            _group("Across the licence boundary", boundary_rows),
        ],
        "missing": missing,
        "actions": list(ACTIONS),
        "state": state,
        "clip": (last or {}).get("path") or "",
        "clip_data_url": vh.inline_clip({"ok": True, "path": (last or {}).get("path", ""),
                                         "bytes": (last or {}).get("bytes", 0),
                                         "content_type": "audio/mpeg"}) if last else "",
    }


def data(ctx: Any, req: Any) -> None:
    """GET /api/voice - the whole panel state in one answer, which is what the panel polls."""
    import voice_harness as vh

    try:
        st = vh.state(deep=True, with_voices=True)
    except Exception as exc:  # noqa: BLE001 - a panel that cannot read says so, never 500s
        req.json(200, {"ok": False, "error": "harness_unavailable", "why": str(exc)[:200]})
        return
    card = build(ctx, st)
    st["card"] = card
    st["state"] = card["state"]
    req.json(200, st)


def control(ctx: Any, req: Any) -> None:
    """POST /api/voice - start or stop the service, say something, or read a file back as text."""
    import voice_harness as vh

    try:
        body = req.body(400_000) or {}
    except Exception as exc:  # noqa: BLE001
        req.json(400, {"ok": False, "error": "unreadable_body", "why": str(exc)[:200]})
        return

    action = str(body.get("action") or "").strip().lower()
    if action not in ACTIONS:
        req.json(400, {"ok": False, "error": "unknown_action", "action": action,
                       "actions": list(ACTIONS)})
        return

    if action == "probe":
        probed = vh.probe(timeout=vh.timeout_s())
        req.json(200, {"ok": bool(probed.get("up")), "probe": probed,
                       "why": "" if probed.get("up") else str(probed.get("error") or "")[:200]})
        return

    if action == "start":
        out = vh.launch(budget_s=int(body.get("budget_s") or vh.DEFAULT_BUDGET_S),
                        wait=bool(body.get("wait", True)))
        out["card"] = build(ctx)
        req.json(200 if out.get("ok") else 409, out)
        return

    if action == "stop":
        out = vh.stop(force=bool(body.get("force")))
        out["card"] = build(ctx)
        req.json(200 if out.get("ok") else 409, out)
        return

    if action == "voice":
        want_engine = body.get("engine")
        want_voice = body.get("voice")
        state = _read_state(ctx)
        if want_engine is not None:
            gate = _gate(ctx, str(want_engine))
            if gate:
                req.json(403, {"ok": False, "error": "gate_refused", "gate": gate})
                return
            state["engine"] = str(want_engine).strip()
        if want_voice is not None:
            gate = _gate(ctx, str(want_voice))
            if gate:
                req.json(403, {"ok": False, "error": "gate_refused", "gate": gate})
                return
            state["voice"] = str(want_voice).strip()
        if body.get("speed") is not None:
            try:
                state["speed"] = float(body["speed"])
            except (TypeError, ValueError):
                req.json(400, {"ok": False, "error": "bad_speed",
                               "why": f"speed must be a number, got {body.get('speed')!r}"})
                return
        wrote = _write_state(ctx, state)
        req.json(200, {"ok": wrote, "state": _read_state(ctx),
                       "why": "" if wrote else "the choice could not be written to disk"})
        return

    if action == "transcribe":
        target = str(body.get("file") or body.get("path") or "")
        if not target:
            req.json(400, {"ok": False, "error": "no_file", "why": "name a file to transcribe"})
            return
        out = vh.transcribe(target, language=str(body.get("language") or ""),
                            model=str(body.get("model") or ""))
        req.json(200 if out.get("ok") else 409, out)
        return

    # --- speak
    text = str(body.get("text") or "").strip()
    if not text:
        req.json(400, {"ok": False, "error": "empty_text", "why": "there is nothing to say"})
        return
    if len(text) > MAX_TEXT_CHARS:
        req.json(400, {"ok": False, "error": "text_too_long",
                       "why": f"{len(text)} characters; this route accepts up to {MAX_TEXT_CHARS}"})
        return

    gate = _gate(ctx, text)
    if gate:
        req.json(403, {"ok": False, "error": "gate_refused", "gate": gate})
        return

    state = _read_state(ctx)
    spoken = vh.speak(
        text,
        voice=str(body.get("voice") or state.get("voice") or ""),
        model=str(body.get("model") or state.get("engine") or ""),
        fmt=str(body.get("format") or "mp3"),
        speed=body.get("speed", state.get("speed")),
        instructions=str(body.get("instructions") or ""),
    )
    if spoken.get("ok"):
        entry = _record_clip(ctx, spoken)
        spoken["record"] = entry
        if ctx is not None and hasattr(ctx, "receipt"):
            try:
                ctx.receipt(prompt=text[:400], output=spoken.get("path") or "",
                            extra={"module": MID, "bytes": spoken.get("bytes"),
                                   "sha256": spoken.get("sha256"), "voice": spoken.get("voice")})
            except Exception:  # noqa: BLE001 - a receipt must never undo a written clip
                pass
    spoken["card"] = build(ctx)
    req.json(200 if spoken.get("ok") else 409, spoken)


def health(ctx: Any) -> dict[str, Any]:
    """What this panel can prove about itself without speaking anything."""
    try:
        import voice_harness as vh

        probed = vh.probe()
        state = _read_state(ctx)
        return {
            "ok": True,
            "detail": (
                f"service {vh.service_base()} "
                f"{'answering' if probed.get('up') else 'not answering'}; "
                f"{len(state.get('clips') or [])} clip record(s); "
                f"engine {state.get('engine') or 'service default'}"
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"the harness did not answer: {str(exc)[:180]}"}


def register(ctx: Any) -> dict[str, Any]:
    """The routes and the pane. It owns no limb: the console asks, the harness speaks."""
    return {
        "routes": [("GET", ROUTE, data), ("POST", ROUTE, control)],
        "limbs": [],
        "panes": [
            {
                "id": "dock.voicestudio",
                "slot": "dock",
                "title": "Voice studio",
                "data": f"GET {ROUTE}",
                "refresh_s": 10,
                "inner_scroll": False,
            }
        ],
        "health": health,
    }
