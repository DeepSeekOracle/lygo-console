"""Portable path repair for the LYGO LLM Console - the --repair-paths entry point.

The kit travels between drives: <EDRIVE>\\lygo-protocol-stack (desktop) ->
E:\\LYGO_BUILDER_KEY\\lygo_llm_console (USB) -> <DRIVE_D>\LYGO_CONSOLE (packaged). Absolute
paths left in config/admin.json and config/console.json keep naming the drive the kit was
configured on, so after a copy every rooted feature goes quiet with no error at all.

This module rewrites those values to the CURRENT drive and kit location:

  * a value under the kit/stack location - old or new - becomes a token: {kit},
    {workspace} or {stack}, which is portable on any drive and in any folder;
  * a value still anchored on the recorded origin drive follows this kit's drive through
    the {drive} token;
  * relative paths, %ENV% values, URLs and other drives' paths are left alone and listed.

Guarantees: refuses to run without --i-consent; prints a before -> after list; backs the
original up to <name>.bak-YYYYMMDD before touching it; writes atomically through
src/atomicio.py; idempotent (a second run finds nothing left to change).

Credential values are never printed: a key that looks like a secret is shown as [REDACTED].

Usage:
    python src/repair_paths.py --config-dir config --i-consent
    python src/repair_paths.py --i-consent            # live config/ directory
    python src/repair_paths.py                        # plan only, refuses to write
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any

import atomicio
from paths import KIT_ROOT, WORKSPACE, stack_root

CONFIG_DIR: Path = KIT_ROOT / "config"
ADMIN_NAME: str = "admin.json"
CONSOLE_NAME: str = "console.json"
TOKEN_MARKS: tuple[str, ...] = ("{kit}", "{workspace}", "{stack}", "{usb}", "{drive}", "%")
PATH_KEY_HINTS: tuple[str, ...] = ("root", "path", "dir", "drive", "models", "location", "home")
SECRET_KEY_HINTS: tuple[str, ...] = ("credential", "token", "secret", "pass", "auth")
POLICY_KEY: str = "path_policy"
MAX_SHOWN: int = 96


# --- small path helpers -------------------------------------------------------
def _split(v: str) -> list[str]:
    return [c for c in str(v).replace("/", "\\").split("\\") if c not in ("", ".")]


def _join(parts: list[str]) -> str:
    return "\\".join(parts)


def _is_abs(v: str) -> bool:
    s = str(v).strip()
    if len(s) >= 3 and s[0].isalpha() and s[1] == ":" and s[2] in "\\/":
        return True
    return s.startswith("\\\\")


def _drive_of(v: str) -> str:
    s = str(v).strip()
    if len(s) >= 2 and s[1] == ":" and s[0].isalpha():
        return s[0].upper()
    return ""


def _norm(v: str) -> str:
    return _join(_split(v)).lower()


def _under_root(np: str, root: str) -> bool:
    r = _norm(root)
    return bool(r) and (np == r or np.startswith(r + "\\"))


def _tail(v: str, root: str) -> str:
    return _join(_split(v)[len(_split(root)):])


def _looks_pathish(v: str) -> bool:
    s = str(v)
    return ("\\" in s) or ("/" in s) or s.startswith(".")


def _is_path_key(key: str) -> bool:
    k = str(key).lower()
    return any(h in k for h in PATH_KEY_HINTS)


def _is_secret_key(key: str) -> bool:
    k = str(key).lower()
    return any(h in k for h in SECRET_KEY_HINTS)


def _display(value: Any, key: str) -> str:
    s = "[REDACTED]" if _is_secret_key(key) else str(value)
    return s if len(s) <= MAX_SHOWN else s[: MAX_SHOWN - 3] + "..."


def _stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d")


# --- resolution context -------------------------------------------------------
def _current_roots() -> list[tuple[str, str]]:
    """Longest root first, so a workspace path tokenises as {workspace}, not {kit}."""
    out: list[tuple[str, str]] = [(str(WORKSPACE), "{workspace}")]
    stack = ""
    try:
        stack = str(stack_root())
    except Exception:  # never let a resolution problem block the repair tool
        stack = ""
    if stack:
        out.append((stack, "{stack}"))
    out.append((str(KIT_ROOT), "{kit}"))
    return sorted(out, key=lambda pair: len(pair[0]), reverse=True)


def _infer_origin(values: list[str]) -> dict[str, str]:
    """Guess the drive/folders the config was written for, when it does not say."""
    sysdrive = (os.environ.get("SystemDrive") or "C:").rstrip(":").upper()
    kit = ""
    stack = ""
    counts: dict[str, int] = {}
    for v in values:
        if not _is_abs(v):
            continue
        d = _drive_of(v)
        if d and d != sysdrive:
            counts[d] = counts.get(d, 0) + 1
        parts = _split(v)
        for i, c in enumerate(parts):
            if c.lower() == KIT_ROOT.name.lower() and len(_join(parts[: i + 1])) > len(kit):
                kit = _join(parts[: i + 1])
            if c.lower() == "lygo-protocol-stack" and len(_join(parts[: i + 1])) > len(stack):
                stack = _join(parts[: i + 1])
    drive = ""
    if counts:
        drive = max(sorted(counts), key=lambda k: counts[k])
    return {"origin_kit_root": kit, "origin_stack_root": stack, "origin_drive": drive}


def _collect_strings(node: Any, out: list[str]) -> list[str]:
    if isinstance(node, dict):
        for v in node.values():
            _collect_strings(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_strings(v, out)
    elif isinstance(node, str):
        out.append(node)
    return out


def _ctx_for(data: dict[str, Any], text: str, path: Path) -> dict[str, Any]:
    pol = data.get(POLICY_KEY) if isinstance(data.get(POLICY_KEY), dict) else {}
    inferred = _infer_origin(_collect_strings(data, []))
    origin_kit = str(pol.get("origin_kit_root") or inferred["origin_kit_root"] or "")
    origin_stack = str(pol.get("origin_stack_root") or inferred["origin_stack_root"] or "")
    origin_drive = str(pol.get("origin_drive") or inferred["origin_drive"] or "").upper()
    origin: list[tuple[str, str]] = []
    if origin_kit:
        origin.append((origin_kit + "\\workspace", "{workspace}"))
        origin.append((origin_kit, "{kit}"))
    if origin_stack:
        origin.append((origin_stack, "{stack}"))
    return {
        "file": path.name,
        "path": path,
        "text": text,
        "current": _current_roots(),
        "origin": sorted(origin, key=lambda pair: len(pair[0]), reverse=True),
        "origin_drive": origin_drive,
        "origin_source": "path_policy" if pol else "inferred from the values",
        "path_keys_only": path.name == CONSOLE_NAME,
        "indent": 4 if "\n    \"" in text else 2,
        "newline": "\r\n" if "\r\n" in text else "\n",
    }


# --- rewriting ----------------------------------------------------------------
def _host_owned(value: str) -> bool:
    """True for paths that belong to the machine rather than to the kit.

    Repair must never adopt C:\\Users\\<user>\\Pictures, Windows or Program Files for a copy.
    A second pass used to do exactly that: once the stamped origin drive became the kit's own
    drive, every path on it looked like an origin path, and the remap produced a path that
    exists but is not what the steward configured. The existence check cannot see this, so the
    host folders are excluded by name.
    """
    low = str(value).strip().lower()
    for name in ("USERPROFILE", "SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        root = (os.environ.get(name) or "").strip().lower()
        if root and low.startswith(root):
            return True
    return False

def _candidate_exists(value: str) -> bool:
    """True when a rewritten root would actually resolve on this host.

    Repair used to remap any origin-drive path to {drive} unconditionally, so a copy on D: got
    D:\\E Drive\\LYRA LOCAL while the real folder stayed on I: - a read root that exists nowhere
    and fails silently, because a missing root simply returns no files. Tokens that only
    paths.py can resolve late, or %ENV% values, are not judgeable here and are allowed through.
    """
    v = str(value)
    for token, replacement in (("{kit}", str(KIT_ROOT)),
                              ("{workspace}", str(KIT_ROOT / "workspace")),
                              ("{drive}", (KIT_ROOT.drive or "C:")[:1])):
        v = v.replace(token, replacement)
    if "{stack}" in v or "{usb}" in v or "%" in v:
        return True
    try:
        return os.path.exists(v)
    except OSError:
        return False

def rewrite_value(value: str, key: str, ctx: dict[str, Any]) -> tuple[str | None, str, str]:
    """(new_value | None, kind, note) for one config string."""
    s = str(value).strip()
    if not s:
        return None, "keep", "empty"
    if any(t in s for t in TOKEN_MARKS):
        return None, "keep", "already portable (token or environment variable)"
    if not _is_abs(s):
        return None, "keep", "relative path - travels with the kit" if _looks_pathish(s) else "not a path value"
    if ctx["path_keys_only"] and not _is_path_key(key):
        return None, "keep", "not a path key"
    np = _norm(s)
    for root, token in ctx["current"]:
        if _under_root(np, root):
            tail = _tail(s, root)
            return token + ("\\" + tail if tail else ""), "token", f"written as {token}"
    for root, token in ctx["origin"]:
        if _under_root(np, root):
            tail = _tail(s, root)
            return token + ("\\" + tail if tail else ""), "relocate", f"was under the old location, now {token}"
    drive = ctx["origin_drive"]
    if drive and _drive_of(s) == drive:
        if _host_owned(s):
            return None, "keep", f"host location ({s}) - a copy of the kit must not adopt it"
        if not _is_path_key(key):
            return None, "review", f"origin drive {drive}: in a non-path key - left for manual review"
        cand = "{drive}:" + s[2:]
        if not _candidate_exists(cand):
            # A remap that lands nowhere is worse than the original: the root silently yields
            # nothing and no layer reports it. Only follow this kit's drive when the folder is
            # really here, otherwise keep the value and say so in the plan output.
            return None, "keep", (f"origin drive {drive}: {cand} does not exist here - kept {s}"
                                  " (rewrite by hand if this copy really owns that folder)")
        return cand, "remap", f"origin drive {drive}: now follows this kit's drive"
    return None, "keep", "host-anchored on another drive"


def _transform(node: Any, prefix: str, ctx: dict[str, Any], rows: list[dict[str, Any]]) -> Any:
    if prefix == POLICY_KEY or prefix.startswith(POLICY_KEY + "."):
        return node  # the origin record is absolute by definition - never tokenised
    if isinstance(node, dict):
        return {k: _transform(v, f"{prefix}.{k}" if prefix else str(k), ctx, rows) for k, v in node.items()}
    if isinstance(node, list):
        out: list[Any] = []
        seen: dict[str, int] = {}
        for i, v in enumerate(node):
            key = f"{prefix}[{i}]"
            new = _transform(v, key, ctx, rows)
            if isinstance(new, str):
                k = _norm(new)
                if k in seen:
                    rows.append(
                        {
                            "file": ctx["file"],
                            "key": key,
                            "before": _display(v, key),
                            "after": f"dropped (kept {prefix}[{seen[k]}])",
                            "status": "change",
                            "kind": "dedupe",
                            "note": f"duplicate of {prefix}[{seen[k]}]",
                        }
                    )
                    continue
                seen[k] = i
            out.append(new)
        return out
    if isinstance(node, str):
        new, kind, note = rewrite_value(node, prefix, ctx)
        rows.append(
            {
                "file": ctx["file"],
                "key": prefix,
                "before": _display(node, prefix),
                "after": _display(new if new is not None else node, prefix),
                "status": "change" if new is not None else "keep",
                "kind": kind,
                "note": note,
            }
        )
        return node if new is None else new
    return node


def _stamp_policy(data: dict[str, Any], ctx: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Record where this config's paths were written, so a later copy can still relocate them.

    The timestamp row never counts as a change: only a real rewrite moves it, which is what
    keeps a second run reporting nothing to do.
    """
    cur = {
        "origin_kit_root": str(KIT_ROOT),
        "origin_stack_root": str(stack_root()),
        "origin_drive": (KIT_ROOT.drive or "")[:1].upper(),
        "last_repair_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": "src/repair_paths.py",
    }
    pol = data.get(POLICY_KEY)
    if not isinstance(pol, dict):
        pol = {}
    for k, v in cur.items():
        old = pol.get(k)
        differs = str(old or "") != str(v)
        counts = differs and old is not None and k != "last_repair_utc"
        pol[k] = v
        rows.append(
            {
                "file": ctx["file"],
                "key": f"{POLICY_KEY}.{k}",
                "before": _display(old, k),
                "after": _display(v, k),
                "status": "change" if counts else "keep",
                "kind": "policy",
                "note": "origin record updated" if counts else "origin record current",
            }
        )
    data[POLICY_KEY] = pol
    return data


# --- plan / apply -------------------------------------------------------------
def plan(config_dir: os.PathLike[str] | str) -> list[tuple[Path, dict[str, Any], dict[str, Any], list[dict[str, Any]]]]:
    """Load every config file and return [(path, mutated_data, ctx, rows), ...] without writing."""
    out: list[tuple[Path, dict[str, Any], dict[str, Any], list[dict[str, Any]]]] = []
    for name in (ADMIN_NAME, CONSOLE_NAME):
        p = Path(config_dir) / name
        if not p.is_file():
            continue
        text = atomicio.read_text(p)
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            out.append((p, {}, {"file": name, "path": p, "text": text, "indent": 4, "newline": "\n", "current": [], "origin": [], "origin_drive": "", "origin_source": "unreadable", "path_keys_only": name == CONSOLE_NAME},
                        [{"file": name, "key": "<file>", "before": "[unreadable json]", "after": "", "status": "review", "kind": "error", "note": "not valid JSON - nothing rewritten"}]))
            continue
        ctx = _ctx_for(data, text, p)
        rows: list[dict[str, Any]] = []
        data = _transform(data, "", ctx, rows)
        if name == ADMIN_NAME:
            data = _stamp_policy(data, ctx, rows)
        out.append((p, data, ctx, rows))
    return out


def print_plan(planned: list[tuple[Path, dict[str, Any], dict[str, Any], list[dict[str, Any]]]]) -> int:
    total = 0
    for _p, _data, _ctx, rows in planned:
        changes = [r for r in rows if r["status"] == "change"]
        total += len(changes)
        print(f"\n=== {rows[0]['file'] if rows else '?'}: {len(rows)} path value(s) scanned, {len(changes)} to change ===")
        for r in rows:
            if r["status"] == "change":
                print(f"  CHANGE  {r['key']:<26} {r['before']}  ->  {r['after']}   [{r['kind']}] {r['note']}")
        for r in rows:
            if r["status"] != "change":
                print(f"  keep    {r['key']:<26} {r['before']}   [{r['kind']}] {r['note']}")
    return total


def apply(planned: list[tuple[Path, dict[str, Any], dict[str, Any], list[dict[str, Any]]]]) -> list[str]:
    """Back up then atomically rewrite every file that has changes. Returns what was written."""
    written: list[str] = []
    for path, data, ctx, rows in planned:
        if not [r for r in rows if r["status"] == "change"]:
            continue
        text = json.dumps(data, indent=ctx["indent"], ensure_ascii=False)
        if ctx["newline"] != "\n":
            text = text.replace("\n", ctx["newline"])
        text += ctx["newline"]
        original = atomicio.read_text(path)
        bak = path.with_name(f"{path.name}.bak-{_stamp()}")
        if not bak.exists():
            atomicio.atomic_write_text(bak, original)
        atomicio.atomic_write_text(path, text)
        written.append(f"{path} (backup {bak.name})")
    return written


def repair_paths(config_dir: os.PathLike[str] | str | None = None, *, consent: bool = False) -> int:
    """Programmatic entry point. Without consent it prints the plan and refuses to write."""
    argv = ["--config-dir", str(config_dir if config_dir is not None else CONFIG_DIR)]
    if consent:
        argv.append("--i-consent")
    return main(argv)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="repair_paths",
        description="Rewrite drive-anchored config paths to the drive and location this kit is running from.",
    )
    ap.add_argument("--config-dir", default=str(CONFIG_DIR), help=f"directory holding {ADMIN_NAME}/{CONSOLE_NAME} (default {CONFIG_DIR})")
    ap.add_argument("--i-consent", action="store_true", dest="i_consent", help="required to write: back up first, then rewrite config paths")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and write nothing")
    ap.add_argument("--json", action="store_true", dest="as_json", help="emit only the plan as JSON")
    args = ap.parse_args(argv)

    planned = plan(args.config_dir)
    if not planned:
        print(f"no {ADMIN_NAME} / {CONSOLE_NAME} under {args.config_dir}")
        return 3
    rows = [r for _p, _d, _c, rr in planned for r in rr]
    changed = [r for r in rows if r["status"] == "change"]
    written: list[str] = []
    if not args.i_consent:
        status = "refused"
    elif args.dry_run:
        status = "dry-run"
    else:
        written = apply(planned)
        status = "applied" if written else "idempotent"

    if args.as_json:
        print(
            json.dumps(
                {
                    "config_dir": str(args.config_dir),
                    "status": status,
                    "changed": len(changed),
                    "planned": rows,
                    "written": written,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 2 if status == "refused" else 0

    print_plan(planned)
    for _p, _d, ctx, _rows in planned:
        print(f"[{ctx['file']}] kit root now {KIT_ROOT} | origin used: {ctx['origin_source']} (drive {ctx['origin_drive'] or '?'})")
    print(f"\n{len(changed)} value(s) would change.")
    if status == "refused":
        print("refusing to write without --i-consent (this rewrites config paths; a .bak-YYYYMMDD is written first).")
        return 2
    if status == "dry-run":
        print("--dry-run: nothing written.")
        return 0
    if status == "idempotent":
        print("idempotent: already portable for this drive and kit location, nothing written.")
        return 0
    for line in written:
        print(f"wrote {line}")
    print("done - re-run to confirm it is idempotent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
