"""lygo.archiveinfo - the read-out for the Internet Archive bridge.

Δ9Φ963-LYGO-ARCHIVEINFO-v1

WHAT THIS ANSWERS
    The card beside the picture engine, the voice card and the song studio: **can this console
    reach the Internet Archive right now, which of its documented doors answered, how long each
    took, and what is the console allowed to conclude from them?** The operator cannot answer that
    anywhere else, and the wrong assumption is expensive: a search that "finds nothing" because a
    door was under load reads exactly like a page that was never archived.

        the doors        availability API / Memento resolver / CDX index - each probed, each timed
        the cost         what each door costs, measured on this machine, in seconds
        the contract     which parameters are accepted and where the behaviour is written down
        the rules        why an empty availability answer is not proof, and which door is asked
                         first when the operator asks for "closest to this date"

WHY IT EXISTS AS A CARD AND NOT A LIMB
    An operator about to search the archive should learn from a card whether the archive is
    answering at all. The actuator (`lygo.archivectrl`) is where a search is actually run; this card
    is the read-out that says whether running one is worth a wait.

WHAT IT DELIBERATELY DOES NOT DO
    * No state, no writes anywhere: `owns: []`, `gate: none`, harmless to reload on any cadence.
    * No re-derivation. Every fact is asked of the harness `wayback` (src/wayback.py) and printed as
      it was told - including the seconds each door really took.
    * It does not probe on every poll. The harness caches the probe for two minutes and counts the
      calls, so a panel refreshing every thirty seconds cannot become three network round trips.

THE HONESTY RULES IT INHERITS
    * A door that did not answer is a NAMED GAP that drags the card off green - never a zero.
    * An unreachable archive is reported as an external state with the reason, not as a console
      fault: this card never turns red about somebody else's server.
    * It never says "the service is up" because a process exists: the three doors are ASKED, and each
      one answers about itself.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

SIGNATURE = "Δ9Φ963-LYGO-ARCHIVEINFO-v1"
MID = "lygo.archiveinfo"
ROUTE = "/api/webarchiveinfo"
ROUTES: tuple[tuple[str, str], ...] = (("GET", ROUTE),)

#: Every owner this card asks for a fact. A missing one is a named gap, never a zero.
OWNERS: tuple[str, ...] = ("wayback",)

#: What the console is allowed to conclude, in the operator's words. Each line is a rule the harness
#: ENFORCES, not a slogan: `closest()` asks the resolver first precisely because of the first one.
RULES: tuple[tuple[str, str, str], ...] = (
    ("An empty availability answer", "not proof of anything",
     "measured 2026-09-27: the availability API answered 'not available' for a URL+date the CDX "
     "index has a capture for, so the resolver is asked first and the index last"),
    ("Closest to a date", "ask the Memento resolver",
     "it 302s to the capture nearest the date, answered in 0.7-2.5 s here, and names the snapshot it "
     "actually served - the CDX server's own closest= parameter is ignored on this deployment"),
    ("The list of captures", "the CDX index",
     "the record of what exists, and slow: 7-40 s measured here, with timeouts on wide domain "
     "queries - that cost is reported, never hidden"),
    ("A negative", "a conclusion of the checks that ran",
     "every door that was asked is named in the answer, so 'not archived' is never a guess"),
    ("What was served", "read from the URL the archive landed on",
     "asking for 1999 and being served a 2002 capture is REPORTED as that, with the drift"),
)


def _at() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _short(seconds: Any) -> str:
    try:
        return f"{float(seconds):.2f} s"
    except (TypeError, ValueError):
        return "?"


def _row(k: str, v: Any, note: str = "", dot: str = "") -> dict[str, Any]:
    return {"k": str(k), "v": str(v), "note": str(note), "dot": str(dot)}


def _group(title: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"title": title, "rows": rows}


def _light(state: str, label: str, text: str, detail: str = "") -> dict[str, Any]:
    return {"state": state, "label": label, "text": text, "detail": detail}


def _name_of(door: str) -> str:
    """The shell's own label prefix, so two doors never paint as the same light."""
    return {
        "availability API": "Availability",
        "Memento resolver": "Resolver",
        "CDX index (paging)": "Index",
    }.get(str(door), str(door)[:12])


def build(ctx: Any = None, st: dict[str, Any] | None = None) -> dict[str, Any]:
    """The whole card from the harness's own answer. Reads nothing itself."""
    st = st or {}
    probe = st.get("probe") or {}
    checks = list(probe.get("checks") or [])
    gaps = list(st.get("gaps") or probe.get("gaps") or [])

    lights: list[dict[str, Any]] = []
    door_rows: list[dict[str, Any]] = []
    for c in checks:
        ok = bool(c.get("ok"))
        door_rows.append(_row(
            _name_of(c.get("door") or ""),
            f"answered in {_short(c.get('seconds'))}" if ok else f"did not answer ({c.get('error') or 'no answer'})",
            f"{c.get('what') or ''} — {c.get('answer') or ''}",
            "green" if ok else "red",
        ))
        lights.append(_light(
            "green" if ok else "red",
            _name_of(c.get("door") or ""),
            f"answered in {_short(c.get('seconds'))}" if ok else str(c.get("answer") or "")[:60],
            str(c.get("what") or ""),
        ))

    if not checks:
        lights.append(_light("grey", "Archive", "unchecked",
                             "the harness reported no probe at all, so this card cannot judge it"))
        gaps.append("no door was probed at all - the card cannot judge the archive")

    up = int(probe.get("doors_up") or 0)
    total = int(probe.get("doors") or len(checks) or 0)
    if not total:
        # No door was probed at all: that is not the archive being down and not a half-open door -
        # it is a card with nothing to judge, which is grey with the reason above.
        state, state_text = "grey", "no archive door has been probed on this console yet"
    elif up == total and total:
        state, state_text = "green", f"all {total} doors answered · slowest {_short(probe.get('slowest_s'))}"
    elif up:
        state = "amber"
        state_text = f"{up}/{total} doors answered · {len(gaps)} named gap(s)"
    else:
        state = "amber"
        state_text = "the Internet Archive could not be reached from this machine"

    rules_rows = [_row(k, v, note, "green") for k, v, note in RULES]

    contract_rows: list[dict[str, Any]] = []
    for ep in st.get("endpoints") or []:
        contract_rows.append(_row(ep.get("door") or "", str(ep.get("url") or ""),
                                  f"{ep.get('what') or ''} — documented at {ep.get('doc') or ''}"))

    cost_rows = [
        _row("Rows per call", str((st.get("caps") or {}).get("rows") or "") + " max",
             f"default {(st.get('caps') or {}).get('default_rows')} rows, "
             f"{(st.get('caps') or {}).get('bytes')} bytes ceiling"),
        _row("Timeouts", ", ".join(
            f"{k.split('_')[0]} {v}s" for k, v in (st.get("timeouts") or {}).items()),
             "generous on purpose: a fast timeout would call a busy archive broken"),
        _row("Retries", "one, on 429/503 only",
             "the archive's own back-pressure; a TIMEOUT is not retried, because a 40 s query would "
             "become an 80 s wait the operator is already watching"),
        _row("Canary", str(probe.get("canary") or "-"),
             "a real host on purpose: a fabricated domain must never be what a health check reports on"),
        _row("Probe cache", "120 s", "so a panel poll is not three round trips"),
        _row("Calls this session", ", ".join(
            f"{k} {v}" for k, v in (probe.get("calls") or {}).items()) or "-",
             "counted by the harness, which is the only place that knows"),
    ]

    missing = list(dict.fromkeys(gaps))
    if not st.get("reachable"):
        missing.append("the archive is unreachable from this machine right now, so a search would "
                       "report nothing found for the wrong reason")

    return {
        "signature": SIGNATURE,
        "at": _at(),
        "state": state,
        "state_text": state_text,
        "lights": lights,
        "groups": [
            _group("The doors", door_rows),
            _group("What the console may conclude", rules_rows),
            _group("The contract", contract_rows),
            _group("What one call costs", cost_rows),
        ],
        "missing": missing,
        "reachable": bool(st.get("reachable")),
        "doors_up": up,
        "doors": total,
        "canary": probe.get("canary") or "",
        "measured": str(probe.get("measured") or ""),
        "cached": bool(probe.get("cached")),
    }


def _owner_gap(name: str, exc: Exception) -> str:
    return f"{name} could not be asked: {type(exc).__name__}: {str(exc)[:140]}"


def data(ctx: Any, req: Any) -> None:
    """GET /api/webarchiveinfo - the whole read-out in one answer, which is what the panel polls."""
    st: dict[str, Any] = {}
    gaps: list[str] = []
    try:
        import wayback as wb

        st = wb.state(deep=True)
        gaps = list(st.get("gaps") or [])
    except Exception as exc:  # noqa: BLE001 - a card that cannot read is a named gap, not a 500
        gaps = [_owner_gap("wayback", exc)]
        st = {"gaps": gaps}
    card = build(ctx, st)
    card["missing"] = list(dict.fromkeys(list(card.get("missing") or []) + gaps))
    req.json(200, {**{k: v for k, v in st.items() if k != "probe"}, "card": card,
                   "missing": card["missing"], "state": card["state"],
                   "state_text": card["state_text"], "at": card["at"]})


def health(ctx: Any) -> dict[str, Any]:
    """Is the harness readable on THIS tree, and what has it already measured? Cheap BY DESIGN.

    This runs inside the host's `load()`, which is inside the console's boot and inside the first
    request a page makes. Asking the Internet Archive from here is the wrong place for it: measured
    on this machine, a stalled TLS read to `web.archive.org` cost 7 s of boot per load - and before
    the host bounded a health call at all, the console sat in `load()` for minutes, which is
    indistinguishable from a hung console.

    So this answers only what it can answer locally and claims NO verdict (`ok: None`, the host's own
    value for "checked nothing"), naming the one thing it did not check. The live door check belongs
    to the card: the operator opens it on purpose, and the harness caches the probe for two minutes.
    """
    try:
        import wayback as wb

        probes = wb.state(deep=False)
        doors = len(probes.get("endpoints") or [])
        detail = (f"the harness answers with {doors} door(s) declared; the live door check runs when this "
                  f"card is read, not at boot (the harness caches it for 120 s)")
        calls = probes.get("call_counts") or {}
        if calls:
            detail += " · calls so far in this process: " + ", ".join(f"{k}={v}" for k, v in sorted(calls.items()))
        return {"ok": None, "detail": detail}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"the harness did not answer: {str(exc)[:180]}"}


def register(ctx: Any) -> dict[str, Any]:
    """The route and the pane. This card has no limb and no state."""
    return {
        "routes": [("GET", ROUTE, data)],
        "limbs": [],
        "panes": [
            {
                "id": "dock.archiveinfo",
                "slot": "dock",
                "title": "Archive",
                "data": f"GET {ROUTE}",
                "refresh_s": 30,
                "inner_scroll": False,
            }
        ],
        "health": health,
    }
