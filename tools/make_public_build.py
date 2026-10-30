"""Export a PUBLIC build of the LYGO console from the working admin kit.

The admin stick is the source of truth; this tool produces the version you can hand to someone
else. It copies only what a stranger needs and REFUSES to export anything credential-bearing:

  * config/api.json, config/admin.json, config/local.json  -> never copied
  * save/ (sessions, receipts), data/ (tokens), __pycache__ -> never copied
  * every text file is scanned for key material before it is written; the build fails loudly
    if any is found, and names the file.

Usage (any python 3.9+):
    python tools/make_public_build.py --out <DRIVE_D>\LYGO_PUBLIC_LYGO_CLAW
    python tools/make_public_build.py --out <DRIVE_D>\LYGO_PUBLIC --with-python --with-engine
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
SIGNATURE = "Δ9Φ963-LYGO-LLM-CONSOLE-v1"

COPY_DIRS = ["src", "portal", "prompts", "tests", "skills", "scripts", "tools", "web_portal", "workspace"]
COPY_FILES = [
    "README.md", "WHITEPAPER.md", "BUILD_MANIFEST.json", "MODELS.md", "COLIBRI.md", "HARDENING.md",
    "LYGO_ENGINE.md", "PUBLIC_PORTAL.md", "SKILLS.md", "READ_DISCLAIMER_FIRST.md", "FULL_LYGO.md",
    "INSTALL.bat", "LYGO_LLM_CONSOLE_STOP.bat", "PUBLIC_GATEWAY.bat",
]
OPTIONAL_DIRS = {"python": "--with-python", "engine": "--with-engine"}
NEVER = {
    "config/api.json", "config/admin.json", "config/local.json",
    "data/.lygo_llm_token", "save",
}

# things that look like a live credential, not like documentation
SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"\bgsk_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxai-[A-Za-z0-9]{20,}"),
    re.compile(r"\bnvapi-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9_\-\.]{30,}"),
]
TEXT_SUFFIX = {".py", ".js", ".css", ".html", ".json", ".md", ".txt", ".bat", ".ps1", ".yml", ".yaml", ".toml", ".example"}


def scan_text(path: Path) -> list[str]:
    if path.suffix.lower() not in TEXT_SUFFIX:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits = []
    for pat in SECRET_PATTERNS:
        for m in pat.finditer(text):
            hits.append("%s:%s" % (path, m.group(0)[:8] + "…"))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="target folder for the public build")
    ap.add_argument("--kit", default=str(KIT), help="admin kit to export from")
    ap.add_argument("--with-python", action="store_true", help="include the bundled CPython")
    ap.add_argument("--with-engine", action="store_true", help="include llama-server (else INSTALL.bat fetches it)")
    ap.add_argument("--force", action="store_true", help="overwrite the target if it exists")
    ap.add_argument(
        "--local-only",
        action="store_true",
        help="ignore the stack release packer; export from this kit alone (offline stick)",
    )
    args = ap.parse_args()

    kit = Path(args.kit).resolve()
    if not args.local_only:
        # The stack already owns the release flow (dist + zip + admin-marker stripping).
        # Delegate to it whenever it is reachable, so there is one public-builder, not two.
        for cand in (
            kit.parent / "tools" / "pack_lygo_llm_console_public.py",
            kit.parent / "stack" / "lygo-protocol-stack" / "tools" / "pack_lygo_llm_console_public.py",
        ):
            if cand.is_file():
                print("delegating to the stack release packer (dist + zip): %s" % cand)
                return subprocess.call([sys.executable, str(cand)])
    out = Path(args.out).resolve()
    if out.exists():
        if not args.force:
            print("refusing: %s already exists (pass --force)" % out)
            return 2
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    copied, leaks, refused = 0, [], []
    for rel in COPY_DIRS:
        src = kit / rel
        if not src.is_dir():
            continue
        for root, dirs, files in os_walk(src):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            r = Path(root).relative_to(kit)
            for f in files:
                if f.endswith((".pyc", ".pyo")):
                    continue
                relf = str(r / f)
                if relf in NEVER or relf.startswith("save/"):
                    refused.append(relf)
                    continue
                dst = out / r / f
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(root) / f, dst)
                copied += 1
                leaks.extend(scan_text(dst))
    for rel in COPY_FILES:
        src = kit / rel
        if not src.is_file():
            continue
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
        leaks.extend(scan_text(dst))
    for rel, flag in OPTIONAL_DIRS.items():
        wanted = args.with_python if rel == "python" else args.with_engine
        if not wanted or not (kit / rel).is_dir():
            continue
        for root, dirs, files in os_walk(kit / rel):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            r = Path(root).relative_to(kit)
            for f in files:
                dst = out / r / f
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(root) / f, dst)
                copied += 1

    # public config: fresh roots, no admin, no keys
    cfg = out / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "console.json").write_text(
        json.dumps(
            {
                "signature": SIGNATURE,
                "bind": "127.0.0.1",
                "port": 9641,
                "llama_port": 11441,
                "embed_port": 11442,
                "ctx_default": 4096,
                "ctx_max": 8192,
                "ngl": 0,
                "threads": 4,
                "max_tokens": 512,
                # RAM-auto: pick the biggest model THIS host can hold (src/registry.py).
                "prefer_by_ram": True,
                "scan_roots": ["./models"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if leaks:
        print("BUILD REFUSED — key material found in the export:")
        for hit in leaks[:20]:
            print("   " + hit)
        shutil.rmtree(out, ignore_errors=True)
        return 3

    (out / "README_PUBLIC.md").write_text(
        "# LYGO LLM Console — public build\n\n"
        "1. `INSTALL.bat` — seeds your own identity (Soul / Identity / Memory). No steward vaults.\n"
        "2. `LYGO_LLM_CONSOLE.bat` — start the console, then open http://127.0.0.1:9641/\n"
        "3. Scan drives, pick a model, Boot LLM. The local engine is the default brain.\n\n"
        "Cloud API is optional: open the brain switch, pick a provider, paste YOUR key. Nothing is\n"
        "shipped with a key — this build contains no credentials of any kind.\n",
        encoding="utf-8",
    )
    (out / "PUBLIC_MANIFEST.json").write_text(
        json.dumps(
            {
                "signature": SIGNATURE,
                "edition": "PUBLIC",
                "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "source_kit": str(kit),
                "files": copied,
                "refused_credential_files": sorted(set(refused)),
                "no_secrets": True,
                "with_python": bool(args.with_python),
                "with_engine": bool(args.with_engine),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("PUBLIC BUILD OK: %s" % out)
    print("   files: %d   credential files refused: %d   secret hits: 0" % (copied, len(set(refused))))
    return 0


def os_walk(root: Path):
    import os

    return os.walk(root)


if __name__ == "__main__":
    sys.exit(main())
