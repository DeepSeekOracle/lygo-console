"""lygo.guardwatch — who could read something they must not?

Δ9Φ963-LYGO-GUARDWATCH-v1

A finder (family "watch"). It answers one question in one place, and it goes amber or red only when
something really is wrong: **is anything readable here that should not be?**

WHAT IT CHECKS
    A. Every credential pointer in `config/admin.json → credential_pointers` — does it resolve, does
       it sit inside the kit, does it sit inside any root a limb may open, and does the kernel's own
       deny rule actually cover it?
    B. Secret-shaped FILES inside the kit tree — and for each one, whether `.gitignore` covers it,
       because a key in the tree that git would commit ships to a public repo.
    C. Secret-shaped files inside the read roots a limb may open, bounded and with the budget stated.
    D. The kit's own declared runtime keys: present, declared, git-ignored.
    E. Does any git remote URL carry a credential in it?

THE ONE RULE IT MAY NEVER BREAK
    **It never opens a credential file.** It stats, it resolves, it compares paths — it never reads a
    byte of one, and it never prints a secret. The direct consequence is stated in `missing[]` every
    time: a secret sitting inside a file whose NAME looks ordinary cannot be found by this card,
    because finding it would mean reading the file.

WHY IT ASKS INSTEAD OF RE-DERIVING
    The kernel already owns the answer to "may this path be touched": `tools._denied(resolved_path)`
    resolves a path to its real form first (so a short name, a junction or a mapped letter cannot slip
    past it) and denies the credential directories outright. This card CALLS that, and names it in
    `sources[]`, rather than keeping a second copy of the deny list that could drift out of step with
    the one actually enforced. Same for the pointers (`admin_map.credential_pointers()`), the roots
    (`admin_map.read_roots()`) and the drives (`admin_map.drives()`).

    It owns no state, writes nothing and fixes nothing — the owner of a thing fixes the thing. Its
    own test scans this file for write calls.
"""

from __future__ import annotations

import fnmatch
import os
import re
import time
from pathlib import Path
from typing import Any

SIGNATURE = "Δ9Φ963-LYGO-GUARDWATCH-v1"
MID = "lygo.guardwatch"
ROUTE = "/api/guardwatch"
ROUTES: tuple[tuple[str, str], ...] = (("GET", ROUTE),)

OWNERS: tuple[str, ...] = ("paths", "admin_map", "tools", "atomicio")

#: How much of a wide root this card will walk, and how deep. Stated in the payload, because a
#: capped walk that does not say it was capped is a false green.
_DEPTH_WIDE = 3
_BUDGET_KIT = 20_000
_BUDGET_PER_ROOT = 12_000
_BUDGET_WIDE_TOTAL = 80_000

#: Secret-shaped NAMES, tier 1: key material. No false positives expected, so a hit is a finding.
_TIER1: tuple[tuple[str, str], ...] = (
    ("dotenv file", r"^\.env(\..+)?$"),
    ("private key / certificate", r"\.(pem|key|pfx|p12|jks|keystore|ppk|kdbx|asc|gpg)$"),
    ("ssh private key", r"^id_(rsa|dsa|ecdsa|ed25519)$"),
    ("stored credential file", r"\.(pass|pgpass|htpasswd|netrc|npmrc|pypirc)$"),
    ("network credential file", r"^\.?(netrc|_netrc)$"),
)

#: Tier 2: the name SAYS it holds a secret AND the file is the kind of file a secret is kept in.
#: Both halves are required. Without the extension half this card can never be green on a real kit:
#: the engine ships `llama-tokenize.exe`, and build output ships `*-secret-*.d.ts` files, and neither
#: is a secret. A false alarm that can never clear trains the reader to ignore the light.
_TIER2_WORD = re.compile(r"(credential|secret|passwd|password|token|apikey|api_key)")
_DATA_EXT: tuple[str, ...] = (
    "", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".txt", ".env", ".xml", ".csv",
    ".db", ".sqlite", ".sqlite3", ".log", ".bak", ".old", ".properties", ".keyring",
)
_TEMPLATE_EXT: tuple[str, ...] = (".example", ".sample", ".template", ".dist", ".md5", ".sha256")

#: The kit's OWN runtime keys. Expected in the tree, declared here so an expected file is never a
#: finding — and checked for git-ignore coverage instead, which is the thing that actually matters.
_EXPECTED: tuple[tuple[str, str], ...] = (
    ("config/api.json", "the saved cloud provider key"),
    ("config/local.json", "this machine's local overrides"),
    ("config/admin.json", "the steward's roots and credential POINTERS — pointers, never values"),
    ("data/.lygo_llm_token", "the console's own loopback/LAN token"),
    ("data/.llama_api_key", "the private engine's own key"),
)


# ------------------------------------------------------------------------------------------------
# small helpers
# ------------------------------------------------------------------------------------------------
def _at() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _plural(n: Any, one: str, many: str | None = None) -> str:
    try:
        i = int(n)
    except (TypeError, ValueError):
        return f"{n} {many or one + 's'}"
    return f"{i} {one if i == 1 else (many or one + 's')}"


def _row(k: str, v: Any, note: str = "", dot: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"k": k, "v": "" if v is None else str(v)}
    if note:
        out["note"] = note
    if dot:
        out["dot"] = dot
    return out


def _group(title: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"title": title, "rows": rows}


def _owner(name: str) -> Any | None:
    try:
        return __import__(name)  # noqa: PLC0415 - late on purpose: a missing owner is a named gap
    except Exception:  # noqa: BLE001
        return None


def _key(p: Any) -> str:
    try:
        return os.path.normcase(str(Path(p).resolve()))
    except (OSError, ValueError):
        return os.path.normcase(str(p))


def _under(path: Any, roots: Any) -> Path | None:
    """The root this path sits inside, or None. Compares REAL paths, never spelling."""
    kp = _key(path)
    for root in roots or ():
        kr = _key(root)
        if kp == kr or kp.startswith(kr.rstrip("\\/") + os.sep):
            return Path(root)
    return None


def _short(path: Any) -> str:
    """The part of a path that tells the operator where to look."""
    text = str(path or "")
    for marker in ("lygo_llm_console", "LYGO_BUILDER_KEY"):
        idx = text.find(marker)
        if idx >= 0:
            return text[idx:]
    return text


def _drive_of(path: Any) -> str:
    text = str(path or "")
    return text[:2].upper() if len(text) > 1 and text[1] == ":" else ""


#: A dot-segment that marks a file as a committed TEMPLATE rather than a live secret. Checked on
#: every segment, so `.env.example` and `moltx.credentials.example.json` are both recognised — a
#: template is meant to be in the repo, and flagging one is a false alarm that can never clear.
_TEMPLATE_MARKERS: tuple[str, ...] = ("example", "sample", "template", "dist", "md5", "sha256")


def _is_template(name: str) -> bool:
    low = name.lower()
    if low.endswith(tuple(_TEMPLATE_EXT)):
        return True
    return any(part in _TEMPLATE_MARKERS for part in low.split("."))


def _secret_rule(name: str) -> str:
    """The rule this filename matches, or "" - the rule table is printed in the payload."""
    low = name.lower()
    # The template test comes FIRST: `.env.example` matches the dotenv pattern too, and tier 1
    # matching it first reported every example file in the tree as a live secret.
    if _is_template(low):
        return ""
    for label, pattern in _TIER1:
        if re.search(pattern, low):
            return label
    _stem, ext = os.path.splitext(low)
    if _TIER2_WORD.search(low) and ext in _DATA_EXT:
        return "named like a secret"
    return ""


def _is_expected_anywhere(path: Path) -> str:
    """The declaration this path matches, or "".

    A kit's own runtime keys are declared in `_EXPECTED`, and the SIBLING kit is a readable root on
    this machine — so the stick's `lygo_llm_console\\data\\.lygo_llm_token` is the same declared key in
    another tree, not a finding. Without this the card reported a declared key as a finding one group
    after calling it expected, which is the card contradicting itself.
    """
    kp = _key(path)
    for rel, why in _EXPECTED:
        tail = os.sep + "lygo_llm_console" + os.sep + rel.replace("/", os.sep)
        if kp.endswith(os.path.normcase(tail)):
            return why
    return ""


def _ignored_rules(root: Path) -> list[tuple[int, str, bool]]:
    """`.gitignore` as (line number, pattern, negated). A stated simplification of git's own rules."""
    path = Path(root) / ".gitignore"
    out: list[tuple[int, str, bool]] = []
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for i, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        negated = s.startswith("!")
        if negated:
            s = s[1:].strip()
        if s:
            out.append((i, s, negated))
    return out


def _gitignore_verdict(root: Path, rel: str, rules: list[tuple[int, str, bool]]) -> tuple[bool, str]:
    """(ignored, which rule decided it). Later lines win, negations un-ignore - like git, simplified."""
    ignored = False
    why = ""
    rel_posix = rel.replace(os.sep, "/")
    for line_no, pattern, negated in rules:
        p = pattern.replace("\\", "/")
        hit = False
        if p.endswith("/"):
            hit = rel_posix.startswith(p.rstrip("/") + "/") or ("/" + p.rstrip("/") + "/") in ("/" + rel_posix + "/")
        elif "/" in p:
            hit = fnmatch.fnmatch(rel_posix, p) or fnmatch.fnmatch(rel_posix, p.lstrip("/"))
        else:
            hit = fnmatch.fnmatch(rel_posix, p) or fnmatch.fnmatch(rel_posix, "*/" + p)
            hit = hit or fnmatch.fnmatch(os.path.basename(rel_posix), p)
        if hit:
            ignored = not negated
            why = f".gitignore line {line_no}: {pattern}"
    return ignored, why


# ------------------------------------------------------------------------------------------------
# A. the credential pointers
# ------------------------------------------------------------------------------------------------
def _pointer_rows(gaps: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    am = _owner("admin_map")
    tools = _owner("tools")
    if am is None or not hasattr(am, "credential_pointers"):
        gaps.append(
            "`admin_map.credential_pointers()` could not be imported, so the deny-list is UNCHECKED here "
            "— this card cannot see what was declared, which is not the same as nothing being declared"
        )
        return [], {"ok": False, "unchecked": True}
    try:
        pointers = am.credential_pointers() or {}
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"the credential pointers could not be read: {type(exc).__name__}: {exc}")
        return [], {"ok": False, "unchecked": True}
    try:
        read_roots = tuple(am.read_roots() or ())
    except Exception as exc:  # noqa: BLE001
        read_roots = ()
        gaps.append(f"the readable roots could not be listed ({type(exc).__name__}: {exc}), so whether a pointer sits inside one is UNCHECKED")
    try:
        kit = Path(_owner("paths").KIT_ROOT)  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        kit = None
        gaps.append("`paths.KIT_ROOT` could not be read, so whether a pointer sits inside the kit is UNCHECKED")

    rows: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    if not pointers:
        rows.append(_row("Pointers declared", "none", "config/admin.json declares no credential pointer — nothing to watch"))
        return rows, {"ok": True, "unchecked": False, "declared": 0, "findings": findings}

    for name, raw in sorted(pointers.items()):
        p = Path(str(raw))
        try:
            exists = p.exists()
            resolved = p.resolve() if exists else p
        except OSError:
            exists, resolved = False, p
        drive = _drive_of(resolved)
        try:
            drive_present = bool(drive) and Path(drive + os.sep).exists()
        except OSError:
            drive_present = False

        in_kit = _under(resolved, (kit,)) if kit is not None else None
        in_read = _under(resolved, read_roots) if read_roots else None
        denied = None
        if tools is not None and hasattr(tools, "_denied"):
            try:
                denied = bool(tools._denied(resolved))
            except Exception:  # noqa: BLE001
                denied = None

        if not exists and not drive_present:
            where = f"on {drive} which is not on this machine" if drive else "the file is not there"
            rows.append(_row(name, "lives elsewhere", f"{where} — a pointer for steward ops, not a fault here"))
        elif not exists:
            rows.append(_row(name, "does not resolve", f"{_short(resolved)} — {drive} is here but the file is not", dot="amber"))
            findings.append(
                {"severity": "amber", "check": "pointer", "what": f"credential pointer '{name}' does not resolve", "where": _short(resolved),
                 "why": f"{drive} is present on this machine but the file it names is not — either the pointer is stale or the file moved"}
            )
        else:
            rows.append(_row(name, "resolves", _short(resolved)))

        if in_kit is not None:
            if in_kit:
                rows.append(_row(f"{name} · inside the kit", "YES", f"{_short(in_kit)} — this ships on the stick and to git", dot="red"))
                findings.append(
                    {"severity": "red", "check": "pointer", "what": f"credential pointer '{name}' resolves INSIDE the kit tree", "where": _short(resolved),
                     "why": "a credential inside the shipped kit travels to the USB stick and to the public repo"}
                )
            if in_read is not None:
                if in_read:
                    rows.append(_row(f"{name} · inside a readable root", "YES", f"{_short(in_read)} — a limb may open this root", dot="red"))
                    findings.append(
                        {"severity": "red", "check": "pointer", "what": f"credential pointer '{name}' sits inside a root a limb can read", "where": f"{_short(resolved)} (root {_short(in_read)})",
                         "why": "the read roots are exactly what the limbs are allowed to open"}
                    )
                else:
                    rows.append(_row(f"{name} · inside a readable root", "no", f"outside all {len(read_roots)} readable roots"))
        if denied is not None:
            if denied:
                rows.append(_row(f"{name} · kernel deny rule", "covered", "tools._denied() refuses this resolved path"))
            else:
                rows.append(_row(f"{name} · kernel deny rule", "NOT covered", "tools._denied() would allow this path", dot="red"))
                findings.append(
                    {"severity": "red", "check": "pointer", "what": f"the kernel deny rule does not cover credential pointer '{name}'", "where": _short(resolved),
                     "why": "config/admin.json declares it a credential but tools._denied() does not refuse it — the declared policy and the enforced one disagree"}
                )
    return rows, {"ok": True, "unchecked": False, "declared": len(pointers), "findings": findings}


# ------------------------------------------------------------------------------------------------
# B / C. secret-shaped files, by name
# ------------------------------------------------------------------------------------------------
def _walk(root: Path, *, max_depth: int | None, budget: int) -> tuple[list[tuple[Path, str]], int, bool]:
    """Bounded walk. Returns (hits, entries walked, budget_spent). Never follows a link.

    `max_depth` is a stated RULE, not a failure: a walk that stops at depth 3 is doing exactly what
    this card says it does, so depth is not reported as an unchecked check. Only running out of
    BUDGET is a real truncation, and only that drags the card off green.
    """
    hits: list[tuple[Path, str]] = []
    walked = 0
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        here, depth = stack.pop()
        try:
            entries = list(os.scandir(here))
        except (OSError, PermissionError):
            continue
        for entry in entries:
            walked += 1
            if walked > budget:
                return hits, budget, True
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if entry.name in (".git", "node_modules"):
                    continue
                if max_depth is None or depth + 1 <= max_depth:
                    stack.append((Path(entry.path), depth + 1))
                continue
            rule = _secret_rule(entry.name)
            if rule:
                hits.append((Path(entry.path), rule))
    return hits, walked, False


def _kit_scan(kit: Path, gaps: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    rules = _ignored_rules(kit)
    expected = {_key(Path(kit) / rel): why for rel, why in _EXPECTED}
    hits, walked, spent = _walk(kit, max_depth=None, budget=_BUDGET_KIT)
    rows: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    undeclared: list[str] = []
    for path, rule in sorted(hits, key=lambda h: str(h[0])):
        kp = _key(path)
        try:
            rel = str(path.relative_to(kit))
        except ValueError:
            rel = str(path)
        if kp in expected:
            rows.append(_row(rel, "expected", f"{rule} — declared: {expected[kp]}"))
            continue
        ignored, why = _gitignore_verdict(kit, rel, rules)
        undeclared.append(rel)
        if ignored:
            rows.append(_row(rel, f"{rule} · git-ignored", why or "matched by .gitignore", dot="amber"))
            findings.append(
                {"severity": "amber", "check": "kit tree", "what": f"a secret-shaped file is in the kit tree: {rel}", "where": _short(path),
                 "why": f"{rule}; .gitignore covers it ({why or 'a pattern matched'}), so it would not be committed — but it does travel on the stick, and someone should confirm it belongs"}
            )
        else:
            rows.append(_row(rel, f"{rule} · NOT git-ignored", "git would commit this file", dot="red"))
            findings.append(
                {"severity": "red", "check": "kit tree", "what": f"a secret-shaped file would be COMMITTED to the public repo: {rel}", "where": _short(path),
                 "why": f"{rule}; no .gitignore pattern matches it — check with `git check-ignore -v {rel}` and add the pattern or move the file"}
            )
    if not hits:
        rows.append(_row("Secret-shaped files", "none", "no file in the kit is named like key material or a credential"))
    if spent:
        gaps.append(
            f"the kit walk hit its {_BUDGET_KIT:,}-entry budget after {walked:,} entries, so a file deeper "
            "in the tree was NOT seen"
        )
    return rows, findings, {"walked": walked, "budget_spent": spent, "hits": len(hits), "undeclared": undeclared}


def _root_scan(kit: Path | None, gaps: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Walk each readable root with its OWN budget, so one big root cannot starve the others."""
    am = _owner("admin_map")
    if am is None or not hasattr(am, "read_roots"):
        gaps.append("`admin_map.read_roots()` could not be imported, so the readable roots were NOT scanned")
        return [], [], {"roots": 0, "walked": 0, "budget_spent": True}
    try:
        roots = tuple(am.read_roots() or ())
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"the readable roots could not be listed: {type(exc).__name__}: {exc}")
        return [], [], {"roots": 0, "walked": 0, "budget_spent": True}

    rows: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    total = 0
    remaining = _BUDGET_WIDE_TOTAL
    spent_any = False
    starved = 0
    for root in roots:
        try:
            if not Path(root).exists():
                continue
        except OSError:
            continue
        if kit is not None and (_under(root, (kit,)) or _key(root) == _key(kit)):
            rows.append(_row(_short(root), "is the kit", "already walked above, in full, with no depth limit"))
            continue
        if remaining <= 0:
            starved += 1
            rows.append(_row(_short(root), "not walked", "every root before it spent the shared ceiling", dot="amber"))
            continue
        budget = min(_BUDGET_PER_ROOT, remaining)
        hits, walked, spent = _walk(Path(root), max_depth=_DEPTH_WIDE, budget=budget)
        remaining -= walked
        total += walked
        spent_any = spent_any or spent
        if hits:
            for path, rule in sorted(hits, key=lambda h: str(h[0]))[:12]:
                declared = _is_expected_anywhere(path)
                if declared:
                    rows.append(
                        _row(_short(path), "expected", f"{rule} — declared: {declared}; this is the sibling kit's own key, and the sibling kit is itself a readable root")
                    )
                    continue
                rows.append(_row(_short(path), rule, "outside the kit, inside a root a limb may open", dot="amber"))
                findings.append(
                    {"severity": "amber", "check": "readable roots", "what": f"a secret-shaped file sits in a readable root: {_short(path)}", "where": f"root {_short(root)}",
                     "why": f"{rule}; a limb may open this root, so confirm this file is meant to be readable"}
                )
            if len(hits) > 12:
                rows.append(_row(_short(root), f"+{len(hits) - 12} more", "capped at 12 rows in this card"))
        else:
            note = f"walked {walked:,} entries to depth {_DEPTH_WIDE}"
            if spent:
                note += " then hit its own budget"
            rows.append(_row(_short(root), "clean", note))
    if not rows:
        rows.append(_row("Readable roots", "none resolved", "a root that is not on this machine is normal, not a fault"))
    if spent_any:
        gaps.append(
            f"a readable root hit its own {_BUDGET_PER_ROOT:,}-entry budget, so files below that point were NOT "
            "seen — the root names it on its own row"
        )
    return rows, findings, {"roots": len(roots), "walked": total, "budget_spent": spent_any, "starved": starved}


# ------------------------------------------------------------------------------------------------
# D / E. the kit's own keys, and what a git remote carries
# ------------------------------------------------------------------------------------------------
def _key_rows(kit: Path, gaps: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rules = _ignored_rules(kit)
    rows: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for rel, why in _EXPECTED:
        p = Path(kit) / rel
        try:
            exists = p.is_file()
        except OSError:
            exists = False
        if not exists:
            rows.append(_row(rel, "not written yet", why))
            continue
        ignored, rule_why = _gitignore_verdict(kit, rel, rules)
        if ignored:
            rows.append(_row(rel, "present · git-ignored", f"{why} · {rule_why or 'a pattern matched'}"))
        else:
            rows.append(_row(rel, "present · NOT git-ignored", f"{why} · git would commit this", dot="red"))
            findings.append(
                {"severity": "red", "check": "declared key", "what": f"the kit's own key file would be committed: {rel}", "where": _short(p),
                 "why": f"{why}; add a .gitignore pattern or the key ships to a public repo"}
            )
    return rows, findings


_REMOTE_CRED = re.compile(r"://[^/\s@]+:[^/\s@]+@|://[^/\s@]*:[^/\s@]*token", re.IGNORECASE)


def _repo_root(start: Path, *, levels: int = 6) -> Path | None:
    """The git checkout this tree sits inside, if any.

    This kit is a SUBFOLDER of its repo — the checkout is rooted at `lygo-protocol-stack`, one level
    above `lygo_llm_console` — so looking only at `<kit>/.git` reports "not a git checkout" for a tree
    that plainly is one, which is a false statement and worse than no row at all.
    """
    here = Path(start)
    for _ in range(levels + 1):
        try:
            if (here / ".git").exists():
                return here
        except OSError:
            return None
        if here.parent == here:
            return None
        here = here.parent
    return None


def _remote_rows(kit: Path, gaps: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repo = _repo_root(kit)
    if repo is None:
        return [
            _row("Git remotes", "no checkout found", "no .git at the kit or any parent within 6 levels — normal on the stick")
        ], []
    git_dir = repo / ".git"
    if git_dir.is_file():
        return [
            _row("Checkout", _short(repo), "this tree is a git WORKTREE — .git is a file pointing at the main checkout"),
            _row("Git remotes", "config lives in the main checkout", "checked where it actually is, not where it was assumed"),
        ], []
    cfg = git_dir / "config"
    if not cfg.is_file():
        gaps.append(f"a git checkout was found at {_short(repo)} but it has no .git/config, so whether a remote carries a credential is UNCHECKED")
        return [_row("Git remotes", "no .git/config", _short(repo), dot="amber")], []
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        gaps.append(f".git/config exists but could not be read ({type(exc).__name__}: {exc}), so whether a remote carries a credential is UNCHECKED")
        return [_row("Git remotes", "unreadable", _short(cfg), dot="amber")], []
    rows: list[dict[str, Any]] = [_row("Checkout", _short(repo), "the kit sits inside this repo; the config checked is its own")]
    findings: list[dict[str, Any]] = []
    current = ""
    seen = 0
    for line in text.splitlines():
        s = line.strip()
        if s.lower().startswith("[remote"):
            current = s.strip("[]")
            continue
        if "=" not in s or not current:
            continue
        key, _, value = s.partition("=")
        if key.strip().lower() != "url":
            continue
        seen += 1
        # The URL is NEVER printed — only whether it carries a credential, because a URL with a token
        # in it IS a secret, and this card may not be the thing that leaks it.
        if _REMOTE_CRED.search(value):
            rows.append(_row(current or f"remote {seen}", "URL carries a credential", "the URL itself is not printed — it is the secret", dot="red"))
            findings.append(
                {"severity": "red", "check": "git remote", "what": f"{current or 'a remote'} has a credential embedded in its URL", "where": _short(cfg),
                 "why": "a token in a remote URL is readable by anyone who can read .git/config — use a credential helper or an SSH key"}
            )
        else:
            rows.append(_row(current or f"remote {seen}", "no credential in the URL", "checked without printing the URL"))
    if not seen:
        rows.append(_row("Git remotes", "none configured", "no remote URL to check"))
    return rows, findings


# ------------------------------------------------------------------------------------------------
# the card
# ------------------------------------------------------------------------------------------------
def build(ctx: Any = None) -> dict[str, Any]:
    gaps: list[str] = []
    groups: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    unchecked: list[str] = []

    paths_mod = _owner("paths")
    kit: Path | None = None
    if paths_mod is None or not hasattr(paths_mod, "KIT_ROOT"):
        gaps.append("`paths.KIT_ROOT` could not be imported, so the kit tree itself was NOT scanned")
        unchecked.append("kit tree")
    else:
        try:
            kit = Path(paths_mod.KIT_ROOT)
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"the kit root could not be resolved: {type(exc).__name__}: {exc}")
            unchecked.append("kit tree")

    pointer_rows, pointer_state = _pointer_rows(gaps)
    if not pointer_state.get("ok"):
        unchecked.append("credential pointers")
    findings.extend(pointer_state.get("findings") or [])
    groups.append(_group("Credential pointers", pointer_rows))

    if kit is not None:
        rows, found, scan = _kit_scan(kit, gaps)
        findings.extend(found)
        if scan.get("budget_spent"):
            unchecked.append("kit tree (budget)")
        rows.append(_row("Walked", f"{scan.get('walked'):,} entries", "no depth limit — the kit is small enough to walk whole"))
        groups.append(_group("Inside the kit", rows))

        keys_rows, keys_found = _key_rows(kit, gaps)
        findings.extend(keys_found)
        groups.append(_group("The kit's own keys", keys_rows))

        remote_rows, remote_found = _remote_rows(kit, gaps)
        findings.extend(remote_found)
        groups.append(_group("Git remotes", remote_rows))

    root_rows, root_found, root_scan = _root_scan(kit, gaps)
    findings.extend(root_found)
    if root_scan.get("budget_spent"):
        unchecked.append("a readable root (budget)")
    if root_scan.get("starved"):
        unchecked.append(f"{root_scan['starved']} readable root(s) not reached")
    groups.append(_group("Readable roots", root_rows))

    red = [f for f in findings if f.get("severity") == "red"]
    amber = [f for f in findings if f.get("severity") != "red"]

    # Green means every check RAN and found nothing. An unchecked check can never be green.
    if red:
        state = "red"
    elif amber or unchecked:
        state = "amber"
    else:
        state = "green"

    bits = []
    if red:
        bits.append(f"{_plural(len(red), 'finding')} that must not stand")
    if amber:
        bits.append(f"{_plural(len(amber), 'thing')} to confirm")
    if unchecked:
        bits.append(f"{len(unchecked)} check(s) could not complete")
    if not bits:
        bits.append("nothing readable that should not be")
    state_text = " · ".join(bits)

    lights = [
        {
            "state": "red" if any(f["check"] == "pointer" and f["severity"] == "red" for f in findings)
            else ("amber" if not pointer_state.get("ok") or any(f["check"] == "pointer" for f in findings) else "green"),
            "label": "Pointers",
            "text": f"{pointer_state.get('declared', 0)} declared" if pointer_state.get("ok") else "unchecked",
            "detail": "every credential pointer resolves, outside the kit and outside every readable root",
            "next_change": "a pointer changes only when config/admin.json changes",
        },
        {
            "state": "red" if any(f["check"] in ("kit tree", "declared key") and f["severity"] == "red" for f in findings)
            else ("amber" if any(f["check"] in ("kit tree", "declared key", "readable roots") for f in findings) else "green"),
            "label": "Files",
            "text": f"{len(findings)} finding(s)" if findings else "nothing secret-shaped",
            "detail": "checked by NAME and LOCATION only — this card never opens a credential file",
        },
        {
            "state": "red" if any(f["check"] == "git remote" for f in findings) else "green",
            "label": "Remotes",
            "text": "no credential in a remote URL" if not any(f["check"] == "git remote" for f in findings) else "a URL carries a credential",
            "detail": "a remote URL with a token in it is itself a secret",
        },
    ]

    groups.append(
        _group(
            "What this card did",
            [
                _row("Checks run", f"{max(0, 4 - len(unchecked))}/4", "a check that could not run says UNCHECKED in the gaps below — it never counts as fine"),
                _row("Findings", _plural(len(findings), "finding"), f"{len(red)} red, {len(amber)} to confirm" if findings else "none"),
                _row("Mode", "read-only", "this module fixes nothing: it reports, and the owner of the thing fixes it"),
                _row("Never opens a credential file", "true", "it stats, resolves and compares paths — it reads no key, and prints no secret"),
                _row("Rules", f"{len(_TIER1) + 1} name patterns + the kernel's own deny rule", "tier 1: key material by extension; tier 2: a secret-shaped NAME in a file a secret is actually kept in"),
                _row("Ignore rule", "matched against .gitignore, last match wins", "a simplification of git's own rules — the matching line is named on every row"),
                _row("Built", SIGNATURE, "the module's own signature"),
            ],
        )
    )

    # Stated every time, because it is the one thing this card structurally cannot do.
    gaps.append(
        "a secret inside a file whose NAME looks ordinary cannot be found by this card, because finding it "
        "would mean reading the file — and reading a credential file is the one thing it may never do"
    )
    gaps.append(
        f"the kit is walked whole (no depth limit); each readable root outside it is read to depth {_DEPTH_WIDE} "
        f"within its own {_BUDGET_PER_ROOT:,}-entry budget, so a file deeper than that in a wide root is outside "
        "this card's view — it is a stated rule, not a failure, and every root says what it walked"
    )
    gaps.append(
        "the ignore check matches .gitignore patterns itself rather than asking git, so treat it as a "
        "first pass: the pattern that matched is named on the row, and `git check-ignore -v <path>` settles it"
    )

    return {
        "ok": True,
        "signature": SIGNATURE,
        "module": MID,
        "at": _at(),
        "state": state,
        "state_text": state_text,
        "issues": findings,
        "checks": {"ran": [c for c in ("pointers", "kit tree", "readable roots", "git remotes") if c not in unchecked], "unchecked": unchecked},
        "lights": lights,
        "groups": groups,
        "missing": gaps,
        "sources": [
            "admin_map.credential_pointers() — what config/admin.json declares, and never a value",
            "admin_map.read_roots() — the roots a limb may open, resolved",
            "admin_map.drives() — which letters this machine actually has, so a pointer on another machine is not a fault",
            "tools._denied(resolved_path) — the kernel's OWN deny rule, called rather than copied",
            "os.scandir over the kit tree and each readable root — names and locations, never contents",
            "the kit's own .gitignore — matched pattern-by-pattern, with the deciding line named",
            ".git/config — whether a remote URL carries a credential, without printing the URL",
            "paths.KIT_ROOT/DATA/CONFIG — where the kit and its own keys live",
        ],
        "refresh_s": 30,
    }


# ------------------------------------------------------------------------------------------------
# module host surface
# ------------------------------------------------------------------------------------------------
def data(ctx: Any, req: Any) -> None:
    req.json(200, build(ctx))


def health(ctx: Any) -> dict[str, Any]:
    """Can this card actually run its checks on THIS tree, right now?"""
    have = [name for name in OWNERS if _owner(name) is not None]
    if not have:
        return {"ok": False, "detail": "none of the owners import on this tree: " + ", ".join(OWNERS)}
    panel = build(ctx)
    unchecked = (panel.get("checks") or {}).get("unchecked") or []
    if len(unchecked) >= 3:
        return {"ok": False, "detail": "most checks could not complete: " + ", ".join(unchecked)}
    return {
        "ok": True,
        "detail": (
            f"reads {len(have)}/{len(OWNERS)} owners · state {panel.get('state')} · "
            f"{len(panel.get('issues') or [])} finding(s), {len(unchecked)} unchecked, "
            f"{len(panel.get('groups') or [])} group(s)"
        ),
    }


def register(ctx: Any) -> dict[str, Any]:
    return {
        "routes": [("GET", ROUTE, data)],
        "limbs": [],
        "panes": [
            {
                "id": "dock.guard",
                "slot": "dock",
                "title": "Guard watch",
                "data": f"GET {ROUTE}",
                "refresh_s": 30,
                "inner_scroll": False,
            }
        ],
        "health": health,
    }
