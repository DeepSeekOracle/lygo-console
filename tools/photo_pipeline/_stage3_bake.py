
import bpy, os
from pathlib import Path

SRC = os.environ["BAKE_SRC"]
OUT = Path(os.environ["BAKE_OUT"])
SIZE = int(os.environ.get("BAKE_SIZE", "1024"))

bpy.ops.wm.open_mainfile(filepath=SRC)
mesh = next(o for o in bpy.context.scene.objects if o.type == "MESH" and "geometry" in o.name.lower())
print("SOURCE", mesh.name, len(mesh.data.vertices))

# source copy keeps the projected material + planar UVs
src = mesh.copy()
src.data = mesh.data.copy()
bpy.context.scene.collection.objects.link(src)
src.name = "bake_source"

# TripoSR ships a triangle soup with thousands of loose shells: remesh the target into one shell so
# Smart UV Project can make a handful of islands instead of one per face.
rem = tgt = mesh
rv = rem.modifiers.new("bake-remesh", "REMESH")
rv.mode = "VOXEL"
rv.voxel_size = 0.015
rv.use_smooth_shade = True
bpy.context.view_layer.objects.active = rem
bpy.ops.object.modifier_apply(modifier=rv.name)
print("REMESHED", len(rem.data.vertices), "verts", len(rem.data.polygons), "faces")

# target gets a fresh Smart UV layout
tgt = mesh
while tgt.data.uv_layers:
    tgt.data.uv_layers.remove(tgt.data.uv_layers[0])
uv = tgt.data.uv_layers.new(name="baked")
tgt.data.uv_layers.active = uv
bpy.context.view_layer.objects.active = tgt
for o in bpy.context.selected_objects:
    o.select_set(False)
tgt.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
# TripoSR exports a triangle soup: without merging, every face is its own UV island (confetti bake)
bpy.ops.mesh.remove_doubles(threshold=0.0006)
bpy.ops.mesh.normals_make_consistent(inside=False)
bpy.ops.uv.smart_project(angle_limit=3.10, island_margin=0.01)
bpy.ops.object.mode_set(mode="OBJECT")

img = bpy.data.images.new("baked-operator", SIZE, SIZE, alpha=False)
img.colorspace_settings.name = "sRGB"
mat = bpy.data.materials.new("operator-baked")
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes.get("Principled BSDF")
tex = nt.nodes.new("ShaderNodeTexImage")
tex.image = img
tex.interpolation = "Linear"
nt.nodes.active = tex
nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
bsdf.inputs["Roughness"].default_value = 0.7
tgt.data.materials.clear()
tgt.data.materials.append(mat)

scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 2
scene.render.bake.use_pass_direct = False
scene.render.bake.use_pass_indirect = False
scene.render.bake.use_pass_color = True
scene.render.bake.margin = 12
scene.render.bake.use_clear = True
scene.render.bake.use_selected_to_active = True
scene.render.bake.cage_extrusion = 0.03

for o in bpy.context.selected_objects:
    o.select_set(False)
src.select_set(True)
tgt.select_set(True)
bpy.context.view_layer.objects.active = tgt
print("BAKING", SIZE)
bpy.ops.object.bake(type="DIFFUSE", use_clear=True, margin=12, use_selected_to_active=True)

bpy.data.images["baked-operator"].filepath_raw = str(OUT / "character-baked.png")
bpy.data.images["baked-operator"].file_format = "PNG"
bpy.data.images["baked-operator"].save()

bpy.data.objects.remove(src, do_unlink=True)
bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "character-baked.blend"))
print("BAKED", OUT / "character-baked.png")
