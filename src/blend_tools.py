"""Blender bridge for the console: import a GLB/blend headlessly and render a studio preview."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

BLENDER = Path(os.environ.get("LYGO_BLENDER") or r"J:\SteamLibrary\steamapps\common\Blender\blender.exe")
WORK = Path(os.environ.get("LYGO_BLENDER_WORK") or (Path(__file__).resolve().parent.parent / "workspace" / "character"))

SCRIPT = r'''
import bpy, math, os, sys
from pathlib import Path
from mathutils import Vector

src = os.environ["LYGO_SRC"]
out = os.environ["LYGO_OUT"]
engine = os.environ.get("LYGO_ENGINE", "WORKBENCH")

bpy.ops.wm.read_factory_settings(use_empty=True)
p = Path(src)
if p.suffix.lower() in {".glb", ".gltf"}:
    bpy.ops.import_scene.gltf(filepath=str(p))
elif p.suffix.lower() == ".blend":
    bpy.ops.wm.open_mainfile(filepath=str(p))
else:
    raise SystemExit("unsupported source: " + str(p))

objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not objs:
    raise SystemExit("no mesh in " + str(p))
mn = Vector((1e9, 1e9, 1e9)); mx = Vector((-1e9, -1e9, -1e9))
for o in objs:
    for c in o.bound_box:
        w = o.matrix_world @ Vector(c)
        mn = Vector((min(mn.x, w.x), min(mn.y, w.y), min(mn.z, w.z)))
        mx = Vector((max(mx.x, w.x), max(mx.y, w.y), max(mx.z, w.z)))
size = mx - mn
for o in objs:
    o.location.x -= (mn.x + mx.x) / 2
    o.location.y -= (mn.y + mx.y) / 2
    o.location.z -= mn.z

bpy.ops.mesh.primitive_plane_add(size=6, location=(0, 0, -0.002))
plane = bpy.context.object
gm = bpy.data.materials.new("ground"); gm.use_nodes = True
gb = gm.node_tree.nodes.get("Principled BSDF")
gb.inputs["Base Color"].default_value = (0.026, 0.033, 0.044, 1); gb.inputs["Roughness"].default_value = 0.6
plane.data.materials.append(gm)

cam_data = bpy.data.cameras.new("cam"); cam = bpy.data.objects.new("cam", cam_data)
bpy.context.scene.collection.objects.link(cam)
cam.location = (size.x * 1.4, -max(2.2, size.z * 2.2), size.z * 0.55)
cam.rotation_euler = (Vector((0, 0, size.z * 0.48)) - cam.location).to_track_quat("-Z", "Y").to_euler()
cam_data.lens = 70
bpy.context.scene.camera = cam

def light(name, loc, energy, s, color):
    d = bpy.data.lights.new(name, "AREA"); d.energy = energy; d.size = s; d.color = color
    o = bpy.data.objects.new(name, d); o.location = loc
    bpy.context.scene.collection.objects.link(o)
    o.rotation_euler = (Vector((0, 0, size.z * 0.5)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
light("key", (2.2, -2.6, 3.0), 700, 3.0, (1.0, 0.9, 0.78))
light("fill", (-2.8, -2.0, 1.5), 300, 4.0, (0.62, 0.78, 1.0))
light("rim", (0.6, 2.8, 2.4), 500, 2.4, (0.7, 0.9, 1.0))

scene = bpy.context.scene
scene.render.engine = "BLENDER_WORKBENCH" if engine == "WORKBENCH" else "CYCLES"
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "MATERIAL"
scene.display.shading.show_shadows = True
scene.display.shading.show_cavity = True
scene.display.shading.cavity_type = "BOTH"
scene.render.resolution_x = 900; scene.render.resolution_y = 1200
scene.world = bpy.data.worlds.new("w"); scene.world.color = (0.02, 0.025, 0.035)
scene.render.filepath = out
bpy.ops.render.render(write_still=True)
print("RENDERED", out)

blend_out = os.environ.get("LYGO_BLEND", "")
if blend_out:
    bpy.ops.wm.save_as_mainfile(filepath=blend_out)
    print("BLEND", blend_out)
'''


def render(path: str, out_png: str = "", engine: str = "WORKBENCH", timeout: int = 300,
           blend_path: str = "") -> dict:
    src = Path(path)
    if not src.exists():
        return {"ok": False, "error": "source not found", "path": str(src)}
    if not BLENDER.exists():
        return {"ok": False, "error": "blender.exe not found", "path": str(BLENDER)}
    WORK.mkdir(parents=True, exist_ok=True)
    out = Path(out_png) if out_png else WORK / (src.stem + "-preview.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    script = WORK / "_render_scene.py"
    script.write_text(SCRIPT, encoding="utf-8")
    env = dict(os.environ, LYGO_SRC=str(src), LYGO_OUT=str(out), LYGO_ENGINE=engine)
    if blend_path:
        env["LYGO_BLEND"] = str(blend_path)
    r = subprocess.run([str(BLENDER), "--background", "--factory-startup", "--python", str(script)],
                       capture_output=True, text=True, timeout=timeout, env=env, cwd=str(WORK))
    ok = r.returncode == 0 and out.exists()
    return {"ok": ok, "path": str(out), "source": str(src), "seconds": None,
            "log": (r.stdout or r.stderr or "")[-500:]}
