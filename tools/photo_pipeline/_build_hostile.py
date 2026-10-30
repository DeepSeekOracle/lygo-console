
import bpy, os, math
from mathutils import Vector

RIG = os.environ["HOSTILE_RIG"]
OUT = os.environ["HOSTILE_OUT"]

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=RIG)
arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
body = next(o for o in bpy.context.scene.objects if o.type == "MESH" and o.name.lower().startswith("vanguard_mesh"))
visor = next((o for o in bpy.context.scene.objects if o.type == "MESH" and "visor" in o.name.lower()), None)
for o in list(bpy.context.scene.objects):
    if o.type == "MESH" and o not in {body, visor}:
        bpy.data.objects.remove(o, do_unlink=True)

def mat(name, rgba, metallic=0.75, rough=0.42, emit=None, strength=0.0):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*rgba, 1)
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = rough
    if emit:
        b.inputs["Emission Color"].default_value = (*emit, 1)
        b.inputs["Emission Strength"].default_value = strength
    m.diffuse_color = (*rgba, 1)   # Workbench MATERIAL preview
    return m

GRAPHITE = mat("HostileGraphite", (0.075, 0.08, 0.09), 0.85, 0.38)
CRIMSON = mat("HostileCrimson", (0.32, 0.015, 0.02), 0.7, 0.35)
STEEL = mat("HostileSteel", (0.10, 0.11, 0.12), 0.95, 0.28)
OPTIC = mat("HostileOptic", (1.0, 0.12, 0.06), 0.0, 0.2, (1.0, 0.08, 0.03), 6.0)

# body: dark gunmetal with a crimson chest trim
body.data.materials.clear()
body.data.materials.append(GRAPHITE)
if visor is not None:
    visor.data.materials.clear()
    visor.data.materials.append(OPTIC)

parts = []

def add_part(name, kind, loc, dims, bone, material, rot=(0, 0, 0)):
    if kind == "cube":
        bpy.ops.mesh.primitive_cube_add(size=1, location=loc, rotation=rot)
    elif kind == "cyl":
        bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.5, depth=1, location=loc, rotation=rot)
    elif kind == "ico":
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=0.5, location=loc, rotation=rot)
    o = bpy.context.active_object
    o.name = name
    o.scale = dims
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    o.data.materials.append(material)
    vg = o.vertex_groups.new(name=bone)
    vg.add(list(range(len(o.data.vertices))), 1.0, "REPLACE")
    mod = o.modifiers.new("armature", "ARMATURE")
    mod.object = arm
    o.parent = arm
    o.matrix_parent_inverse = arm.matrix_world.inverted()
    parts.append(o)
    return o

# head: crest + brow plate
add_part("helmet-brow", "cube", (0, -0.02, 1.685), (0.235, 0.19, 0.045), "mixamorig:Head", STEEL)
# chest plate + crimson sternum bar
add_part("chest-plate", "cube", (0, 0.05, 1.36), (0.40, 0.24, 0.34), "mixamorig:Spine2", GRAPHITE)
add_part("chest-trim", "cube", (0, -0.125, 1.38), (0.30, 0.02, 0.05), "mixamorig:Spine2", CRIMSON)
# pauldrons
add_part("pauldron-l", "ico", (-0.28, 0.02, 1.48), (0.20, 0.18, 0.15), "mixamorig:LeftArm", STEEL)
add_part("pauldron-r", "ico", (0.28, 0.02, 1.48), (0.20, 0.18, 0.15), "mixamorig:RightArm", STEEL)
# backpack + antenna
add_part("backpack", "cube", (0, 0.20, 1.30), (0.30, 0.16, 0.34), "mixamorig:Spine2", GRAPHITE)
add_part("antenna", "cyl", (0.13, 0.24, 1.62), (0.012, 0.012, 0.38), "mixamorig:Spine2", CRIMSON)
# thigh + knee plates
add_part("thigh-l", "cube", (-0.115, -0.01, 0.82), (0.16, 0.18, 0.26), "mixamorig:LeftUpLeg", GRAPHITE)
add_part("thigh-r", "cube", (0.115, -0.01, 0.82), (0.16, 0.18, 0.26), "mixamorig:RightUpLeg", GRAPHITE)
add_part("knee-l", "cube", (-0.115, -0.11, 0.55), (0.14, 0.05, 0.09), "mixamorig:LeftLeg", CRIMSON)
add_part("knee-r", "cube", (0.115, -0.11, 0.55), (0.14, 0.05, 0.09), "mixamorig:RightLeg", CRIMSON)

# join the add-ons into one skinned mesh
bpy.ops.object.select_all(action="DESELECT")
for p in parts:
    p.select_set(True)
bpy.context.view_layer.objects.active = parts[0]
bpy.ops.object.join()
armor = bpy.context.active_object
armor.name = "hostile_armor"

# export rig + both skinned meshes with the existing clips
bpy.ops.object.select_all(action="DESELECT")
for o in (arm, body, armor):
    o.select_set(True)
if visor is not None:
    visor.select_set(True)
bpy.context.view_layer.objects.active = arm
out = os.path.join(OUT, "hostile-rig.glb")
bpy.ops.export_scene.gltf(filepath=out, use_selection=True, export_apply=False,
                          export_animations=True, export_animation_mode="ACTIONS",
                          export_nla_strips=False, export_image_format="AUTO")
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "hostile-rig.blend"))
print("HOSTILE EXPORT", out)
