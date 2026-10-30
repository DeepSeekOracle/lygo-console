"""lygo.envwatch — what needs fixing, in one card.

The pair to `lygo.llminfo`. That module answers *what is hooked up*; this one answers *what needs
fixing*: skill files that never reach the model, mapped paths that do not resolve, error lines the
console wrote in the background, and the daemons this kit runs and watches.

Three rules shape every line of this file:

1. **A finding is a fact with a location.** Every issue carries what, why and *where* (a path, a
   file, a log line). A panel that says "problems found" without a location is noise.
2. **A search that did not run is reported UNCHECKED, never folded into "fine".** Each collector
   says which checks it actually ran; if an owner will not import, the group says so by name.
3. **The search is stated.** `nothing needs fixing` is a claim about a named search — the panel
   prints its log rule table and how many checks ran, so the operator can audit the claim instead of
   trusting it. Green from a check that never ran is the one lie this module may not tell.

It owns no state, writes nothing, gates nothing and **fixes nothing**: a watcher that also repaired
would hide the thing it found.
"""

from __future__ import annotations

import datetime as _dt
import re
import json
import os
import socket
from collections import Counter
from pathlib import Path
from typing import Any

SIG = "Δ9Φ963-LYGO-ENVWATCH-v1"
MID = "lygo.envwatch"
ROUTE = "/api/envwatch"

#: Severity ladder. `grey` (no basis to judge) is deliberately NOT in it: it belongs to a light,
#: never to a finding.
RANK = {"green": 0, "amber": 1, "red": 2}

#: A log line counts as an error only if it matches one of these NAMED rules. Broad word-matching
#: ("error", "failed") cries wolf on engine logs full of tensor shapes, and a scanner that cries
#: wolf trains the operator to ignore it. The names are printed in the panel.
LOG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("traceback", ("traceback (most recent call last)",)),
    ("handler failed", ("handler_failed",)),
    ("engine fault", ("cuda error", "out of memory", "failed to load model", "error loading model", "terminate called")),
    ("write refused", ("failed: open:", "permissionerror", "winerror 5", "winerror 32")),
    ("refused action", ("refused to ", "refused: ")),
)

#: Which severity a rule carries when its line is recent. An old line is downgraded one step (see
#: `_age_step`), because a log entry from last week is history, not a live fault.
RULE_LEVEL = {
    "traceback": "red",
    "handler failed": "red",
    "engine fault": "red",
    "write refused": "amber",
    "refused action": "amber",
}

#: P0 verdicts that mean the gate let an answer through. Anything else is a gate ACTION and is
#: reported with its own name — never silently counted as "fine".
BENIGN_VERDICTS = ("AMPLIFY", "SOFTEN")

LOG_TAIL_BYTES = 400_000  # a 1 MB engine log must not be re-read in full every refresh
RECEIPT_SAMPLE = 200  # newest receipts inspected for gate verdicts


# ------------------------------------------------------------------------------------------------
# small helpers
# ------------------------------------------------------------------------------------------------
def _now_iso() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _size(n: Any) -> str:
    try:
        f = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024.0
    return f"{f:.1f} GB"


def _plural(n: int, one: str, many: str = "") -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _age_step(level: str, age_h: float | None) -> tuple[str, str]:
    """An old finding is still a finding, but it is not a live fault. Downgrade one step."""
    if age_h is None or age_h <= 24:
        return level, ""
    if level == "red":
        return "amber", f"last seen {age_h / 24:.1f} days ago"
    if level == "amber":
        return "green", f"only {age_h / 24:.1f} days old — kept in the rows, no longer a fault"
    return level, ""


def _tail_text(path: Path, cap: int = LOG_TAIL_BYTES) -> str:
    with open(path, "rb") as fh:
        try:
            fh.seek(-cap, os.SEEK_END)
        except OSError:
            fh.seek(0)
        return fh.read().decode("utf-8", errors="replace")


def _line_time(line: str) -> _dt.datetime | None:
    """Console lines start `[YYYY-MM-DD HH:MM:SS]`; engine logs start `YYYY-MM-DD HH:MM:SS`."""
    for cand, fmt in ((line[1:20], "%Y-%m-%d %H:%M:%S"), (line[:19], "%Y-%m-%d %H:%M:%S")):
        try:
            return _dt.datetime.strptime(cand, fmt)
        except ValueError:
            continue
    return None


def _hours_since(when: Any) -> float | None:
    if isinstance(when, (int, float)):
        try:
            return max(0.0, (_dt.datetime.now().timestamp() - float(when)) / 3600.0)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _path_key(p: Path) -> str:
    """One file, one row. On Windows `rglob("SKILL.md")` and `rglob("skill.md")` both match the same
    file, so an undeduped walk reports every bundled skill twice and invents a shadowing problem."""
    try:
        return os.path.normcase(str(p.resolve()))
    except OSError:
        return os.path.normcase(str(p))


def _port_open(port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout):
            return True
    except OSError:
        return False


_HASH_RUN = re.compile(r"[0-9A-Fa-f]{32,}")


def _safe_row(d: dict[str, Any]) -> dict[str, Any]:
    """Every string this card renders passes through here.

    A panel carrying a 64-hex run is indistinguishable from a panel leaking a hash, and this card's own
    contract forbids that shape. Redacting at the EDGE means the rule holds whichever module or stored
    record the words came from - measured 2026-09-20: a GPU crash detail remembered from an older build
    still named a CAS blob after the log quote had been fixed, because only that one path was covered.
    """
    return {k: (_HASH_RUN.sub("[hash]", v) if isinstance(v, str) else v) for k, v in d.items()}


class _Part:
    """One check's findings, kept apart so a missing owner can never look like a clean check."""

    def __init__(self, name: str, why: str) -> None:
        self.name = name
        self.why = why
        self.rows: list[dict[str, Any]] = []
        self.issues: list[dict[str, str]] = []
        self.unchecked: list[str] = []

    def row(self, k: str, v: Any = "", note: str = "", dot: str = "") -> None:
        row: dict[str, Any] = {"k": str(k), "v": "" if v is None else str(v)}
        if note:
            row["note"] = str(note)
        if dot:
            row["dot"] = dot
        self.rows.append(_safe_row(row))

    def issue(self, level: str, title: str, detail: str = "", where: str = "") -> None:
        self.issues.append(_safe_row({"level": level, "title": title, "detail": detail, "where": where,
                                      "check": self.name}))

    def unchecked_for(self, reason: str) -> None:
        self.unchecked.append(f"{self.name}: {reason}")


# ------------------------------------------------------------------------------------------------
# 1. skills — what the model can load, and what sits on disk and never arrives
# ------------------------------------------------------------------------------------------------
def _skills(seen_slugs: dict[str, str] | None = None) -> _Part:
    part = _Part("skills", "what the model is offered vs what is actually on disk")
    seen_slugs = {} if seen_slugs is None else seen_slugs
    try:
        import skills_mod  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        part.unchecked_for(f"skills_mod did not import ({type(exc).__name__}: {exc})")
        return part
    try:
        # catalog() already merges save/skills/enabled.json into each row's own `enabled` flag, so
        # reading the state file a second time here would only be a second chance to disagree with it.
        catalogue = skills_mod.catalog()
    except Exception as exc:  # noqa: BLE001
        part.unchecked_for(f"the skill catalogue could not be read ({type(exc).__name__}: {exc})")
        return part

    loaded = len(catalogue)
    on_count = sum(1 for r in catalogue if r.get("enabled"))

    layers: list[tuple[str, Path]] = []
    for label, attr, tail in (("workspace", "WORKSPACE", "skills"), ("clawhub", "INSTALLED", None)):
        raw = getattr(skills_mod, attr, None)
        if raw is not None:
            layers.append((label, Path(raw) / tail if tail else Path(raw)))
    try:
        for extra in skills_mod.extra_roots():
            layers.append(("extra", Path(extra)))
    except Exception as exc:  # noqa: BLE001
        part.unchecked_for(f"the extra skill roots could not be listed ({type(exc).__name__})")
    bundled = getattr(skills_mod, "BUNDLED", None)
    if bundled is not None:
        layers.append(("bundled", Path(bundled)))

    parse = getattr(skills_mod, "_parse_skill_md", None)
    if parse is None:
        part.unchecked_for("the skill parser is not reachable, so no file-level check of the skill tree ran")

    files_total = 0
    unparseable: list[str] = []
    shadowed: list[tuple[str, str, str]] = []  # slug, path, hidden_by
    for source, root in layers:
        if not root.is_dir():
            part.row(f"skill layer · {source}", "absent", str(root))
            if source == "extra":
                part.issue(
                    "amber",
                    "a declared skill root is not there",
                    "the console keeps looking in a folder that does not exist, so any skill that lived there cannot load",
                    str(root),
                )
            continue
        found: dict[str, Path] = {}
        try:
            for pat in ("SKILL.md", "skill.md"):
                for f in root.rglob(pat):
                    found.setdefault(_path_key(f), f)
        except OSError as exc:
            part.unchecked_for(f"the {source} skill folder could not be walked ({exc.__class__.__name__})")
            continue
        files_total += len(found)
        part.row(f"skill layer · {source}", _plural(len(found), "file"), str(root))
        if parse is None:
            continue
        slugs_here: dict[str, str] = {}
        for f in sorted(found.values(), key=str):
            try:
                rec = parse(f.read_text(encoding="utf-8", errors="replace"), f)
            except Exception:  # noqa: BLE001 - a bad file must not stop the sweep
                unparseable.append(str(f))
                continue
            if not rec:
                unparseable.append(str(f))
                continue
            slugs_here.setdefault(str(rec.get("slug") or ""), str(f))
        for slug, where in slugs_here.items():
            seen_before = seen_slugs.get(slug)
            if seen_before:
                shadowed.append((slug, where, seen_before))
            else:
                seen_slugs[slug] = where

    part.row("Catalogue", _plural(loaded, "skill"), "what the model is offered")
    part.row("Enabled", _plural(on_count, "skill"), "the rest are present but switched off")
    part.row("Files on disk", _plural(files_total, "file"), "counted once per file, deduplicated")
    if files_total:
        part.row("Unreadable", _plural(len(unparseable), "file"), "no name in the front matter — the catalogue skips it")
        part.row("Shadowed", _plural(len(shadowed), "file"), "same slug in an earlier layer — the model only sees the first one")

    if unparseable:
        part.issue(
            "amber",
            f"{_plural(len(unparseable), 'skill file')} never reach the model",
            "a SKILL.md with nothing in its front matter is skipped by the catalogue, so the skill exists on disk and not to the agent",
            "; ".join(unparseable[:3]) + (f" (+{len(unparseable) - 3} more)" if len(unparseable) > 3 else ""),
        )
    if shadowed:
        part.issue(
            "amber",
            f"{_plural(len(shadowed), 'skill')} are shadowed by an earlier layer",
            "the same slug exists twice and the model only ever sees the first copy — the other file can be edited for hours with no effect",
            "; ".join(f"{slug} → {path} (hidden by {bye})" for slug, path, bye in shadowed[:2]),
        )
    if loaded and not on_count:
        part.issue("amber", "every skill is switched off", "the catalogue loads them and none are enabled for the prompt", "")
    return part


# ------------------------------------------------------------------------------------------------
# 2. paths — mapped roots that are not firing
# ------------------------------------------------------------------------------------------------
def _impossible_target(line: str) -> str:
    """Why the path in this line cannot exist - '' when it looks like a real path.

    A refusal of a path that can never *be* a path (an embedded null, an empty target) is the
    writing guard working exactly as designed. Reporting it as a fault is how a card trains its
    operator to stop reading it, so such a line is reported as a guard that held instead.
    """
    if "\x00" in line:
        return "the path carries an embedded null character, which no filesystem accepts"
    low = line.lower()
    if "failed: open:" in low:
        tail = line.split("failed: open:", 1)[1].strip().strip("'\"")
        if not tail:
            return "the write was handed an empty path"
    return ""


def _total_bytes(files: list[Path]) -> int:
    """Total size of `files`, skipping any that vanished between the listing and the stat.

    Store folders hold session files, receipts and archives; anything that sweeps them can delete one
    while this runs, and a missing file is not a reason to fail the whole card.
    """
    total = 0
    for f in files:
        try:
            total += f.stat().st_size
        except OSError:
            continue
    return total

def _split_declared_optional(missing: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(absent on purpose, genuinely missing) using the config's own `optional_roots` declaration.

    If the paths owner cannot answer, every root stays a real finding - the conservative way round,
    because a fault wrongly called 'optional' is worse than an optional root wrongly called a fault.
    """
    try:
        import admin_map  # noqa: PLC0415

        declared = admin_map.is_optional_root
    except Exception:  # noqa: BLE001
        return [], missing
    on_purpose = [m for m in missing if declared(m.get("path"))]
    real = [m for m in missing if not declared(m.get("path"))]
    return on_purpose, real


def _paths() -> _Part:
    part = _Part("paths", "mapped roots, and whether each one resolves right now")
    mounts: list[dict[str, Any]] = []
    try:
        import workspace_map  # noqa: PLC0415

        data = workspace_map.list_mounts()
        mounts = list(data.get("mounts") or [])
    except Exception as exc:  # noqa: BLE001
        part.unchecked_for(f"workspace_map did not answer ({type(exc).__name__}: {exc})")

    if mounts:
        missing = [m for m in mounts if str(m.get("status")) == "missing"]
        absent_by_choice, missing = _split_declared_optional(missing)
        revoked = [m for m in mounts if m.get("revoked")]
        part.row("Mapped paths", _plural(len(mounts), "root"), "what the limbs may read")
        part.row("Resolving", _plural(len(mounts) - len(missing) - len(absent_by_choice), "root"), "the path exists right now")
        if revoked:
            part.row("Revoked", _plural(len(revoked), "root"), "switched off on purpose")
        if absent_by_choice:
            part.row(
                "Absent (declared optional)",
                _plural(len(absent_by_choice), "root"),
                "; ".join(str(m.get("path")) for m in absent_by_choice[:3])
                + " — set in config/admin.json as mapped only during steward ops, so absent is normal, not a fault",
            )
        if missing:
            part.issue(
                "amber",
                f"{_plural(len(missing), 'mapped path')} do not resolve",
                "a limb asked for a file under one of these answers refused, and the operator reads a refusal as a missing file",
                "; ".join(f"{m.get('path')} ({m.get('source')})" for m in missing[:3]),
            )
        writable = [m for m in mounts if m.get("write")]
        part.row("Write-enabled", _plural(len(writable), "root"), "the rest are read-only mounts — reads work, writes are not offered")
    try:
        import admin_map  # noqa: PLC0415

        for warning in admin_map.path_warnings():
            if warning:
                part.issue("amber", "the kit root is unresolved", str(warning), "admin_map.path_warnings()")
        admin = bool(admin_map.is_admin())
        part.row("Admin console", "yes" if admin else "no", "an admin tree is allowed its config roots")
        for label, fn in (("chatagent root", admin_map.chatagent_root_status), ("USB builder key", admin_map.usb_root_status)):
            try:
                st = fn()
                ok = bool(st.get("verified"))
                part.row(label, "verified" if ok else "unresolved", str(st.get("reason") or "")[:120])
                if not ok:
                    part.issue(
                        "amber",
                        f"the {label} does not verify",
                        "the console found the folder but not the marker that proves it is the right one",
                        str(st.get("root") or "") + " · " + str(st.get("reason") or ""),
                    )
            except Exception as exc:  # noqa: BLE001
                part.unchecked_for(f"{label} status failed ({type(exc).__name__})")
    except Exception as exc:  # noqa: BLE001
        part.unchecked_for(f"admin_map did not answer ({type(exc).__name__}: {exc})")

    try:
        import workspace_map  # noqa: PLC0415

        extra = [str(p) for p in workspace_map.extra_paths("read")]
        part.row("Extra read roots", _plural(len(extra), "path"), "; ".join(extra[:2]) if extra else "none mapped by the operator")
    except Exception as exc:  # noqa: BLE001
        part.unchecked_for(f"the extra read roots could not be listed ({type(exc).__name__})")
    return part


# ------------------------------------------------------------------------------------------------
# 3. background errors — what the kit wrote while nobody was looking
# ------------------------------------------------------------------------------------------------
def _errors() -> _Part:
    part = _Part("errors", "error lines in the logs, matched against the stated rules")
    try:
        import paths  # noqa: PLC0415

        log_dir = Path(paths.LOGS)
    except Exception as exc:  # noqa: BLE001
        part.unchecked_for(f"the log folder is not known ({type(exc).__name__})")
        log_dir = None

    scanned = 0
    found_any = False
    if log_dir is not None and log_dir.is_dir():
        for f in sorted(log_dir.glob("*.log")):
            try:
                lines = _tail_text(f).splitlines()
            except OSError as exc:
                part.unchecked_for(f"{f.name} could not be read ({exc.__class__.__name__})")
                continue
            scanned += 1
            hits: list[tuple[str, str]] = []
            for line in lines:
                low = line.lower()
                for name, pats in LOG_RULES:
                    if any(p in low for p in pats):
                        hits.append((name, line.strip()))
                        break
            # A refusal of a path that can never exist is the guard working, not a fault. It stays
            # visible (a row, with the reason) and never becomes a finding.
            held = [(n, ln) for n, ln in hits if n == "write refused" and _impossible_target(ln)]
            if held:
                hits = [(n, ln) for n, ln in hits if not (n == "write refused" and _impossible_target(ln))]
                part.row(f.name, _plural(len(held), "line"), "correctly refused: " + _impossible_target(held[-1][1]))
            if not hits:
                continue
            found_any = True
            by_rule = Counter(name for name, _ in hits)
            last_name, last_line = hits[-1]
            when = _line_time(last_line)
            # llama.cpp's own logs carry RELATIVE stamps (seconds since it started), not dates, and an
            # engine fault there is exactly the kind of line an operator must not be told is "live"
            # forever. The file's last write is the only age evidence on offer, so it is used - and
            # named in the finding, because the reader has to know the age did not come from the line.
            dated_by_file = when is None
            if dated_by_file:
                # A log can be rotated between the read above and this stat - `scripts/rotate_logs.py` moves
                # files the card is allowed to be reading at that moment. Losing the age is not an error: the
                # fault is still reported and still loud (age 0 means no downgrade), and a file that moved
                # mid-read must never turn the panel into a 500.
                try:
                    mtime: float | None = f.stat().st_mtime
                except OSError:
                    mtime = None
                if mtime is None:
                    age_h = 0.0
                    stamp = "undated (the log moved while it was being read)"
                else:
                    age_h = _hours_since(mtime)
                    try:
                        stamp = _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") + " (log file)"
                    except (OSError, OverflowError, ValueError):
                        stamp = "undated"
            else:
                age_h = max(0.0, (_dt.datetime.now() - when).total_seconds() / 3600.0)
                stamp = when.strftime("%Y-%m-%d %H:%M")
            level, note = _age_step(RULE_LEVEL.get(last_name, "amber"), age_h)
            if dated_by_file and note:
                note += " — dated by the log file's last write, the line itself carries no date"
            summary = " · ".join(f"{n}×{c}" if c > 1 else n for n, c in by_rule.most_common())
            part.row(f.name, _plural(len(hits), "line"), summary)
            part.issue(
                level,
                f"{f.name}: {summary}",
                # A quoted line is REDACTED of hash-looking runs. A 64-hex CAS blob name is
                # indistinguishable from a leaked hash, and this panel's own rule is that nothing of
                # that shape reaches it - the operator can open the log itself for the raw line.
                (f"last line ({stamp}): " + re.sub(r"[A-Fa-f0-9]{32,}", "[hash redacted]", last_line[-200:]))
                + (f" — {note}" if note else ""),
                str(f),
            )
        part.row("Log files searched", _plural(scanned, "file"), "save/logs/*.log, newest 400 KB each" if scanned else "none found")
        part.row("Rules used", _plural(len(LOG_RULES), "pattern set"), ", ".join(n for n, _ in LOG_RULES))
        archived = sorted((log_dir / "archive").glob("*.log")) if (log_dir / "archive").is_dir() else []
        if archived:
            part.row(
                "Archived logs",
                _plural(len(archived), "file"),
                "rotated to save/logs/archive/ and deliberately not scanned — counted here so the history stays "
                f"visible rather than hidden (newest: {archived[-1].name})",
            )
    if log_dir is not None and not scanned:
        part.unchecked_for("no *.log file in save/logs, so the background-error sweep had nothing to read")

    # The cloud brain's own record of a failed call is a background error too.
    try:
        import cloud_api  # noqa: PLC0415

        st = cloud_api.public_status()
        if st.get("degraded"):
            part.issue(
                "amber",
                "the API brain is marked degraded",
                "the console is keeping plain turns local for the cooldown instead of burning a call that keeps failing",
                f"{st.get('last_provider') or st.get('provider')} · {st.get('last_code')} · {st.get('last_error') or ''}"[:200],
            )
        tried = st.get("chain_tried") or []
        if tried:
            part.row("Provider attempts", ", ".join(str(t) for t in tried), "a provider that answered with an error code")
    except Exception as exc:  # noqa: BLE001
        part.unchecked_for(f"cloud_api did not answer ({type(exc).__name__})")

    # P0 gate actions: a verdict outside the benign set means the gate changed or held an answer.
    try:
        import paths  # noqa: PLC0415

        receipts = sorted(Path(paths.RECEIPTS).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:RECEIPT_SAMPLE]
    except Exception as exc:  # noqa: BLE001
        receipts = []
        part.unchecked_for(f"the receipt store could not be listed ({type(exc).__name__})")
    verdicts: Counter[str] = Counter()
    actions: list[str] = []
    for r in receipts:
        try:
            rec = json.loads(r.read_text(encoding="utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            verdicts["UNREADABLE"] += 1
            continue
        v = str(rec.get("gate_verdict") or "?")
        verdicts[v] += 1
        if v not in BENIGN_VERDICTS:
            actions.append(f"{v} · {rec.get('id')} · {rec.get('ts')}")
    if receipts:
        part.row("Gate verdicts", _plural(len(receipts), "receipt"), ", ".join(f"{k} {v}" for k, v in verdicts.most_common(4)))
        if actions:
            part.issue(
                "amber",
                f"{_plural(len(actions), 'answer')} were acted on by the P0 gate",
                "a verdict outside AMPLIFY/SOFTEN means the gate softened or held something — worth reading before the operator wonders what changed",
                "; ".join(actions[:2]),
            )
    if found_any:
        part.row("To clear a line", "read the file named above", "this panel reports; it does not repair")

    # The witness/event journal the kit writes for itself.
    try:
        import paths  # noqa: PLC0415

        ev = Path(paths.MYCELIUM) / "events.jsonl"
        if ev.is_file():
            lines = ev.read_text(encoding="utf-8", errors="replace").splitlines()
            bad = [ln for ln in lines if '"ok": false' in ln or '"ok":false' in ln]
            part.row("Event journal", _plural(len(lines), "event"), "save/mycelium/events.jsonl")
            if bad:
                part.issue("amber", f"{_plural(len(bad), 'event')} recorded a failure", "the journal is the kit's own record of what it did", str(ev))
        else:
            part.row("Event journal", "absent", str(ev))
    except Exception as exc:  # noqa: BLE001
        part.unchecked_for(f"the event journal could not be read ({type(exc).__name__})")
    return part


# ------------------------------------------------------------------------------------------------
# 4. monitors — the daemons this kit runs and watches
# ------------------------------------------------------------------------------------------------
def _monitors() -> _Part:
    part = _Part("monitors", "the daemons this kit can see, and whether they answer")
    ports: dict[str, int] = {}
    try:
        import paths  # noqa: PLC0415

        ports = {"engine": int(paths.LLAMA_PORT), "colibri": int(paths.COLIBRI_PORT), "console": int(paths.DEFAULT_PORT)}
    except Exception as exc:  # noqa: BLE001
        part.unchecked_for(f"the port profile could not be read ({type(exc).__name__})")

    try:
        import engine  # noqa: PLC0415

        binary = engine.resolve_binary()
        ram = engine.available_ram_bytes()
        part.row("Engine binary", "present" if binary else "MISSING", str(binary or "the engine can never boot on this tree"))
        part.row("RAM free", _size(ram), "a model needs its file size plus 2 GiB")
        if not binary:
            part.issue(
                "red",
                "the engine binary is missing",
                "no local model can boot on this tree at all — only the API brain would answer",
                "engine.resolve_binary() → None",
            )
    except Exception as exc:  # noqa: BLE001
        part.unchecked_for(f"engine did not answer ({type(exc).__name__}: {exc})")

    for label, port in ports.items():
        up = _port_open(port)
        part.row(f"Port {port}", "listening" if up else "closed", f"{label} — the port profile's value; the console may be serving this page on another port")
    try:
        import lygo_engine  # noqa: PLC0415

        st = lygo_engine.status()
        probe = st.get("probe") or {}
        part.row("Accelerator", str(probe.get("backend") or "cpu"), str(probe.get("gpu_reason") or ""))
        devices = probe.get("devices") or []
        if devices:
            d = devices[0]
            part.row("GPU", str(d.get("name")), f"{_size((d.get('free_mib') or 0) * 1024 * 1024)} free of {_size((d.get('total_mib') or 0) * 1024 * 1024)}")
        part.row("Threads", probe.get("threads"), "what the engine would use")
        if probe and probe.get("gpu_ok") is False:
            # This amber is TRUE whenever it appears, and the assistant once silenced it by calling a fit
            # verdict "proof" - a plan is arithmetic ("the card is big enough"), not evidence the engine
            # offloaded anything. Measured 2026-09-20: the plan said gpu_full for eight models while the
            # engine in use was the CPU-only base build. Report it; a proven load is `backends`' call.
            part.issue("amber", "the GPU backend is not proven on this host",
                       str(probe.get("gpu_detail") or probe.get("gpu_reason") or ""), "lygo_engine.probe()")
        part.row("colibri", "installed" if st.get("colibri") else "not installed", "the optional second engine")
    except Exception as exc:  # noqa: BLE001
        part.unchecked_for(f"lygo_engine.status() failed ({type(exc).__name__}: {exc})")

    try:
        import backends  # noqa: PLC0415

        rep = backends.report()
        part.row("Backend layer", str(rep.get("active")), f"{rep.get('reason')} — installed: {', '.join(rep.get('installed') or []) or 'none'}")
    except Exception as exc:  # noqa: BLE001
        part.unchecked_for(f"backends.report() failed ({type(exc).__name__})")

    try:
        import perf  # noqa: PLC0415

        rep = perf.report()
        part.row("Perf plan", str(rep.get("mode")), str(rep.get("reason") or ""))
    except Exception as exc:  # noqa: BLE001
        part.unchecked_for(f"perf.report() failed ({type(exc).__name__})")

    # The stack monitor: a real daemon-check that exists in this kit and is NOT run on a timer.
    try:
        import paths  # noqa: PLC0415

        st = paths.stack_root_status()
        root = Path(str(st.get("root") or ""))
        runner = root / "stack" / "lygo_stack.py"
        part.row("Stack monitor", "runnable" if runner.is_file() else "absent", f"{runner} (run deliberately, not on a refresh)")
        if not st.get("verified"):
            part.issue("amber", "the stack root does not verify", str(st.get("reason") or ""), str(st.get("root") or ""))
    except Exception as exc:  # noqa: BLE001
        part.unchecked_for(f"the stack monitor could not be located ({type(exc).__name__})")
    return part


# ------------------------------------------------------------------------------------------------
# 5. stores — the places the console writes while it works
# ------------------------------------------------------------------------------------------------
def _stores() -> _Part:
    part = _Part("stores", "the folders the console writes while it works, and their size")
    try:
        import paths  # noqa: PLC0415

        for label, folder in (("receipts", paths.RECEIPTS), ("sessions", paths.SAVE / "sessions"),
                              ("vault", paths.SAVE / "vault"), ("notepad", paths.NOTEPAD)):
            p = Path(folder)
            if not p.is_dir():
                part.issue("amber", f"the {label} store folder is missing", "the console will create it on the next write, but a store it cannot see is worth knowing about", str(p))
                continue
            files = [f for f in p.rglob("*") if f.is_file()]
            part.row(label, _plural(len(files), "file"), _size(_total_bytes(files)))
    except Exception as exc:  # noqa: BLE001
        part.unchecked_for(f"the stores could not be listed ({type(exc).__name__}: {exc})")

    try:
        import compaction  # noqa: PLC0415

        st = compaction.safe_status()
        win = st.get("window") or {}
        part.row("Session record", f"{st.get('turns_total')} turns", f"{st.get('compactions')} compactions · {st.get('sessions_sealed')} sealed")
        part.row("Window", f"{win.get('used_pct')}%", "auto-compact folds the oldest turns at " + str(win.get("auto_compact_pct")) + "%")
        rc = st.get("recall") or {}
        if rc.get("runs"):
            part.row("Recall (RAG)", _plural(int(rc.get("runs") or 0), "search").replace("1 searchs", "1 search"),
                     f"last handed over {int(rc.get('chars') or 0):,} chars"
                     + (f" · {int(rc.get('empty') or 0)} found nothing" if rc.get("empty") else "")
                     + f" · index {int((st.get('rag') or {}).get('blocks') or 0):,} blocks")
        if win.get("will_compact_next_turn"):
            part.issue("amber", "the next turn will compact the record", "the oldest turns are about to be folded into the record — expected, but it changes what the model can still see", f"used {win.get('used_pct')}% of {win.get('ctx')}")
    except Exception as exc:  # noqa: BLE001
        part.unchecked_for(f"compaction.safe_status() failed ({type(exc).__name__})")
    return part


# ------------------------------------------------------------------------------------------------
# the panel
# ------------------------------------------------------------------------------------------------
def _missing() -> list[str]:
    return [
        "a skill that loads and then misbehaves is invisible here: nothing in this tree records per-skill execution, so this panel can only say what loaded, never what worked",
        "another application's daemon — or a Windows service — cannot be seen from this kit; only the console's own engine, backend layer and stack monitor are visible",
        "an error a module swallows silently leaves no line to find: this check reads the console's own logs, so a fault that was never written down cannot be reported",
        "on the WEB edition there is no disk, no engine and no log file, so skills, paths and background errors are unreadable from a browser tab",
        "a file that is readable but wrong (a mount that exists and points at the wrong drive) passes every check here: this panel tests reachability, not meaning",
    ]


def build(ctx: Any = None) -> dict[str, Any]:
    """Collect everything into one panel. Never raises: a watcher that dies reports nothing."""
    parts = [_skills({}), _paths(), _errors(), _monitors(), _stores()]
    by_name = {p.name: p for p in parts}

    def part_of(name: str) -> _Part:
        """A light reads its check by name; a check that did not arrive must not blank the card."""
        return by_name.get(name) or _Part(name, "not collected")

    issues: list[dict[str, str]] = []
    unchecked: list[str] = []
    for p in parts:
        issues.extend(p.issues)
        unchecked.extend(p.unchecked)
    if unchecked:
        # A check that did not run is not a clean check. Without this, a tree where every owner is
        # missing would report "nothing needs fixing" - the exact lie this module exists to avoid.
        issues.append({
            "level": "amber",
            "title": f"{_plural(len(unchecked), 'check')} could not run",
            "detail": "an unexamined part of the environment cannot be reported as good, so the card stays amber until the check can run again",
            "where": "; ".join(unchecked)[:300],
            "check": "checks",
        })
    issues.sort(key=lambda i: (-RANK.get(str(i.get("level")), 0), str(i.get("title"))))

    ran = [p.name for p in parts if not p.unchecked]
    state = "green"
    for i in issues:
        if RANK.get(str(i.get("level")), 0) > RANK[state]:
            state = str(i.get("level"))
    bad = [i for i in issues if i.get("level") != "green"]

    worst = {"green": "green", "amber": "amber", "red": "red"}[state]
    if bad:
        state_text = f"{_plural(len(bad), 'thing')} need{'s' if len(bad) == 1 else ''} fixing"
    else:
        state_text = f"nothing needs fixing — {_plural(len(parts), 'check')} ran clean"

    # ---- groups ---------------------------------------------------------------------------------
    fix = {"title": "Needs fixing", "rows": []}
    if bad:
        for i in bad:
            fix["rows"].append({"k": i["title"], "v": i.get("detail") or "", "note": i.get("where") or i.get("check", ""), "dot": i["level"]})
    else:
        fix["rows"].append({
            "k": "nothing needs fixing",
            "v": ", ".join(p.name for p in parts) + " all clean",
            "note": "this card turns amber or red the moment one of them finds something — green here means the searches ran and came back empty",
            "dot": "green",
        })

    checks = {"title": "Checks that ran", "rows": [{"k": p.name, "v": _plural(len(p.rows), "row"), "note": p.why[:90] if p.why else ""} for p in parts]}
    if unchecked:
        checks["rows"].append({"k": "UNCHECKED", "v": _plural(len(unchecked), "check"), "note": "; ".join(unchecked)[:240], "dot": "amber"})

    groups = [fix] + [{"title": p.name.replace("_", " ").title(), "rows": p.rows} for p in parts if p.rows] + [checks]

    # ---- lights ---------------------------------------------------------------------------------
    skills_part = part_of("skills")
    errs = part_of("errors")
    monitors = part_of("monitors")
    lights: list[dict[str, Any]] = [{
        "id": "env",
        "label": "Environment",
        "state": worst,
        "text": state_text,
        "detail": " · ".join(i["title"] for i in bad[:3]) if bad else "checks: " + ", ".join(ran),
    }]

    s_issues = [i for i in skills_part.issues if i.get("level") != "green"]
    lights.append({
        "id": "skills",
        "label": "Skills",
        "state": "amber" if s_issues else ("green" if skills_part.rows else "grey"),
        "text": (s_issues[0]["title"] if s_issues else f"{_plural(len(skills_part.rows), 'row')} read") if skills_part.rows else "not checked",
        "detail": s_issues[0].get("where", "") if s_issues else "what the model is offered matches what is on disk",
    })

    e_issues = [i for i in errs.issues if i.get("level") != "green"]
    lights.append({
        "id": "background",
        "label": "Background",
        "state": "red" if any(i["level"] == "red" for i in e_issues) else ("amber" if e_issues else ("green" if errs.rows else "grey")),
        "text": e_issues[0]["title"] if e_issues else ("no error lines in the logs searched" if errs.rows else "not checked"),
        "detail": e_issues[0].get("detail", "")[:220] if e_issues else "save/logs/*.log matched none of the " + str(len(LOG_RULES)) + " stated rules",
    })

    m_issues = [i for i in monitors.issues if i.get("level") != "green"]
    lights.append({
        "id": "monitors",
        "label": "Monitors",
        "state": "red" if any(i["level"] == "red" for i in m_issues) else ("amber" if m_issues else ("green" if monitors.rows else "grey")),
        "text": m_issues[0]["title"] if m_issues else ("engine, backends and the stack monitor are where they should be" if monitors.rows else "not checked"),
        "detail": m_issues[0].get("where", "") if m_issues else "nothing here is watched on a timer; the checks run when this card refreshes",
    })

    # ---- rows for a few facts the operator asks first --------------------------------------------
    groups.append({
        "title": "What this card did",
        "rows": [
            {"k": "Checks run", "v": f"{len(ran)}/{len(parts)}", "note": "a check it could not run says UNCHECKED above, it never counts as fine"},
            {"k": "Findings", "v": _plural(len(bad), "finding"), "note": "each one names a location you can open"},
            {"k": "Mode", "v": "read-only", "note": "this module fixes nothing: it reports, and the owner of the thing fixes it"},
            {"k": "Rules", "v": f"{len(LOG_RULES)} named log patterns", "note": ", ".join(n for n, _ in LOG_RULES)},
            {"k": "Built", "v": SIG, "note": "the module's own signature"},
        ],
    })

    return {
        "ok": True,
        "signature": SIG,
        "module": MID,
        "at": _now_iso(),
        "state": worst,
        "state_text": state_text,
        "issues": issues,
        "checks": {"ran": ran, "unchecked": unchecked},
        "lights": lights,
        "groups": groups,
        "missing": _missing(),
        "sources": [
            "skills_mod.catalog()/load_state()/extra_roots() — what the model is offered",
            "the skill folders walked directly — what sits on disk and never arrives",
            "workspace_map.list_mounts()/extra_paths() — mapped roots and whether they resolve",
            "admin_map.path_warnings()/chatagent_root_status()/usb_root_status() — the roots that must verify",
            "save/logs/*.log — background error lines, matched by the stated rule table",
            "cloud_api.public_status() — a provider that answered with an error code",
            "save/receipts/*.json — P0 gate verdicts, never the message text",
            "lygo_engine.status()/backends.report()/perf.report()/engine.resolve_binary() — the daemons",
            "paths.stack_root_status() — the stack monitor, located and not run",
            "compaction.safe_status() — the record, the window and the stores",
        ],
        "refresh_s": 10,
    }


def data(ctx: Any, req: Any) -> None:
    """GET /api/envwatch — the one card."""
    req.json(200, build(ctx))


def health(ctx: Any) -> dict[str, Any]:
    """Can this card read the owners of its findings on THIS tree, right now?"""
    owners = ("skills_mod", "workspace_map", "admin_map", "lygo_engine", "backends", "perf", "compaction", "cloud_api", "paths")
    have = [n for n in owners if _importable(n)]
    panel = build(ctx)
    if len(have) < 3:
        return {"ok": False, "detail": f"only {len(have)}/{len(owners)} fact owners importable: {', '.join(have) or 'none'}"}
    return {
        "ok": True,
        "detail": (
            f"reads {len(have)}/{len(owners)} owners · state {panel['state']} · "
            f"{len(panel['issues'])} finding(s), {len(panel['checks']['unchecked'])} unchecked, "
            f"{len(panel['groups'])} group(s), {len(panel['missing'])} named gap(s)"
        ),
    }


def _importable(name: str) -> bool:
    try:
        __import__(name)  # noqa: PLC0415
        return True
    except Exception:  # noqa: BLE001 - reported as a gap, never raised
        return False


def register(ctx: Any) -> dict[str, Any]:
    return {
        "routes": [("GET", ROUTE, data)],
        "limbs": [],
        "panes": [{
            "id": "dock.env",
            "slot": "dock",
            "title": "Environment watch",
            "data": f"GET {ROUTE}",
            "refresh_s": 10,
            "inner_scroll": False,
        }],
        "health": health,
    }
