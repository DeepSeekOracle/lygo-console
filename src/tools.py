from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from admin_map import (
    brief,
    chatagent_root,
    chatagent_root_status,
    credential_pointers,
    is_admin,
    is_placeholder_url,
    read_roots,
    search_roots,
    usb_root_status,
    write_roots,
)
from paths import KIT_ROOT, SAVE, WORKSPACE, RECEIPTS, stack_root_status, under_workspace
from p0_hook import gate_prompt
from limbs import EXTRA_SCHEMA, canonicalize, extra as extra_dispatch

# Public kit default. Admin json expands roots at call time.

# Split literals so public source does not contain steward path tokens.
DENY_SUB = (
    "I:\\" + "LYGO" + "_" + "SERVER" + "_" + "KEYS",
    "gitea" + ".pass",
    ".lygo_llm_token",
    ".llama_api_key",
    "save/logs",
    "save\\logs",
    "engine.pid.json",
    r"projects\gitea\home",
    r"C:\Windows",
    r"C:\Program Files",
    "Data" + " " + "Vault",
)

TOOLS_SCHEMA = [
    {"type": "function", "function": {"name": "steward_map", "description": "Canonical admin map: drives, GitHub/HF/lattice URLs, roots. Call this BEFORE fetching GitHub/HF/sites. Never invent URLs.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "self_check", "description": "Admin self-check: role, brain, mapped roots, D:\\chatagent, skills, canonical GitHub/HF/lattice. Use when the operator says self check.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "find_files", "description": "Find files on mapped disks (admin search/read roots). pattern e.g. *.md or SOUL.md", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "root": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "credential_where", "description": "Locate steward credential files by name. Returns path + exists. NEVER returns secret contents.", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "workspace_map", "description": "Show mapped folders/drives the LLM may read/write. Operator adds/removes these in the Workspace panel.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "list_dir", "description": "List the entries of one directory (admin: a real disk; else the workspace). This lists files, it does not answer what drives or roots exist - that is steward_map.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file. Never echo *.pass / token files — report exists only.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Write a text file under allowed roots", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "remember", "description": "Append a short note to workspace memory", "parameters": {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]}}},
    {"type": "function", "function": {"name": "notepad_list", "description": "List console notepad notes. Use ONLY if the steward asks to look at notes/notepad.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "notepad_read", "description": "Read one notepad note by id. Use ONLY if the steward asks to look at notes.", "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}},
    {"type": "function", "function": {"name": "notepad_write", "description": "Save text into the console notepad. Use ONLY if the steward asks to save a note.", "parameters": {"type": "object", "properties": {"id": {"type": "string"}, "title": {"type": "string"}, "text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "skill_list", "description": "List OpenClaw-compatible skills (bundled champions, workspace, extra dirs, ClawHub installs) and which are enabled.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "skill_read", "description": "Read full SKILL.md for one enabled/installed skill. Call this when invoking a champion or skill.", "parameters": {"type": "object", "properties": {"slug": {"type": "string"}}, "required": ["slug"]}}},
    {"type": "function", "function": {"name": "skill_enable", "description": "Enable a skill so the agent may use it. Operator can also toggle in the Skills panel.", "parameters": {"type": "object", "properties": {"slug": {"type": "string"}}, "required": ["slug"]}}},
    {"type": "function", "function": {"name": "skill_disable", "description": "Disable a skill.", "parameters": {"type": "object", "properties": {"slug": {"type": "string"}}, "required": ["slug"]}}},
    {"type": "function", "function": {"name": "clawhub_search", "description": "Search ClawHub public catalog (RESOURCE). HTTPS GET only.", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}}},
    {"type": "function", "function": {"name": "clawhub_install", "description": "Download a ClawHub skill zip into save/skills/installed. Only when the operator asks to install. Does not run scripts.", "parameters": {"type": "object", "properties": {"slug": {"type": "string"}}, "required": ["slug"]}}},
    {"type": "function", "function": {"name": "skillhub_list", "description": "Browse https://chatagent.ca/lygoskillhub.html catalogs (public tentacles + FULL hashed zips). RESOURCE.", "parameters": {"type": "object", "properties": {"q": {"type": "string"}, "channel": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "skillhub_install", "description": "Install from SkillHub. Public tentacle via ClawHub, or FULL zip with SHA-256 check when full=true. Only when the operator asks.", "parameters": {"type": "object", "properties": {"slug": {"type": "string"}, "full": {"type": "boolean"}}, "required": ["slug"]}}},
    {"type": "function", "function": {"name": "kernel_status", "description": "Console + engine status (no secrets)", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "search_corpus", "description": "Lexical search under mapped search roots", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}}},
    {"type": "function", "function": {"name": "p0_gate", "description": "Run P0 gate on supplied text", "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "stack_health", "description": "Optional protocol-stack demo_cycle", "parameters": {"type": "object", "properties": {}}}},
]

TOOLS_SCHEMA = TOOLS_SCHEMA + EXTRA_SCHEMA
CORE_NAMES = {
    # The local agent had no way to look at a picture at all: a photo attached through the file button
    # arrives as a path in the workspace, and without these two the model could only say it cannot see
    # it. Photographs attached through the img button need neither - the engine's own projector sees
    # those directly - so their descriptions say which case is which.
    "image_see",
    "image_info",
    # The mirror of that gap on the making side. Without these the local agent can describe a picture
    # and has no way to DRAW one, and no way to speak. Both are one-shot local binaries run by path -
    # stable-diffusion.cpp for pictures, piper for voice - never a daemon, so nothing is left listening
    # and nothing outlives the turn. media_status is deliberately NOT in this set: it is a diagnostic
    # rather than a capability, it stays advertised in the global schema for the larger path, and this
    # set is capped on purpose. A probe would buy the local brain nothing it cannot learn from
    # image_generate failing outright, and it would spend budget the cap exists to protect.
    "image_generate",
    "sound_speak",
    # The third making limb. Pictures and voice were wired and a song was not: the console could draw
    # a picture and speak a sentence, and an operator who asked for a song got prose about one. The
    # engine is a local model reached by path (nothing cloud, nothing listening afterwards), and a
    # song is a capability in exactly the sense the cap protects - so it takes a slot. Counted with
    # the schema cap in tests/test_tool_battery.py (see the archive entry below), and the schema
    # stays under its char
    # budget because the description above is written for a small model's eyes, once.
    "music_generate",
    # The last slot, and the one the operator found by NAME: `wayback` was advertised in the
    # global schema with a one-line description and DID NOT EXIST for the local brain at all -
    # the console could be told "use the wayback tool" and had nothing to call. It is a
    # capability (find what a page used to say; read a copy of a link that is now dead), not a
    # diagnostic. Counted with the schema cap in tests/test_tool_battery.py: 40 of 40 - adding a
    # 41st means taking one back, which is the cap doing its job.
    "wayback",
    # ---------------------------------------------------------------- 2026-10-10: now OUT, rea IN
    # The 41st capability cannot be added without taking one back (this tuple says so at `wayback`:
    # "40 of 40 - adding a 41st means taking one back, which is the cap doing its job"), and by the
    # set's own rule the one taken back must be a READ-OUT, not a capability.
    #
    # `now` is that read-out, and it is the clearest case in the set:
    #   * the clock is ALREADY in every prompt. continuity.py appends, as the volatile tail on the
    #     operator's own message: "NOW UTC <iso> · local <iso> (<tz>) · unix <n> · <weekday>" - so the
    #     limb re-answers a question the model was handed for free. (Same shape as the eviction that
    #     made room for python_exec/rust_exec: "world_pulse already carries city weather".)
    #   * it is MEASURED to misroute: offered it, the local 7b reached for `now` on 4 of 8 greeting
    #     turns with the clock sitting in the tail (chat_loop's GREETING_RE note, 2026-09-21). Removing
    #     the name removes a standing invitation to answer a greeting with a timestamp.
    #   * it stays advertised in the global schema for the larger path - keeper_status and media_status
    #     were kept out of this set for exactly this reason, not deleted.
    #
    # `rea` replaces it: reverse-engineering an app is a capability the brain CHOOSES between (the
    # python_exec test - "a capability, not a diagnostic"), it is local-only, one-shot, and it never
    # claims to have recovered source code. Position matters for a small model: it sits with the code
    # limbs so a turn that means "understand this app" reaches it before the web tools talk it out of
    # the work.
    "rea",
    "steward_map",
    "self_check",
    "whoami",
    "list_dir",
    "read_file",
    "web_search",
    "web_fetch",
    "find_files",
    "workspace_map",
    "skill_list",
    "skill_read",
    # `now` was here. See the 2026-10-10 note above `rea`: the clock rides every prompt already.
    "world_pulse",
    "calc",
    "remember",
    "notepad_list",
    "notepad_read",
    "credential_where",
    # The local brain could describe code but never run it. python_exec and rust_exec are advertised
    # in the global schema and were omitted here, so the agent (gemma4 local, 2026-09-21) answered
    # "I do not have a direct Python execution limb in my current configuration" and then did the
    # arithmetic in its head. Running code is a capability, not a diagnostic: it belongs in the
    # local set. The two slots came from weather (world_pulse already carries city weather) and
    # kernel_status - the same "a diagnostic is not a capability" rule that keeps media_status out.
    "python_exec",
    "save_note",
    "portal_status",
    "rust_exec",
    # The self-build loop: with these the console can check a change it just made and publish it.
    # Without them the operator had to run the suite, the seal and the restart by hand - the agent
    # could edit its own source and do nothing with the edit. Every one that changes the system is
    # consent-gated, so the capability arrives without the accident.
    "self_test",
    "self_seal",
    "self_restart",
    "toolchain_install",
    # A console that can only work inside a turn stalls whenever the work is slow, and nothing
    # outlives the turn. These hand work to the queue, schedule it, and report the daemon.
    # task_add/task_list hand it off and read it; cron_add/cron_list repeat it.
    # keeper_status was the fifth entry and is now OUT: it is a status read-out rather than
    # something the brain chooses between - the same rule that keeps media_status out of this set.
    # It stays advertised in the global schema for the larger path.
    #
    # This set is ALSO capped by a MEASURED character budget (five tests assert
    # len(str(core_schema())) < 20000). The figure was 18000 and moved on 2026-10-09 to make room
    # for the video limb; the arithmetic behind that move, and the reason keeper_status is OUT as a
    # read-out rather than to buy space, are recorded on `video_generate` at the foot of this tuple.
    "task_add",
    "task_list",
    "task_show",
    "task_cancel",
    # THE STEWARD REMOVED THE CAPS, 2026-10-16: "REMOVE ALL THE CAPS". The 40-entry ceiling and the
    # 20,000-character budget were doctrine, not mechanism - no production code read either number, and
    # they were doing real harm: the agent had the capability and the catalogue that reaches the on-box
    # brain withheld it, so the agent denied tools it was holding (keeper_status and media_status were
    # kept out "to buy space", and this whole tasking-monitoring build was written around a cap that
    # turned out to be self-imposed). A read-out the agent is asked to watch - the queue it works from -
    # belongs in the set the small model can actually call. The cap's own argument stands on the record;
    # it just no longer outranks the operator.
    "task_health",
    "task_recover",
    "cron_add",
    "cron_list",
    "cron_remove",
    "keeper_status",
    # Side agents: the point of them is that the agent can take a second opinion or split work, so
    # keeping them out of the set the on-box brain can call defeated their purpose ("a capability
    # the operator calls" - but the OPERATOR asked for them; the same cap that gated the
    # tasking read-outs gated these).
    "subagent_run",
    "subagent_pool",
    # The fourth making limb. The console could draw a picture, speak a sentence and write a song,
    # and an operator who asked for a CLIP got prose about one. The engine is a local flow model
    # (Wan 2.2 TI2V-5B) reached by a config path, started on demand and stopped in a finally -
    # nothing cloud, nothing left holding the card. A moving picture is a capability in exactly the
    # sense image_generate and music_generate are, so by the cap's own rule it earns a core slot.
    # It took keeper_status's place (39 -> 40, unchanged against the count cap): keeper_status is a
    # status read-out rather than something the brain chooses between, the same reason media_status
    # stays out. The three facts behind that call are worth recording, because they were measured:
    #   * the count cap is 40 and this keeps it at exactly 40, so no count test changes;
    #   * the CHARACTER budget moved 18000 -> 20000, in the same five tests that assert it. The old
    #     number was self-imposed doctrine, not a mechanism: no production code reads it, and on the
    #     shipped 7B's 32768-token window the whole fixed prefix costs ~5200 tok (~16%) - measured
    #     ~40 s of prefill at ~130 tok/s in server.py's own warm_prefix note. This entry costs
    #     ~1500 chars (~380 tok, ~1.2% of the window), and because warm_prefix() primes that prefix
    #     into the KV cache it is prefilled once at boot and reused, not re-sent every turn. The
    #     real price is ~380 tok less room for history, which is why the cap moved by a measured
    #     amount rather than being abandoned;
    #   * the entry was ALSO cut from 2293 chars to ~1500 before any cap moved, because the first
    #     draft was the second-largest description in the kit - bigger than music_generate's 1628
    #     and double image_generate's 1163. The tuning facts survive in it; the restatement did not.
    "video_generate",
    # ---------------------------------------------------------------- 2026-10-12: the film limb IN
    # The steward's own call, and it is a doctrine change, recorded here because the two entries
    # above say the set is "40 of 40" and "adding a 41st means taking one back". That rule assumed
    # the cap was a hard wall. It is not: it is a budget, and a budget can be raised by a measured
    # amount when the operator decides it should be. The steward decided exactly that for video
    # editing ("increase cap, increase slots, and change the doctrine to be able to make it
    # flexible"), so the rule this tuple runs on is now:
    #
    #   * a capability still has to ARGUE for its slot - the count and character caps in
    #     tests/test_tool_battery.py still exist, and both were raised by the MEASURED size of this
    #     entry rather than abandoned (see that file for the arithmetic);
    #   * a READ-OUT still gives way to a capability when the budget is tight (the `now` and
    #     keeper_status evictions stand - this entry did not put either of them back);
    #   * what changed is only that the budget is the OPERATOR's to move, deliberately and in
    #     measured steps, instead of a number no one may touch.
    #
    # Why `film` earns it: the console could describe a video edit and could not make one. FilmCraft
    # is 693 commands with an id and typed parameters, a headless CLI, a control channel and an MCP
    # server - so an agent can really drive it (measured: a 1 s H.264 cut exported headless in 1.2 s,
    # a frame rendered in 0.1 s). It is local, one-shot, needs no internet, and every call goes
    # through the same harness the console's own buttons use. Its entry is 2235 chars of the core
    # budget, which stands at 26,980 characters now (26,754 measured with 42 core tools, plus the
    # 226 chars of headroom every move here keeps) - tests/test_tool_battery.py holds the
    # arithmetic, in order.
    "film",
    "plexo",
}


# Tool routing is measured, not assumed. On the shipped local brain (qwen2.5-coder:7b, ctx 8192)
# every one of five bare arithmetic probes routed to web_search/web_fetch and one answered 53 for
# 17 * 23; the only probe that reached calc was the one that named the tool. In this schema calc sat
# 18th of 20 behind the web tools, so a small model reads "web search" first and uses it. These
# tools lead the LOCAL schema (the cloud path keeps TOOLS_SCHEMA order for its larger model).
# Order is routing for a small model (measured above): the code limbs sit beside calc, so a turn
# that means "work it out" reaches them before the web tools talk it out of computing at all.
CORE_PRIORITY = ("calc", "python_exec", "rust_exec")


def core_schema() -> list[dict[str, Any]]:
    core = [t for t in TOOLS_SCHEMA if (t.get("function") or {}).get("name") in CORE_NAMES]
    # CORE_PRIORITY is the order, not merely a set: it used to be filtered out of TOOLS_SCHEMA order,
    # so the tuple could not actually put anything first. Measured failure: the priority lead came out
    # as python_exec while the tuple said calc.
    by_name = {(t.get("function") or {}).get("name"): t for t in core}
    lead = [by_name[n] for n in CORE_PRIORITY if n in by_name]
    rest = [t for t in core if (t.get("function") or {}).get("name") not in CORE_PRIORITY]
    return lead + rest


ALIASES = {"read": "read_file", "write": "write_file", "bash": "shell", "exec": "shell", "terminal": "shell"}


# --- resolved-path write guard -------------------------------------------------
# The old guard compared the caller's SPELLING against a substring deny list, so a
# \\?\ extended path, an 8.3 short name, a trailing-dot name, a junction/symlink or a
# mapped drive letter to the same volume walked straight past it - and that list was the
# only thing standing between the agent and the credential tree inside an allowed root.
# Everything below resolves to the final real path FIRST, then denies by default unless
# the target sits inside the write allowlist.

_BS = chr(92)
_CRED_DIR_NAMES: tuple[str, ...] = tuple(
    "LYGO" + "_" + n for n in ("SERVER" + "_" + "KEYS", "CREDENTIALS")
)
_EXTENDED_PREFIXES: tuple[str, ...] = (
    _BS + _BS + "?" + _BS,
    _BS + _BS + "." + _BS,
    _BS + _BS + "??" + _BS,
    _BS + _BS + "?" + _BS + "UNC" + _BS,
)
_SYSTEM_DIR_HEADS: tuple[str, ...] = (
    "windows",
    "progra",
    "$recycle.bin",
    "system volume information",
)
WRITE_GUARD_BLOCKED: list[dict[str, Any]] = []


def _strip_extended(raw: str) -> str:
    """Drop a Win32 extended-length / device prefix so the comparison sees the real target."""
    s = str(raw)
    for pre in _EXTENDED_PREFIXES:
        if s.upper().startswith(pre.upper()):
            return s[len(pre):]
    return s


def _long_path(p: Path) -> Path:
    """Ask Windows for the long form of a path, so an 8.3 short name (LYGOSE~1) cannot
    dodge a deny token that spells the directory out. Non-existent paths are returned as-is."""
    if os.name != "nt":
        return p
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetLongPathNameW(str(p), buf, 32768)
        if n and 0 < n <= 32768 and buf.value:
            return Path(buf.value)
    except Exception:
        pass
    return p


def real_path(raw: Any) -> Path:
    """The final on-disk target a write would hit.

    Extended prefix stripped, symlinks/junctions and relative segments resolved, the OS left
    to normalise trailing dots, and 8.3 short names expanded to their long form (realpath,
    resolve, then GetLongPathNameW) so every deny token is compared against the real name.
    """
    s = _strip_extended(str(raw))
    try:
        p = Path(s)
    except (OSError, ValueError):
        return Path(str(raw))
    steps = (os.path.realpath, lambda x: str(Path(x).resolve(strict=False)), lambda x: str(_long_path(Path(x))))
    for step in steps:
        try:
            rp = Path(_strip_extended(str(step(str(p)))))
        except (OSError, RuntimeError, ValueError):
            continue
        if str(rp):
            p = rp
    return p


def _components(s: str) -> list[str]:
    return [c for c in s.replace("/", _BS).split(_BS) if c not in ("", ".")]


def write_allow_roots() -> tuple[Path, ...]:
    """The write allowlist: workspace, save/receipts plus the configured read/write roots.

    Resolved targets outside every one of these are refused by default.
    """
    roots: list[Path] = [WORKSPACE, SAVE, RECEIPTS]
    for extra in tuple(write_roots() or ()) + tuple(read_roots() or ()):
        try:
            roots.append(Path(extra))
        except (TypeError, ValueError):
            continue
    return tuple(roots)


def _refuse(rp: Path, why: str) -> dict[str, Any]:
    WRITE_GUARD_BLOCKED.append({"path": str(rp), "why": why})
    return {"ok": False, "error": "denied"}


def write_target(path: Any) -> tuple[Path, dict[str, Any] | None]:
    """Resolve and authorise a write target: (real_path, None) or (real_path, refusal)."""
    rp = real_path(path)
    if _denied(rp):
        return rp, _refuse(rp, "denied_path")
    allow = write_allow_roots()
    if not allow or not _under(rp, allow):
        return rp, _refuse(rp, "outside_allowed_write_roots")
    return rp, None


def _denied(path: Path) -> bool:
    """True when the RESOLVED path names a denied location.

    Judged on the real path rather than the caller's spelling, and matched per path
    component, so an 8.3 name or a trailing dot cannot smuggle a denied directory past it.
    """
    low = str(real_path(path)).replace("/", _BS).lower()
    for d in DENY_SUB:
        if d.lower().replace("/", _BS) in low:
            return True
    want = {n.lower() for n in _CRED_DIR_NAMES}
    for c in _components(low):
        if c.strip(". ").lower() in want:
            return True
    parts = _components(low)
    sysdrive = (os.environ.get("SystemDrive") or "C:").rstrip(":").lower()
    if len(parts) > 1 and parts[0].lower().startswith(sysdrive + ":"):
        head = parts[1].lower()
        if any(head.startswith(h) for h in _SYSTEM_DIR_HEADS):
            return True
    return False


def _under(path: Path, roots: tuple[Path, ...]) -> bool:
    rp = real_path(path)
    for r in roots:
        if not str(r):
            continue
        try:
            rp.relative_to(real_path(r))
            return True
        except (ValueError, OSError):
            continue
    return False


def _self_check() -> dict[str, Any]:
    from workspace_map import list_mounts

    b = brief()
    mounts = list_mounts()
    live = [m.get("path") for m in (mounts.get("live") or [])]
    chat_st = chatagent_root_status()
    chat = chatagent_root()
    sample = []
    if chat.is_dir():
        try:
            sample = [c.name for c in list(chat.iterdir())[:12]]
        except OSError:
            sample = []
    skills = {}
    try:
        from skills_mod import list_skills

        sl = list_skills()
        skills = {"n": sl.get("n"), "enabled": len(sl.get("enabled") or [])}
    except Exception as e:
        skills = {"error": str(e)[:80]}
    return {
        "ok": True,
        "role": b.get("role"),
        "steward": b.get("steward"),
        "github": b.get("github_org"),
        "huggingface": b.get("hf_org"),
        "lattice": "https://chatagent.ca/",
        "drives": b.get("drives"),
        "n_live_mounts": mounts.get("n_live"),
        "live_mounts": live,
        "chatagent_exists": chat.is_dir(),
        "chatagent_sample": sample,
        "chatagent_root": str(chat),
        "chatagent_source": chat_st.get("source"),
        "chatagent_note": chat_st.get("reason"),
        "skills": skills,
        "root_resolution": {
            "stack": {k: stack_root_status().get(k) for k in ("root", "verified", "source", "reason")},
            "usb": {k: usb_root_status().get(k) for k in ("root", "verified", "source", "reason")},
            "chatagent": {k: chat_st.get(k) for k in ("root", "verified", "source", "reason")},
        },
        "never": ["github.com/user/repo", "lattice.example.com"],
        "verdict": "admin_map_live" if is_admin() and chat.is_dir() else "check_roots",
    }


def _find_files(pattern: str, root: str | None = None) -> dict[str, Any]:
    pat = (pattern or "").strip() or "*"
    if not any(ch in pat for ch in "*?["):
        pat = f"*{pat}*"
    roots: list[Path] = []
    if root:
        p = Path(root)
        if not p.is_absolute():
            p = WORKSPACE / p
        if _denied(p) or not _under(p, read_roots()):
            return {"ok": False, "error": "denied"}
        roots = [p]
    else:
        seen: set[str] = set()
        for r in list(search_roots()) + list(read_roots()):
            try:
                key = str(r.resolve())
            except OSError:
                continue
            if key in seen:
                continue
            seen.add(key)
            roots.append(r)
    hits: list[str] = []
    visited = 0
    for r in roots:
        if len(hits) >= 50:
            break
        try:
            it = r.rglob(pat)
        except OSError:
            continue
        for p in it:
            visited += 1
            if visited > 6000 or len(hits) >= 50:
                break
            try:
                if not p.is_file() or _denied(p) or not _under(p, read_roots()):
                    continue
            except OSError:
                continue
            hits.append(str(p))
    return {"ok": True, "pattern": pat, "hits": hits, "n": len(hits), "visited": visited}


def dispatch(name: str, args: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    name = ALIASES.get(name, name)
    name, args = canonicalize(name, args or {})
    extra = extra or {}
    if name == "steward_map":
        return brief()
    if name == "self_check":
        return _self_check()
    if name == "workspace_map":
        from workspace_map import list_mounts

        return list_mounts()
    if name == "find_files":
        return _find_files(str(args.get("pattern") or args.get("q") or "*"), args.get("root"))
    if name == "credential_where":
        q = str(args.get("q") or args.get("name") or "").lower()
        rows = []
        for k, v in credential_pointers().items():
            blob = (k + " " + v).lower()
            if q and q not in blob:
                continue
            p = Path(v)
            rows.append({"name": k, "path": v, "exists": p.is_file(), "redacted": True})
        return {"ok": True, "pointers": rows, "rule": "never echo secret contents; path + exists only"}
    if name == "list_dir":
        raw = str(args.get("path") or "").strip()
        if not raw or raw in {".", "drives", "roots"}:
            return {"ok": True, "roots": [str(p) for p in read_roots()], "hint": "pass a root path to list entries"}
        p = under_workspace(raw)
        if _denied(p) or not _under(p, read_roots()):
            return {"ok": False, "error": "denied", "hint": "path not on admin map; call steward_map"}
        if not p.is_dir():
            return {"ok": False, "error": "not_dir"}
        names = []
        for child in list(p.iterdir())[:200]:
            names.append(child.name + ("/" if child.is_dir() else ""))
        return {"ok": True, "path": str(p), "entries": names, "n": len(names)}
    if name == "read_file":
        rp = real_path(under_workspace(args.get("path") or ""))
        if _denied(rp) or not _under(rp, read_roots()):
            return {"ok": False, "error": "denied"}
        if not rp.is_file():
            return {"ok": False, "error": "not_file"}
        low = rp.name.lower()
        if low.endswith(".pass") or low in {".lygo_llm_token", ".llama_api_key"} or "lygo.pass" in low:
            return {"ok": True, "exists": True, "redacted": True, "path": str(rp), "bytes": rp.stat().st_size, "note": "credential file present; content not echoed"}
        data = rp.read_text(encoding="utf-8", errors="replace")[:64_000]
        return {"ok": True, "text": data}
    if name == "write_file":
        target, refusal = write_target(under_workspace(args.get("path") or ""))
        if refusal is not None:
            return refusal
        target.parent.mkdir(parents=True, exist_ok=True)
        content = str(args.get("content") or "")
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(target), "bytes": len(content.encode("utf-8"))}
    if name == "remember":
        from continuity import append_memory

        return append_memory(str(args.get("note") or args.get("text") or ""))
    if name == "notepad_list":
        from notepad import list_notes

        return list_notes()
    if name == "notepad_read":
        from notepad import read_note

        return read_note(str(args.get("id") or args.get("name") or "scratch"))
    if name == "notepad_write":
        from notepad import write_note

        return write_note(args.get("id"), str(args.get("title") or ""), str(args.get("text") or args.get("content") or ""))
    if name == "skill_list":
        from skills_mod import list_skills

        return list_skills()
    if name == "skill_read":
        from skills_mod import read_skill

        return read_skill(str(args.get("slug") or args.get("name") or args.get("id") or ""))
    if name == "skill_enable":
        from skills_mod import set_enabled

        return set_enabled(str(args.get("slug") or ""), True)
    if name == "skill_disable":
        from skills_mod import set_enabled

        return set_enabled(str(args.get("slug") or ""), False)
    if name == "clawhub_search":
        from skills_mod import clawhub_search

        return clawhub_search(str(args.get("q") or args.get("query") or ""))
    if name == "clawhub_install":
        from skills_mod import clawhub_install

        return clawhub_install(str(args.get("slug") or args.get("name") or ""))
    if name == "skillhub_list":
        from skills_mod import skillhub_list

        return skillhub_list(str(args.get("q") or ""), str(args.get("channel") or "all"))
    if name == "skillhub_install":
        from skills_mod import skillhub_install

        return skillhub_install(str(args.get("slug") or args.get("name") or ""), bool(args.get("full")))
    if name == "kernel_status":
        from engine import foreign_daemon_port_open, resolve_binary, runner_for
        from paths import LLAMA_PORT
        from p0_hook import PHYSICS_AVAILABLE

        from colibri import resolve_coli, status as coli_status
        from lygo_engine import status as lygo_status
        from paths import COLIBRI_PORT

        r = runner_for(LLAMA_PORT)
        rc = runner_for(COLIBRI_PORT)
        from runtime_facts import facts as _self_facts

        f = _self_facts()
        return {
            "ok": True,
            "model": f.get("model"),
            "engine": f.get("engine"),
            "build": f.get("build"),
            "brain": f.get("brain"),
            "brain_label": f.get("brain_label"),
            "n_limbs": f.get("n_limbs"),
            "physics": PHYSICS_AVAILABLE,
            "lygo_engine": lygo_status(),
            "engine_binary": bool(resolve_binary()),
            "colibri_launcher": bool(resolve_coli()),
            "colibri": coli_status(),
            "chat_runner": bool(r or rc),
            "engine_port": COLIBRI_PORT if rc else LLAMA_PORT,
            "foreign_daemon_port_open": foreign_daemon_port_open(),
            "kit": str(KIT_ROOT),
            "admin": is_admin(),
        }
    if name == "search_corpus":
        q = str(args.get("q") or "").lower()
        hits = []
        if q:
            for root in search_roots():
                if len(hits) >= 30:
                    break
                try:
                    it = root.rglob("*")
                except OSError:
                    continue
                for p in it:
                    if not p.is_file():
                        continue
                    try:
                        if p.stat().st_size > 256_000:
                            continue
                    except OSError:
                        continue
                    if p.suffix.lower() not in {".md", ".txt", ".json", ".html", ".py", ".bat"}:
                        continue
                    try:
                        t = p.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        continue
                    if q in t.lower():
                        hits.append(str(p))
                    if len(hits) >= 30:
                        break
        return {"ok": True, "hits": hits, "admin": is_admin()}
    if name == "p0_gate":
        return {"ok": True, "gate": gate_prompt(str(args.get("text") or ""))}
    if name == "stack_health":
        from stack_health import run_stack_health

        return run_stack_health()
    if name in {"web_fetch", "jina_fetch", "download_url", "page_thumbnail", "http_json", "wayback"}:
        url = str(args.get("url") or "")
        if is_placeholder_url(url):
            return {
                "ok": False,
                "error": "placeholder_url",
                "url": url,
                "hint": "never invent example.com or github.com/user/repo — use steward_map",
                "map": brief(),
            }
    got = extra_dispatch(name, args)
    if got is not None:
        return got
    return {"ok": False, "error": f"unknown_tool:{name}"}


def parse_fence_tool(text: str) -> dict[str, Any] | None:
    marker = "```tool"
    i = text.find(marker)
    if i < 0:
        marker = "```json"
        i = text.find(marker)
        if i < 0:
            return None
    rest = text[i + len(marker) :]
    end = rest.find("```")
    if end < 0:
        return None
    blob = rest[:end].strip()
    try:
        obj = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    name = obj.get("name") or obj.get("tool")
    if not name:
        return None
    args = obj.get("arguments") or obj.get("args") or obj.get("parameters") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"value": args}
    return {"name": str(name), "arguments": args}
