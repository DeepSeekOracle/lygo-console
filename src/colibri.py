"""Optional Colibri engine (JustVugg/colibri). MoE experts stream from SSD. Not llama.cpp."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from engine import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    Runner,
    _assign_job,
    _clean_env,
    _health,
    _runners,
    _write_pids,
    binary_forbidden,
    stop_port,
)
from paths import ENGINE_DIR, KIT_ROOT, LOGS, ensure_dirs

COLIBRI_HOME = ENGINE_DIR / "colibri"
SOURCE = "https://github.com/JustVugg/colibri"


def resolve_coli() -> Path | None:
    env = os.environ.get("LYGO_COLI", "").strip()
    cands = []
    if env:
        cands.append(Path(env))
    cands.extend(
        [
            COLIBRI_HOME / "coli.cmd",
            COLIBRI_HOME / "coli.exe",
            COLIBRI_HOME / "coli",
            ENGINE_DIR / "coli.cmd",
            ENGINE_DIR / "coli.exe",
        ]
    )
    w = shutil.which("coli.cmd") or shutil.which("coli")
    if w:
        cands.append(Path(w))
    for c in cands:
        if c.is_file() and not binary_forbidden(c):
            return c
    return None


def looks_like_colibri_model(path: Path) -> bool:
    if not path.is_dir():
        return False
    cfg = path / "config.json"
    if not cfg.is_file():
        return False
    names = {p.name.lower() for p in path.iterdir() if p.is_file()}
    if "model.safetensors.index.json" in names:
        return True
    if any(n.endswith(".safetensors") for n in names):
        return True
    # Official DeepSeek/Kimi shards often sit in subdirs; config.json is enough with a weight file nearby.
    try:
        obj = json.loads(cfg.read_text(encoding="utf-8", errors="replace")[:80_000])
    except Exception:
        obj = {}
    arch = " ".join(str(x) for x in (obj.get("architectures") or [])).lower()
    mt = str(obj.get("model_type") or obj.get("architectures") or "").lower()
    blob = arch + " " + mt + " " + str(obj.get("model_type") or "")
    keys = ("deepseek", "kimi", "glm", "qwen", "olmoe", "inkling", "moe")
    return any(k in blob for k in keys)


def model_card(path: Path) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    p = path / "config.json"
    if p.is_file():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8", errors="replace")[:80_000])
        except Exception:
            cfg = {}
    arch = cfg.get("model_type") or (cfg.get("architectures") or ["unknown"])[0]
    coli = resolve_coli()
    return {
        "id": "coli-" + path.name,
        "path": str(path),
        "kind": "colibri",
        "engine": "colibri",
        "architecture": str(arch),
        "source": "colibri",
        "runnable": bool(coli),
        "status": "ok" if coli else "NEED_COLIBRI",
        "reason": None if coli else "put coli.cmd in engine/colibri from github.com/JustVugg/colibri/releases",
        "bytes": 0,
        "ctx": int(cfg.get("max_position_embeddings") or cfg.get("seq_length") or 4096),
        "n_gpu_layers": 0,
        "mmproj": None,
        "note": "MoE streamed from disk (VRAM+RAM+SSD). Slow on modest PCs. Not GGUF.",
    }


def spawn_colibri(*, port: int, model_dir: Path, alias: str, api_key: str, extra_env: dict[str, str] | None = None) -> Runner:
    exe = resolve_coli()
    if exe is None:
        raise FileNotFoundError("coli launcher missing — engine/colibri from JustVugg/colibri releases")
    model_dir = Path(model_dir)
    if not looks_like_colibri_model(model_dir):
        raise FileNotFoundError(f"not a Colibri model dir: {model_dir}")
    ensure_dirs()
    LOGS.mkdir(parents=True, exist_ok=True)
    log_f = open(LOGS / f"colibri-{port}.log", "ab", buffering=0)
    env = _clean_env()
    env["COLI_MODEL"] = str(model_dir)
    if api_key:
        env["COLI_API_KEY"] = api_key
    if extra_env:
        for k, v in extra_env.items():
            if v is not None:
                env[str(k)] = str(v)
    argv = [str(exe), "serve", "--host", "127.0.0.1", "--port", str(port), "--model", str(model_dir), "--model-id", alias or model_dir.name]
    if exe.suffix.lower() in {".cmd", ".bat"}:
        argv = ["cmd", "/c"] + argv
    stop_port(port)
    proc = subprocess.Popen(
        argv,
        cwd=str(exe.parent),
        env=env,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
    )
    job = _assign_job(proc.pid)
    runner = Runner(
        port=port,
        gguf=str(model_dir),
        kind="colibri",
        proc=proc,
        api_key=api_key,
        job=job,
        alias=alias,
        log_path=str(log_f.name),
    )
    _runners[port] = runner
    _write_pids()
    deadline = time.time() + 600
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"colibri exited {proc.returncode} — see {log_f.name}")
        if _health(port, api_key):
            return runner
        time.sleep(3)
    stop_port(port)
    raise TimeoutError("colibri_health_timeout")


def status() -> dict[str, Any]:
    return {
        "ok": True,
        "engine": "colibri",
        "source": SOURCE,
        "launcher": str(resolve_coli()) if resolve_coli() else None,
        "home": str(COLIBRI_HOME),
        "kit": str(KIT_ROOT),
    }
