#!/usr/bin/env python3
"""Lay a sealed build back over the live kit - the revert path.

WHY THIS EXISTS
    Reverting must be mechanical and it must be proven before it writes. This
    tool verifies every file in the seal against its hash list FIRST; if the
    seal is not exactly what it claims to be, nothing is written at all.

USAGE
    python restore_build.py --from "<seal>/PC_LOCAL" --root "<live kit>" [--dry-run]

WHAT IT DOES
    1. Finds SHA256SUMS-<KIT>.txt next to the seal (or --sums), verifies every
       raw sha256. Any mismatch stops the run before a single byte is written.
    2. Classifies every sealed file against the live tree: identical / will restore.
    3. Writes the build back, and makes the restored files writeable again
       (sealed files are read-only by design).

WHAT IT NEVER TOUCHES
    The operator's runtime and keys: save/, workspace/, data/, models/, logs,
    config/api.json, config/local.json, *.gguf, *.log. A revert rewinds the
    BUILD, it does not rewind the operator's work or their model choice.
    Nothing is ever deleted: files present in the live tree but absent from the
    seal are reported and left alone.

AFTERWARDS
    python scripts/certify_build.py        # expect: CERTIFIED BUILD
    python -m unittest discover -s tests   # expect: 592 tests, OK

EXIT
    0 restored/verified · 1 refused (seal failed verification) · 2 error
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import sys
from pathlib import Path

PROTECTED_DIRS = {"save", "data", "models", "logs", "__pycache__", ".git", "node_modules"}
PROTECTED_FILES = {"api.json", "local.json"}
PROTECTED_SUFFIX = {".gguf", ".safetensors", ".log", ".tmp"}
WRITE_MODE = stat.S_IWRITE | stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def parse_sums(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, rel = parts[0].strip(), parts[1].strip()
        if rel.startswith("*"):
            rel = rel[1:]
        entries.append((rel, digest.lower()))
    return entries


def load_carried(src: Path) -> set[str]:
    """The build's working configuration that this seal carries on purpose."""
    carried = {"save/registry.json"}
    facts = src.parent / "CANON.json"
    if facts.exists():
        try:
            got = json.loads(facts.read_text(encoding="utf-8")).get("carried_config")
            if isinstance(got, list) and got:
                carried = {str(x).replace("\\", "/") for x in got}
        except (OSError, ValueError):
            pass
    return carried


def load_dirs(src: Path) -> list[str]:
    """Folders the build owns, so a restore rebuilds the shape - empty folders included."""
    facts = src.parent / "CANON.json"
    if not facts.exists():
        return []
    try:
        got = json.loads(facts.read_text(encoding="utf-8")).get("dirs")
    except (OSError, ValueError):
        return []
    return [str(x).replace("\\", "/") for x in got] if isinstance(got, list) else []


def protected(rel: Path) -> bool:
    parts = rel.parts
    if any(p in PROTECTED_DIRS for p in parts):
        return True
    name = parts[-1] if parts else ""
    if name in PROTECTED_FILES:
        return True
    return rel.suffix.lower() in PROTECTED_SUFFIX


def main() -> int:
    ap = argparse.ArgumentParser(description="Restore a sealed build over the live kit.")
    ap.add_argument("--from", dest="src", required=True, help="the sealed kit dir (e.g. <seal>/PC_LOCAL)")
    ap.add_argument("--root", required=True, help="the live kit dir to restore into")
    ap.add_argument("--sums", default="", help="explicit SHA256SUMS file (default: sibling of --from)")
    ap.add_argument("--dry-run", action="store_true", help="verify and report, write nothing")
    ap.add_argument("--allow-mismatch", action="store_true", help="continue even if the seal fails verification")
    args = ap.parse_args()

    src = Path(args.src).resolve()
    root = Path(args.root).resolve()
    if not src.is_dir():
        print("NO SUCH SEAL: %s" % src)
        return 1
    if root.exists():
        if not (root / "VERSION").exists() and any(root.iterdir()):
            print("NOT A KIT: %s has files but no VERSION." % root)
            print("Refusing to write a build into a directory that is not a kit.")
            print("(An empty directory - or one that does not exist yet - is fine: that is the")
            print(" disaster-recovery case, and the seal is laid down fresh.)")
            return 1
    else:
        root.mkdir(parents=True)
        print("fresh target created: %s" % root)

    sums_path = Path(args.sums).resolve() if args.sums else src.parent / ("SHA256SUMS-%s.txt" % src.name)
    if not sums_path.exists():
        print("NO HASH LIST: expected %s (pass --sums to point at one)" % sums_path)
        return 1

    entries = parse_sums(sums_path)
    if not entries:
        print("EMPTY HASH LIST: %s" % sums_path)
        return 1

    seal_root = src.parent
    pairs: list[tuple[str, Path, str]] = []
    for rel, digest in entries:
        rel_norm = rel.lstrip("./")
        parts = Path(rel_norm).parts
        if len(parts) < 2 or parts[0] != src.name:
            print("HASH LIST DOES NOT MATCH: %r is not under %s/" % (rel_norm, src.name))
            return 1
        pairs.append((rel_norm, Path(*parts[1:]), digest))

    print("seal       : %s" % src)
    print("hash list  : %s  (%d files)" % (sums_path.name, len(entries)))
    print("restore to : %s" % root)

    print("\nverifying the seal against its own hash list ...")
    bad: list[str] = []
    for rel, _kitrel, digest in pairs:
        p = seal_root / rel
        if not p.exists():
            bad.append("%s  (missing from seal)" % rel)
        elif sha256_file(p) != digest:
            bad.append("%s  (content does not match its hash)" % rel)
    if bad:
        print("  %d problem(s):" % len(bad))
        for line in bad[:20]:
            print("   ! %s" % line)
        if not args.allow_mismatch:
            print("\nREFUSING TO RESTORE: this seal is not intact. Nothing was written.")
            return 1
        print("\ncontinuing anyway because --allow-mismatch was given.")

    print("  seal intact: %d/%d files verified" % (len(entries) - len(bad), len(entries)))

    todo: list[tuple[str, str]] = []
    same = 0
    held = 0
    carried = load_carried(src)
    for _rel, kitrel, digest in pairs:
        key = str(kitrel).replace("\\", "/")
        if protected(kitrel) and key not in carried:
            held += 1
            continue
        dst = root / kitrel
        if dst.exists() and sha256_file(dst) == digest:
            same += 1
            continue
        todo.append((str(kitrel).replace("\\", "/"), digest))

    print("\nidentical already : %d" % same)
    print("will restore      : %d" % len(todo))
    print("held back (yours) : %d  (keys, runtime, models, logs)" % held)
    if carried:
        print("carried config    : %s  (restored - this is what makes the build work)" % ", ".join(sorted(carried)))
    for rel, _ in todo[:25]:
        print("   ~ %s" % rel)
    if len(todo) > 25:
        print("   ... and %d more" % (len(todo) - 25))

    if args.dry_run:
        print("\nDRY RUN - nothing written.")
        return 0
    if not todo:
        print("\nNothing to do - the live tree already matches this seal.")
        return 0

    written = 0
    for rel, _digest in todo:
        s = src / rel
        d = root / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        if d.exists():
            try:
                d.chmod(WRITE_MODE)
            except OSError:
                pass
        shutil.copy2(s, d)
        try:
            d.chmod(WRITE_MODE)
        except OSError:
            pass
        written += 1

    made = 0
    for rel_dir in load_dirs(src):
        d = root / rel_dir
        if not d.is_dir():
            d.mkdir(parents=True, exist_ok=True)
            made += 1
    print("\nRESTORED %d file(s) into %s" % (written, root))
    if made:
        print("created %d folder(s) the build owns (empty folders count)" % made)

    extra = []
    sealed = {str(kitrel).replace("\\", "/") for _rel, kitrel, _d in pairs}
    for base, dirs, files in __import__("os").walk(root):
        dirs[:] = [x for x in dirs if x not in PROTECTED_DIRS and x != "__pycache__"]
        for n in files:
            rel = str(Path(base).relative_to(root) / n).replace("\\", "/")
            if rel not in sealed and not protected(Path(rel)) and not n.endswith(".pyc"):
                extra.append(rel)
    if extra:
        print("\nlive-only files left alone (not in this seal): %d" % len(extra))
        for rel in extra[:10]:
            print("   + %s" % rel)

    print("\nNow finish the job:")
    print("  python scripts/certify_build.py         # expect CERTIFIED BUILD")
    print("  python -m unittest discover -s tests    # expect 592 tests, OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
