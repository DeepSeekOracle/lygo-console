"""Prove this kit's own shell scripts can actually run - a fault class no test can see.

Why this exists: on 2026-10-12 an audit found `scripts/fetch_colibri.ps1` could not parse at all. The
file was UTF-8 *without* a BOM, so Windows PowerShell 5.1 read it as ANSI (CP1252); the third byte of
an em dash (E2 80 94) landed on 0x94, PowerShell accepts U+201D as a string terminator, and the parser
stopped with "unexpected token". Two launcher .bat files carried the same character. Nothing in the
suite noticed, because nothing imports a .ps1 - and a script that cannot parse is a capability the
operator believes exists and does not.

So the rule this enforces: every .ps1/.bat/.cmd in the tree is pure ASCII, and every .ps1 passes
PowerShell's own parser. ASCII is not a style preference here, it is the only encoding that behaves
the same in Windows PowerShell 5.1, PowerShell 7 and cmd.exe.

Usage:  python scripts/check_scripts_ascii.py [ROOT] [--no-parse]
Exit 1 if any script is non-ASCII or fails to parse.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import tempfile

SKIP_DIRS = {".git", "__pycache__", "workspace", "save", "engine", "models", "node_modules",
             "_dl", "win-x64", "linux-x86_64", "macos-universal", "web"}

PARSE_HELPER = """param([string[]]$Files)
$bad = 0
foreach ($f in $Files) {
  $errs = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $f).Path, [ref]$null, [ref]$errs)
  if ($errs -and $errs.Count -gt 0) {
    Write-Output "PARSE-FAIL $f"
    $errs | ForEach-Object { Write-Output ("    line $($_.Extent.StartLineNumber): $($_.Message)") }
    $bad++
  } else {
    Write-Output "PARSE-OK $f"
  }
}
exit $bad
"""


def scripts_under(root: pathlib.Path) -> list[pathlib.Path]:
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in {".ps1", ".bat", ".cmd"}:
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Every shell script is ASCII and parses.")
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--no-parse", action="store_true", help="only check the bytes, do not call PowerShell")
    args = ap.parse_args()
    root = pathlib.Path(args.root).resolve()

    files = scripts_under(root)
    print(f"{len(files)} script(s) under {root}\n")

    dirty: list[pathlib.Path] = []
    ps1: list[pathlib.Path] = []
    for path in files:
        raw = path.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig" if bom else "utf-8", errors="replace")
        odd = [(i + 1, ch) for i, ch in enumerate(text) if ord(ch) > 127]
        rel = path.relative_to(root).as_posix()
        if odd:
            dirty.append(path)
            print(f"NOT-ASCII {rel}")
            print(f"    BOM={bom}  {len(odd)} non-ASCII character(s)")
            for line_no, ch in odd[:6]:
                print(f"    line {line_no}: U+{ord(ch):04X} {ch!r}")
            if not bom and path.suffix.lower() == ".ps1":
                print("    ^ BOM-less AND non-ASCII: Windows PowerShell 5.1 reads this as ANSI and "
                      "may fail to parse")
        else:
            print(f"ok        {rel}  (BOM={bom}, pure ASCII)")
        if path.suffix.lower() == ".ps1":
            ps1.append(path)

    print()
    failed = 0
    if args.no_parse or not ps1:
        print(f"byte check: {len(dirty)} file(s) with non-ASCII  |  parser check: skipped")
    else:
        with tempfile.TemporaryDirectory(prefix="lygo-parse-") as tmp:
            helper = pathlib.Path(tmp) / "parse.ps1"
            helper.write_text(PARSE_HELPER, encoding="ascii", newline="")
            cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(helper)]
            cmd += [str(p) for p in ps1]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            out = (proc.stdout or "") + (proc.stderr or "")
            for line in out.splitlines():
                if line.startswith("PARSE-OK"):
                    print(f"parses    {pathlib.Path(line.split(' ', 1)[1]).relative_to(root).as_posix()}")
                elif line.startswith("PARSE-FAIL"):
                    failed += 1
                    print(line)
                    dirty.append(pathlib.Path(line.split(" ", 1)[1]))
                elif line.startswith("    line"):
                    print(line)
            if proc.returncode is None or not out.strip():
                print("PowerShell produced no verdict - treating that as a failure rather than a pass")
                failed += 1
        print(f"\nbyte check: {len(dirty)} file(s) with non-ASCII  |  parser check: {failed} failing")

    ok = not dirty and not failed
    print("ALL SCRIPTS CLEAN" if ok else "SCRIPTS NEED ATTENTION")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
