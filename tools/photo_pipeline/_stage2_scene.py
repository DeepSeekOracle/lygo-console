
import bpy, math, os, sys
from pathlib import Path
from mathutils import Vector

GLB = os.environ["PP_GLB"]
PHOTO = os.environ["PP_PHOTO"]
OUT = Path(os.environ["PP_OUT"])
HEIGHT = float(os.environ.get("PP_HEIGHT", "1.8"))
PAINT = os.environ.get("PP_PAINT", "1") == "1"
ENGINE = os.environ.get("PP_ENGINE", "WORKBENCH")
# photo subject box, measured from the alpha channel by 1_mesh.py's sibling check
PX0, PY0, PX1, PY1 = [float(v) for v in os.environ.get("PP_BOX", "0.339,0.057,0.693,0.920").split(",")]

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=GLB)
obj = next(o for o in bpy.context.scene.objects if o.type == "MESH")

# TripoSR exports facing +Y; turn it so the camera at -Y sees the face.
obj.rotation_euler[2] = math.pi
bpy.context.view_layer.update()

def bounds():
    mn = Vector((1e9, 1e9, 1e9)); mx = Vector((-1e9, -1e9, -1e9))
    for c in obj.bound_box:
        w = obj.matrix_world @ Vector(c)
        mn = Vector((min(mn.x, w.x), min(mn.y, w.y), min(mn.z, w.z)))
        mx = Vector((max(mx.x, w.x), max(mx.y, w.y), max(mx.z, w.z)))
    return mn, mx

mn, mx = bounds()
size = mx - mn
scale = HEIGHT / max(1e-6, size.z)
obj.scale = (scale, scale, scale)
bpy.context.view_layer.update()
mn, mx = bounds()
size = mx - mn
obj.location.x -= (mn.x + mx.x) / 2
obj.location.y -= (mn.y + mx.y) / 2
obj.location.z -= mn.z
bpy.context.view_layer.update()
mn, mx = bounds()
size = mx - mn
print("SCALED", tuple(round(v, 3) for v in size))

me = obj.data
while me.uv_layers:
    me.uv_layers.remove(me.uv_layers[0])
uv = me.uv_layers.new(name="front")

# palette zones (sRGB 0..1) sampled from the reference photo's clothing
ZONES = [
    (0.00, 0.11, (0.06, 0.10, 0.55), "shoe"),      # blue sneakers
    (0.11, 0.47, (0.12, 0.19, 0.30), "jeans"),
    (0.47, 0.80, (0.055, 0.055, 0.065), "hoodie"),
    (0.80, 0.875, (0.62, 0.42, 0.31), "hands"),
    (0.875, 0.935, (0.70, 0.50, 0.38), "face"),
    (0.935, 1.01, (0.045, 0.05, 0.06), "cap"),
]

def zone_mat(name, rgb):
    m = bpy.data.materials.get("zone-" + name) or bpy.data.materials.new("zone-" + name)
    m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = 0.72
    m.diffuse_color = (*rgb, 1)
    return m

mats = [zone_mat(n, c) for (_, _, c, n) in ZONES]
photo_mat = bpy.data.materials.new("photo-front"); photo_mat.use_nodes = True
nt = photo_mat.node_tree
bsdf = nt.nodes.get("Principled BSDF")
img = bpy.data.images.load(PHOTO)
tex = nt.nodes.new("ShaderNodeTexImage"); tex.image = img
uvnode = nt.nodes.new("ShaderNodeUVMap"); uvnode.uv_map = "front"
nt.links.new(uvnode.outputs["UV"], tex.inputs["Vector"])
nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
bsdf.inputs["Roughness"].default_value = 0.68
me.materials.clear()
me.materials.append(photo_mat)
for m in mats:
    me.materials.append(m)

for poly in me.polygons:
    n = (obj.matrix_world.to_3x3() @ poly.normal).normalized()
    z = (obj.matrix_world @ poly.center).z / size.z
    if PAINT and n.y < -0.62:
        poly.material_index = 0
    else:
        idx = 1
        for k, (lo, hi, _, _) in enumerate(ZONES):
            if lo <= z < hi:
                idx = k + 1
                break
        # the head's side/back is hair and cap, not a second face: only the photo carries the face
        if z > 0.82 and n.y > -0.2:
            idx = 6
        poly.material_index = idx
    for li in poly.loop_indices:
        v = me.vertices[me.loops[li].vertex_index].co
        w = obj.matrix_world @ v
        ux = (w.x - mn.x) / max(1e-6, size.x)
        vz = (w.z - mn.z) / max(1e-6, size.z)
        u = PX0 + ux * (PX1 - PX0)
        y = PY0 + (1.0 - vz) * (PY1 - PY0)
        uv.data[li].uv = (u, 1.0 - y)

# ground
bpy.ops.mesh.primitive_plane_add(size=8, location=(0, 0, -0.002))
plane = bpy.context.object
gm = bpy.data.materials.new("ground"); gm.use_nodes = True
gb = gm.node_tree.nodes.get("Principled BSDF")
gb.inputs["Base Color"].default_value = (0.03, 0.037, 0.05, 1); gb.inputs["Roughness"].default_value = 0.55
plane.data.materials.append(gm)

# camera: 3/4 front, full body
bpy.ops.object.camera_add(location=(1.9, -4.2, 1.5))
cam = bpy.context.object
cam.rotation_euler = (Vector((0, 0, HEIGHT * 0.5)) - cam.location).to_track_quat("-Z", "Y").to_euler()
cam.data.lens = 55
bpy.context.scene.camera = cam

def light(loc, energy, s, color):
    bpy.ops.object.light_add(type="AREA", location=loc)
    o = bpy.context.object
    o.data.energy = energy; o.data.size = s; o.data.color = color
    o.rotation_euler = (Vector((0, 0, HEIGHT * 0.5)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
light((2.2, -2.4, 3.0), 700, 3.0, (1.0, 0.9, 0.78))
light((-2.6, -1.8, 1.6), 300, 4.0, (0.62, 0.78, 1.0))
light((0.6, 2.6, 2.6), 520, 2.4, (0.7, 0.9, 1.0))

sc = bpy.context.scene
if ENGINE == "CYCLES":
    sc.render.engine = "CYCLES"
    sc.cycles.samples = 48
    sc.cycles.use_denoising = True
    sc.cycles.device = "CPU"
else:
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.display.shading.light = "STUDIO"
    sc.display.shading.color_type = "TEXTURE"
    sc.display.shading.show_shadows = True
    sc.display.shading.show_cavity = True
    sc.display.shading.cavity_type = "BOTH"
sc.render.resolution_x = 900
sc.render.resolution_y = 1200
sc.world = bpy.data.worlds.new("w"); sc.world.color = (0.02, 0.025, 0.035)
sc.render.filepath = str(OUT / "character.png")
bpy.ops.render.render(write_still=True)

# straight front for comparison
cam.location = (0, -4.4, HEIGHT * 0.5)
cam.rotation_euler = (Vector((0, 0, HEIGHT * 0.5)) - cam.location).to_track_quat("-Z", "Y").to_euler()
sc.render.filepath = str(OUT / "character-front.png")
bpy.ops.render.render(write_still=True)

bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "character.blend"))
bpy.ops.object.select_all(action="DESELECT")
obj.select_set(True)
bpy.ops.export_scene.gltf(filepath=str(OUT / "character-game.glb"), use_selection=True,
                          export_apply=True)
print("STAGE2 OK", OUT)
