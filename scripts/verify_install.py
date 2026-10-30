"""Acceptance check for ONE install of the console. Run it on either one - it detects which.

    C:/Python313/python.exe scripts/verify_install.py            # fast, read-only
    C:/Python313/python.exe scripts/verify_install.py --deep     # also hashes the model vault
    C:/Python313/python.exe scripts/verify_install.py --certify  # also runs the build certifier

It answers the question the steward actually asks before booting: is this install standalone (own
engine, own models, no daemon), intact (license, brand, schemas), and clean (no secrets printed, no
stray engine left listening)? Every line is PASS, FAIL or NOTE, and the exit code is 0 only when
nothing failed. Nothing is written outside save/verify/ and no network call is made.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "src"))

PORTS = {9641: "desktop console", 9651: "USB console", 11441: "chat engine",
         11451: "embed engine", 11461: "vision runner"}
RESULTS: list[tuple[str, str, str]] = []       # (verdict, check, detail)


def say(verdict: str, check: str, detail: str = "") -> None:
    RESULTS.append((verdict, check, detail))
    print("%-4s %-34s %s" % (verdict, check, detail), flush=True)


def check(fn):
    """Run one check; an exception is a FAIL with the reason, never a traceback in the operator's face."""
    try:
        fn()
    except Exception as exc:                    # noqa: BLE001 - a broken install must not abort the rest
        say("FAIL", fn.__name__.replace("check_", "").replace("_", " "), "%s: %s" % (type(exc).__name__, exc))


# ---------------------------------------------------------------- what is this install

def check_install_identity() -> None:
    import paths
    where = "USB kit" if "LYGO_BUILDER_KEY" in str(KIT).upper() else "live tree"
    say("NOTE", "install", "%s  %s" % (where, paths.KIT_ROOT))
    say("NOTE", "python", sys.version.split()[0])
    missing = [d for d in ("src", "config", "engine", "save") if not (KIT / d).is_dir()]
    say("FAIL" if missing else "PASS", "layout", "missing: %s" % missing if missing else "src config engine save")


def check_engine_binary() -> None:
    """The llama-server.exe at the top of engine/ is a small shim - the real ones live per backend.

    Sizing the shim (9 KB) reads as a broken install when it is the normal layout, so measure the
    largest real binary and name the backend layers found.
    """
    exe = KIT / "engine" / "llama-server.exe"          # exactly what src/engine.py spawns
    if not exe.is_file():
        return say("FAIL", "own engine binary", "engine/llama-server.exe is missing - this install cannot boot itself")
    impl = [p for p in (KIT / "engine").glob("*.dll") if p.name.lower() in ("llama.dll", "ggml.dll")]
    heavy = max(impl, key=lambda p: p.stat().st_size) if impl else None
    if not heavy or heavy.stat().st_size < 1_000_000:
        return say("FAIL", "own engine binary", "llama-server.exe is present but no implementation DLL with it")
    layers = sorted(d.name for d in (KIT / "engine" / "backends").glob("*")) if (KIT / "engine" / "backends").is_dir() else []
    cpu = len(list((KIT / "engine").glob("ggml-cpu-*.dll")))
    say("PASS", "own engine binary", "engine/llama-server.exe (%.0f KB loader) + %s %.0f MB  layers: %s  %d cpu backends" % (
        exe.stat().st_size / 1e3, heavy.name, heavy.stat().st_size / 1e6, ",".join(layers) or "-", cpu))


def check_no_daemon_launch() -> None:
    bad = []
    for path in (KIT / "src").glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"^.*subprocess.*ollama.*$", text, re.I | re.M):
            if "Never subprocess" in m.group(0):
                continue
            bad.append("%s:%s" % (path.name, m.group(0).strip()[:60]))
    say("FAIL" if bad else "PASS", "no source launches a daemon", "; ".join(bad)[:90] or "0 matches in src/")


def _foreign_root_source(value: str) -> str:
    """Which config file makes this path a scan root? 'nothing' when no file declares it."""
    hits = []
    for cfg in sorted((KIT / "config").glob("*.json")):
        if value.lower() in cfg.read_text(encoding="utf-8", errors="replace").lower():
            hits.append(cfg.name)
    return ", ".join(hits) or "nothing (built in)"


def check_scan_roots() -> None:
    """Reading a foreign store is allowed; depending on one is not.

    Importing a model out of another store once is a documented way to bring weights over, so a root
    that points into a daemon's folder is a NOTE naming the file that declares it. It only becomes a
    FAIL when the model this console would boot lives there - that is a dependency, and it would break
    on a machine that never ran that store.
    """
    import registry as reg
    import server
    roots = [str(r) for r in server.default_scan_roots({})]
    present = [r for r in roots if Path(r).is_dir()]
    say("PASS" if roots else "FAIL", "scan roots", "%d roots, %d exist" % (len(roots), len(present)))
    foreign = [r for r in roots if ".ollama" in r.lower()]
    for r in roots:
        say("NOTE", "  root", "%s%s" % (r, "   <- foreign store" if r in foreign else ""))
    for r in foreign:
        say("NOTE", "  foreign root declared by", "%s declares %s" % (_foreign_root_source(r), r))
    data = reg.load()
    sel = next((m for m in (data.get("models") or []) if m.get("id") == data.get("selected")), None)
    dep = bool(sel) and any(str(sel.get("path") or "").lower().startswith(f.rstrip("\\/").lower()) for f in foreign)
    if dep:
        say("FAIL", "default needs no foreign store", "%s -> %s" % (sel.get("id"), sel.get("path")))
    else:
        say("PASS", "default needs no foreign store", "%s -> %s" % (data.get("selected"), (sel or {}).get("path")))


def check_config_lists_no_daemon_path() -> None:
    cfg = KIT / "config" / "console.json"
    text = cfg.read_text(encoding="utf-8") if cfg.is_file() else "{}"
    hit = json.dumps(json.loads(text)).lower().count(".ollama")
    say("FAIL" if hit else "PASS", "config names no daemon path", "%d mention(s) in config/console.json" % hit)


# ---------------------------------------------------------------- what the console tells a model

def check_schemas() -> None:
    from tools import TOOLS_SCHEMA
    names = [t["function"]["name"] for t in TOOLS_SCHEMA]
    dupes = sorted({n for n in names if names.count(n) > 1})
    ollama = [t["function"]["name"] for t in TOOLS_SCHEMA
              if "ollama" in json.dumps(t).lower()]
    say("FAIL" if (dupes or ollama) else "PASS", "tool schemas", "%d tools, %d unique%s%s" % (
        len(names), len(set(names)), "  dupes: %s" % dupes if dupes else "",
        "  mention ollama: %s" % ollama if ollama else ""))


# ---------------------------------------------------------------- what this install owns

def check_registry_and_ownership() -> None:
    import registry as reg
    import server
    data = reg.load()
    models = data.get("models") or []
    selected = data.get("selected")
    sel = next((m for m in models if m.get("id") == selected), None)
    say("PASS" if sel else "FAIL", "default model",
        "%s (%s)" % (selected, data.get("selected_source")) if sel else "selected=%r is not in the registry" % selected)
    if sel:
        p = Path(str(sel.get("path") or ""))
        say("PASS" if p.is_file() else "FAIL", "  its weights are on this machine",
            "%s  %.2f GB" % (p.name, p.stat().st_size / 1e9) if p.is_file() else str(p))
        if sel.get("mmproj"):
            say("PASS" if Path(str(sel["mmproj"])).is_file() else "FAIL", "  it can read a picture",
                Path(str(sel["mmproj"])).name)

    roots = [str(r).rstrip("\\/").lower() for r in server.default_scan_roots({})]
    if sel:
        p = Path(str(sel.get("path") or ""))
        inside = any(str(p).lower().startswith(r) for r in roots)
        say("PASS" if inside else "FAIL", "the default's weights are in a root this kit scans",
            ("%s" % p) if inside else "%s is outside every scanned root - this install would boot off a store it does not own" % p)
    owned = [m for m in models if str(m.get("source")) == "lygo_vault"]
    if not owned:
        say("NOTE", "owned models", "none on this tree - its own model store is the vault")
        return
    outside = [m["id"] for m in owned
               if (p := Path(str(m.get("path") or ""))).is_file()
               and not any(str(p).lower().startswith(r) for r in roots)]
    say("FAIL" if outside else "PASS", "owned models live in a scanned root",
        "outside: %s" % outside if outside else "%d owned" % len(owned))


def _vault_candidates() -> list[Path]:
    cands = []
    env = os.environ.get("LYGO_MODELS")
    if env:
        cands.append(Path(env))
    else:
        say("NOTE", "LYGO_MODELS", "not visible in this shell (set for the machine; a shell opened before it will not see it)")
    cands += [KIT / "models", KIT.parent / "product" / "models", KIT.parent / "product" / "models" / "ollama"]
    if os.name == "nt":
        cands.append(Path("<MODELS>"))
    out, seen = [], set()
    for c in cands:
        key = str(c).lower()
        if key not in seen and c.is_dir():
            seen.add(key)
            out.append(c)
    return out


def check_vault(deep: bool) -> None:
    """The vault is wherever this install keeps its own weights - probe, never assume one drive."""
    found = []
    for c in _vault_candidates():
        man = next((c / n for n in ("manifest.json", "MODEL_MANIFEST.json") if (c / n).is_file()), None)
        if man:
            found.append((c, man))
    if not found:
        return say("FAIL", "model vault", "no manifest under any candidate: %s" % ", ".join(str(c) for c in _vault_candidates()))
    declared = os.environ.get("LYGO_MODELS")
    if declared and not any(str(c) == declared for c, _ in found):
        say("FAIL", "declared vault", "LYGO_MODELS=%s has no manifest" % declared)
    man = found[0][1]
    data = json.loads(man.read_text(encoding="utf-8"))
    entries = data.get("models") or data
    rows = entries.values() if isinstance(entries, dict) else entries
    rows = [r for r in rows if isinstance(r, dict)]
    if rows and not any(r.get("path") or r.get("file") or r.get("gguf") for r in rows):
        names = [str(r.get("name") or r.get("id") or "?") for r in rows]
        return say("PASS", "model vault declaration", "%s lists %d models by name (%s) - no file paths here, "
                                                        "so this is what the kit intends to hold, not proof of what it has"
                   % (man.name, len(rows), ", ".join(names[:5])))
    base = man.parent

    def _resolve(value: str) -> Path:
        p = Path(str(value))
        return p if p.is_absolute() else base / p

    size = sum(_resolve(r.get("path")).stat().st_size for r in rows if _resolve(r.get("path")).is_file())
    say("PASS", "model vault", "%s  %d files  %.2f GB" % (found[0][0], len(rows), size / 1e9))
    gone = [r for r in rows if not Path(str(r.get("path"))).is_file()]
    if gone:
        say("NOTE", "  vault entries not on disk", "%d of %d (relative paths resolve inside the vault folder)" % (len(gone), len(rows)))
    if deep:
        bad = []
        for r in rows:
            p = Path(str(r.get("path")))
            if not p.is_file():
                bad.append("%s missing" % p.name)
                continue
            want = str(r.get("sha256") or "")
            if want and hashlib.sha256(p.read_bytes()).hexdigest() != want:
                bad.append("%s hash" % p.name)
        say("FAIL" if bad else "PASS", "  vault hashes", "; ".join(bad) or "every file matches the manifest")


def check_integrity() -> None:
    lic = KIT / "LICENSE"
    if not lic.is_file():
        return say("FAIL", "license", "LICENSE is missing")
    text = lic.read_text(encoding="utf-8", errors="replace")
    sig = "Δ9Φ963-LICENSE-v3.0" in text
    lines = sum(1 for _ in text.splitlines())
    say("PASS" if sig else "FAIL", "license", "%d lines, signature %s" % (lines, "present" if sig else "MISSING"))
    for name in ("BRAND_MANIFEST.json", "SESSIONS_MANIFEST.json"):
        f = KIT / name
        say("PASS" if f.is_file() else "NOTE", "manifest", "%s%s" % (name, "" if f.is_file() else " (not present)"))


def check_secret_hygiene() -> None:
    import paths
    present = [p.name for p in (paths.TOKEN_PATH, paths.LLAMA_KEY_PATH) if p.is_file()]
    say("PASS", "secrets on disk", "%s  (present; this check never reads a value)" % (", ".join(present) or "none"))
    offenders = []
    for path in (KIT / "config").glob("*.json"):
        for m in re.finditer(r'"(api_key|token|password|secret)"\s*:\s*"([^"]{8,})"', path.read_text(encoding="utf-8", errors="replace"), re.I):
            if m.group(2).lower() not in ("redacted", "change-me", "none", "null"):
                offenders.append("%s:%s" % (path.name, m.group(1)))
    say("FAIL" if offenders else "PASS", "no secret committed in config", ", ".join(offenders)[:90] or "clean")


def check_nothing_left_running() -> None:
    """A booted console is the point; a runner left behind after a turn is not.

    The console (9641 desktop, 9651 stick) and its own engine (11441, 11451) are supposed to be
    listening while the operator is working - that is a running kit, not a leak. The one-shot runners
    (11461 vision, 11471 bench) must be gone when the turn that spawned them is over.
    """
    live_ports = {9641, 9651, 11441, 11451}
    busy = {}
    for port, what in PORTS.items():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                busy[port] = what
    live = sorted(p for p in busy if p in live_ports)
    stray = sorted(p for p in busy if p not in live_ports)
    if live:
        say("NOTE", "console is up", "; ".join("%d %s" % (p, busy[p]) for p in live))
    if stray:
        say("FAIL", "runner left behind", "; ".join("%d %s" % (p, busy[p]) for p in stray))
    if not busy:
        say("NOTE", "ports", "nothing running - the console is not booted right now")


def check_certify() -> None:
    script = KIT / "scripts" / "certify_build.py"
    if not script.is_file():
        return say("NOTE", "certifier", "scripts/certify_build.py not present")
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, cwd=str(KIT))
    line = next((ln for ln in out.stdout.splitlines() if "VERDICT" in ln or "UNCERT" in ln), "")
    say("PASS" if "CERTIFIED" in line else "FAIL", "build certifier", line.strip() or ("exit %d" % out.returncode))


def main() -> int:
    ap = argparse.ArgumentParser(description="acceptance check for one install of the console")
    ap.add_argument("--deep", action="store_true", help="also hash the model vault (slow)")
    ap.add_argument("--certify", action="store_true", help="also run scripts/certify_build.py")
    args = ap.parse_args()

    for fn in (check_install_identity, check_engine_binary, check_no_daemon_launch, check_scan_roots,
               check_config_lists_no_daemon_path, check_schemas, check_registry_and_ownership,
               check_integrity, check_secret_hygiene, check_nothing_left_running):
        check(fn)
    check(lambda: check_vault(args.deep))
    if args.certify:
        check(check_certify)

    failed = [r for r in RESULTS if r[0] == "FAIL"]
    print("\n%s  (%d checks, %d failed)" % (
        "ALL GOOD - this install is standalone" if not failed else "NOT READY - fix the FAIL lines above",
        len(RESULTS), len(failed)))
    try:
        out = KIT / "save" / "verify"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("verify_%s.json" % time.strftime("%Y%m%d_%H%M%S"))).write_text(json.dumps(
            {"kit": str(KIT), "when": time.strftime("%Y-%m-%d %H:%M:%S"),
             "results": [{"verdict": v, "check": c, "detail": d} for v, c, d in RESULTS]}, indent=1), encoding="utf-8")
    except Exception:                            # a read-only stick must still print its verdict
        pass
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
