"""ship.py — keep the Forge copy of Lattice City in step with LIVE, and stamp ships.

Two jobs, and only two:

    python tools/ship.py sync --check-live
        Copy the repo game into the Forge project (so /preview/<slug>/ runs the real game),
        hash the key files, and optionally compare those hashes to the live site.

    python tools/ship.py stamp --project <dir> --signer delta
        Write a SHIP STAMP: one hash over the stamped files + the commit. Only for a full ship
        that an agent AND a human have signed off. Without --human it records status "candidate".

Rationale: during build the ledger is append-only and unhashed. At ship time we want receipts.
A stamp is the receipt: what shipped, from which commit, matching LIVE or not.

Stdlib only. No network unless --check-live is passed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_GAME = Path(r"D:\excavationpro-ca\games\lattice-city")
PROJECT = Path(r"<PROJECTS>\lattice-city")
LIVE = "https://excavationpro.ca/games/lattice-city/"
KEY_FILES = ("city.js", "index.html", "city.css", "radio.js", "assets/tiles/manifest.json")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _lf(data: bytes) -> bytes:
    """Normalise line endings.

    The working tree is checked out with CRLF (core.autocrlf on Windows) while GitHub Pages serves
    the LF blob from git. Hashing raw bytes therefore reports DIFF on every file even when the
    content is identical — compare normalised text, not checkout bytes.
    """
    return data.replace(b"\r\n", b"\n")


def sha256_lf(path: Path) -> str:
    return hashlib.sha256(_lf(path.read_bytes())).hexdigest()


def remote_bytes(url: str) -> bytes | None:
    """Fetch and decompress. GitHub Pages sends gzip; hashing the wire bytes gives false DIFFs."""
    import gzip
    import zlib
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "LYGO-Forge-Ship/1.0",
            "Accept-Encoding": "gzip, deflate",
        })
        with urllib.request.urlopen(req, timeout=25) as r:
            data = r.read()
            enc = (r.headers.get("Content-Encoding") or "").lower()
        if "gzip" in enc:
            data = gzip.decompress(data)
        elif "deflate" in enc:
            data = zlib.decompress(data)
        return data
    except Exception as exc:
        print(f"  ! could not fetch {url}: {exc}")
        return None


def remote_sha(url: str) -> str | None:
    data = remote_bytes(url)
    return hashlib.sha256(_lf(data)).hexdigest() if data is not None else None


def git_head(repo: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def cmd_sync(args) -> int:
    src, dst = Path(args.src), Path(args.dst)
    if not (src / "city.js").exists():
        print(f"source is not a game checkout: {src}", file=sys.stderr)
        return 2
    if not dst.exists():
        print(f"project not found: {dst}", file=sys.stderr)
        return 2
    copied = 0
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != ".git"]
        rel = Path(root).relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(Path(root) / f, dst / rel / f)
            copied += 1

    files = {}
    for rel in KEY_FILES:
        p = dst / rel
        if p.exists():
            files[rel] = sha256_lf(p)
    live = {}
    if args.check_live:
        print("checking LIVE …")
        for rel in KEY_FILES:
            live[rel] = remote_sha(LIVE + rel)
    match = None
    if live:
        match = all(live.get(k) and files.get(k) == live[k] for k in live)

    version = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": str(src),
        "project": str(dst),
        "commit": git_head(src),
        "files": files,
        "live": live,
        "live_match": match,
        "copied": copied,
    }
    out = dst / "docs" / "VERSIONS.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(version, indent=2) + "\n", encoding="utf-8")

    print(f"synced {copied} files  {src} -> {dst}")
    print(f"commit {version['commit'] or '?'}   wrote {out}")
    for rel, h in files.items():
        tag = ""
        if live.get(rel):
            tag = "  LIVE " + ("MATCH" if live[rel] == h else "DIFF")
        print(f"  {rel:32s} {h[:16]}{tag}")
    if live:
        print(f"live match: {'YES' if match else 'NO'}")
    return 0 if (match is not False) else 1


def cmd_stamp(args) -> int:
    project = Path(args.project)
    vpath = project / "docs" / "VERSIONS.json"
    if not vpath.exists():
        print(f"no {vpath} — run `ship.py sync` first", file=sys.stderr)
        return 2
    version = json.loads(vpath.read_text(encoding="utf-8"))
    bundle = hashlib.sha256()
    for rel in sorted(version.get("files", {})):
        bundle.update(f"{rel}:{version['files'][rel]}\n".encode())
    bundle.update(f"commit:{version.get('commit','')}\n".encode())
    digest = bundle.hexdigest()
    verdict = "confirmed" if args.human else "candidate"
    stamp = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "kind": "ship",
        "version": digest,
        "bundle_sha256": digest,
        "commit": version.get("commit", ""),
        "files": version.get("files", {}),
        "live_match": version.get("live_match"),
        "signer": args.signer,
        "human_signoff": args.human or "",
        "status": verdict,
        "note": args.note or "",
    }
    out = project / "docs" / "SHIP-STAMPS.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(stamp) + "\n")
    print(f"stamp {digest[:16]}  commit {stamp['commit'] or '?'}  status {verdict}")
    print(f"appended to {out}")
    if verdict == "candidate":
        print("PENDING: no --human sign-off yet. This is not an audit stamp.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="copy the repo game into the project, hash, optionally check LIVE")
    s.add_argument("--src", default=str(REPO_GAME))
    s.add_argument("--dst", default=str(PROJECT))
    s.add_argument("--check-live", action="store_true")
    s.set_defaults(func=cmd_sync)

    t = sub.add_parser("stamp", help="write a ship stamp (full ship + human sign-off only)")
    t.add_argument("--project", default=str(PROJECT))
    t.add_argument("--signer", default="delta")
    t.add_argument("--human", default="", help="human who signed off; omit to record a candidate")
    t.add_argument("--note", default="")
    t.set_defaults(func=cmd_stamp)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
