
import bpy, os, json, shutil

ASSETS = os.environ["ENEMY_ASSETS"]
OUT = os.environ["ENEMY_OUT"]
GAME = os.environ["ENEMY_GAME"]

# source glb -> { material name: (base rgb, metallic, roughness, emissive rgb or None, strength) }
JOBS = {
    "cesium-man": {
        "Cesium_Man-effect": ((0.055, 0.06, 0.065), 0.55, 0.45, None, 0.0),
    },
    "robot-rig": {
        "Grey": ((0.10, 0.115, 0.125), 0.85, 0.38, None, 0.0),
        "Main": ((0.30, 0.02, 0.03), 0.70, 0.34, None, 0.0),
        "Black": ((0.02, 0.02, 0.025), 0.60, 0.50, None, 0.0),
        "Orange": ((0.30, 0.02, 0.03), 0.70, 0.34, None, 0.0),
    },
    "xbot-rig": {
        "Beta_Joints_MAT": ((0.035, 0.035, 0.04), 0.70, 0.40, (0.70, 0.10, 0.75), 1.1),
        "asdf1:Beta_HighLimbsGeoSG2": ((0.09, 0.12, 0.10), 0.55, 0.48, None, 0.0),
    },
}

def make_material(name, spec):
    base, metallic, rough, emit, strength = spec
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*base, 1)
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = rough
    if emit:
        b.inputs["Emission Color"].default_value = (*emit, 1)
        b.inputs["Emission Strength"].default_value = strength
    m.diffuse_color = (*base, 1)
    return m

for src, mapping in JOBS.items():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=os.path.join(ASSETS, f"{src}.glb"))
    arm = next((o for o in bpy.context.scene.objects if o.type == "ARMATURE"), None)
    replaced = {}
    for obj in [o for o in bpy.context.scene.objects if o.type == "MESH"]:
        for slot in obj.material_slots:
            old = slot.material
            if not old:
                continue
            key = old.name
            # glTF appends .001 etc for duplicates: match on the stem
            stem = key.split(".")[0]
            spec = mapping.get(key) or mapping.get(stem)
            if not spec:
                continue
            new = replaced.get(stem)
            if not new:
                new = make_material(f"Hostile_{stem}", spec)
                replaced[stem] = new
            slot.material = new
    out = os.path.join(OUT, f"hostile-{src}.glb")
    bpy.ops.object.select_all(action="DESELECT")
    for o in bpy.context.scene.objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.export_scene.gltf(filepath=out, use_selection=True, export_apply=False,
                              export_animations=True, export_animation_mode="ACTIONS",
                              export_nla_strips=False)
    shutil.copyfile(out, os.path.join(GAME, f"hostile-{src}.glb"))
    print("RECOLOURED", src, "materials", list(replaced), "->", os.path.join(GAME, f"hostile-{src}.glb"))
