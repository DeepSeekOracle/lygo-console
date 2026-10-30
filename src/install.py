"""Public first-run for LYGO LLM Console. Never copies admin.json or steward vaults."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parent
PROMPTS = KIT / "prompts"
WORKSPACE = KIT / "workspace"
SAVE = KIT / "save"
ENGINE = KIT / "engine"
MODELS = KIT / "models"
DATA = KIT / "data"
CONFIG = KIT / "config"

try:  # kit ports: a USB stick ships 9651/11451, never the desktop pair
    from paths import DEFAULT_PORT  # noqa: E402
except Exception:  # standalone copy of this file
    DEFAULT_PORT = 9641

SEED_FILES = ("SOUL.md", "IDENTITY.md", "MEMORY.md", "MAP.md", "BRAIN.md", "LINKS.md")
ADMIN_MARKERS = (
    "LYGO" + "_" + "SERVER" + "_" + "KEYS",
    "LYGO" + "_" + "BUILDER" + "_" + "KEY",
    "<LAN_IP>." + "209",
    "gitea" + ".pass",
    "GamePC",
)


def is_admin_tree() -> bool:
    return (CONFIG / "admin.json").is_file()


def _write_if_missing(dest: Path, text: str) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        return False
    dest.write_text(text, encoding="utf-8")
    return True


def seed_identity(*, force_public: bool = False) -> list[str]:
    """Copy public prompts into workspace. Skip if this is the steward admin tree unless force_public."""
    done: list[str] = []
    if is_admin_tree() and not force_public:
        return ["skip_admin_tree"]
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "skills").mkdir(parents=True, exist_ok=True)
    for name in SEED_FILES:
        src = PROMPTS / name
        dest = WORKSPACE / name
        if not src.is_file():
            continue
        body = src.read_text(encoding="utf-8")
        if dest.is_file() and not force_public:
            old = dest.read_text(encoding="utf-8", errors="replace")
            if any(m in old for m in ADMIN_MARKERS):
                dest.write_text(body, encoding="utf-8")
                done.append("replaced_admin_" + name)
            continue
        dest.write_text(body, encoding="utf-8")
        done.append("seeded_" + name)
    jl = WORKSPACE / "memory.jsonl"
    if not jl.is_file():
        jl.write_text("", encoding="utf-8")
        done.append("seeded_memory.jsonl")
    return done


def ensure_layout() -> list[str]:
    made: list[str] = []
    for p in (
        WORKSPACE,
        WORKSPACE / "skills",
        SAVE,
        SAVE / "sessions",
        SAVE / "notepad" / "notes",
        SAVE / "receipts",
        SAVE / "skills" / "installed",
        DATA,
        ENGINE,
        MODELS,
        CONFIG,
    ):
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            made.append(str(p.relative_to(KIT)))
    _write_if_missing(WORKSPACE / ".gitkeep", "")
    _write_if_missing(MODELS / ".gitkeep", "Put GGUF files here (or map another folder in Workspace).\n")
    _write_if_missing(
        CONFIG / "local.json.example",
        '{\n  "scan_roots": ["./models"],\n'
        '  "comment": "Public kit. Add your own folders in the Workspace panel."\n}\n',
    )
    if not (CONFIG / "local.json").is_file():
        shutil.copy2(CONFIG / "local.json.example", CONFIG / "local.json")
        made.append("config/local.json")
    return made


def write_first_run() -> Path:
    p = DATA / "first_run.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "channel": "PUBLIC",
        "kit": str(KIT),
        "hint": "Scan GGUF, Boot a model, add folders in Workspace. Identity is yours — edit Soul / Identity / Memory.",
        "not": ["admin.json", "steward vaults", "GamePC drives"],
        "portal": f"http://127.0.0.1:{DEFAULT_PORT}/",
    }
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (KIT / "FIRST_RUN.txt").write_text(
        "LYGO LLM Console — public kit\n\n"
        "1. This folder is yours. Soul / Identity / Memory start as public seeds.\n"
        "2. Put GGUF files in models\\ or click Scan. The kit boots its own engine on them - it never needs another service.\n"
        "3. Add any extra folders in the left-rail Workspace panel.\n"
        f"4. Double-click LYGO_LLM_CONSOLE.bat → http://127.0.0.1:{DEFAULT_PORT}/\n"
        "5. This is not the steward admin tree. No vaults shipped.\n"
        "Page: https://chatagent.ca/lygo-llm-console.html\n",
        encoding="utf-8",
    )
    return p


def python_ok() -> str:
    return sys.executable


def engine_present() -> bool:
    return (ENGINE / "llama-server.exe").is_file()


def fetch_engine() -> dict[str, str]:
    ps1 = KIT / "scripts" / "fetch_engine.ps1"
    if not ps1.is_file():
        return {"ok": "false", "error": "missing_script"}
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ps1),
    ]
    try:
        r = subprocess.run(cmd, cwd=str(KIT), capture_output=True, text=True, timeout=300)
    except Exception as e:
        return {"ok": "false", "error": str(e)}
    return {
        "ok": "true" if r.returncode == 0 and engine_present() else "false",
        "code": str(r.returncode),
        "out": (r.stdout or "")[-800],
        "err": (r.stderr or "")[-400],
    }


def report() -> dict[str, object]:
    return {
        "ok": True,
        "channel": "admin" if is_admin_tree() else "public",
        "kit": str(KIT),
        "python": python_ok(),
        "engine": engine_present(),
        "workspace": sorted(p.name for p in WORKSPACE.glob("*.md")),
        "portal": f"http://127.0.0.1:{DEFAULT_PORT}/",
        "bat": "LYGO_LLM_CONSOLE.bat",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Install the PUBLIC LYGO LLM Console for this user.")
    ap.add_argument("--fetch-engine", action="store_true", help="Download ggml-org llama-server CPU zip into engine/")
    ap.add_argument("--force-public-identity", action="store_true", help="Overwrite workspace identity even on an admin tree")
    args = ap.parse_args(argv)
    print("LYGO LLM Console — public install")
    print("kit:", KIT)
    if is_admin_tree() and not args.force_public_identity:
        print("admin.json present — steward tree. Layout only; identity not replaced.")
    layout = ensure_layout()
    seeded = seed_identity(force_public=args.force_public_identity)
    fr = write_first_run()
    print("layout:", ", ".join(layout) or "ok")
    print("identity:", ", ".join(seeded) or "kept")
    print("first_run:", fr)
    print("python:", python_ok())
    if args.fetch_engine:
        print("fetch_engine:", fetch_engine())
    elif not engine_present():
        print("engine: missing — run: powershell -File scripts\\fetch_engine.ps1")
        print("  or put llama-server.exe in engine\\ from ggml-org CPU zip (pin b11457)")
    else:
        print("engine: llama-server.exe present")
    from colibri import resolve_coli

    if resolve_coli():
        print("colibri: launcher present (optional MoE SSD engine)")
    else:
        print("colibri: optional — scripts\\fetch_colibri.ps1 (engine only, not 372GB weights)")
    print("Next: double-click LYGO_LLM_CONSOLE.bat")
    print(f"Portal: http://127.0.0.1:{DEFAULT_PORT}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
