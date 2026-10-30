"""Media limbs: generate an image, generate speech. Local binaries, no cloud, no pip.

Both generators are one-shot command-line processes that live OUTSIDE the kit (they are hundreds of
megabytes), so this module reaches them through config keys and never assumes a drive letter:

    config/console.json
      "media_root": "<MEDIA>"          # where the tools + models live on THIS machine
      "sd_exe":     ""                        # default <media_root>/tools/sd/sd-cli.exe
      "sd_model":   ""                        # default: the first *.safetensors under <media_root>/models/sd
      "piper_exe":  ""                        # default <media_root>/tools/piper/piper.exe
      "piper_voice":""                        # default: the first *.onnx under <media_root>/models/voices
      "image_timeout_s": 900
      "sound_timeout_s": 180

Design notes that are load-bearing:
  * stable-diffusion.cpp ships as sd-cli.exe (one-shot) and sd-server.exe. The one-shot binary is used
    here on purpose: no daemon, nothing left listening, and nothing to clean up if the request dies.
  * The 8 GB card is shared with the chat engine, and on this box the chat model takes all of it.
    MEASURED 2026-09-21: the CUDA build loaded its 6.9 GB checkpoint, ran 19.8 s, then died with
    `model manager cannot make enough memory available on CUDA0: need 644.05 MB device / 132.05 MB
    budget, available 0.00 MB device` - sd.cpp's own --auto-fit did NOT rescue it, so the earlier note
    here ("a generation can start while chat is loaded, just slower") was wrong and is struck. The route
    that works with the card held is the CPU build, and it is not a slow fallback: MEASURED through this
    limb, a real 1024x1024 SDXL-Turbo picture in 172.1 s. `hardware.picture_route` asks the box which
    route is open BEFORE an engine is spawned.
  * Turbo/distilled checkpoints need very few steps and a low guidance scale. Running SDXL-Turbo at
    the stock 20 steps / cfg 7 is both slow AND ugly, so the defaults are derived from the file name.
"""
from __future__ import annotations

import random
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from hardware import RENDER_NEED_MIB, picture_route, render_need_mib
from paths import KIT_ROOT, WORKSPACE, console_cfg

# Checkpoint families that are distilled: few steps, low guidance. Prefix match on the file name.
TURBO_HINTS = ("turbo", "schnell", "lightning", "lcm", "dmd", "hyper")

# When the caller leaves negative empty: stop the usual clone/morph artifacts on a one-person scene.
# Short on purpose - a long negative fights the prompt on distilled checkpoints.
STABILITY_NEGATIVE = (
    "duplicate, twins, cloned face, extra person, extra heads, extra arms, extra hands, "
    "third arm, extra legs, extra limbs, fused, morphing, deformed, extra fingers, "
    "mutated hands, poorly drawn hands, split face"
)
_GROUP_SCENE = re.compile(
    r"\b(two people|two women|two men|three|four|couple|pair|group|crowd|twins|both|together|duo)\b",
    re.I,
)
_ONE_PERSON = re.compile(
    r"\b(woman|man|girl|guy|lady|person|character|model|her|him|she|he)\b", re.I
)
_ALREADY_SOLO = re.compile(r"\b(single subject|one person|solo|one woman|one man)\b", re.I)


def stabilize_prompt(prompt: str) -> str:
    """A one-person scene gets a single-subject cue so the renderer does not clone the figure."""
    p = (prompt or "").strip()
    if not p or _GROUP_SCENE.search(p) or _ALREADY_SOLO.search(p):
        return p
    if _ONE_PERSON.search(p):
        return p + ", single subject, one person, two arms, two hands, coherent anatomy"
    return p


def stabilize_negative(negative: str) -> str:
    """Keep an explicit negative; fill the empty one with anti-clone/morph terms."""
    n = (negative or "").strip()
    return n if n else STABILITY_NEGATIVE


def _cfg() -> dict[str, Any]:
    try:
        return console_cfg() or {}
    except Exception:  # noqa: BLE001
        return {}


def media_root() -> Path:
    """Where the generators live: the machine's own media root, or THIS kit when it carries them.

    The console ships the picture engine inside the package (tools/sd-cpu), so a fresh all-in-one
    install works with no config at all: when the configured root has no tools/ of its own but this kit
    does, the kit IS the media root. A machine that has a real media root (the steward's <MEDIA>,
    with its CUDA build, its checkpoints and piper) keeps it exactly as before - the packaged engine is
    a fallback for machines that have nothing, never an override of a machine that has something.
    """
    raw = str(_cfg().get("media_root") or "<MEDIA>").strip()
    root = Path(raw)
    if not (root / "tools").is_dir() and (KIT_ROOT / "tools" / "sd-cpu" / "sd-cli.exe").is_file():
        return KIT_ROOT
    return root


def _resolve(cfg_key: str, default_rel: str, pattern: str | None = None) -> Path | None:
    """An explicit config path wins; otherwise the declared default; otherwise the first glob match."""
    explicit = str(_cfg().get(cfg_key) or "").strip()
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    base = media_root() / default_rel
    if pattern:
        parent = base if base.is_dir() else base.parent
        if parent.is_dir():
            hits = sorted(x for x in parent.glob(pattern) if x.is_file())
            if hits:
                return hits[0]
        return None
    return base if base.is_file() else None


def sd_exe() -> Path | None:
    cfg = _cfg()
    cands = [_resolve("sd_exe", "tools/sd/sd-cli.exe")]
    if not cfg.get("sd_exe"):
        # The CPU build is a deliberate fallback, not a silent substitution - status() says which ran.
        cands.append(media_root() / "tools" / "sd-cpu" / "sd-cli.exe")
    for c in cands:
        if c and c.is_file():
            return c
    return None


def _route_for_a_render(model_path: Any = None) -> tuple[str, str]:
    """('cpu', why) when the card cannot take this render right now, else ('', '').

    A boot-time total is a guess: the number that decides a render is the free VRAM at the moment it is
    asked for. And the number to clear is the CHECKPOINT, not the shortfall a failed attempt reported:
    MEASURED 2026-09-23, with the 6.9 GB SDXL-Turbo checkpoint on disk, 7113 MiB free drew a real picture
    on the CUDA build in 12.5 s while the chat model resident left ~0 MiB free. Asking a card for 644 MiB
    when the weights have nowhere to go is how a doomed render reaches the card at all.
    A sensor that fails must never decide: any error here means "try the card", the status quo.

    `model_path` is the checkpoint THIS render will use. Size the question from it, not from the
    default: MEASURED 2026-10-09, a call handed SD 1.5 (~1.3 GiB) was still judged against SDXL
    (6.6 GiB), answered "cpu", and burned 182 s on the CPU for weights the card could hold.
    """
    need = RENDER_NEED_MIB
    try:
        mp = model_path if model_path is not None else sd_model()
        if mp and mp.is_file():
            need = render_need_mib(mp.stat().st_size)
    except Exception:
        need = RENDER_NEED_MIB
    try:
        route, why = picture_route(need)
    except Exception:
        return "", ""
    if route == "cpu" and sd_cpu_exe() and sd_cpu_exe().is_file():
        return "cpu", why
    return "", ""


def sd_cpu_exe() -> Path | None:
    p = media_root() / "tools" / "sd-cpu" / "sd-cli.exe"
    return p if p.is_file() else None


def sd_model() -> Path | None:
    return _resolve("sd_model", "models/sd", "*.safetensors")


def image_recipes() -> list[dict[str, Any]]:
    """Named image models, each carrying the argument shape its own architecture needs.

    A single-checkpoint SD/SDXL file is one argv shape: `-m <file>`. A modern flow model is not.
    Qwen-Image wants a diffusion model, its OWN VAE and a text-encoder LLM passed separately, and a
    wrong or missing pairing fails deep inside the engine instead of saying so. So a multi-weight
    model is declared here as data, every path resolved against media_root, and each entry reports
    which of its files are actually present rather than claiming to be usable.

    Nothing is downloaded or guessed: an entry whose weights are absent is `ready: false` with the
    missing keys named, which is what makes an un-integrated model visible instead of silent.
    """
    raw = _cfg().get("image_models")
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        rec = dict(item)
        paths: dict[str, str] = {}
        missing: list[str] = []
        for key in ("engine", "diffusion_model", "vae", "llm", "llm_vision"):
            val = str(item.get(key) or "").strip()
            if not val:
                continue
            p = Path(val)
            if not p.is_absolute():
                p = media_root() / val
            paths[key] = str(p)
            if not p.is_file():
                missing.append(key)
        rec["paths"] = paths
        rec["missing"] = missing
        rec["ready"] = not missing and "diffusion_model" in paths
        # Where the weights come from, and on what terms. We ship the wiring, never the weights: a
        # model that is wired but not downloaded must be able to tell the operator which page to fetch
        # it from, or the capability is indistinguishable from a missing feature.
        rec["license"] = str(item.get("license") or "")
        src = item.get("sources")
        rec["sources"] = {k: dict(v) for k, v in src.items() if isinstance(v, dict)} if isinstance(src, dict) else {}
        # Opt-in unless the operator says otherwise. The declared heavyweight models exist for the
        # big host the same kit may be carried to; the fast single-checkpoint path stays what runs by
        # default on a card that cannot hold ~9.5 GB of weights.
        rec["default"] = bool(item.get("default"))
        out.append(rec)
    return out


def image_recipe(name: str) -> dict[str, Any] | None:
    """One declared image model by id, or None when the name is a plain file path."""
    key = str(name or "").strip()
    if not key:
        return None
    for rec in image_recipes():
        if str(rec.get("id")) == key:
            return rec
    return None


def _fresh_seed() -> int:
    """sd-cli's own default is 42. Leaving it unset reprints the same picture for the same words."""
    return random.SystemRandom().randrange(1, 2**31)


def _main_recipe() -> dict[str, Any] | None:
    """Qwen-Image 2.1 when its weights are on disk. Turbo is the fallback, not the main.

    MEASURED on gen-20260925-000614.png (SDXL-Turbo, 4 steps, 1024): a third arm fused into the
    costume. MEASURED again 2026-10-13 on the country-cover asks: Turbo ignored "no people", fused
    a second guitar and arm into the one figure, and with seed 42 reprinted that frame. Qwen-Image
    2.1 is the draw that follows the sentence. An explicit checkpoint, or cpu=True, stays on the
    classic path. A machine without these weights keeps Turbo.
    """
    rec = image_recipe("qwen-image-2.1")
    if rec and rec.get("ready"):
        return rec
    return None


def _align(value: int, step: int) -> int:
    """Engine dimensions must be a multiple of the architecture's own tile. 32 for flow models."""
    step = max(8, int(step or 64))
    return max(step, (int(value) // step) * step)


def _image_from_recipe(
    recipe: dict[str, Any],
    prompt: str,
    negative: str,
    width: int,
    height: int,
    steps: int,
    cfg_scale: float,
    timeout: int,
) -> dict[str, Any]:
    """Render with a declared multi-weight model: diffusion weights + its own VAE + text encoder."""
    rid = str(recipe.get("id"))
    paths = recipe.get("paths") or {}
    if recipe.get("missing"):
        return {
            "ok": False,
            "error": "image_weights_missing",
            "model": rid,
            "missing": list(recipe["missing"]),
            "paths": paths,
            "license": recipe.get("license") or "",
            "sources": recipe.get("sources") or {},
            "hint": "the model is wired but its weights are not all on disk. Fetch the files listed in "
                    "`missing` from the URLs in `sources` and drop them at the paths above; a flow "
                    "model needs its own VAE and text encoder alongside the diffusion weights",
        }
    exe = Path(paths["engine"]) if paths.get("engine") else (sd_exe() or sd_cpu_exe())
    if not exe or not Path(exe).is_file():
        return {"ok": False, "error": "no_image_engine", "model": rid,
                "hint": "no engine binary for this model - set \"engine\" in its image_models entry"}
    d_size = _inum(recipe.get("size"), 1024) or 1024
    d_steps = _inum(recipe.get("steps"), 40) or 40
    d_cfg = _fnum(recipe.get("cfg_scale"), 6.0) or 6.0
    align = _inum(recipe.get("align"), 32) or 32
    w = _align(_inum(width, d_size) or d_size, align)
    h = _align(_inum(height, d_size) or d_size, align)
    st = _inum(steps, d_steps) or d_steps
    cf = _fnum(cfg_scale, d_cfg) or d_cfg
    prompt = stabilize_prompt(prompt)
    negative = stabilize_negative(negative)
    dest = _out_dir("image") / _stamp("gen", ".png")
    to = int(timeout or _cfg().get("image_timeout_s") or 900)

    argv = [str(exe), "--diffusion-model", paths["diffusion_model"]]
    for key, flag in (("vae", "--vae"), ("llm", "--llm"), ("llm_vision", "--llm_vision")):
        if paths.get(key):
            argv += [flag, paths[key]]
    argv += ["-p", prompt, "-o", str(dest), "-W", str(w), "-H", str(h),
             "--steps", str(st), "--cfg-scale", str(cf), "-s", str(_fresh_seed())]
    if negative:
        argv += ["-n", negative]
    # Declared per model, not baked in: a new architecture's flags belong to its config entry.
    argv += [str(a) for a in (recipe.get("extra") or [])]
    try:
        t0 = time.time()
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=to, cwd=str(exe.parent))
        secs = round(time.time() - t0, 1)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "image_timeout", "seconds": to, "model": rid,
                "hint": "raise config/console.json \"image_timeout_s\" - a first run also loads "
                        "several weight files into RAM, and a flow model is slow on an 8 GB card"}
    except OSError as exc:
        return {"ok": False, "error": "image_engine_failed", "why": str(exc)[:300], "exe": str(exe)}
    if not dest.is_file() or dest.stat().st_size == 0:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()[-6:]
        return {"ok": False, "error": "image_failed", "exit": proc.returncode, "log_tail": tail,
                "seconds": secs, "model": rid, "exe": str(exe)}
    return {
        "ok": True, "path": str(dest), "bytes": dest.stat().st_size, "seconds": secs,
        "width": w, "height": h, "steps": st, "cfg_scale": cf, "model": rid,
        "engine": exe.parent.name, "command": " ".join(argv[1:]),
        "weights": {k: Path(v).name for k, v in paths.items()},
        "note": "the picture is on disk; call image_info for its dimensions or image_see to have the "
                "vision model describe it back",
    }


def piper_exe() -> Path | None:
    return _resolve("piper_exe", "tools/piper/piper.exe")


def piper_voice() -> Path | None:
    return _resolve("piper_voice", "models/voices", "*.onnx")


def espeak_data() -> Path | None:
    p = media_root() / "tools" / "piper" / "espeak-ng-data"
    return p if p.is_dir() else None


def _is_turbo(name: str) -> bool:
    n = name.lower()
    return any(h in n for h in TURBO_HINTS)


def _is_xl(name: str) -> bool:
    n = name.lower()
    return "xl" in n or "sdxl" in n


def _out_dir(kind: str) -> Path:
    d = WORKSPACE / ("images" if kind == "image" else "audio")
    d.mkdir(parents=True, exist_ok=True)
    return d


MEDIA_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MEDIA_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def workspace_image_path(name: str) -> Path | None:
    """A file inside workspace/images, or None. Basename only — no path traversal."""
    base = Path(str(name or "").strip()).name
    if not base or base in {".", ".."}:
        return None
    ext = Path(base).suffix.lower()
    if ext not in MEDIA_IMAGE_EXTS:
        return None
    root = _out_dir("image").resolve()
    p = (root / base).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        return None
    return p if p.is_file() else None


def _stamp(prefix: str, ext: str) -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}{ext}"


def _inum(value: Any, default: int) -> int:
    """A model can send "4", 4.0, "" or "small" for a numeric arg. Never raise inside a handler."""
    try:
        if value is None or (isinstance(value, str) and not value.strip()):
            return int(default)
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _fnum(value: Any, default: float) -> float:
    try:
        if value is None or (isinstance(value, str) and not value.strip()):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def memory_hint(tail: list[str] | None) -> str:
    """Turn the engine's own memory warning into the operator's next move.

    A full card is the NORMAL state on a working console: the chat model is the card. Measured on this
    host 2026-09-21 with gemma4-12b resident (10.9 GB, 79 GPU layers), the picture engine loaded its
    6.9 GB checkpoint and then died 19.8 s in, exit 1, no picture, its own log saying

        [WARN] model_manager.cpp:1801 - model manager cannot make enough memory available on CUDA0:
        need 644.05 MB device / 132.05 MB budget, available 0.00 MB device / 1015.60 MB budget

    Reported as a bare `image_failed`, that leaves the operator reading a CUDA warning - which is how
    "image creation is broken" is experienced when the card is merely busy. Returns "" for any other
    failure: a missing checkpoint must never be given a memory story.
    """
    blob = " ".join(str(x) for x in (tail or []))
    low = blob.lower()
    if "cannot make enough memory available" not in low and "available 0.00 mb device" not in low:
        return ""
    return ("the picture engine could not get device memory because the chat model is holding the card "
            "(the engine's own log: \"cannot make enough memory available on CUDA0\"). Two ways out: stop "
            "the chat engine to free the card, or ask for the picture on the CPU - slower, and the card "
            "is not needed at all. media_status names both engines.")


def media_status() -> dict[str, Any]:
    """What is actually wired on this machine. The admin console and the model both read this."""
    se, sce, sm, pe, pv, ed = sd_exe(), sd_cpu_exe(), sd_model(), piper_exe(), piper_voice(), espeak_data()
    studio = studio_voice_status()
    piper_ready = bool(pe and pv and ed)
    main_id = str((_main_recipe() or {}).get("id") or "")
    out: dict[str, Any] = {
        "ok": True,
        "media_root": str(media_root()),
        "media_root_exists": media_root().is_dir(),
        "image": {
            "exe": str(se) if se else None,
            "exe_on_disk": bool(se and se.is_file()),
            "cpu_exe": str(sce) if sce else None,
            "model": str(sm) if sm else None,
            "model_bytes": sm.stat().st_size if sm and sm.is_file() else 0,
            "ready": bool(se and sm),
            # What actually runs when nothing is asked for by name. Qwen-Image 2.1 when its weights
            # are on disk; the fast single-checkpoint path only when they are not.
            "default": main_id or (sm.name if sm else None),
            # Every declared model, including one whose weights are not on disk yet: a model that is
            # wired but not downloaded is reported as exactly that, never as absent.
            "models": [
                {
                    "id": str(r.get("id")),
                    "ready": bool(r.get("ready")),
                    "missing": list(r.get("missing") or []),
                    "steps": r.get("steps"),
                    "cfg_scale": r.get("cfg_scale"),
                    "size": r.get("size"),
                    "engine": Path(str((r.get("paths") or {}).get("engine") or "")).name or None,
                    "default": bool(r.get("default")) or str(r.get("id")) == main_id,
                    # We ship the wiring, never the weights: a model that is not downloaded must
                    # carry the vendor URLs that would complete it, and the terms it comes under.
                    "license": str(r.get("license") or ""),
                    "sources": dict(r.get("sources") or {}),
                }
                for r in image_recipes()
            ],
        },
        "sound": {
            "exe": str(pe) if pe else None,
            "exe_on_disk": bool(pe and pe.is_file()),
            "voice": str(pv) if pv else None,
            "espeak_data": str(ed) if ed else None,
            "piper_ready": piper_ready,
            "studio": studio,
            "ready": piper_ready or bool(studio.get("up")),
        },
    }
    return out


def image_generate(
    prompt: str,
    negative: str = "",
    width: int = 0,
    height: int = 0,
    steps: int = 0,
    cfg_scale: float = 0.0,
    model: str = "",
    cpu: bool = False,
    timeout: int = 0,
) -> dict[str, Any]:
    """Render one picture with the local stable-diffusion.cpp build. Writes into workspace/images."""
    p = (prompt or "").strip()
    if not p:
        return {"ok": False, "error": "empty_prompt"}
    p = stabilize_prompt(p)
    negative = stabilize_negative(negative)
    # A declared multi-weight model is chosen by id; anything else is a checkpoint path, which is the
    # single-weight shape below. `cpu` forces the classic path, since a declared engine is its own build.
    recipe = None if cpu else image_recipe(model)
    if recipe is None and not cpu and not str(model or "").strip():
        recipe = _main_recipe()
    if recipe is not None:
        return _image_from_recipe(recipe, p, negative, width, height, steps, cfg_scale, timeout)
    # Resolve the checkpoint BEFORE asking which route is open: the honest answer depends on
    # THIS checkpoint, not on the default one (see _route_for_a_render).
    mp = Path(model) if model.strip() else sd_model()
    # Which route is actually open? Asked BEFORE an engine is spawned, from live readings, so a card the
    # chat model is holding costs the operator nothing instead of a ~20 s attempt and a CUDA warning.
    routed_on, route_why = ("cpu", "") if cpu else _route_for_a_render(mp)
    if routed_on == "cpu":
        cpu = True
    exe = (sd_cpu_exe() if cpu else sd_exe()) or sd_exe() or sd_cpu_exe()
    if not exe or not exe.is_file():
        return {"ok": False, "error": "no_image_engine",
                "hint": "no sd-cli.exe found - set config/console.json \"sd_exe\", or \"media_root\" to the "
                        "folder holding tools/sd/sd-cli.exe",
                "media_root": str(media_root())}
    if not mp or not mp.is_file():
        return {"ok": False, "error": "no_image_model",
                "hint": "no *.safetensors checkpoint found - set config/console.json \"sd_model\", or put a "
                        "checkpoint under <media_root>/models/sd/",
                "media_root": str(media_root())}
    name = mp.name
    # Distilled checkpoints: few steps, guidance near 1. Otherwise the SD defaults.
    d_steps, d_cfg = (4, 1.0) if _is_turbo(name) else (20, 7.0)
    d_size = 1024 if _is_xl(name) else 512
    w = _inum(width, d_size) or d_size
    h = _inum(height, d_size) or d_size
    st = _inum(steps, d_steps) or d_steps
    cf = _fnum(cfg_scale, d_cfg) or d_cfg
    # SD wants multiples of 64; a bad size is a confusing crash rather than an error message.
    w = max(64, (w // 64) * 64)
    h = max(64, (h // 64) * 64)
    dest = _out_dir("image") / _stamp("gen", ".png")
    cfgd = _cfg()
    to = int(timeout or cfgd.get("image_timeout_s") or 900)

    argv = [str(exe), "-m", str(mp), "-p", p, "-o", str(dest),
            "-W", str(w), "-H", str(h), "--steps", str(st), "--cfg-scale", str(cf),
            "-s", str(_fresh_seed())]
    if negative:
        argv += ["-n", negative]
    # MEASURED on this host 2026-09-23, SDXL-Turbo at 1024x1024: on the CPU build the untitled VAE decode
    # died mid-pass (exit 3221225786, its log ending at "decoding 1 latents"), wrote no file, and the limb
    # answered `image_failed` after 183.4 s. The SAME argv plus --vae-tiling decoded the latent in 49 tiles
    # and saved a real 2.1 MB picture in 272.0 s. Tiling is the difference between "image creation is
    # broken" and a file on disk, so the CPU route always carries it. The CUDA build drew the same prompt
    # in 12.5 s without it and is left exactly as it was.
    if cpu or exe.parent.name.lower().endswith("cpu"):
        argv.append("--vae-tiling")
    try:
        t0 = time.time()
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=to, cwd=str(exe.parent))
        secs = round(time.time() - t0, 1)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "image_timeout", "seconds": to, "model": name,
                "hint": "raise config/console.json \"image_timeout_s\" - a first run also loads the checkpoint"}
    except OSError as exc:
        return {"ok": False, "error": "image_engine_failed", "why": str(exc)[:300], "exe": str(exe)}
    if not dest.is_file() or dest.stat().st_size == 0:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()[-6:]
        out: dict[str, Any] = {"ok": False, "error": "image_failed", "exit": proc.returncode,
                               "log_tail": tail, "seconds": secs, "model": name,
                               "exe": str(exe)}
        hint = memory_hint(tail)
        if hint:
            out["hint"] = hint
            # Offered only when that engine is actually on disk: a route the operator cannot take
            # is worse than no suggestion, because it reads as another broken tool.
            if not cpu and sd_cpu_exe() and sd_cpu_exe().is_file():
                out["retry_with_cpu"] = True
                # MEASURED on this host: the chat model holds the card on every working boot, so "the card
                # is busy" is the normal state here, not an edge case. Reporting VRAM and stopping is the
                # same as never drawing at all, which is how the operator experiences it ("image creation
                # is broken"). So take the route that needs no card - once - and say which route drew it.
                again = image_generate(prompt=p, negative=negative, width=w, height=h, steps=st,
                                       cfg_scale=cf, model=str(mp), cpu=True, timeout=to)
                again["fell_back_from"] = {"engine": exe.parent.name, "reason": hint}
                if again.get("ok"):
                    again["drawn_on"] = "cpu"
                    again["note"] = (str(again.get("note") or "").strip() +
                                     " Drawn on the CPU because the card was held by the chat model -"
                                     " slower than the card, but it needs no video memory.").strip()
                return again
        return out
    return {
        "ok": True, "path": str(dest), "bytes": dest.stat().st_size, "seconds": secs,
        "width": w, "height": h, "steps": st, "cfg_scale": cf,
        "model": name, "engine": exe.parent.name, "command": " ".join(argv[1:]),
        "note": ("the picture is on disk; call image_info for its dimensions or image_see to have the "
                 "vision model describe it back"
                 + (" Drawn on the CPU because %s." % route_why if route_why else "")),
    }


def studio_voice_status() -> dict[str, Any]:
    """Is VoiceStudio installed here, and is its loopback speech service answering?

    Read-only. The AGPL application is never imported; this asks voice_harness, which speaks HTTP.
    """
    out: dict[str, Any] = {
        "present": False, "up": False, "base": "", "version": "",
        "models": [], "error": "", "engine": "voicestudio",
    }
    try:
        import voice_harness as vh
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:200]
        return out
    try:
        inst = vh.install_state()
        out["present"] = bool(inst.get("present"))
        if not out["present"]:
            out["error"] = str(inst.get("reason") or "VoiceStudio is not installed here")
            return out
        probed = vh.probe(timeout=vh.PROBE_TIMEOUT_S)
        out["up"] = bool(probed.get("up"))
        out["base"] = str(probed.get("base") or vh.service_base())
        out["version"] = str(probed.get("version") or "")
        out["models"] = [m for m in (probed.get("models") or []) if m]
        out["error"] = str(probed.get("error") or "")
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:200]
    return out


def _wav_seconds(path: Path) -> float:
    try:
        import wave

        with wave.open(str(path)) as w:
            return round(w.getnframes() / float(w.getframerate() or 1), 2)
    except Exception:  # noqa: BLE001
        return 0.0


def _speak_via_studio(text: str, voice: str = "", length_scale: float = 1.0) -> dict[str, Any]:
    """One clip through VoiceStudio's OpenAI-compatible /v1/audio/speech. WAV, kit workspace."""
    import voice_harness as vh

    speed = None
    try:
        ls = float(length_scale or 1.0)
        if ls > 0 and abs(ls - 1.0) > 1e-6:
            speed = round(1.0 / ls, 4)
    except (TypeError, ValueError):
        speed = None
    t0 = time.time()
    r = vh.speak(text, voice=str(voice or ""), fmt="wav", speed=speed)
    secs = round(time.time() - t0, 1)
    if not r.get("ok"):
        r["seconds"] = secs
        r["engine"] = "voicestudio"
        return r
    dest = Path(str(r.get("path") or ""))
    dur = _wav_seconds(dest) if dest.is_file() else 0.0
    r["engine"] = "voicestudio"
    r["seconds"] = secs
    r["audio_seconds"] = dur
    r["note"] = "spoken through VoiceStudio on loopback; the WAV is in this kit's workspace"
    return r


def sound_speak(text: str, voice: str = "", length_scale: float = 1.0, timeout: int = 0) -> dict[str, Any]:
    """Speak text into a WAV. VoiceStudio when that service is up; Piper otherwise."""
    t = (text or "").strip()
    if not t:
        return {"ok": False, "error": "empty_text"}
    studio = studio_voice_status()
    if studio.get("up"):
        r = _speak_via_studio(t, voice=voice, length_scale=length_scale)
        if r.get("ok"):
            return r
        r["fell_back_from"] = "voicestudio"
        # Studio answered health then refused the clip: still try Piper so a down engine
        # is not a silent show-stopper when the CPU voice is on disk.
    exe = piper_exe()
    if not exe or not exe.is_file():
        return {"ok": False, "error": "no_voice_engine",
                "hint": "piper.exe not found - set config/console.json \"piper_exe\" or \"media_root\"",
                "media_root": str(media_root())}
    vp = Path(voice) if voice.strip() else piper_voice()
    if not vp or not vp.is_file():
        return {"ok": False, "error": "no_voice_model",
                "hint": "no voice *.onnx found - set config/console.json \"piper_voice\", or put a voice "
                        "under <media_root>/models/voices/",
                "media_root": str(media_root())}
    ed = espeak_data()
    if not ed:
        return {"ok": False, "error": "no_espeak_data",
                "hint": "espeak-ng-data is missing beside piper.exe; piper cannot phonemise without it"}
    dest = _out_dir("sound") / _stamp("speak", ".wav")
    to = int(timeout or _cfg().get("sound_timeout_s") or 180)
    argv = [str(exe), "-m", str(vp), "-f", str(dest), "--espeak_data", str(ed), "-q"]
    if float(length_scale or 1.0) != 1.0:
        argv += ["--length_scale", str(float(length_scale))]
    try:
        t0 = time.time()
        proc = subprocess.run(argv, input=t, capture_output=True, text=True, timeout=to, cwd=str(exe.parent))
        secs = round(time.time() - t0, 1)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "sound_timeout", "seconds": to}
    except OSError as exc:
        return {"ok": False, "error": "voice_engine_failed", "why": str(exc)[:300], "exe": str(exe)}
    if not dest.is_file() or dest.stat().st_size == 0:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()[-6:]
        return {"ok": False, "error": "sound_failed", "exit": proc.returncode, "log_tail": tail, "seconds": secs}
    dur = _wav_seconds(dest)
    out_ok: dict[str, Any] = {
        "ok": True, "path": str(dest), "bytes": dest.stat().st_size, "seconds": secs,
        "audio_seconds": dur, "voice": vp.name, "engine": "piper",
        "command": " ".join(argv[1:]),
        "note": "the WAV is on disk beside the other media the console made (Piper fallback)",
    }
    if studio.get("up"):
        out_ok["fell_back_from"] = "voicestudio"
    return out_ok
