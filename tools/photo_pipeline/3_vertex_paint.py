"""Stage 3 (alternative to UV bake): paint the photo into VERTEX COLORS.

Planar UV projection smears at the sides and Smart-UV islands on a decimated scan bake to confetti
(thousands of tiny islands). Vertex colours interpolate across the surface, so face/beard/cap logo read
without UV seams, and glTF carries them as COLOR_0 for the game.

    python 3_vertex_paint.py <character.blend> <photo.png> --out <dir>
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

BLENDER = Path(os.environ.get("LYGO_BLENDER") or r"J:\SteamLibrary\steamapps\common\Blender\blender.exe")
HERE = Path(__file__).resolve().parent

SCRIPT = r'''
import bpy, os, math
from mathutils import Vector

SRC = os.environ["VP_SRC"]; PHOTO = os.environ["VP_PHOTO"]; OUT = os.environ["VP_OUT"]
BOX = [float(v) for v in os.environ.get("VP_BOX", "0.339,0.057,0.693,0.920").split(",")]

bpy.ops.wm.open_mainfile(filepath=SRC)
obj = next(o for o in bpy.context.scene.objects if o.type == "MESH" and "geometry" in o.name.lower())
me = obj.data

img = bpy.data.images.load(PHOTO)
W, H = img.size
px = list(img.pixels)

def sample(u, v):
    x = max(0, min(W - 1, int(u * W))); y = max(0, min(H - 1, int(v * H)))
    i = (y * W + x) * 4
    return px[i], px[i + 1], px[i + 2]

ZONES = [
    (0.00, 0.11, (0.05, 0.09, 0.55)),   # blue sneakers
    (0.11, 0.47, (0.11, 0.17, 0.27)),   # jeans
    (0.47, 0.80, (0.05, 0.05, 0.06)),   # hoodie
    (0.80, 0.875, (0.62, 0.42, 0.31)),  # hands
    (0.875, 0.935, (0.70, 0.50, 0.38)), # face
    (0.935, 1.01, (0.04, 0.045, 0.055)),# cap
]

def palette(z):
    for lo, hi, c in ZONES:
        if lo <= z < hi:
            return c
    return (0.05, 0.05, 0.06)

if not me.color_attributes:
    me.color_attributes.new(name="Col", type="BYTE_COLOR", domain="POINT")
attr = me.color_attributes[0]
attr.name = "Col"

mn, mx = None, None
for v in me.vertices:
    w = obj.matrix_world @ v.co
    mn = w.copy() if mn is None else Vector((min(mn.x, w.x), min(mn.y, w.y), min(mn.z, w.z)))
    mx = w.copy() if mx is None else Vector((max(mx.x, w.x), max(mx.y, w.y), max(mx.z, w.z)))
size = mx - mn
rot = obj.matrix_world.to_3x3()

for v in me.vertices:
    w = obj.matrix_world @ v.co
    n = (rot @ v.normal).normalized()
    zn = (w.z - mn.z) / max(1e-6, size.z)
    if n.y < -0.10:
        un = (w.x - mn.x) / max(1e-6, size.x)
        u = BOX[0] + un * (BOX[2] - BOX[0])
        vv = BOX[1] + (1.0 - zn) * (BOX[3] - BOX[1])
        c = sample(u, vv)
    else:
        c = palette(zn)
    attr.data[v.index].color = (c[0], c[1], c[2], 1.0)

mat = bpy.data.materials.new("operator-vertex")
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes.get("Principled BSDF")
vc = nt.nodes.new("ShaderNodeVertexColor")
vc.layer_name = "Col"
nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
bsdf.inputs["Roughness"].default_value = 0.7
me.materials.clear()
me.materials.append(mat)

bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "character-vertex.blend"))
mesh_sel = bpy.context.view_layer.objects.active = obj
for o in bpy.context.selected_objects: o.select_set(False)
obj.select_set(True)
bpy.ops.export_scene.gltf(filepath=os.path.join(OUT, "character-vertex.glb"), use_selection=True,
                          export_apply=True, export_vertex_color="MATERIAL")
print("VERTEX PAINT DONE")
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("blend")
    ap.add_argument("photo")
    ap.add_argument("--out", default="")
    ap.add_argument("--box", default="0.339,0.057,0.693,0.920")
    args = ap.parse_args()
    src = Path(args.blend).resolve(); photo = Path(args.photo).resolve()
    if not src.exists() or not photo.exists():
        print(json.dumps({"ok": False, "error": "missing input"}))
        return 2
    out = Path(args.out).resolve() if args.out else src.parent
    out.mkdir(parents=True, exist_ok=True)
    script = HERE / "_stage3_vertex.py"
    script.write_text(SCRIPT, encoding="utf-8")
    env = dict(os.environ, VP_SRC=str(src), VP_PHOTO=str(photo), VP_OUT=str(out), VP_BOX=args.box)
    r = subprocess.run([str(BLENDER), "--background", "--factory-startup", "--python", str(script)],
                       capture_output=True, text=True, timeout=900, env=env, cwd=str(HERE))
    ok = r.returncode == 0 and (out / "character-vertex.blend").exists()
    print(json.dumps({"ok": ok, "blend": str(out / "character-vertex.blend"),
                      "glb": str(out / "character-vertex.glb"),
                      "log": (r.stdout or r.stderr or "")[-400:]}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
