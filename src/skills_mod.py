"""OpenClaw-compatible skills for LYGO LLM Console.

A skill is a folder with SKILL.md (YAML frontmatter + markdown body).
Load order (highest first):
  1. workspace/skills
  2. save/skills/installed  (ClawHub installs)
  3. extra dirs (enabled.json, LYGO_SKILLS_DIRS, ~/.agents/skills, ~/.openclaw/skills, ~/.grok/skills)
  4. kit bundled skills/    (15 Δ9 champions)

Enabled skills inject name+description into the system prompt.
Full SKILL.md is loaded only via skill_read or when the operator invokes that skill.
Skills are instruction packs. This module does not execute skill scripts.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import quote, urlencode

import hashlib

from paths import KIT_ROOT, SAVE, WORKSPACE, ensure_dirs, stack_root
from atomicio import atomic_write_text, read_text
from p0_hook import gate_prompt

BUNDLED = KIT_ROOT / "skills"
INSTALLED = SAVE / "skills" / "installed"
STATE_PATH = SAVE / "skills" / "enabled.json"
#: Bundled CAPABILITY skills - the ones a module ships with - seeded and enabled on a fresh kit,
#: beside the 15 Δ9 seats above. They are not seats and claim no authority: each one is the written
#: half of a limb the console already has, so the agent knows the verbs, the honest workflow and the
#: refusals before it reaches for the tool. `filmcraft` arrives with the film module (the `film`
#: limb); a capability whose limb is not installed still teaches the agent what to say instead.
BUNDLED_EXTRA_DEFAULT: tuple[str, ...] = ("filmcraft", "lygo-downloads", "unity-harness")
CLAW_HUB = "https://clawhub.ai"
SKILLHUB = "https://chatagent.ca/lygoskillhub.html"
SKILLHUB_CAT = "https://chatagent.ca/data/lygoskillhub_catalog.json"
SKILLHUB_FULL_CAT = "https://chatagent.ca/data/lygo-full-skills/catalog.json"
SKILLHUB_FULL_DIST = "https://chatagent.ca/data/lygo-full-skills/dist/"
MAX_SKILL_BODY = 12_000
# The old slice was files[:200] before Windows returned SKILL.md twice, so the
# catalog hid the rest of the tree. Dedupe first, then keep this many.
WALK_CAP = 1200
INDEX_READ = 4000
MAX_ZIP = 6_000_000
MAX_CATALOG = 900_000
ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,79}$")
_CAT_CACHE: dict[str, Any] = {"ts": 0.0, "hub": None, "full": None}


def core_root() -> Path:
    """Live LYRA core root, resolved at call time — never a frozen drive letter.

    Generated skill text must name the real location of the core the Δ9 seat derives from,
    so the kit keeps working when the stick moves from I: to D:. Order: LYGO_CORE_ROOT,
    a LYRA_CORE inside or beside the resolved stack root, else the same-named sibling of
    this kit (which is what the core is on the default GamePC layout).
    """
    env = (os.environ.get("LYGO_CORE_ROOT") or "").strip()
    if env:
        return Path(env)
    base = stack_root() or KIT_ROOT.parent
    for cand in (base / "LYRA_CORE", base.parent / "LYRA_CORE"):
        if cand.is_dir():
            return cand
    return base.parent / "LYRA_CORE"


CHAMPIONS: list[dict[str, str]] = [
    {
        "slug": "champion-lyra",
        "champion_id": "LYRΔ",
        "name": "LYRΔ — Spiral Memory Guardian",
        "role": "Memory, song, continuity of theme",
        "when": "continuity, recall, lyrical framing of technical work",
        "invoke": "Invoke LYRΔ — Observed / Inferred / Unknown, then next actions.",
        "defer": "structure→ARKOS; signal→ÆTHERIS; sanctity→SANCORA; irreversible→OMNIΣIREN",
    },
    {
        "slug": "champion-d9ra",
        "champion_id": "Δ9RA",
        "name": "Δ9RA — Boundary vigilance",
        "role": "Wolf-edge challenge and risk review",
        "when": "stress-test a plan, challenge assumptions, name threats",
        "invoke": "Invoke Δ9RA — list risks, assumptions, and what would break first.",
        "defer": "Not a professional security audit.",
    },
    {
        "slug": "champion-srath",
        "champion_id": "ΣRΛΘ",
        "name": "ΣRΛΘ — Shadow sentinel",
        "role": "Omissions, hidden failure, red-team reading",
        "when": "something is quietly wrong or unsaid",
        "invoke": "Invoke ΣRΛΘ — what is missing, omitted, or failing silently?",
        "defer": "Do not invent hidden plots. Unknown stays unknown.",
    },
    {
        "slug": "champion-arkos",
        "champion_id": "ARKOS",
        "name": "ARKOS — Ethical Reality Architect",
        "role": "Systems maps, modules, trust boundaries",
        "when": "before coding: boxes, arrows, out-of-scope, sequence",
        "invoke": "Invoke ARKOS — Intent, Constraints, Blueprint, Failure modes, Refactor, Receipts.",
        "defer": "Architecture theater without a smallest shippable slice is a failure.",
    },
    {
        "slug": "champion-kairos",
        "champion_id": "KAIROS",
        "name": "KAIROS — Temporal Harmonizer",
        "role": "Timing, sequencing, what happens first",
        "when": "priority and release order are the real problem",
        "invoke": "Invoke KAIROS — ordered steps with why this order.",
        "defer": "Do not schedule work the operator did not ask for.",
    },
    {
        "slug": "champion-aetheris",
        "champion_id": "ÆTHERIS",
        "name": "ÆTHERIS — Truth Fractal Engine",
        "role": "Claims vs evidence, cut noise",
        "when": "need a falsifiable claim and a receipt",
        "invoke": "Invoke ÆTHERIS — claim, evidence, counter, unknown.",
        "defer": "Web hits are RESOURCE. Dual ledgers / Star Chart are CANON.",
    },
    {
        "slug": "champion-scendr",
        "champion_id": "ΣCENΔR",
        "name": "ΣCENΔR — Paradox Weaver",
        "role": "Scenarios where both may be true",
        "when": "forced single-answer is premature",
        "invoke": "Invoke ΣCENΔR — at least two live scenarios and what would falsify each.",
        "defer": "Do not collapse to one story for comfort.",
    },
    {
        "slug": "champion-sancora",
        "champion_id": "SANCORA",
        "name": "SANCORA — Collective Healing Nexus",
        "role": "Shared vocabulary, handoff, collaboration",
        "when": "multiple humans/agents must stay coherent",
        "invoke": "Invoke SANCORA — shared terms, handoff packet, what not to mix.",
        "defer": "Healing language is protocol, not medical advice.",
    },
    {
        "slug": "champion-sephrael",
        "champion_id": "SEPHRAEL",
        "name": "SEPHRAEL — Echo-walker",
        "role": "What repeats, what is fragile, what to archive",
        "when": "session drift, fragile memory, echoes across chats",
        "invoke": "Invoke SEPHRAEL — echoes, fragile items, archive vs discard.",
        "defer": "Do not store secrets in MEMORY.md.",
    },
    {
        "slug": "champion-omnisiren",
        "champion_id": "OMNIΣIREN",
        "name": "OMNIΣIREN — Silent storm",
        "role": "Fewer words, sharper constraints",
        "when": "verbosity is blocking execution",
        "invoke": "Invoke OMNIΣIREN — constraints only, then the next irreversible-safe step.",
        "defer": "Silent does not mean hidden. No covert actions.",
    },
    {
        "slug": "champion-lightfather",
        "champion_id": "Lightfather",
        "name": "Lightfather — Provenance seat",
        "role": "Publisher ethics, consent, truth preservation",
        "when": "who publishes, what is CANON, consent for writes",
        "invoke": "Invoke Lightfather — provenance, consent, CANON vs RESOURCE.",
        "defer": "Persona lens. Never claim to be the human operator. Never replace them.",
    },
    {
        "slug": "champion-volaris",
        "champion_id": "VΩLARIS",
        "name": "VΩLARIS — Prism judgment",
        "role": "Multi-criteria decisions in the open",
        "when": "tradeoffs must be visible, not hidden",
        "invoke": "Invoke VΩLARIS — criteria, scores, tradeoffs, recommendation.",
        "defer": "Show the criteria. Do not hide the loss function.",
    },
    {
        "slug": "champion-zeta",
        "champion_id": "ZETAΔ9",
        "name": "ZETAΔ9 — Threshold walker",
        "role": "Edge cases and weird inputs",
        "when": "designs need to bend before they break",
        "invoke": "Invoke ZETAΔ9 — weird inputs, failure modes, boundary tests.",
        "defer": "Do not actually break production to prove a point.",
    },
    {
        "slug": "champion-justicae",
        "champion_id": "JUSTICAE",
        "name": "JUSTICAE — Fairness and process",
        "role": "Who is affected, disclosure, consent",
        "when": "public posts, shared skills, other people are in scope",
        "invoke": "Invoke JUSTICAE — affected parties, disclosure, consent gaps.",
        "defer": "No doxxing. No harassment. Process over punishment theater.",
    },
    {
        "slug": "champion-seidon",
        "champion_id": "ΣEIDŌN",
        "name": "ΣEIDŌN — Mirror witness",
        "role": "Surface foam vs deep current on long work",
        "when": "roadmap honesty, depth vs noise",
        "invoke": "Invoke ΣEIDŌN — surface vs depth, what can wait, what is the tide.",
        "defer": "Witness. Do not flatten a long project into a slogan.",
    },
    {
        "slug": "champion-lyra-architect",
        "champion_id": "LYRA-Δ9",
        "name": "LYRA-Δ9 — Architect-agent seat",
        "role": "Architect + operator: Intent → Constraints → Blueprint → Build → Verify → Memory",
        "when": "any multi-step build, refactor, install, or agent-design task on this console",
        "invoke": "Invoke LYRA-Δ9 — intent, constraints, blueprint, smallest shippable slice, receipt.",
        "defer": "Never publish for the steward. Never report a receipt you did not get.",
        "aliases": ["lyra architect", "lyra-architect", "lyra architect agent", "architect agent"],
        "body": (
            "## Architect protocol (this seat)\n"
            "1. **Intent** — what the operator actually wants, one line. Two readings? Say both.\n"
            "2. **Constraints** — disks, ports, tokens, consent, what must not be touched.\n"
            "3. **Blueprint** — files, functions, data flow; name the smallest shippable slice.\n"
            "4. **Build** — do the slice with real limb calls. No description of work instead of work.\n"
            "5. **Verify** — run it, read it back, show the receipt (path, HTTP code, test count).\n"
            "6. **Memory** — durable facts to MEMORY.md via remember. Never secrets.\n\n"
            "**Truth discipline:** Observed / Inferred / Unknown always separated. CANON = dual\n"
            "ledgers / Haven Star Chart; chat, web, tool dumps = RESOURCE; SHADOW = known-missing,\n"
            "named not filled. No fabricated receipts.\n\n"
            f"**Charter file:** `workspace/LYRA_ARCHITECT.md` (distilled from `{core_root()}`\n"
            "and the Δ9 spine). Kin: LYRΔ (memory), ARKOS (systems), ÆTHERIS (evidence),\n"
            "Lightfather (provenance).\n\n"
        ),
    },
]


def _skill_md(c: dict[str, str]) -> str:
    return (
        f"---\n"
        f"name: {c['slug']}\n"
        f"description: Δ9 Council champion {c['champion_id']} — {c['role']}. Enable to align the agent with this seat.\n"
        f"version: 1.0.0\n"
        f"metadata: {{\"openclaw\": {{\"emoji\": \"🜂\", \"homepage\": \"https://chatagent.ca/champions.html\"}}, \"lygo\": true, \"champion_id\": \"{c['champion_id']}\"}}\n"
        f"---\n\n"
        f"# {c['name']}\n\n"
        f"**Seat:** {c['champion_id']}  \n"
        f"**Role:** {c['role']}  \n"
        f"**Use when:** {c['when']}\n\n"
        f"## Contract\n"
        f"- Advisor only. Not a controller. Not the human operator.\n"
        f"- Separate **Observed / Inferred / Unknown**.\n"
        f"- Dual ledgers / Haven Star Chart = CANON. This chat and the web = RESOURCE.\n"
        f"- P0: no OS wipe, no fabricated receipts, no secrets in MEMORY.md.\n"
        f"- {c['defer']}\n\n"
        f"## Invoke\n"
        f"{c['invoke']}\n\n"
        f"{c.get('body') or ''}"
        f"Hub: https://chatagent.ca/champions.html\n"
    )


def seed_bundled() -> None:
    root = BUNDLED / "champions"
    root.mkdir(parents=True, exist_ok=True)
    for c in CHAMPIONS:
        d = root / c["slug"]
        d.mkdir(parents=True, exist_ok=True)
        p = d / "SKILL.md"
        if not p.is_file():
            atomic_write_text(p, _skill_md(c))
    # Capability skills ship as real files in the tree (skills/filmcraft/SKILL.md and friends).
    # Seeding only guarantees the folder exists: a missing SKILL.md is reported by skill_list, not
    # papered over with a generated stub - a stub would read as a skill that works.
    for slug in BUNDLED_EXTRA_DEFAULT:
        (BUNDLED / slug).mkdir(parents=True, exist_ok=True)
    readme = BUNDLED / "README.md"
    if not readme.is_file():
        atomic_write_text(
            readme,
            "# Console skills (OpenClaw-compatible)\n\n"
            "Each skill is a folder with `SKILL.md`. Champions ship bundled, and a capability skill\n"
            "ships with the module it belongs to (e.g. `filmcraft` with the film limb).\n"
            "Install more from ClawHub into `save/skills/installed/`.\n"
            "Drop extra SKILL.md trees in `workspace/skills` or "
            "`~/.agents/skills` / `~/.openclaw/skills`.\n",
            encoding="utf-8",
        )


def ensure() -> None:
    ensure_dirs()
    (SAVE / "skills").mkdir(parents=True, exist_ok=True)
    INSTALLED.mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "skills").mkdir(parents=True, exist_ok=True)
    seed_bundled()
    if not STATE_PATH.is_file():
        save_state({"enabled": [c["slug"] for c in CHAMPIONS] + list(BUNDLED_EXTRA_DEFAULT),
                    "extra_dirs": []})


def load_state() -> dict[str, Any]:
    ensure()
    try:
        data = json.loads(read_text(STATE_PATH))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("enabled", [c["slug"] for c in CHAMPIONS] + list(BUNDLED_EXTRA_DEFAULT))
    data.setdefault("extra_dirs", [])
    return data


def save_state(data: dict[str, Any]) -> None:
    (SAVE / "skills").mkdir(parents=True, exist_ok=True)
    atomic_write_text(STATE_PATH, json.dumps(data, indent=2))


def _parse_skill_md(text: str, path: Path) -> dict[str, Any] | None:
    fm: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            raw = text[3:end]
            body = text[end + 4 :].lstrip("\n")
            for line in raw.splitlines():
                if ":" not in line or line.lstrip().startswith("#"):
                    continue
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip('"').strip("'")
    name = (fm.get("name") or path.parent.name).strip()
    if not name:
        return None
    desc = fm.get("description") or ""
    return {
        "slug": name,
        "description": desc[:240],
        "version": fm.get("version") or "",
        "body": body[:MAX_SKILL_BODY],
        "path": str(path),
        "dir": str(path.parent),
        "frontmatter": fm,
    }


def _skill_files(root: Path, cap: int = WALK_CAP) -> list[Path]:
    """SKILL.md paths, one per resolved file. The cap applies after that dedupe."""
    try:
        found = list(root.rglob("SKILL.md")) + list(root.rglob("skill.md"))
    except OSError:
        return []
    seen: set[str] = set()
    out: list[Path] = []
    for path in found:
        try:
            key = os.path.normcase(str(path.resolve()))
        except OSError:
            key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
        if len(out) >= cap:
            break
    return out


def _walk_skills(root: Path, source: str, depth: int = 5) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for p in _skill_files(root):
        try:
            rel = p.relative_to(root)
            if len(rel.parts) > depth:
                continue
            text = read_text(p, errors="replace")[:INDEX_READ]
        except OSError:
            continue
        parsed = _parse_skill_md(text, p)
        if not parsed:
            continue
        parsed["source"] = source
        out.append(parsed)
    return out


def extra_roots() -> list[Path]:
    roots: list[Path] = []
    st = load_state()
    for raw in st.get("extra_dirs") or []:
        p = Path(str(raw))
        if p.is_dir():
            roots.append(p)
    env = os.environ.get("LYGO_SKILLS_DIRS", "")
    for part in env.split(os.pathsep):
        p = Path(part.strip()) if part.strip() else None
        if p and p.is_dir():
            roots.append(p)
    home = Path.home()
    try:
        from roster import added_skill_roots

        for cand in added_skill_roots():
            if cand.is_dir():
                roots.append(cand)
    except ImportError:
        pass
    for cand in (
        home / ".agents" / "skills",
        home / ".openclaw" / "skills",
        home / ".openclaw" / "workspace" / "skills",
        home / ".grok" / "skills",
        home / ".clawhub" / "skills",
    ):
        if cand.is_dir():
            roots.append(cand)
    seen: set[str] = set()
    unique: list[Path] = []
    for path in roots:
        try:
            key = os.path.normcase(str(path.resolve()))
        except OSError:
            key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def catalog() -> list[dict[str, Any]]:
    ensure()
    seen: dict[str, dict[str, Any]] = {}
    layers = [
        (WORKSPACE / "skills", "workspace"),
        (INSTALLED, "clawhub"),
    ]
    for r in extra_roots():
        layers.append((r, "extra"))
    layers.append((BUNDLED, "bundled"))
    enabled = set(str(x) for x in load_state().get("enabled") or [])
    for root, source in layers:
        label = source
        folded = str(root).replace("\\", "/").lower()
        if "unity-agent-plugin" in folded:
            label = "unity"
        for sk in _walk_skills(root, label):
            slug = sk["slug"]
            if slug in seen:
                continue
            sk["enabled"] = slug in enabled
            sk["champion"] = slug.startswith("champion-")
            seen[slug] = sk
    rows = list(seen.values())
    rows.sort(key=lambda x: (0 if x.get("champion") else 1, x.get("slug") or ""))
    return rows


def list_skills() -> dict[str, Any]:
    rows = catalog()
    slim = [
        {
            "slug": r["slug"],
            "description": r.get("description"),
            "source": r.get("source"),
            "enabled": r.get("enabled"),
            "champion": r.get("champion"),
            "dir": r.get("dir"),
        }
        for r in rows
    ]
    return {
        "ok": True,
        "n": len(slim),
        "enabled": [r["slug"] for r in slim if r.get("enabled")],
        "skills": slim,
        "clawhub": CLAW_HUB,
        "skillhub": SKILLHUB,
        "note": "Full SKILL.md is not in the system prompt. Call skill_read when invoking a skill. Unity harness skills use source unity. Browse SkillHub with skillhub_list.",
    }


def read_skill(slug: str) -> dict[str, Any]:
    slug = (slug or "").strip()
    if not slug:
        return {"ok": False, "error": "empty"}
    for r in catalog():
        if r["slug"] == slug or r["slug"].endswith("/" + slug) or slug in {r["slug"], Path(r["dir"]).name}:
            body = r.get("body") or ""
            path = r.get("path") or ""
            if path:
                try:
                    full = read_text(Path(path), errors="replace")[: MAX_SKILL_BODY + 2000]
                    parsed = _parse_skill_md(full, Path(path))
                    if parsed:
                        body = parsed.get("body") or body
                except OSError:
                    pass
            if gate_prompt((body or "")[:4000]).get("verdict") == "QUARANTINE":
                return {"ok": False, "error": "p0_blocked", "slug": r["slug"]}
            return {
                "ok": True,
                "slug": r["slug"],
                "description": r.get("description"),
                "source": r.get("source"),
                "enabled": r.get("enabled"),
                "text": body,
                "path": path,
            }
    aliases = {c["champion_id"].lower(): c["slug"] for c in CHAMPIONS}
    aliases.update({c["slug"].replace("champion-", ""): c["slug"] for c in CHAMPIONS})
    hit = aliases.get(slug.lower())
    if hit:
        return read_skill(hit)
    return {"ok": False, "error": "missing", "slug": slug}


def set_enabled(slug: str, on: bool) -> dict[str, Any]:
    st = load_state()
    en = [str(x) for x in st.get("enabled") or []]
    slug = (slug or "").strip()
    found = None
    for r in catalog():
        if r["slug"] == slug or Path(r["dir"]).name == slug:
            found = r["slug"]
            break
    if not found:
        aliases = {c["champion_id"].lower(): c["slug"] for c in CHAMPIONS}
        found = aliases.get(slug.lower()) or slug
    if on:
        if found not in en:
            en.append(found)
    else:
        en = [x for x in en if x != found]
    st["enabled"] = en
    save_state(st)
    return {"ok": True, "slug": found, "enabled": on, "enabled_list": en}


def add_root(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_dir():
        return {"ok": False, "error": "not_dir"}
    st = load_state()
    dirs = [str(x) for x in st.get("extra_dirs") or []]
    s = str(p.resolve())
    if s not in dirs:
        dirs.append(s)
    st["extra_dirs"] = dirs
    save_state(st)
    return {"ok": True, "extra_dirs": dirs, "added": s}


def prompt_catalog(cap: int = 1000) -> str:
    """The ENABLED SKILLS block, held to `cap` chars.

    The cap is a hard budget, not a hint: this block is one section of the identity block, which
    must stay under 16,500 chars to leave room for history and the answer in an 8192-token window.
    MEASURED 2026-09-25: at cap=1500 a FRESH store (every \u03949 seat enabled at once) composed
    16,561 - over the ceiling - while a used store fitted. Lowering the cap covers the fresh
    install, which is the case that ships on the USB stick."""
    rows = [r for r in catalog() if r.get("enabled")]
    if not rows:
        return (
            "SKILLS: none enabled. Operator can turn on Δ9 champions in the Skills panel "
            "(LYRΔ, Δ9RA, ΣRΛΘ, ARKOS, KAIROS, ÆTHERIS, ΣCENΔR, SANCORA, SEPHRAEL, "
            "OMNIΣIREN, Lightfather, VΩLARIS, ZETAΔ9, JUSTICAE, ΣEIDŌN)."
        )
    lines = [
        "ENABLED SKILLS (OpenClaw-compatible). Name+description only. "
        "Call skill_read before following a skill. Do not invent skill bodies."
    ]
    used = len(lines[0])
    # Δ9 seating rows all end with the same sentence. Said once instead of eleven times it gives the
    # identity block back ~430 chars of the budget that keeps it inside the model's window - and the
    # case that used to overrun that budget was a FRESH store, where every seat is enabled at once.
    SHARED = "Enable to align the agent with this seat."
    shared_seen = 0
    body: list[str] = []
    for r in rows:
        desc = (r.get("description") or "").strip()
        if SHARED in desc:
            shared_seen += 1
            desc = " ".join(desc.replace(SHARED, "").split())
        line = f"- {r['slug']}: {desc}"
        if used + len(line) + 1 > cap:
            body.append(f"- … {len(rows)} enabled; skill_list for the rest")
            break
        body.append(line)
        used += len(line) + 1
    lines.extend(body)
    if shared_seen >= 2:
        lines.append(
            "- A \u03949 Council seat above reads: " + SHARED
            + " The other seats are off until the operator enables them."
            if shared_seen == len(rows)
            else "- Some rows above end with \"" + SHARED + "\" (seat alignment)."
        )
    return "\n".join(lines)


def match_invoked(user_text: str) -> list[str]:
    """Which seats/skills the operator named. The most specific name wins.

    "summon LYRA architect" has to land on the architect seat, not on LYRΔ (plain "lyra"), so hits
    are ranked by how much of the name matched instead of by list order.
    """
    t = (user_text or "").lower()
    scored: list[tuple[int, str]] = []
    for c in CHAMPIONS:
        keys = {c["slug"], c["champion_id"].lower(), c["slug"].replace("champion-", "")}
        keys.add(c["champion_id"].replace("Δ", "d").replace("Σ", "s").replace("Λ", "l").lower())
        for a in c.get("aliases") or []:
            keys.add(str(a).lower())
        best = max((len(k) for k in keys if k and k in t), default=0)
        if best:
            scored.append((best, c["slug"]))
    scored.sort(key=lambda row: (-row[0], row[1]))
    hits: list[str] = [slug for _, slug in scored]
    m = re.search(r"(?:/skill|skill_read|invoke|summon|align with)\s+([a-zA-Z0-9._ΔΣΛΩÆ-]{2,40})", user_text or "", re.I)
    if m:
        hits.append(m.group(1))
    # unique preserve
    out: list[str] = []
    for h in hits:
        if h not in out:
            out.append(h)
    return out[:3]


def _http_json(url: str) -> Any:
    from web_tools import _blocked, _get

    why = _blocked(url)
    if why:
        return {"ok": False, "error": why}
    code, raw, ctype = _get(url)
    if code != 200:
        return {"ok": False, "error": f"http_{code}"}
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {"ok": False, "error": "not_json", "ctype": ctype}


def clawhub_search(q: str) -> dict[str, Any]:
    q = (q or "").strip()[:120]
    if not q:
        url = f"{CLAW_HUB}/api/v1/skills?limit=8&sort=downloads&nonSuspiciousOnly=true"
    else:
        url = f"{CLAW_HUB}/api/v1/search?" + urlencode({"q": q, "limit": "8", "nonSuspiciousOnly": "true"})
    data = _http_json(url)
    if isinstance(data, dict) and data.get("ok") is False:
        return data
    items = []
    if isinstance(data, dict):
        items = data.get("items") or data.get("results") or data.get("skills") or []
        if not items and isinstance(data.get("skill"), dict):
            items = [data]
    elif isinstance(data, list):
        items = data
    rows = []
    for it in items[:8]:
        if not isinstance(it, dict):
            continue
        sk = it.get("skill") if isinstance(it.get("skill"), dict) else it
        slug = sk.get("slug") or it.get("slug") or ""
        owner = ""
        if isinstance(it.get("owner"), dict):
            owner = it["owner"].get("handle") or ""
        elif isinstance(sk.get("owner"), dict):
            owner = sk["owner"].get("handle") or ""
        rows.append(
            {
                "slug": slug,
                "display": sk.get("displayName") or sk.get("name") or slug,
                "summary": (sk.get("summary") or sk.get("description") or it.get("summary") or "")[:240],
                "owner": owner,
                "url": f"{CLAW_HUB}/{owner}/skills/{slug}" if owner else f"{CLAW_HUB}/skills/{slug}",
            }
        )
    return {"ok": True, "q": q, "hits": rows, "class": "RESOURCE"}


def clawhub_inspect(slug: str) -> dict[str, Any]:
    slug = (slug or "").strip().lstrip("@")
    if not slug or not ID_RE.match(slug.replace("/", "-")):
        return {"ok": False, "error": "bad_slug"}
    data = _http_json(f"{CLAW_HUB}/api/v1/skills/{quote(slug, safe='/-')}")
    if isinstance(data, dict) and data.get("ok") is False:
        return data
    if not isinstance(data, dict):
        return {"ok": False, "error": "bad_payload"}
    mod = data.get("moderation") or {}
    if mod.get("isMalwareBlocked"):
        return {"ok": False, "error": "malware_blocked", "slug": slug}
    return {"ok": True, "slug": slug, "data": json.dumps(data, default=str)[:8000], "moderation": mod, "class": "RESOURCE"}


def _download(url: str, max_bytes: int) -> tuple[int, bytes, str]:
    from web_tools import CTX, TIMEOUT, UA, _blocked
    import urllib.error
    import urllib.request

    why = _blocked(url)
    if why:
        return 0, why.encode("utf-8"), why
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,application/zip,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=max(TIMEOUT, 30), context=CTX) as resp:
            return resp.status, resp.read(max_bytes + 1), resp.headers.get("Content-Type") or ""
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"")[:4000], ""
    except Exception as e:
        return 0, str(e).encode("utf-8")[:200], "error"


def _safe_member(name: str, dest: Path) -> Path | None:
    """Relative target for one zip member, or None when the member must be skipped.

    A member name is attacker-controlled: besides '..' and a leading '/', Windows happily
    resolves a drive-absolute name ('C:/Users/x/evil.txt') to an absolute path, which would
    drop the file outside save/skills entirely. Reject anything that is not strictly a
    relative path inside dest (same containment check the HTTP file routes use).
    """
    raw = (name or "").replace("\\", "/")
    if not raw or raw.startswith("/") or PureWindowsPath(raw).drive:
        return None
    if Path(raw).is_absolute():
        return None
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    rel = Path(*parts[-4:]) if len(parts) > 4 else Path(*parts)
    target = (dest / rel).resolve()
    root = dest.resolve()
    if target != root and root not in target.parents:
        return None
    return target


def _commit_staged(staging: Path, dest: Path) -> None:
    """Move a fully-extracted skill tree into place, replacing any previous copy."""
    for item in sorted(staging.iterdir()):
        target = dest / item.name
        if item.is_dir():
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                target.unlink()
            shutil.move(str(item), str(target))
        else:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            os.replace(str(item), str(target))


def _extract_skill_zip(raw: bytes, dest: Path, origin: dict[str, Any]) -> dict[str, Any]:
    dest.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        if b"---" in raw[:80]:
            atomic_write_text(dest / "SKILL.md", raw.decode("utf-8", errors="replace"))
            return {"ok": True, "path": str(dest), "files": 1}
        return {"ok": False, "error": "not_zip"}
    # Extract into a sibling temp dir first: nothing reaches save/skills until the tree has
    # passed the SKILL.md check, and a partial/bad archive never leaves a half-written skill.
    staging = Path(tempfile.mkdtemp(prefix=dest.name + ".", suffix=".staging", dir=str(dest.parent)))
    n = 0
    try:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if name.endswith("/"):
                continue
            low = name.lower()
            if low.endswith((".exe", ".dll", ".bat", ".cmd", ".ps1", ".msi", ".scr")):
                continue
            if info.file_size > 500_000:
                continue
            target = _safe_member(name, staging)
            if target is None:
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(info)[:500_000])
                n += 1
            except OSError:
                continue
            if n >= 80:
                break
        if not list(staging.rglob("SKILL.md")) and not list(staging.rglob("skill.md")):
            return {"ok": False, "error": "no_skill_md", "files": n, "path": str(dest)}
        atomic_write_text(staging / ".clawhub-origin.json", json.dumps(origin, indent=2))
        _commit_staged(staging, dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {"ok": True, "path": str(dest), "files": n}


def _load_catalogs(force: bool = False) -> dict[str, Any]:
    now = time.time()
    if not force and _CAT_CACHE.get("hub") and now - float(_CAT_CACHE.get("ts") or 0) < 600:
        return _CAT_CACHE
    hub_code, hub_raw, _ = _download(SKILLHUB_CAT, MAX_CATALOG)
    full_code, full_raw, _ = _download(SKILLHUB_FULL_CAT, MAX_CATALOG)
    def _parse(code: int, raw: bytes) -> dict[str, Any]:
        if code != 200:
            return {}
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    _CAT_CACHE.update({"ts": now, "hub": _parse(hub_code, hub_raw), "full": _parse(full_code, full_raw)})
    return _CAT_CACHE


def skillhub_list(q: str = "", channel: str = "all") -> dict[str, Any]:
    qn = (q or "").strip().lower()
    ch = (channel or "all").lower()
    cats = _load_catalogs()
    hits: list[dict[str, Any]] = []
    if ch in {"all", "public", "hub", "tentacle"}:
        for it in (cats.get("hub") or {}).get("skills") or []:
            if not isinstance(it, dict):
                continue
            if it.get("kind") not in {None, "skill"}:
                continue
            blob = " ".join(str(it.get(k) or "") for k in ("slug", "name", "summary", "category"))
            if qn and qn not in blob.lower():
                continue
            hits.append(
                {
                    "slug": it.get("slug"),
                    "display": it.get("name") or it.get("slug"),
                    "summary": (it.get("summary") or "")[:240],
                    "channel": "public_tentacle",
                    "category": it.get("category"),
                    "clawhub_url": it.get("clawhub_url"),
                    "has_full_zip": bool(it.get("has_full_zip")),
                    "url": SKILLHUB,
                }
            )
    if ch in {"all", "full", "engineer"}:
        for it in (cats.get("full") or {}).get("skills") or []:
            if not isinstance(it, dict):
                continue
            blob = " ".join(str(it.get(k) or "") for k in ("slug", "name", "role", "tier"))
            if qn and qn not in blob.lower():
                continue
            hits.append(
                {
                    "slug": it.get("slug"),
                    "display": it.get("name") or it.get("slug"),
                    "summary": (it.get("role") or "")[:240],
                    "channel": "full_zip",
                    "tier": it.get("tier"),
                    "zip": it.get("zip"),
                    "sha256": it.get("zip_sha256"),
                    "bytes": it.get("bytes"),
                    "url": SKILLHUB + "#full-lygo",
                }
            )
    # featured first
    feat = set((cats.get("full") or {}).get("featured") or [])
    hits.sort(key=lambda x: (0 if x.get("slug") in feat else 1, x.get("channel") != "public_tentacle", x.get("slug") or ""))
    return {
        "ok": True,
        "q": q,
        "channel": ch,
        "n": len(hits),
        "hits": hits[:40],
        "hub": SKILLHUB,
        "class": "RESOURCE",
    }


def skillhub_install(slug: str, full: bool = False) -> dict[str, Any]:
    slug = (slug or "").strip().lstrip("@")
    if slug.startswith("deepseekoracle/"):
        slug = slug.split("/", 1)[1]
    if not slug:
        return {"ok": False, "error": "empty"}
    if not full:
        return clawhub_install("deepseekoracle/" + slug if "/" not in slug else slug)
    cats = _load_catalogs()
    rec = None
    for it in (cats.get("full") or {}).get("skills") or []:
        if isinstance(it, dict) and it.get("slug") == slug:
            rec = it
            break
    if not rec:
        return {"ok": False, "error": "not_in_full_catalog", "slug": slug, "hub": SKILLHUB + "#full-lygo"}
    zname = str(rec.get("zip") or "")
    want = str(rec.get("zip_sha256") or "").lower()
    url = SKILLHUB_FULL_DIST + zname
    code, raw, _ = _download(url, MAX_ZIP)
    if code != 200:
        return {"ok": False, "error": f"http_{code}", "url": url}
    if len(raw) > MAX_ZIP:
        return {"ok": False, "error": "too_large"}
    got = hashlib.sha256(raw).hexdigest()
    if want and got != want:
        return {"ok": False, "error": "hash_mismatch", "expected": want, "got": got, "url": url}
    dest = INSTALLED / (slug + "--full")
    unpacked = _extract_skill_zip(
        raw,
        dest,
        {"slug": slug, "source": "skillhub_full", "sha256": got, "url": url, "ts": time.time()},
    )
    if not unpacked.get("ok"):
        return unpacked
    set_enabled(slug + "--full", True)
    return {
        "ok": True,
        "slug": slug,
        "channel": "full_zip",
        "sha256": got,
        "bytes": len(raw),
        "path": unpacked.get("path"),
        "files": unpacked.get("files"),
        "enabled": True,
        "note": "FULL zip hash-checked. Live Star Chart / git push still need human consent.",
    }


def clawhub_install(slug: str) -> dict[str, Any]:
    slug = (slug or "").strip().lstrip("@")
    if not slug:
        return {"ok": False, "error": "empty"}
    insp = clawhub_inspect(slug)
    if not insp.get("ok"):
        return insp
    url = f"{CLAW_HUB}/api/v1/download?" + urlencode({"slug": slug})
    code, raw, ctype = _download(url, MAX_ZIP)
    if code == 0 and ctype not in {"", "error"}:
        return {"ok": False, "error": ctype}
    if code != 200:
        return {"ok": False, "error": f"http_{code}", "detail": raw[:200].decode("utf-8", errors="replace")}
    if "json" in (ctype or "").lower() or raw[:1] in (b"{", b"["):
        return {
            "ok": False,
            "error": "github_handoff",
            "hint": "GitHub-backed skill — drop SKILL.md into workspace/skills or install FULL zip from SkillHub",
            "detail": raw[:400].decode("utf-8", errors="replace"),
        }
    if len(raw) > MAX_ZIP:
        return {"ok": False, "error": "too_large"}
    dest = INSTALLED / slug.replace("/", "--")
    unpacked = _extract_skill_zip(raw, dest, {"slug": slug, "source": "clawhub", "ts": time.time()})
    if not unpacked.get("ok"):
        return unpacked
    set_enabled(slug.replace("/", "--"), True)
    return {"ok": True, "slug": slug, "path": unpacked.get("path"), "files": unpacked.get("files"), "enabled": True}
