"""Photo to a GLB, on this machine's GPU.

The public site cannot hold the model. This limb runs TripoSR (MIT, Stability AI
and Tripo AI) from a local checkout when that checkout and its virtualenv exist.
The panel credits Modly by Lightning Pixel. Modly's source is not included.
The photo is read from the workspace or a mapped read root and is not uploaded.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from paths import KIT_ROOT, WORKSPACE, console_cfg

MESH_EXTS = {".glb", ".gltf", ".obj"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_LOCK = threading.Lock()
_DEFAULT_HOME = Path("<MODELS>/TripoSR")


def _cfg() -> dict[str, Any]:
    try:
        return console_cfg() or {}
    except Exception:  # noqa: BLE001
        return {}


def mesh_home() -> Path:
    raw = str(_cfg().get("mesh_home") or "").strip()
    return Path(raw) if raw else _DEFAULT_HOME


def mesh_python() -> Path | None:
    explicit = str(_cfg().get("mesh_python") or "").strip()
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    cand = mesh_home() / ".venv" / "Scripts" / "python.exe"
    return cand if cand.is_file() else None


def _runner() -> Path:
    return KIT_ROOT / "tools" / "mesh_triposr.py"


def _shim() -> Path:
    return KIT_ROOT / "tools" / "mesh_shims"


def mesh_dir() -> Path:
    d = WORKSPACE / "meshes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def workspace_mesh_path(name: str) -> Path | None:
    """A mesh already written under workspace/meshes. Basename only."""
    base = Path(str(name or "").replace("\\", "/").split("/")[-1]).name
    if not base or base in {".", ".."}:
        return None
    if Path(base).suffix.lower() not in MESH_EXTS:
        return None
    root = mesh_dir().resolve()
    p = (root / base).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        return None
    return p if p.is_file() else None


def _stamp(ext: str) -> str:
    return "mesh-" + time.strftime("%Y%m%d-%H%M%S") + ext


def _resolution(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 192
    return max(64, min(256, n))


def _foreground(value: Any) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = 0.85
    return max(0.5, min(1.0, n))


def _ratio(value: Any) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = 0.5
    return max(0.15, min(1.0, n))


def mesh_status() -> dict[str, Any]:
    """Fast report. Does not import torch and does not download weights."""
    home = mesh_home()
    py = mesh_python()
    tsr = (home / "tsr" / "system.py").is_file()
    ready = py is not None and tsr and _runner().is_file()
    hint = "Drop a photo. The first mesh also downloads the TripoSR weights onto this PC."
    if not tsr:
        hint = "TripoSR is not checked out at " + str(home)
    elif py is None:
        hint = "The TripoSR virtualenv is missing under " + str(home / ".venv")
    return {
        "ok": True,
        "ready": ready,
        "engine": "TripoSR",
        "license": "MIT",
        "source": "https://github.com/VAST-AI-Research/TripoSR",
        "weights": "stabilityai/TripoSR",
        "credit": "Based on Modly by Lightning Pixel. This kit does not include Modly's source.",
        "modly": "https://github.com/lightningpixel/modly",
        "modly_author": "https://github.com/lightningpixel",
        "home": str(home),
        "python": str(py or ""),
        "hint": hint,
    }


def _allowed_image(path: str) -> tuple[Path | None, str]:
    from image_tools import _allowed_read
    from paths import under_workspace

    raw = str(path or "").strip()
    if not raw:
        return None, "empty"
    p, why = _allowed_read(under_workspace(raw))
    if why:
        return p, why
    if not p.is_file():
        return p, "missing"
    if p.suffix.lower() not in IMAGE_EXTS:
        return p, "not_image"
    if p.stat().st_size > 20 * 1024 * 1024:
        return p, "too_large"
    return p, ""


def _run(args: list[str], timeout: int) -> dict[str, Any]:
    py = mesh_python()
    if py is None or not _runner().is_file():
        return {"ok": False, "error": "not_installed", "status": mesh_status()}
    env = dict(**os_environ())
    env["LYGO_MESH_HOME"] = str(mesh_home())
    env["LYGO_MESH_SHIM"] = str(_shim())
    env["PYTHONUNBUFFERED"] = "1"
    try:
        proc = subprocess.run(
            [str(py), str(_runner()), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(mesh_home() if mesh_home().is_dir() else KIT_ROOT),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "timeout": timeout}
    line = ""
    for row in (proc.stdout or "").splitlines():
        if row.strip().startswith("{"):
            line = row.strip()
    if not line:
        tail = ((proc.stderr or "") + "\n" + (proc.stdout or ""))[-700:]
        return {"ok": False, "error": "no_result", "code": proc.returncode, "detail": tail}
    try:
        got = json.loads(line)
    except json.JSONDecodeError:
        return {"ok": False, "error": "bad_result", "detail": line[:400]}
    if not isinstance(got, dict):
        return {"ok": False, "error": "bad_result"}
    if proc.returncode != 0 and got.get("ok") is not False:
        got["ok"] = False
        got["code"] = proc.returncode
    return got


def os_environ() -> dict[str, str]:
    import os
    return dict(os.environ)


def mesh_from_image(
    path: str,
    resolution: Any = None,
    remove_bg: Any = True,
    timeout: Any = None,
    foreground: Any = None,
) -> dict[str, Any]:
    image, why = _allowed_image(path)
    if why:
        return {"ok": False, "error": why, "path": str(image or path)}
    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "error": "busy", "hint": "a mesh is already being built"}
    try:
        name = _stamp(".glb")
        dest = mesh_dir() / name
        try:
            limit = int(timeout) if timeout else 1200
        except (TypeError, ValueError):
            limit = 1200
        limit = max(60, min(1800, limit))
        cut = "1" if remove_bg is not False and str(remove_bg).lower() not in {"0", "false", "no"} else "0"
        got = _run(
            [
                "generate",
                str(image),
                str(dest),
                str(_resolution(resolution)),
                cut,
                "%.2f" % _foreground(foreground),
            ],
            limit,
        )
        if not got.get("ok"):
            return got
        if not dest.is_file() or dest.stat().st_size < 32:
            return {"ok": False, "error": "empty_mesh", "detail": str(got)[:400]}
        got["name"] = name
        got["path"] = str(dest)
        got["url"] = "/api/media/mesh/" + name
        got["source_image"] = str(image)
        return got
    finally:
        _LOCK.release()


def mesh_decimate(name: str, ratio: Any = None, timeout: Any = None) -> dict[str, Any]:
    src = workspace_mesh_path(name)
    if src is None:
        return {"ok": False, "error": "missing", "name": str(name or "")}
    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "error": "busy"}
    try:
        out_name = _stamp(".glb")
        dest = mesh_dir() / out_name
        try:
            limit = int(timeout) if timeout else 180
        except (TypeError, ValueError):
            limit = 180
        got = _run(["decimate", str(src), str(dest), str(_ratio(ratio))], max(30, min(600, limit)))
        if not got.get("ok"):
            return got
        got["name"] = out_name
        got["path"] = str(dest)
        got["url"] = "/api/media/mesh/" + out_name
        got["from"] = src.name
        return got
    finally:
        _LOCK.release()
