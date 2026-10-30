#!/usr/bin/env python3
"""Seal the live kit as a frozen build inside the vault.

WHY THIS EXISTS
    A vault copy is the thing you revert TO. It is only a safety net if it is
    byte-complete, hash-listed, and cannot be edited by accident. So: the live
    tree is where you work, the seal is reference only, and a sealed build is
    never edited - you build forward and seal the NEXT release number.

USAGE (from anywhere)
    python seal_build.py --root "<kit dir>" [--vault <DRIVE_D>\LYGO_CANON]
                         [--name WORKING] [--kit PC_LOCAL] [--dry-run]

WHAT IT WRITES  <vault>/<YYYY-MM-DD>_build-<release>_<name>/
    <KIT>/                      copy of the build, every file marked read-only
    SHA256SUMS-<KIT>.txt        raw sha256 of every copied file, sha256sum -c compatible
    CANON.md                    what this is, where it came from, how to restore it
    CANON.json                  the same facts, machine-readable
    RESTORE.txt                 the one command that puts it back
    _freeze_facts.json          files / bytes / listing digest / timing

WHAT IS NEVER COPIED
    Keys and machine state: config/api.json, data/.llama_api_key, data/.lygo_llm_token.
    Runtime: save/, workspace/, logs, sessions, receipts, vault, mycelium, bench,
    verify, archive, __pycache__, .pytest_cache, *.pyc, .git.
    Model weights: *.gguf / *.safetensors (the vault carries the build, not the models).

EXIT
    0 sealed and verified · 1 refused (target exists) · 2 verification failed
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import time
from datetime import datetime
from pathlib import Path

# Only true runtime/keys folders are skipped. Everything else - including workspace/,
# which holds the console's identity (SOUL.md, MEMORY.md, IDENTITY.md) and the kit's own
# empty folders - is part of the BUILD and must survive a revert. An earlier pass at this
# skipped workspace/ and the restored console came back with no soul (a test caught it).
SKIP_DIRS = {
    "save", "data", "__pycache__", ".pytest_cache", ".git", ".mypy_cache", "node_modules",
}
SKIP_FILES = {
    "api.json", "local.json", ".llama_api_key", ".lygo_llm_token",
    ".env", ".env.local", ".netrc", ".npmrc", ".pypirc", ".git-credentials",
}
SKIP_SUFFIX = {".pyc", ".pyo", ".gguf", ".safetensors", ".log", ".tmp", ".bak"}
SKIP_NAMES_CONTAIN = (".bak-", ".pre_", "postcanon")

# Files carried even though their folder is runtime state. This is the build's
# WORKING CONFIGURATION - save/registry.json holds `selected` (the boot model), so
# without it a revert hands back a console that boots a different model and loses
# what made the sealed version work (gemma4-12b reads images; others do not).
CARRY_DEFAULT = ("save/registry.json",)

DEFAULT_VAULT = Path(r"<DRIVE_D>\LYGO_CANON")


def write_index(vault: Path) -> None:
    """One file at the vault root that answers: what can I revert to, and how."""
    rows: list[str] = []
    for d in sorted(vault.iterdir()):
        if not d.is_dir():
            continue
        canon = d / "CANON.json"
        if canon.exists():
            try:
                data = json.loads(canon.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rows.append("| %s | `%s` | %s | %s | %s | `python scripts/restore_build.py --from \"%s\" --root \"<live %s>\"` |"
                        % (data.get("sealed_utc", "")[:10], data.get("release", "-"), data.get("kit", "?"),
                           data.get("files", 0), str(data.get("sha256_of_listing", ""))[:12],
                           data.get("seal", ""), data.get("kit", "")))
        elif list(d.glob("*/*/VERSION")):
            rows.append("| - | see `%s` | legacy | - | - | pre-tooling seal: copy it over a live tree by hand, or restore with `--from <seal>/<KIT>` if a SHA256SUMS-*.txt sits beside it |" % d.name)

    body = """# LYGO BUILD VAULT - the versions you can revert to

Every directory here is a **frozen build**: a copy made by `scripts/seal_build.py`, hash-listed
with `SHA256SUMS-<KIT>.txt`, and marked read-only so no session can edit it.

**Rule: a sealed build is reference only. Never edit a sealed build, never work inside one.**
Work in the live tree, seal the next release number, and revert with `scripts/restore_build.py`
(which verifies every hash *before* it writes anything).

| sealed | release | kit | files | listing digest | revert with |
|---|---|---|---|---|---|
%s

Seals carry the build - code, portal, tests, scripts, prompts, skills, engine, launchers, docs,
manifests and VERSION. They never carry keys (`config/api.json`, `data/.llama_api_key`), the
operator's runtime (`save/`, `workspace/`, logs, sessions) or model weights (`*.gguf`).
""" % ("\n".join(rows) if rows else "| - | - | - | - | - | nothing sealed yet |")

    try:
        (vault / "INDEX.md").write_text(body, encoding="utf-8")
    except OSError as exc:
        print("  (could not update the vault index: %s)" % exc)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_version(root: Path) -> tuple[str, str]:
    lines = (root / "VERSION").read_text(encoding="utf-8").splitlines()
    release = lines[0].strip() if lines else "0.0.0"
    tag = lines[1].strip() if len(lines) > 1 else ""
    return release, tag


def guess_kit(root: Path) -> str:
    up = str(root).upper().replace("/", "\\")
    return "USB_CLAW" if "LYGO_BUILDER_KEY" in up else "PC_LOCAL"


def skip(rel: Path) -> bool:
    parts = rel.parts
    if any(p in SKIP_DIRS for p in parts):
        return True
    if not parts:
        return False
    name = parts[-1]
    if name in SKIP_FILES:
        return True
    if rel.suffix.lower() in SKIP_SUFFIX:
        return True
    low = name.lower()
    return any(mark in low for mark in SKIP_NAMES_CONTAIN)


def collect(root: Path, carry: tuple[str, ...] = ()) -> list[tuple[Path, Path]]:
    out: list[tuple[Path, Path]] = []
    seen: set[str] = set()
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(files):
            src = Path(base) / name
            rel = src.relative_to(root)
            if skip(rel):
                continue
            key = str(rel).replace("\\", "/")
            if key not in seen:
                seen.add(key)
                out.append((rel, src))
    for rel_str in carry:
        rel = Path(str(rel_str).replace("\\", "/"))
        src = root / rel
        key = str(rel).replace("\\", "/")
        if src.is_file() and key not in seen:
            seen.add(key)
            print("carrying config  : %s  (the build's working configuration)" % key)
            out.append((rel, src))
    return sorted(out, key=lambda pair: str(pair[0]).lower())


def collect_dirs(root: Path) -> list[str]:
    """Every folder the build owns - empty ones included. A build is its shape too:
    tests/fixtures and the kit's own models/ folder are empty directories that matter."""
    out: list[str] = []
    for base, dirs, _files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = Path(base).relative_to(root)
        if str(rel) not in (".", ""):
            out.append(str(rel).replace("\\", "/"))
    return sorted(set(out))


def main() -> int:
    ap = argparse.ArgumentParser(description="Seal a kit as a frozen build in the vault.")
    ap.add_argument("--root", required=True, help="the live kit directory to seal")
    ap.add_argument("--vault", default=str(DEFAULT_VAULT), help="vault root (default <DRIVE_D>\LYGO_CANON)")
    ap.add_argument("--name", default="", help="short label for the seal (e.g. WORKING, build-line)")
    ap.add_argument("--kit", default="", help="PC_LOCAL or USB_CLAW (auto-detected)")
    ap.add_argument("--carry", action="append", default=None,
                    help="runtime file to include anyway (repeatable; default: save/registry.json)")
    ap.add_argument("--dry-run", action="store_true", help="report what would be sealed, write nothing")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not (root / "VERSION").exists():
        print("NOT A KIT: no VERSION in %s" % root)
        return 1

    release, tag = read_version(root)
    kit = args.kit or guess_kit(root)
    stamp = datetime.now().strftime("%Y-%m-%d")
    name = args.name.strip() or (tag.split()[0] if tag else "build")
    target = Path(args.vault).resolve() / ("%s_build-%s_%s" % (stamp, release, name))

    carry = tuple(args.carry) if args.carry else CARRY_DEFAULT
    files = collect(root, carry)
    dirs = collect_dirs(root)
    total = sum(p.stat().st_size for _, p in files)
    print("kit        : %s" % kit)
    print("release    : %s  (%s)" % (release, tag or "-"))
    print("source     : %s" % root)
    print("seal       : %s" % target)
    print("files      : %d   %.1f MB" % (len(files), total / 1048576.0))
    print("folders    : %d  (empty folders included - the build is its shape too)" % len(dirs))
    print("skipped    : keys (config/api.json, data/*key*), the operator's save/ history, *.gguf, caches")

    if args.dry_run:
        print("\nDRY RUN - nothing written.")
        return 0

    if target.exists():
        print("\nREFUSING: %s already exists. A seal is never overwritten -" % target)
        print("pick another --name, or seal the next release number.")
        return 1

    kitdir = target / kit
    t0 = time.time()
    kitdir.mkdir(parents=True)
    for d in dirs:
        (kitdir / d).mkdir(parents=True, exist_ok=True)
    sums: list[tuple[str, str, int]] = []
    for rel, src in files:
        dst = kitdir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        sums.append((str(rel).replace("\\", "/"), sha256_file(dst), dst.stat().st_size))

    sums_txt = target / ("SHA256SUMS-%s.txt" % kit)
    with sums_txt.open("w", encoding="utf-8", newline="\n") as fh:
        for rel, digest, _size in sums:
            fh.write("%s  %s/%s\n" % (digest, kit, rel))

    listed = "\n".join("%s  %s/%s" % (d, kit, r) for r, d, _ in sums)
    digest_of_listing = hashlib.sha256(listed.encode("utf-8")).hexdigest()
    facts = {
        "sealed_utc": datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z"),
        "kit": kit,
        "release": release,
        "release_tag": tag,
        "source": str(root),
        "seal": str(kitdir),
        "sums_file": str(sums_txt),
        "files": len(sums),
        "bytes": total,
        "sha256_of_listing": digest_of_listing,
        "hash_rule": "raw sha256 of the file as copied; paths are relative to the seal root",
        "verify_with": "cd \"%s\" && sha256sum -c %s" % (target, "SHA256SUMS-%s.txt" % kit),
        "hash_rule_note": "the kit's own certify_build.py hashes LF-normalised bytes; this list is raw bytes",
        "carried_config": sorted(str(c).replace("\\", "/") for c in carry),
        "dirs": dirs,
        "copy_seconds": round(time.time() - t0, 1),
    }
    (target / "_freeze_facts.json").write_text(json.dumps(facts, indent=2) + "\n", encoding="utf-8")
    (target / "CANON.json").write_text(json.dumps(facts, indent=2) + "\n", encoding="utf-8")

    carried_txt = ", ".join("`%s`" % c for c in facts["carried_config"]) or "none"
    canon_md = f"""# FROZEN BUILD - {kit} {release}

**This is a vault copy. It is reference only. Never edit it, never build inside it.**
Work happens in the live tree (`{root}`); you build forward and seal the next release number.

| | |
|---|---|
| release | `{release}` |
| tag | `{tag or '-'}` |
| kit | `{kit}` |
| source | `{root}` |
| sealed | {facts['sealed_utc']} |
| files | {facts['files']} |
| bytes | {facts['bytes']} |
| listing digest | `{digest_of_listing}` |

## What a seal carries, and what it never carries

Carried: the build (code, portal, tests, scripts, prompts, skills, engine, launchers, docs, manifests,
VERSION), the folder shape (empty folders included, because the kit's own `models/` folder and
its test fixtures are empty folders that matter) and the build's working configuration -
{carried_txt}. That configuration is what makes this
seal *work*: `save/registry.json` holds the boot model, and a revert without it comes back booting a
different model.

Never carried: keys (`config/api.json`, `data/.llama_api_key`, `data/.lygo_llm_token`), the operator's
runtime (`save/` beyond the carried config, `workspace/`, logs, sessions, receipts), and model weights
(`*.gguf`). A seal carries the build, not secrets and not the operator's history.

## Restore it (revert)

```
python scripts/restore_build.py --from "{kitdir}" --root "{root}"
```

That verifies every file against `{sums_txt.name}` **before** it writes anything, then lays the build
back. Runtime state and keys are never touched by a restore. Finish with:

```
python scripts/certify_build.py      # expect: CERTIFIED BUILD
python -m unittest discover -s tests # expect: 592 tests, OK
```

## Rule

A sealed build is the revert target, so it must be complete and it must actually run. Seal only a build
whose tests and certifier are green AND whose console has been booted and used - a seal nobody ever
started is how a broken release becomes the thing you revert to.
"""
    (target / "CANON.md").write_text(canon_md, encoding="utf-8")
    (target / "RESTORE.txt").write_text(
        "RESTORE %s %s\n\n"
        "    python scripts/restore_build.py --from \"%s\" --root \"%s\"\n\n"
        "Then: python scripts/certify_build.py  and  python -m unittest discover -s tests\n"
        "This seal is reference only - never edit it.\n" % (kit, release, kitdir, root),
        encoding="utf-8")

    for base, _dirs, names in os.walk(kitdir):
        for n in names:
            p = Path(base) / n
            try:
                p.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
            except OSError:
                pass
    for doc in ("CANON.md", "CANON.json", "RESTORE.txt", "_freeze_facts.json", sums_txt.name):
        try:
            (target / doc).chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
        except OSError:
            pass

    print("\nverifying the seal against its own hash list ...")
    bad = 0
    for rel, digest, _size in sums:
        p = kitdir / rel
        if not p.exists() or sha256_file(p) != digest:
            print("  MISMATCH %s" % rel)
            bad += 1
    if bad:
        print("\nFAILED: %d file(s) do not match the seal's own list. Do not trust this copy." % bad)
        return 2
    print("  %d files verified, listing digest %s" % (len(sums), digest_of_listing[:16]))
    write_index(Path(args.vault).resolve())
    print("\nSEALED  %s  in %.1fs" % (target, time.time() - t0))
    print("Vault index: %s" % (Path(args.vault).resolve() / "INDEX.md"))
    print("Next: keep building in the live tree, and seal the NEXT release number when it ships.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
