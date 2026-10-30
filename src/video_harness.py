"""LYGO video harness — ComfyUI as a one-engine-at-a-time service, for an 8 GB card.

The console can already draw a picture and make a song. It could not make a moving picture, so
this is the third making engine and it follows the same law as the other two: the heavy thing
lives **outside** the git tree, is reached by a **config path** (never a drive letter), is started
only when the operator asks, and is terminated in a ``finally`` so nothing is left holding the
card or listening on a port when the turn ends.

Three facts are kept SEPARATE on purpose — app present / adapter present / weights present. A
single ``installed`` boolean is how a select says "usable" while the render refuses, and how a
missing weight file gets masked by an ``engine_not_installed`` verdict. Each is reported, and a
refusal names the exact missing file.

Measured on the steward's rig (RTX 4060 Ti 8 GB, i5-13600KF, 32 GB RAM, models on a USB drive),
on 2026-10-20 with the current ComfyUI checks:

  * Q6_K, 704x480, 97 frames, 20 steps    ~298 s warm; ~199 s warm on a second prompt
  * Q4_K_M, 704x480, 97 frames, 20 steps   ~358 s warm
  * Q8_0, 704x480, 49 frames, 20 steps      ~901 s in the current desktop-VRAM state, and the
                                            output still morphed. The old ~260 s number does not
                                            reproduce when the card is already shared with apps.

The useful conclusions are narrower than the old note: **Q6_K at 97 frames is the quality/speed
fit** on this card. 49 frames is the failure mode; the Q8_0 path is kept only as a recorded
legacy comparison. The model load and the card's free-VRAM state dominate wall time more than
the step count.

Do not promise a duration from the model's paper numbers. Quote the measured ratio above.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SIGNATURE = "Δ9Φ963-LYGO-VIDEO-HARNESS-v1"

#: Upstream projects. ComfyUI is GPL-3.0; the Wan 2.2 weights this drives are Apache-2.0.
UPSTREAM = "https://github.com/comfyanonymous/ComfyUI"
UPSTREAM_LICENSE = "GPL-3.0-only (ComfyUI) / Apache-2.0 (Wan 2.2 weights)"
BRIDGE_REASON = (
    "ComfyUI is GPL-3.0-only and the LYGO console is LYGO Sovereign License v3.0, which forbids "
    "relicensing to GPL and forbids publishing a modified core - so the engine is never vendored "
    "into this tree. The console bridges to a ComfyUI the operator installed, keeping every plugin "
    "file outside the kit and reachable only by the config path below. The MODELS are Apache-2.0 "
    "and are the only part that may travel with a public build."
)

#: ComfyUI's own default port. An already-running engine is REUSED rather than fought over, and it
#: collides with nothing this kit owns (9641 console / 11441 chat engine / 9642 gateway / 3900 voice).
DEFAULT_PORT = 8188

#: Measured, not guessed: the service needs ~155 s to answer on this machine's USB-hosted install,
#: so a 30 s budget reports a healthy engine as dead.
READY_POLL_S = 2.0
DEFAULT_BUDGET_S = 240
PROBE_TIMEOUT_S = 3.0

#: A render is minutes, not seconds. One shot; the caller polls rather than blocking a request.
DEFAULT_RENDER_TIMEOUT_S = 3600

#: ComfyUI answers /system_stats with the device, the VRAM and its own version. This is the door
#: the harness trusts, because it is the same door the render path uses.
HEALTH_PATHS = ("/system_stats",)

#: Longest clip this harness inlines as a data URL for the panel. Beyond this the file is reported
#: by absolute path and the panel says so, rather than shipping megabytes through a JSON route.
INLINE_LIMIT_BYTES = 2 * 1024 * 1024

#: The loader class the GGUF path needs. Its presence comes from the ComfyUI-GGUF plugin, which is
#: the *adapter* - a fact distinct from the app and from the weights.
GGUF_LOADER = "UnetLoaderGGUF"


# --- the declared model recipes (data, never a path) ---------------------------------------------
#
# A flow model is not one file. It is a diffusion file plus its OWN text encoder plus its OWN VAE,
# plus its own steps/cfg/shift and frame limits. That is declared here as data so a second model is
# an entry, not a branch. `length` must satisfy 4n+1 (the Wan latent's own rule); the recipes carry
# legal values so a caller cannot ask for 50.

MODELS: dict[str, dict[str, Any]] = {
    "wan22-5b-q6": {
        "title": "Wan 2.2 TI2V-5B (Q6_K, 97f default)",
        "uploader": "unsloth/Wan2.2-TI2V-5B-GGUF",
        "diffusion": "Wan2.2-TI2V-5B-Q6_K.gguf",
        "text_encoder": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "vae": "wan2.2_vae.safetensors",
        "clip_type": "wan",
        "license": "apache-2.0",
        "sha256": {
            "Wan2.2-TI2V-5B-Q6_K.gguf":
                "355f6bee35c4c6cbd0f275112619fe8ac6f7b9b067b885723667b3bde29497c3",
            "umt5_xxl_fp8_e4m3fn_scaled.safetensors":
                "c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68",
            "wan2.2_vae.safetensors":
                "e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156",
        },
        "defaults": {"width": 704, "height": 480, "length": 97, "steps": 20, "cfg": 5.0,
                     "shift": 8.0, "sampler_name": "uni_pc", "scheduler": "simple",
                     "denoise": 1.0, "fps": 24},
        "limits": {"max_width": 704, "max_height": 480, "max_length": 121,
                   "legal_lengths": [25, 49, 73, 97, 121]},
        "vram_mib": 6000,
        "measured": {"cold_boot_s": 155, "wall_s_97f_warm_s": 298,
                     "wall_s_97f_human_warm_s": 199},
        "note": "MEASURED 2026-10-20 on this rig: Q6_K is the best quality/speed fit found. "
                "97 frames at 704x480, 20 steps took 298 s warm on the fox benchmark and 199 s "
                "warm on the human benchmark. The human stayed one person with a coherent red "
                "coat and face across the clip; no gross morphing. Q4_K_M is faster (358 s) but "
                "slightly softer. Q8_0 at 49 frames took 901 s and still morphed, so it is legacy. "
                "The default MUST stay at 97 frames; 49 was the failure mode.",
    },
    "wan22-5b-q4": {
        "title": "Wan 2.2 TI2V-5B (Q4_K_M, fast fallback)",
        "uploader": "unsloth/Wan2.2-TI2V-5B-GGUF",
        "diffusion": "Wan2.2-TI2V-5B-Q4_K_M.gguf",
        "text_encoder": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "vae": "wan2.2_vae.safetensors",
        "clip_type": "wan",
        "license": "apache-2.0",
        "sha256": {
            "Wan2.2-TI2V-5B-Q4_K_M.gguf":
                "95b19697b7f98e65b0a543640e9ca7b4dfec32e2a6e3731e8e10708be52655e2",
            "umt5_xxl_fp8_e4m3fn_scaled.safetensors":
                "c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68",
            "wan2.2_vae.safetensors":
                "e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156",
        },
        "defaults": {"width": 704, "height": 480, "length": 97, "steps": 20, "cfg": 5.0,
                     "shift": 8.0, "sampler_name": "uni_pc", "scheduler": "simple",
                     "denoise": 1.0, "fps": 24},
        "limits": {"max_width": 704, "max_height": 480, "max_length": 121,
                   "legal_lengths": [25, 49, 73, 97, 121]},
        "vram_mib": 4600,
        "measured": {"wall_s_97f_warm_s": 358},
        "note": "Fast fallback: 97 frames in 358 s warm. The fox stayed coherent; the face was "
                "slightly softer than Q6. Use when Q6 is unavailable, not as the default.",
    },
    "wan22-5b": {
        "title": "Wan 2.2 TI2V-5B (Q8_0)",
        "uploader": "unsloth/Wan2.2-TI2V-5B-GGUF",
        "diffusion": "Wan2.2-TI2V-5B-Q8_0.gguf",
        "text_encoder": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "vae": "wan2.2_vae.safetensors",
        "clip_type": "wan",
        "license": "apache-2.0",
        "sha256": {
            "Wan2.2-TI2V-5B-Q8_0.gguf":
                "57bece983817ab2f957546683bb670f13be7d99022d45674840cd999a050ea8f",
            "umt5_xxl_fp8_e4m3fn_scaled.safetensors":
                "c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68",
            "wan2.2_vae.safetensors":
                "e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156",
        },
        "defaults": {"width": 704, "height": 480, "length": 97, "steps": 20, "cfg": 5.0,
                     "shift": 8.0, "sampler_name": "uni_pc", "scheduler": "simple",
                     "denoise": 1.0, "fps": 24},
        "limits": {"max_width": 704, "max_height": 480, "max_length": 121,
                   "legal_lengths": [25, 49, 73, 97, 121]},
        "vram_mib": 7300,
        "measured": {"cold_boot_s": 155, "wall_s_49f": 260, "sampling_s_49f": 65,
                     "wall_s_97f_warm_s": 268},
        "note": "Text-to-video AND image-to-video in one model. The template ships the image path "
                "bypassed; this harness builds the text-to-video half and never wires a start image. "
                "FRAME COUNT IS THE QUALITY LEVER, MEASURED: the same fox-in-snow prompt produced no "
                "readable fox AT ALL at 49 frames and a clearly resolved, coherent fox at 97, and the "
                "97-frame render cost only 8 s more than the 49-frame one once the model was "
                "resident. Sampling is cheap; the load is not. So `length` DEFAULTS TO 97 - a default "
                "that renders an unreadable subject is not a speed setting, it is a broken one, and "
                "8 s is not worth it. Use a shorter legal length only when the shot genuinely does "
                "not need it. `vram_mib` is the RESIDENT card need measured at 704x480x97: pass it "
                "to the render swap, because a park that frees only RAM leaves the card short and "
                "pushes ComfyUI into CPU offload (MEASURED 2026-10-10: a clip that takes 4.5 min was "
                "still running at 13+ with this short by ~770 MiB).",
    },
    "wan22-5b-turbo": {
        "title": "Wan 2.2 TI2V-5B Turbo (Q8_0, distilled)",
        # Same 5.1 GB unet as the non-turbo build (measured on disk 2026-10-10), so it needs the same
        # card. Leaving this None made a park on the turbo report need=0, which SKIPS the VRAM test
        # entirely - the render then competes with the chat model for the card, which is the exact
        # silent-CPU-render failure this module exists to prevent.
        "vram_mib": 7300,
        "uploader": "hum-ma/Wan2.2-TI2V-5B-Turbo-GGUF",
        "diffusion": "Wan2_2-TI2V-5B-Turbo-Q8_0.gguf",
        "text_encoder": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "vae": "wan2.2_vae.safetensors",
        "clip_type": "wan",
        "license": "apache-2.0",
        "sha256": {
            "Wan2_2-TI2V-5B-Turbo-Q8_0.gguf":
                "b4d7d1888d5ec2c29046cc443239faad99614cd3d356c61d8192fcac5f4c98fc",
            "umt5_xxl_fp8_e4m3fn_scaled.safetensors":
                "c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68",
            "wan2.2_vae.safetensors":
                "e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156",
        },
        "defaults": {"width": 704, "height": 480, "length": 49, "steps": 8, "cfg": 1.0,
                     "shift": 8.0, "sampler_name": "uni_pc", "scheduler": "simple",
                     "denoise": 1.0, "fps": 24},
        "limits": {"max_width": 704, "max_height": 480, "max_length": 121,
                   "legal_lengths": [25, 49, 73, 97, 121]},
        "measured": {"cold_boot_s": 155, "wall_s_49f": 247, "sampling_s_49f": None},
        "note": "Distilled few-step build. MEASURED AND NOT RECOMMENDED on this rig: 8 steps saved "
                "only 13 s of a 247 s render (5%) because the ~200 s model load dominates the total, "
                "and the output degraded visibly on the same prompt - the breaking wave lost its "
                "structure and read as a smooth blob, against clearly resolved water at 20 steps. It "
                "stays declared so the comparison remains recorded, not because it is the better "
                "choice. Use wan22-5b.",
    },
}

#: The same words the console's own reasoning block uses, so the operator reads one story.
MEASURED_RATIO = (
    "Measured on this rig 2026-10-20 at 704x480, 20 steps: Q6_K 97 frames = 298 s warm and "
    "coherent; Q4_K_M 97 frames = 358 s warm; Q8_0 49 frames = 901 s and still morphed. Frame "
    "count is the quality lever, and Q6_K at 97 frames is the default. Do not use 49 frames."
)


# --- where things are (config path, never a drive letter) ----------------------------------------


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name) or ""
    return Path(raw) if raw else None


def _video_cfg() -> dict[str, Any]:
    """The kit's own config block, so an operator can point the bridge somewhere and say so."""
    try:
        import paths

        cfg = paths.console_cfg() or {}
    except Exception:  # noqa: BLE001 - a config that will not load is not a crash here
        cfg = {}
    return cfg.get("video") if isinstance(cfg.get("video"), dict) else {}


def app_root() -> Path | None:
    """The ComfyUI install. Set by config or environment; NEVER a hardcoded drive letter.

    A drive letter baked into this file is what breaks the moment the operator's stick moves from
    I: to D:, so an unset root is reported as a named refusal with the exact key to set instead of
    a guess that happens to work on the machine it was written on.
    """
    raw = _video_cfg().get("app_root") or os.environ.get("LYGO_VIDEO_APP") or ""
    return Path(str(raw)) if raw else None


def media_root() -> Path | None:
    """Where the weights live. ComfyUI's own convention is ``<app>/models``, so that is the default."""
    raw = _video_cfg().get("media_root") or os.environ.get("LYGO_VIDEO_ROOT") or ""
    if raw:
        return Path(str(raw))
    root = app_root()
    return (root / "models") if root else None


def comfy_python() -> Path | None:
    root = app_root()
    return (root / ".venv" / "Scripts" / "python.exe") if root else None


def workspace_dir() -> Path:
    """Where rendered clips land: this kit's own workspace tree, never the engine's."""
    try:
        import paths

        return Path(paths.WORKSPACE) / "video"
    except Exception:  # noqa: BLE001
        return Path(__file__).resolve().parents[1] / "workspace" / "video"


def engine_log_path() -> Path:
    return workspace_dir() / "engine.log"


def launch_record_path() -> Path:
    return workspace_dir() / "engine-launch.json"


# --- state: app / adapter / weights, three separate facts -----------------------------------------


def app_state() -> dict[str, Any]:
    """Is ComfyUI installed here, and what does the install actually contain?"""
    root = app_root()
    out: dict[str, Any] = {
        "root": str(root) if root else "",
        "present": False,
        "main": "",
        "python": "",
        "python_present": False,
        "version": "",
    }
    if root is None:
        out["reason"] = (
            "no ComfyUI install is declared - set video.app_root in config/console.json (or the "
            "LYGO_VIDEO_APP environment variable) to the ComfyUI folder"
        )
        return out
    main = root / "main.py"
    py = comfy_python()
    out["main"] = str(main)
    out["python"] = str(py) if py else ""
    try:
        out["present"] = main.is_file()
        out["python_present"] = bool(py and py.is_file())
    except OSError as exc:
        out["reason"] = f"the install could not be read: {exc}"
        return out
    if not out["present"] and "reason" not in out:
        out["reason"] = f"no main.py under {root} - that is not a ComfyUI install"
    return out


def adapter_state() -> dict[str, Any]:
    """The plugin that lets the engine read a GGUF diffusion file.

    Kept apart from the app and the weights because all three fail differently: without the app
    nothing boots, without this the loader class does not exist, and without the weights the
    loader finds no file. Collapsing them is how a missing weight reads as a missing engine.
    """
    root = app_root()
    out: dict[str, Any] = {"name": "ComfyUI-GGUF", "loader": GGUF_LOADER,
                           "path": "", "present": False}
    if root is None:
        out["reason"] = "no ComfyUI install is declared, so its plugins cannot be located"
        return out
    node_dir = root / "custom_nodes" / "ComfyUI-GGUF"
    out["path"] = str(node_dir)
    try:
        out["present"] = (node_dir / "nodes.py").is_file()
    except OSError as exc:
        out["reason"] = f"the plugin directory could not be read: {exc}"
        return out
    if not out["present"]:
        out["reason"] = (
            f"the {GGUF_LOADER} loader is absent - install the ComfyUI-GGUF plugin under "
            f"{node_dir}"
        )
    return out


def weights_state(model_id: str) -> dict[str, Any]:
    """Per-file presence for one recipe, naming the missing file rather than a count."""
    recipe = MODELS.get(model_id)
    if recipe is None:
        return {"declared": False, "present": False, "files": [], "missing": [],
                "reason": f"{model_id!r} is not a declared model"}
    root = media_root()
    roles = {
        "diffusion": ("unet", recipe["diffusion"]),
        "text_encoder": ("text_encoders", recipe["text_encoder"]),
        "vae": ("vae", recipe["vae"]),
    }
    files, missing = [], []
    for role, (sub, name) in roles.items():
        path = (root / sub / name) if root else None
        entry = {"role": role, "name": name, "path": str(path) if path else "", "present": False,
                 "bytes": 0}
        try:
            if path is not None and path.is_file():
                entry["present"] = True
                entry["bytes"] = int(path.stat().st_size)
        except OSError:
            entry["present"] = False
        if not entry["present"]:
            missing.append(name)
        files.append(entry)
    out = {"declared": True, "present": not missing, "files": files, "missing": missing,
           "root": str(root) if root else ""}
    if root is None:
        out["reason"] = (
            "no weights root is declared - set video.media_root (or LYGO_VIDEO_ROOT) to the "
            "ComfyUI models folder"
        )
    elif missing:
        out["reason"] = (
            f"{len(missing)} weight file(s) missing for {model_id}: {', '.join(missing)}"
        )
    return out


def model_state(model_id: str) -> dict[str, Any]:
    """The recipe, its three facts kept apart, and whether it can actually be driven right now."""
    recipe = MODELS.get(model_id)
    if recipe is None:
        return {"id": model_id, "declared": False, "drivable": False,
                "reason": f"{model_id!r} is not a declared model"}
    app, adapter, weights = app_state(), adapter_state(), weights_state(model_id)
    out = {
        "id": model_id,
        "title": recipe["title"],
        "declared": True,
        "license": recipe["license"],
        "uploader": recipe["uploader"],
        "app_present": app["present"],
        "adapter_present": adapter["present"],
        "weights_present": weights["present"],
        "weights": weights,
        "defaults": recipe["defaults"],
        "limits": recipe["limits"],
        "measured": recipe["measured"],
        "note": recipe["note"],
        "drivable": bool(app["present"] and adapter["present"] and weights["present"]),
    }
    if not out["drivable"]:
        # Name the FIRST missing thing and say which of the three it is - never "not installed".
        if not app["present"]:
            out["reason"] = f"engine_not_installed: {app.get('reason', 'ComfyUI was not found')}"
        elif not adapter["present"]:
            out["reason"] = f"adapter_not_installed: {adapter.get('reason', 'GGUF plugin absent')}"
        else:
            out["reason"] = f"weights_missing: {weights.get('reason', 'weight files absent')}"
    return out


# --- config ---------------------------------------------------------------------------------------


def service_port() -> int:
    raw = (_video_cfg().get("service_port") or os.environ.get("LYGO_VIDEO_PORT")
           or DEFAULT_PORT)
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def service_base() -> str:
    return f"http://127.0.0.1:{service_port()}"


def render_timeout_s() -> float:
    try:
        return float(_video_cfg().get("render_timeout_s") or DEFAULT_RENDER_TIMEOUT_S)
    except (TypeError, ValueError):
        return float(DEFAULT_RENDER_TIMEOUT_S)


# --- HTTP, stdlib only ----------------------------------------------------------------------------


def _http(method: str, path: str, *, data: bytes | None = None,
          timeout: float = PROBE_TIMEOUT_S) -> tuple[int, bytes]:
    url = service_base() + path
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "lygo-video-harness")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), (exc.read() or b"")
    except Exception:  # noqa: BLE001 - a closed port is a state, not a traceback
        return 0, b""


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError:
        return {}


def probe(timeout: float | None = None) -> dict[str, Any]:
    """Is an engine answering, and what card is it holding? One GET, no side effects."""
    out: dict[str, Any] = {"up": False, "base": service_base(), "port": service_port(),
                           "version": "", "device": "", "vram_total_gb": 0.0,
                           "vram_free_gb": 0.0, "reason": ""}
    for path in HEALTH_PATHS:
        status, raw = _http("GET", path, timeout=timeout or PROBE_TIMEOUT_S)
        if status != 200:
            out["reason"] = f"{path} answered HTTP {status or 'no response'}"
            continue
        body = _json(raw)
        system = body.get("system") or {}
        devices = body.get("devices") or [{}]
        dev = devices[0] if devices else {}
        out.update({
            "up": True,
            "version": str(system.get("comfyui_version") or ""),
            "python": str(system.get("python_version") or ""),
            "torch": str(system.get("pytorch_version") or ""),
            "device": str(dev.get("name") or ""),
            "vram_total_gb": round(float(dev.get("vram_total") or 0) / 1e9, 2),
            "vram_free_gb": round(float(dev.get("vram_free") or 0) / 1e9, 2),
            "ram_free_gb": round(float(system.get("ram_free") or 0) / 1e9, 2),
            "reason": "",
        })
        return out
    return out


def object_info(timeout: float = 30.0) -> dict[str, Any]:
    status, raw = _http("GET", "/object_info", timeout=timeout)
    return _json(raw) if status == 200 else {}


def loader_available() -> dict[str, Any]:
    """Ask the RUNNING engine whether the GGUF loader class exists.

    Never decide this by grepping the binary or the plugin folder: the class only matters if the
    live server registered it, and a plugin that is present on disk can still fail to import.
    """
    oi = object_info()
    if not oi:
        return {"checked": False, "present": False,
                "reason": "the engine is not answering, so its node list could not be read"}
    return {"checked": True, "present": GGUF_LOADER in oi,
            "reason": "" if GGUF_LOADER in oi else f"the engine did not register {GGUF_LOADER}"}


def models() -> list[dict[str, Any]]:
    """Every declared recipe with its state, so the panel and the limb name the same ids."""
    live = loader_available()
    out = []
    for model_id in MODELS:
        state = model_state(model_id)
        state["loader_registered"] = live["present"] if live["checked"] else None
        out.append(state)
    return out


def resolve_model(model_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (recipe, None) or (None, refusal). Refusals name the exact thing that is missing."""
    if not model_id:
        model_id = next(iter(MODELS))
    if model_id not in MODELS:
        return None, {"ok": False, "error": "undeclared_model",
                      "why": f"{model_id!r} is not a declared model",
                      "declared": sorted(MODELS)}
    state = model_state(model_id)
    if not state["drivable"]:
        return None, {"ok": False, "error": "model_not_usable", "model": model_id,
                      "why": state.get("reason", "this model cannot be driven"),
                      "app_present": state["app_present"],
                      "adapter_present": state["adapter_present"],
                      "weights_present": state["weights_present"],
                      "missing": state["weights"]["missing"]}
    return MODELS[model_id], None


# --- service lifecycle -------------------------------------------------------------------------


def port_free(port: int | None = None) -> bool:
    import socket

    port = port or service_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def backend_argv(port: int | None = None) -> dict[str, Any]:
    """The argv that starts the engine. Low-VRAM is the 8 GB profile the harness was measured on."""
    py = comfy_python()
    root = app_root()
    port = port or service_port()
    if py is None or root is None:
        return {"ok": False, "error": "engine_not_installed",
                "why": (app_state().get("reason") or "no ComfyUI install is declared")}
    return {
        "ok": True,
        "cwd": str(root),
        "argv": [str(py), "-s", "main.py", "--listen", "127.0.0.1", "--port", str(port),
                 "--lowvram", "--disable-auto-launch"],
    }


def backend_env(port: int | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    # The engine writes into its own tree; keep its cache beside the weights rather than on C:.
    root = media_root()
    if root is not None:
        env.setdefault("HF_HOME", str(root.parent / ".hf"))
    return env


def launch_record() -> dict[str, Any]:
    path = launch_record_path()
    try:
        return json.loads(path.read_text(encoding="utf-8") or "{}")
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {}


def _write_launch_record(data: dict[str, Any]) -> None:
    path = launch_record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _alive(pid: Any) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True, timeout=15)
        return str(pid) in (out.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _tail(path: Path, lines: int = 12) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def launch(budget_s: int | None = None, wait: bool = True) -> dict[str, Any]:
    """Start the engine if nothing is answering, and prove it serves before returning.

    One engine at a time is a hardware rule on an 8 GB card, not a preference: an already-running
    engine is REUSED, never restarted out from under a render that is in flight.
    """
    already = probe()
    if already["up"]:
        return {"ok": True, "started": False, "reused": True, "why": "an engine is already serving",
                "probe": already}

    argv = backend_argv()
    if not argv["ok"]:
        return argv

    log = engine_log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log, "a", encoding="utf-8", buffering=1)
    handle.write(f"\n=== launch {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    try:
        proc = subprocess.Popen(argv["argv"], cwd=argv["cwd"], env=backend_env(),
                                stdout=handle, stderr=subprocess.STDOUT,
                                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                                               if os.name == "nt" else 0))
    except OSError as exc:
        handle.close()
        return {"ok": False, "error": "engine_launch_failed", "why": f"the engine would not start: {exc}"}

    _write_launch_record({"pid": proc.pid, "port": service_port(), "argv": argv["argv"],
                          "started": time.time(), "log": str(log), "owned": True})

    budget = int(budget_s or DEFAULT_BUDGET_S)
    if not wait:
        return {"ok": True, "started": True, "pid": proc.pid, "serving": False,
                "why": "started without waiting; the engine serves in about 155 s on this machine"}

    deadline = time.time() + budget
    while time.time() < deadline:
        if proc.poll() is not None:
            return {"ok": False, "error": "engine_died", "pid": proc.pid,
                    "why": f"the engine exited with code {proc.returncode}",
                    "log_tail": _tail(log)}
        state = probe()
        if state["up"]:
            return {"ok": True, "started": True, "serving": True, "pid": proc.pid,
                    "seconds": round(time.time() - (deadline - budget), 1), "probe": state}
        time.sleep(READY_POLL_S)
    return {"ok": False, "error": "engine_not_ready", "pid": proc.pid,
            "why": f"nothing answered {HEALTH_PATHS[0]} within {budget} s",
            "log_tail": _tail(log)}


def stop(force: bool = False) -> dict[str, Any]:
    """Stop ONLY the engine this harness started. An engine we did not start is left alone."""
    record = launch_record()
    pid = record.get("pid")
    if not record.get("owned") or not pid:
        return {"ok": False, "error": "not_ours",
                "why": "no engine started by this harness is on record; nothing was stopped"}
    if not _alive(pid):
        _write_launch_record({**record, "owned": False, "stopped": time.time()})
        return {"ok": True, "stopped": False, "why": "the engine this harness started is already gone"}
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"] if os.name == "nt"
                       else ["kill", "-9", str(pid)],
                       capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": "stop_failed", "why": f"the engine would not stop: {exc}"}
    gone = not _alive(pid)
    _write_launch_record({**record, "owned": False, "stopped": time.time()})
    return {"ok": True, "stopped": True, "gone": gone, "pid": pid,
            "why": "the engine this harness started is gone" if gone
                   else "the process is still alive - a forced stop may be needed"}


# --- the workflow (the recipe, turned into a graph) ------------------------------------------------


def _legal_length(recipe: dict[str, Any], length: int) -> int:
    """Wan's latent wants 4n+1. Snap to a legal value rather than sending one the engine refuses."""
    legal = recipe["limits"]["legal_lengths"]
    return min(legal, key=lambda n: abs(n - int(length)))


def build_workflow(model_id: str, *, prompt: str, negative: str = "", width: int = 0,
                   height: int = 0, length: int = 0, steps: int = 0, cfg: float = 0.0,
                   seed: int = 0, filename_prefix: str = "video/LYGO") -> dict[str, Any]:
    """The API-format graph for the text-to-video half of the official template.

    Faithful to the template the model's own authors ship, with exactly three deliberate
    departures, each named: the UNET loader is the GGUF class (the 8 GB path), the resolution and
    frame count come from the recipe's limits (8 GB), and the prompt/seed are the operator's.
    The template's bypassed image path is NOT wired - no start image leaves a text-to-video call.
    """
    recipe, refusal = resolve_model(model_id)
    if refusal:
        raise ValueError(refusal["why"])
    d, lim = recipe["defaults"], recipe["limits"]
    width = int(width or d["width"])
    height = int(height or d["height"])
    if width > lim["max_width"] or height > lim["max_height"]:
        raise ValueError(
            f"{width}x{height} exceeds this recipe's {lim['max_width']}x{lim['max_height']} ceiling "
            f"for an 8 GB card")
    length = _legal_length(recipe, length or d["length"])
    steps = int(steps or d["steps"])
    cfg = float(cfg or d["cfg"])
    if seed in (0, -1, None):
        seed = int(time.time() * 1000) % (2 ** 31)

    return {
        "37": {"class_type": GGUF_LOADER, "inputs": {"unet_name": recipe["diffusion"]},
               "_meta": {"title": "Load Diffusion Model (GGUF)"}},
        "38": {"class_type": "CLIPLoader",
               "inputs": {"clip_name": recipe["text_encoder"], "type": recipe["clip_type"],
                          "device": "default"},
               "_meta": {"title": "Load Text Encoder"}},
        "39": {"class_type": "VAELoader", "inputs": {"vae_name": recipe["vae"]},
               "_meta": {"title": "Load VAE"}},
        "48": {"class_type": "ModelSamplingSD3",
               "inputs": {"model": ["37", 0], "shift": d["shift"]}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["38", 0], "text": str(prompt or "")},
              "_meta": {"title": "Positive Prompt"}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["38", 0], "text": str(negative or "")},
              "_meta": {"title": "Negative Prompt"}},
        "55": {"class_type": "Wan22ImageToVideoLatent",
               "inputs": {"vae": ["39", 0], "width": width, "height": height,
                          "length": length, "batch_size": 1}},
        "3": {"class_type": "KSampler",
              "inputs": {"model": ["48", 0], "positive": ["6", 0], "negative": ["7", 0],
                         "latent_image": ["55", 0], "seed": seed, "steps": steps, "cfg": cfg,
                         "sampler_name": d["sampler_name"], "scheduler": d["scheduler"],
                         "denoise": d["denoise"]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["39", 0]}},
        "57": {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "fps": d["fps"]}},
        "58": {"class_type": "SaveVideo",
               "inputs": {"video": ["57", 0], "filename_prefix": filename_prefix,
                          "format": "auto", "codec": "auto"}},
    }


# --- rendering ------------------------------------------------------------------------------------


def _outputs_of(history: dict[str, Any]) -> list[dict[str, Any]]:
    """Every file reference in a history entry, whatever key the save node used.

    `SaveVideo` reports under `images` on this build and `videos` on others; a key-named reader is
    how a finished render gets reported as "no output".
    """
    found: list[dict[str, Any]] = []
    for _node, outs in (history.get("outputs") or {}).items():
        if not isinstance(outs, dict):
            continue
        for _key, value in outs.items():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and item.get("filename"):
                        found.append(item)
    return found


def _fetch(item: dict[str, Any], dest: Path) -> dict[str, Any]:
    qs = urllib.parse.urlencode({"filename": item["filename"],
                                 "subfolder": item.get("subfolder", ""),
                                 "type": item.get("type", "output")})
    status, raw = _http("GET", f"/view?{qs}", timeout=300)
    if status != 200 or not raw:
        return {"ok": False, "why": f"/view answered HTTP {status} for {item['filename']}"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    return {"ok": True, "path": str(dest), "bytes": len(raw)}


def render(model_id: str = "", *, prompt: str, negative: str = "", width: int = 0,
           height: int = 0, length: int = 0, steps: int = 0, cfg: float = 0.0, seed: int = 0,
           timeout_s: float | None = None, auto_start: bool = True) -> dict[str, Any]:
    """Render one clip and return the file that really landed on disk.

    The engine's own output path and the kit's copy are BOTH reported: a copied artifact and a
    freshly rendered one are different facts, and a later reader needs to tell them apart.
    """
    prompt = str(prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "prompt_required",
                "why": "there is nothing to render - describe the scene in `prompt`"}

    recipe, refusal = resolve_model(model_id)
    if refusal:
        return refusal
    model_id = model_id or next(iter(MODELS))

    if not probe()["up"]:
        if not auto_start:
            return {"ok": False, "error": "service_down",
                    "why": f"nothing is answering {service_base()}{HEALTH_PATHS[0]}"}
        started = launch()
        if not started.get("ok"):
            return {"ok": False, "error": started.get("error", "engine_not_started"),
                    "why": started.get("why", "the engine would not start"),
                    "log_tail": started.get("log_tail", "")}

    live = loader_available()
    if live["checked"] and not live["present"]:
        return {"ok": False, "error": "adapter_not_registered", "why": live["reason"]}

    try:
        graph = build_workflow(model_id, prompt=prompt, negative=negative, width=width,
                               height=height, length=length, steps=steps, cfg=cfg, seed=seed)
    except ValueError as exc:
        return {"ok": False, "error": "workflow_refused", "why": str(exc)}

    body = json.dumps({"prompt": graph, "client_id": "lygo-video-harness"}).encode("utf-8")
    status, raw = _http("POST", "/prompt", data=body, timeout=120)
    result = _json(raw)
    if status != 200 or "prompt_id" not in result:
        return {"ok": False, "error": "submit_failed",
                "why": f"/prompt answered HTTP {status}",
                "detail": json.dumps(result, indent=2)[:2000]}
    prompt_id = result["prompt_id"]

    deadline = time.time() + float(timeout_s or render_timeout_s())
    history: dict[str, Any] = {}
    while time.time() < deadline:
        if not _alive(launch_record().get("pid")) and not probe()["up"]:
            return {"ok": False, "error": "engine_died", "prompt_id": prompt_id,
                    "why": "the engine this render was using is gone", "log_tail": _tail(engine_log_path())}
        status, raw = _http("GET", f"/history/{prompt_id}", timeout=60)
        if status == 200:
            entry = _json(raw).get(prompt_id)
            if entry:
                history = entry
                break
        time.sleep(5)

    if not history:
        return {"ok": False, "error": "render_timeout", "prompt_id": prompt_id,
                "why": f"no result after {round(float(timeout_s or render_timeout_s()))} s"}

    state = history.get("status") or {}
    if state.get("status_str") != "success":
        detail = ""
        for message in state.get("messages") or []:
            if message and message[0] in {"execution_error", "execution_interrupted"}:
                detail = json.dumps(message[1], indent=2)[:2000]
        return {"ok": False, "error": "render_failed", "prompt_id": prompt_id,
                "why": "the engine reported the render did not finish", "detail": detail}

    items = _outputs_of(history)
    if not items:
        return {"ok": False, "error": "no_artifact", "prompt_id": prompt_id,
                "why": "the render reported success and named no file"}

    engine_dir = (app_root() or Path(".")) / "output" / "video"
    fetched = []
    for item in items:
        dest = workspace_dir() / item["filename"]
        got = _fetch(item, dest)
        if got.get("ok"):
            got["engine_path"] = str(engine_dir / item["filename"])
            got["name"] = item["filename"]
            fetched.append(got)
    if not fetched:
        return {"ok": False, "error": "artifact_not_fetchable", "prompt_id": prompt_id,
                "why": "the engine named a file that could not be read back",
                "detail": json.dumps(items)[:1000]}

    first = fetched[0]
    return {"ok": True, "model": model_id, "prompt_id": prompt_id, "prompt": prompt,
            "path": first["path"], "name": first["name"], "bytes": first["bytes"],
            "engine_path": first["engine_path"], "files": fetched,
            "seconds": round(time.time() - (deadline - float(timeout_s or render_timeout_s())), 1),
            "measured": recipe["measured"], "ratio": MEASURED_RATIO}


def cancel() -> dict[str, Any]:
    """Interrupt whatever is sampling. ComfyUI's own door; the process keeps running."""
    status, _ = _http("POST", "/interrupt", data=b"{}", timeout=15)
    return {"ok": status == 200, "why": "the engine was asked to stop sampling"
            if status == 200 else f"/interrupt answered HTTP {status}"}


# --- CLI ------------------------------------------------------------------------------------------


def _main(argv: list[str]) -> int:
    what = argv[0] if argv else "probe"
    if what == "probe":
        print(json.dumps({**probe(), "app": app_state(), "adapter": adapter_state(),
                          "loader_registered": loader_available()}, indent=2))
    elif what == "models":
        print(json.dumps(models(), indent=2))
    elif what == "launch":
        print(json.dumps(launch(), indent=2))
    elif what == "stop":
        print(json.dumps(stop(), indent=2))
    elif what == "render":
        prompt = ""
        for i, arg in enumerate(argv):
            if arg == "--prompt" and i + 1 < len(argv):
                prompt = argv[i + 1]
        model = ""
        for i, arg in enumerate(argv):
            if arg == "--model" and i + 1 < len(argv):
                model = argv[i + 1]
        out = render(model, prompt=prompt, auto_start=True)
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1
    else:
        print(f"usage: {sys.argv[0]} [probe|models|launch|stop|render --prompt TEXT [--model ID]]")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
