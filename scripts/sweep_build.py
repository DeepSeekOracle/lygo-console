#!/usr/bin/env python3
"""Bug sweep for this kit, re-runnable. Static pass, plus a live smoke when asked.

The 1.2.2 release was closed by a debug pass over the tree that found six defects; this is that pass
with the findings written down instead of remembered. Every check prints its own line so a run can be
quoted, and the exit code is 0 only when nothing is wrong.

    python scripts/sweep_build.py              # static only
    python scripts/sweep_build.py --live       # static + a live smoke (console must be up)
    python scripts/sweep_build.py --port 9641
"""

from __future__ import annotations

import argparse
import ast
import builtins
import json
import py_compile
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TREES = ("src", "tests", "scripts", "tools")

problems: list[str] = []
notes: list[str] = []


def say(kind: str, msg: str) -> None:
    print(f"  [{kind}] {msg}")
    if kind in ("FAIL", "ERROR"):
        problems.append(msg)
    else:
        notes.append(msg)


# ---------------------------------------------------------------- static passes

def sweep_syntax() -> None:
    print("= syntax =")
    bad = 0
    for tree in TREES:
        for p in sorted((ROOT / tree).rglob("*.py")):
            try:
                py_compile.compile(str(p), doraise=True, cfile=str(p) + ".swp")
            except py_compile.PyCompileError as exc:
                bad += 1
                say("FAIL", f"{p.relative_to(ROOT)}: {str(exc).splitlines()[-1][:120]}")
            finally:
                swp = Path(str(p) + ".swp")
                if swp.exists():
                    swp.unlink()
    if not bad:
        say(" ok ", f"every .py compiles in {'/'.join(TREES)}")


def sweep_encoding() -> None:
    print("= encoding =")
    bad = 0
    for tree in TREES:
        for p in sorted((ROOT / tree).rglob("*.py")):
            try:
                raw = p.read_bytes()
                raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                bad += 1
                say("FAIL", f"{p.relative_to(ROOT)} is not UTF-8: {exc}")
    if not bad:
        say(" ok ", "every source file decodes as UTF-8")


def _names_defined(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            out.add(node.id)
        elif isinstance(node, ast.arg):
            out.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                out.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            out.add(node.name)
    return out


def sweep_duplicates() -> None:
    print("= duplicate definitions (last one silently wins) =")
    found = 0
    for tree in TREES:
        for p in sorted((ROOT / tree).rglob("*.py")):
            try:
                tree_ast = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            seen: dict[str, int] = {}
            for node in tree_ast.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name in seen:
                        found += 1
                        say("FAIL", f"{p.relative_to(ROOT)}:{node.lineno} defines {node.name} "
                                    f"again (first at line {seen[node.name]})")
                    seen[node.name] = node.lineno
    if not found:
        say(" ok ", "no module defines the same top-level name twice")


def sweep_undefined_names() -> None:
    """A light Name check: catches the class of defect the 1.2.2 sweep found (dead code with
    undefined names). Deliberately conservative - module-level names only, no attribute guessing."""
    print("= undefined names =")
    allowed = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__", "__spec__"}
    flagged = 0
    for tree in TREES:
        for p in sorted((ROOT / tree).rglob("*.py")):
            try:
                tree_ast = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            defined = _names_defined(tree_ast) | allowed
            # names referenced inside string-typed annotations and f-strings are covered by the walk
            used = {n.id for n in ast.walk(tree_ast)
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            missing = sorted(used - defined)
            # A name defined in ANOTHER module of this tree is out of this file's scope but is not a
            # typo, so only report names defined nowhere but this file. (The first version of this
            # check unioned the whole tree - including this file - which made it incapable of ever
            # finding anything.)
            if missing:
                elsewhere = _everywhere_except(p)
                real = [m for m in missing if m not in elsewhere]
                if real:
                    flagged += 1
                    say("FAIL", f"{p.relative_to(ROOT)}: possibly undefined {', '.join(real[:6])}")
    if not flagged:
        say(" ok ", "no name is used without being defined in the tree")


_everywhere_cache: dict[str, set[str]] = {}


def _everywhere_except(skip: Path) -> set[str]:
    """Every name defined anywhere in the tree EXCEPT in `skip`."""
    key = str(skip)
    if key not in _everywhere_cache:
        allnames: set[str] = set()
        for p in ROOT.rglob("*.py"):
            if p == skip:
                continue
            try:
                allnames |= _names_defined(ast.parse(p.read_text(encoding="utf-8", errors="replace")))
            except Exception:
                continue
        _everywhere_cache[key] = allnames
    return _everywhere_cache[key]


def sweep_bare_except() -> None:
    print("= bare except (hides every failure, including KeyboardInterrupt) =")
    found = 0
    for tree in TREES:
        for p in sorted((ROOT / tree).rglob("*.py")):
            try:
                tree_ast = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree_ast):
                if isinstance(node, ast.ExceptHandler) and node.type is None:
                    found += 1
                    say("note", f"{p.relative_to(ROOT)}:{node.lineno} bare except")
    say(" ok " if not found else "note", f"{found} bare except(s)")


def sweep_markers() -> None:
    print("= TODO / FIXME / XXX / HACK =")
    pat = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
    hits = []
    self_name = Path(__file__).name
    for tree in TREES:
        for p in sorted((ROOT / tree).rglob("*.py")):
            if p.name == self_name:
                continue  # this scanner names the markers it looks for
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if pat.search(line) and not line.strip().startswith("#!"):
                    hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:110]}")
    for h in hits[:20]:
        print("     ", h)
    say(" ok ", f"{len(hits)} marker(s)")


def sweep_orphans() -> None:
    print("= modules nobody imports =")
    src = ROOT / "src"
    mods = {p.stem: p for p in sorted(src.glob("*.py"))}
    blob = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in ROOT.rglob("*.py"))
    for name, p in mods.items():
        if name.startswith("__"):
            continue
        if len(re.findall(rf"\b{re.escape(name)}\b", blob)) <= 1:
            say("note", f"src/{p.name} is not referenced anywhere (orphan?)")
    say(" ok ", "orphan scan done")


def sweep_version_consistency() -> None:
    print("= version =")
    ver = (ROOT / "VERSION").read_text(encoding="utf-8", errors="replace").splitlines()
    number = (ver[0] if ver else "").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", number):
        say("FAIL", f"VERSION first line is not x.y.z: {number!r}")
    else:
        say(" ok ", f"VERSION = {number}")
    # the published manifests must agree with the tree
    for man in ("BUILD_MANIFEST.json", "SESSIONS_MANIFEST.json", "BRAND_MANIFEST.json"):
        p = ROOT / man
        if not p.exists():
            say("note", f"{man} missing")
            continue
        try:
            json.loads(p.read_text(encoding="utf-8"))
            say(" ok ", f"{man} parses")
        except Exception as exc:
            say("FAIL", f"{man} does not parse: {exc}")


def sweep_stale_manifests() -> None:
    print("= manifest freshness (delegated to certify) ==")
    cert = ROOT / "scripts" / "certify_build.py"
    if not cert.exists():
        say("note", "scripts/certify_build.py missing")
        return
    import subprocess
    r = subprocess.run([sys.executable, str(cert)], cwd=str(ROOT), capture_output=True, text=True,
                       timeout=180)
    verdict = [l for l in (r.stdout or "").splitlines() if "VERDICT" in l]
    if r.returncode == 0 and verdict:
        say(" ok ", verdict[0].strip()[:100])
    else:
        say("FAIL", f"certify_build returned {r.returncode}: "
                    f"{(verdict or ['no verdict line'])[0][:140]}")


# ------------------------------------------------------------------ live smoke

def live_smoke(port: int) -> None:
    base = f"http://127.0.0.1:{port}"
    print(f"= live smoke on {base} =")

    def call(path, body=None, timeout=120):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))

    try:
        code, health = call("/api/health", timeout=20)
        say(" ok " if code == 200 else "FAIL", f"GET /api/health -> {code}")
    except Exception as exc:
        say("FAIL", f"GET /api/health: {exc!r}")
        return
    ver_file = (ROOT / "VERSION").read_text(encoding="utf-8").splitlines()[0].strip()
    live_ver = str(health.get("version") or health.get("build") or "")
    say(" ok " if ver_file in live_ver or not live_ver else "FAIL",
        f"health reports version {live_ver!r}, VERSION says {ver_file!r}")

    try:
        code, cloud = call("/api/cloud", timeout=20)
        say(" ok " if cloud.get("keys_wired") else "FAIL",
            f"GET /api/cloud -> keys_wired={cloud.get('keys_wired')} provider={cloud.get('provider')} "
            f"enabled={cloud.get('enabled')}")
        if cloud.get("chain_label"):
            say(" ok ", f"walk record: {cloud.get('chain_label')}")
    except Exception as exc:
        say("FAIL", f"GET /api/cloud: {exc!r}")

    # one local turn, end to end
    try:
        t0 = time.time()
        code, j = call("/api/chat", {"messages": [{"role": "user", "content": "Reply with exactly: SWEEP OK"}],
                                     "stream": False}, timeout=300)
        text = str(j.get("text") or "").strip()
        secs = round(time.time() - t0, 1)
        say(" ok " if "SWEEP OK" in text else "FAIL",
            f"local turn {code} {secs}s answer={text[:60]!r} brain={j.get('brain')}")
    except Exception as exc:
        say("FAIL", f"local turn: {exc!r}")

    # The image limb, through the route that dispatches limbs.
    try:
        code, j = call("/api/limb", {"name": "image_list", "arguments": {}}, timeout=60)
        files = j.get("files") if isinstance(j, dict) else None
        say(" ok " if (isinstance(files, list)) else "FAIL",
            f"image limb via /api/limb -> {code}, {len(files or [])} picture(s) on disk")
    except Exception as exc:
        say("FAIL", f"image limb: {exc!r}")

    # The session archive route.
    try:
        code, j = call("/api/archive", timeout=60)
        say(" ok " if isinstance(j, dict) and j.get("ok") else "FAIL",
            f"session archive -> {code}, {len(j.get('sessions') or [])} session(s)")
    except Exception as exc:
        say("FAIL", f"session archive: {exc!r}")

    # The forever history (the filed conversation windows) lives on disk, not on a route.
    root = ROOT / "workspace" / "memory" / "conversations"
    filed = sorted(root.rglob("*.md")) if root.exists() else []
    index = (root / "INDEX.md").exists() if root.exists() else False
    total = sum(f.stat().st_size for f in filed)
    say(" ok " if filed else "FAIL",
        f"forever history: {len(filed)} conversation file(s), {total:,} bytes, INDEX.md={index}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--port", type=int, default=9641)
    a = ap.parse_args()
    print(f"sweep of {ROOT}")
    sweep_syntax()
    sweep_encoding()
    sweep_duplicates()
    sweep_undefined_names()
    sweep_bare_except()
    sweep_markers()
    sweep_orphans()
    sweep_version_consistency()
    sweep_stale_manifests()
    if a.live:
        live_smoke(a.port)
    print()
    fails = len(problems)
    print(f"SWEEP RESULT: {fails} failure(s), {len(notes)} note(s)")
    for p in problems:
        print(f"  FAIL {p}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
