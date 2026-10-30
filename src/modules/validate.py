"""Manifest validation for LYGO Function Modules.

Δ9Φ963-LYGO-MODULE-CORE-v1

One validator, three callers: the host at boot (a bad manifest refuses that module by name),
`scripts/certify_build.py` (a bad manifest fails the certification of the copy), and the module's own
tests. Validation is deliberately strict about *shape* and honest about *absences* — it never guesses
a default for a missing required field, because a guessed value is a behaviour nobody chose.

CLI:  python -m modules.validate            # validate the shipped tree, human output
      python -m modules.validate --json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ID_RE = re.compile(r"^lygo\.[a-z0-9]+(\.[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
RANGE_RE = re.compile(r"^(>=|>|<=|<|==|=)?\s*(\d+\.\d+\.\d+)$")
SURFACE_RE = re.compile(r"^(FULL|DEGRADED\(.+\)|N[/_]A\(.+\))$")

ALLOWED_STATES = (
    "PROPOSED",
    "SPEC'D",
    "SCAFFOLDED",
    "WIRED",
    "TESTED",
    "PARITY",
    "RELEASED",
    "SEALED",
    "DISABLED",
    "REFUSED",
)
SURFACES = ("pc", "usb", "web")
GATES = ("p0", "none")
WRITE_WAYS = ("atomicio",)

REQUIRED: tuple[str, ...] = (
    "id",
    "title",
    "summary",
    "version",
    "introduced",
    "lifecycle",
    "surfaces",
    "requires",
    "kernel_services",
    "machinery",
    "routes",
    "limbs",
    "panes",
    "state",
    "health",
    "tests",
    "docs",
    "adapter",
    "notes",
)


def _version_tuple(text: str) -> tuple[int, int, int]:
    a, b, c = (int(x) for x in text.split("."))
    return a, b, c


def console_satisfies(required: str, release: str) -> bool:
    """Does `release` satisfy the module's `requires.console` range?

    Supports `>=1.2.0`, `>1.2.0`, `<=1.2.0`, `<1.2.0`, `==1.2.0`, `=1.2.0`, a bare `1.2.0`
    (meaning `>=`), and `*` (any release). Anything unparseable is reported as NOT satisfied:
    a requirement nobody can read is not a requirement met.
    """
    want = str(required or "").strip()
    if not want:
        return False
    if want == "*":
        return True
    m = RANGE_RE.match(want)
    if not m:
        return False
    op = m.group(1) or ">="
    a, b = _version_tuple(m.group(2)), _version_tuple(release)
    return {
        ">=": a <= b,
        ">": a < b,
        "<=": a >= b,
        "<": a > b,
        "==": a == b,
        "=": a == b,
    }[op]


def validate_manifest(manifest: Any, module_dir: Path, catalog_id: str | None = None) -> list[str]:
    """Every problem with one manifest, as readable strings. Empty list = valid."""
    problems: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest is not a JSON object"]

    for field in REQUIRED:
        if field not in manifest:
            problems.append(f"missing required field: {field}")

    mid = str(manifest.get("id") or "")
    if not ID_RE.match(mid):
        problems.append(f"id must look like lygo.<name> (lower-case, dots only), got {mid!r}")
    if catalog_id and mid and mid != catalog_id:
        problems.append(f"id {mid!r} does not match its catalog entry/directory {catalog_id!r}")

    for field in ("title", "summary"):
        if not str(manifest.get(field) or "").strip():
            problems.append(f"{field} must be non-empty text")
    if len(str(manifest.get("summary") or "")) > 200:
        problems.append("summary is longer than one line (200 chars)")

    for field in ("version", "introduced"):
        val = str(manifest.get(field) or "")
        if not SEMVER_RE.match(val):
            problems.append(f"{field} must be semver X.Y.Z, got {val!r}")

    lifecycle = str(manifest.get("lifecycle") or "")
    if lifecycle not in ALLOWED_STATES:
        problems.append(f"lifecycle {lifecycle!r} is not one of {', '.join(ALLOWED_STATES)}")

    surfaces = manifest.get("surfaces")
    if not isinstance(surfaces, dict):
        problems.append("surfaces must be an object with pc/usb/web entries")
    else:
        for ed in SURFACES:
            val = str(surfaces.get(ed) or "")
            if not SURFACE_RE.match(val):
                problems.append(
                    f"surfaces.{ed} must be FULL, DEGRADED(reason) or N/A(reason); got {val!r}"
                )

    req = manifest.get("requires")
    if not isinstance(req, dict):
        problems.append("requires must be an object")
    else:
        if not str(req.get("console") or "").strip():
            problems.append("requires.console is required (e.g. \">=1.2.0\", \"*\")")
        elif not (str(req.get("console")).strip() == "*" or RANGE_RE.match(str(req.get("console")).strip())):
            problems.append(f"requires.console {req.get('console')!r} is not a readable range")
        if not isinstance(req.get("modules"), list):
            problems.append("requires.modules must be a list (may be empty)")
        if not isinstance(req.get("env"), list):
            problems.append("requires.env must be a list (may be empty)")
        python = req.get("python")
        if not isinstance(python, list) or not python:
            problems.append("requires.python must be a non-empty list, e.g. [\"3.11\"]")

    for field in ("kernel_services", "machinery", "tests"):
        val = manifest.get(field)
        if not isinstance(val, list) or not val:
            problems.append(f"{field} must be a non-empty list")

    routes = manifest.get("routes")
    if not isinstance(routes, list):
        problems.append("routes must be a list (may be empty for a headless module)")
    else:
        seen: set[tuple[str, str]] = set()
        for i, r in enumerate(routes):
            if not isinstance(r, dict):
                problems.append(f"routes[{i}] must be an object")
                continue
            method = str(r.get("method") or "").upper()
            path = str(r.get("path") or "")
            if method not in ("GET", "POST"):
                problems.append(f"routes[{i}].method must be GET or POST, got {method!r}")
            if not path.startswith("/api/"):
                problems.append(f"routes[{i}].path must start with /api/, got {path!r}")
            if not str(r.get("handler") or ""):
                problems.append(f"routes[{i}].handler is required")
            key = (method, path)
            if key in seen:
                problems.append(f"routes[{i}] repeats {method} {path} inside this manifest")
            seen.add(key)

    limbs = manifest.get("limbs")
    if not isinstance(limbs, list):
        problems.append("limbs must be a list (may be empty)")
    else:
        names: set[str] = set()
        for i, l in enumerate(limbs):
            if not isinstance(l, dict):
                problems.append(f"limbs[{i}] must be an object")
                continue
            name = str(l.get("name") or "")
            if not name:
                problems.append(f"limbs[{i}].name is required")
            if str(l.get("effect") or "") not in ("read", "write"):
                problems.append(f"limbs[{i}].effect must be read or write")
            if name in names:
                problems.append(f"limbs[{i}] repeats limb name {name!r}")
            names.add(name)

    panes = manifest.get("panes")
    if not isinstance(panes, list):
        problems.append("panes must be a list (empty for a headless module)")
    else:
        for i, p in enumerate(panes):
            if not isinstance(p, dict):
                problems.append(f"panes[{i}] must be an object")
                continue
            for field in ("slot", "id"):
                if not str(p.get(field) or ""):
                    problems.append(f"panes[{i}].{field} is required")

    owns = manifest.get("state")
    if not isinstance(owns, dict):
        problems.append("state must be an object (owns/writes_via/gate)")
    else:
        owned = owns.get("owns")
        if not isinstance(owned, list):
            problems.append("state.owns must be a list (empty for a read-only module)")
        else:
            for i, p in enumerate(owned):
                text = str(p or "")
                if text.startswith(("/", "\\")) or ":" in text:
                    problems.append(
                        f"state.owns[{i}] must be kit-relative ({text!r}); absolute paths are refused"
                    )
        if str(owns.get("writes_via") or "") not in WRITE_WAYS:
            problems.append("state.writes_via must be atomicio")
        if str(owns.get("gate") or "") not in GATES:
            problems.append("state.gate must be p0 or none")

    adapter = str(manifest.get("adapter") or "")
    if adapter and not (module_dir / adapter).is_file():
        problems.append(f"adapter file {adapter!r} is not present in {module_dir.name}/")
    if adapter and (module_dir / adapter).suffix != ".py":
        problems.append("adapter must be a .py file")

    return problems


def load_catalog(modules_dir: Path, catalog_path: Path | None = None) -> dict[str, Any]:
    """The catalog, or a readable failure. Never raises."""
    path = Path(catalog_path) if catalog_path else Path(modules_dir) / "catalog.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return {"ok": False, "error": f"catalog_unreadable: {exc}", "modules": []}
    except ValueError as exc:
        return {"ok": False, "error": f"catalog_invalid_json: {exc}", "modules": []}
    if not isinstance(data, dict) or not isinstance(data.get("modules"), list):
        return {"ok": False, "error": "catalog_invalid_shape", "modules": []}
    return {"ok": True, "error": "", **data}


def validate_tree(modules_dir: Path, catalog_path: Path | None = None) -> list[str]:
    """Validate every catalog entry: its directory, its manifest and their agreement."""
    modules_dir = Path(modules_dir)
    problems: list[str] = []
    cat = load_catalog(modules_dir, catalog_path)
    if not cat.get("ok"):
        return [str(cat.get("error"))]

    seen: set[str] = set()
    entries = cat.get("modules") or []
    if not entries:
        return ["catalog lists no modules"]
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            problems.append(f"catalog.modules[{i}] must be an object")
            continue
        mid = str(entry.get("id") or "")
        if not ID_RE.match(mid):
            problems.append(f"catalog.modules[{i}].id must look like lygo.<name>, got {mid!r}")
            continue
        if mid in seen:
            problems.append(f"catalog.modules[{i}] repeats id {mid!r}")
        seen.add(mid)
        mdir = modules_dir / mid
        if not mdir.is_dir():
            problems.append(f"{mid}: directory {mdir.name}/ is missing")
            continue
        mpath = mdir / "module.json"
        if not mpath.is_file():
            problems.append(f"{mid}: module.json is missing")
            continue
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append(f"{mid}: module.json is not valid JSON ({exc})")
            continue
        for problem in validate_manifest(manifest, mdir, catalog_id=mid):
            problems.append(f"{mid}: {problem}")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    modules_dir = Path(__file__).resolve().parent
    as_json = "--json" in args
    problems = validate_tree(modules_dir)
    cat = load_catalog(modules_dir)
    entries = cat.get("modules") or []
    if as_json:
        print(json.dumps({"modules_dir": str(modules_dir), "catalog_ok": bool(cat.get("ok")),
                          "modules": len(entries), "problems": problems}, indent=2))
        return 1 if problems else 0
    print(f"LYGO module manifests — {modules_dir}")
    print("=" * 72)
    for entry in entries:
        if isinstance(entry, dict):
            state = "enabled" if entry.get("enabled", True) else "disabled"
            print(f"  {entry.get('id')}  order={entry.get('order')}  {state}")
    if problems:
        print("PROBLEMS")
        for p in problems:
            print("  " + p)
        return 1
    print(f"VERDICT: {len(entries)} module manifest(s) valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
