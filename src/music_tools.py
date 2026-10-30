"""Music limbs: make a SONG with a local engine. Lyrics + style in, stereo audio out.

Δ9Φ963-LYGO-MUSIC-TOOLS-v1

WHAT THIS IS
    The third making limb beside pictures (`media_tools.image_generate`) and voice
    (`media_tools.sound_speak`). It renders a full song - vocals and accompaniment together - with a
    local model, and writes it into `workspace/audio/songs/<run>/`, returning the path it really wrote.

THE ENGINE IS NOT SHIPPED, IT IS FOUND
    A song engine is tens of gigabytes of weights, so this module reaches it through config keys and
    never assumes a drive letter, exactly like the picture engine:

        config/console.json
          "music_engine":  "yue"                 # which declared engine renders a song
          "music_root":    ""                    # where THIS machine keeps music engines+weights
          "yue_root":      ""                    # the YuE app folder (holds inference/gradio_server.py)
          "music_timeout_s": 3600
          "music_profile": 0                     # 0 = choose from the live card reading, else 1..5

    Resolution order for the app, all of it recorded in engine_state()["resolve"]["candidates"]:
    explicit "yue_root" -> <music_root>/tools/yue -> the standard Pinokio app folder -> a bounded scan.
    A machine with no engine is reported as exactly that (`no_music_engine`), with the missing entry
    script named and the URL that would fill it - never as "music does not work".

WHY A SERVER AND NOT A ONE-SHOT CLI
    The picture engine is a single CLI binary, so this kit runs it one-shot and nothing is left
    listening. This engine is not: its own CLI entry (`inference/infer.py`, upstream) is BROKEN in the
    installed fork - it reads `args.sdpa` at line 65 and never defines the argument - so the only
    entry point that runs is its server (`inference/gradio_server.py`, the same one the operator's
    Pinokio window starts). MEASURED on this host 2026-09-25: `infer.py --help` raises
    `AttributeError: 'Namespace' object has no attribute 'sdpa'`, while `gradio_server.py` defines
    `--sdpa` and serves. So this limb starts that server on a FREE port it picks, waits for its own
    printed URL, posts one job, and terminates it in a `finally` - one engine at a time on an 8 GB
    card, and nothing listening when the call returns. The result is taken from the OUTPUT FOLDER
    (a file that appeared during this run), never from the server's chatty return value: a song is
    evidenced by bytes on disk, which is the same rule the picture limb follows.

THE WEIGHTS ARE THE OPERATOR'S, NOT THIS KIT'S
    YuE is Apache-2.0 (m-a-p), the YuEGP fork is DeepBeepMeep's; the kit ships the wiring and the
    resolver, never 20 GB of weights. A declared weight that is absent is reported as
    wired-but-not-downloaded, naming the file and where it belongs.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import music_lyrics as ml
from paths import KIT_ROOT, WORKSPACE, console_cfg, stack_root

SIGNATURE = "Δ9Φ963-LYGO-MUSIC-TOOLS-v1"

#: Where songs go on this kit. The limb writes here; the read-out module only reads.
SONGS_DIR = WORKSPACE / "audio" / "songs"

#: The declared engines. `yue` is the one that is installed on the steward's box; the other two are
#: declared so a card can say "wired but not downloaded" rather than pretending they do not exist.
ENGINES: tuple[dict[str, Any], ...] = (
    {
        "id": "yue",
        "label": "YuE (YuEGP fork) - lyrics2song, vocals + accompaniment",
        "kind": "lyrics2song",
        "entry": "inference/gradio_server.py",
        "venv": ("env/Scripts/python.exe", "env/bin/python"),
        "license": "Apache-2.0 (YuE, m-a-p) - YuEGP fork by DeepBeepMeep",
        "page": "https://github.com/deepbeepmeep/YuEGP",
        "upstream": "https://github.com/multimodal-art-projection/YuE",
        # MEASURED on this host's 8 GB card, 2026-09-25: one 1500-token section took 3850 s of compute
        # at profile 3, and the app's own rate is 1000 tokens to about 10 s of audio. Sections are sung
        # one after another inside ONE job, so the length of a song is bought in hours of compute.
        "cost": ("one section is minutes to about two hours here (measured: 1500 tokens = 1 h 4 min at "
                 "profile 3 on the 8 GB card), and the sections of your lyrics add up"),
    },
    {
        "id": "ace_step",
        # The install this kit drives is the upstream ACE-Step app, headless, through the one-shot CLI
        # built beside it (`render_cli.py`, which prints machine-readable progress and result lines).
        "label": "ACE-Step 1.5 turbo (diffusers) - lyrics2song, full songs with vocals, FAST",
        "kind": "lyrics2song",
        "entry": "render_cli.py",
        "venv": (".venv/Scripts/python.exe", ".venv/bin/python"),
        "license": "MIT (ACE-Step 1.5, ace-step) - engine and weights both MIT",
        "page": "https://github.com/ace-step/ACE-Step",
        "upstream": "https://huggingface.co/ACE-Step",
        # MEASURED on this host, 2026-09-25: 30 s of 48 kHz stereo song in 50.1 s of render, plus about
        # 45 s of model load (the lyric LM is 41 s of that). The engine itself read the card as tier3 /
        # 8 GB and said it can hold 480 s of audio, so a three-minute song is minutes here.
        "cost": ("minutes, not hours: measured here at 30 s of song in 50 s of render plus ~45 s of "
                 "model load, and this card can hold up to 480 s of audio in one go"),
    },
    {
        "id": "stable_audio",
        "label": "Stable Audio (stable-audio-tools) - text2audio, instrumentals and SFX",
        "kind": "text2music",
        "entry": "run_gradio.py",
        "venv": ("env/Scripts/python.exe", "env/bin/python"),
        "license": "MIT (stable-audio-tools) - weights carry their own licence per checkpoint",
        "page": "https://github.com/Stability-AI/stable-audio-tools",
        "upstream": "https://huggingface.co/stabilityai",
        "cost": "instrumentals and SFX, no vocals, and no checkpoints downloaded here",
    },
)

#: Which engines this kit has an ADAPTER for - the code that actually drives them. Declaring an
#: engine without one is honest (the card says so, the switch refuses by name) and is how the next
#: engine gets added without pretending. MEASURED 2026-09-25: only the YuE path exists end to end.
ENGINE_ADAPTERS: dict[str, str] = {"yue": "yue", "ace_step": "acestep"}

#: Where the app of each engine is looked for when nothing is configured, under the music root.
ENGINE_SUBDIRS: dict[str, tuple[str, ...]] = {
    "yue": ("tools/yue", "yue", "YuE"),
    "ace_step": ("tools/ace-step", "ace-step", "acestep"),
    "stable_audio": ("tools/stable-audio", "stable-audio", "stableaudio"),
}

#: ACE-Step installed by hand rather than through Pinokio's script: its app folder, by convention.
ACE_STEP_FALLBACKS: tuple[str, ...] = (
    "C:/pinokio/api/acestep.git/app",
    "C:/pinokio/api/ace-step.git/app",
    "D:/pinokio/api/acestep.git/app",
)

#: Non-Pinokio installs of the same apps, so a machine that installed them by hand is still found.
#: The Pinokio apps are the measure: `C:\pinokio\api\<app>.git\app` is where its installer puts them.
STABLE_AUDIO_FALLBACKS: tuple[str, ...] = (
    "C:/pinokio/api/stableaudio.git/app",
    "C:/pinokio/api/stable-audio.git/app",
)

#: The file names a YuE install must have. Each is reported by name when it is missing.
YUE_MODELS: tuple[dict[str, str], ...] = (
    {"id": "stage1-cot", "hub": "models--m-a-p--YuE-s1-7B-anneal-en-cot",
     "note": "stage 1, lyrics to audio codes (the 'cot' checkpoint is what the cot path uses)"},
    {"id": "stage1-icl", "hub": "models--m-a-p--YuE-s1-7B-anneal-en-icl",
     "note": "stage 1 in-context-learning checkpoint - only needed for audio-prompt covers"},
    {"id": "stage2", "hub": "models--m-a-p--YuE-s2-1B-general",
     "note": "stage 2, audio codes to 16 kHz waveforms"},
    {"id": "xcodec", "rel": "inference/xcodec_mini_infer/final_ckpt/ckpt_00360000.pth",
     "note": "the music codec itself (1.36 GB) - without it nothing decodes"},
    {"id": "vocal-decoder", "rel": "inference/xcodec_mini_infer/decoders/decoder_131000.pth",
     "note": "the vocal vocoder decoder"},
    {"id": "inst-decoder", "rel": "inference/xcodec_mini_infer/decoders/decoder_151000.pth",
     "note": "the instrumental vocoder decoder"},
)

#: Where a YuE app is looked for, after the configured root, in order.
APP_FALLBACKS: tuple[str, ...] = (
    "tools/yue",
    "../yue",
    "C:/pinokio/api/yue.git/app",
    "C:/pinokio/api/yue/app",
)

#: Free VRAM a full-GPU quantized render wants. The engine's own published figure for the quantized
#: path is under 4 GB, and profile 3 additionally holds the codec and its decoders; this is a floor
#: used to CHOOSE a route, and the route a render actually took is reported in its result.
RENDER_NEED_MIB = 4200
RESERVE_MIB = 512

#: Free VRAM must also clear this before a full-GPU profile is worth choosing at all.
OFFLOAD_PROFILE = 5          # quantized + max offload: the route for a card the chat model holds
FULL_PROFILE = 3             # quantized, full GPU: the route for a free card on this class of box

_CAND_RE: list[dict[str, str]] = []


#: What a browser may be served for a rendered song, by extension. The kernel's media route reads
#: this, the same way it reads the picture types: a song the studio lists plays in place, with no
#: second copy of the file anywhere on the machine.
MEDIA_AUDIO_TYPES: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".m4a": "audio/mp4",
}


def workspace_song_path(name: str) -> Path | None:
    """Resolve a bare file name to a rendered song inside this kit's songs tree.

    Basename only, and the resolved file must really sit under the songs root: a name that carries a
    path is refused rather than joined, so no request can walk out of the tree.
    """
    bare = Path(str(name or "")).name
    if not bare or bare != str(name or ""):
        return None
    try:
        root = SONGS_DIR.resolve()
        for cand in SONGS_DIR.glob("*/" + bare):
            if cand.is_file() and root in cand.resolve().parents:
                return cand
    except OSError:
        return None
    return None


# --- engines: what is declared, what is installed, and which one renders ----------------------

#: Renders in flight, keyed by job id -> the engine process's pid. A cancel needs the pid WHILE the
#: render is running, which is exactly when the job record on disk cannot carry it yet.
_LIVE: dict[str, int] = {}


def _register(job_id: str, proc: Any) -> None:
    if job_id and proc is not None:
        try:
            _LIVE[str(job_id)] = int(proc.pid)
        except (AttributeError, TypeError, ValueError):
            pass


def _unregister(job_id: str) -> None:
    _LIVE.pop(str(job_id or ""), None)


def live_pid(job_id: str) -> int:
    """The pid of the engine rendering this job, or 0. The studio's cancel reads this."""
    return int(_LIVE.get(str(job_id or "")) or 0)


#: Job ids the operator has already stopped. This exists because a stop can land while the engine is
#: still coming up, when there is no pid to kill yet: without the flag the record would say
#: "cancelled" while the engine went on to hold the card and render for an hour.
_STOPPED: set[str] = set()


def mark_stopped(job_id: str) -> None:
    """Record that the operator stopped this render. Read by `music_generate` before it posts work."""
    if job_id:
        _STOPPED.add(str(job_id))


def is_stopped(job_id: str) -> bool:
    return bool(job_id) and str(job_id) in _STOPPED


def forget_stopped(job_id: str) -> None:
    """Clear the flag once the render is over, so no later job can inherit a stop."""
    _STOPPED.discard(str(job_id or ""))


def _choice_path() -> Path:
    from paths import SAVE

    return SAVE / "music" / "engine.json"


def chosen_engine() -> str:
    """The engine in use: what the operator picked in the studio, then config, then the installed.

    The studio's own file wins over config because it is the more recent statement of intent; config
    stays the shipped default so a fresh kit renders without anyone opening the panel.
    """
    pick = ""
    try:
        from atomicio import read_text

        pick = str(json.loads(read_text(_choice_path()) or "{}").get("engine") or "").strip()
    except (OSError, ValueError):
        pick = ""
    declared = {str(e["id"]) for e in ENGINES}
    if pick and pick in declared:
        return pick
    cfg = str(_cfg().get("music_engine") or "").strip()
    if cfg in declared:
        return cfg
    return "yue"


def set_engine(engine_id: str) -> dict[str, Any]:
    """Switch engines. Refused by name for an id this build does not declare."""
    want = str(engine_id or "").strip()
    known = {e["id"]: e for e in engines_state()}
    if want not in known:
        return {"ok": False, "error": "unknown_engine", "engine": want, "declared": sorted(known),
                "hint": "declared engines: " + ", ".join(sorted(known))}
    try:
        from atomicio import atomic_write_text

        path = _choice_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(
            {"engine": want, "set_iso": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=1))
    except OSError as exc:
        return {"ok": False, "error": "choice_not_saved", "why": str(exc)[:200]}
    return {"ok": True, "engine": want, "state": known[want].get("state"),
            "note": known[want].get("note"), "adapter": known[want].get("adapter") or ""}


def _engine_app(engine: dict[str, Any]) -> Path | None:
    """Where this engine's app sits on THIS machine: config, then the music root, then its own
    installer's place (Pinokio puts an app at `api/<app>.git/app`)."""
    eid = str(engine.get("id") or "")
    cands: list[Path] = []
    raw = str(_cfg().get(f"{eid}_root") or "").strip()
    if raw:
        cands.append(Path(raw))
    for sub in ENGINE_SUBDIRS.get(eid, ()):
        cands.append(music_root() / sub)
    if eid == "stable_audio":
        cands.extend(Path(x) for x in STABLE_AUDIO_FALLBACKS)
    elif eid == "ace_step":
        cands.extend(Path(x) for x in ACE_STEP_FALLBACKS)
    elif eid == "yue":
        # Only YuE gets the general fallbacks: handing them to another engine would report YuE's own
        # folder as that engine's app, which reads as "installed" for something that is not there.
        cands.extend(Path(x) for x in APP_FALLBACKS)
    for cand in cands:
        try:
            if cand.is_dir():
                return cand
        except OSError:
            continue
    return None


def _engine_python(engine: dict[str, Any], app: Path | None) -> Path | None:
    if app is None:
        return None
    for rel in (engine.get("venv") or ()):
        cand = app / str(rel)
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def engines_state() -> list[dict[str, Any]]:
    """Every declared engine, with what is really on this machine and what is missing, by name.

    THREE STATES, and only the first can render today: `installed` (app, its own python and every
    weight are on disk), `declared` (some of that is missing - named), and no adapter (`wired: false`
    - the switch is honest about it and refuses the render rather than rendering with the wrong one).
    """
    out: list[dict[str, Any]] = []
    for engine in ENGINES:
        eid = str(engine.get("id") or "")
        entry = str(engine.get("entry") or "")
        app = _engine_app(engine)
        py = _engine_python(engine, app)
        cost = str(engine.get("cost") or "")
        entry_present = bool(app and entry and (app / entry).is_file())
        missing: list[str] = []
        weights: list[dict[str, Any]] = []
        version = ""
        if eid == "yue":
            if app is not None:
                weights = _weights(app)
                missing.extend(str(w.get("id") or w.get("hub") or "?") for w in weights
                               if not (w.get("present") or w.get("exists")))
            else:
                missing.append("the YuE app itself (inference/gradio_server.py)")
        elif eid == "stable_audio":
            # Its checkpoints live under the app's own cache, and its launcher pulls them under a
            # scrambled repo name - so the honest check is "is there a models--* folder", not a
            # name match against the upstream id.
            pulls: list[Path] = []
            try:
                for root in ({app.parent / "cache", app / "cache"} if app else set()):
                    pulls.extend(sorted(root.glob("models--*")))
            except OSError:
                pulls = []
            weights = [{"id": p.name, "present": True, "path": str(p)} for p in pulls]
            if not pulls:
                missing.append("its audio checkpoints (no models--* folder in its cache)")
            if app is None:
                missing.append("its app itself (run_gradio.py)")
        elif eid == "ace_step":
            if app is not None:
                weights = _acestep_weights(app)
                missing.extend(str(w.get("id")) for w in weights if not w.get("present"))
            else:
                missing.append("the ACE-Step app itself (render_cli.py beside it)")
        else:
            if app is None:
                missing.append(f"its app and binary ({entry})")
            else:
                missing.append(f"its weights ({eid} is declared but no checkpoint is on disk)")
        if not entry_present and "its app" not in " ".join(missing) and app is not None:
            missing.append(entry)
        if py is None:
            missing.append("its own python (" + ", ".join(engine.get("venv") or ("env",)) + ")")
        usable = bool(app and entry_present and py and not missing)
        #: Whether the app half is whole - app, entry and its own python. Weights are checked
        #: separately by the limb, which can name the exact file that is missing.
        app_ready = bool(app and entry_present and py)
        if usable and ENGINE_ADAPTERS.get(eid):
            installed = True
            note = "installed here - it can render"
        else:
            installed = False
            if usable:
                # The app is whole and this build still cannot drive it. Saying "installed" here would
                # offer a choice the render then refuses, so the card says which half is missing.
                note = ("the app and its own python are on disk, but this build has no adapter for it "
                        "- it cannot render yet")
                missing.append("an adapter in this build (the switch is real, the render is not)")
            else:
                note = "declared, not usable yet: " + "; ".join(missing)
        out.append({
            "id": eid,
            "label": str(engine.get("label") or eid),
            "kind": str(engine.get("kind") or ""),
            "state": "installed" if installed else "declared",
            "wired": bool(ENGINE_ADAPTERS.get(eid)),
            "app_ready": app_ready,
            "adapter": ENGINE_ADAPTERS.get(eid, ""),
            "note": note,
            # What this engine costs on THIS machine, in its own measurement. An 8 GB card turns a song
            # into a choice between a long job and a shorter one, and the operator cannot make that
            # choice without numbers - so the number travels with the engine, not in a wiki.
            "cost": cost,
            "missing": missing,
            "app": str(app) if app else "",
            "entry": entry,
            "entry_present": entry_present,
            "python": str(py) if py else "",
            "weights": weights,
            "license": str(engine.get("license") or ""),
            "page": str(engine.get("page") or ""),
            "upstream": str(engine.get("upstream") or ""),
            "version": version,
            "chosen": eid == chosen_engine(),
        })
    return out


def _cfg() -> dict[str, Any]:
    try:
        return console_cfg() or {}
    except Exception:  # noqa: BLE001
        return {}


def _stamp(prefix: str = "song") -> str:
    return time.strftime("%Y%m%d-%H%M%S") + f"_{prefix}"


def _slug(text: str, limit: int = 32) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:limit].rstrip("-") or "song")


def _record(kind: str, source: str, path: Any, verdict: str, reason: str = "") -> dict[str, str]:
    row = {"kind": kind, "source": source, "path": str(path or ""), "verdict": verdict, "reason": reason}
    _CAND_RE.append(row)
    return row


def music_root() -> Path:
    """Where THIS machine keeps music engines and their weights.

    An explicit "music_root" wins. Otherwise the media root's own sibling (`<media_root>/music`) is
    used so a box that already declares <MEDIA> for pictures and voice keeps music beside it,
    and a kit that ships nothing at all simply has no root - which engine_state() says out loud.
    """
    raw = str(_cfg().get("music_root") or "").strip()
    if raw:
        return Path(raw)
    media = str(_cfg().get("media_root") or "").strip()
    if media:
        return Path(media) / "music"
    return KIT_ROOT / "models" / "music"


def yue_app() -> Path | None:
    """The YuE app folder (the one holding inference/gradio_server.py), or None with a recorded why."""
    del _CAND_RE[:]
    entry = "inference/gradio_server.py"
    explicit = str(_cfg().get("yue_root") or "").strip()
    cands: list[tuple[Path, str]] = []
    if explicit:
        cands.append((Path(explicit), 'config "yue_root"'))
    cands.append((music_root() / "tools/yue", "music_root/tools/yue"))
    for rel in APP_FALLBACKS:
        p = Path(rel)
        cands.append((p if p.is_absolute() else (KIT_ROOT / rel).resolve(), f"kit-relative:{rel}"))
    for path, source in cands:
        try:
            ok_entry = (path / entry).is_file()
            is_dir = path.is_dir()
        except OSError:
            ok_entry, is_dir = False, False
        if ok_entry:
            _record("music-engine", source, path, "won", f"found {entry}")
            return path
        _record("music-engine", source, path,
                "skipped", "not a directory" if not is_dir else f"no {entry}")
    return None


def yue_python(app: Path) -> Path | None:
    """The interpreter that runs the engine: the app's OWN venv, never this kit's Python.

    YuE needs torch, flash-attn, triton and mmgp at pinned versions; running it under the console's
    interpreter would fail on the first import. The venv travels with the app.
    """
    for rel in ("env/Scripts/python.exe", "env/bin/python"):
        p = app / rel
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def _hub_dir(app: Path) -> Path:
    """Where this app's HuggingFace cache lives. Pinokio keeps it beside the app, not in %USERPROFILE%."""
    for cand in (app.parent / "cache" / "HF_HOME", app.parent.parent / "cache" / "HF_HOME"):
        try:
            if (cand / "hub").is_dir():
                return cand
        except OSError:
            continue
    env = os.environ.get("HF_HOME", "").strip()
    if env and (Path(env) / "hub").is_dir():
        return Path(env)
    return Path.home() / ".cache" / "huggingface"


def _weights(app: Path) -> list[dict[str, Any]]:
    """Every declared weight file, with whether it is really on disk. Absent is named, never guessed."""
    hub = _hub_dir(app) / "hub"
    out: list[dict[str, Any]] = []
    for spec in YUE_MODELS:
        if "hub" in spec:
            base = hub / spec["hub"]
            present = False
            hugest = 0
            try:
                snaps = sorted((base / "snapshots").glob("*")) if (base / "snapshots").is_dir() else []
                for s in snaps:
                    got = [p for p in s.iterdir() if p.suffix in (".safetensors", ".bin", ".pth")]
                    if got:
                        present = True
                        hugest = max(hugest, sum(p.stat().st_size for p in got if p.is_file()))
            except OSError:
                present = False
            out.append({"id": spec["id"], "path": str(base), "present": present,
                        "bytes": hugest, "note": spec["note"]})
        else:
            p = app / spec["rel"]
            try:
                size = p.stat().st_size if p.is_file() else 0
            except OSError:
                size = 0
            out.append({"id": spec["id"], "path": str(p), "present": size > 0,
                        "bytes": size, "note": spec["note"]})
    return out


def _acestep_weights(app: Path) -> list[dict[str, Any]]:
    """What ACE-Step needs on disk: a DiT, a lyric LM, the VAE and a text encoder, each by name.

    Its own folder layout, not a hub cache - so each piece is checked by the name the app itself uses
    (`checkpoints/<name>`), and a piece that is missing is named rather than counted.
    """
    out: list[dict[str, Any]] = []
    for name, note in (("acestep-v15-turbo", "the turbo diffusion model (8 steps, no CFG)"),
                       ("acestep-5Hz-lm-0.6B", "the small lyric/metadata language model"),
                       ("Qwen3-Embedding-0.6B", "the text encoder its captions are read with"),
                       ("vae", "the audio VAE")):
        p = app / "checkpoints" / name
        size = 0
        try:
            if p.is_dir():
                size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        except OSError:
            size = 0
        out.append({"id": name, "path": str(p), "present": size > 0, "bytes": size, "note": note})
    return out


def ffmpeg_bin(name: str = "ffmpeg") -> Path | None:
    """ffmpeg/ffprobe: on PATH, else the winget package folder. Used to measure and to re-encode."""
    found = shutil.which(name)
    if found:
        return Path(found)
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Microsoft" / "WinGet" / "Packages"
    try:
        for hit in sorted(base.glob(f"*FFmpeg*/**/bin/{name}.exe")):
            if hit.is_file():
                return hit
    except OSError:
        pass
    return None


def audio_seconds(path: Path) -> float | None:
    """Length in seconds, read by ffprobe. None when nothing could measure it - never a guess."""
    exe = ffmpeg_bin("ffprobe")
    if not exe:
        return None
    try:
        p = subprocess.run([str(exe), "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(path)],
                           capture_output=True, text=True, timeout=30)
        return round(float((p.stdout or "").strip().splitlines()[0]), 1) if p.returncode == 0 else None
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def _free_port() -> int:
    """A port nobody owns. The engine is told to bind exactly this, so two consoles cannot collide."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _reachable(port: int, timeout: float = 0.6) -> bool:
    """Is the music engine answering yet? Polled while it boots, so the attempt budget stays small.

    MEASURED 2026-09-25: the caller polls every (2 s + this), and a closed loopback port costs the whole
    budget here (WinError 10061 after ~2,007 ms). At 1.5 s each idle poll cost 1,500 ms; the caller's own
    deadline still decides how long it waits.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=timeout) as r:
            return int(getattr(r, "status", 200)) < 500
    except Exception:  # noqa: BLE001 - any failure is "not up yet"
        return False


def route_for_a_song(need_mib: int = RENDER_NEED_MIB) -> dict[str, Any]:
    """Which profile this song should render on, from the LIVE card reading, before anything spawns.

    A full-GPU profile on a card the chat model is holding is how "the music tool is broken" is
    experienced. The offload profile is the honest alternative here: the engine streams layers from
    RAM, so it still renders - slower - on a card with nothing free. Both are real renders; the
    result of the render says which one happened.
    """
    from hardware import free_vram_mib, holder_note

    free = int(free_vram_mib() or 0)
    who = holder_note()
    if free >= need_mib + RESERVE_MIB:
        return {"route": "cuda", "profile": FULL_PROFILE, "free_mib": free, "need_mib": need_mib,
                "why": f"the card has {free} MiB free and this profile wants {need_mib} MiB"}
    why = f"the card has {free} MiB free but a full-GPU render wants {need_mib} MiB"
    if who:
        why += f"; {who}"
    return {"route": "cpu-offload", "profile": OFFLOAD_PROFILE, "free_mib": free, "need_mib": need_mib,
            "why": why + " - rendering with layers streamed from RAM: slower, and it needs no free VRAM"}


def engine_state() -> dict[str, Any]:
    """Everything a card or the limb needs to know about this machine's music engine. Never raises."""
    engine_id = str(_cfg().get("music_engine") or "yue").strip().lower() or "yue"
    declared = next((e for e in ENGINES if e["id"] == engine_id), None)
    app = yue_app()
    py = yue_python(app) if app else None
    weights = _weights(app) if app else []
    missing = [w["id"] for w in weights if not w["present"]]
    entry = (app / "inference" / "gradio_server.py") if app else None
    out = {
        "ok": True,
        "signature": SIGNATURE,
        "engine": engine_id,
        "engine_label": (declared or {}).get("label", ""),
        "music_root": str(music_root()),
        "app": str(app) if app else None,
        "app_exists": bool(app),
        "entry_script": str(entry) if entry else None,
        "entry_present": bool(entry and entry.is_file()),
        "python": str(py) if py else None,
        "python_present": bool(py),
        "hub": str(_hub_dir(app)) if app else None,
        "weights": weights,
        "weights_missing": missing,
        "infer_cli": {
            "path": str(app / "inference" / "infer.py") if app else None,
            # Measured, and the reason this limb drives the server instead: the fork's CLI reads an
            # argument it never declares, so it cannot start at all.
            "usable": False,
            "why": "the installed fork's infer.py reads args.sdpa and never declares --sdpa, so its "
                   "CLI cannot run; the app's own server (gradio_server.py) is the entry that works",
        },
        "songs_dir": str(SONGS_DIR),
        "songs_dir_writable": _writable(SONGS_DIR),
        "ffmpeg": (lambda p: str(p) if p else None)(ffmpeg_bin("ffmpeg")),
        "ffprobe": (lambda p: str(p) if p else None)(ffmpeg_bin("ffprobe")),
        "timeout_s": int(_cfg().get("music_timeout_s") or 3600),
        "resolve": list(_CAND_RE),
        "engines": [
            {"id": e["id"], "label": e["label"], "license": e["license"], "page": e["page"],
             "installed": bool(app) if e["id"] == "yue" else _other_engine_installed(e)}
            for e in ENGINES
        ],
        "ready": bool(app and py and entry and entry.is_file() and not missing),
        "last_song": last_song(),
    }
    return out


def _other_engine_installed(engine: dict[str, Any]) -> bool:
    """A declared engine that is not YuE: is its entry binary on disk (under the music root or kit)?"""
    rel = str(engine.get("entry") or "")
    for base in (music_root(), KIT_ROOT):
        try:
            if (base / rel).is_file():
                return True
        except OSError:
            continue
    return False


def _writable(path: Path) -> bool:
    """A real write test: a folder that merely exists is not a folder a song can land in."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def last_song() -> dict[str, Any] | None:
    """The newest song this kit rendered, from the file itself plus its sidecar record if present."""
    try:
        hits = [p for p in SONGS_DIR.glob("*/*.mp3") if p.is_file() and p.stat().st_size > 0]
    except OSError:
        return None
    if not hits:
        return None
    newest = max(hits, key=lambda p: p.stat().st_mtime)
    rec: dict[str, Any] = {"path": str(newest), "bytes": newest.stat().st_size,
                           "when": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(newest.stat().st_mtime))}
    side = newest.with_suffix(".json")
    if side.is_file():
        try:
            extra = json.loads(side.read_text(encoding="utf-8"))
            if isinstance(extra, dict):
                for k in ("engine", "seed", "profile", "route", "segments", "audio_seconds",
                          "render_seconds", "lyrics", "style", "stems"):
                    if k in extra:
                        rec[k] = extra[k]
        except (OSError, ValueError):
            rec["sidecar_unreadable"] = str(side)
    return rec


# ------------------------------------------------------------------------------------------------
# the render
# ------------------------------------------------------------------------------------------------
def _style_text(style: str) -> str:
    """One line of music tags, in the shape the engine's own example uses."""
    s = re.sub(r"\s+", " ", (style or "").strip())
    return s or "upbeat uplifting electronic pop bright warm vocal"


def _lyrics_text(lyrics: str) -> str:
    """Lyrics in the engine's section-tag shape, straight from the console's own plan.

    Built by `music_lyrics.singable` on purpose: the plan is what the estimate line counts, so the
    words that render and the number the operator was shown cannot disagree. MEASURED 2026-09-25:
    the old builder recognised only bare `[verse]`-style tags, so a sheet written `[VERSE 1]` - the
    operator's own shape - fell through to the untagged path and those bracket lines would have been
    SUNG; and it returned text without a trailing newline, which the engine's own splitter reads as
    "the last section never ended", dropping it. A written song planned 7 sections and arrived as 6.
    """
    return ml.singable(lyrics)


def _job_payload(style: str, lyrics: str, segments: int, seed: int, max_new_tokens: int) -> dict[str, Any]:
    """The exact body the app's own UI sends for one song - same order, same meaning.

    MEASURED 2026-09-25: Gradio needs a placeholder for the trailing `gr.State` the server owns, so
    a body with 10 slots comes back `ValueError: An event handler (generate_song) didn't receive
    enough input values (needed: 11, got: 10)` and no song - the state slot is null on the wire and
    filled in on the server. The audio-prompt slots stay null because this limb renders from lyrics,
    not from a reference track; 0.0 / 30.0 / 1 are the app's own defaults for prompt start, prompt
    end and repeat.
    """
    return {"data": [style, lyrics, int(segments), int(seed), int(max_new_tokens),
                     None, None, 0.0, 30.0, 1, None]}


def _start_server(py: Path, app: Path, profile: int, port: int, timeout_s: int,
                  log_path: Path) -> tuple[subprocess.Popen | None, str]:
    """Spawn the engine's server on a port we chose and wait until it is really serving.

    The app's own folder is the run directory and its cache is the model cache: HF_HOME is set to the
    app's own hub because the checkpoint ids are resolved from the HuggingFace cache, and a console
    process does not inherit Pinokio's environment. Without it the engine would try to download the
    weights again instead of using the ones already on disk.
    """
    argv = [str(py), "gradio_server.py", "--profile", str(profile), "--server_port", str(port),
            "--verbose", "1"]
    env = dict(os.environ)
    hub = _hub_dir(app)
    env.update({
        "HF_HOME": str(hub),
        "HUGGINGFACE_HUB_CACHE": str(hub / "hub"),
        "TORCH_HOME": str(hub.parent / "TORCH_HOME"),
        "GRADIO_TEMP_DIR": str(hub.parent / "GRADIO_TEMP_DIR"),
        "GRADIO_ANALYTICS_ENABLED": "False",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
    })
    for dead in ("LYGO_CONSOLE_PORT", "LYGO_LLAMA_PORT", "LYGO_EMBED_PORT", "LYGO_COLIBRI_PORT"):
        env.pop(dead, None)
    try:
        log = log_path.open("w", encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, f"could not open the engine log: {exc}"
    try:
        proc = subprocess.Popen(argv, cwd=str(app / "inference"), env=env,
                                stdout=log, stderr=subprocess.STDOUT, text=True)
    except OSError as exc:
        log.close()
        return None, f"could not start the engine: {exc}"
    deadline = time.time() + max(120, min(timeout_s, 1800))
    while time.time() < deadline:
        if proc.poll() is not None:
            log.close()
            return None, f"the engine exited with code {proc.returncode} before it served"
        if _reachable(port):
            return proc, f"serving on http://127.0.0.1:{port}"
        time.sleep(2.0)
    _stop_server(proc)
    log.close()
    return None, f"the engine did not come up within {int(max(120, min(timeout_s, 1800)))} s"


def _stop_server(proc: subprocess.Popen | None) -> None:
    """Terminate the engine and its children. Called from a `finally`, always."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=25)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)
    except OSError:
        pass
    if os.name == "nt":
        try:                                     # a python parent can leave torch children holding VRAM
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=25)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _post_job(port: int, payload: dict[str, Any], timeout_s: int) -> dict[str, Any]:
    """Start one render through the app's own API. Returns the event stream's tail, for the log."""
    body = json.dumps(payload).encode("utf-8")
    started: dict[str, Any] = {}
    for path in (f"/gradio_api/call/generate_song", "/call/generate_song"):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                started = json.loads(r.read().decode("utf-8", "replace") or "{}")
        except Exception as exc:  # noqa: BLE001 - the other spelling may be the one this build serves
            started = {"error": str(exc)}
            continue
        if started.get("event_id"):
            started["endpoint"] = path
            break
    if not started.get("event_id"):
        return {"ok": False, "error": "engine_api_unreachable", "detail": started}
    stream = f"http://127.0.0.1:{port}{started['endpoint']}/{started['event_id']}"
    tail: list[str] = []
    try:
        with urllib.request.urlopen(stream, timeout=max(120, timeout_s)) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").rstrip()
                if line.startswith("data:"):
                    tail.append(line[5:].strip()[:200])
                    del tail[:-12]
    except Exception as exc:  # noqa: BLE001 - a stream that ends early is reported, not raised
        tail.append(f"stream: {exc}")
    return {"ok": True, "endpoint": started["endpoint"], "tail": tail}


def _engine_output_roots(app: Any) -> list[Path]:
    """Where the engine ITSELF writes finished songs.

    Measured, not assumed: the app runs with `inference/` as its working directory and logs
    `Saved: ./output\\vocoder\\stems\\vtrack.mp3`, so a finished mix lands in
    `<app>/inference/output/vocoder/mix/`. That is where its own UI has always put songs - the
    folder already held the operator's earlier renders. A limb that only searched its own run folder
    reported `song_failed` over a song that had just been written and named in the log.
    """
    roots: list[Path] = []
    base = Path(str(app or ""))
    for cand in (base / "inference" / "output", base / "output"):
        try:
            if cand.is_dir():
                roots.append(cand)
        except OSError:
            continue
    return roots


def _song_in(run_dir: Path, existing: set[Path]) -> Path | None:
    """The song this run produced: the newest non-empty audio file that was not there before."""
    hits: list[Path] = []
    for pat in ("*.mp3", "*.wav", "*.flac"):
        try:
            hits.extend(p for p in run_dir.rglob(pat) if p.is_file() and p.stat().st_size > 0)
        except OSError:
            continue
    fresh = [p for p in hits if p not in existing]
    if not fresh:
        return None
    # A mix is the deliverable when the engine produced one; the stems sit beside it.
    mixed = [p for p in fresh if "_mixed" in p.name.lower() or "mix" in p.parent.name.lower()]
    return max(mixed or fresh, key=lambda p: p.stat().st_mtime)


def music_generate(style: str = "", lyrics: str = "", seconds: int = 0, model: str = "",
                   seed: int = 0, profile: int = 0, timeout: int = 0, keep_stems: bool = True,
                   engine: str = "", job_id: str = "", segments: int = 0,
                   max_new_tokens: int = 0, on_log: Any = None,
                   title: str = "") -> dict[str, Any]:
    """Render one song locally and return the path it really wrote.

    `style` is the music description (genre, mood, instrumentation, vocal character) and `lyrics` is
    the song's words in sections. A request with no lyrics is refused by NAME (`lyrics_required`)
    rather than rendered as a guess: this engine sings the words it is given, and inventing them here
    would put text in the operator's mouth that nobody wrote. The console's own brain writes lyrics
    when the operator asks for a song about a topic - that is a text job, and this limb is the singer.

    `engine` (or its older name `model`) picks which declared engine renders; empty means the
    operator's choice, then config, then the installed one. An engine with no adapter is refused by
    name - switching is real, and so is refusing, because rendering with the wrong engine and saying
    nothing would be worse than either. `job_id` lets a caller in the studio cancel this render.
    """
    st = str(style or "").strip()
    ly = str(lyrics or "").strip()
    if not st and not ly:
        return {"ok": False, "error": "empty_request",
                "hint": 'pass "style" (the music) and "lyrics" (the words)'}
    eng = (str(engine or "").strip() or str(model or "").strip() or chosen_engine())
    known = {e["id"]: e for e in engines_state()}
    if eng not in known:
        return {"ok": False, "error": "unknown_engine", "engine": eng,
                "declared": sorted(known),
                "hint": "declared engines: " + ", ".join(sorted(known))}
    if not ENGINE_ADAPTERS.get(eng):
        return {"ok": False, "error": "engine_not_wired", "engine": eng,
                "state": known[eng].get("state"), "note": known[eng].get("note"),
                "hint": f"\"{eng}\" is declared and this console has no adapter for it yet: the switch "
                        "is real, the render is not. Use an engine whose state is installed."}
    if not known[eng].get("app_ready"):
        # The engine's own app, entry or python is missing. Its weights are NOT checked here: the
        # weight check below names the exact file, which is the more useful answer.
        return {"ok": False, "error": "engine_not_installed", "engine": eng,
                "missing": known[eng].get("missing"),
                "hint": f'"{eng}" is not installed on this machine: set config/console.json '
                        f'"{eng}_root" to the folder holding its entry script, or install it under the '
                        f"music root - media_status names every declared engine this machine has"}
    # The app and the python of the CHOSEN engine, from its own row. This used to read YuE's own
    # reading (`state`) whatever engine was asked for: invisible while YuE was the only engine, and a
    # real bug with two - ACE-Step's CLI was handed YuE's app folder and failed to find its own script.
    row = known[eng]
    app = Path(str(row.get("app"))) if row.get("app") else None
    py = Path(str(row.get("python"))) if row.get("python") else None
    if not py:
        return {"ok": False, "error": "no_music_engine_python", "engine": eng, "app": str(app),
                "hint": "the app has no python of its own - the engine's own venv travels with it"}
    if not ly:
        return {"ok": False, "error": "lyrics_required", "engine": eng,
                "hint": "this engine sings the words it is given: write the lyrics first (with sections "
                        "like [verse] and [chorus]) and call again with them",
                "style": st}
    missing_weights = [str(w.get("id")) for w in (row.get("weights") or []) if not w.get("present")]
    if missing_weights:
        return {"ok": False, "error": "no_music_weights", "engine": eng, "missing": missing_weights,
                "hint": "the engine is installed but these weights are not on disk: "
                        + ", ".join(missing_weights)}
    # YuE's OWN reading, kept for the YuE path below (its server, its profile, its card route, its
    # output roots). The app/python/weights above come from the chosen engine's own row.
    state = engine_state()

    # How long to sing is decided by the WORDS, because the engine's own unit is a section of lyrics:
    # `run_n_segments` is its "Number of Sequences (paragraphs in Lyrics)" slider (1..10), and the app
    # does `min(run_n_segments, len(sections))` itself. Plain lyrics carry NO `[tag]` sections, so a
    # paragraph of words renders NOTHING - `music_lyrics` reads the operator's own [verse]/[chorus]
    # structure, or gives plain words the structure of their stanzas, and states the length that
    # follows. An explicit `segments`/`seconds` still wins: asking for a fragment on purpose is
    # legitimate. MEASURED 2026-09-25: 1500 tokens (15 s) took 3850 s at profile 3 on an 8 GB card,
    # which is why the card states the cost of the song before anyone presses Generate.
    words = _lyrics_text(ly)
    want = int(segments or 0)
    asked = int(max_new_tokens or 0)
    # The engine's OWN control bounds, read from its UI and not guessed: YuE's gradio_server.py declares
    # max_new_tokens as Slider(300, 6000, step=300) and its sequences slider as Slider(1, 10). A value
    # outside those is refused inside gradio's own preprocess, so the engine has already booted and the
    # operator is left with a bare `song_failed` whose cause is only in the engine log. MEASURED
    # 2026-09-25: a 200-token job burned 91 s of engine boot on `gradio.exceptions.Error: 'Value 200 is
    # less than minimum value 300.'` Refusing here costs nothing and names the range to type instead.
    # THE REQUEST IS WHAT IS CHECKED, NOT THE PLAN. `ml.plan()` normalises a token budget and a
    # section count into legal ones, so checking its OUTPUT could never fail: a request for 200 tokens
    # came back as 300, the check passed, and a real engine was booted to sing it. MEASURED 2026-09-25
    # - that bug rendered a whole YuE song out of this kit's own test suite, on the operator's card.
    lim = {"tokens": (300, 6000), "sections": (1, 10)} if eng == "yue" else {}
    for key, val, what in (("tokens", asked, "tokens per section (1000 is about 10 s of audio)"),
                           ("sections", want, "sections")):
        lo, hi = lim.get(key, (0, 0))
        if lo and val and not (lo <= val <= hi):
            return {"ok": False, "error": key + "_out_of_range", "engine": eng, key: val,
                    "range": [lo, hi],
                    "hint": f'"{eng}" takes {what} from {lo} to {hi}; {val} is outside that, and the '
                            f"engine would only say so after a full boot"}
    if want <= 0 and int(seconds or 0) > 0:
        # Derived from the APP'S OWN unit ("1000 tokens = 10s"), not from a helper that clamps: a
        # helper that returns at most MAX_SECTIONS can never be asked "is this too long for you?".
        per_section = max(1, int((asked or ml.DEFAULT_TOKENS) * ml.SECONDS_PER_1000_TOKENS // 1000))
        need = max(1, -(-int(seconds) // per_section))          # ceiling division, integer-only
        hi_sections = lim.get("sections", (0, 0))[1]
        if hi_sections and need > hi_sections:
            # A song longer than the engine can sing is refused by name too, instead of being quietly
            # shortened to the engine's ceiling and rendered as a different song than the one asked for.
            return {"ok": False, "error": "sections_out_of_range", "engine": eng, "sections": need,
                    "range": [1, hi_sections],
                    "hint": f'"{eng}" sings at most {hi_sections} sections, and {seconds} s is {need} '
                            f"of them - ask for "
                            f"{hi_sections * (asked or ml.DEFAULT_TOKENS) // 100} s or fewer, or use an "
                            f"engine that renders a whole song in one pass"}
        want = max(1, min(ml.MAX_SECTIONS, need))
    sung = ml.plan(words, tokens=asked or ml.DEFAULT_TOKENS, want_sections=want)
    segs, tokens = int(sung["run_n_segments"]), int(sung["tokens"])
    words_sent = ml.structured(words) if segs else ""
    if segs <= 0:
        return {"ok": False, "error": "lyrics_required", "sections": 0,
                "hint": "the words carry no sections to sing: write the lyrics (a blank line between "
                        "stanzas is enough, or tag them [verse] / [chorus])",
                "style": st, "plan": sung}
    if ENGINE_ADAPTERS.get(eng) == "acestep":
        # ACE-Step takes a song LENGTH, not a token budget, and renders in minutes what the YuE path
        # buys in hours - so its adapter is its own one-shot CLI and there is no server to start or stop.
        # The length is the plan's own (one ~30 s section per part of the words) unless asked otherwise.
        return _acestep_render(app=app, py=py, style=st, words=words_sent,
                               seconds=int(seconds or 0) or int(sung["seconds"]) or 30,
                               seed=int(seed or 0), timeout=int(timeout or 0), job_id=job_id,
                               on_log=on_log, title=title, engine=eng)

    per_section = max(60, int(timeout or state["timeout_s"]))
    # `music_timeout_s` is how long ONE section may take. A full song is several sections rendered one
    # after another in a single job, so the job gets that much per section - a job killed at the limit
    # is a song lost - and the ceiling is a day, past which something is wrong rather than slow.
    to = max(60, min(24 * 3600, per_section * max(1, segs)))
    forget_reading()                             # a render is the one caller that wants it fresh
    chosen = route_for_a_song()
    prof = int(profile or 0) or (int(_cfg().get("music_profile") or 0) or int(chosen["profile"]))
    run = SONGS_DIR / (_stamp(_slug(st or ly)) + "")
    try:
        run.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "error": "no_song_folder", "why": str(exc)[:200], "path": str(run)}
    genre_path = run / "style.txt"
    lyrics_path = run / "lyrics.txt"
    genre_path.write_text(_style_text(st), encoding="utf-8")
    lyrics_path.write_text(words_sent or _lyrics_text(ly), encoding="utf-8")
    out_roots = _engine_output_roots(app)
    existing = {p for p in run.rglob("*") if p.is_file()}
    engine_output = ""
    for root in out_roots:
        try:
            existing |= {p for p in root.rglob("*") if p.is_file()}
        except OSError:
            continue
    port = _free_port()
    log_path = run / "engine.log"
    if callable(on_log):
        # The panel reads the JOB RECORD while the engine works, never the process, so the record has to
        # carry the log the engine's own words go to - that log is the only place a song's position can
        # be read from. A reporting callback must never be able to break a render, hence the guard.
        try:
            on_log({"log": str(log_path), "run_dir": str(run), "sections": segs, "tokens": tokens,
                    "planned_audio_seconds": sung["seconds"], "timeout_s": to,
                    "section_tags": [s["tag"] for s in sung["sections"]][:segs]})
        except Exception:  # noqa: BLE001
            pass
    t0 = time.time()
    proc, why = _start_server(py, app, prof, port, to, log_path)
    if proc is None:
        tail = _log_tail(log_path)
        return {"ok": False, "error": "music_engine_failed", "why": why, "profile": prof,
                "log": str(log_path), "log_tail": tail, "seconds": round(time.time() - t0, 1),
                **(_oom_hint(tail) or {})}
    job: dict[str, Any]
    _register(job_id, proc)
    try:
        if is_stopped(job_id):
            # The hand was already on the switch while the engine was still booting, so there was no
            # pid to kill yet. Stopping here means the render never sings: a record that says
            # "cancelled" over a card that stays busy for an hour would be a lie about this machine.
            forget_reading()
            return {"ok": False, "error": "cancelled", "profile": prof,
                    "seconds": round(time.time() - t0, 1), "log": str(log_path),
                    "hint": "stopped on request while the engine was still starting - it never sang"}
        job = _post_job(port, _job_payload(_style_text(st), words_sent or _lyrics_text(ly), segs,
                                          int(seed or 0), tokens), to)
    finally:
        _stop_server(proc)
        _unregister(job_id)
    render_s = round(time.time() - t0, 1)
    song = _song_in(run, existing)
    if song is None:
        # Nothing new in the run folder - so look where the engine actually wrote. The kit keeps its
        # own copy in the run folder (the playlist, the players and the claim machinery all read that
        # one tree) and the engine's own file is left exactly where it is.
        for root in out_roots:
            found = _song_in(root, existing)
            if found is None:
                continue
            engine_output = str(found)
            try:
                import shutil as _shutil

                dest = run / found.name
                _shutil.copy2(found, dest)
                song = dest
            except OSError:
                song = found  # better the engine's own path than losing a song that exists
            break
    if song is None:
        tail = _log_tail(log_path)
        out: dict[str, Any] = {"ok": False, "error": "song_failed", "profile": prof,
                               "seconds": render_s, "log": str(log_path), "log_tail": tail,
                               "engine_tail": (job or {}).get("tail"), "job": (job or {}).get("detail")}
        out.update(_oom_hint(tail) or {})
        return out
    dest = run / (song.stem + ".mp3")
    if song.suffix.lower() != ".mp3":
        conv = _to_mp3(song, dest)
        if conv:
            dest = conv
    if dest != song:
        try:
            shutil.copy2(song, dest) if not dest.is_file() else None
        except OSError:
            dest = song
    stems = []
    if keep_stems:
        stems = [str(p) for p in run.rglob("*")
                 if p.is_file() and ("_itrack" in p.name or "_vtrack" in p.name)]
    secs = audio_seconds(dest)
    record = {
        "engine": "yue", "style": _style_text(st), "lyrics": ly[:4000], "seed": int(seed or 0),
        "profile": prof, "route": chosen["route"], "segments": segs, "max_new_tokens": tokens,
        "render_seconds": render_s, "audio_seconds": secs, "stems": stems,
        # What the song was made of: the sections that were sung and the length that should buy, beside
        # the length that came back. A plain-lyrics grouping is recorded as such, not as the operator's
        # own structure, and the timeout the job was given is recorded so a kill can be explained.
        "sections": segs, "section_tags": [s["tag"] for s in sung["sections"]][:segs],
        "lyrics_tags": bool(sung["tagged"]), "planned_audio_seconds": sung["seconds"],
        "lyrics_note": sung["note"], "timeout_s": to, "lyrics_structured": words_sent[:4000],
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        (dest.with_suffix(".json")).write_text(json.dumps(record, indent=2), encoding="utf-8")
    except OSError:
        pass
    return {
        "ok": True, "path": str(dest), "bytes": dest.stat().st_size, "seconds": render_s,
        "audio_seconds": secs, "engine": "yue", "model": "YuE-s1-7B-anneal-en-cot + YuE-s2-1B-general",
        "seed": int(seed or 0), "profile": prof, "route": chosen["route"], "segments": segs,
        "stems": stems, "run_dir": str(run), "log": str(log_path),
        "engine_output": engine_output,
        "lyrics_path": str(lyrics_path), "style_path": str(genre_path),
        "route_why": chosen["why"],
        # The plan, stated: how many sections were sung, how long that should be, and the words' own
        # note (a plain-lyrics grouping is reported rather than presented as the operator's choice).
        "sections": segs, "section_tags": [s["tag"] for s in sung["sections"]][:segs],
        "lyrics_tags": bool(sung["tagged"]), "planned_audio_seconds": sung["seconds"],
        "lyrics_note": sung["note"], "timeout_s": to,
        "note": f"Rendered on {chosen['route']} ({chosen['why']}). "
                + (f"Length {secs} s. " if secs else "")
                + "The song is the file at `path`; its stems and the exact lyrics it sang sit beside it.",
    }


#: ACE-Step's own stage words, in the order its CLI reports them, as a sentence for the panel.
_ACE_STAGES: dict[str, str] = {
    "dit_load": "loading the music model",
    "dit_ready": "music model ready",
    "lm_load": "loading the lyric model",
    "lm_ready": "lyric model ready",
    "generating": "writing the song",
}


def _acestep_progress(text: str) -> dict[str, Any]:
    """Where an ACE-Step render has got to, read from its own `@@ACESTEP_PROGRESS@@` lines."""
    last: dict[str, Any] = {}
    for m in re.finditer(r"@@ACESTEP_PROGRESS@@ (\{.*\})", text):
        try:
            last = json.loads(m.group(1))
        except (ValueError, TypeError):
            continue
    if not last:
        return {}
    stage = str(last.get("stage") or "")
    out: dict[str, Any] = {"stage": 0, "at": 0, "of": 0, "eta": "", "ace_stage": stage,
                           "text": _ACE_STAGES.get(stage, stage or "working")}
    if stage == "progress":
        # MEASURED 2026-09-25: the CLI also reports fine-grained progress lines while it works
        # (`{"stage":"progress","pct":51.0,"desc":"Preparing inputs...","elapsed_s":9.1}`). A multi-minute
        # render that only ever says "progress" tells the operator nothing, so the description and the
        # percentage are carried through verbatim - its own words, not a re-phrasing here.
        pct = last.get("pct")
        desc = str(last.get("desc") or "").strip()
        if pct is not None:
            try:
                out["pct"] = float(pct)
            except (TypeError, ValueError):
                out["pct"] = None
        if desc or out.get("pct") is not None:
            out["text"] = desc + (f" ({int(float(out['pct']))}%)" if out.get("pct") is not None else "")
        return out
    params = last.get("params") if isinstance(last.get("params"), dict) else {}
    if stage == "generating" and params:
        out["of"] = int(params.get("steps") or 0)
        out["text"] = (f"writing the song: {int(float(params.get('duration') or 0))} s of audio from "
                      f"seed {params.get('seed')} in {out['of'] or 8} steps")
    return out


def _acestep_render(app: Path, py: Path, style: str, words: str, seconds: int, seed: int,
                    timeout: int, job_id: str, on_log: Any = None, title: str = "",
                    engine: str = "ace_step") -> dict[str, Any]:
    """Render one song with the ACE-Step CLI - a one-shot process, the way the picture limb works.

    MEASURED on this 8 GB card 2026-09-25: 30 s of 48 kHz stereo song in 50.1 s of render plus about
    45 s of model load, against HOURS per section on the YuE path. That is the whole reason both
    engines are declared: this one buys a finished song in minutes, the other buys a better one in
    hours, and the operator chooses knowingly instead of waiting to find out.

    Nothing is left listening - the CLI writes its audio and exits. Its stdout carries only
    `@@ACESTEP_PROGRESS@@ {json}` stage lines and one final `@@ACESTEP_RESULT@@ {json}` (human noise goes
    to stderr), and exit 0 means the audio is on disk. Both are read here, and the FILE is what decides:
    a result that claims success with no audio behind it is reported as a failure by name.
    """
    run = SONGS_DIR / f"{_stamp('song')}_{_slug(style)}"
    run.mkdir(parents=True, exist_ok=True)
    audio_dir = run / "audio"
    log_path = run / "engine.log"        # stdout: the machine-readable lines `stage_progress` reads
    err_path = run / "engine.err.log"    # stderr: the engine's own human log, kept for a failure's tail
    spec_path = run / "spec.json"
    result_path = run / "result.json"
    prog_path = run / "progress.jsonl"
    spec = {
        "caption": _style_text(style),
        "lyrics": words,
        "duration": int(seconds),
        "seed": int(seed or 0),
        "audio_format": "wav",
        "thinking": True,
        "instrumental": False,
    }
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    # A song here is a LENGTH, so there is no per-section budget to multiply: the whole call is one
    # render. The engine's own plan says what it can hold, so the ceiling is generous and honest.
    budget = int(timeout or 0) or max(900, int(seconds) * 5 + 300)
    if callable(on_log):
        try:
            on_log({"log": str(log_path), "run_dir": str(run), "sections": len(ml.sections(words)),
                    "tokens": 0, "planned_audio_seconds": int(seconds), "timeout_s": budget,
                    "section_tags": [s["tag"] for s in ml.sections(words)]})
        except Exception:  # noqa: BLE001 - a reporting callback must never break a render
            pass
    cmd = [str(py), str(app / "render_cli.py"), "--spec", str(spec_path), "--out", str(audio_dir),
           "--name", run.name, "--seed", str(int(seed or 0)), "--result-json", str(result_path),
           "--progress-jsonl", str(prog_path)]
    t0 = time.time()
    try:
        with log_path.open("w", encoding="utf-8") as out, err_path.open("w", encoding="utf-8") as err:
            # No env was passed here until 2026-09-26, which is why ACE renders died on an emoji.
            proc = subprocess.Popen(cmd, cwd=str(app), stdout=out, stderr=err, env=_utf8_env())
            _register(job_id, proc)
            try:
                deadline = t0 + budget
                while proc.poll() is None:
                    if is_stopped(job_id):
                        _stop_server(proc)
                        return {"ok": False, "error": "cancelled", "engine": engine,
                                "seconds": round(time.time() - t0, 1), "log": str(log_path),
                                "run_dir": str(run), "log_tail": _log_tail(err_path),
                                "hint": "stopped on request while the engine was rendering"}
                    if time.time() > deadline:
                        _stop_server(proc)
                        return {"ok": False, "error": "music_timeout", "engine": engine,
                                "timeout_s": budget, "seconds": round(time.time() - t0, 1),
                                "log": str(log_path), "run_dir": str(run),
                                "log_tail": _log_tail(err_path),
                                "hint": f"the render did not finish inside {budget} s"}
                    time.sleep(2)
            finally:
                _unregister(job_id)
    except OSError as exc:
        return {"ok": False, "error": "music_engine_failed", "engine": engine, "run_dir": str(run),
                "why": str(exc)[:200], "hint": "the engine's own CLI could not be started"}
    render_s = round(time.time() - t0, 1)
    # The engine's own word first (its result file, then the last result line), and the FILE decides.
    res: dict[str, Any] = {}
    try:
        res = json.loads(result_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        res = {}
    if not res:
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for m in re.finditer(r"@@ACESTEP_RESULT@@ (\{.*\})", text):
            try:
                res = json.loads(m.group(1))
            except (ValueError, TypeError):
                continue
    if not isinstance(res, dict):
        res = {}
    src = ""
    for audio in (res.get("audios") or []):
        if not isinstance(audio, dict):
            continue
        cand = Path(str(audio.get("path") or ""))
        if not cand.is_absolute():
            cand = app / cand
        try:
            if cand.is_file() and cand.stat().st_size > 0:
                src = str(cand)
                break
        except OSError:
            continue
    if not src:
        return {"ok": False, "error": "song_failed", "engine": engine, "seconds": render_s,
                "log": str(log_path), "run_dir": str(run), "log_tail": _log_tail(err_path),
                "engine_said": str(res.get("status_message") or "")[:300],
                "hint": "the engine finished without naming an audio file it wrote"}
    src_p = Path(src)
    # An operator-given title names the file. Blank keeps the old name exactly, so a console that
    # never sends a title behaves as it always did - a rename is never a surprise.
    label = _slug(title, 40) if str(title or "").strip() else ""
    dest = run / (f"{run.name}-{label}.mp3" if label else f"{run.name}.mp3")
    if _to_mp3(src_p, dest) is None:
        # No encoder on this machine is not a reason to lose the song: the engine's own wav is kept and
        # named, exactly as the picture limb keeps what it wrote.
        try:
            kept = run / src_p.name
            shutil.copy2(src_p, kept)
            dest = kept
        except OSError:
            dest = src_p
    secs = audio_seconds(dest)
    if not secs:
        for row in (res.get("measured") or []):
            if isinstance(row, dict) and row.get("seconds"):
                secs = float(row["seconds"])
                break
    secs = secs or None
    tags = [s["tag"] for s in ml.sections(words)]
    record = {
        "engine": engine, "title": str(title or "").strip(),
        "style": _style_text(style), "lyrics": words[:4000], "seed": int(seed or 0),
        "model": str((res.get("plan") or {}).get("dit") or "acestep-v15-turbo"),
        "render_seconds": render_s, "audio_seconds": secs, "sections": len(tags),
        "section_tags": tags, "requested_seconds": int(seconds),
        "planned_audio_seconds": int(seconds), "timeout_s": budget,
        "engine_output": str(src_p), "run_dir": str(run), "log": str(log_path),
        "spec_path": str(spec_path), "result_path": str(result_path),
        "gpu": str((res.get("plan") or {}).get("gpu_name") or ""),
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        (dest.with_suffix(".json")).write_text(json.dumps(record, indent=2), encoding="utf-8")
    except OSError:
        pass
    return {
        "ok": True, "path": str(dest), "bytes": dest.stat().st_size, "seconds": render_s,
        "audio_seconds": secs, "engine": engine, "title": str(title or "").strip(),
        "model": str((res.get("plan") or {}).get("dit") or "acestep-v15-turbo"),
        "seed": int(seed or 0), "sections": len(tags), "section_tags": tags, "stems": [],
        "run_dir": str(run), "log": str(log_path), "engine_output": str(src_p),
        "lyrics_path": str(spec_path), "requested_seconds": int(seconds), "timeout_s": budget,
        "note": f"Rendered with ACE-Step on {record['gpu'] or 'this machine'}. "
                + (f"Length {secs} s. " if secs else "")
                + "The song is the file at `path`; the spec it was made from (style and lyrics) sits "
                  "beside it, and the engine's own result file is in the same folder.",
    }


def _utf8_env() -> dict[str, str]:
    """The environment a song engine's CLI must be given, on Windows above all.

    WHY THIS EXISTS - MEASURED 2026-09-26, and it cost the operator a finishable render.
    ACE-Step's engine logs carry emoji: the 5 Hz LM reports "\u2705 5Hz LM initialized
    successfully". That string is printed to a stdout this console REDIRECTS TO A FILE so it can parse
    the engine's own @@ACESTEP_PROGRESS@@ lines - and a redirected Python stdout picks its encoding from
    the LOCALE, not from the terminal. On Windows that is cp1252, printing one emoji raises
    UnicodeEncodeError, and the engine's CLI exits 1 about 40 s into model load, having rendered
    nothing and broken nothing. Three runs on this machine died that way. The YuE path had always set
    PYTHONIOENCODING because its server needed it; the ACE path passed no env at all, which is the
    whole asymmetry.

    This is the belt to the engine CLI's braces: render_cli.py also forces UTF-8 on its own streams
    (see force_utf8_streams), and both ends are guarded because either one alone can be bypassed by a
    launcher that was started differently.
    """
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _to_mp3(src: Path, dest: Path) -> Path | None:
    exe = ffmpeg_bin("ffmpeg")
    if not exe:
        return None
    try:
        p = subprocess.run([str(exe), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
                            "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                            str(dest)], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return dest if p.returncode == 0 and dest.is_file() and dest.stat().st_size > 0 else None


#: The engine announces its own progress one section at a time, in its own words:
#:     "Stage 1: Generating Sequence 3 out of 6"    (words -> audio codes: this is the slow part)
#:     "Stage 2: ..., segment 2 out of 4"           (codes -> audible audio)
#: A full song is HOURS of stage 1, so a status line that does not read these says nothing all day
#: while the card is held and the operator cannot tell a working render from a hung one.
_STAGE1_RE = re.compile(r"Stage 1: Generating Sequence (\d+) out of (\d+)")
_STAGE2_RE = re.compile(r"Stage 2:[^\n]*?segment (\d+) out of (\d+)", re.IGNORECASE)
_TQDM_RE = re.compile(r"(\d+)/(\d+) \[(\d+:\d\d)(?:<(\d+:\d\d))?")


def stage_progress(log_path: str | Path | None) -> dict[str, Any]:
    """Where a render has got to, read from the engine's OWN log. `{}` before it has started.

    Only the tail is read: the engine's progress bar rewrites the same region for hours and the file
    runs to megabytes, and this is called on every poll of the studio's panel.
    """
    path = Path(str(log_path or ""))
    try:
        if not path.is_file():
            return {}
        size = path.stat().st_size
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            if size > 65536:
                fh.seek(size - 65536)
            text = fh.read()
    except OSError:
        return {}
    if "@@ACESTEP_PROGRESS@@" in text:
        # ACE-Step's CLI says what it is doing in its own machine-readable words, and its "generating"
        # line is the one that matters: the song itself is written inside that stage.
        ace = _acestep_progress(text)
        if ace:
            return ace
    hits: list[tuple[int, dict[str, Any]]] = []
    for m in _STAGE1_RE.finditer(text):
        hits.append((m.end(), {"stage": 1, "at": int(m.group(1)), "of": int(m.group(2)),
                               "text": f"writing the song: section {m.group(1)} of {m.group(2)}",
                               "eta": ""}))
    for m in _STAGE2_RE.finditer(text):
        hits.append((m.end(), {"stage": 2, "at": int(m.group(1)), "of": int(m.group(2)),
                               "text": f"turning it into audio: part {m.group(1)} of {m.group(2)}",
                               "eta": ""}))
    if hits:
        # The LAST line the engine wrote is where it is, whatever stage it belongs to: it alternates
        # (section 2's codes, section 2's audio, section 3's codes ...), so a reader that prefers any
        # stage-2 line over a later stage-1 line freezes on section 1 of a six-section song for hours.
        return max(hits, key=lambda h: h[0])[1]
    last = None
    for m in _TQDM_RE.finditer(text):
        last = m
    if last:
        return {"stage": 0, "at": int(last.group(1)), "of": int(last.group(2)),
                "text": f"working: {last.group(1)}/{last.group(2)}", "eta": last.group(4) or ""}
    return {}


def _log_tail(path: Path, lines: int = 8) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [ln.strip()[:220] for ln in text.strip().splitlines()[-lines:] if ln.strip()]


def _oom_hint(tail: list[str]) -> dict[str, Any] | None:
    """The engine's own out-of-memory words, turned into the operator's next move."""
    blob = " ".join(tail or []).lower()
    if "out of memory" not in blob and "cuda error" not in blob and "no space left" not in blob:
        return None
    return {"hint": "the engine ran out of memory. The chat model holds most of this card on a normal "
                    "boot: stop the console's engine to free it, lower music_profile to 5 (deepest "
                    "offload), or close the console and render the song then. media_status names the "
                    "card and who is holding it."}


#: The route probe is an nvidia-smi call, and one console poll asks for the studio's card more than
#: once (the route handler, the strip card and the module's own health each want it). Only that probe
#: is held, for a moment: a second-old reading of a free or held card is still true. Everything cheap
#: around it - the engine's install resolution, its weights, its readiness - is read fresh every call,
#: because a caller that has just changed the engine must not be answered from a held copy.
_READING: dict[str, Any] = {}
READING_TTL_S = 1.5


def forget_reading() -> None:
    """Drop the held card reading. Called when what it describes has just changed."""
    _READING.clear()


def route_once() -> dict[str, Any]:
    """The route a song would take right now, probed at most once per hold window."""
    now = time.time()
    held_at = float(_READING.get("at") or 0)
    if _READING.get("value") and (now - held_at) < READING_TTL_S:
        return dict(_READING["value"])
    route = route_for_a_song()
    _READING["at"], _READING["value"] = now, route
    return dict(route)


def music_status(fresh: bool = False) -> dict[str, Any]:
    """The limb's own read-out: engine_state plus the route a song would take right now."""
    st = engine_state()
    route = route_for_a_song() if fresh else route_once()
    st["route_now"] = route
    st["ready_to_sing"] = bool(st["ready"])
    return st
