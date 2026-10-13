"""Building and organizing ops: fit, col, rename, curve."""

import re

import bpy
import mathutils

from .ops import _add_material, _check_free, _tree, _wbox, objs, op, summary, v3

# ---------------------------------------------------------------- fit (normalize imports)


def _roots_of(obs):
    s = set(obs)
    return [o for o in obs if o.parent not in s]


@op("fit")
def op_fit(a):
    """Normalize an imported model: real size, sitting on the floor, origin-independent.
    a: obj, height | longest | dims [x,y,z] (meters; uniform scale), at [x,y] (default: keep), floor (default true),
    apply (apply scale into meshes, default true), faces (decimate to about this many faces)."""
    roots = _roots_of(objs(a.get("obj")))
    bpy.context.view_layer.update()
    lo, hi = _wbox(roots)
    size = hi - lo
    k = 1.0
    if "height" in a:
        k = float(a["height"]) / max(size.z, 1e-9)
    elif "longest" in a:
        k = float(a["longest"]) / max(max(size), 1e-9)
    elif "dims" in a:
        want = v3(a, "dims")
        k = min(want[i] / size[i] for i in range(3) if size[i] > 1e-9)
    c = (lo + hi) / 2
    pivot = mathutils.Vector((c.x, c.y, lo.z))
    for r in roots:
        mw = r.matrix_world.copy()
        # Scale around the bottom centre so the model doesn't drift away while resizing.
        r.matrix_world = (mathutils.Matrix.Translation(pivot) @ mathutils.Matrix.Scale(k, 4)
                          @ mathutils.Matrix.Translation(-pivot) @ mw)
    bpy.context.view_layer.update()
    lo, hi = _wbox(roots)
    c = (lo + hi) / 2
    at = a.get("at", [c.x, c.y])
    delta = mathutils.Vector((at[0] - c.x, at[1] - c.y, -lo.z if a.get("floor", True) else 0))
    for r in roots:
        mw = r.matrix_world.copy()
        mw.translation += delta
        r.matrix_world = mw
    bpy.context.view_layer.update()
    if a.get("apply", True):
        meshes = [x for r in roots for x in _tree(r) if x.type == "MESH" and x.data.users == 1]
        if meshes:
            with bpy.context.temp_override(selected_editable_objects=meshes, selected_objects=meshes,
                                           active_object=meshes[0], object=meshes[0]):
                bpy.ops.object.transform_apply(location=False, rotation=False, scale=True, isolate_users=False)
    note = ""
    if a.get("faces"):
        meshes = [x for r in roots for x in _tree(r) if x.type == "MESH"]
        total = sum(len(m.data.polygons) for m in meshes)
        if total > int(a["faces"]):
            ratio = max(0.01, int(a["faces"]) / total)
            for m in meshes:
                md = m.modifiers.get("Fit_Decimate") or m.modifiers.new("Fit_Decimate", "DECIMATE")
                md.ratio = ratio
            note = f" decimate {total}->{int(total * ratio)} faces"
    lo, hi = _wbox(roots)
    d = hi - lo
    return f"fit {','.join(r.name for r in roots)} x{k:.4g} -> dim({d.x:.2f},{d.y:.2f},{d.z:.2f}) base z{lo.z:.2f}{note}"


# ---------------------------------------------------------------- collections


def _collection(name, parent=None):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
    parent_col = bpy.data.collections.get(parent) if parent else bpy.context.scene.collection
    if parent and parent_col is None:
        parent_col = _collection(parent)
    if col.name not in parent_col.children and col != parent_col:
        for other in [bpy.context.scene.collection, *bpy.data.collections]:
            if other != parent_col and col.name in other.children:
                other.children.unlink(col)
        parent_col.children.link(col)
    return col


def move_to_collection(o, name):
    col = _collection(name)
    for x in _tree(o):
        for c in list(x.users_collection):
            if c != col:
                c.objects.unlink(x)
        if x.name not in col.objects:
            col.objects.link(x)
    return col


def _layer_collection(lc, col):
    if lc.collection == col:
        return lc
    for ch in lc.children:
        r = _layer_collection(ch, col)
        if r:
            return r
    return None


_COL_COLORS = {"none": "NONE", "red": "COLOR_01", "orange": "COLOR_02", "yellow": "COLOR_03", "green": "COLOR_04",
               "blue": "COLOR_05", "violet": "COLOR_06", "pink": "COLOR_07", "brown": "COLOR_08"}


@op("col")
def op_col(a):
    """Collections. a: name, obj (move these objects, with their children, into it), parent (collection),
    hide (viewport+render), exclude (from the view layer), color (red|orange|yellow|green|blue|violet|pink|brown)."""
    col = _collection(a["name"], a.get("parent"))
    moved = []
    if a.get("obj"):
        for o in objs(a["obj"]):
            move_to_collection(o, col.name)
            moved.append(o.name)
    if "hide" in a:
        col.hide_viewport = col.hide_render = bool(a["hide"])
    if "exclude" in a:
        lc = _layer_collection(bpy.context.view_layer.layer_collection, col)
        if lc:
            lc.exclude = bool(a["exclude"])
    if "color" in a:
        col.color_tag = _COL_COLORS.get(str(a["color"]).lower(), a["color"])
    return f"col {col.name}: {len(col.all_objects)} objects" + (f" (+{','.join(moved[:6])})" if moved else "")


# ---------------------------------------------------------------- rename


@op("rename")
def op_rename(a):
    """Batch rename. a: obj, seq ('Leg_{n}': numbered, ordered by sort), sort (x|y|z|name), start (default 1),
    prefix, suffix, find + replace (regex), data (rename mesh/light data too, default true)."""
    obs = objs(a.get("obj"))
    srt = a.get("sort", "name")
    if srt in ("x", "y", "z"):
        i = "xyz".index(srt)
        obs = sorted(obs, key=lambda o: o.matrix_world.translation[i])
    else:
        obs = sorted(obs, key=lambda o: o.name)
    new = []
    for n, o in enumerate(obs, start=int(a.get("start", 1))):
        nm = a["seq"].replace("{n}", str(n)) if a.get("seq") else o.name
        if a.get("find"):
            nm = re.sub(a["find"], a.get("replace", ""), nm)
        nm = a.get("prefix", "") + nm + a.get("suffix", "")
        new.append(nm)
    if len(set(new)) != len(new):
        raise ValueError("rename would give two objects the same name; use seq with {n}")
    mine = set(obs)
    for nm in new:
        other = bpy.data.objects.get(nm)
        if other is not None and other not in mine:
            _check_free(nm)
    # Two passes so swapping names inside the set doesn't collide.
    for o in obs:
        o.name = "__claude_tmp_" + o.name
    out = []
    for o, nm in zip(obs, new):
        old = o.name[len("__claude_tmp_"):]
        o.name = nm
        if a.get("data", True) and o.data is not None and o.data.users == 1:
            o.data.name = nm
        out.append(f"{old}>{nm}")
    return "renamed " + " ".join(out[:12]) + (f" +{len(out) - 12}" if len(out) > 12 else "")


# ---------------------------------------------------------------- curves


@op("curve")
def op_curve(a):
    """Curve from points. a: name, pts [[x,y,z],...], type (poly|smooth), cyclic, bevel (thickness radius, m),
    res (bevel roundness, default 4), fill (cyclic 2D curves: fill the shape), lathe ([[r,z],...] profile spun
    around Z: vases, bottles, lamp shades, turned legs), steps (lathe segments, default 48), loc, mat, smooth,
    mesh (convert to a mesh)."""
    name = a.get("name", "Curve")
    _check_free(name)
    lathe = a.get("lathe")
    pts = [[p[0], 0, p[1]] for p in lathe] if lathe else a.get("pts")
    if not pts or len(pts) < 2:
        raise ValueError("curve needs pts [[x,y,z],...] (or lathe [[r,z],...]) with at least 2 points")
    pts = [list(p) + [0] * (3 - len(p)) for p in pts]
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "2D" if a.get("fill") else "3D"
    smooth = a.get("type", "poly") == "smooth"
    sp = cu.splines.new("BEZIER" if smooth else "POLY")
    if smooth:
        sp.bezier_points.add(len(pts) - 1)
        for bp, p in zip(sp.bezier_points, pts):
            bp.co = p
            bp.handle_left_type = bp.handle_right_type = "AUTO"
    else:
        sp.points.add(len(pts) - 1)
        for cp, p in zip(sp.points, pts):
            cp.co = (*p, 1)
    sp.use_cyclic_u = bool(a.get("cyclic"))
    if a.get("bevel"):
        cu.bevel_depth = float(a["bevel"])
        cu.bevel_resolution = int(a.get("res", 4))
        cu.use_fill_caps = True
    if a.get("fill"):
        cu.fill_mode = "BOTH"
    o = bpy.data.objects.new(name, cu)
    bpy.context.collection.objects.link(o)
    if "loc" in a:
        o.location = v3(a, "loc")
    if lathe:
        md = o.modifiers.new("Lathe", "SCREW")
        md.axis = "Z"
        md.steps = md.render_steps = int(a.get("steps", 48))
        md.use_merge_vertices = True
        md.use_normal_calculate = True
        md.use_smooth_shade = True
    if a.get("mesh") or lathe:
        dg = bpy.context.evaluated_depsgraph_get()
        me = bpy.data.meshes.new_from_object(o.evaluated_get(dg))
        me.name = name
        loc = o.location.copy()
        bpy.data.objects.remove(o)
        bpy.data.curves.remove(cu)
        o = bpy.data.objects.new(name, me)
        bpy.context.collection.objects.link(o)
        o.location = loc
        if a.get("smooth", bool(lathe)):
            for p in me.polygons:
                p.use_smooth = True
    for x in bpy.context.selected_objects:
        x.select_set(False)
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    _add_material(o, a)
    return "+ " + summary(o) + (f" pts{len(pts)}" if not lathe else " lathe")
