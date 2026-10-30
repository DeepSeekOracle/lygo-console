"""Lattice City — vault snapshot.

Locks a PUSHED commit of games/lattice-city into the LYGO mirror vault, so any agent that
wrecks the working copy can rebuild the exact released file set.

Vault root:  <EDRIVE>\\LYGO GAMES AND WEBPAGES\\lattice-city\\
  RELEASE-READONLY\\<commit>\\   exact export of the pushed tree (optionally write-locked)
  SUBMISSIONS\\<stamp>_<task>_<agent>\\  pre-overwrite backups and hand-ins
  LEDGER.jsonl                  append-only: every snapshot, with file count + sha256
  <project>_<commit>.zip        cold storage

Usage:
    C:\\Python313\\python.exe tools\\vault_snapshot.py snapshot            # HEAD, locked
    C:\\Python313\\python.exe tools\\vault_snapshot.py snapshot --no-lock
    C:\\Python313\\python.exe tools\\vault_snapshot.py list

Rules (see skill project-vault / lygo-mirror-vault):
  * snapshot only what is pushed;
  * never write into RELEASE-READONLY (restore direction is vault -> working copy);
  * never edit or delete a SUBMISSIONS folder; append a new one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

REPO = Path(r"D:\excavationpro-ca")
GAME_IN_REPO = "games/lattice-city"
VAULT_ROOT = Path(r"<EDRIVE>\LYGO GAMES AND WEBPAGES")
PROJECT = "lattice-city"


def run(args, cwd=None):
    out = subprocess.run(args, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"FAILED {' '.join(args)}\n{out.stderr.strip()}")
    return out.stdout.strip()


def git_dir():
    return Path(run(["git", "rev-parse", "--show-toplevel"], REPO))


def is_dirty():
    return bool(run(["git", "status", "--porcelain"], REPO))


def head_commit():
    return run(["git", "rev-parse", "--short", "HEAD"], REPO)


def export_tree(commit: str, dest: Path):
    """Write the exact committed subtree to dest (LF content as stored in git)."""
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tf:
        tar_path = Path(tf.name)
    try:
        run(["git", "archive", "--format=tar", "-o", str(tar_path), commit, GAME_IN_REPO], REPO)
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tar_path) as tar:
            for member in tar.getmembers():
                parts = member.name.split("/", 2)
                if len(parts) < 3 or not member.isfile():
                    continue
                src = tar.extractfile(member)
                target = dest / parts[2]
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, "wb") as fh:
                    shutil.copyfileobj(src, fh)
    finally:
        tar_path.unlink(missing_ok=True)


def sha256(path: Path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def bundle_hash(root: Path):
    """One hash over every file path + content, in sorted order."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).replace("\\", "/").encode())
            h.update(sha256(p).encode())
    return h.hexdigest()


def lock(path: Path):
    """Deny content writes and child deletes (Windows). Reads and listing MUST keep working, or the
    vault cannot be used to rebuild anything — so never deny the generic W right, only WD/AD/DE/DC."""
    who = run(["whoami"]).strip()
    subprocess.run(["icacls", str(path), "/deny", f"{who}:(OI)(CI)(WD,AD,DE,DC)"],
                   capture_output=True, text=True)
    return verify_lock(path)


def verify_lock(path: Path) -> bool:
    """Lock is good only if: listing works, a file can be read, and a new file cannot be written."""
    try:
        next(path.glob("*"))
    except OSError:
        return False
    files = [p for p in path.glob("*") if p.is_file()]
    if not files:
        return False
    try:
        files[0].read_bytes()[:64]
    except OSError:
        return False
    probe = path / "_vault_write_probe.tmp"
    try:
        probe.write_text("x", encoding="utf-8")
    except OSError:
        return True          # write refused: correct
    try:
        probe.unlink()
    except OSError:
        pass
    return False             # write succeeded: the lock is not real


def append_ledger(vault: Path, entry: dict):
    with open(vault / "LEDGER.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def readme(vault: Path):
    text = f"""# LYGO MIRROR VAULT — {PROJECT}

Two copies of the truth, plus git as the third.

## WORKING COPY
`<PROJECTS>\\lattice-city\\site` — what the Forge and its agents edit. Preview on
http://127.0.0.1:8796/preview/lattice-city/. It is expected to be messy and wrong sometimes.

## VAULT (read-only reference, append-only submissions)
`{vault}`

| Folder | Rule |
|---|---|
| `RELEASE-READONLY/<commit>/` | Exact export of a **pushed** release, taken from git (not the dirty tree). Write-locked. Never edited or deleted. |
| `SUBMISSIONS/<stamp>_<task>_<agent>/` | Append-only pre-overwrite backups and hand-ins. Nothing is ever overwritten. |
| `LEDGER.jsonl` | Append-only: every snapshot and submission with file count, bundle hash and per-file hashes. |
| `{PROJECT}_<commit>.zip` | Cold storage of a finished release. |

## THE LOOP
1. An agent edits the working copy. Before overwriting a file, copy it into
   `SUBMISSIONS/<stamp>_<task>_<agent>/`.
2. When a commit is pushed, `python tools\\vault_snapshot.py snapshot` exports it here and locks it.
3. If a file is empty, truncated or corrupt: read it from `RELEASE-READONLY/<newest>/<path>`, rebuild it,
   and write it back to the **working copy**. Never write into the vault.

## UNLOCK (only if a snapshot must be removed)
`icacls "<vault>\\RELEASE-READONLY\\<commit>" /remove:d "%USERNAME%"`
"""
    (vault / "README.md").write_text(text, encoding="utf-8")


def cmd_snapshot(args):
    if is_dirty():
        print("WARNING: working tree is dirty; snapshot still uses the committed tree at HEAD.")
    commit = head_commit()
    vault = VAULT_ROOT / PROJECT
    release = vault / "RELEASE-READONLY" / commit
    (vault / "SUBMISSIONS").mkdir(parents=True, exist_ok=True)
    if release.exists():
        print(f"release {commit} already vaulted at {release}")
        return 0

    export_tree(commit, release)
    files = [p for p in release.rglob("*") if p.is_file()]
    bh = bundle_hash(release)

    zip_path = vault / f"{PROJECT}_{commit}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in files:
            zf.write(p, p.relative_to(release))

    locked = False
    if not args.no_lock:
        locked = lock(release)

    readme(vault)
    append_ledger(vault, {
        "action": "snapshot",
        "project": PROJECT,
        "release": commit,
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
        "bundle_sha256": bh,
        "zip": zip_path.name,
        "locked": locked,
        "source": "git",
        "commit": commit,
        "at": datetime.now().isoformat(timespec="seconds"),
    })
    print(f"snapshot {commit}: {len(files)} files, {sum(p.stat().st_size for p in files)/1e6:.2f} MB")
    print(f"bundle {bh}")
    print(f"zip    {zip_path}")
    print(f"locked {locked}")
    return 0


def cmd_list(args):
    vault = VAULT_ROOT / PROJECT
    releases = vault / "RELEASE-READONLY"
    if not releases.exists():
        print("no vault yet")
        return 0
    for d in sorted(releases.iterdir()):
        n = len([p for p in d.rglob("*") if p.is_file()])
        print(f"{d.name:12s} {n:5d} files  {d}")
    ledger = vault / "LEDGER.jsonl"
    if ledger.exists():
        lines = ledger.read_text(encoding="utf-8").strip().splitlines()
        print(f"ledger: {len(lines)} entries")
        if lines:
            e = json.loads(lines[-1])
            print(f"last: {e.get('action')} {e.get('release', '')} {e.get('at', '')}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Lattice City vault snapshot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--no-lock", action="store_true", help="skip the ACL write lock")
    sub.add_parser("list")
    args = ap.parse_args()
    return cmd_snapshot(args) if args.cmd == "snapshot" else cmd_list(args)


if __name__ == "__main__":
    sys.exit(main())
