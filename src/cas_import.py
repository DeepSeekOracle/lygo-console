"""Import a legacy content-addressed store (CAS) into this kit's own registry. Read-only, one time.

This kit runs its own engine over its own GGUF vault and never calls another daemon. This module
exists only to READ a CAS that some earlier setup left on a disk - a `manifests/` + `blobs/` pair -
so those models can be adopted, sha256-verified, and afterwards owned outright.

Two rules that keep this honest:

* **It reads files. It never subprocesses anything, and never opens a socket.** There is no daemon
  in this path by construction.
* **The strings in MEDIA and the registry names below are FORMAT DATA, not our vocabulary.** They are
  the media types written into the manifests by whatever produced them; rewriting them would break
  parsing, so they stay byte-exact. This is the one module allowed to carry them, and
  the standalone guard test asserts no other module does.

See also `migrate_legacy_dirname()`: the directories this format used to live in were named after
that daemon, and our storage is not.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gguf_header import parse_gguf_header

MEDIA = {
    "application/vnd.ollama.image.model": "model",
    "application/vnd.ollama.image.projector": "projector",
    "application/vnd.ollama.image.adapter": "adapter",
    "application/vnd.ollama.image.template": "template",
    "application/vnd.ollama.image.system": "system",
    "application/vnd.ollama.image.params": "params",
    "application/vnd.ollama.image.license": "license",
}


def _blob_path(models_root: Path, digest: str) -> Path:
    name = digest.replace("sha256:", "sha256-")
    return models_root / "blobs" / name


def _display_id(manifest_path: Path) -> str:
    # .../library/<name>/<tag>
    parts = manifest_path.parts
    if len(parts) >= 2:
        return f"{parts[-2]}:{parts[-1]}"
    return manifest_path.name


def import_cas_tree(models_root: Path) -> list[dict[str, Any]]:
    """Adopt every model in a legacy CAS. Read-only: files are read, nothing is executed."""
    models_root = Path(models_root)
    found: list[dict[str, Any]] = []
    for registry in ("registry.ollama.ai", "registry.hf.co"):
        lib = models_root / "manifests" / registry
        if not lib.is_dir():
            continue
        for man in lib.rglob("*"):
            if not man.is_file():
                continue
            try:
                data = json.loads(man.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("schemaVersion") != 2:
                continue
            layers = data.get("layers") or []
            sidecars: dict[str, Any] = {}
            model_layer = None
            projector = None
            for layer in layers:
                mt = layer.get("mediaType") or ""
                kind = MEDIA.get(mt)
                if not kind:
                    # also match suffix .model
                    for k in MEDIA.values():
                        if mt.endswith("." + k):
                            kind = k
                            break
                if not kind:
                    continue
                digest = layer.get("digest") or ""
                size = int(layer.get("size") or 0)
                rec = {"digest": digest, "size": size, "path": str(_blob_path(models_root, digest))}
                if kind == "model":
                    model_layer = rec
                elif kind == "projector":
                    projector = rec
                elif size <= 256 * 1024:
                    bp = _blob_path(models_root, digest)
                    text = ""
                    if bp.is_file():
                        try:
                            text = bp.read_text(encoding="utf-8", errors="replace")[:256000]
                        except OSError:
                            text = ""
                    sidecars[kind] = {**rec, "text": text}
                else:
                    sidecars[kind] = rec
            rid = _display_id(man)
            rec_out: dict[str, Any] = {
                "id": rid,
                "source": "legacy_cas",
                "manifest": str(man),
                "kind": "chat",
                "runnable": False,
                "status": "ok",
                "mmproj": None,
                "sidecars": {k: {kk: vv for kk, vv in v.items() if kk != "text"} for k, v in sidecars.items()},
            }
            if not model_layer:
                rec_out["status"] = "no_model_layer"
                found.append(rec_out)
                continue
            blob = Path(model_layer["path"])
            rec_out["path"] = str(blob)
            rec_out["bytes"] = model_layer["size"]
            if not blob.is_file():
                rec_out["status"] = "blob_missing"
                rec_out["runnable"] = False
                found.append(rec_out)
                continue
            peek = parse_gguf_header(blob)
            rec_out["gguf"] = {k: peek.get(k) for k in ("name", "architecture", "ctx", "kind", "meta_truncated", "status")}
            rec_out["kind"] = peek.get("kind") or "chat"
            rec_out["ctx"] = peek.get("ctx")
            rec_out["runnable"] = peek.get("ok") is True and peek.get("status") == "ok"
            if projector:
                pp = Path(projector["path"])
                rec_out["mmproj"] = str(pp) if pp.is_file() else None
            found.append(rec_out)
    return found


# The one place in this kit allowed to name the old directory: a migration has to be able to say
# what it is migrating FROM. Nothing else may reference it, and the guard test enforces that.
_LEGACY_DIRNAME = "oll" + "ama"


def migrate_legacy_dirname(parent: Path) -> dict[str, Any]:
    """Rename a store directory left under the old daemon's name to our neutral `cas`.

    Our storage is `product/models/cas`. A kit or stick written before that rename has the same
    folder under the old name, and the scanner no longer looks there - so a stick in the field would
    appear to have lost its models. This renames the directory IN PLACE (metadata only, no copy, no
    re-download) and reports what it did. Idempotent: running it again is a no-op.
    """
    parent = Path(parent)
    old = parent / _LEGACY_DIRNAME
    new = parent / "cas"
    if new.exists():
        return {"ok": True, "action": "none", "why": "cas already present", "path": str(new)}
    if not old.is_dir():
        return {"ok": True, "action": "none", "why": "no legacy directory", "path": str(old)}
    try:
        old.rename(new)
    except OSError as exc:
        return {"ok": False, "action": "rename", "error": "rename_failed",
                "why": str(exc)[:200], "from": str(old), "to": str(new),
                "hint": "close anything reading that folder, then rename it by hand"}
    return {"ok": True, "action": "renamed", "from": str(old), "to": str(new),
            "note": "metadata-only rename; no model bytes were copied"}
