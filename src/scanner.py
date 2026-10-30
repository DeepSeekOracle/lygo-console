from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from gguf_header import parse_gguf_header
from cas_import import import_cas_tree
import model_fit

SKIP_DIR = {"windows", "$recycle.bin", "node_modules", ".git", "system volume information"}

# Folders that hold no model but do hold thousands of files. Skipped only by the wide discovery walk;
# an explicit root is always walked, because the operator naming a folder is an instruction.
NOISY_DIR = {
    "appdata",
    "program files",
    "program files (x86)",
    "programdata",
    "$windows.~bt",
    "$windows.~ws",
    "winsxs",
    "driverstore",
    "installer",
    "temp",
    "tmp",
    "__pycache__",
    ".venv",
    "venv",
    "site-packages",
}
DRIVE_FIXED = 3  # GetDriveType: DRIVE_FIXED



def _expand(root: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(root)))


def scan_roots(
    roots: list[str],
    wall_s: float = 25.0,
    *,
    annotate_fit: bool = False,
    skip_noisy: bool = False,
    vram_free_mib: int | None = None,
    ram_total: int | None = None,
    find_stores: bool = False,
) -> dict[str, Any]:
    """Walk every root for models. `wall_s` is the budget for **each** root, not for all of them.

    One shared budget let the first large root consume it while every later root reported nothing,
    which is how a scan across several drives accuses itself of being empty. Each root now gets its
    own wall clock, and `seconds` is the total spent for the caller to display.

    `find_stores` is for a scan the operator asked for: a walk then recognises a content-addressed
    store *inside* the tree it is walking (a `blobs/` folder beside `manifests/`, or blobs with the
    manifests gone) and reads the models out of it. Without that, a walk steps over the folder
    holding the operator's largest models and reports zero - which is exactly what happened to a
    190 GB store sitting two folders deep on D:.
    """
    t0 = time.time()
    models: list[dict[str, Any]] = []
    truncated = False
    seen: set[str] = set()
    fit_vram = int(vram_free_mib) if vram_free_mib is not None else 0
    fit_ram = int(ram_total) if ram_total is not None else 0

    def _finish(rec: dict[str, Any], dims: Any = None) -> dict[str, Any]:
        if annotate_fit:
            model_fit.annotate(rec, dims=dims, vram_free_mib=fit_vram, ram_total=fit_ram)
        return rec

    for raw in roots:
        root = _expand(raw)
        if not root.exists():
            continue
        # legacy CAS
        root_deadline = time.time() + max(1.0, float(wall_s))
        if (root / "manifests").is_dir() and (root / "blobs").is_dir():
            for rec in import_cas_tree(root):
                # Key on the NAME here, not the file. Several names can point at one weights blob (a
                # LYGO turbo variant is the same GGUF under another Modelfile name); dropping the
                # later ones in the scanner meant the registry never saw them and could not record
                # them as aliases, so they vanished from a list the operator asked to be complete.
                # File identity is the registry's job, and it keeps one record per file.
                key = "id:" + str(rec.get("id") or rec.get("path"))
                if key in seen:
                    continue
                seen.add(str(key))
                models.append(_finish(rec))
            continue
        if root.is_file() and root.suffix.lower() == ".gguf":
            h = parse_gguf_header(root)
            models.append(_finish(_from_header(h, root), h.get("found")))
            continue
        if not root.is_dir():
            continue
        skip = SKIP_DIR | NOISY_DIR if skip_noisy else SKIP_DIR
        for dirpath, dirnames, filenames in os.walk(root):
            if time.time() > root_deadline:
                truncated = True
                break
            dirnames[:] = [d for d in dirnames if d.lower() not in skip]
            base = Path(dirpath)
            if find_stores and (base / "blobs").is_dir():
                # A store this walk can name. Descending into `blobs/` would walk a hundred
                # extension-less files and still report nothing, so read it and move on.
                store = _store_records(base, seen)
                if store:
                    for rec in store:
                        models.append(_finish(rec))
                    dirnames[:] = []
                    continue
            if "config.json" in filenames and (
                "model.safetensors.index.json" in filenames
                or any(fn.endswith(".safetensors") for fn in filenames)
            ):
                from colibri import looks_like_colibri_model, model_card

                if looks_like_colibri_model(base):
                    models.append(_finish(model_card(base)))
                    dirnames[:] = []
                    continue
                models.append(
                    {
                        "id": base.name,
                        "path": str(base),
                        "kind": "archive",
                        "status": "UNSUPPORTED_FORMAT",
                        "reason": "safetensors_hf",
                        "runnable": False,
                        "source": "disk",
                    }
                )
                dirnames[:] = []
                continue
            for fn in filenames:
                if time.time() - t0 > wall_s:
                    truncated = True
                    break
                if not fn.lower().endswith(".gguf"):
                    continue
                p = base / fn
                key = str(p)
                if key in seen:
                    continue
                seen.add(key)
                h = parse_gguf_header(p)
                if (h.get("kind") or "") == "mmproj" and _model_beside(p):
                    # A projector is a sidecar, not a model: registering it as one put "gemma4-12b-mmproj"
                    # in the picker and left the model beside it looking blind.
                    continue
                models.append(_finish(_from_header(h, p), h.get("found")))
    return {"models": models, "scan_truncated": truncated, "seconds": round(time.time() - t0, 3)}


def _model_beside(projector: Path) -> Path | None:
    """The chat model a projector file belongs to, when it sits in the same folder."""
    stem = projector.stem
    names: list[str] = []
    if stem.lower().endswith("-mmproj"):
        names.append(stem[: -len("-mmproj")] + ".gguf")
    if stem.lower().startswith("mmproj-"):
        names.append(stem[len("mmproj-") :] + ".gguf")
    for name in names:
        cand = projector.parent / name
        if cand.is_file():
            return cand
    # A matching name is not the only shape. `mmproj-BF16.gguf` sits beside the model it belongs to
    # without sharing a stem, and a folder holding exactly one chat model and one projector has
    # exactly one answer - registering the projector as a model in its own right put a 0.18 GB
    # "Gemma-4-12B-It" in the picker that no operator can load.
    try:
        models = [
            p
            for p in sorted(projector.parent.glob("*.gguf"))
            if p.is_file() and "mmproj" not in p.name.lower() and p != projector
        ]
    except OSError:
        return None
    return models[0] if len(models) == 1 else None


def _projector_beside(model: Path) -> Path | None:
    """The projector that belongs to a model file, when it sits in the same folder."""
    stem = model.stem
    if stem.lower().endswith("-mmproj"):
        return None
    for name in (stem + "-mmproj.gguf", "mmproj-" + stem + ".gguf"):
        cand = model.parent / name
        if cand.is_file():
            return cand
    try:
        for cand in sorted(model.parent.glob("*mmproj*.gguf")):
            if cand.is_file() and stem.lower() in cand.name.lower():
                return cand
    except OSError:
        return None
    return None


def _from_header(h: dict[str, Any], path: Path) -> dict[str, Any]:
    try:
        nbytes = path.stat().st_size
    except OSError:
        nbytes = 0
    kind = h.get("kind") or "chat"
    beside = _projector_beside(path) if kind == "chat" else None
    return {
        "id": h.get("name") or path.stem,
        "path": str(path),
        "kind": kind,
        "ctx": h.get("ctx"),
        # A scan finds models; it does not decide how they run. Stamping 0 here made every scanned
        # model look CPU-pinned, and the launch path trusted that over the adaptive plan.
        "n_gpu_layers": None,
        "mmproj": str(beside) if beside else None,
        "source": "gguf",
        "bytes": nbytes,
        "runnable": bool(h.get("ok")) and h.get("status") == "ok",
        "status": h.get("status"),
        "meta_truncated": h.get("meta_truncated"),
        "architecture": h.get("architecture"),
    }


def _store_records(base: Path, seen: set[str], cap: int = 400) -> list[dict[str, Any]]:
    """The models inside a store a walk has just found. Read-only, always files.

    `blobs/` beside `manifests/` is the shape this kit calls a cas, and the manifests name every
    model in it. A store whose manifests are gone is still full of weights, so each blob's own GGUF
    header is read instead - a blob is a plain file and the engine loads it like any other.
    """
    out: list[dict[str, Any]] = []
    blobs = base / "blobs"
    if not blobs.is_dir():
        return out
    if (base / "manifests").is_dir():
        for rec in import_cas_tree(base):
            # By name, for the reason in scan_roots: the registry is what decides one-record-per-file.
            key = "id:" + str(rec.get("id") or rec.get("path") or "")
            if key in seen:
                continue
            seen.add(key)
            out.append(rec)
        return out
    try:
        entries = sorted(blobs.iterdir())
    except OSError:
        return out
    for blob in entries[:cap]:
        try:
            if not blob.is_file() or blob.stat().st_size < 1_048_576:
                continue
        except OSError:
            continue
        key = str(blob)
        if key in seen:
            continue
        h = parse_gguf_header(blob)
        if not h.get("ok") or (h.get("kind") or "") == "mmproj":
            continue
        seen.add(key)
        rec = _from_header(h, blob)
        rec["store"] = str(base)
        rec["unnamed_store"] = True
        out.append(rec)
    return out


SCAN_CACHE = "scan_cache.json"


def _cache_path() -> Path:
    try:
        import paths

        return Path(paths.SAVE) / SCAN_CACHE
    except Exception:
        return Path("save") / SCAN_CACHE


def _cache_read(roots: list[str], per_root_s: float, cache_s: float) -> dict[str, Any] | None:
    """A fresh scan of the same roots answers from disk instead of walking the drives again."""
    try:
        raw = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("roots") != list(roots) or float(raw.get("per_root_s") or 0.0) != float(per_root_s):
        return None
    age = time.time() - float(raw.get("ts") or 0.0)
    if age < 0 or age > float(cache_s):
        return None
    result = raw.get("result")
    if not isinstance(result, dict) or not result.get("models"):
        return None
    fresh = dict(result)
    fresh["cached"] = True
    fresh["age_s"] = round(age, 1)
    fresh["scan_n"] = len(fresh.get("models") or [])
    return fresh


def _cache_write(result: dict[str, Any], roots: list[str], per_root_s: float) -> None:
    try:
        from atomicio import atomic_write_text

        atomic_write_text(
            _cache_path(),
            json.dumps(
                {
                    "ts": time.time(),
                    "roots": list(roots),
                    "per_root_s": float(per_root_s),
                    "result": result,
                },
                indent=1,
                ensure_ascii=False,
            ),
        )
    except Exception:
        # A cache is an optimisation. A machine that cannot write one still scans.
        pass


def _scan_roots_parallel(
    roots: list[str],
    per_root_s: float,
    vram: int,
    ram: int,
) -> dict[str, Any]:
    """Walk the roots at once, one worker each, and keep the caller's order.

    The drives are independent: walking C: while D: waits made a four-drive scan the sum of four slow
    walks. `pool.map` yields in input order, so the model list stays deterministic.
    """
    if not roots:
        return {"models": [], "scan_truncated": False, "seconds": 0.0}
    t0 = time.time()
    models: list[dict[str, Any]] = []
    truncated = False

    def _one(root: str) -> dict[str, Any]:
        return scan_roots(
            [root],
            per_root_s,
            annotate_fit=True,
            skip_noisy=True,
            vram_free_mib=vram,
            ram_total=ram,
            find_stores=True,
        )

    with ThreadPoolExecutor(max_workers=max(1, min(len(roots), 8))) as pool:
        for res in pool.map(_one, roots):
            models.extend(res.get("models") or [])
            truncated = truncated or bool(res.get("scan_truncated"))
    return {"models": models, "scan_truncated": truncated, "seconds": round(time.time() - t0, 3)}


def fixed_drives() -> list[str]:
    """Local fixed drives, for a search that should not care which drive a model lives on.

    Removable media and network shares are excluded: a stick pulled mid-scan, or a share that stalls,
    would make the model list depend on who happens to be at the machine.
    """
    if os.name != "nt":
        return [os.path.abspath(os.sep)]
    out: list[str] = []
    try:
        import ctypes

        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if not (mask >> i) & 1:
                continue
            letter = f"{chr(ord('A') + i)}:\\"
            try:
                if ctypes.windll.kernel32.GetDriveTypeW(letter) == DRIVE_FIXED:
                    out.append(letter)
            except Exception:
                continue
    except Exception:
        return []
    return out


def discover(
    extra_roots: list[str] | None = None,
    *,
    per_root_s: float = 20.0,
    include_drives: bool = True,
    cache_s: float = 300.0,
) -> dict[str, Any]:
    """Every model this PC can show, wherever it lives, each with what this host would do with it.

    This is the console's own scanner: it looks for model files, not for another program's store, and
    it reports a model it cannot run beside one it can. "Available but too large" is a fact about
    this machine; dropping it would make the list lie by omission.

    `per_root_s` is spent **per root**, so a slow drive cannot starve the ones after it, and a root
    that ran out of budget is reported as truncated rather than as empty. The roots are walked at the
    same time (one worker each) and a repeat scan of the same roots inside `cache_s` answers from
    `save/scan_cache.json` with `cached: True` and `age_s`, so pressing the button twice is instant
    and the answer says how old it is instead of pretending to be new.
    """
    roots: list[str] = []
    for raw in list(extra_roots or []):
        if raw and str(raw) not in roots:
            roots.append(str(raw))
    declared = os.environ.get("LYGO_MODELS")
    if declared and declared not in roots:
        roots.append(declared)
    if include_drives:
        for drive in fixed_drives():
            if drive not in roots:
                roots.append(drive)

    vram = model_fit.vram_free_mib()
    ram = model_fit.ram_total_mib()
    if cache_s > 0:
        hit = _cache_read(roots, per_root_s, cache_s)
        if hit is not None:
            return hit
    result = _scan_roots_parallel(roots, per_root_s, vram, ram)
    result["roots"] = roots
    result["vram_free_mib"] = vram
    result["ram_total_mib"] = ram
    result["fit"] = model_fit.summary(result.get("models"))
    result["cached"] = False
    result["scan_n"] = len(result.get("models") or [])
    if cache_s > 0:
        _cache_write(result, roots, per_root_s)
    return result
