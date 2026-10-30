"""Move a model the kit already knows about into this machine's own LYGO model vault.

Standalone rule: the console boots its own llama.cpp on GGUF files it OWNS. An existing model that
lives in an Ollama blob folder is a plain file, so it can be imported once - read-only on the source -
and then the kit never needs that folder again.

    python scripts/import_to_vault.py --list
    python scripts/import_to_vault.py --models llama3.1:8b,qwen2.5-coder:14b
    python scripts/import_to_vault.py --models gemma4:12b --register

--register repoints save/registry.json at the vault copy (backed up first). Without it the vault
gets the files and the registry keeps pointing where it did, so the switch stays the steward's call.

Vault location: --vault, else $LYGO_MODELS, else <MODELS> if that drive exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
REGISTRY = KIT / "save" / "registry.json"


def sha256(path: Path, chunk: int = 1 << 22) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def vault_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    declared = os.environ.get("LYGO_MODELS", "").strip()
    if declared:
        return Path(declared)
    for drive in ("I:", "U:", "F:"):
        cand = Path(drive + "\\LYGO_MODELS")
        if cand.is_dir():
            return cand
    return KIT / "models"


def dest_name(model_id: str, kind: str) -> str:
    base = model_id.replace(":", "-").replace("/", "-")
    return base + ("-mmproj.gguf" if kind == "mmproj" else ".gguf")


def import_one(rec: dict, vault: Path, manifest: dict) -> dict:
    mid = str(rec.get("id"))
    entry = {"id": mid, "path": None, "mmproj": None, "bytes": 0, "sha256": None, "imported_from": str(rec.get("path") or "")}
    jobs = [(str(rec.get("path") or ""), "model")]
    if rec.get("mmproj"):
        jobs.append((str(rec["mmproj"]), "mmproj"))
    for src_s, kind in jobs:
        src = Path(src_s)
        if not src.is_file():
            print("   source gone: %s" % src_s)
            continue
        dst = vault / dest_name(mid, kind)
        if not (dst.is_file() and dst.stat().st_size == src.stat().st_size):
            t0 = time.time()
            shutil.copyfile(src, dst)
            print("   copied %-44s %6.2f GB  %5.1fs" % (dst.name, dst.stat().st_size / 1e9, time.time() - t0))
        else:
            print("   already present: %s" % dst.name)
        a, b = sha256(src), sha256(dst)
        if a != b:
            print("   MISMATCH on %s - not registering it" % dst.name)
            return {"id": mid, "ok": False, "why": "sha_mismatch"}
        if kind == "mmproj":
            entry["mmproj"] = str(dst)
            entry["mmproj_sha256"] = a
        else:
            entry["path"] = str(dst)
            entry["bytes"] = dst.stat().st_size
            entry["sha256"] = a
    entry["ok"] = bool(entry["path"])
    if entry["ok"]:
        manifest[mid] = entry
    return entry


def register(entry: dict, vault: Path) -> None:
    """Point the registry at the vault copy. Backs the registry up first; explicit fields only."""
    recs = json.loads(REGISTRY.read_text(encoding="utf-8"))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    shutil.copyfile(REGISTRY, REGISTRY.with_name("registry.json.bak-vault-" + stamp))
    hit = False
    for rec in recs.get("models") or []:
        if str(rec.get("id")) != str(entry["id"]):
            continue
        hit = True
        rec["path"] = entry["path"]
        rec["gguf"] = entry["path"]
        rec["source"] = "lygo_vault"
        rec["manifest"] = None
        if entry.get("sha256"):
            rec["sha256"] = entry["sha256"]
        if entry.get("bytes"):
            rec["bytes"] = entry["bytes"]
        if entry.get("mmproj"):
            rec["mmproj"] = entry["mmproj"]
    if not hit:
        print("   not in the registry, nothing to repoint")
        return
    tmp = REGISTRY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(recs, indent=1), encoding="utf-8")
    tmp.replace(REGISTRY)
    print("   registry repointed (backup: registry.json.bak-vault-%s)" % stamp)


def main() -> int:
    ap = argparse.ArgumentParser(description="Import models into the LYGO vault (read-only on the source).")
    ap.add_argument("--models", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--register", action="store_true")
    ap.add_argument("--vault", default=None)
    args = ap.parse_args()

    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    by_id = {str(r.get("id")): r for r in (reg.get("models") or [])}
    if args.list:
        for mid, rec in sorted(by_id.items()):
            p = str(rec.get("path") or "")
            owned = "vault" if p.lower().startswith(str(vault_path(args.vault)).lower()) else "outside"
            print("%-26s %-8s %s" % (mid, owned, p))
        return 0
    wanted = sorted(by_id) if args.all else [m.strip() for m in args.models.split(",") if m.strip()]
    if not wanted:
        print("nothing asked for: --models id,id | --all | --list")
        return 2

    vault = vault_path(args.vault)
    vault.mkdir(parents=True, exist_ok=True)
    manifest_p = vault / "manifest.json"
    manifest = json.loads(manifest_p.read_text(encoding="utf-8")) if manifest_p.is_file() else {}
    print("vault: %s" % vault)
    failed = []
    for mid in wanted:
        rec = by_id.get(mid)
        if not rec:
            print("== %s: not in the registry" % mid)
            failed.append(mid)
            continue
        print("== %s" % mid)
        entry = import_one(rec, vault, manifest)
        if not entry.get("ok"):
            failed.append(mid)
        elif args.register:
            register(entry, vault)
    manifest_p.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    total = sum(p.stat().st_size for p in vault.glob("*.gguf")) / 1e9
    print()
    print("vault: %s  (%.2f GB, %d models)" % (vault, total, len(manifest)))
    if failed:
        print("failed: %s" % ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
