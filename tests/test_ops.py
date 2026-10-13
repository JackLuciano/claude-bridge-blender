"""Op-level tests. Run inside Blender:
    blender -b --factory-startup --python tests/test_ops.py
Exits non-zero on failure.
"""

import os
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import math  # noqa: E402

import bpy  # noqa: E402
import mathutils  # noqa: E402

from claude_bridge import ops  # noqa: E402


def run(*batch):
    res, ok = ops.run_batch(list(batch))
    return ok, "\n".join(res)


def fresh():
    ops.op_new({})


TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def t_add_and_scene():
    fresh()
    ok, out = run({"op": "add", "type": "cube", "name": "Box", "loc": [1, 2, 3], "size": 2})
    assert ok, out
    o = bpy.data.objects["Box"]
    assert tuple(round(x, 3) for x in o.location) == (1, 2, 3)
    assert round(o.dimensions.x, 3) == 2
    ok, out = run({"op": "scene"})
    assert "Box[mesh]" in out, out


@test
def t_all_primitives():
    fresh()
    types = [k for k in ops._PRIMS] + ["point", "sun", "spot", "area"]
    ok, out = run(*[{"op": "add", "type": t, "name": f"P_{t}"} for t in types])
    assert ok, out
    for t in types:
        assert f"P_{t}" in bpy.data.objects, t


@test
def t_set_move_rot():
    fresh()
    ok, out = run({"op": "add", "type": "cube", "name": "A"},
                  {"op": "set", "obj": "A", "rot": [0, 0, 90], "scale": 2},
                  {"op": "move", "obj": "A", "by": [1, 0, 0], "rot": [0, 0, 10], "scale": 0.5})
    assert ok, out
    o = bpy.data.objects["A"]
    assert round(o.location.x, 3) == 1
    assert abs(o.rotation_euler.z - 1.745329) < 1e-4
    assert round(o.scale.x, 3) == 1


@test
def t_material():
    fresh()
    ok, out = run({"op": "add", "type": "sphere", "name": "S", "mat": {"name": "Red", "color": "#ff0000", "rough": 0.2, "metal": 1}})
    assert ok, out
    m = bpy.data.materials["Red"]
    bsdf = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    assert abs(bsdf.inputs["Roughness"].default_value - 0.2) < 1e-6
    assert bsdf.inputs["Base Color"].default_value[0] > 0.99
    assert bpy.data.objects["S"].data.materials[0] == m
    # Reuse by name
    ok, out = run({"op": "add", "type": "cube", "name": "C"}, {"op": "mat", "obj": "C", "name": "Red"})
    assert ok and bpy.data.objects["C"].data.materials[0] == m, out
    # Color shorthand on add creates a material
    ok, out = run({"op": "add", "type": "cube", "name": "Q", "color": "#00ff00"})
    assert ok and "mat:Q_mat" in out, out
    # "mat": "#hex" is a color, not a material name (regression: cube came out white)
    for i, spec in enumerate(["#ff0000", [1, 0, 0], {"name": "#ff0000"}]):
        ok, out = run({"op": "add", "type": "cube", "name": f"R{i}", "mat": spec})
        m = bpy.data.objects[f"R{i}"].active_material
        assert ok and m.diffuse_color[0] > 0.99 and m.diffuse_color[1] < 0.01 and not m.name.startswith("#"), (spec, out)


@test
def t_tex_all_kinds():
    fresh()
    for k in ops._TEX_KINDS:
        ok, out = run({"op": "add", "type": "cube", "name": f"T_{k}", "mat": {"name": f"M_{k}"}},
                      {"op": "tex", "obj": f"T_{k}", "kind": k, "scale": 3})
        assert ok and f"tex {k}" in out, out
        nt = bpy.data.materials[f"M_{k}"].node_tree
        bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
        assert bsdf.inputs["Base Color"].is_linked and bsdf.inputs["Roughness"].is_linked, k
    # Wood grain follows the longest side: a flat floor along X/Y, not Z
    ok, out = run({"op": "add", "type": "plane", "name": "Floor", "size": 1, "dims": [6, 4, 0]},
                  {"op": "tex", "obj": "Floor", "kind": "wood"})
    wave = next(n for n in bpy.data.objects["Floor"].active_material.node_tree.nodes if n.type == "TEX_WAVE")
    assert ok and wave.rings_direction == "X", (out, wave.rings_direction)
    # Re-texturing replaces nodes instead of piling them up
    n_before = len(bpy.data.materials["M_wood"].node_tree.nodes)
    ok, out = run({"op": "tex", "mat": "M_wood", "kind": "marble"})
    assert ok and len(bpy.data.materials["M_wood"].node_tree.nodes) <= n_before + 1, out
    ok, out = run({"op": "tex", "obj": "T_wood", "kind": "plaid"})
    assert not ok and "kind must be one of" in out


@test
def t_modifiers_and_apply():
    fresh()
    ok, out = run({"op": "add", "type": "cube", "name": "M"},
                  {"op": "mod", "obj": "M", "type": "bevel", "props": {"width": 0.1, "segments": 3}},
                  {"op": "mod", "obj": "M", "type": "array", "props": {"count": 3}})
    assert ok, out
    o = bpy.data.objects["M"]
    assert [m.type for m in o.modifiers] == ["BEVEL", "ARRAY"]
    assert o.modifiers[1].count == 3
    ok, out = run({"op": "apply", "obj": "M", "mod": "all"})
    assert ok, out
    assert len(o.modifiers) == 0 and len(o.data.vertices) > 8


@test
def t_bool_with_object_ref():
    fresh()
    ok, out = run({"op": "add", "type": "cube", "name": "Base", "size": 2},
                  {"op": "add", "type": "cylinder", "name": "Hole", "size": 0.5, "scale": [1, 1, 3]},
                  {"op": "mod", "obj": "Base", "type": "bool", "props": {"object": "Hole", "operation": "DIFFERENCE"}})
    assert ok, out
    assert bpy.data.objects["Base"].modifiers[0].object.name == "Hole"


@test
def t_dup_select_join_del():
    fresh()
    ok, out = run({"op": "add", "type": "cube", "name": "D"},
                  {"op": "dup", "obj": "D", "n": 3, "by": [2, 0, 0]})
    assert ok, out
    assert len([o for o in bpy.data.objects if o.name.startswith("D")]) == 4
    ok, out = run({"op": "select", "obj": [o.name for o in bpy.data.objects if o.name.startswith("D")]},
                  {"op": "join", "obj": "sel"})
    assert ok, out
    assert len(bpy.context.scene.objects) == 1
    ok, out = run({"op": "del", "obj": "active"})
    assert ok and len(bpy.context.scene.objects) == 0, out


@test
def t_props_get_and_prop():
    fresh()
    ok, out = run({"op": "prop", "path": "scene.render.fps", "value": 30}, {"op": "get", "path": "scene.render.fps"})
    assert ok and "scene.render.fps=30" in out, out
    ok, out = run({"op": "add", "type": "point", "name": "L", "energy": 500, "color": "#ffaa00"},
                  {"op": "get", "path": "L.data.energy"})
    assert ok and "=500" in out, out


@test
def t_camera_and_aim():
    fresh()
    ok, out = run({"op": "add", "type": "cube", "name": "T"},
                  {"op": "cam", "name": "Cam", "loc": [5, -5, 5], "at": "T", "lens": 35})
    assert ok, out
    cam = bpy.data.objects["Cam"]
    assert bpy.context.scene.camera == cam and cam.data.lens == 35
    fwd = cam.matrix_world.to_quaternion() @ __import__("mathutils").Vector((0, 0, -1))
    to_t = (-cam.location).normalized()
    assert fwd.dot(to_t) > 0.999


@test
def t_bop_and_py():
    fresh()
    ok, out = run({"op": "add", "type": "cube", "name": "B"},
                  {"op": "bop", "id": "object.shade_smooth", "obj": "B"},
                  {"op": "py", "code": "out = len(bpy.data.objects)"})
    assert ok and "py 1" in out, out
    # Regression: a non-numeric tuple crashed fmt() with ValueError
    ok, out = run({"op": "py", "code": "out = ('Claude', True, 3)"})
    assert ok and "Claude" in out, out


@test
def t_error_stops_batch():
    fresh()
    ok, out = run({"op": "add", "type": "cube", "name": "X"}, {"op": "set", "obj": "Nope", "loc": [0, 0, 0]},
                  {"op": "add", "type": "cube", "name": "Y"})
    assert not ok and "no object 'Nope'" in out
    assert "X" in bpy.data.objects and "Y" not in bpy.data.objects
    # Clear message for malformed vectors (seen with Haiku)
    ok, out = run({"op": "add", "type": "cube", "name": "V", "scale": [1, 2]})
    assert not ok and "'scale' needs 3 numbers" in out, out
    ok, out = run({"op": "add", "type": "cube", "name": "V2", "loc": [1, 2]})
    assert not ok and "'loc' needs 3 numbers" in out, out
    # set falls back to object data: light energy via both shorthand and props
    ok, out = run({"op": "add", "type": "point", "name": "Lamp"},
                  {"op": "set", "obj": "Lamp", "energy": 150, "color": "#ffaa55"},
                  {"op": "set", "obj": "Lamp", "props": {"shadow_soft_size": 0.5}})
    lamp = bpy.data.objects["Lamp"].data
    assert ok and lamp.energy == 150 and abs(lamp.shadow_soft_size - 0.5) < 1e-6, out
    ok, out = run({"op": "set", "obj": "Lamp", "props": {"nonsense_attr": 1}})
    assert not ok and "nonsense_attr" in out, out
    ok, out = run({"add": {"type": "cube", "name": "Short"}})
    assert ok and "Short" in bpy.data.objects, out
    ok, out = run({"op": "bogus"})
    assert not ok and "unknown op" in out


@test
def t_export_import_render():
    fresh()
    d = tempfile.mkdtemp()
    p = os.path.join(d, "x.obj")
    ok, out = run({"op": "add", "type": "monkey", "name": "Suz"}, {"op": "export", "path": p})
    assert ok and os.path.exists(p), out
    fresh()
    ok, out = run({"op": "import", "path": p})
    assert ok and "imported 1" in out, out
    if os.environ.get("CLAUDE_BRIDGE_NO_GPU"):
        return  # CI machines without OpenGL crash Blender's renderer
    png = os.path.join(d, "r.png")
    ok, out = run({"op": "cam", "loc": [3, -3, 2], "at": [0, 0, 0]},
                  {"op": "add", "type": "sun"},
                  {"op": "render", "path": png, "res": [64, 48], "engine": "BLENDER_WORKBENCH"})
    assert ok and os.path.exists(png), out
    # Settings are restored, and look:true hands back a small JPEG
    r = bpy.context.scene.render
    before = (r.engine, r.resolution_x, r.resolution_percentage)
    ok, out = run({"op": "render", "res": [80, 60], "engine": "BLENDER_WORKBENCH", "preview": True, "look": True, "max": 64})
    assert ok and (r.engine, r.resolution_x, r.resolution_percentage) == before, (out, r.engine)
    assert len(ops.IMAGES) == 1 and ops.IMAGES[0].endswith(".jpg") and os.path.exists(ops.IMAGES[0])
    ok, out = run({"op": "del", "obj": "Camera"}, {"op": "render"})
    assert not ok and "no scene camera" in out, out


@test
def t_batch_safety():
    fresh()
    # Edit Mode: add must not put geometry into the edited mesh
    ok, out = run({"op": "add", "type": "cube", "name": "User"})
    bpy.ops.object.mode_set(mode="EDIT")
    ok, out = run({"op": "add", "type": "cube", "name": "New", "loc": [3, 0, 0]})
    assert ok and "mode edit_mesh>object" in out, out
    assert len(bpy.data.objects["User"].data.vertices) == 8 and "New" in bpy.data.objects, out
    # Same name again updates instead of creating Box.001; another type is an error
    ok, out = run({"op": "add", "type": "cube", "name": "New", "loc": [5, 0, 0]})
    assert ok and "(existed)" in out and "New.001" not in bpy.data.objects, out
    assert round(bpy.data.objects["New"].location.x, 3) == 5
    ok, out = run({"op": "add", "type": "sun", "name": "New"})
    assert not ok and "already exists" in out, out
    ok, out = run({"op": "set", "obj": "New", "name": "User"})
    assert not ok and "taken" in out, out
    # dup copies children by default
    ok, out = run({"op": "add", "type": "cube", "name": "Kid", "loc": [0, 0, 2]},
                  {"op": "set", "obj": "Kid", "parent": "User"},
                  {"op": "dup", "obj": "User", "by": [0, 3, 0], "name": "User2"})
    assert ok and len(bpy.data.objects["User2"].children) == 1, out
    # Batch kinds and undo labels
    assert ops.batch_kind([{"op": "scene"}, {"op": "get", "path": "scene.frame_current"}]) == "ro"
    assert ops.batch_kind([{"op": "undo"}]) == "history"
    assert ops.batch_kind([{"op": "scene"}, {"op": "add"}]) == "write"
    lbl = ops.undo_label([{"op": "add", "name": "Sofa"}, {"op": "mat"}, {"op": "mod"}, {"op": "mod"}, {"op": "tex"}])
    assert lbl == "Claude: add Sofa, mat, mod +2", lbl


@test
def t_dims_bake():
    fresh()
    ok, out = run({"op": "add", "type": "cube", "name": "Top", "dims": [4, 1, 0.1]},
                  {"op": "add", "type": "plane", "name": "Floor", "dims": [6, 4, 0]})
    assert ok, out
    for n, d in (("Top", (4, 1, 0.1)), ("Floor", (6, 4, 0))):
        o = bpy.data.objects[n]
        assert tuple(round(x, 4) for x in o.scale) == (1, 1, 1), (n, tuple(o.scale))
        assert tuple(round(x, 4) for x in o.dimensions) == d, (n, tuple(o.dimensions))
    # Shared mesh: not baked; tex compensates through the mapping node instead
    ok, out = run({"op": "add", "type": "cube", "name": "S1"}, {"op": "dup", "obj": "S1", "linked": True, "name": "S2"},
                  {"op": "set", "obj": "S2", "dims": [6, 2, 2]}, {"op": "tex", "obj": "S2", "kind": "marble"})
    s2 = bpy.data.objects["S2"]
    assert ok and round(s2.scale.x, 3) == 3, (out, tuple(s2.scale))
    mapping = next(n for n in s2.active_material.node_tree.nodes if n.type == "MAPPING")
    assert round(mapping.inputs["Scale"].default_value[0], 3) == 3, out


@test
def t_scene_cap():
    fresh()
    ok, out = run({"op": "add", "type": "cube", "name": "Rock"}, {"op": "dup", "obj": "Rock", "n": 20, "name": "Rock"},
                  *[{"op": "add", "type": "ico", "name": f"P{i}"} for i in range(10)])
    assert ok, out
    ok, out = run({"op": "scene"})
    assert ok and "Rock.001-.020 x20 [mesh]" in out and "Rock.005" not in out, out
    ok, out = run({"op": "scene", "limit": 3})
    lines = out.splitlines()
    assert len(lines) == 5 and lines[-1].startswith("+") and "more" in lines[-1], out


@test
def t_world():
    fresh()
    for spec in ({"color": "#202830", "strength": 0.5}, {"hdri": "interior", "rot": 90}, {"sky": True, "sun": True},
                 {"hdri": "studio", "bg": "#ffffff"}):
        ok, out = run({"op": "world", **spec})
        assert ok and out.startswith("world"), (spec, out)
        nt = bpy.context.scene.world.node_tree
        outn = next(n for n in nt.nodes if n.type == "OUTPUT_WORLD")
        assert outn.inputs["Surface"].is_linked, spec
    assert "Sky_Sun" in bpy.data.objects
    env = [n for n in nt.nodes if n.type == "TEX_ENVIRONMENT"]
    assert len(env) == 1 and env[0].image, "repeat calls must replace nodes"
    ok, out = run({"op": "world", "hdri": "mars"})
    assert not ok and "hdri must be one of" in out


@test
def t_place_and_check():
    fresh()
    ok, out = run({"op": "add", "type": "cube", "name": "Table", "dims": [1.2, 0.8, 0.75], "loc": [0, 0, 2]},
                  {"op": "mod", "obj": "Table", "type": "bevel", "props": {"width": 0.02}},
                  {"op": "set", "obj": "Table", "rot": [0, 0, 30]},
                  {"op": "place", "obj": "Table", "on": "floor"},
                  {"op": "add", "type": "cylinder", "name": "Mug", "dims": [0.08, 0.08, 0.1], "loc": [3, 3, 5]},
                  {"op": "place", "obj": "Mug", "on": "Table", "at": "center"})
    assert ok, out
    bpy.context.view_layer.update()
    tlo, thi = ops._wbox([bpy.data.objects["Table"]])
    mlo, _ = ops._wbox([bpy.data.objects["Mug"]])
    assert abs(tlo.z) < 1e-4, tlo
    assert abs(mlo.z - thi.z) < 1e-3, (mlo.z, thi.z)
    ok, out = run({"op": "check", "only": ["floating", "overlap"]})
    assert ok and out.startswith("check ok"), out
    # A raised box reports its gap; two boxes in each other overlap
    ok, out = run({"op": "add", "type": "cube", "name": "Hover", "size": 1, "loc": [5, 0, 1]},
                  {"op": "add", "type": "cube", "name": "A1", "size": 1, "loc": [8, 0, 0.5]},
                  {"op": "add", "type": "cube", "name": "A2", "size": 1, "loc": [8.5, 0, 0.5]},
                  {"op": "check"})
    assert ok and "floating Hover 0.5 above the floor" in out, out
    assert "overlap A1 x A2" in out and "no camera" in out and "no lights" in out, out
    ok, out = run({"op": "place", "obj": "A2", "against": "A1", "side": "+x"}, {"op": "check", "only": ["overlap"]})
    assert ok and "A2[mesh] @(9,0,0.5)" in out and out.endswith("check ok (5 objects)"), out


@test
def t_coercion_and_api():
    fresh()
    ok, out = run({"op": "add", "type": "spot", "name": "Spot"},
                  {"op": "set", "obj": "Spot", "props": {"spot_size": 45, "color": "#ff0000"}},
                  {"op": "prop", "path": "Spot.data.shadow_soft_size", "value": 0.2},
                  {"op": "add", "type": "cube", "name": "Q"},
                  {"op": "set", "obj": "Q", "props": {"rotation_mode": "quaternion"}},
                  {"op": "set", "obj": "Q", "rot": [0, 0, 90]},
                  {"op": "move", "obj": "Q", "rot": [0, 0, 90]},
                  {"op": "mod", "obj": "Q", "type": "bevel", "props": {"angle_limit": 40, "limit_method": "angle"}},
                  {"op": "add", "type": "cube", "name": "Cutter"},
                  {"op": "mod", "obj": "Q", "type": "bool", "props": {"object": "Cutter"}})
    assert ok, out
    spot = bpy.data.objects["Spot"].data
    assert abs(spot.spot_size - 0.7854) < 1e-3 and spot.color[0] > 0.99 and spot.color[1] < 0.01
    q = bpy.data.objects["Q"]
    assert abs(abs(q.rotation_quaternion.to_euler().z) - math.pi) < 1e-3, tuple(q.rotation_quaternion)
    assert abs(q.modifiers["bevel"].angle_limit - math.radians(40)) < 1e-4 and q.modifiers["bevel"].limit_method == "ANGLE"
    assert q.modifiers["bool"].object.name == "Cutter"
    ok, out = run({"op": "mod", "obj": "Q", "name": "Nope", "props": {}})
    assert not ok and "give type" in out, out
    ok, out = run({"op": "mod", "obj": "Q", "name": "bevel", "props": {"wdth": 1}})
    assert not ok and "has no 'wdth'" in out, out
    # api lookups
    ok, out = run({"op": "api", "id": "mesh.subdivide"}, {"op": "api", "path": "Q.modifiers.bevel"},
                  {"op": "api", "node": "ShaderNodeTexNoise"}, {"op": "api", "search": "bevel"})
    assert ok and "number_cuts:int" in out and "width:float" in out and "Scale" in out and "mesh.bevel" in out, out
    # aim respects parents
    ok, out = run({"op": "add", "type": "empty", "name": "Rig", "rot": [0, 0, 45]}, {"op": "cam", "name": "C2", "loc": [5, 0, 1]},
                  {"op": "set", "obj": "C2", "parent": "Rig"}, {"op": "aim", "obj": "C2", "at": [0, 0, 0]})
    cam = bpy.data.objects["C2"]
    fwd = cam.matrix_world.to_quaternion() @ mathutils.Vector((0, 0, -1))
    assert ok and fwd.dot((-cam.matrix_world.translation).normalized()) > 0.999, out


@test
def t_animation():
    from claude_bridge import anim
    fresh()
    ok, out = run({"op": "add", "type": "cube", "name": "Box"},
                  {"op": "key", "obj": "Box", "f": 1, "loc": [0, 0, 0], "interp": "linear"},
                  {"op": "key", "obj": "Box", "f": 25, "loc": [4, 0, 0], "ease": "out"},
                  {"op": "add", "type": "point", "name": "Lamp"},
                  {"op": "key", "obj": "Lamp", "f": 1, "props": {"energy": 0}},
                  {"op": "key", "obj": "Lamp", "f": 20, "props": {"energy": 500}})
    assert ok, out
    sc = bpy.context.scene
    sc.frame_set(13)
    box = bpy.data.objects["Box"]
    assert 1.5 < box.location.x < 2.5, box.location.x  # linear from frame 1
    kps = [kp for fc in anim.fcurves(box) if fc.data_path == "location" for kp in fc.keyframe_points]
    assert kps[0].interpolation == "LINEAR" and kps[1].easing == "EASE_OUT"
    assert bpy.data.objects["Lamp"].data.animation_data is not None
    ok, out = run({"op": "anim", "start": 1, "end": 48, "fps": 24, "preset": "turntable"},
                  {"op": "anim", "preset": "spin", "obj": "Box"},
                  {"op": "anim", "preset": "pop_in", "obj": "Box", "stagger": 2})
    assert ok and "turntable" in out, out
    cam = sc.camera
    assert cam.parent and cam.parent.name == "Turntable" and sc.frame_end == 48
    sc.frame_set(25)
    assert abs(math.degrees(bpy.data.objects["Turntable"].rotation_euler.z) - 180) < 5
    # Constraints: tracking camera faces its target
    ok, out = run({"op": "cam", "name": "Cam2", "loc": [6, 6, 3], "active": False},
                  {"op": "con", "obj": "Cam2", "type": "track", "target": "Box"},
                  {"op": "add", "type": "curve", "name": "Path"},
                  {"op": "add", "type": "cube", "name": "Rider", "size": 0.2},
                  {"op": "con", "obj": "Rider", "type": "follow_path", "target": "Path"})
    assert ok and "track_to" in out and "follow_path" in out, out
    bpy.context.view_layer.update()
    c2 = bpy.data.objects["Cam2"]
    fwd = c2.matrix_world.to_quaternion() @ mathutils.Vector((0, 0, -1))
    to_box = (box.matrix_world.translation - c2.matrix_world.translation).normalized()
    assert fwd.dot(to_box) > 0.99, fwd.dot(to_box)
    assert bpy.data.objects["Path"].data.animation_data is not None


@test
def t_fit_col_rename_curve():
    fresh()
    d = tempfile.mkdtemp()
    p = os.path.join(d, "big.obj")
    # A model "exported in centimetres": 200 units tall, floating
    ok, out = run({"op": "add", "type": "cube", "name": "Giant", "dims": [50, 50, 200], "loc": [0, 0, 300]},
                  {"op": "export", "path": p})
    fresh()
    ok, out = run({"op": "import", "path": p, "fit": {"height": 1.8}, "col": "Props"})
    assert ok and "fit" in out, out
    o = bpy.context.scene.objects[0]
    bpy.context.view_layer.update()
    lo, hi = ops._wbox([o])
    assert abs(hi.z - lo.z - 1.8) < 1e-3 and abs(lo.z) < 1e-4, (lo, hi)
    assert o.users_collection[0].name == "Props"
    ok, out = run({"op": "fit", "obj": o.name, "longest": 1, "at": [2, 3], "faces": 4})
    assert ok and "dim(" in out, out
    # Collections and renames
    ok, out = run(*[{"op": "add", "type": "cylinder", "name": f"L{i}", "loc": [x, 0, 0]} for i, x in enumerate([3, 1, 2])],
                  {"op": "col", "name": "Table", "obj": ["L0", "L1", "L2"], "color": "green"},
                  {"op": "rename", "obj": ["L0", "L1", "L2"], "seq": "Leg_{n}", "sort": "x"})
    assert ok, out
    assert [bpy.data.objects[f"Leg_{n}"].location.x for n in (1, 2, 3)] == [1, 2, 3]
    assert len(bpy.data.collections["Table"].objects) == 3 and bpy.data.collections["Table"].color_tag == "COLOR_04"
    ok, out = run({"op": "rename", "obj": ["Leg_1", "Leg_2"], "seq": "Same"})
    assert not ok and "same name" in out
    # Curves: pipe and lathe vase
    ok, out = run({"op": "curve", "name": "Pipe", "pts": [[0, 0, 0], [1, 0, 0], [1, 1, 1]], "bevel": 0.05, "mat": "#888888"},
                  {"op": "curve", "name": "Vase", "lathe": [[0, 0], [0.15, 0.02], [0.2, 0.2], [0.08, 0.4], [0.1, 0.5]]})
    assert ok, out
    vase = bpy.data.objects["Vase"]
    assert vase.type == "MESH" and abs(vase.dimensions.z - 0.5) < 1e-3 and abs(vase.dimensions.x - 0.4) < 0.01, tuple(vase.dimensions)
    assert bpy.data.objects["Pipe"].type == "CURVE" and abs(bpy.data.objects["Pipe"].data.bevel_depth - 0.05) < 1e-6


@test
def t_mesh_edit():
    fresh()
    ok, out = run({"op": "add", "type": "cube", "name": "Drawer", "dims": [0.6, 0.5, 0.2]},
                  {"op": "edit", "obj": "Drawer", "sel": "-y", "do": [{"inset": 0.03}, {"extrude": -0.01}]},
                  {"op": "add", "type": "cube", "name": "Tower", "size": 1},
                  {"op": "edit", "obj": "Tower", "sel": "+z", "do": [{"extrude": [0, 0, 1]}, {"inset": 0.1}, {"extrude": -0.3}]},
                  {"op": "add", "type": "cube", "name": "Cuts", "size": 1},
                  {"op": "edit", "obj": "Cuts", "do": [{"cut": {"axis": "z", "n": 2}}]},
                  {"op": "add", "type": "cube", "name": "Bev", "size": 1},
                  {"op": "edit", "obj": "Bev", "do": [{"bevel": {"w": 0.05, "seg": 2}}]},
                  {"op": "add", "type": "cube", "name": "Two", "size": 1, "mat": {"name": "Body", "color": "#ffffff"}},
                  {"op": "mat", "name": "Trim", "color": "#ff0000"},
                  {"op": "edit", "obj": "Two", "sel": {"above": 0.4}, "do": [{"mat": "Trim"}]})
    assert ok, out
    tower = bpy.data.objects["Tower"]
    bpy.context.view_layer.update()
    assert abs(tower.dimensions.z - 2) < 1e-4, tuple(tower.dimensions)
    # 6 + 4 inset ring + 4 walls of the recess
    assert len(bpy.data.objects["Drawer"].data.polygons) == 14, len(bpy.data.objects["Drawer"].data.polygons)
    bpy.context.view_layer.update()
    assert abs(bpy.data.objects["Drawer"].dimensions.y - 0.5) < 1e-4  # recessed inwards, not outwards
    assert len(bpy.data.objects["Cuts"].data.polygons) == 14  # 2 loops add 8 side faces
    assert len(bpy.data.objects["Bev"].data.polygons) > 6
    two = bpy.data.objects["Two"]
    assert sorted({p.material_index for p in two.data.polygons}) == [0, 1] and two.data.materials[1].name == "Trim"
    ok, out = run({"op": "edit", "obj": "Two", "sel": "+q"})
    assert not ok and "sel must be" in out
    ok, out = run({"op": "dup", "obj": "Two", "linked": True, "name": "Two_L"}, {"op": "edit", "obj": "Two", "do": [{"inset": 0.1}]})
    assert not ok and "shares its mesh" in out


@test
def t_nodes():
    fresh()
    ok, out = run({"op": "add", "type": "sphere", "name": "Ball", "mat": "Rusty"},
                  {"op": "nodes", "mat": "Rusty",
                   "add": {"n": "noise", "r": {"type": "ramp"}, "b": "bump"},
                   "set": {"n.Scale": 12, "n.noise_dimensions": "3D", "r.0": "#3a2010", "r.1": "#b0602a", "r.1.pos": 0.7},
                   "link": ["n.Fac>r.Fac", "r.Color>bsdf.Base Color", "n>b.Height", "b.Normal>bsdf.Normal"]},
                  {"op": "nodes", "mat": "Rusty", "show": True})
    assert ok and ("n.Fac>r.Fac" in out or "n.Factor>r.Factor" in out) and "r.Color>bsdf.Base Color" in out, out
    nt = bpy.data.materials["Rusty"].node_tree
    ramp = next(n for n in nt.nodes if n.type == "VALTORGB")
    assert abs(ramp.color_ramp.elements[1].position - 0.7) < 1e-6
    ok, out = run({"op": "nodes", "mat": "Rusty", "link": ["n.Nope>r.Fac"]})
    assert not ok and "has no output 'Nope'" in out and "Fac" in out, out
    ok, out = run({"op": "nodes", "mat": "Rusty", "add": {"x": "flubber"}})
    assert not ok and "unknown node type" in out
    # Geometry nodes: scatter rocks over a ground plane
    ok, out = run({"op": "add", "type": "plane", "name": "Ground", "dims": [10, 10, 0]},
                  {"op": "add", "type": "ico", "name": "Rock", "size": 0.2, "loc": [0, 0, -5]},
                  {"op": "nodes", "gn": "Scatter", "obj": "Ground", "preset": "scatter", "instance": "Rock", "density": 2})
    assert ok and "scatter Rock" in out, out
    dg = bpy.context.evaluated_depsgraph_get()
    # Object Info outputs geometry, so instances show up as instanced geometry of Ground, not as Rock objects.
    n_inst = sum(1 for i in dg.object_instances if i.is_instance and i.parent and i.parent.original.name == "Ground")
    assert 50 < n_inst < 400, n_inst
    # GN modifier inputs by interface name
    ng = bpy.data.node_groups["Scatter"]
    ng.interface.new_socket("Amount", in_out="INPUT", socket_type="NodeSocketFloat")
    ok, out = run({"op": "mod", "obj": "Ground", "name": "Scatter", "inputs": {"Amount": 3.5}})
    md = bpy.data.objects["Ground"].modifiers["Scatter"]
    ident = next(i.identifier for i in ng.interface.items_tree if i.name == "Amount")
    props = getattr(md, "properties", None)
    val = getattr(props.inputs, ident).value if props is not None and hasattr(props, "inputs") else md[ident]
    assert ok and abs(val - 3.5) < 1e-6, out
    # World and compositor trees resolve
    ok, out = run({"op": "nodes", "world": True, "set": {"bg.Strength": 0.5}},
                  {"op": "nodes", "comp": True, "add": {"g": "glare"}})
    assert ok, out


@test
def t_img_and_assets():
    from claude_bridge import assets
    fresh()
    d = tempfile.mkdtemp()
    for fn in ("planks_diff_1k.jpg", "planks_rough_1k.jpg", "planks_nor_gl_1k.jpg", "planks_nor_dx_1k.jpg", "planks_disp_1k.png"):
        im = bpy.data.images.new(fn, 16, 16)
        im.filepath_raw = os.path.join(d, fn)
        im.file_format = "PNG" if fn.endswith("png") else "JPEG"
        im.save()
        bpy.data.images.remove(im)
    maps = assets.detect_maps(d)
    assert set(maps) == {"albedo", "rough", "normal", "disp"} and maps["normal"].endswith("nor_gl_1k.jpg"), maps
    ok, out = run({"op": "add", "type": "plane", "name": "Deck", "dims": [6, 3, 0]},
                  {"op": "img", "obj": "Deck", "dir": d, "size_m": 2, "name": "Planks"})
    assert ok and "tile 2m box" in out, out
    m = bpy.data.materials["Planks"]
    bsdf = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    assert all(bsdf.inputs[s].is_linked for s in ("Base Color", "Roughness", "Normal"))
    assert any(n.type == "TEX_IMAGE" and n.image.colorspace_settings.is_data for n in m.node_tree.nodes)
    # mat/tex work on a material whose Principled node isn't connected (seen on imports)
    bad = bpy.data.materials.new("Imported")
    bad.use_nodes = True
    nt = bad.node_tree
    for l in list(nt.links):
        nt.links.remove(l)
    ok, out = run({"op": "add", "type": "cube", "name": "Imp", "mat": "Imported"},
                  {"op": "mat", "obj": "Imp", "name": "Imported", "color": "#ff0000"},
                  {"op": "tex", "obj": "Imp", "kind": "noise"})
    outn = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    assert ok and outn.inputs["Surface"].is_linked, out
    if os.environ.get("CLAUDE_BRIDGE_NET") == "1":  # real Poly Haven round trip
        os.environ.setdefault("CLAUDE_BRIDGE_ASSETS", tempfile.mkdtemp())
        ok, out = run({"op": "asset", "search": "wood planks floor"})
        assert ok and "textures:" in out, out
        first = out.split(": ", 1)[1].split(" ")[0]
        ok, out = run({"op": "add", "type": "plane", "name": "PH", "dims": [4, 4, 0]},
                      {"op": "asset", "id": first, "obj": "PH"},
                      {"op": "asset", "search": "studio", "type": "hdris"})
        assert ok and "img" in out and "hdris:" in out, out
        hd = out.splitlines()[-1].split(": ", 1)[1].split(" ")[0]
        ok, out = run({"op": "asset", "id": hd})
        assert ok and out.startswith("world"), out
        assert "Asset Credits" in bpy.data.texts
        ok, out = run({"op": "asset", "search": "armchair", "type": "models"})
        mid = out.split(": ", 1)[1].split(" ")[0]
        ok, out = run({"op": "asset", "id": mid, "at": [3, 0]})
        assert ok and "imported" in out and "base z0.00" in out, out


@test
def t_risky():
    fresh()
    bpy.data.objects.new("UserThing", None)
    bpy.context.scene.collection.objects.link(bpy.data.objects["UserThing"])
    ok, out = run({"op": "add", "type": "cube", "name": "Mine"})
    r = ops.describe_risky([{"op": "del", "obj": "Mine"}, {"op": "add"}])
    assert r == [], r
    r = ops.describe_risky([{"op": "del", "obj": ["Mine", "UserThing"]}, {"op": "py", "code": "import os"},
                            {"op": "new"}, {"op": "save", "path": "C:/Windows/x.blend"},
                            {"op": "export", "path": os.path.join(tempfile.gettempdir(), "ok.glb")},
                            {"op": "bop", "id": "wm.quit_blender"}])
    assert len(r) == 5 and "UserThing" in r[0] and "Mine" not in r[0] and r[1].startswith("run Python"), r


@test
def t_room():
    fresh()
    ok, out = run({"op": "room", "pts": [[0, 0], [5, 0], [5, 4], [0, 4]], "h": 2.6, "t": 0.12, "ceiling": True,
                   "openings": [{"wall": 0, "at": 1, "w": 0.9}, {"wall": 1, "at": 1.2, "w": 1.6, "h": 1.2, "sill": 0.9}]})
    assert ok and "4 walls" in out and "2 openings" in out, out
    col = bpy.data.collections["Room"]
    names = {o.name for o in col.objects}
    for n in ("Room_Wall_1", "Room_Wall_2", "Room_Wall_3", "Room_Wall_4", "Room_Floor", "Room_Ceiling",
              "Room_Frame_1_1", "Room_Frame_2_2", "Room_Glass_2_2"):
        assert n in names, (n, names)
    bpy.context.view_layer.update()
    w1 = bpy.data.objects["Room_Wall_1"]
    lo, hi = ops._wbox([w1])
    assert abs((hi.x - lo.x) - 5.12) < 1e-3 and abs(hi.z - 2.6) < 1e-4, (lo, hi)
    # The door is a real hole: a ray through its centre passes the wall
    from mathutils.bvhtree import BVHTree
    t = ops._bvh(w1)
    hit = t.ray_cast(mathutils.Vector((1.45, -1, 1.0)), mathutils.Vector((0, 1, 0)), 2)
    assert hit[0] is None, hit
    hit = t.ray_cast(mathutils.Vector((3.5, -1, 1.0)), mathutils.Vector((0, 1, 0)), 2)
    assert hit[0] is not None
    ok, out = run({"op": "room", "name": "Bad", "pts": [[0, 0], [2, 0], [2, 2]], "openings": [{"wall": 0, "at": 1.5, "w": 1}]})
    assert not ok and "doesn't fit" in out, out
    ok, out = run({"op": "check", "only": ["floating"]})
    assert ok and "Room_" not in out, out


@test
def t_game():
    fresh()
    ok, out = run({"op": "add", "type": "cylinder", "name": "Barrel Body", "args": {"vertices": 24}},
                  {"op": "set", "obj": "Barrel Body", "scale": [1, 1, 2], "rot": [0, 0, 30]},
                  {"op": "mod", "obj": "Barrel Body", "type": "subsurf", "props": {"levels": 2}},
                  {"op": "check", "only": ["game"], "budget": 500})
    assert ok and "OVER" in out and "unapplied rotation/scale" in out and "n-gons" in out and "name has spaces" in out, out
    o = bpy.data.objects["Barrel Body"]
    ok, out = run({"op": "apply", "obj": "Barrel Body", "mod": "all"})
    while o.data.uv_layers:
        o.data.uv_layers.remove(o.data.uv_layers[0])
    ok, out = run({"op": "gameprep", "obj": "Barrel Body", "engine": "unreal", "lods": 2, "collision": True})
    assert ok and "transforms" in out and "uv" in out and "LOD2" in out and "UCX_Barrel_Body_00" in out, out
    o = bpy.data.objects["Barrel_Body"]
    assert o.data.uv_layers and tuple(round(s, 4) for s in o.scale) == (1, 1, 1)
    assert len(bpy.data.objects["Barrel_Body_LOD2"].data.polygons) < len(o.data.polygons) / 2
    d = tempfile.mkdtemp()
    ok, out = run({"op": "export", "path": d, "preset": "unity", "obj": "Barrel_Body", "per": "object"},
                  {"op": "export", "path": os.path.join(d, "scene.glb"), "preset": "godot"})
    assert ok and os.path.exists(os.path.join(d, "Barrel_Body.fbx")) and os.path.exists(os.path.join(d, "scene.glb")), out


def main():
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    sys.stdout.flush()
    os._exit(1 if failed else 0)


main()
