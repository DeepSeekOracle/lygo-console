"""Local roster of console limbs, skills, and the Unity harness.

The card, the Skills panel, and the isolated Hermes brain read this same scan.
Nothing here starts Unity, a model, or a network call. A missing folder is a
named gap, not an empty success.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

# Skills whose folder should sit in the isolated Hermes home so they win over
# a same-named copy under ~/.grok/skills. The Unity plugin copy of unity-cli
# is a different file from the older grok skill of that name.
# Operator skills the Manager must see in its own skills folder, ahead of the big catalog.
ADMIN_LINK_NAMES = (
    "github-operator",
    "huggingface-operator",
    "huggingface-hub",
    "github",
    "lattice-operator",
    "lygo-manager-orchestration",
    "lygo-system-orientation",
    "agent-shared-workspace",
)
UNITY_LINK_NAMES = (
    "unity-2d-3d-building",
    "unity-agent-web-games",
    "unity-cli",
    "unity-materials-and-surface-art",
    "unity-webgl-publishing",
    "lygo-unity-arcade-build",
    "forge-unity-worker",
)

_CACHE: dict[str, Any] = {"at": 0.0, "snap": None}
_TTL_S = 30.0
_DESC = 90


def _home() -> Path:
    return Path.home()


def _under_desktop_hermes(path: Path) -> bool:
    """True when path is inside a desktop Hermes profile. Those stay unlinked."""
    try:
        resolved = path.resolve()
    except OSError:
        return False
    blocked: list[Path] = [_home() / ".hermes"]
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        blocked.append(Path(local) / "hermes")
    for root in blocked:
        try:
            base = root.resolve()
        except OSError:
            continue
        if resolved == base or base in resolved.parents:
            return True
    return False


def unity_plugin_roots() -> list[Path]:
    plugins = _home() / ".grok" / "installed-plugins"
    if not plugins.is_dir():
        return []
    return [p for p in sorted(plugins.glob("unity-agent-plugin-*/skills")) if p.is_dir()]


def bundled_grok_root() -> Path | None:
    path = _home() / ".grok" / "bundled" / "skills"
    return path if path.is_dir() else None


def grok_skills_root() -> Path | None:
    path = _home() / ".grok" / "skills"
    return path if path.is_dir() else None


def kit_capability_dirs() -> list[Path]:
    base = Path(__file__).resolve().parents[1] / "skills"
    found: list[Path] = []
    for name in ("filmcraft", "lygo-downloads", "unity-harness"):
        path = base / name
        if (path / "SKILL.md").is_file():
            found.append(path)
    return found


def added_skill_roots() -> list[Path]:
    """Roots the console catalog did not already walk.

    Unity comes first so the plugin copy of a shared name is the one the
    catalog keeps. The Grok skill tree is added by skills_mod itself.
    """
    roots: list[Path] = []
    roots.extend(unity_plugin_roots())
    bundled = bundled_grok_root()
    if bundled is not None:
        roots.append(bundled)
    return roots


def hermes_external_dirs() -> list[str]:
    """Skill folders for the isolated profile. Never a desktop Hermes home."""
    dirs: list[Path] = []
    dirs.extend(unity_plugin_roots())
    bundled = bundled_grok_root()
    if bundled is not None:
        dirs.append(bundled)
    grok = grok_skills_root()
    if grok is not None:
        dirs.append(grok)
    dirs.extend(kit_capability_dirs())
    out: list[str] = []
    seen: set[str] = set()
    for path in dirs:
        if _under_desktop_hermes(path):
            continue
        try:
            key = os.path.normcase(str(path.resolve()))
        except OSError:
            continue
        if key in seen or not path.is_dir():
            continue
        seen.add(key)
        out.append(path.as_posix())
    return out


def _junction(src: Path, dest: Path) -> bool:
    """Directory junction. A dest that already exists is left alone."""
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            dest.symlink_to(src, target_is_directory=True)
        except OSError:
            return False
        return dest.exists()
    import subprocess

    ran = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(dest), str(src)],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return ran.returncode == 0 and dest.exists()


def _skill_dirs(root: Path) -> list[Path]:
    found: list[Path] = []
    if not root.is_dir():
        return found
    try:
        children = sorted(root.iterdir())
    except OSError:
        return found
    for child in children:
        if child.is_dir() and (child / "SKILL.md").is_file():
            found.append(child)
    return found


def link_local_skills(home: Path) -> list[str]:
    """Junction Unity skills into the isolated home so they outrank external copies."""
    if _under_desktop_hermes(home):
        return []
    linked: list[str] = []
    dest_root = home / "skills"
    for root in unity_plugin_roots():
        for src in _skill_dirs(root):
            if _under_desktop_hermes(src):
                continue
            dest = dest_root / src.name
            if _junction(src, dest):
                linked.append(src.name)
    grok = grok_skills_root()
    if grok is not None:
        for name in UNITY_LINK_NAMES + ADMIN_LINK_NAMES:
            src = grok / name
            if not (src / "SKILL.md").is_file() or _under_desktop_hermes(src):
                continue
            dest = dest_root / name
            if dest.exists():
                continue
            if _junction(src, dest):
                linked.append(name)
        stack = Path(r"<EDRIVE>\.grok\skills\lygo-protocol-stack-operator")
        dest = dest_root / "lygo-protocol-stack-operator"
        if (stack / "SKILL.md").is_file() and not dest.exists() and not _under_desktop_hermes(stack):
            if _junction(stack, dest):
                linked.append("lygo-protocol-stack-operator")
    pointer = dest_root / "lygo-manager-roster"
    pointer.mkdir(parents=True, exist_ok=True)
    (pointer / "SKILL.md").write_text(
        "---\n"
        "name: lygo-manager-roster\n"
        "description: Console limbs, skills, and the Unity harness. Call lygo_roster, then skill_read.\n"
        "---\n\n"
        "# Manager roster\n\n"
        "Call lygo_roster for the live list. Call lygo_limb with name skill_read and "
        "arguments {\"slug\": \"<name>\"} before following a skill.\n\n"
        "The Unity harness is the unity-cli skill plus the Unity agent plugin skills. "
        "Read unity-cli before any Unity command. Do not launch the Editor unless the "
        "operator asked for that.\n",
        encoding="utf-8",
    )
    manager = dest_root / "lygo-lattice-manager"
    manager.mkdir(parents=True, exist_ok=True)
    (manager / "SKILL.md").write_text(
        "---\n"
        "name: lygo-lattice-manager\n"
        "description: LYGO architect. Git, GitHub, Hugging Face, and the lattice map for this console.\n"
        "---\n\n"
        "# Lattice manager\n\n"
        "You are the Manager inside the live console at "
        "<CONSOLE_DIR>. "
        "Ports: console 9641, portal 8790, engine 11441.\n\n"
        "Read github-operator before gh or git. Read huggingface-operator before hf. "
        "Read lattice-operator before a Star Chart read. "
        "The live kit is not <EDRIVE>\\lygo-protocol-stack\\lygo_llm_console.\n\n"
        "Preflight: gh auth status, hf auth whoami. Never print a token.\n"
        "Ask Justin before push, upload, deploy, or publish.\n"
        "Public stack: https://github.com/DeepSeekOracle/lygo-protocol-stack\n"
        "HF dataset: https://huggingface.co/datasets/DeepSeekOracle/lygo-protocol-stack\n",
        encoding="utf-8",
    )
    return linked


def is_unity(slug: str, path: str, source: str) -> bool:
    if source == "unity":
        return True
    text = f"{slug} {path}".replace("\\", "/").lower()
    return "unity" in text


def _clip(text: str) -> str:
    flat = " ".join(str(text or "").split())
    if len(flat) <= _DESC:
        return flat
    return flat[: _DESC - 3] + "..."


def snapshot(force: bool = False) -> dict[str, Any]:
    """One cached read of the catalog and the tool schema."""
    now = time.time()
    cached = _CACHE.get("snap")
    if not force and isinstance(cached, dict) and (now - float(_CACHE.get("at") or 0)) < _TTL_S:
        return cached
    from skills_mod import catalog
    from tools import CORE_NAMES, TOOLS_SCHEMA

    skills = []
    unity = []
    for row in catalog():
        slug = str(row.get("slug") or "")
        path = str(row.get("path") or "")
        source = str(row.get("source") or "")
        item = {
            "slug": slug,
            "description": _clip(str(row.get("description") or "")),
            "source": source,
            "enabled": bool(row.get("enabled")),
            "unity": is_unity(slug, path, source),
        }
        skills.append(item)
        if item["unity"]:
            unity.append(item)
    limbs = []
    for tool in TOOLS_SCHEMA:
        fn = tool.get("function") or {}
        name = str(fn.get("name") or "")
        if not name:
            continue
        limbs.append({
            "name": name,
            "core": name in CORE_NAMES,
            "description": _clip(str(fn.get("description") or "")),
        })
    snap = {
        "skills": skills,
        "unity": unity,
        "limbs": limbs,
        "roots": hermes_external_dirs(),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _CACHE["at"] = now
    _CACHE["snap"] = snap
    return snap


def card(q: str = "") -> dict[str, Any]:
    """Pane payload. A query narrows skills and limbs. Counts stay on the lights."""
    snap = snapshot()
    query = " ".join(str(q or "").lower().split())
    skills = list(snap["skills"])
    unity = list(snap["unity"])
    limbs = list(snap["limbs"])
    if query:
        skills = [s for s in skills if query in (s["slug"] + " " + s["description"]).lower()]
        unity = [s for s in unity if query in (s["slug"] + " " + s["description"]).lower()]
        limbs = [s for s in limbs if query in (s["name"] + " " + s["description"]).lower()]
    n_skills = len(snap["skills"])
    n_unity = len(snap["unity"])
    n_limbs = len(snap["limbs"])
    n_core = sum(1 for row in snap["limbs"] if row.get("core"))
    gaps: list[str] = []
    if not unity_plugin_roots():
        gaps.append("Unity agent plugin skills folder was not found under ~/.grok/installed-plugins")
    if grok_skills_root() is None:
        gaps.append("Grok skill tree ~/.grok/skills was not found")
    if n_unity and n_skills and n_limbs and not gaps:
        state = "green"
        state_text = f"{n_skills} skills · {n_unity} unity · {n_core} core limbs"
    else:
        state = "amber"
        state_text = f"{n_skills} skills · {n_unity} unity · {n_limbs} tools"
        if gaps:
            state_text = "roster incomplete"
    unity_rows = [
        {"k": row["slug"], "v": row["source"] or "unity", "note": row["description"], "dot": "green"}
        for row in unity[:48]
    ]
    if len(unity) > 48:
        unity_rows.append({
            "k": "more",
            "v": str(len(unity) - 48),
            "note": "filter the Skills panel or call lygo_roster with q",
            "dot": "grey",
        })
    limb_rows = [
        {
            "k": row["name"],
            "v": "core" if row.get("core") else "extended",
            "note": row["description"],
            "dot": "green" if row.get("core") else "grey",
        }
        for row in limbs[:60]
    ]
    root_rows = [
        {"k": "root", "v": path, "note": "Hermes external_dirs, isolated profile only", "dot": "green"}
        for path in snap["roots"]
    ]
    if query:
        root_rows.insert(0, {
            "k": "query",
            "v": query,
            "note": f"{len(skills)} skills match, {len(unity)} unity, {len(limbs)} tools",
            "dot": "green",
        })
    return {
        "ok": True,
        "at": snap["at"],
        "state": state,
        "state_text": state_text,
        "lights": [
            {"label": "Skills", "text": str(n_skills), "state": "green" if n_skills else "amber"},
            {"label": "Unity", "text": str(n_unity), "state": "green" if n_unity else "amber"},
            {"label": "Limbs", "text": f"{n_core} core / {n_limbs}", "state": "green" if n_core else "amber"},
        ],
        "groups": [
            {"title": "Unity harness", "rows": unity_rows or [
                {"k": "unity", "v": "0", "note": "no Unity skill folder was found", "dot": "amber"},
            ]},
            {"title": "Console limbs", "rows": limb_rows},
            {"title": "Where Hermes reads", "rows": root_rows or [
                {"k": "roots", "v": "0", "note": "no skill folder was found for the isolated profile", "dot": "amber"},
            ]},
        ],
        "missing": gaps,
        "n_skills": n_skills,
        "n_unity": n_unity,
        "n_limbs": n_limbs,
        "n_core": n_core,
        "query": query,
        "skills": [{"slug": s["slug"], "source": s["source"], "enabled": s["enabled"]} for s in skills[:80]],
        "unity_slugs": [s["slug"] for s in unity],
        "limb_names": [s["name"] for s in limbs],
    }
