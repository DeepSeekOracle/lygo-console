"""The song studio's engine room: one render at a time, a cancel that really stops it.

Δ9Φ963-LYGO-MUSIC-JOBS-v1

WHY THIS EXISTS
    The music limb is synchronous: it renders a song and only then answers. That is right for a call
    the brain makes inside a turn, and wrong for a studio panel, where a render runs for tens of
    minutes and the operator must be able to watch it, leave it, and stop it. This module turns a
    render into a JOB: a record on disk, a worker thread, a status line, and a cancel that kills the
    engine process.

WHAT IT OWNS
    `save/music/` - the engine choice the operator picked, and one file per job. Writes go through
    atomicio. Nothing here writes a song: the songs belong to `music_tools` (workspace/audio/songs).

HONESTY RULES BAKED IN
    - A job whose worker is gone is reported `interrupted`, never left reading `running` forever.
    - A render that has produced no engine output for a while says so out loud in its status line.
    - A cancel is recorded as `cancelled` only after the engine process is really dead.
    - Nothing here invents progress: the status line is either the engine's own last line or the
      limb's own named failure.

STANDING LAW THIS RESPECTS
    One engine at a time on an 8 GB card: `start` refuses while another job is running (by name),
    and the route decision lives in `music_tools.route_for_a_song`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import music_tools as mt
from atomicio import atomic_write_text, read_text
from paths import SAVE

SIGNATURE = "Δ9Φ963-LYGO-MUSIC-JOBS-v1"

#: The same stamp under the name the studio card asks for, so the card can say which engine room it
#: is talking to without importing a second module to find out.
STUDIO_SIGNATURE = SIGNATURE

SAVE_MUSIC = SAVE / "music"
JOBS_DIR = SAVE_MUSIC / "jobs"
CHOICE = SAVE_MUSIC / "engine.json"

#: A render is left alone for this long before its status line admits the engine has gone quiet.
QUIET_S = 900
#: A job older than this with no live worker is over, whatever it last wrote.
RUNNING_LIMIT_S = 7200

_LOCK = threading.RLock()
_CANCEL: set[str] = set()
_WORKERS: dict[str, threading.Thread] = {}


def _now() -> str:
    return time.strftime("%H:%M:%S")


def _iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _jobs_dir() -> Path:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    return JOBS_DIR


def _write(path: Path, obj: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(obj, indent=1, ensure_ascii=False))


def _read(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(read_text(path) or "{}")
    except (OSError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _slug(text: str, limit: int = 28) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return (out[:limit] or "song").strip("-")


def _job_path(job_id: str) -> Path:
    return _jobs_dir() / (re.sub(r"[^A-Za-z0-9_.-]", "", job_id or "") + ".json")


def engines() -> list[dict[str, Any]]:
    """Every declared engine and which of them this machine can really run."""
    return mt.engines_state()


def chosen_engine() -> str:
    """The engine in use: the operator's pick, then config, then the installed one."""
    return mt.chosen_engine()


def set_engine(engine_id: str) -> dict[str, Any]:
    """Switch engines. Refused by name when the id is not declared, or not installed here."""
    return mt.set_engine(engine_id)


def defaults() -> dict[str, Any]:
    """What the studio's controls start at. These are the ENGINE's own numbers, not invented ones.

    `segments: 0` means "one section per part of the words": this engine splits the lyrics into
    sections and sings one section at a time, so the length of a song follows from its lyrics. A fixed
    1 here silently capped every render at a single section - MEASURED: 15 s of audio for 64 min of
    compute - which is why the field starts EMPTY and the lyric sheet decides.

    3000 new tokens is the app's own default PER SECTION, and its own rate is 1000 tokens to about
    10 s of audio: one section is about 30 s of music, so a six-section song is about three minutes.
    """
    return {
        "segments": 0,
        "seed": 0,
        "max_new_tokens": 3000,
        "style": "inspiring female uplifting pop, airy bright vocal, electronic, warm bass",
    }


# --- the playlist ----------------------------------------------------------------------------


def _sidecar(song: Path) -> dict[str, Any]:
    """The record the limb wrote beside the audio. Empty when there is none - never guessed."""
    for cand in (song.with_suffix(".json"), song.parent / "song.json"):
        if cand.is_file():
            return _read(cand)
    return {}


def songs(limit: int = 25) -> list[dict[str, Any]]:
    """Every song this kit has rendered, newest first, with the facts its own record carries."""
    root = mt.SONGS_DIR
    if not root.is_dir():
        return []
    found: list[tuple[float, dict[str, Any]]] = []
    for audio in root.glob("*/*.mp3"):
        try:
            st = audio.stat()
        except OSError:
            continue
        side = _sidecar(audio)
        found.append((st.st_mtime, {
            "name": audio.name,
            "path": str(audio),
            "bytes": int(st.st_size),
            "when": time.strftime("%H:%M", time.localtime(st.st_mtime)),
            "seconds": side.get("audio_seconds"),
            "engine": side.get("engine") or "",
            "seed": side.get("seed"),
            "sections": side.get("sections"),
            "style": side.get("style") or "",
            # A take may have been named by the operator. A take with no title is reported with
            # an empty title rather than an invented one: the panel falls back to the file name,
            # which is a fact, where a made-up title would not be.
            "title": str(side.get("title") or ""),
        }))
    found.sort(key=lambda t: t[0], reverse=True)
    return [row for _, row in found[:max(1, int(limit))]]


def song_file(name: str) -> Path | None:
    """Resolve one song by bare file name, INSIDE the songs tree. Traversal is refused, by name."""
    bare = Path(str(name or "")).name
    if not bare or bare != str(name or ""):
        return None
    root = mt.SONGS_DIR
    for cand in root.glob("*/" + bare):
        try:
            if cand.is_file() and root.resolve() in cand.resolve().parents:
                return cand
        except OSError:
            continue
    return None



def retitle(name: str, title: str = "") -> dict[str, Any]:
    """Give a take a name, or take one away, without rendering anything again.

    WHY A SEPARATE ACTION: a title is a label on a file, not a property of the render. Re-rendering a
    song to change what it is called would throw away the audio (and, on this card, minutes to hours of
    compute) to change a string. So the title is written into the take's own SIDECAR - the JSON the
    engine adapter already puts beside every song - and the panel reads it from there.

    The audio file is deliberately NOT renamed. A song is reachable by bare file name through the
    kernel's media route and that name is in the operator's browser and in their downloads folder;
    renaming it would break every link already handed out. The name on disk is the engine's own
    stamped name and stays stable; the title is what the console and the operator call it.

    Refused by name on a take that is not there, so a typo cannot silently succeed.
    """
    found = song_file(name)
    if found is None:
        return {"ok": False, "error": "no_such_song", "name": str(name or ""),
                "hint": "no take by that name is in this console's songs tree"}
    side = found.with_suffix(".json")
    record = _sidecar(found) or {}
    if not isinstance(record, dict):
        record = {}
    clean = str(title or "").strip()[:120]
    if clean:
        record["title"] = clean
    else:
        record.pop("title", None)
    record["title_at"] = _now()
    try:
        side.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": "title_write_failed", "why": str(exc)[:200], "name": found.name}
    return {"ok": True, "name": found.name, "title": clean, "sidecar": str(side),
            "note": ("the take is called " + clean if clean
                     else "the title is cleared; the take is listed under its file name again")}

# --- jobs ------------------------------------------------------------------------------------


def start(style: str = "", lyrics: str = "", engine: str = "", segments: int = 0,
          seed: int = 0, max_new_tokens: int = 0, timeout: int = 0,
          title: str = "") -> dict[str, Any]:
    """Queue one render and answer at once. The render itself runs in a worker thread.

    `title` is what to CALL the take. It is optional on purpose: a render must never fail for
    want of a name, so a blank title is not an error - the engine's own stamped name stands, the
    take is listed under its file name, and `retitle` can name it afterwards without re-rendering.
    """
    style = str(style or "").strip()
    lyrics = str(lyrics or "").strip()
    if not style:
        return {"ok": False, "error": "empty_request", "hint": "say what the music should be"}
    if not lyrics:
        return {"ok": False, "error": "lyrics_required",
                "hint": "this engine sings the words it is given - write or paste the lyrics first"}
    with _LOCK:
        running = _running_job()
        if running:
            return {"ok": False, "error": "job_running", "job": running,
                    "hint": "one render at a time on this machine - cancel it or wait for it"}
        eng = (engine or chosen_engine()).strip()
        known = {e["id"]: e for e in engines()}
        if eng not in known:
            return {"ok": False, "error": "unknown_engine", "engine": eng,
                    "hint": "declared engines: " + ", ".join(sorted(known)) if known else "no engines declared"}
        if known[eng].get("state") != "installed":
            return {"ok": False, "error": "engine_not_installed", "engine": eng,
                    "hint": known[eng].get("note") or "switch to an engine that is installed here"}
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + _slug(style or lyrics)
        job = {
            "id": job_id,
            "state": "running",
            "status": "starting the engine",
            "engine": eng,
            "style": style,
            "lyrics": lyrics,
            "title": str(title or "").strip(),
            "segments": int(segments or defaults()["segments"]),
            "seed": int(seed or 0),
            "max_new_tokens": int(max_new_tokens or defaults()["max_new_tokens"]),
            "timeout": int(timeout or 0),
            "started": _now(),
            "started_iso": _iso(),
            "started_ts": time.time(),
            "song": None,
            "error": "",
        }
        _write(_job_path(job_id), job)
        worker = threading.Thread(target=_run, args=(job_id,), name="music-job-" + job_id, daemon=True)
        _WORKERS[job_id] = worker
        worker.start()
    return {"ok": True, "job": _public(job)}


def _run(job_id: str) -> None:
    """The worker: render, then record what really happened - the song, or the named failure."""
    path = _job_path(job_id)
    job = _read(path)
    try:
        mt.forget_reading()                      # the engine choice and the free card may have moved
        out = mt.music_generate(style=job.get("style", ""), lyrics=job.get("lyrics", ""),
                                seed=job.get("seed") or 0, timeout=job.get("timeout") or 0,
                                engine=job.get("engine", ""), job_id=job_id,
                                segments=job.get("segments") or 0,
                                max_new_tokens=job.get("max_new_tokens") or 0,
                                title=job.get("title", ""),
                                on_log=lambda info: _note_engine_log(path, info))
    except Exception as exc:  # noqa: BLE001 - a broken render is the job's own failure
        out = {"ok": False, "error": "music_job_failed", "hint": str(exc)[:200]}
    cancelled = job_id in _CANCEL
    song = str(out.get("path")) if (out.get("ok") and out.get("path")) else ""
    if cancelled:
        # The operator's stop wins over the limb's answer. A killed render can still come back
        # "ok" if it had already finished writing, so the file is not hidden - it is named in the
        # status and kept on the record - but this console does not call a stopped render a song.
        state = "cancelled"
        status = (f"stopped on request - the engine had already written {song}" if song
                  else "stopped on request before a song was written")
    elif out.get("ok"):
        state, status = "done", "wrote " + song
    else:
        state = "failed"
        status = str(out.get("hint") or out.get("error") or "render failed")
    job.update({
        "finished_ts": time.time(),
        "finished_iso": _iso(),
        "elapsed_s": round(time.time() - float(job.get("started_ts") or time.time()), 1),
        "song": song or None,
        "error": "" if (out.get("ok") and not cancelled) else str(
            out.get("error") or ("cancelled" if cancelled else "render_failed")),
        "hint": str(out.get("hint") or out.get("note") or "")[:300],
        "detail": {k: out.get(k) for k in ("route", "seconds", "audio_seconds", "log", "bytes") if out.get(k)},
        "state": state,
        "status": status,
    })
    # The record and the worker registry must move together, and the record must land FIRST: a job
    # whose thread has gone but whose record still says "running" reads as an interrupted render,
    # and that is exactly how a finished song was once recorded here as interrupted.
    with _LOCK:
        _write(path, job)
        _WORKERS.pop(job_id, None)
        _CANCEL.discard(job_id)
        mt.forget_stopped(job_id)                # a stop belongs to one render, not to the engine
        mt.forget_reading()                      # the card and the playlist have just changed


def _public(job: dict[str, Any]) -> dict[str, Any]:
    """One job, as the studio needs it: no lyrics echoed back into the page."""
    out = {k: v for k, v in job.items() if k not in ("lyrics",)}
    if job.get("state") == "running":
        out["elapsed_s"] = round(time.time() - float(job.get("started_ts") or time.time()), 1)
        out["status"], out["quiet_s"] = _progress(job)
    return out


def _progress(job: dict[str, Any]) -> tuple[str, float]:
    """The engine's own last line, plus how long it has been quiet. Never a guess."""
    log = (job.get("detail") or {}).get("log") or ""
    path = Path(str(log))
    if not path.is_file():
        return str(job.get("status") or "starting the engine"), 0.0
    try:
        quiet = max(0.0, time.time() - path.stat().st_mtime)
        raw = read_text(path, errors="replace")[-4000:]
    except OSError:
        return str(job.get("status") or "rendering"), 0.0
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", raw) if ln.strip()]
    beat = ""
    for ln in reversed(lines):
        if re.search(r"\d+/\d+|it/s|%\|", ln) or "Generation" in ln or "stage" in ln.lower():
            beat = ln[-160:]
            break
    if not beat and lines:
        beat = lines[-1][-160:]
    text = beat or "rendering"
    if quiet > QUIET_S:
        text += f" · engine quiet for {int(quiet // 60)} min"
    return text, quiet


def _running_job() -> dict[str, Any] | None:
    """The job the studio is waiting on, or None. A dead worker never reads as running.

    The whole read - record AND worker registry - happens under the lock, because the two must be
    seen together: the worker pops itself and writes its record as one move (see `_run`), so a
    reader that takes the record and then the registry can only ever see both sides of that move,
    never one before and one after. Reading them apart is how a finished song was once recorded here
    as an interrupted render.
    """
    with _LOCK:
        for path in sorted(_jobs_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:8]:
            job = _read(path)
            if job.get("state") != "running":
                continue
            alive = job.get("id") in _WORKERS
            age = time.time() - float(job.get("started_ts") or 0)
            if alive and age < RUNNING_LIMIT_S:
                return _public(job)
            job["state"] = "interrupted"
            job["status"] = ("its worker is gone - the console was restarted or killed while it rendered"
                             if not alive else f"still marked running after {int(age // 3600)} h")
            job["error"] = "interrupted"
            _write(path, job)
    return None


def _note_engine_log(path: Path, info: dict[str, Any]) -> None:
    """Write the engine's log path (and the plan) into the RUNNING record, under the lock.

    The panel reads the record, never the process: without this it cannot find the log that says which
    section of the song the engine is on, and a render that takes hours shows nothing at all.
    """
    with _LOCK:
        job = _read(path)
        if job.get("state") != "running":
            return
        job["engine_log"] = str(info.get("log") or "")
        job["run_dir"] = str(info.get("run_dir") or "")
        if info.get("sections"):
            job["sections"] = int(info["sections"])
            job["planned_audio_seconds"] = info.get("planned_audio_seconds")
            job["section_tags"] = info.get("section_tags") or []
            job["timeout_s"] = info.get("timeout_s")
        _write(path, job)


def state() -> dict[str, Any]:
    """The newest job, running one first. `none` when this kit has never rendered.

    A running job carries the engine's OWN position in the song (`progress`: section 3 of 6, read from
    the log the engine writes as it works). A full song is hours here: "rendering" with no position is
    indistinguishable from a render that hung, and the operator has no reason to leave it running.
    """
    running = _running_job()
    if running:
        pos = mt.stage_progress(running.get("engine_log") or "")
        if pos:
            running["progress"] = pos
            running["status"] = f"{running.get('status') or 'rendering'} - {pos['text']}"
        return running
    files = sorted(_jobs_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        # A JOB RECORD IS NOT A SONG. A song can be on this kit because the limb, a script or an earlier
        # console wrote it, so reporting on the absence of RECORDS in words about SONGS is a pane
        # contradicting its own playlist: measured live, the status said "no song rendered yet" directly
        # above two finished songs with players. Count the songs and say which fact is actually absent.
        made = len(songs())
        return {"id": "", "state": "none", "started": "", "elapsed_s": 0.0,
                "status": (f"{made} song{'' if made == 1 else 's'} on disk - nothing rendering"
                           if made else "no song rendered yet")}
    return _public(_read(files[0]))


def cancel(job_id: str = "") -> dict[str, Any]:
    """Stop a render: mark it, kill the engine process tree, then record the truth."""
    with _LOCK:
        job = _read(_job_path(job_id)) if job_id else {}
        if job.get("state") != "running":
            running = _running_job()
            job = _read(_job_path(running["id"])) if running else job
        if job.get("state") != "running":
            return {"ok": False, "error": "nothing_to_cancel", "job": _public(job) if job else {}}
        _CANCEL.add(str(job.get("id")))
        mt.mark_stopped(str(job.get("id")))       # the limb reads this before it posts work
        pid = mt.live_pid(str(job.get("id"))) or job.get("engine_pid") or ((job.get("detail") or {}).get("pid"))
    killed = _kill(pid)
    mt.forget_reading()
    out: dict[str, Any] = {"ok": True, "killed_pid": killed,
                           "job": _public(_read(_job_path(str(job.get("id")))))}
    if not killed:
        # Honest, because it is the difference between "it is already dead" and "it will stop": the
        # engine was still coming up, so there was nothing to kill yet.
        out["note"] = ("the engine was still starting, so there was no process to kill yet - the "
                       "render stops before it sings")
    return out


def _kill(pid: Any) -> int:
    """Kill one engine process and its children. 0 when there was nothing to kill."""
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return 0
    if pid <= 0:
        return 0
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=25)
        else:
            os.kill(pid, 9)
        return pid
    except (OSError, subprocess.TimeoutExpired):
        return 0


# --- the one route the panel reads ------------------------------------------------------------


def studio_state() -> dict[str, Any]:
    """Everything the studio panel needs, in one answer: engines, the live job, the playlist."""
    eng = chosen_engine()
    out = {
        "ok": True,
        "engine": eng,
        "engines": engines(),
        "job": state(),
        "songs": songs(),
        "defaults": defaults(),
        "signature": SIGNATURE,
    }
    # Whether the card can be had right now. The panel shows this before a render is pressed,
    # because "will this even fit" is the question an 8 GB box makes the operator ask every time.
    try:
        out["route"] = mt.music_status().get("route_now") or {}
    except Exception as exc:
        out["route"] = {"why": f"the card could not be read: {str(exc)[:120]}"}
    return out
