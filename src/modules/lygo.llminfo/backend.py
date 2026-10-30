"""lygo.llminfo — one panel for whatever LLM is hooked up (LOCAL engine or an API).

Δ9Φ963-LYGO-LLMINFO-v1

WHAT THIS IS
    The console already pulls a lot of LLM facts: the selected model and its file facts, the
    live window budget and the record counters, the API switch and its last answer, the clock.
    They are scattered across `/api/health`, `/api/compaction`, `/api/cloud` and `/api/models`.
    This module collects them into ONE answer the shell can render in one place, and - the part
    that matters - it says out loud which facts do NOT exist rather than leaving a blank the
    operator reads as "zero".

WHAT IT DELIBERATELY DOES NOT DO
    * No new state, no writes, no gate: it is a read-only view (`owns: []`).
    * No duplicated kernel logic. Every number comes from the module that owns it
      (`registry`, `compaction`, `cloud_api`, `world_clock`, `version`). If a value is not
      reachable, it lands in `missing` - never in a plausible-looking default.
    * No key material. `cloud_api.public_status()` answers `has_key`/`key_count` only, and this
      module copies that: a value from `config/api.json` must never reach a payload.

RATE WINDOWS ARE THE PROVIDER'S POLICY, NOT OUR GUESS
    Only DeepSeek publishes a time-of-day rate window. Verified 2026-09-20 against the
    provider's own pricing page: "Off-peak rates are half of the peak rates. Peak hours are
    01:00 - 04:00 and 06:00 - 10:00 UTC, Monday through Friday (all other hours are off-peak)."
    The windows are defined on the UTC clock and never move for daylight saving, so the local
    rendering here is DERIVED from the UTC window each time (never hard-coded), and the window's
    local clock time therefore follows whatever the operator's machine is doing.
    For every other wired provider the honest answer is "no published window", which is what
    `state: grey` with a named reason means.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

SIGNATURE = "Δ9Φ963-LYGO-LLMINFO-v1"

# --- the provider rate windows we actually know -------------------------------------------
PEAK = {
    "deepseek": {
        "label": "DeepSeek",
        "windows_utc": [(1, 0, 4, 0), (6, 0, 10, 0)],  # (start_h, start_m, end_h, end_m)
        "weekdays_only": True,
        "discount": "half the peak rate all other hours",
        "source": "https://api-docs.deepseek.com/quick_start/pricing/",
    },
}

UNPUBLISHED = (
    "{provider} publishes no time-of-day rate window, so there is nothing to warn about: "
    "its card is flat. If that ever changes, the window belongs here as data, with the source."
)

# Facts nobody publishes, said plainly instead of rendered as an empty cell.
NOT_PUBLISHED = {
    "limits": "providers do not return that model's token limits in a chat response; the provider's own docs are the authority, so this panel will not invent a number",
    "quota": "no provider here returns remaining quota or rate-limit headers in these responses; when the API answers with a limit you will see it as last_code/last_error below",
    "usage": "the local engine reports no per-turn token usage over its API; counts on this panel are the record's own estimate (chars/token is shown with them)",
}


# --------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------
def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _size(n: Any) -> str:
    """Bytes at whatever scale they are actually at: "0.00 GB" for a 300 KB journal hides it."""
    try:
        v = float(n)
    except Exception:  # noqa: BLE001
        return "—"
    for unit, step in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if v >= step:
            return f"{v / step:.2f} {unit}"
    return f"{int(v)} B"


def _mins(h: int, m: int) -> int:
    return int(h) * 60 + int(m)


def _hhmm(mins: int) -> str:
    mins = int(mins) % 1440
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _local_label(moments: _dt.datetime) -> str:
    return moments.astimezone().strftime("%a %H:%M")


def window_local(midnight_utc: _dt.datetime, start_m: int, dur: int = 0) -> str:
    """Render a UTC-clock window in the operator's own clock, for the day given.

    Derived from today's UTC midnight and then converted, so a DST change moves the local
    label on its own - which is exactly what a UTC-defined window does to a human.
    """
    moment = midnight_utc + _dt.timedelta(minutes=int(start_m))
    return moment.astimezone().strftime("%H:%M")


# --------------------------------------------------------------------------------------
# the rate-window light
# --------------------------------------------------------------------------------------
def peak_state(provider: Any, api_active: bool = False, now_utc: _dt.datetime | None = None) -> dict[str, Any]:
    """Green off-peak / red peak / grey when the provider publishes no window.

    Pure function: no clock of its own unless asked, no I/O. That is what makes it testable
    at any hour of any weekday - including the two blocks and the weekend, which are the cases
    an operator would otherwise only meet by chance.
    """
    key = str(provider or "").strip().lower()
    spec = PEAK.get(key)
    now = now_utc or _utc_now()
    if spec is None:
        return {
            "id": "peak",
            "label": "API rate",
            "state": "grey",
            "text": f"no window published for {key or 'this provider'}",
            "detail": UNPUBLISHED.format(provider=key or "This provider"),
            "source": "",
        }

    windows = [(_mins(s[0], s[1]), _mins(s[2], s[3])) for s in spec["windows_utc"]]
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    mins = _mins(now.hour, now.minute)
    weekday = now.weekday()  # 0 = Monday
    weekend = spec["weekdays_only"] and weekday >= 5
    hit = None if weekend else next((w for w in windows if w[0] <= mins < w[1]), None)

    if hit is not None:
        state, when = "red", today + _dt.timedelta(minutes=hit[1])
        text = "PEAK — full rate"
    else:
        state, when = "green", None
        for day in range(0, 8):
            wd = (weekday + day) % 7
            if spec["weekdays_only"] and wd >= 5:
                continue
            for start, _end in windows:
                if day == 0 and start <= mins:
                    continue
                when = today + _dt.timedelta(days=day, minutes=start)
                break
            if when is not None:
                break
        text = "off-peak — " + spec["discount"]

    if when is None:  # nothing left in the week: never happens with a Mon-Fri window, but say so
        away, at_local, at_utc = None, "unknown", "unknown"
    else:
        away = max(0, int((when - now).total_seconds() // 60))
        at_local, at_utc = _local_label(when), when.strftime("%a %H:%M UTC")

    spans = [
        f"{window_local(today, w[0])}–{window_local(today, w[1])}"
        for w in windows
    ]
    in_use = "the API brain is on" if api_active else "the local brain is on, so no API rate applies"
    return {
        "id": "peak",
        "label": "API rate",
        "state": state,
        "text": text,
        "detail": (
            f"{spec['label']} rate window · peak {spans[0]} and {spans[1]} local"
            f"{' (Mon–Fri)' if spec['weekdays_only'] else ''} · {in_use}"
        ),
        "next_change": (
            f"{'peak ends' if state == 'red' else 'peak starts'} in {away} min ({at_local} local · {at_utc})"
            if away is not None else "no boundary in the coming week"
        ),
        "windows_local": spans,
        "windows_utc": [f"{_hhmm(w[0])}–{_hhmm(w[1])} UTC" for w in windows],
        "weekdays_only": spec["weekdays_only"],
        "discount": spec["discount"],
        "source": spec["source"],
    }


# --------------------------------------------------------------------------------------
# reading the owners of each fact
# --------------------------------------------------------------------------------------
def _row(k: str, v: Any, note: str = "") -> dict[str, Any]:
    return {"k": k, "v": "—" if v is None or v == "" else str(v), "note": note}


def _when(v: Any) -> str:
    """A moment, however the owner handed it over: epoch seconds, or a timestamp already written."""
    if v in (None, "", 0):
        return "never"
    try:
        stamp = float(v)
        if stamp > 1e8:  # an epoch, not a duration
            return _dt.datetime.fromtimestamp(stamp).astimezone().strftime("%a %H:%M")
    except (TypeError, ValueError):
        pass
    return str(v)


def _secs(v: Any) -> str:
    try:
        return f"{int(float(v))}s"
    except (TypeError, ValueError):
        return "—"


def _model_group() -> tuple[dict[str, Any] | None, list[str]]:
    missing: list[str] = []
    try:
        import registry  # noqa: PLC0415 - kernel module, present at run time only

        reg = registry.load() or {}
        selected = reg.get("selected") or ""
        rec = registry.get(selected) or {} if selected else {}
        reach = rec.get("reach") if isinstance(rec.get("reach"), dict) else {}
        rows = [
            _row("Model", selected or "none selected"),
            _row("Chosen by", reg.get("selected_source") or "?", "manual = the operator pinned it; ram = tuned to free memory"),
            _row("Kind", rec.get("kind"), f"{rec.get('architecture') or '?'} architecture"),
            _row("Context", rec.get("ctx"), "the model's own window (tokens)"),
            _row("On disk", _size(rec.get("bytes")), "weights only; KV cache and projector are extra"),
            _row("File", rec.get("path")),
            _row("Held by", rec.get("source"), "lygo_vault = this kit's own store"),
            _row("GPU layers", rec.get("n_gpu_layers"), "99 = every layer offloaded"),
            _row("Vision", "yes" if rec.get("mmproj") else "no", "a photo needs a projector (mmproj)"),
            _row(
                "This kit",
                ("carries it" if reach.get("portable") else ("runs it, does not carry it" if reach.get("reachable") else "cannot see it")),
                reach.get("why") or "",
            ),
        ]
        if selected and not reach.get("portable"):
            missing.append(
                f"the selected model ({selected}) is not carried by this kit, so this kit is not "
                "portable on its own — a copy in the kit's own vault fixes that"
            )
        return {"title": "Model (local)", "rows": rows}, missing
    except Exception as exc:  # noqa: BLE001 - a panel must never take the console down
        missing.append(f"model facts unavailable: {exc}")
        return None, missing


def _token_group() -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    """The live window budget, from the owner of it (compaction)."""
    missing: list[str] = []
    light: dict[str, Any] = {}
    try:
        import compaction  # noqa: PLC0415

        st = compaction.safe_status() or {}
        w = st.get("window") if isinstance(st.get("window"), dict) else {}
        if not w:
            missing.append("the record answered without a window report")
            return None, missing, light
        ctx = int(w.get("ctx") or 0)
        live = int(w.get("live_tokens") or 0)
        compact_at = int(w.get("compact_at") or 0)
        used = float(w.get("used_pct") or 0.0)
        auto = float(w.get("auto_compact_pct") or 0.0)
        rc = (st.get("recall") or {}) if isinstance(st, dict) else {}
        _rline = (f"recall: {int(rc.get('runs') or 0)} search(es), last {int(rc.get('chars') or 0):,} chars"
                  if rc.get("runs") else "recall: not used yet - the history still fits the window")
        left = max(0, compact_at - live) if compact_at else 0
        rows = [
            _row("Engine window", f"{ctx:,} tokens" if ctx else "?", "what the engine can hold this turn"),
            _row("In the window now", f"{live:,} tokens",
                 f"{used:.1f}% of the {int(w.get('history_tokens') or 0):,}-token history room - "
                 f"{100.0 * live / max(1, ctx):.1f}% of the {ctx:,}-token engine window"),
            _row("The record behind it", f"{int(w.get('journal_tokens') or 0):,} tokens",
                 f"{int(w.get('journal_turns') or 0)} turns since the last fold - {float(w.get('journal_pct') or 0):,.0f}% of one window, all of it filed and searchable; the engine never sees it all at once"),
            _row("Room before auto-compact", f"{left:,} tokens", f"the record compacts at {auto:.0f}% ({compact_at:,} tokens)"),
            _row("History budget", f"{int(w.get('history_tokens') or 0):,} tokens", "reserved for the conversation"),
            _row("Reserved", f"{int(w.get('system_reserve') or 0):,} sys + {int(w.get('answer_reserve') or 0):,} answer + {int(w.get('safety_reserve') or 0):,} safety", "never handed to history"),
            _row("KV cache", f"{int(w.get('kv_mib_estimate') or 0)} MiB", "estimate for this window and context type"),
            _row("Estimate basis", f"{w.get('chars_per_token')} chars/token", NOT_PUBLISHED["usage"]),
            _row("Next turn", "compacts first" if w.get("will_compact_next_turn") else "answers as is", _rline),
        ]
        state = "green"
        if auto and used >= auto:
            state = "red"
        elif auto and used >= 0.6 * auto:
            state = "amber"
        light = {
            "id": "tokens",
            "label": "Window",
            "state": state,
            "text": f"{used:.0f}% full · {left:,} tokens of room",
            "detail": f"auto-compact at {auto:.0f}% — the record folds the oldest turns and keeps going",
        }
        return {"title": "Tokens", "rows": rows}, missing, light
    except Exception as exc:  # noqa: BLE001
        missing.append(f"token budget unavailable: {exc}")
        return None, missing, light


def _api_group() -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    missing: list[str] = []
    api: dict[str, Any] = {}
    try:
        import cloud_api  # noqa: PLC0415

        api = cloud_api.public_status() or {}
        on = bool(api.get("active")) or str(api.get("mode") or "") == "api"
        rows = [
            _row("Switch", "API (cloud)" if on else "LOCAL (engine)", "LOCAL is the default and the fallback"),
            _row("Provider", api.get("label") or api.get("provider"), api.get("url") or ""),
            _row("Model", api.get("model")),
            _row("Key", f"yes · {api.get('key_count') or 0} saved" if api.get("has_key") else "none saved", "never shown, never sent anywhere but the provider"),
            _row("Wired", ", ".join(api.get("keys_wired") or []) or "—", "chain: " + " → ".join(api.get("chain") or []) or "chain: —"),
            _row("Handoffs", api.get("handoffs"), "cloud failures that fell back to the local engine"),
            _row("Degraded", "yes" if api.get("degraded") else "no", api.get("last_error") or ""),
            _row("Last answer", api.get("last_code"), f"{_when(api.get('last_at'))} · {api.get('last_error') or 'ok'}"),
            _row("Cooldown", _secs(api.get("cooldown_s")), "after a handoff, plain turns stay local for this long"),
            _row("Token limits", "not published", NOT_PUBLISHED["limits"]),
            _row("Remaining quota", "not published", NOT_PUBLISHED["quota"]),
        ]
        return {"title": "API", "rows": rows}, missing, {"on": on, "provider": api.get("provider") or api.get("default_provider") or ""}
    except Exception as exc:  # noqa: BLE001
        missing.append(f"API facts unavailable: {exc}")
        return None, missing, {}


def _record_group() -> tuple[dict[str, Any] | None, list[str]]:
    missing: list[str] = []
    try:
        import compaction  # noqa: PLC0415

        st = compaction.safe_status() or {}
        roll = st.get("roll_due") if isinstance(st.get("roll_due"), dict) else {}
        rows = [
            _row("Turns in this session", st.get("turns_live"), f"{st.get('turns_total')} recorded in total"),
            _row("Sealed behind", st.get("turns_sealed"), "older turns folded into the record"),
            _row("Compactions", st.get("turns_compacted"), f"last: {st.get('last_compact_iso') or 'never'}"),
            _row("Journal", _size(st.get("journal_bytes")), "the live transcript"),
            _row("Sealed bytes", _size(st.get("sealed_bytes"))),
            _row("Archive", _size(st.get("archive_bytes")), f"{st.get('sessions_sealed')} session(s) sealed"),
            _row("Roll due", "yes" if roll.get("roll") else "no", roll.get("why") or ""),
        ]
        return {"title": "Record", "rows": rows}, missing
    except Exception as exc:  # noqa: BLE001
        missing.append(f"record counters unavailable: {exc}")
        return None, missing


def _clock_group() -> tuple[dict[str, Any] | None, list[str], str]:
    missing: list[str] = []
    try:
        import world_clock  # noqa: PLC0415

        pulse = world_clock.pulse() or {}
        rows = [
            _row("Local", pulse.get("local_iso"), pulse.get("local_tz") or ""),
            _row("UTC", pulse.get("utc_iso"), "rate windows are defined on this clock"),
            _row("Day", pulse.get("weekday")),
        ]
        return {"title": "Clock", "rows": rows}, missing, str(pulse.get("utc_iso") or "")
    except Exception as exc:  # noqa: BLE001
        missing.append(f"clock unavailable: {exc}")
        return None, missing, ""


def _console_group(ctx: Any) -> tuple[dict[str, Any] | None, list[str]]:
    missing: list[str] = []
    rows: list[dict[str, Any]] = []
    try:
        import version  # noqa: PLC0415

        rows.append(_row("Release", version.release(), version.tag() or ""))
        rows.append(_row("Build", version.stamp()))
    except Exception as exc:  # noqa: BLE001
        missing.append(f"release unknown: {exc}")
    rows.append(_row("Edition", getattr(ctx, "edition", None) or "?", "pc · usb · web — one console, three systems"))
    rows.append(_row("Kit", getattr(ctx, "kit_root", None) or getattr(ctx, "root", None) or "?"))
    return ({"title": "Console", "rows": rows} if rows else None), missing


# --------------------------------------------------------------------------------------
# the answer
# --------------------------------------------------------------------------------------
# How a verdict reads to a person. The last three are the point of the group: a model this host
# cannot run is still reported, and labelled as such instead of offered as a brain.
VERDICT_WORD = {
    "gpu_full": "runs on the GPU — fast",
    "gpu_partial": "GPU + CPU — usable",
    "cpu_ok": "CPU only — works, slow",
    "cpu_tight": "very tight — expect swapping",
    "too_big": "will NOT run here",
    "unknown": "size unknown — unjudged",
}
VERDICT_ORDER = {
    "gpu_full": 0,
    "gpu_partial": 1,
    "cpu_ok": 2,
    "cpu_tight": 3,
    "too_big": 4,
    "unknown": 5,
}


def _availability_group(limit: int = 14) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    """What this PC can run, and what it can *see* but cannot run.

    The console scans every place a model can live, so this panel exists to keep "here" and
    "usable here" apart: a 300 GB model on an 8 GB card is a fact about this machine, and the
    operator asked to see it rather than have it quietly dropped.

    A record with no `fit` block has simply not been scanned with placement in mind yet, and that is
    said out loud. This panel never guesses a verdict and never renders a gap as a blank.
    """
    missing: list[str] = []
    light: dict[str, Any] = {}
    try:
        import registry  # noqa: PLC0415 - kernel module, present at run time only

        models = [m for m in (registry.load().get("models") or []) if isinstance(m, dict)]
        if not models:
            missing.append("no models are registered yet — a scan is what finds what this PC can see")
            return None, missing, light
        judged = [m for m in models if isinstance(m.get("fit"), dict)]
        if not judged:
            missing.append(
                "models are registered but none carry a placement verdict, so this panel cannot say "
                "which would run here — a scan with placement enabled is what answers that"
            )
            return None, missing, light

        def rank(m: dict[str, Any]) -> tuple[int, int]:
            verdict = str((m.get("fit") or {}).get("verdict") or "unknown")
            return (VERDICT_ORDER.get(verdict, 6), -int(m.get("bytes") or 0))

        rows: list[dict[str, Any]] = []
        runs = cannot = 0
        for m in sorted(judged, key=rank):
            fit = m.get("fit") or {}
            verdict = str(fit.get("verdict") or "unknown")
            if verdict == "sidecar":
                continue
            if verdict in ("gpu_full", "gpu_partial", "cpu_ok"):
                runs += 1
            else:
                cannot += 1
            rows.append(
                _row(
                    str(m.get("id") or "?"),
                    VERDICT_WORD.get(verdict, verdict),
                    f"{_size(m.get('bytes'))} · {fit.get('text') or fit.get('reason') or ''}",
                )
            )
        head = [
            _row("Visible here", f"{len(rows)} model(s)", "every model this PC can see, wherever it lives"),
            _row("Runnable now", runs, "fits this host's GPU or its RAM"),
            _row(
                "Visible, not runnable",
                cannot,
                "too large for this host — listed on purpose, and never offered as the brain",
            ),
        ]
        shown = rows[:limit]
        if len(rows) > limit:
            shown.append(_row("…", f"{len(rows) - limit} more", "the panel shows the first " + str(limit)))
        state = "green" if runs else ("amber" if cannot else "grey")
        light = {
            "id": "models",
            "label": "Local models",
            "state": state,
            "text": f"{runs} runnable · {cannot} visible but too large",
            "detail": "the console scans every fixed drive; a model it cannot run is still named here",
        }
        return {"title": "Models here", "rows": head + shown}, missing, light
    except Exception as exc:  # noqa: BLE001 - a panel must never take the console down
        missing.append(f"local model list unavailable: {exc}")
        return None, missing, light


def _models_group() -> tuple[dict[str, Any] | None, list[str]]:
    """What this box can RUN, and what it can only SEE - the checker's own measurements.

    Every model the console can see is listed with the labels src/model_check.py measured (text, coder,
    image-in, image-out, sound, embed) and a dot: green = this host ran it and answered a real turn,
    grey = visible but not runnable here (a RIG limit, named in words - never an agent fault), amber =
    attempted and failed on the wiring. A model nobody has attempted is said to be unchecked rather
    than shown as if it had passed.
    """
    missing: list[str] = []
    try:
        import model_route  # noqa: PLC0415

        rep = model_route.rig_report()
    except Exception as e:  # noqa: BLE001 - a gap is named, never raised
        return None, [
            f"the model checker's results cannot be read ({type(e).__name__}), so this panel cannot say "
            f"which models this box can run - press the checker to measure them"
        ]
    rows: list[dict[str, Any]] = []
    for m in rep.get("routes") or []:
        caps = "+".join(m.get("caps") or []) or "unlabelled"
        if m.get("runs"):
            dot = "green"
            note = (f"ran here · {m['gen_tps']} tok/s · boot {m['boot_s']}s"
                    if m.get("gen_tps") else "ran here") + ((" · " + str(m["gpu_backend"])) if m.get("gpu_backend") else "")
        elif m.get("verdict") == "unchecked":
            dot, note = "grey", "not checked yet - no verdict measured on this box"
        elif m.get("fail_class") == "rig":
            dot, note = "grey", f"rig limit: {m.get('why') or 'did not boot here'}"
        else:
            dot = "amber"
            note = str(m.get("hint") or f"{m.get('verdict')}: {m.get('why') or ''}")
        rows.append({**_row(m.get("id"), caps, note), "dot": dot})
    if not rows:
        missing.append("no models are registered on this tree yet, so there is nothing to label")
    title = f"Models this box can run ({rep.get('models_running', 0)} of {rep.get('models_total', 0)})"
    return {"title": title, "rows": rows}, missing


def build(ctx: Any = None) -> dict[str, Any]:
    """Collect everything into one panel. Never raises: a panel that dies is worse than a gap."""
    groups: list[dict[str, Any]] = []
    missing: list[str] = []
    lights: list[dict[str, Any]] = []

    model_group, miss = _model_group()
    token_group, miss2, token_light = _token_group()
    api_group, miss3, api_state = _api_group()
    record_group, miss4 = _record_group()
    clock_group, miss5, utc_iso = _clock_group()
    console_group, miss6 = _console_group(ctx)
    avail_group, miss7, avail_light = _availability_group()
    models_group, miss8 = _models_group()
    for chunk in (miss, miss2, miss3, miss4, miss5, miss6, miss7, miss8):
        missing.extend(chunk)

    peak = peak_state(api_state.get("provider"), api_active=bool(api_state.get("on")))
    if api_state.get("on"):
        peak["text"] = peak["text"]
    else:
        peak["detail"] = peak["detail"] + " · the window still applies the moment you switch to API"
    lights.append(peak)
    if token_light:
        lights.append(token_light)
    if avail_light:
        lights.append(avail_light)

    # the rate window's own group, so the numbers behind the light are readable too
    rate_rows = [
        _row("Status", "PEAK" if peak["state"] == "red" else ("off-peak" if peak["state"] == "green" else "no published window")),
        _row("Window (local)", " and ".join(peak.get("windows_local") or []) or "—", "derived from the UTC window, follows your clock"),
        _row("Window (UTC)", " and ".join(peak.get("windows_utc") or []) or "—", "as the provider defines it — never shifts for DST"),
        _row("Applies", "Mon–Fri only" if peak.get("weekdays_only") else "every day", "the provider's own rule"),
        _row("Boundary", peak.get("next_change")),
        _row("Tested by", api_state.get("provider") or "no provider wired", "a red light here means the same tokens cost more right now"),
        _row("Source", peak.get("source") or "—", "the provider's published policy, not our inference"),
    ]

    groups.append({"title": "Now", "rows": [
        _row("Brain", "API (cloud)" if api_state.get("on") else "LOCAL (engine)", "the console falls back to LOCAL on an API failure"),
        _row("Rate", peak["text"], peak["detail"]),
    ]})
    if model_group:
        groups.append(model_group)
    if token_group:
        groups.append(token_group)
    groups.append({"title": "Rate window", "rows": rate_rows})
    if api_group:
        groups.append(api_group)
    if record_group:
        groups.append(record_group)
    if clock_group:
        groups.append(clock_group)
    if console_group:
        groups.append(console_group)
    if avail_group:
        groups.append(avail_group)
    # What this machine can actually run - measured, not claimed. A rig limit is a fact about the box,
    # so it sits here as a grey row and never drags this card (or the agent) out of green.
    if models_group:
        groups.append(models_group)

    if not any(g["title"] == "Tokens" for g in groups) and NOT_PUBLISHED["usage"] not in missing:
        missing.append(NOT_PUBLISHED["usage"])

    # The shell reads an optional `state` (green/amber/red/grey) to paint this card and the
    # strip's chip. An info panel has one honest state to give: whether the owners of its facts
    # answered. A gap the provider itself has (no published rate window) is NOT a fault - it is
    # named in `missing` where it belongs - so it never drags this card out of green.
    owners = ("registry", "compaction", "cloud_api", "world_clock", "version")
    unreadable = []
    for _name in owners:
        try:
            __import__(_name)  # noqa: PLC0415
        except Exception:  # noqa: BLE001 - a gap, reported below, never raised
            unreadable.append(_name)

    return {
        "ok": True,
        "signature": SIGNATURE,
        "module": "lygo.llminfo",
        "state": "grey" if unreadable else "green",
        "state_text": (
            f"could not read {len(unreadable)} of {len(owners)} fact owners" if unreadable
            else "every fact owner answered"
        ),
        "at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "utc": utc_iso or _utc_now().isoformat(timespec="seconds"),
        "lights": lights,
        "groups": groups,
        "missing": missing,
        "sources": [
            "registry.load() — the selected model record",
            "compaction.safe_status() — the live window and the record",
            "cloud_api.public_status() — the switch, never a key",
            "world_clock.pulse() — local and UTC",
            "version.release()/stamp() — which build this is",
            "model_check.json + model_route.rig_report() — what this host proved it can run",
        ],
        "refresh_s": 5,
    }


def data(ctx: Any, req: Any) -> None:
    """GET /api/llminfo — the one panel."""
    req.json(200, build(ctx))


def health(ctx: Any) -> dict[str, Any]:
    """Can the panel read the owners of its facts on THIS tree, right now?"""
    have, want = [], []
    for name in ("registry", "compaction", "cloud_api", "world_clock"):
        want.append(name)
        try:
            __import__(name)  # noqa: PLC0415
            have.append(name)
        except Exception:  # noqa: BLE001 - reported, never raised
            pass
    panel = build(ctx)
    if not have:
        return {"ok": False, "detail": "no fact owner importable: " + ", ".join(want)}
    return {
        "ok": True,
        "detail": (
            f"reads {len(have)}/{len(want)} fact owners ({', '.join(have)}) · "
            f"{len(panel['groups'])} group(s), {len(panel['lights'])} light(s), {len(panel['missing'])} named gap(s)"
        ),
    }


def register(ctx: Any) -> dict[str, Any]:
    return {
        "routes": [("GET", "/api/llminfo", data)],
        "limbs": [],
        "panes": [{"id": "dock.llm", "slot": "dock", "title": "LLM data", "data": "GET /api/llminfo", "refresh_s": 5, "inner_scroll": False}],
        "health": health,
        "startup": None,
        "shutdown": None,
    }
