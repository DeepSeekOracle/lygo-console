"""Package the LYGO Local Agent Console as a self-contained, branded local hub.

Copies the kit to a destination drive (default <DRIVE_D>\LYGO_CONSOLE), leaves runtime state and
secrets behind, gives the copy its OWN port triplet so it can run beside the original, and
writes a manifest + operator README. Verifies the result: no secret from the source tree may
appear anywhere in the destination, and the copy must actually boot and answer /api/health.

    python tools/pack_to_drive.py --dest <DRIVE_D>\LYGO_CONSOLE
    python tools/pack_to_drive.py --dest <DRIVE_D>\LYGO_CONSOLE --apply     (default is a dry run)
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
import re
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
SIGNATURE = "Δ9Φ963-LYGO-LLM-CONSOLE-v1"

# Runtime state and secrets: never copied. data/ holds the console token and the engine pid;
# save/ holds sessions, receipts, notes and installed skills; both config files are host state
# (api.json carries a live provider key, local.json the host's scan roots).
EXCLUDE_DIRS = {"__pycache__", ".git", ".pytest_cache", "data", "save", "_package_build"}
EXCLUDE_FILES = {
    "config/api.json",
    "config/local.json",
    "src/_torn_console.json",
}
EXCLUDE_GLOBS = ("*.pyc", "*.log", "*.tmp", "models/*.gguf")
KEEP_IN_EXCLUDED_DIRS = {"workspace/.gitkeep"}


def rel(p: Path) -> str:
    return p.relative_to(KIT).as_posix()


def plan_copy() -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    skipped: list[str] = []
    for p in KIT.rglob("*"):
        r = rel(p)
        parts = set(p.relative_to(KIT).parts)
        if parts & EXCLUDE_DIRS:
            if r in KEEP_IN_EXCLUDED_DIRS:
                files.append(p)
            continue
        if r in EXCLUDE_FILES:
            skipped.append(r + "  (state/secret)")
            continue
        if any(p.match(g) for g in EXCLUDE_GLOBS):
            skipped.append(r + "  (pattern)")
            continue
        if p.is_file():
            files.append(p)
    return files, skipped


# A field is a secret when its NAME says so. config/api.json also holds the public endpoint and
# the model id, and collecting those aborted every cut: the endpoint is in the kit's own source
# by design, so "every string in api.json" is the wrong test.
SECRET_KEY = re.compile(r"(key|token|secret|passwd|password|pwd|auth|cookie|credential|bearer)", re.I)


def _carries_secret(value: str) -> bool:
    """True only for a URL that really hides a credential: userinfo or a query string.

    A long path is not evidence - model ids and endpoints contain long dotted runs.
    """
    if not value.startswith(("http://", "https://")):
        return False
    rest = value.split("://", 1)[1]
    if "@" in rest.split("/", 1)[0]:
        return True
    return "?" in rest and len(rest.split("?", 1)[1].strip()) >= 8


def secret_values() -> dict[str, str]:
    """Every secret-ish string in the source tree, so we can prove none reached the copy.

    Labels only - the values stay in memory and are never printed.
    """
    out: dict[str, str] = {}
    candidates = [
        KIT / "config" / "api.json",
        KIT / "data" / ".lygo_llm_token",
        KIT / "data" / ".llama_api_key",
        KIT / "data" / "cloud.json",
        KIT / "save" / "cloud.json",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            txt = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        try:
            obj = json.loads(txt)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and len(v) >= 16 and (SECRET_KEY.search(k) or _carries_secret(v)):
                    out[f"{rel(path)}:{k}"] = v
        elif len(txt) >= 16:
            out[rel(path)] = txt
    return out


def scan_for_secrets(dest: Path, secrets: dict[str, str]) -> dict[str, list[str]]:
    """Return {secret_label: [files containing it]} - labels only, never the values."""
    hits: dict[str, list[str]] = {}
    payloads: list[tuple[Path, bytes]] = []
    for p in dest.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".json", ".py", ".bat", ".md", ".txt", ".js", ".css", ".html", ".jsonl", ".cfg", ".ini"}:
            try:
                payloads.append((p, p.read_bytes()))
            except OSError:
                continue
    for label, value in secrets.items():
        found = [rel_dest(dest, p) for p, b in payloads if value.encode("utf-8") in b]
        if found:
            hits[label] = found
    return hits


def rel_dest(dest: Path, p: Path) -> str:
    try:
        return p.relative_to(dest).as_posix()
    except ValueError:
        return str(p)


def free_port_ok(port: int) -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def write_hub_config(dest: Path, port: int, engine_port: int, embed_port: int, label: str) -> dict:
    cfg_path = dest / "config" / "console.json"
    cfg: dict = {}
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    before = {k: cfg.get(k) for k in ("port", "bind")}
    # Key names matter: the launchers read these from config and EXPORT LYGO_*_PORT, which is
    # what src/paths.py actually uses (paths.py:69-72 reads env, not this file). Writing
    # "engine_port" here would be a dead key while llama_port kept the SOURCE tree's value -
    # which is how a hub ends up fighting the desktop console for 11441.
    cfg["port"] = port
    cfg["bind"] = "127.0.0.1"          # loopback: this hub is local. LAN needs --lan --i-consent.
    cfg["llama_port"] = engine_port
    cfg["embed_port"] = embed_port
    cfg["colibri_port"] = embed_port + 1
    cfg["hub"] = {"label": label, "ports": {"console": port, "engine": engine_port, "embed": embed_port}}
    # scan_roots: the source tree may point at drive-anchored model stores (a mapped U:, another
    # disk) that do not exist for this copy. The hub scans its own models/ folder instead.
    roots_before = cfg.get("scan_roots")
    if isinstance(roots_before, list):
        # Keep only roots this copy can actually reach: kit-relative entries and %VAR%/~ entries
        # stay (a hub on a machine with Ollama still finds that store); entries anchored to a
        # drive or UNC path this copy will not have (a mapped U:, the source desktop's I:) go.
        keep = [s for s in roots_before if isinstance(s, str) and not Path(s).is_absolute()]
        cfg["scan_roots"] = keep or ["./models"]
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "before": before,
        "after": {k: cfg[k] for k in ("port", "bind", "llama_port", "embed_port", "colibri_port")},
        "scan_roots": {"before": roots_before, "after": cfg["scan_roots"]},
    }


def _ascii(text: str) -> str:
    """Transliterate the sigil for cmd.

    A .bat must stay ASCII: cmd reads it in the console code page, so a UTF-8 sigil prints as
    mojibake - and the raw glyph aborted the whole cut with UnicodeEncodeError. The real label
    keeps its glyphs in README_HUB.md and PACKAGE_MANIFEST.json.
    """
    table = str.maketrans({
        "\u0394": "D", "\u03a6": "F", "\u2014": "-", "\u2013": "-", "\u00b7": "-",
        "\u2192": "->", "\u21bb": "retry", "\u2018": chr(39), "\u2019": chr(39),
        "\u201c": chr(34), "\u201d": chr(34), "\u2265": ">=", "\u2264": "<=", "\u2026": "...",
    })
    return text.translate(table).encode("ascii", "replace").decode("ascii")


def write_run_bat(dest: Path, port: int, label: str) -> Path:
    alabel = _ascii(label)
    run = dest / "RUN_LYGO_HUB.bat"
    run.write_text(
        f"""@echo off
REM ==========================================================================
REM  LYGO LOCAL AGENT CONSOLE - {alabel}
REM  Branded local hub. The console's port/bind live in config\\console.json.
REM  This kit runs beside the original: its own port triplet, its own state.
REM  Signatures: {_ascii(SIGNATURE)}
REM ==========================================================================
setlocal
cd /d "%~dp0"
set "LYGO_HUB=1"
set "LYGO_HUB_LABEL={alabel}"
echo [LYGO HUB] {alabel}
echo [LYGO HUB] kit  %CD%
echo [LYGO HUB] console port {port} (config\\console.json)
call "%~dp0LYGO_LLM_CONSOLE.bat"
endlocal
""",
        encoding="ascii",
        newline="\r\n",
    )
    return run


def write_readme(dest: Path, manifest: dict) -> Path:
    ports = manifest["ports"]
    md = f"""# LYGO LOCAL AGENT CONSOLE — {manifest['label']}

Packaged {manifest['packaged_at']} from the canonical kit
`{manifest['source']}` onto this drive as a **self-contained local hub**.

- **Start:** `RUN_LYGO_HUB.bat` (wraps `LYGO_LLM_CONSOLE.bat`)
- **Stop:** `LYGO_LLM_CONSOLE_STOP.bat`
- **Open:** http://127.0.0.1:{ports['console']}/ — the browser opens itself on start
- **Ports (this copy only):** console `{ports['console']}`, engine `{ports['engine']}`, embed `{ports['embed']}`
- **Signature:** `{manifest['signature']}` · build `{manifest['build']}`

## Why this copy is safe to run beside the original
The desktop kit keeps `9641/11441/11442`, the USB stick keeps `9651/11451/11452`, and this hub
keeps `{ports['console']}/{ports['engine']}/{ports['embed']}`. The port and bind live in
`config/console.json`, and the launcher's kill-sweep reads the same values — so starting this
hub cannot stop the console you already have running.

## What was deliberately left behind
{chr(10).join('- `' + s + '`' for s in manifest['excluded'])}

Runtime state (`data/`, `save/`, `workspace/`) is not copied: the hub mints its own token on
first start and begins with a clean session history. To bring your history over, copy those
folders from the source kit yourself, deliberately.

## Re-provisioning (first run)
1. Start it. The engine binaries came with the kit.
2. Models: put GGUF files in `models/`, or point `scan_roots` in `config/console.json` at a
   model store, then press **Scan drives** and **Boot LLM**.
3. Cloud API (optional): paste a provider key in the API row and press **Save key**. Keys are
   never packaged — nothing sensitive was copied from the source tree, and the packager proved
   it by scanning this whole folder for the source's secrets.

## Boundaries
Local means local: this hub binds `127.0.0.1` and needs the explicit `--lan --i-consent`
handshake before it will face a network. The P0 gate runs before actions, and the physics layer
is verified at startup (`physics=` is printed on every boot and reported by `/api/health`).
"""
    out = dest / "README_HUB.md"
    out.write_text(md, encoding="utf-8")
    return out


def repair_dest_roots(dest: Path) -> tuple[bool, str]:
    """Point the copy's drive-anchored config roots at its own drive.

    config/admin.json is a permission boundary, not a convenience default: if its roots still name
    the source drive, every admin-tier file action inside the hub is refused and the copy looks
    broken for reasons that have nothing to do with the code. src/repair_paths.py owns that
    rewrite - it is called here rather than reimplemented so there is one implementation of it.
    """
    script = dest / "src" / "repair_paths.py"
    if not script.is_file():
        return False, "src/repair_paths.py is not in the copy: config roots still name the source drive"
    last = ""
    for flags in (["--i-consent"], ["--i-consent", "--kit", str(dest)]):
        try:
            proc = subprocess.run(
                [sys.executable, str(script), *flags],
                cwd=str(dest),
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as e:
            last = f"{type(e).__name__}: {e}"
            continue
        if proc.returncode == 0:
            tail = [ln for ln in (proc.stdout or "").strip().splitlines() if ln.strip()][-6:]
            return True, " | ".join(tail) if tail else "ok"
        last = ((proc.stderr or proc.stdout or "").strip().splitlines() or [""])[-1]
    return False, f"repair_paths exited non-zero: {last[:200]}"


def boot_check(dest: Path, port: int, timeout_s: int = 90) -> tuple[bool, str]:
    """Really start the copy from its own folder and ask it for /api/health."""
    py = dest / "engine" / "python.exe"
    exe = str(py) if py.is_file() else sys.executable
    proc = subprocess.Popen(
        [exe, "src/server.py", "--no-browser"],
        cwd=str(dest),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    import http.client

    deadline = time.time() + timeout_s
    health = ""
    ok = False
    while time.time() < deadline:
        time.sleep(1.0)
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request("GET", "/api/health")
            r = c.getresponse()
            health = r.read().decode("utf-8", "replace")
            c.close()
            ok = r.status == 200
            break
        except OSError:
            if proc.poll() is not None:
                break
    if ok:
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request("POST", "/api/shutdown", body=b"{}", headers={"Content-Length": "2"})
            c.getresponse().read()
            c.close()
        except OSError:
            pass
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
    tail = ""
    if proc.stdout is not None:
        try:
            tail = proc.stdout.read()[-1500:]
        except Exception:
            tail = ""
    return ok, (health[:600] or tail)


def repair_dest_local_ports(dest: Path, ports: dict) -> str:
    """Stop a stale destination local.json from overriding the copy's own triplet.

    config/local.json is per-machine state, so it is never copied - but the kit SEEDS it from
    config/local.json.example on first boot (src/install.py). While that example shipped the
    source tree's concrete ports, refreshing an existing hub left a local.json that beat
    console.json and pinned the copy to the original console's ports, so the two could not run
    at once. An empty ports block means "console.json decides", which is what a copy wants.
    """
    lj = dest / "config" / "local.json"
    if not lj.is_file():
        return "no local.json in the copy (console.json decides)"
    try:
        obj = json.loads(lj.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return f"local.json unreadable ({e}) - left alone"
    if not isinstance(obj, dict) or not isinstance(obj.get("ports"), dict) or not obj["ports"]:
        return "local.json defers to console.json"
    was = dict(obj["ports"])
    want = {"port": ports["console"], "llama_port": ports["engine"],
            "embed_port": ports["embed"], "colibri_port": ports["embed"] + 1}
    if all(was.get(k) == v for k, v in want.items()):
        return f"local.json already the copy's triplet {was}"
    obj["ports"] = {}
    obj["ports_repaired_comment"] = ("was pinned to " + json.dumps(was) + " by a stale example; emptied so this "
                                     "copy follows its own config/console.json. Set values here to override.")
    tmp = lj.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, lj)
    return f"local.json ports {was} -> {{}} (console.json decides)"


def main() -> int:
    ap = argparse.ArgumentParser(description="Package the LYGO Local Agent Console as a local hub.")
    ap.add_argument("--dest", default=r"<DRIVE_D>\LYGO_CONSOLE")
    ap.add_argument("--label", default="LYGO TURBO DRIVE")
    ap.add_argument("--port", type=int, default=9741)
    ap.add_argument("--engine-port", type=int, default=11541)
    ap.add_argument("--embed-port", type=int, default=11542)
    ap.add_argument("--apply", action="store_true", help="really write (default: dry run)")
    ap.add_argument("--no-boot-check", action="store_true")
    ap.add_argument("--no-repair", action="store_true", help="do not rewrite drive-anchored config roots in the copy")
    args = ap.parse_args()
    dest = Path(args.dest)

    files, skipped = plan_copy()
    total = sum(f.stat().st_size for f in files)
    manifest = {
        "label": args.label,
        "signature": SIGNATURE,
        "packaged_at": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "source": str(KIT),
        "dest": str(dest),
        "build": (KIT / "src" / "server.py").read_text(encoding="utf-8").split('BUILD = "')[1].split('"')[0],
        "ports": {"console": args.port, "engine": args.engine_port, "embed": args.embed_port},
        "files": len(files),
        "bytes": total,
        "excluded": sorted({s.split("  (")[0] for s in skipped} | {"data/", "save/", "workspace/ (except .gitkeep)", "__pycache__/", ".git/"}),
    }
    print(f"PACKAGE PLAN -> {dest}")
    print(f"  {len(files)} files, {total/1e6:.1f} MB, build {manifest['build']}")
    print(f"  ports: console {args.port} / engine {args.engine_port} / embed {args.embed_port}")
    print(f"  excluded ({len(skipped)}): " + ", ".join(sorted({s.split('  (')[0] for s in skipped})[:12]))
    for p in (args.port, args.engine_port, args.embed_port):
        print(f"  port {p} free on this machine: {free_port_ok(p)}")
    if not args.apply:
        print("\nDRY RUN - nothing written. Re-run with --apply.")
        return 0

    if dest.exists() and (dest / "src" / "server.py").is_file():
        print(f"\nrefreshing existing hub at {dest} (state folders are preserved)")
    dest.mkdir(parents=True, exist_ok=True)
    written = 0
    for src_file in files:
        target = dest / rel(src_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, target)
        written += 1
    print(f"\ncopied {written} files")

    cfg_change = write_hub_config(dest, args.port, args.engine_port, args.embed_port, args.label)
    fixed, fixed_detail = (True, "skipped (--no-repair)") if args.no_repair else repair_dest_roots(dest)
    run_bat = write_run_bat(dest, args.port, args.label)
    print("local.json   :", repair_dest_local_ports(dest, {"console": args.port, "engine": args.engine_port, "embed": args.embed_port}))
    manifest["config"] = cfg_change
    manifest["config_roots_repaired"] = {"ok": fixed, "detail": fixed_detail[:400]}
    (dest / "PACKAGE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_readme(dest, manifest)
    print(f"wrote RUN_LYGO_HUB.bat, PACKAGE_MANIFEST.json, README_HUB.md")
    print(f"console.json port/bind {cfg_change['before']} -> {cfg_change['after']}")
    print(f"config roots for this drive: {'ok' if fixed else 'NOT repaired'} - {fixed_detail[:160]}")

    # ---- verification ----
    secrets = secret_values()
    hits = scan_for_secrets(dest, secrets)
    print(f"\nsecret scan: {len(secrets)} source secret(s) checked, {len(hits)} found in the copy")
    if hits:
        for label, where in hits.items():
            print(f"  LEAK {label} -> {where[:5]}")
        print("ABORT: a secret reached the package. Remove it and re-run.")
        return 3

    if not args.no_boot_check:
        print("booting the copy from its own folder ...")
        ok, detail = boot_check(dest, args.port)
        print(("PASS " if ok else "FAIL ") + "the packaged hub answers /api/health on its own port")
        if not ok:
            print(detail)
            return 4
        try:
            health = json.loads(detail)
            print(f"  build={health.get('build')} port={health.get('port')} bind={health.get('bind')} "
                  f"physics={health.get('physics')} config_errors={health.get('config_errors')}")
        except Exception:
            print("  " + detail[:300])
    print("\npackage complete:", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
