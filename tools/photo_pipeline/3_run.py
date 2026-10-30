"""Stage 3: run the whole photo -> mesh -> Blender series in one command."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONSOLE = Path(__import__("os").environ.get("LYGO_CONSOLE_ROOT", r"<CONSOLE_DIR>"))


def run(cmd: list[str]) -> tuple[int, dict]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = r.stdout.strip()
    try:
        data = json.loads(out[out.index("{"):]) if "{" in out else {}
    except json.JSONDecodeError:
        data = {"raw": out[-600:]}
    if not data:
        data = {"raw": (out or r.stderr)[-600:]}
    return r.returncode, data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("photo", nargs="?", default="")
    ap.add_argument("--from-glb", default="", help="skip stage 1")
    ap.add_argument("--out", default="")
    ap.add_argument("--engine", default="WORKBENCH", choices=["WORKBENCH", "CYCLES"])
    ap.add_argument("--height", type=float, default=1.8)
    args = ap.parse_args()
    out = Path(args.out) if args.out else (CONSOLE / "workspace" / "character" / ("pp-" + time.strftime("%Y%m%d-%H%M%S")))
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    summary: dict = {"out": str(out), "engine": args.engine}

    if args.from_glb:
        glb = Path(args.from_glb).resolve()
    else:
        if not args.photo:
            ap.error("give a photo or --from-glb")
        code, mesh = run([sys.executable, str(HERE / "1_mesh.py"), args.photo, "--out", str(out)])
        summary["stage1"] = mesh
        if code != 0 or not mesh.get("ok"):
            print(json.dumps({"ok": False, "stage": "mesh", "detail": mesh}, indent=1))
            return 1
        glb = Path(mesh["glb"]).resolve()
    target = out / "character.glb"
    if glb.resolve() != target.resolve():
        shutil.copyfile(glb, target)

    code, scene = run([sys.executable, str(HERE / "2_blender.py"), str(target), args.photo or "",
                       "--out", str(out), "--engine", args.engine, "--height", str(args.height)])
    summary["stage2"] = scene
    summary["ok"] = bool(code == 0 and scene.get("ok"))
    summary.update({k: scene.get(k) for k in ("glb", "blend", "preview", "front", "game_glb")})
    print(json.dumps(summary, indent=1))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
