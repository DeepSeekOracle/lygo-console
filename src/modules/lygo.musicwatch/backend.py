"""lygo.musicwatch — a finder over the music limb: the faults, named with their remedy.

Δ9Φ963-LYGO-MUSICWATCH-v1

WHAT THIS IS
    The watch half of the song-making pair. `lygo.musicinfo` reports the facts; this card reports
    the FAULTS - the things that are wrong, missing, half-wired or quietly lying about a song. It
    answers one question: **if the operator asks this console for a song right now, what will stop
    it, and what exactly is missing?**

THE CHECKS, AND WHY EACH ONE IS HERE
    engine      the app directory resolves at all (no engine means no song, and the search order is
                printed with the finding)
    entry       the server this limb drives is a real file - not the broken `infer.py` CLI
    python      the app's own virtualenv interpreter is on disk
    weights     every declared weight file, by name: a stage-1 transformer or the xcodec codec that
                is absent is named with the folder it belongs in, because fetching it is the fix
    cache       the model cache the weights are resolved from
    callable    THE CHECK THAT MATTERS MOST, and the one no human can see: a limb that is
                implemented and dispatchable but missing from `tools.CORE_NAMES` is invisible to the
                console's own brain, which will answer "I have no music tool" while the source says
                otherwise. This asks `tools.core_schema()` the question the local model is asked.
    dispatch    the limb name really has a branch in the dispatcher (a source-level check, because
                calling it to find out would render a song)
    output      the folder songs land in is writable - a render that dies at the copy step is a
                whole song thrown away
    audio tools ffprobe for a song's real duration, ffmpeg for conversion
    listening   nothing left listening: a previous render's engine server still holding a port is a
                finding, because this console's rule is that a job leaves no daemon behind
    dead runs   a run folder with an engine log and no audio file: the last error line from that log
                is printed, so a failed render cannot be mistaken for a slow one

WHAT IT DELIBERATELY DOES NOT DO
    * It fixes nothing. It downloads nothing, deletes nothing, starts nothing: `owns: []`,
      `gate: none`, and its own test scans this file for write and spawn calls - the one subprocess
      it runs is a read-only process listing, and a listing that cannot run is UNCHECKED, never green.
    * It re-derives nothing. The engine resolution, the declared weights and the missing names come
      from `music_tools`; this card judges what it is handed.

GREEN MEANS EVERY CHECK RAN AND FOUND NOTHING. A check that could not run is named in `unchecked`
and drags the card to amber, because "I could not look" is not "it is fine".
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

SIGNATURE = "Δ9Φ963-LYGO-MUSICWATCH-v1"
MID = "lygo.musicwatch"
ROUTE = "/api/musicwatch"
ROUTES: tuple[tuple[str, str], ...] = (("GET", ROUTE),)

OWNERS: tuple[str, ...] = ("music_tools", "tools", "paths")
_AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".ogg")


def _at() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _short(path: Any) -> str:
    text = str(path or "")
    for marker in ("lygo_llm_console", "yue.git", "pinokio", "LYGO_MEDIA"):
        idx = text.find(marker)
        if idx >= 0:
            return text[idx:]
    return text


def _row(k: str, v: Any, note: str = "", dot: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"k": k, "v": "" if v is None else str(v)}
    if note:
        out["note"] = note
    if dot:
        out["dot"] = dot
    return out


def _group(title: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"title": title, "rows": rows}


def _light(state: str, label: str, text: str, detail: str = "", next_change: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"state": state, "label": label, "text": text}
    if detail:
        out["detail"] = detail
    if next_change:
        out["next_change"] = next_change
    return out


def _owner(name: str) -> Any | None:
    try:
        return __import__(name)  # noqa: PLC0415 - deliberately late: a missing owner is a finding
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------------------------------------
# the checks
# ------------------------------------------------------------------------------------------------
def _check(checks: list[dict[str, Any]], findings: list[dict[str, Any]], name: str, ok: Any,
           detail: str, severity: str = "amber", remedy: str = "") -> None:
    """One check's result. `ok is None` means the check could not run - named, never green."""
    checks.append({"check": name, "ok": ok, "detail": detail, "severity": "" if ok else severity})
    if not ok:
        item: dict[str, Any] = {"check": name, "severity": severity if ok is False else "unknown",
                                "detail": detail}
        if remedy:
            item["remedy"] = remedy
        findings.append(item)


def _stale_engines(timeout_s: int = 25) -> tuple[list[str], str]:
    """(pids of leftover engine servers, why it could not be checked). A read-only listing."""
    cmd = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "
           "'gradio_server\\.py|inference[/\\\\]infer\\.py' } | "
           "ForEach-Object { \"$($_.ProcessId)|$($_.Name)\" }")
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout_s,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"the process listing could not be run ({type(exc).__name__}: {exc})"
    if proc.returncode != 0:
        return [], f"the process listing exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}"
    ours = f"{os.getpid()}"
    out = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        pid = line.split("|", 1)[0].strip()
        if pid and pid != ours:
            out.append(pid)
    return out, ""


def _dead_runs(songs_dir: Path, limit: int = 4, quiet_min: int = 10) -> tuple[list[dict[str, Any]], str]:
    """Run folders that logged an engine and produced no audio: a render that failed, named.

    A run whose log is still being written is NOT dead - it is a render in progress, and calling it
    failed while the engine is working is the same kind of lie as calling a missing file done.
    MEASURED 2026-09-25 on this kit: a live 2-minute render read as a failed run before this window
    existed, so the engine log has to have been quiet for `quiet_min` minutes to count as a finding.
    """
    try:
        runs = [d for d in songs_dir.iterdir() if d.is_dir()]
    except OSError as exc:
        return [], f"the songs folder could not be listed ({type(exc).__name__}: {exc})"
    dead: list[dict[str, Any]] = []
    now = time.time()
    for run in sorted(runs, key=lambda d: d.stat().st_mtime if d.exists() else 0, reverse=True)[:limit]:
        try:
            audio = [p for p in run.rglob("*") if p.is_file() and p.suffix.lower() in _AUDIO_EXTS
                     and p.stat().st_size > 0]
        except OSError:
            continue
        if audio:
            continue
        log = run / "engine.log"
        if not log.is_file():
            continue          # a folder with no log is not evidence of a failed render
        tail = ""
        try:
            quiet = max(0.0, (now - log.stat().st_mtime) / 60.0)
            if quiet < quiet_min:
                continue      # still writing: a render in progress, not a finding
            lines = [ln.strip() for ln in log.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
            error_lines = [ln for ln in lines if "Error" in ln or "error" in ln or "Traceback" in ln]
            tail = (error_lines[-1] if error_lines else (lines[-1] if lines else ""))[:300]
        except OSError:
            tail = ""
        dead.append({"run": _short(run), "when": time.strftime("%Y-%m-%d %H:%M", time.localtime(run.stat().st_mtime)),
                     "quiet": f"quiet for {quiet:.0f} min",
                     "said": tail or "the log ended without an error line"})
    return dead, ""


def _collect() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str], dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    unchecked: list[str] = []

    mt = _owner("music_tools")
    tools_mod = _owner("tools")

    if mt is None:
        unchecked.append("music_tools could not be imported, so every engine check below is UNCHECKED - not passing")
        return checks, findings, groups, unchecked, {"state": st_of(None, checks, findings, unchecked), "engine_ok": None}

    try:
        st = mt.music_status()
    except Exception as exc:  # noqa: BLE001
        unchecked.append(f"music_tools.music_status() raised, so nothing below could be checked: {type(exc).__name__}: {exc}")
        return checks, findings, groups, unchecked, {"state": st_of(None, checks, findings, unchecked), "engine_ok": None}

    # --- engine install -------------------------------------------------------------------------
    app = st.get("app")
    _check(checks, findings, "engine", bool(app),
           f"engine app: {_short(app)}" if app else "no music engine app resolves on this machine",
           severity="red",
           remedy="install the engine (or point config `music_root` at one); the limb then refuses by name instead of inventing a song")
    _check(checks, findings, "entry", bool(st.get("entry_present")),
           f"entry script {Path(str(st.get('entry_script') or '?')).name}: "
           + ("present" if st.get("entry_present") else "MISSING - the limb drives this file"),
           severity="red",
           remedy="reinstall the engine app: the entry script is what the console starts")
    _check(checks, findings, "python", bool(st.get("python_present")),
           "the app's own python: " + ("found" if st.get("python_present") else "MISSING"),
           severity="red",
           remedy="run the app's own installer so its virtualenv exists; the console does not use its own python for this")
    cli = st.get("infer_cli") or {}
    _check(checks, findings, "cli", not cli.get("usable"),
           "the installed fork's command line cannot run (" + str(cli.get("why") or "no reason given")[:160] + ")"
           if not cli.get("usable") else "the fork's command line is usable",
           severity="amber",
           remedy="nothing to fix: the limb drives `gradio_server.py` instead, which is the entry that works")

    # --- weights --------------------------------------------------------------------------------
    missing = list(st.get("weights_missing") or [])
    _check(checks, findings, "weights", not missing,
           f"{len(st.get('weights') or [])} declared weight file(s): all on disk"
           if not missing else "not on disk: " + ", ".join(str(m) for m in missing),
           severity="red",
           remedy="fetch the named file into the engine's model cache; a song cannot render without it")
    _check(checks, findings, "cache", bool(st.get("hub")),
           f"model cache: {_short(st.get('hub'))}" if st.get("hub") else "no model cache resolved",
           severity="red")

    # --- callable by the console's own brain ----------------------------------------------------
    try:
        schema_text = str(tools_mod.core_schema()) if tools_mod is not None else ""
    except Exception as exc:  # noqa: BLE001
        schema_text = ""
        unchecked.append(f"tools.core_schema() raised, so whether the brain can call the limb is UNCHECKED: {type(exc).__name__}: {exc}")
    if tools_mod is None:
        unchecked.append("`tools` could not be imported, so whether the on-box brain can call the limb is UNCHECKED")
    elif schema_text:
        core_names = list(getattr(tools_mod, "CORE_NAMES", ()) or ())
        in_schema = "music_generate" in schema_text
        in_names = "music_generate" in core_names
        _check(checks, findings, "callable", in_schema and in_names,
               "the on-box brain is offered `music_generate` (core schema and CORE_NAMES)"
               if (in_schema and in_names) else
               "the limb is implemented but the brain is not offered it "
               f"(core schema: {'yes' if in_schema else 'no'}, CORE_NAMES: {'yes' if in_names else 'no'})",
               severity="red",
               remedy="add `music_generate` to tools.CORE_NAMES and to the core schema, then restart the console")

        limbs_mod = _owner("limbs")
        if limbs_mod is None:
            unchecked.append("`limbs` could not be imported, so the dispatcher check is UNCHECKED")
        else:
            branch = False
            try:
                src = Path(str(getattr(limbs_mod, "__file__", ""))).read_text(encoding="utf-8", errors="replace")
                branch = 'name == "music_generate"' in src
            except OSError:
                branch = False
            _check(checks, findings, "dispatch", branch,
                   "`music_generate` has a branch in the dispatcher" if branch
                   else "no dispatcher branch found for `music_generate`",
                   severity="red",
                   remedy="wire the name in limbs.py: the schema alone does not make a call land")

    # --- the folders and the tools --------------------------------------------------------------
    songs_dir = Path(str(st.get("songs_dir") or ""))
    try:
        exists = songs_dir.is_dir()
        writable = bool(st.get("songs_dir_writable"))
    except OSError:
        exists, writable = False, False
    _check(checks, findings, "output", writable,
           f"songs land in {_short(songs_dir)} (writable)" if writable else
           f"{_short(songs_dir)} is " + ("present but NOT writable" if exists else "not there yet"),
           severity="red",
           remedy="make the folder writable: a render that cannot be copied loses the whole song")
    audio_tools = [n for n, p in (("ffmpeg", st.get("ffmpeg")), ("ffprobe", st.get("ffprobe"))) if p]
    _check(checks, findings, "audio-tools", len(audio_tools) == 2,
           "ffmpeg and ffprobe both found" if len(audio_tools) == 2
           else f"found: {', '.join(audio_tools) or 'neither'} (a song still renders; its duration cannot be read)",
           severity="amber",
           remedy="install ffmpeg so a song's real duration can be read back")

    # --- nothing left listening, and nothing that died unnoticed --------------------------------
    stale, why = _stale_engines()
    if why:
        unchecked.append("whether a previous render left an engine listening is UNCHECKED: " + why)
    else:
        _check(checks, findings, "listening", not stale,
               "no music engine process is running" if not stale
               else f"{len(stale)} music engine process(es) running: pids " + ", ".join(stale),
               severity="amber",
               remedy="a render in progress looks exactly like this, so check the newest run folder first: if its log is still being written the engine is working and will end itself, and if the log is silent the process is a leftover from a render killed from outside and can be ended")

    dead, why = _dead_runs(songs_dir if songs_dir.name else Path("."))
    if why:
        unchecked.append("past renders that produced nothing are UNCHECKED: " + why)
    else:
        _check(checks, findings, "dead-runs", not dead,
               "every run folder that has an engine log also has audio" if not dead
               else f"{len(dead)} run folder(s) logged an engine and produced no audio",
               severity="amber",
               remedy="read the named log line: a failed render is not a slow one")

    if dead:
        groups.append(_group("Runs that produced no song", [
            _row(item["run"], item["when"], item["said"], dot="amber") for item in dead
        ]))
    if stale:
        groups.append(_group("Still running", [_row(f"pid {p}", "a music engine process", "should not outlive a render", dot="amber") for p in stale]))
    return checks, findings, groups, unchecked, {"state": st_of(st, checks, findings, unchecked), "engine_ok": bool(app)}


def st_of(st: dict[str, Any] | None, checks: list[dict[str, Any]],
          findings: list[dict[str, Any]], unchecked: list[str]) -> str:
    """green only when every check ran and found nothing; amber when anything could not be looked at."""
    if st is None or unchecked:
        return "amber" if checks else "grey"
    hard = [f for f in findings if f.get("severity") == "red"]
    return "amber" if (hard or findings) else "green"


# ------------------------------------------------------------------------------------------------
# the card
# ------------------------------------------------------------------------------------------------
def build(ctx: Any = None) -> dict[str, Any]:
    """One payload: what is wrong with the music limb, by name, with the remedy for each finding."""
    checks, findings, extra_groups, unchecked, verdict = _collect()
    mt = _owner("music_tools")
    st: dict[str, Any] = {}
    if mt is not None:
        try:
            st = mt.music_status()
        except Exception:  # noqa: BLE001
            st = {}

    hard = [f for f in findings if f.get("severity") == "red"]
    rows = [
        _row(c["check"], "ok" if c.get("ok") else ("unchecked" if c.get("ok") is None else "FOUND"),
             c.get("detail", ""), dot="" if c.get("ok") else "amber")
        for c in checks
    ]
    groups = [_group("Every check, in order", rows), *extra_groups]
    if findings:
        groups.append(_group("Findings, with the fix", [
            _row(f["check"], f.get("severity", ""), (f.get("detail", "") + " — " + f.get("remedy", "")).strip(" —"))
            for f in findings
        ]))

    lights = [
        _light(
            "green" if not hard else "amber",
            "Wiring",
            f"{len(checks)} check(s) run" if not unchecked else f"{len(unchecked)} unchecked",
            (", ".join(u for u in unchecked)[:200] if unchecked else "every check below could be run on this machine"),
        ),
        _light(
            "green" if not findings else ("amber" if not hard else "red"),
            "Faults",
            "none found" if not findings else f"{len(findings)} found",
            (", ".join(str(f["check"]) for f in findings))[:200],
        ),
        _light(
            "green" if st.get("ready") else "amber",
            "Ready to sing",
            "yes" if st.get("ready") else "no",
            "engine, entry, python and every weight file present" if st.get("ready")
            else "one of engine / entry / python / weights is not in place - the findings above name which",
        ),
    ]

    state = verdict.get("state") or "grey"
    if unchecked and state == "green":
        state = "amber"
    state_text = (
        "every check ran and found nothing"
        if state == "green" else
        f"{len(findings)} finding(s), {len(unchecked)} check(s) that could not run"
    )
    return {
        "ok": True,
        "signature": SIGNATURE,
        "module": MID,
        "at": _at(),
        "state": state,
        "state_text": state_text,
        "lights": lights,
        "groups": groups,
        "checks": checks,
        "findings": findings,
        "unchecked": unchecked,
        "missing": list(unchecked) + [f"{f['check']}: {f.get('detail', '')}" for f in findings],
        "sources": [
            "music_tools.music_status() — the engine resolution, the declared weights and their names",
            "tools.core_schema() / tools.CORE_NAMES — whether the on-box brain is offered the limb",
            "limbs source — whether the dispatcher has a branch for the limb name",
            "a read-only process listing — whether a previous render left an engine listening",
            "the run folders themselves — a run with a log and no audio is a failed render, not a slow one",
        ],
        "routes": [{"method": "GET", "path": ROUTE, "purpose": "this card"}],
        "refresh_s": 30,
    }


# ------------------------------------------------------------------------------------------------
# module host surface
# ------------------------------------------------------------------------------------------------
def data(ctx: Any, req: Any) -> None:
    req.json(200, build(ctx))


def health(ctx: Any) -> dict[str, Any]:
    """Can this card run its checks at all, on THIS tree, right now?"""
    if _owner("music_tools") is None:
        return {"ok": False, "detail": "`music_tools` does not import on this tree, so this card could not check anything"}
    panel = build(ctx)
    checks = panel.get("checks") or []
    if not checks:
        return {"ok": False, "detail": "no check could be run, so this card would read empty"}
    return {
        "ok": True,
        "detail": (
            f"ran {len(checks)} check(s) · {len(panel.get('findings') or [])} finding(s) · "
            f"{len(panel.get('unchecked') or [])} unchecked · state {panel.get('state')}"
        ),
    }


def register(ctx: Any) -> dict[str, Any]:
    """The routes and the pane this card claims. It claims no limb: it only looks at one."""
    return {
        "routes": [("GET", ROUTE, data)],
        "limbs": [],
        "panes": [
            {
                "id": "dock.musicwatch",
                "slot": "dock",
                "title": "Song watch",
                "data": f"GET {ROUTE}",
                "refresh_s": 30,
                "inner_scroll": False,
            }
        ],
        "health": health,
    }
