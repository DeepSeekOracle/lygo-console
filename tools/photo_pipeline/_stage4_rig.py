
import bpy, os, math
from mathutils import Vector

SRC = os.environ["RIG_SRC"]; OUT = os.environ["RIG_OUT"]; RIG = os.environ["RIG_GLB"]

bpy.ops.wm.open_mainfile(filepath=SRC)
our = next(o for o in bpy.context.scene.objects if o.type == "MESH" and "geometry" in o.name.lower())
for o in list(bpy.context.scene.objects):
    if o.type in {"LIGHT", "CAMERA"}:
        bpy.data.objects.remove(o, do_unlink=True)

bpy.ops.import_scene.gltf(filepath=RIG)
arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
vanguard = [o for o in bpy.context.scene.objects if o.type == "MESH" and o.parent == arm]
target_h = 1.73
for m in vanguard:
    if m.name.lower().startswith("vanguard"):
        mn = Vector((1e9, 1e9, 1e9)); mx = Vector((-1e9, -1e9, -1e9))
        for c in m.bound_box:
            w = m.matrix_world @ Vector(c)
            mn = Vector((min(mn.x, w.x), min(mn.y, w.y), min(mn.z, w.z)))
            mx = Vector((max(mx.x, w.x), max(mx.y, w.y), max(mx.z, w.z)))
        target_h = max(target_h, (mx - mn).z)
for m in list(vanguard):
    bpy.data.objects.remove(m, do_unlink=True)

# The glTF import leaves the armature at scale 0.01; binding to that shrinks the character to ~1 cm
# world units. Normalise the armature transform before parenting.
for o in bpy.context.selected_objects:
    o.select_set(False)
arm.select_set(True)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
print("ARM APPLIED", tuple(round(v, 3) for v in arm.scale))

# scale our mesh to the rig height and put its feet at the rig origin
mn = Vector((1e9, 1e9, 1e9)); mx = Vector((-1e9, -1e9, -1e9))
for c in our.bound_box:
    w = our.matrix_world @ Vector(c)
    mn = Vector((min(mn.x, w.x), min(mn.y, w.y), min(mn.z, w.z)))
    mx = Vector((max(mx.x, w.x), max(mx.y, w.y), max(mx.z, w.z)))
size = mx - mn
s = target_h / max(1e-6, size.z)
our.scale = (our.scale.x * s, our.scale.y * s, our.scale.z * s)
bpy.context.view_layer.objects.active = our
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
mn = Vector((1e9, 1e9, 1e9)); mx = Vector((-1e9, -1e9, -1e9))
for c in our.bound_box:
    w = our.matrix_world @ Vector(c)
    mn = Vector((min(mn.x, w.x), min(mn.y, w.y), min(mn.z, w.z)))
    mx = Vector((max(mx.x, w.x), max(mx.y, w.y), max(mx.z, w.z)))
our.location.x -= (mn.x + mx.x) / 2
our.location.y -= (mn.y + mx.y) / 2
our.location.z -= mn.z
bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)

# bind at rest pose
arm.data.pose_position = "REST"
for o in bpy.context.selected_objects:
    o.select_set(False)
our.select_set(True)
arm.select_set(True)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.parent_set(type="ARMATURE_AUTO")
arm.data.pose_position = "POSE"
print("BOUND", our.name, "verts", len(our.data.vertices), "groups", len(our.vertex_groups))

# export all armature actions (Idle/Run/Walk/TPose)
for o in bpy.context.selected_objects:
    o.select_set(False)
our.select_set(True)
arm.select_set(True)
bpy.context.view_layer.objects.active = arm
out = os.path.join(OUT, "operator-photo.glb")
bpy.ops.export_scene.gltf(filepath=out, use_selection=True, export_apply=False,
                          export_animations=True, export_animation_mode="ACTIONS",
                          export_nla_strips=False, export_vertex_color="MATERIAL",
                          export_image_format="AUTO")
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "operator-photo.blend"))
print("RIG EXPORT", out)
