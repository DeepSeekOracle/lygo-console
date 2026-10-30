"""Photo -> mesh -> Blender in one call.

Wraps the console's TripoSR limb (local GPU, MIT) and the Blender bridge so the operator can hand a
photo in and get back a GLB, a Blender scene and a rendered preview, without a manual step between.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from blend_tools import WORK, render
from mesh_tools import mesh_from_image


def photo_to_3d(path: str, paint: bool = True, engine: str = "WORKBENCH",
                resolution: Any = None, foreground: Any = None, timeout: Any = 1200) -> dict[str, Any]:
    photo = Path(str(path or ""))
    if not photo.exists():
        return {"ok": False, "error": "photo_not_found", "path": str(photo)}

    mesh = mesh_from_image(str(photo), resolution, True, timeout, foreground)
    if not mesh.get("ok"):
        return {"ok": False, "stage": "mesh", "photo": str(photo), "detail": mesh}

    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = WORK / "photo-to-3d" / stamp
    outdir.mkdir(parents=True, exist_ok=True)
    glb = outdir / "character.glb"
    shutil.copyfile(mesh["path"], glb)
    blend = outdir / "character.blend"
    preview = outdir / "character.png"

    shot = render(str(glb), str(preview), engine or "WORKBENCH", timeout=300, blend_path=str(blend))
    if not shot.get("ok"):
        return {"ok": False, "stage": "blender", "photo": str(photo), "glb": str(glb), "detail": shot}

    return {
        "ok": True,
        "photo": str(photo),
        "glb": str(glb),
        "blend": str(blend),
        "preview": str(preview),
        "dir": str(outdir),
        "engine": engine,
        "url": mesh.get("url", ""),
        "note": "TripoSR gives an untextured shell; the photo can be projected in Blender, but a "
                "likeness texture needs UV unwrap + bake or texture painting.",
    }

def mesh_to_blender(glb_path: str, engine: str = "WORKBENCH", out_dir: str = "") -> dict[str, Any]:
    """Skip the photo step: take an existing GLB straight into a Blender scene + preview."""
    src = Path(str(glb_path or ""))
    if not src.exists():
        return {"ok": False, "error": "glb_not_found", "path": str(src)}
    outdir = Path(out_dir) if out_dir else (WORK / "photo-to-3d" / time.strftime("%Y%m%d-%H%M%S"))
    outdir.mkdir(parents=True, exist_ok=True)
    glb = outdir / "character.glb"
    if src.resolve() != glb.resolve():
        shutil.copyfile(src, glb)
    blend = outdir / "character.blend"
    preview = outdir / "character.png"
    shot = render(str(glb), str(preview), engine or "WORKBENCH", timeout=300, blend_path=str(blend))
    if not shot.get("ok"):
        return {"ok": False, "stage": "blender", "glb": str(glb), "detail": shot}
    return {"ok": True, "glb": str(glb), "blend": str(blend), "preview": str(preview),
            "dir": str(outdir), "engine": engine}


def _main() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Photo -> TripoSR mesh -> Blender scene + preview")
    ap.add_argument("photo", nargs="?", help="input photo on this PC")
    ap.add_argument("--from-glb", default="", help="skip TripoSR and use an existing GLB")
    ap.add_argument("--engine", default="WORKBENCH", choices=["WORKBENCH", "CYCLES"])
    ap.add_argument("--resolution", type=int, default=None)
    ap.add_argument("--foreground", type=float, default=None)
    ap.add_argument("--out", default="", help="output directory")
    args = ap.parse_args()
    if args.from_glb:
        result = mesh_to_blender(args.from_glb, args.engine, args.out)
    elif args.photo:
        result = photo_to_3d(args.photo, True, args.engine, args.resolution, args.foreground)
    else:
        ap.error("give a photo or --from-glb")
        return 2
    print(json.dumps(result, indent=1))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_main())
