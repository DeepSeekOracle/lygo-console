#!/usr/bin/env python3
"""certify_build.py - is this copy a LYGO Certified Build, or a modified one?

Run from the kit root (the folder holding LICENSE and this kit's manifests):

    python scripts/certify_build.py
    python scripts/certify_build.py --quiet      # verdict only
    python scripts/certify_build.py --json       # machine-readable

What it does
------------
1. Brand + license identity: confirms LICENSE / NOTICE / TRADEMARKS.md / LICENSING.md are present,
   that LICENSE carries the v3.0 resonance signature, and that the NOTICE still claims the marks.
2. Integrity: reads every *_MANIFEST.json in the kit root that lists shipped files as
   {path, sha256_16, bytes} entries and recomputes each hash + size from disk. Line endings are
   normalised to LF before hashing: a raw-byte comparison would flag every Windows checkout
   (core.autocrlf=true) as MODIFIED even though nothing changed.
3. Verdict: CERTIFIED (everything matches) or MODIFIED (list of what differs).

Why it matters
--------------
The license (LYGO Sovereign License v3.0, sections 4 and 9) says a build whose hashes no longer match
its published manifest is UNCERTIFIED: it must not be resold, and must not keep the LYGO marks as if
the Steward endorsed it. This script is the cheap, honest test anyone - the Steward, a buyer, a
reviewer - can run on a copy before trusting or reselling it.

Read-only. Writes nothing. Prints no file contents (no secrets).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# Line-ending bytes, written via codes so no escaping layer can mangle them:
CRLF = bytes((13, 10))
LF = bytes((10,))  # note the comma: bytes((10)) would be ten NUL bytes, not a newline

KIT = Path(__file__).resolve().parent.parent
SIGNATURE = "\u03949\u03a6963-LICENSE-v3.0"
BRAND_FILES = ["LICENSE", "NOTICE", "TRADEMARKS.md", "LICENSING.md", "SUCCESSION.md"]
MARKS = ["LYGO", "\u03949\u03a6963", "Justin Helmer"]


def identity(path: Path) -> tuple[str, int]:
    """Content identity of a shipped file: (sha256 over LF-normalised bytes, that length).

    Line endings are a checkout detail, not part of the build's identity. With core.autocrlf=true
    (the Windows default) git rewrites text files to CRLF whenever it checks them out - after a
    clone, a rebase with autostash, or a restore - so hashing raw bytes makes a pristine copy read
    MODIFIED for no reason. Normalising CRLF -> LF first means a certified build verifies on
    Windows, Linux and macOS alike, while any real edit (a changed word, a stripped notice, a
    rebranded surface) still changes the hash.
    """
    data = path.read_bytes()
    if CRLF in data:
        data = data.replace(CRLF, LF)
    return hashlib.sha256(data).hexdigest(), len(data)


def check_code_loads(kit: Path) -> tuple[bool, list[str]]:
    """Byte-compile src/ (and any .py under portal/). A hash match on unimportable code is not a certificate.

    D1: certify used to print CERTIFIED BUILD while chat_loop.py could not even import.
    """
    notes: list[str] = []
    src = kit / "src"
    if not src.is_dir():
        return True, ["no src/ tree in this kit"]
    import compileall
    import py_compile

    ok = True
    if not compileall.compile_dir(str(src), quiet=1, force=False):
        ok = False
        notes.append("src/ failed python -m compileall (a module does not byte-compile)")
    portal = kit / "portal"
    if portal.is_dir():
        for py in portal.rglob("*.py"):
            try:
                py_compile.compile(str(py), doraise=True)
            except py_compile.PyCompileError as exc:
                ok = False
                notes.append(f"portal compile fail {py.name}: {exc}")
    import subprocess

    src_s = str(src)
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import chat_loop, tools, limbs, engine, registry, p0_hook"
    )
    r = subprocess.run(
        [sys.executable, "-c", probe, src_s],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode != 0:
        ok = False
        err = (r.stderr or r.stdout or "").strip().splitlines()
        tail = err[-1] if err else f"exit {r.returncode}"
        notes.append(f"src import probe failed: {tail[:300]}")
    if ok:
        notes.append("src/ byte-compiles and entry modules import")
    return ok, notes


def check_brand() -> tuple[bool, list[str]]:
    notes: list[str] = []
    ok = True
    for name in BRAND_FILES:
        p = KIT / name
        if not p.is_file():
            ok = False
            notes.append(f"MISSING  {name} (license package incomplete)")
        else:
            notes.append(f"present  {name}  ({p.stat().st_size} bytes)")
    lic = KIT / "LICENSE"
    if lic.is_file():
        text = lic.read_text(encoding="utf-8", errors="replace")
        if SIGNATURE in text:
            notes.append(f"license  signature {SIGNATURE} found")
        else:
            ok = False
            notes.append(f"ALERT    LICENSE does not carry {SIGNATURE} - wrong or edited license")
        for mark in MARKS:
            if mark not in text:
                ok = False
                notes.append(f"ALERT    LICENSE no longer names {mark!r}")
    notice = KIT / "NOTICE"
    if notice.is_file():
        ntext = notice.read_text(encoding="utf-8", errors="replace")
        if "Sovereign License" not in ntext:
            ok = False
            notes.append("ALERT    NOTICE does not reference the LYGO Sovereign License")
    return ok, notes


def check_manifest(path: Path) -> tuple[int, int, list[str]]:
    """Return (checked, matched, problem-lines)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - report, never crash the verdict
        return 0, 0, [f"UNREADABLE {path.name}: {exc}"]
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list):
        return 0, 0, []
    checked = matched = 0
    problems: list[str] = []
    for row in files:
        if not isinstance(row, dict) or "path" not in row:
            continue
        rel = str(row["path"])
        want16 = str(row.get("sha256_16") or "")
        want_bytes = row.get("bytes")
        target = KIT / rel
        if not target.is_file():
            checked += 1
            problems.append(f"MISSING  {rel}")
            continue
        checked += 1
        got, size = identity(target)
        raw_size = target.stat().st_size
        if want16 and not got.startswith(want16):
            problems.append(f"CHANGED  {rel}  manifest {want16}  on-disk {got[:16]}")
            continue
        if isinstance(want_bytes, int) and size != want_bytes and raw_size != want_bytes:
            problems.append(f"RESIZED  {rel}  manifest {want_bytes}  on-disk {size}")
            continue
        matched += 1
    return checked, matched, problems


def _leave_console_up() -> None:
    """D9: leave the console answering. Certifying beside a live console must not end with it down."""
    import os

    if os.environ.get("LYGO_NO_ENSURE", "").strip().lower() in {"1", "true", "yes"}:
        return
    try:
        import ensure_console

        _ok, line = ensure_console.ensure(KIT, quiet=True, restore_only=True)
        print("console: " + line)
    except Exception as exc:  # noqa: BLE001 - a helper must never fail the run it protects
        print(f"console: not checked ({type(exc).__name__})")


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify this LYGO kit copy against its manifests.")
    ap.add_argument("--quiet", action="store_true", help="print the verdict only")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    brand_ok, brand_notes = check_brand()
    manifests = sorted(p for p in KIT.glob("*_MANIFEST.json") if p.is_file())
    total_checked = total_matched = 0
    problems: list[str] = []
    per_manifest: list[dict] = []
    for m in manifests:
        checked, matched, probs = check_manifest(m)
        if checked == 0:
            continue
        total_checked += checked
        total_matched += matched
        problems.extend(probs)
        per_manifest.append({"manifest": m.name, "checked": checked, "matched": matched,
                             "problems": probs})

    # Module manifests (Phase M1, WO-0001): a module that cannot be validated is a build problem.
    # Disabled only if the module tree is missing entirely, which is a legitimate pre-module kit.
    module_problems: list[str] = []
    module_count = 0
    modules_dir = KIT / "src" / "modules"
    if modules_dir.is_dir():
        try:
            import sys as _sys

            if str(KIT / "src") not in _sys.path:
                _sys.path.insert(0, str(KIT / "src"))
            from modules import validate as _validate  # noqa: PLC0415

            catalog = _validate.load_catalog(modules_dir)
            if not catalog.get("ok"):
                module_problems.append(f"MODULES   catalog unreadable: {catalog.get('error')}")
            else:
                module_count = len([m for m in catalog["modules"] if m.get("enabled", True)])
            module_problems.extend(
                f"MODULES   {p}" for p in _validate.validate_tree(modules_dir)
            )
        except Exception as exc:  # noqa: BLE001 - report, never crash the verdict
            module_problems.append(f"MODULES   could not be validated: {type(exc).__name__}: {exc}")
    problems.extend(module_problems)

    code_ok, code_notes = check_code_loads(KIT)
    if not args.json and not args.quiet:
        print("Code load (src import / compileall)")
        for n in code_notes:
            print("  " + n)
    if not code_ok:
        problems.extend(code_notes)

    certified = brand_ok and not problems
    integrity_checked = total_checked > 0

    if args.json:
        print(json.dumps({
            "kit": str(KIT),
            "certified": certified,
            "brand_ok": brand_ok,
            "license_signature": SIGNATURE,
            "files_checked": total_checked,
            "files_matched": total_matched,
            "integrity_checked": integrity_checked,
            "manifests": per_manifest,
            "problems": problems,
        }, indent=2))
        return 0 if certified else 1

    if not args.quiet:
        print(f"LYGO build certification - {KIT}")
        print("=" * 72)
        print("Identity: sha256 over LF-normalised bytes (line endings are a checkout detail,\n          not a build change - a CRLF checkout of a clean build still certifies)")
        print("Brand and license identity")
        for n in brand_notes:
            print("  " + n)
        print("Integrity against manifests")
        if not per_manifest:
            print("  no manifest with file hashes found in this folder")
            print("  (brand identity verified; file integrity NOT verified - no manifest to check against)")
        for row in per_manifest:
            print(f"  {row['manifest']}: {row['matched']}/{row['checked']} files match")
        for p in problems:
            print("  " + p)
        print("Module manifests")
        if module_count:
            print(f"  {module_count} enabled module(s) validated against the contract")
        else:
            print("  no module tree in this kit (pre-module build)")
        print("=" * 72)

    if certified and not integrity_checked:
        print("VERDICT: CERTIFIED BRAND - license package intact in this copy.")
        print("         No hash manifest was found, so file integrity could not be verified here.")
        return 0

    if certified:
        print("VERDICT: CERTIFIED BUILD - files match the published manifest and the license is intact.")
        print("         You may use it and build on it under LYGO Sovereign License v3.0.")
        print("         Resale, rebranding and modified redistribution are still not permitted.")
        return 0

    print("VERDICT: MODIFIED / UNCERTIFIED COPY")
    print("         Files differ from the published manifest, or the license package is not intact.")
    print("         Under LYGO Sovereign License v3.0 (sections 4 and 9) this copy:")
    print("           - must NOT be resold, redistributed or offered as a product;")
    print("           - must NOT keep the LYGO / LYGO CLAW / PC LOCAL CONSOLE marks as if the")
    print("             Steward produced or endorses it;")
    print("           - must NOT be presented as official, certified or unmodified.")
    print("         Get a clean copy from the canonical source in NOTICE.")
    return 1


if __name__ == "__main__":
    _console_code = main()
    _leave_console_up()
    sys.exit(_console_code)
