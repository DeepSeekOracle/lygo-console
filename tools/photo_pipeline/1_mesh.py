"""Stage 1: photo -> GLB with TripoSR (LYGO console limb)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CONSOLE = Path(__import__("os").environ.get("LYGO_CONSOLE_ROOT", r"<CONSOLE_DIR>"))
sys.path.insert(0, str(CONSOLE / "src"))

from mesh_tools import mesh_from_image  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("photo")
    ap.add_argument("--out", default="", help="output dir (default: workspace/character/pp-<time>)")
    ap.add_argument("--resolution", type=int, default=192)
    ap.add_argument("--foreground", type=float, default=0.85)
    args = ap.parse_args()
    photo = Path(args.photo)
    if not photo.exists():
        print(json.dumps({"ok": False, "error": "photo_not_found", "path": str(photo)}))
        return 2
    outdir = Path(args.out) if args.out else (CONSOLE / "workspace" / "character" / ("pp-" + __import__("time").strftime("%Y%m%d-%H%M%S")))
    outdir.mkdir(parents=True, exist_ok=True)
    got = mesh_from_image(str(photo), args.resolution, True, 1200, args.foreground)
    result = {"ok": bool(got.get("ok")), "stage": "mesh", "photo": str(photo), "out": str(outdir)}
    if got.get("ok"):
        result["glb"] = got["path"]
    else:
        result["detail"] = got
    print(json.dumps(result, indent=1))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
