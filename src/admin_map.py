"""Admin unlock map. Loaded only if config/admin.json exists (not in public git)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from paths import KIT_ROOT, SAVE, WORKSPACE, stack_root, stack_root_status

_cache: dict[str, Any] | None = None


def invalidate() -> None:
    global _cache
    _cache = None


def load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    p = KIT_ROOT / "config" / "admin.json"
    if not p.is_file():
        _cache = {}
        return _cache
    try:
        _cache = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _cache = {}
    return _cache


def is_admin() -> bool:
    return bool(load())


def optional_roots() -> list[str]:
    """Roots the config declares as legitimately absent - mapped only during steward ops.

    A declared-optional root that does not resolve is **normal**. A reader that calls it a fault
    sends the operator chasing a drive that is meant to be offline, so the declaration lives in
    `config/admin.json` next to the roots it describes, and this is the only place it is read.
    """
    try:
        raw = load().get("optional_roots")
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(raw, list):
        return []
    return [str(x) for x in raw if str(x).strip()]


def _norm_root(path: Any) -> str:
    return os.path.normcase(str(path or "").strip().rstrip("\\/"))


def is_optional_root(path: Any) -> bool:
    """True when `path` - or something inside it - was declared optional by the config."""
    want = _norm_root(path)
    if not want:
        return False
    for r in optional_roots():
        root = _norm_root(r)
        if root and (want == root or want.startswith(root + os.sep)):
            return True
    return False


def _usb_root() -> str:
    """The builder-key root when one resolves, else '' - usb_root_status() carries the reason."""
    st = usb_root_status()
    return str(st.get("root") or "")


# --- chatagent tree resolution ------------------------------------------------
# Was a baked-in Path(r"D:\chatagent"): on any host without D:, self_check reported
# chatagent_exists False with no hint why. Env -> config -> kit-relative -> drive letters LAST.

CHATAGENT_ENV: tuple[str, ...] = ("LYGO_CHATAGENT_ROOT", "LYGO_CHATAGENT")
CHATAGENT_DIR: str = "chatagent"
CHATAGENT_DRIVE_LETTERS: str = "DICFUE"
CHATAGENT_MARKERS: tuple[str, ...] = (".git", "sites", "games", "site")


def _chatagent_candidates() -> list[tuple[str, Path | None]]:
    out: list[tuple[str, Path | None]] = []
    for name in CHATAGENT_ENV:
        raw = os.environ.get(name, "").strip()
        out.append((f"env:{name}", Path(raw) if raw else None))
    try:
        cfg = load().get("chatagent_root")
    except Exception:
        cfg = None
    out.append(("config:chatagent_root", Path(str(cfg)) if cfg else None))
    out.append((f"kit-relative:KIT_ROOT.parent/{CHATAGENT_DIR}", KIT_ROOT.parent / CHATAGENT_DIR))
    out.append((f"kit-relative:KIT_ROOT.parent.parent/{CHATAGENT_DIR}", KIT_ROOT.parent.parent / CHATAGENT_DIR))
    here = (KIT_ROOT.drive or "")[:1].upper()
    letters = ([here] if here else []) + [c for c in CHATAGENT_DRIVE_LETTERS if c != here]
    for letter in letters:
        out.append((f"drive-scan:{letter}:{chr(92)}{CHATAGENT_DIR}", Path(f"{letter}:{chr(92)}{CHATAGENT_DIR}")))
    return out


def chatagent_root_status() -> dict[str, Any]:
    """Resolve the chatagent tree and say which candidate won or why each was skipped."""
    log: list[dict[str, str]] = []
    for source, cand in _chatagent_candidates():
        if cand is None:
            log.append({"source": source, "path": "", "verdict": "unset", "reason": "not set"})
            continue
        try:
            is_dir = cand.is_dir()
        except (OSError, ValueError):
            is_dir = False
        if not is_dir:
            log.append({"source": source, "path": str(cand), "verdict": "skipped", "reason": "not a directory"})
            continue
        marker = next((m for m in CHATAGENT_MARKERS if (cand / m).exists()), "")
        log.append(
            {
                "source": source,
                "path": str(cand),
                "verdict": "won" if marker else "exists-unverified",
                "reason": f"marker {marker}" if marker else "no chatagent marker",
            }
        )
        if marker:
            return {
                "root": str(cand),
                "verified": True,
                "source": source,
                "reason": f"{source} verified by {marker}",
                "candidates": log,
            }
    reason = (
        f"chatagent tree not found; expected <drive>:{chr(92)}{CHATAGENT_DIR} or the configured "
        f"chatagent_root - set LYGO_CHATAGENT_ROOT to name it"
    )
    log.append({"source": "fallback", "path": "", "verdict": "missing", "reason": reason})
    return {"root": "", "verified": False, "source": "none", "reason": reason, "candidates": log}


def chatagent_root() -> Path:
    """The chatagent tree, or a named-not-found path when this host has none."""
    st = chatagent_root_status()
    root = str(st.get("root") or "")
    return Path(root) if root else (KIT_ROOT.parent / CHATAGENT_DIR)


# --- builder-key (/USB) root resolution ---------------------------------------
# The old code tried E:\ and then fell back to KIT_ROOT.parent, so the desktop kit
# silently accepted the WRONG tree - <EDRIVE>\lygo-protocol-stack passed the loose
# "has a lygo_llm_console child" test and {usb} expanded to the stack, reporting success.
# Now: explicit env override first, then kit-relative, then a drive-letter scan LAST, and
# a candidate only counts when a marker that really lives in the builder-key tree
# (BUILDER_MANIFEST.json signature, LYGO_CLAW.bat, LYGO_USB_BOOT.bat) confirms it. When no
# tree verifies we warn loudly with the expected path and leave {usb} unexpanded.

USB_ROOT_ENV: tuple[str, ...] = ("LYGO_USB_ROOT", "LYGO_BUILDER_KEY_ROOT")
USB_DIR_NAME: str = "LYGO_BUILDER_KEY"
USB_MARKERS: tuple[str, ...] = ("LYGO_CLAW.bat", "LYGO_USB_BOOT.bat")
USB_MANIFEST: str = "BUILDER_MANIFEST.json"
USB_SIGNATURE: str = "\u0394" + "9\u03a6963" + "-BUILDER-KEY-MANIFEST" + "-v1"
USB_DRIVE_LETTERS: str = "EDFICUV"

_usb_log: list[dict[str, str]] = []
_USB_WARNED: list[str] = []


def _usb_record(source: str, path: Any, verdict: str, reason: str = "") -> dict[str, str]:
    row = {"source": source, "path": str(path or ""), "verdict": verdict, "reason": reason}
    _usb_log.append(row)
    if os.environ.get("LYGO_PATHS_DEBUG", "").strip():
        print(f"[admin_map] usb-root {source} = {row['path']} -> {verdict} ({reason})", file=sys.stderr)
    return row


def usb_resolution_log() -> list[dict[str, str]]:
    """Every candidate the last builder-key root resolution considered, in order."""
    return [dict(r) for r in _usb_log]


def _usb_marker(root: Path) -> str:
    """A reason string when root really is the builder-key tree, else ''."""
    man = root / USB_MANIFEST
    if man.is_file():
        try:
            sig = str(json.loads(man.read_text(encoding="utf-8")).get("signature") or "")
        except (OSError, json.JSONDecodeError):
            sig = ""
        if USB_SIGNATURE in sig:
            return f"{USB_MANIFEST} signature {USB_SIGNATURE}"
        if sig:
            return ""  # a manifest without the kit signature is not this tree
    for name in USB_MARKERS:
        if (root / name).is_file():
            return f"marker {name}"
    if (root / USB_DIR_NAME).is_dir() and (root / "lygo_llm_console").is_dir():
        return f"subdir {USB_DIR_NAME} + lygo_llm_console"
    return ""


_BS_OR_SLASH: str = chr(92)


def _usb_candidates() -> list[tuple[str, Path | None]]:
    out: list[tuple[str, Path | None]] = []
    for name in USB_ROOT_ENV:
        raw = os.environ.get(name, "").strip()
        out.append((f"env:{name}", Path(raw) if raw else None))
    out.append(("kit-relative:KIT_ROOT", KIT_ROOT))
    out.append(("kit-relative:KIT_ROOT.parent", KIT_ROOT.parent))
    out.append((f"kit-relative:KIT_ROOT.parent/{USB_DIR_NAME}", KIT_ROOT.parent / USB_DIR_NAME))
    here = (KIT_ROOT.drive or "")[:1].upper()
    letters = ([here] if here else []) + [c for c in USB_DRIVE_LETTERS if c != here]
    for letter in letters:
        cand = f"{letter}:{_BS_OR_SLASH}{USB_DIR_NAME}"
        out.append((f"drive-scan:{cand}", Path(cand)))
    return out


def _warn_usb(reason: str) -> None:
    """One hard stderr warning per distinct reason - never a silent wrong-tree fallback."""
    if reason in _USB_WARNED:
        return
    _USB_WARNED.append(reason)
    sys.stderr.write(f"[admin_map] WARNING: {reason}\n")
    sys.stderr.flush()


def usb_root_status() -> dict[str, Any]:
    """Resolve the LYGO_BUILDER_KEY tree and say exactly what happened.

    Order: LYGO_USB_ROOT / LYGO_BUILDER_KEY_ROOT -> kit-relative -> drive letters LAST.
    """
    del _usb_log[:]
    expected = "<drive>:" + chr(92) + USB_DIR_NAME
    for source, cand in _usb_candidates():
        if cand is None:
            _usb_record(source, "", "unset", "not set")
            continue
        try:
            is_dir = cand.is_dir()
        except (OSError, ValueError):
            is_dir = False
        if not is_dir:
            _usb_record(source, cand, "skipped", "not a directory")
            continue
        marker = _usb_marker(cand)
        if marker:
            _usb_record(source, cand, "won", marker)
            return {
                "root": str(cand),
                "verified": True,
                "source": source,
                "reason": f"{source} verified by {marker}",
                "candidates": usb_resolution_log(),
            }
        if source.startswith("env:"):
            why = (
                f"{source} names {cand} but it carries no builder-key marker ({USB_MANIFEST} "
                f"signature / {' / '.join(USB_MARKERS)}); honoring the explicit override anyway"
            )
            _usb_record(source, cand, "won-unverified", why)
            _warn_usb(why)
            return {
                "root": str(cand),
                "verified": False,
                "source": source,
                "reason": why,
                "candidates": usb_resolution_log(),
            }
        _usb_record(source, cand, "skipped", "exists but carries no builder-key marker")
    reason = (
        f"builder-key tree not found; expected {expected} with {USB_MANIFEST} signature "
        f"{USB_SIGNATURE} or one of {'/'.join(USB_MARKERS)} - set LYGO_USB_ROOT to name it; "
        "{usb} roots stay unexpanded (no wrong tree inspected)"
    )
    _usb_record("fallback", "", "missing", reason)
    _warn_usb(reason)
    return {"root": "", "verified": False, "source": "none", "reason": reason, "candidates": usb_resolution_log()}


def path_warnings() -> list[str]:
    """Unresolved-root warnings for status surfaces."""
    st = usb_root_status()
    return [] if st.get("verified") else [str(st.get("reason") or "")]


def _expand_path(raw: str) -> Path:
    usb = _usb_root()
    s = str(raw)
    s = s.replace("{kit}", str(KIT_ROOT))
    s = s.replace("{workspace}", str(WORKSPACE))
    s = s.replace("{stack}", str(stack_root()))
    s = s.replace("{drive}", (KIT_ROOT.drive or "")[:1] or "C")
    if usb:
        s = s.replace("{usb}", usb)
    return Path(s)


def paths_of(key: str, fallback: tuple[Path, ...]) -> tuple[Path, ...]:
    raw = load().get(key)
    out: list[Path] = []
    seen: set[str] = set()
    try:
        from workspace_map import extra_paths, revoked_set

        revoked = revoked_set()
        extra = extra_paths("read" if key == "read_roots" else "write" if key == "write_roots" else "search")
    except Exception:
        revoked = set()
        extra = []
    seq = list(raw) if isinstance(raw, list) else []
    for x in seq:
        p = _expand_path(str(x))
        try:
            if not p.exists():
                continue
            keyp = str(p.resolve())
        except OSError:
            continue
        if keyp in revoked or keyp in seen:
            continue
        seen.add(keyp)
        out.append(p)
    for p in extra:
        try:
            keyp = str(p.resolve())
        except OSError:
            continue
        if keyp in seen or keyp in revoked:
            continue
        seen.add(keyp)
        out.append(p)
    if out:
        return tuple(out)
    return fallback


def read_roots() -> tuple[Path, ...]:
    return paths_of("read_roots", (WORKSPACE,))


def write_roots() -> tuple[Path, ...]:
    return paths_of("write_roots", (WORKSPACE, SAVE))


def search_roots() -> tuple[Path, ...]:
    return paths_of("search_roots", (WORKSPACE,))


def links() -> dict[str, Any]:
    return load().get("links") if isinstance(load().get("links"), dict) else {}


def credential_pointers() -> dict[str, str]:
    raw = load().get("credential_pointers")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if k == "note" or not isinstance(v, str):
            continue
        out[str(k)] = v
    return out


def drives() -> dict[str, str]:
    """Drive roles. Config wins; the fallback describes roles without pinning another host's
    drive letter into the kit (the old fallback said "stream F:\\LYGO when mapped")."""
    raw = load().get("drives")
    if isinstance(raw, dict) and raw:
        return {str(k): str(v) for k, v in raw.items()}
    sysdrive = (os.environ.get("SystemDrive") or "C:").rstrip(":").upper()
    return {
        sysdrive: "Windows + profile",
        "D": "chatagent git (sites + games)",
        "E": "USB builder key tree",
        "I": "stack, model vault, LYRA LOCAL",
        "U": "stream drive when mapped",
    }


def is_placeholder_url(url: str) -> bool:
    u = (url or "").strip().lower()
    if not u:
        return False
    if "example.com" in u or "example.org" in u:
        return True
    if "github.com/user/" in u or u.rstrip("/").endswith("github.com/user"):
        return True
    if "huggingface.co/models/transformers" in u:
        return True
    if "lattice.example" in u:
        return True
    return False


def brief() -> dict[str, Any]:
    ln = links()
    return {
        "ok": True,
        "role": "admin_kernel" if is_admin() else "public_kit",
        "steward": "Justin Helmer / Excavationpro / Lightfather",
        "drives": drives() if is_admin() else {},
        "read_roots": [str(p) for p in read_roots()],
        "write_roots": [str(p) for p in write_roots()],
        "search_roots": [str(p) for p in search_roots()],
        "github_org": ln.get("github_org") or "https://github.com/DeepSeekOracle",
        "hf_org": ln.get("hf_org") or "https://huggingface.co/DeepSeekOracle",
        "github_repos": ln.get("github_repos") or [
            "https://github.com/DeepSeekOracle/chatagent",
            "https://github.com/DeepSeekOracle/lygo-protocol-stack",
        ],
        "hf": ln.get("hf") or [ln.get("hf_org") or "https://huggingface.co/DeepSeekOracle"],
        "sites": ln.get("sites") or ["https://chatagent.ca/"],
        "lattice": ln.get("lattice")
        or [
            "https://chatagent.ca/",
            "https://chatagent.ca/join/",
            "https://chatagent.ca/agents/",
            "https://chatagent.ca/starchart/",
            "https://chatagent.ca/witness/",
            "https://chatagent.ca/lygoskillhub.html",
        ],
        "credential_tool": "credential_where",
        "rules": [
            "Never invent github.com/user/repo or lattice.example.com.",
            "Never print *.pass contents. Call credential_where; report path + exists only.",
            "Use list_dir / find_files on real drives. Use web_fetch on LINKS.md URLs.",
        ],
    }


def brief_text() -> str:
    b = brief()
    lines = [
        "ADMIN BRIEF (call steward_map if you need the full map):",
        "The operator's own folders are mapped: Desktop, Documents, Downloads, home. To put a file "
        "there call save_note with where=desktop (or documents/downloads/home) and consent=true; it "
        "returns the verified absolute path. Never report a file as created unless the limb that "
        "wrote it said so - a receipt you did not receive is a receipt you do not have.",
        f"role={b.get('role')} steward={b.get('steward')}",
        f"GitHub org: {b.get('github_org')}",
        f"Hugging Face: {b.get('hf_org')}",
        "Lattice: https://chatagent.ca/  join/ agents/ starchart/ witness/ lygoskillhub.html",
        "Forbidden placeholders: github.com/user/repo  lattice.example.com  example.com",
        "Passwords: credential_where — path only, never echo secrets.",
    ]
    if is_admin():
        lines.append("Drives: C Windows · D chatagent · E USB · I stack · U stream (if mapped)")
        lines.append("First tools: steward_map, whoami, list_dir, find_files, web_fetch real URLs.")
    return "\n".join(lines)
