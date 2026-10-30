"""Generate GAME-INDEX.txt — a plain-text engineering map of Lattice City.

Why: finding "where is the windmill" used to mean grepping city.js and guessing which theme
rolled it. This parses the real source and emits one file that answers:

  * where the game lives (both sites, both engines, git checkouts)
  * every file and what it is
  * every function with a line number
  * the constants that matter
  * every sprite key -> files -> frames -> pixel size -> who uses it
  * every theme -> zone -> level -> sprite (this is how you find the windmill)
  * orphan PNGs (on disk, not in the manifest) and missing files (in the manifest, not on disk)

Usage:
    python tools/map_game.py                          # writes references/GAME-INDEX.txt
    python tools/map_game.py --root <game dir> --out <file>
    python tools/map_game.py --grep windmill          # quick lookup, no file written

Stdlib only. Re-run after touching city.js, manifest.json or the tile folder.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from pathlib import Path

LIVE = r"D:\excavationpro-ca\games\lattice-city"
SITES = [
    ("https://excavationpro.ca/games/lattice-city/", "LIVE", "DeepSeekOracle/excavationpro-ca",
     r"games/lattice-city/", r"D:\excavationpro-ca", "city.js (classic script)"),
    ("https://chatagent.ca/games/lattice-city/", "OLD", "DeepSeekOracle/chatagent",
     r"games/lattice-city/", r"<USER>\source\chatagent",
     "assets/index-BqpfSHLf.js (Vite bundle)"),
]
BUILD = r"<DESKTOP>\LATTICE-CITY-BUILD"


def png_size(path: Path):
    try:
        with path.open("rb") as fh:
            head = fh.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return struct.unpack(">II", head[16:24])
    except OSError:
        return None


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def parse_functions(js: str):
    out = []
    for m in re.finditer(r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)", js, re.M):
        line = js.count("\n", 0, m.start()) + 1
        out.append((line, m.group(1), m.group(2).strip()))
    return out


def parse_const(js: str, name: str):
    m = re.search(rf"^\s*const\s+{re.escape(name)}\s*=\s*([^;\n]+)", js, re.M)
    return m.group(1).strip() if m else None


def parse_object_block(js: str, start: int, open_ch: str = "{", close_ch: str = "}") -> str:
    """Return the text inside the first balanced block starting at/after `start`."""
    i = js.find(open_ch, start)
    if i < 0:
        return ""
    depth = 0
    for j in range(i, len(js)):
        if js[j] == open_ch:
            depth += 1
        elif js[j] == close_ch:
            depth -= 1
            if depth == 0:
                return js[i + 1:j]
    return js[i + 1:]


def parse_map(js: str, name: str):
    """Parse `const NAME = { k: "sprite", ... }` into a dict."""
    m = re.search(rf"const\s+{re.escape(name)}\s*=\s*\{{", js)
    if not m:
        return {}
    body = parse_object_block(js, m.start())
    out = {}
    for k, v in re.findall(r"(\w+|\d+)\s*:\s*\"([\w\-]+)\"", body):
        out[k] = v
    return out


def parse_themes(js: str):
    """THEMES = { name: { label, R: [[...]], C: [...], I: [...] } } -> list of dicts."""
    m = re.search(r"const\s+THEMES?\s*=\s*\{", js)
    if not m:
        return []
    body = parse_object_block(js, m.start())
    themes = []
    for tm in re.finditer(r"^\s{4}(\w+):\s*\{", body, re.M):
        name = tm.group(1)
        chunk = parse_object_block(body, tm.start())
        label = re.search(r'label:\s*"([^"]*)"', chunk)
        zones = {}
        for z in ("R", "C", "I"):
            zm = re.search(rf"^\s*{z}:\s*(\[.*\])\s*,?\s*$", chunk, re.M)
            if not zm:
                continue
            levels = []
            for lv in re.findall(r"\[([^\[\]]*)\]", zm.group(1)):
                levels.append(re.findall(r'"([\w\-]+)"', lv))
            if levels:
                zones[z] = levels
        themes.append({"name": name, "label": label.group(1) if label else "",
                       "zones": zones})
    return themes


def build_map(root: Path) -> tuple[str, dict]:
    js_path = root / "city.js"
    js = read(js_path) if js_path.exists() else ""
    html = read(root / "index.html") if (root / "index.html").exists() else ""
    man_path = root / "assets" / "tiles" / "manifest.json"
    manifest = json.loads(read(man_path)) if man_path.exists() else {}
    tiles = root / "assets" / "tiles"
    disk_pngs = sorted(p.name for p in tiles.glob("*.png")) if tiles.exists() else []
    gen_pngs = sorted(p.name for p in (tiles / "gen").glob("*.png")) if (tiles / "gen").exists() else []

    funcs = parse_functions(js)
    rail = parse_map(js, "RAIL")
    road = parse_map(js, "ROAD")
    svc = parse_map(js, "SVC_SPRITE")
    themes = parse_themes(js)
    anim = parse_const(js, "ANIM_SPRITES") or ""

    landmarks = re.findall(r'\{\s*key:\s*"([\w\-]+)"', js)
    all_used = set(rail.values()) | set(road.values()) | set(svc.values())
    for t in themes:
        for lv in t["zones"].values():
            for group in lv:
                all_used.update(group)
    all_used.update(landmarks)

    manifest_keys = sorted(manifest)
    frames = {k: len(v) for k, v in manifest.items()}
    multi = {k: v for k, v in frames.items() if v > 1}
    in_manifest_files = {f for v in manifest.values() for f in v}
    orphans = [f for f in disk_pngs if f not in in_manifest_files]
    missing = sorted(f for f in in_manifest_files if not (tiles / f).exists())

    # sprite key -> owners
    owners = {}
    for k, v in rail.items():
        owners.setdefault(v, []).append(f"RAIL[{k}]")
    for k, v in road.items():
        owners.setdefault(v, []).append(f"ROAD[{k}]")
    for k, v in svc.items():
        owners.setdefault(v, []).append(f"SVC_SPRITE[{k}]")
    for t in themes:
        for z, lv in t["zones"].items():
            for idx, group in enumerate(lv, 1):
                for s in group:
                    owners.setdefault(s, []).append(f"THEME[{t['name']}].{z}[lvl {idx}]")
    for s in landmarks:
        owners.setdefault(s, []).append("LANDMARK")

    L = []
    w = L.append
    w("=" * 78)
    w("LATTICE CITY - ENGINEERING INDEX")
    w("=" * 78)
    w(f"generated from: {root}")
    w("regenerate:     python tools/map_game.py")
    w("")

    w("1. WHERE THIS GAME LIVES")
    w("-" * 78)
    for url, state, repo, path, local, engine in SITES:
        w(f"{state:5s} {url}")
        w(f"      repo      {repo}")
        w(f"      path      {path}")
        w(f"      checkout  {local}")
        w(f"      engine    {engine}")
    w(f"      test build {BUILD}  ->  http://127.0.0.1:8791/   (serve over HTTP, never file://)")
    w("")

    w("2. PAGE WIRING (index.html)")
    w("-" * 78)
    for m in re.finditer(r'(?:src|href)="(\./[^"]+)"', html):
        w(f"      {m.group(1)}")
    w("")

    w("3. FILES")
    w("-" * 78)
    for name in ("city.js", "city.css", "index.html", "radio.js", "radio.json",
                 "assets/tiles/manifest.json", "tools/make_rail_tiles.py"):
        p = root / name
        if p.exists():
            kb = max(1, round(p.stat().st_size / 1024))
            w(f"      {name:34s} {kb:>7} KB")
    w(f"      assets/tiles/*.png                {len(disk_pngs):>7} files")
    w(f"      assets/tiles/gen/*.png            {len(gen_pngs):>7} files")
    w("")

    w("4. CONSTANTS THAT MATTER")
    w("-" * 78)
    for name in ("RS", "TW", "TH", "HALF_W", "HALF_H", "GROUND_OFFSET", "ASSET_VER",
                 "ANIM_SPRITES", "FARE", "TRAIN_CAP", "DWELL", "SAVE_KEY"):
        v = parse_const(js, name)
        if v:
            w(f"      {name:16s} {v}")
    w("")

    w("5. FUNCTIONS (line numbers in city.js)")
    w("-" * 78)
    for line, name, args in funcs:
        w(f"      {line:>5}  {name}({args})")
    w(f"      ---- {len(funcs)} functions")
    w("")

    w("6. SPRITE KEYS (manifest -> files -> pixels -> used by)")
    w("-" * 78)
    for k in manifest_keys:
        files = manifest[k]
        sizes = []
        for f in files:
            s = png_size(tiles / f)
            sizes.append(f"{s[0]}x{s[1]}" if s else "?")
        use = ", ".join(owners.get(k, [])) or "(not referenced in code)"
        frames_note = f"{len(files)} frame(s)" if len(files) > 1 else "1"
        w(f"      {k:34s} {frames_note:11s} {','.join(sizes):22s} {use}")
    w("")

    if multi:
        w("7. MULTI-FRAME KEYS (animation candidates)")
        w("-" * 78)
        for k, n in sorted(multi.items()):
            on = "ANIMATING" if re.search(rf"\b{k}\b", anim) else "static (add to ANIM_SPRITES)"
            w(f"      {k:34s} {n} frames   {on}")
        w("")

    w("8. THEMES -> ZONE -> LEVEL -> SPRITE")
    w("-" * 78)
    w("      This is how you find a building: the theme rolled by the region decides the sprite.")
    for t in themes:
        w(f"      [{t['name']}] {t['label']}")
        for z, levels in t["zones"].items():
            for idx, group in enumerate(levels, 1):
                w(f"          {z} lvl {idx}: {', '.join(group)}")
    w("")

    w("9. SERVICES (SVC_SPRITE + radius/upkeep lives in SVC_RULE)")
    w("-" * 78)
    for k, v in svc.items():
        w(f"      {k:10s} -> {v}")
    w("")

    w("10. RAIL MAP (neighbour bitmask -> sprite)")
    w("-" * 78)
    w("      bits: 1 up-right  2 down-right  4 down-left  8 up-left")
    for k in sorted(rail, key=lambda x: int(x)):
        w(f"      mask {int(k):>2} ({int(k):04b}) -> {rail[k]}")
    w("")

    w("11. HYGIENE")
    w("-" * 78)
    w(f"      manifest keys          {len(manifest_keys)}")
    w(f"      png on disk            {len(disk_pngs)}")
    w(f"      orphan png (not in manifest) {len(orphans)}")
    for o in orphans[:15]:
        w(f"          {o}")
    if len(orphans) > 15:
        w(f"          ... {len(orphans) - 15} more")
    w(f"      missing files (in manifest, not on disk) {len(missing)}")
    for mf in missing[:15]:
        w(f"          {mf}")
    w("")

    w("12. HOW TO FIND AN ASSET (worked example)")
    w("-" * 78)
    w("      Q: where is the windmill?")
    w("      A: python tools/map_game.py --grep windmill")
    for k in manifest_keys:
        if "windmill" in k or "watermill" in k:
            w(f"         {k:30s} -> {', '.join(manifest[k])}")
            w(f"              used by: {', '.join(owners.get(k, ['(theme only)']))}")
    w("      Then section 8: the sprite only appears under those theme entries.")
    w("      Then LC.findSprite('b04_windmill') in the browser to get tile coordinates.")
    w("")

    w("13. BROWSER QA HOOKS (window.LC)")
    w("-" * 78)
    for m in re.finditer(r"^\s{4}([\w]+):?\s*(?:\(|\w+,)", parse_object_block(js, js.find("window.LC =") if "window.LC =" in js else 0), re.M):
        pass
    for hook in ("animInfo", "findSprite", "tileKey", "railStats", "railRoute", "railInfo",
                 "counts", "paint", "tileToScreen", "screenToTile", "focusOn"):
        w(f"      LC.{hook}")
    w("")

    stats = {"keys": len(manifest_keys), "funcs": len(funcs), "themes": len(themes),
             "orphans": len(orphans), "missing": len(missing), "png": len(disk_pngs),
             "multi": len(multi)}
    return "\n".join(L) + "\n", stats


def grep(root: Path, term: str) -> int:
    text, stats = build_map(root)
    hits = [ln for ln in text.splitlines() if term.lower() in ln.lower()]
    if not hits:
        print(f"no match for {term!r}")
        return 1
    print("\n".join(hits[:60]))
    print(f"\n[{len(hits)} matching lines]")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent.parent
    ap.add_argument("--root", default=os.environ.get("LC_GAME_ROOT", LIVE))
    ap.add_argument("--out", default=str(here / "references" / "GAME-INDEX.txt"))
    ap.add_argument("--grep")
    args = ap.parse_args()
    root = Path(args.root)
    if not (root / "city.js").exists():
        print(f"not a Lattice City checkout: {root}", file=sys.stderr)
        return 2
    if args.grep:
        return grep(root, args.grep)
    text, stats = build_map(root)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({len(text.splitlines())} lines)")
    print("  " + ", ".join(f"{k}={v}" for k, v in stats.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
