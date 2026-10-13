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

import bpy  # noqa: E402

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
    png = os.path.join(d, "r.png")
    ok, out = run({"op": "cam", "loc": [3, -3, 2], "at": [0, 0, 0]},
                  {"op": "add", "type": "sun"},
                  {"op": "render", "path": png, "res": [64, 48], "engine": "BLENDER_WORKBENCH"})
    assert ok and os.path.exists(png), out


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
                  {"op": "set", "obj": "S2", "dims": [6, 2, 2]}, {"op": "tex", "obj": "S2", "kind": "wood"})
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
