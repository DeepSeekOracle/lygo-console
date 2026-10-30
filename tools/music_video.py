#!/usr/bin/env python3
"""music_video.py - many short clips, cut to a beat, into one long video.

The video engine on this box (Wan 2.2 TI2V-5B through ComfyUI) makes a COHERENT 4-5 second shot and
nothing longer: `limits.max_length` is 121 frames (5.04 s at 24 fps) and pushing past it does not buy
time, it buys drift. So a music video is not one long render - it is N short ones assembled on the
beat grid, which is how they are cut anyway.

Measured on the steward's rig (RTX 4060 Ti 8 GB, weights on I:): a 97-frame shot is ~65 s of sampling
plus overhead once the engine is RESIDENT, and ~200 s of that first shot is the engine loading. So the
whole job is: start the engine ONCE, keep it warm for every shot, and never let a restart land in the
middle of a batch.

Subcommands
    plan      write a shot list (JSON) from a brief, a scene list, or a track length
    render    render every shot in the list, resumable, engine kept warm
    assemble  normalize + cut (or crossfade) the shots and write the finished MP4
    run       plan (if needed) then render then assemble
    status    what the ledger says

Examples
    # 3:30 of video, cuts every 8 beats at 92 BPM, 5 s shots
    python tools/music_video.py plan "midnight city, rain, neon" --seconds 210 --bpm 92 \
        --beats-per-cut 8 --out workspace/video/mv_midnight/shots.json
    python tools/music_video.py render workspace/video/mv_midnight/shots.json
    python tools/music_video.py assemble workspace/video/mv_midnight/shots.json \
        --out workspace/video/mv_midnight/midnight.mp4

Nothing here invents a render: every path reported is a file that exists on disk, and every shot the
engine refused is listed by id and reason instead of being quietly dropped.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "src"))

LEGAL_LENGTHS = (25, 49, 73, 97, 121)      # 4n+1, the Wan latent's rule
FPS = 24


# ------------------------------------------------------------------ ffmpeg
def ffmpeg() -> str:
    for name in ("ffmpeg", "ffmpeg.exe"):
        got = shutil.which(name)
        if got:
            return got
    for cand in (r"C:\ffmpeg\bin\ffmpeg.exe",):
        if Path(cand).is_file():
            return cand
    raise SystemExit("ffmpeg not found on PATH - install it (winget install Gyan.FFmpeg) and retry")


def ffprobe_duration(path: Path) -> float:
    probe = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(path)],
                       capture_output=True, text=True)
    try:
        return float((r.stdout or "0").strip().splitlines()[0])
    except Exception:
        return 0.0


def run(cmd: list[str], what: str) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "")[-1500:]
        raise RuntimeError(f"{what} failed (exit {r.returncode}):\n{tail}")


# ------------------------------------------------------------------ shot list
def beats_per_shot(bpm: float, beats: int) -> float:
    return (60.0 / float(bpm)) * int(beats)


def nearest_legal(shot_s: float) -> int:
    """The 4n+1 frame count whose duration lands closest to the beat slice.

    A slice longer than the clip means a freeze at the end (tpad), which reads as a stutter at a
    cut; picking the length that already fits the grid means every cut lands on the beat with no
    padding at all.
    """
    target = shot_s * FPS
    legal = [n for n in LEGAL_LENGTHS if n <= 121]
    return min(legal, key=lambda n: abs(n - target))


def make_plan(brief: str, scenes: list[str], seconds: float, bpm: float, beats: int,
              length: int, seed: int) -> dict[str, Any]:
    """A shot list: `seconds` of screen time, snapped to whole beats, one scene per shot.

    The prompt carries SCENE + CAMERA + LIGHT on purpose - the engine invents no shot for you, and a
    bare subject returns a weak clip. Style words are appended to every shot so 40 renders read as
    one film rather than 40 postcards.
    """
    shot_s = beats_per_shot(bpm, beats) if bpm else round(seconds / max(1, len(scenes) or 1), 2)
    shot_s = max(1.0, shot_s)
    count = max(1, int(round(seconds / shot_s)))
    cameras = ["slow push in", "slow pull back", "gentle tracking shot left to right",
               "locked-off wide shot", "handheld follow", "slow orbit", "low angle drift up",
               "high angle drift down"]
    light = ["low key, single warm practical", "cool moonlight", "neon spill and wet reflections",
             "harsh midday sun", "fog with backlit haze", "candle warm, deep shadow"]
    rng = random.Random(seed or 7)
    length = int(length) or nearest_legal(shot_s)
    shots = []
    for i in range(count):
        scene = scenes[i % len(scenes)] if scenes else brief
        cam = cameras[i % len(cameras)]
        # Light stays with the brief, not with the shot index: a film that changes its lighting
        # every four seconds reads as 40 unconnected postcards, which is the failure mode this
        # whole pipeline exists to avoid.
        style = "cinematic film still, shallow depth of field"
        tail = brief if style in brief else f"{style}, {brief}"
        shots.append({
            "id": f"s{i + 1:02d}",
            "prompt": f"{scene}, {cam}, {tail}",
            "length": int(length),
            "slice_s": shot_s,
            "seed": (seed + i) if seed else 0,
        })
    return {
        "signature": "Δ9Φ963-MUSIC-VIDEO-PLAN-v1",
        "brief": brief,
        "bpm": bpm, "beats_per_cut": beats, "shot_s": shot_s,
        "target_s": seconds, "shots": shots,
    }


# ------------------------------------------------------------------ render
def _park_context(model: str):
    """Tell the console's chat model to step off the card for this batch, then bring it back.

    A clip needs ~13.5 GB of weights in RAM and its unet resident on the card. With the console's chat
    model loaded the card has ~400 MiB free, so an un-parked render either refuses to start or silently
    offloads to CPU (measured 2026-10-10: a 4.5 min clip still running at 13+). `borrowed_card` walks
    the same park the `video_generate` limb uses, sized from the recipe that will actually open.
    """
    import contextlib

    try:
        import render_swap
        from video_harness import MODELS as _MODELS
        from video_harness import weights_state as _weights_state

        mid = str(model or "").strip() or next(iter(_MODELS))
        recipe = _MODELS.get(mid) or {}
        sizes = [int(f.get("bytes") or 0) // (1024 * 1024)
                 for f in (_weights_state(mid).get("files") or [])]
        vram = int(recipe.get("vram_mib") or 0)
        return render_swap.borrowed_card(vram, render_swap.need_ram_for(sizes))
    except Exception as exc:  # noqa: BLE001 - a park that cannot be sized must not stop the render
        print(f"park: {type(exc).__name__}: {exc} - rendering without parking")
        return contextlib.nullcontext()


def render_plan(plan_path: Path, model: str = "wan22-5b", limit: int = 0,
                max_retries: int = 1, dry: bool = False) -> dict[str, Any]:
    if dry:
        return _render_plan_inner(plan_path, model=model, limit=limit,
                                  max_retries=max_retries, dry=dry)
    with _park_context(model):
        return _render_plan_inner(plan_path, model=model, limit=limit,
                                  max_retries=max_retries, dry=dry)


def _is_take(path: Path) -> bool:
    """True when this is a real clip, not a truncated header: ffprobe reports a duration over a second."""
    try:
        return ffprobe_duration(path) > 1.0
    except (OSError, ValueError, subprocess.SubprocessError, SystemExit):
        return False


def _write_ledger(path: Path, ledger: dict[str, Any]) -> None:
    """The ledger is the resume record: what was rendered, how long it took, and what the prompt was.
    Written atomically so a killed worker leaves a readable file rather than half of one."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _adopt_orphan(vh, outdir: Path, dest: Path, ledger: dict[str, Any]) -> Path | None:
    """Adopt a finished take that no worker claimed.

    MEASURED 2026-10-17: a worker killed between "the engine finished" and "the copy landed" left shot
    3 sitting in the engine's output folder while the shot's own folder stayed empty - four minutes of
    GPU time thrown away, and the pipeline would have paid for it again. A take is adoptable when it is
    one file, newer than every shot already claimed here, and not already named in the ledger.
    """
    root = vh.app_root()
    if root is None:
        return None
    src_dir = root / "output" / "video"
    if not src_dir.is_dir():
        return None
    claimed = {str((v or {}).get("path") or "") for v in ledger.values()}
    newest = max((p.stat().st_mtime for p in outdir.glob("*.mp4")), default=0.0)
    cands = [p for p in src_dir.glob("*.mp4")
             if p.stat().st_mtime > newest and str(p) not in claimed and p.stat().st_size > 100_000]
    if len(cands) != 1:
        return None
    src = cands[0]
    shutil.copy2(src, dest)
    print(f"    adopted the unclaimed take {src.name} -> {dest.name} "
          f"({dest.stat().st_size // 1024} KB, rendered before the worker died)")
    return src


def _render_plan_inner(plan_path: Path, model: str = "wan22-5b", limit: int = 0,
                       max_retries: int = 1, dry: bool = False) -> dict[str, Any]:
    import video_harness as vh

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    shots = plan["shots"]
    root = plan_path.parent
    outdir = root / "shots"
    outdir.mkdir(parents=True, exist_ok=True)
    ledger_path = root / "ledger.json"
    ledger: dict[str, Any] = {}
    if ledger_path.is_file():
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except Exception:
            ledger = {}

    if limit:
        shots = shots[:limit]

    started = time.time()
    warm = None
    done = 0
    failed: list[dict[str, str]] = []
    for n, shot in enumerate(shots, 1):
        sid = shot["id"]
        dest = outdir / f"{sid}.mp4"
        rec = ledger.get(sid) or {}
        if dest.is_file() and dest.stat().st_size > 10_000 and (rec.get("ok") or _is_take(dest)):
            # A take on disk that the ledger never got to record is still a rendered take: the worker
            # can be killed between the copy and the ledger write. Re-rendering it would pay for the
            # same four minutes of GPU twice.
            if not rec.get("ok"):
                ledger[sid] = {"ok": True, "path": str(dest), "backfilled": True}
                _write_ledger(ledger_path, ledger)
            print(f"[{n}/{len(shots)}] {sid}: already rendered ({dest.name}, {dest.stat().st_size // 1024} KB)")
            done += 1
            continue
        if not (dest.is_file() and dest.stat().st_size > 10_000) and not dry:
            if _adopt_orphan(vh, outdir, dest, ledger):
                ledger[sid] = {"ok": True, "path": str(dest), "adopted": True}
                done += 1
                _write_ledger(ledger_path, ledger)
                continue
        prompt = shot["prompt"]
        length = int(shot.get("length") or 97)
        if dry:
            print(f"[{n}/{len(shots)}] {sid}: WOULD render {length}f :: {prompt[:90]}")
            continue
        last_err = ""
        for attempt in range(1 + max_retries):
            t0 = time.time()
            probe_before = vh.probe().get("up")
            got = vh.render(model, prompt=prompt, length=length, seed=int(shot.get("seed") or 0))
            wall = round(time.time() - t0, 1)
            if got.get("ok"):
                src = Path(got["path"])
                if src.resolve() != dest.resolve():
                    shutil.copy2(src, dest)
                rec = {"ok": True, "path": str(dest), "length": length, "wall_s": wall,
                       "engine_was_warm": bool(probe_before), "seed": shot.get("seed") or 0,
                       "prompt": prompt, "prompt_id": got.get("prompt_id")}
                if warm is None:
                    warm = bool(probe_before)
                done += 1
                print(f"[{n}/{len(shots)}] {sid}: ok {wall}s (engine warm={bool(probe_before)}) -> {dest.name}")
                break
            last_err = f"{got.get('error')}: {got.get('why')}"
            print(f"[{n}/{len(shots)}] {sid}: attempt {attempt + 1} failed - {last_err}")
            rec = {"ok": False, "error": last_err, "length": length, "wall_s": wall}
            time.sleep(5)
        else:
            failed.append({"id": sid, "error": last_err})
        ledger[sid] = rec
        ledger_path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")

    total = round(time.time() - started, 1)
    print(f"\nrendered {done}/{len(shots)} in {total}s ({round(total / max(1, done), 1)}s per shot)")
    if failed:
        print("FAILED: " + ", ".join(f"{f['id']} ({f['error']})" for f in failed))
    return {"ok": not failed, "done": done, "requested": len(shots), "failed": failed,
            "seconds": total, "ledger": str(ledger_path)}


# ------------------------------------------------------------------ assemble
def assemble(plan_path: Path, out_path: Path, transition: str = "cut", fade_s: float = 0.5,
             audio: str = "", upscale: str = "1280x720", beat_audio: bool = False) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    root = plan_path.parent
    outdir = root / "shots"
    seg_dir = root / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)

    shots = [s for s in plan["shots"] if (outdir / f"{s['id']}.mp4").is_file()]
    if not shots:
        raise SystemExit(f"no rendered shots in {outdir} - run `render` first")

    track_s = ffprobe_duration(Path(audio)) if audio else 0.0
    slice_default = float(plan.get("shot_s") or 5.0)

    segs: list[Path] = []
    for i, shot in enumerate(shots):
        src = outdir / f"{shot['id']}.mp4"
        # last shot may need to fill the tail of the track
        slice_s = float(shot.get("slice_s") or slice_default)
        if beat_audio and track_s:
            remaining = track_s - sum(float(s.get("slice_s") or slice_default) for s in shots[:i])
            slice_s = max(0.6, min(slice_s, remaining)) if remaining > 0 else slice_s
        seg = seg_dir / f"{shot['id']}_seg.mp4"
        have = ffprobe_duration(src)
        pad = max(0.0, slice_s - have)
        vf = (f"scale=704:480:force_original_aspect_ratio=decrease,"
              f"pad=704:480:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS}")
        if pad > 0.04:
            vf += f",tpad=stop_mode=clone:stop_duration={pad:.2f}"
        cmd = [ffmpeg(), "-y", "-i", str(src), "-vf", vf, "-t", f"{slice_s:.3f}",
               "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
               "-pix_fmt", "yuv420p", str(seg)]
        run(cmd, f"normalize {shot['id']}")
        segs.append(seg)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    post = f",scale={upscale}:flags=lanczos" if upscale else ""

    if transition == "fade" and len(segs) > 1:
        # xfade chain: total length = sum(slices) - fades
        inputs: list[str] = []
        for seg in segs:
            inputs += ["-i", str(seg)]
        filt = []
        cur = "[0:v]"
        offset = 0.0
        for i in range(1, len(segs)):
            offset += ffprobe_duration(segs[i - 1]) - fade_s
            label = f"[v{i}]"
            filt.append(f"{cur}[{i}:v]xfade=transition=fade:duration={fade_s}:offset={offset:.3f}{label}")
            cur = label
        filt.append(f"{cur}{post.lstrip(',') or 'null'}[vout]" if post else f"{cur}null[vout]")
        audio_args = ["-i", audio] if audio else []
        cmd = [ffmpeg(), "-y", *inputs, *audio_args,
               "-filter_complex", ";".join(filt), "-map", "[vout]"]
        if audio:
            cmd += ["-map", f"{len(segs)}:a", "-c:a", "aac", "-b:a", "192k", "-shortest"]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                str(out_path)]
        run(cmd, "assemble (xfade)")
    else:
        listfile = seg_dir / "concat.txt"
        # ffmpeg resolves relative entries against the LIST's directory, so every line is absolute;
        # forward slashes and single quotes are what the concat demuxer expects on Windows too.
        listfile.write_text("".join(f"file '{s.resolve().as_posix()}'\n" for s in segs),
                            encoding="utf-8")
        vf = f"scale={upscale}:flags=lanczos" if upscale else "null"
        cmd = [ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(listfile)]
        if audio:
            cmd += ["-i", audio]
        cmd += ["-vf", vf]
        if audio:
            cmd += ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-shortest"]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                str(out_path)]
        run(cmd, "assemble (concat)")

    dur = ffprobe_duration(out_path)
    print(f"wrote {out_path}  {dur:.1f}s  {out_path.stat().st_size / 1_048_576:.1f} MB")
    return {"ok": True, "out": str(out_path), "seconds": round(dur, 1),
            "shots": len(segs), "transition": transition}


def scenes_from_file(path: Path) -> list[str]:
    """One scene per line; blank lines and `#` comments ignored.

    This is the seam a delegated (free) agent uses: its whole job is to write a text file, and the
    prompt format is something a small model can hold in its head. Nothing here needs an LLM.
    """
    out: list[str] = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def audio_duration(path: str | Path) -> float:
    """Seconds of audio, or 0.0 when the file cannot be probed (never raises: a missing duration
    falls back to an explicit --seconds rather than aborting a night's render)."""
    try:
        return max(0.0, float(ffprobe_duration(Path(path))))
    except (OSError, ValueError, subprocess.SubprocessError, SystemExit):
        return 0.0


def bpm_from_audio(path: str | Path) -> float:
    """A plain onset-autocorrelation tempo estimate - no librosa, no model, no tokens.

    Decode to 8 kHz mono, take a 10 ms energy envelope, keep the positive rises (onsets), then look for
    the lag between 0.3 s and 1.0 s that lines up best with itself. The result is folded into
    70-160 BPM because a half/double reading is the usual error and both cut the same.
    """
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy ships with the kit
        return 0.0
    cmd = [ffmpeg(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000",
           "-f", "s16le", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return 0.0
    if r.returncode != 0 or not r.stdout:
        return 0.0
    x = np.frombuffer(r.stdout, dtype="<i2").astype(np.float32)
    hop, win = 80, 320
    if len(x) < win * 8:
        return 0.0
    frames = 1 + (len(x) - win) // hop
    env = np.array([np.sqrt(np.mean(x[i * hop: i * hop + win] ** 2) + 1e-9) for i in range(frames)],
                   dtype=np.float32)
    onset = np.maximum(0.0, np.diff(env))
    onset -= onset.mean()
    if not np.any(onset > 0):
        return 0.0
    best_lag, best_score = 0, -1e18
    for lag in range(30, 101):                      # 0.30 s .. 1.00 s -> 60..200 BPM
        if lag >= len(onset):
            break
        score = float(np.dot(onset[:-lag], onset[lag:]))
        if score > best_score:
            best_lag, best_score = lag, score
    if not best_lag:
        return 0.0
    bpm = 60.0 / (best_lag * 0.01)
    while bpm < 70.0:
        bpm *= 2.0
    while bpm > 160.0:
        bpm /= 2.0
    return round(bpm * 2.0) / 2.0                   # nearest half-BPM


# ------------------------------------------------------------------ status
def status(plan_path: Path) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    root = plan_path.parent
    ledger = {}
    if (root / "ledger.json").is_file():
        ledger = json.loads((root / "ledger.json").read_text(encoding="utf-8"))
    ok = [s for s in plan["shots"] if (root / "shots" / f"{s['id']}.mp4").is_file()]
    print(f"plan: {len(plan['shots'])} shots, {plan.get('shot_s')}s each, target {plan.get('target_s')}s")
    print(f"rendered: {len(ok)}  ledger entries: {len(ledger)}")
    for rec_id, rec in ledger.items():
        if not rec.get("ok"):
            print(f"  failed {rec_id}: {rec.get('error')}")
    return {"ok": True, "shots": len(plan["shots"]), "rendered": len(ok)}


# ------------------------------------------------------------------ CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan")
    p.add_argument("brief")
    p.add_argument("--scene", action="append", default=[], help="repeatable: one line per scene")
    p.add_argument("--scenes-file", default="", help="a text file, one scene per line (# comments)")
    p.add_argument("--seconds", type=float, default=210.0)
    p.add_argument("--bpm", type=float, default=0.0)
    p.add_argument("--beats-per-cut", type=int, default=8)
    p.add_argument("--length", type=int, default=0,
                   help="frames per shot (4n+1); 0 = pick the legal length nearest the beat slice")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", required=True)

    r = sub.add_parser("render")
    r.add_argument("plan")
    r.add_argument("--model", default="wan22-5b")
    r.add_argument("--limit", type=int, default=0, help="render only the first N shots (a test)")
    r.add_argument("--retries", type=int, default=1)
    r.add_argument("--dry", action="store_true")

    a = sub.add_parser("assemble")
    a.add_argument("plan")
    a.add_argument("--out", required=True)
    a.add_argument("--transition", choices=("cut", "fade"), default="cut")
    a.add_argument("--fade", type=float, default=0.5)
    a.add_argument("--audio", default="")
    a.add_argument("--upscale", default="1280x720")
    a.add_argument("--beat-audio", action="store_true")

    s = sub.add_parser("status")
    s.add_argument("plan")

    d = sub.add_parser("director", help="plan + render + assemble in one call (the free-agent seam)")
    d.add_argument("brief")
    d.add_argument("--scenes-file", default="", help="a text file, one scene per line (# comments)")
    d.add_argument("--scene", action="append", default=[])
    d.add_argument("--song", default="", help="the track: sets the length, muxes the audio, drives --detect-bpm")
    d.add_argument("--bpm", type=float, default=0.0)
    d.add_argument("--detect-bpm", action="store_true", help="estimate the tempo from --song")
    d.add_argument("--seconds", type=float, default=0.0, help="0 = the song's own length")
    d.add_argument("--beats-per-cut", type=int, default=8)
    d.add_argument("--length", type=int, default=0)
    d.add_argument("--out-dir", required=True)
    d.add_argument("--limit", type=int, default=0)
    d.add_argument("--transition", choices=("cut", "fade"), default="cut")
    d.add_argument("--fade", type=float, default=0.5)
    d.add_argument("--upscale", default="1280x720")
    d.add_argument("--plan-only", action="store_true", help="write the plan, render nothing")

    args = ap.parse_args(argv)
    if args.cmd == "plan":
        scenes = list(args.scene) or ([args.brief])
        if args.scenes_file:
            scenes = scenes_from_file(Path(args.scenes_file)) or scenes
        plan = make_plan(args.brief, scenes, args.seconds, args.bpm, args.beats_per_cut,
                         args.length, args.seed)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {out}: {len(plan['shots'])} shots x {plan['shot_s']}s = "
              f"{len(plan['shots']) * plan['shot_s']:.0f}s")
        return 0
    if args.cmd == "director":
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        scenes = list(args.scene)
        if args.scenes_file:
            scenes = scenes_from_file(Path(args.scenes_file)) or scenes
        if not scenes:
            print("director: give --scenes-file or at least one --scene "
                  "(a list of shots is the one thing this pipeline cannot invent)")
            return 2
        seconds = float(args.seconds or 0.0)
        if not seconds and args.song:
            seconds = audio_duration(args.song)
        if not seconds:
            seconds = 210.0
        bpm = float(args.bpm or 0.0)
        if not bpm and args.detect_bpm and args.song:
            bpm = bpm_from_audio(args.song)
            print(f"tempo: {bpm or 'unknown'} BPM (estimated from the audio)")
        plan = make_plan(args.brief, scenes, seconds, bpm, args.beats_per_cut, args.length, 0)
        plan_path = out_dir / "plan.json"
        plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"plan: {len(plan['shots'])} shots x {plan['shot_s']:.2f}s "
              f"= {len(plan['shots']) * plan['shot_s']:.0f}s of film -> {plan_path}")
        if args.plan_only:
            return 0
        res = render_plan(plan_path, limit=args.limit)
        if not res.get("ok"):
            print("render stopped early; the ledger says which shot failed")
            return 1
        if args.limit:
            print(f"rendered the first {args.limit} shot(s) only; assemble skipped")
            return 0
        film = out_dir / "film.mp4"
        assemble(plan_path, film, transition=args.transition, fade_s=args.fade,
                 audio=args.song, upscale=args.upscale)
        return 0
    if args.cmd == "render":
        res = render_plan(Path(args.plan), model=args.model, limit=args.limit,
                          max_retries=args.retries, dry=args.dry)
        return 0 if res["ok"] else 1
    if args.cmd == "assemble":
        res = assemble(Path(args.plan), Path(args.out), transition=args.transition,
                       fade_s=args.fade, audio=args.audio, upscale=args.upscale,
                       beat_audio=args.beat_audio)
        return 0 if res["ok"] else 1
    if args.cmd == "status":
        status(Path(args.plan))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
