"""Game-asset audit, preparation (UVs, LODs, collision) and engine export presets."""

import os
import re

import bmesh
import bpy
import mathutils

from .ops import CREATED, _roots, _tree, objs, op, op_select

ENGINES = ("unity", "unreal", "godot")


def _tris(o):
    dg = bpy.context.evaluated_depsgraph_get()
    eo = o.evaluated_get(dg)
    me = eo.to_mesh()
    try:
        me.calc_loop_triangles()
        return len(me.loop_triangles)
    finally:
        eo.to_mesh_clear()


def audit(scope="all", budget=20000):
    """Lines about what would trip up a game engine, with a fix hint each."""
    lines = []
    meshes = [x for r in _roots(scope) for x in _tree(r) if x.type == "MESH"]
    total = 0
    for o in meshes:
        t = _tris(o)
        total += t
        me = o.data
        issues = []
        if not me.uv_layers:
            issues.append("no UVs")
        rot_scale = any(abs(v) > 1e-4 for v in o.rotation_euler) or any(abs(s - 1) > 1e-4 for s in o.scale)
        if rot_scale:
            issues.append("unapplied rotation/scale")
        ngons = sum(1 for p in me.polygons if len(p.vertices) > 4)
        if ngons:
            issues.append(f"{ngons} n-gons")
        bm = bmesh.new()
        bm.from_mesh(me)
        nm = sum(1 for e in bm.edges if not e.is_manifold)
        bm.free()
        if nm:
            issues.append(f"{nm} non-manifold edges")
        if re.search(r"\s|\.\d{3}$", o.name):
            issues.append("name has spaces or .001")
        if any(m.type in ("SUBSURF", "BEVEL") and m.show_render for m in o.modifiers):
            issues.append("unapplied subsurf/bevel")
        if issues:
            lines.append(f"game {o.name} {t} tris: {', '.join(issues)}")
    head = f"game total {total} tris in {len(meshes)} meshes (budget {budget})"
    if total > budget:
        head += f" OVER by {total - budget} -> gameprep{{lods}} or mod decimate"
    hint = " -> gameprep{obj,engine} fixes UVs/transforms/names" if lines else ""
    return [head + hint] + lines


def _box_uv(me, scale=1.0):
    """UVs by box projection from each face's dominant axis (works headless, no edit-mode operators)."""
    bm = bmesh.new()
    bm.from_mesh(me)
    uv = bm.loops.layers.uv.verify()
    for f in bm.faces:
        n = f.normal
        ax = max(range(3), key=lambda i: abs(n[i]))
        u, v = [(1, 2), (0, 2), (0, 1)][ax]
        for lp in f.loops:
            lp[uv].uv = (lp.vert.co[u] * scale, lp.vert.co[v] * scale)
    bm.to_mesh(me)
    bm.free()


def _apply_rot_scale(o):
    if o.data.users > 1:
        return False
    mw = o.matrix_world.copy()
    loc, rot, sca = mw.decompose()
    basis = mathutils.Matrix.LocRotScale(None, rot, sca)
    o.data.transform(basis)
    for ch in o.children:
        ch.matrix_parent_inverse = basis @ ch.matrix_parent_inverse
    o.matrix_world = mathutils.Matrix.Translation(loc)
    return True


_COLLISION = {"unreal": "UCX_{n}_00", "godot": "{n}-convcolonly", "unity": "{n}_Collider"}


@op("gameprep")
def op_gameprep(a):
    """Get meshes game-ready. a: obj, engine (unity|unreal|godot), uv (box-project meshes without UVs, default
    true), apply (rotation/scale, default true), lods (n: decimated copies _LOD1.._LODn at 50%, 25%...),
    collision (convex hull named the engine's way), names (replace spaces/.001, default true)."""
    eng = a.get("engine", "unity")
    if eng not in ENGINES:
        raise ValueError(f"engine must be one of {','.join(ENGINES)}")
    out = []
    for o in [x for x in objs(a.get("obj")) if x.type == "MESH"]:
        done = []
        if a.get("names", True):
            clean = re.sub(r"\.(\d{3})$", r"_\1", re.sub(r"\s+", "_", o.name))
            if clean != o.name and clean not in bpy.data.objects:
                o.name = clean
                done.append("name")
        if a.get("apply", True) and _apply_rot_scale(o):
            done.append("transforms")
        if a.get("uv", True) and not o.data.uv_layers:
            _box_uv(o.data)
            done.append("uv")
        for k in range(1, int(a.get("lods", 0)) + 1):
            name = f"{o.name}_LOD{k}"
            old = bpy.data.objects.get(name)
            if old:
                bpy.data.objects.remove(old)
            dg = bpy.context.evaluated_depsgraph_get()
            me = bpy.data.meshes.new_from_object(o.evaluated_get(dg))
            lod = bpy.data.objects.new(name, me)
            for c in o.users_collection:
                c.objects.link(lod)
            lod.matrix_world = o.matrix_world.copy()
            md = lod.modifiers.new("LOD", "DECIMATE")
            md.ratio = 0.5 ** k
            dg = bpy.context.evaluated_depsgraph_get()
            dec = bpy.data.meshes.new_from_object(lod.evaluated_get(dg))
            lod.modifiers.clear()
            old_me = lod.data
            lod.data = dec
            bpy.data.meshes.remove(old_me)
            dec.name = name
            lod.hide_set(True)
            CREATED.add(name)
            done.append(f"LOD{k} {len(dec.polygons)}f")
        if a.get("collision"):
            name = _COLLISION[eng].format(n=o.name)
            old = bpy.data.objects.get(name)
            if old:
                bpy.data.objects.remove(old)
            bm = bmesh.new()
            bm.from_mesh(o.data)
            hull = bmesh.ops.convex_hull(bm, input=bm.verts)
            bmesh.ops.delete(bm, geom=list(set(hull["geom_interior"]) | set(hull["geom_unused"])), context="VERTS")
            me = bpy.data.meshes.new(name)
            bm.to_mesh(me)
            bm.free()
            col = bpy.data.objects.new(name, me)
            for c in o.users_collection:
                c.objects.link(col)
            col.matrix_world = o.matrix_world.copy()
            col.display_type = "WIRE"
            col.hide_render = True
            CREATED.add(name)
            done.append(f"collision {name}")
        out.append(f"{o.name}: {', '.join(done) or 'already ok'}")
    return "\n".join(out) if out else "no meshes in obj"


_PRESETS = {
    "unity": ("fbx", dict(apply_scale_options="FBX_SCALE_ALL", axis_forward="-Z", axis_up="Y", bake_space_transform=True,
                          mesh_smooth_type="FACE", add_leaf_bones=False, use_mesh_modifiers=True)),
    "unreal": ("fbx", dict(apply_scale_options="FBX_SCALE_NONE", axis_forward="-Y", axis_up="Z", mesh_smooth_type="FACE",
                           add_leaf_bones=False, use_mesh_modifiers=True, use_tspace=True)),
    "godot": ("glb", dict(export_format="GLB", export_yup=True, export_apply=True)),
}


def export_preset(a):
    """export{path, preset: unity|unreal|godot, obj, per: 'object'} -> engine-ready FBX/GLB files."""
    eng = a["preset"]
    if eng not in _PRESETS:
        raise ValueError(f"preset must be one of {','.join(_PRESETS)}")
    ext, kw = _PRESETS[eng]
    targets = objs(a["obj"]) if a.get("obj") else [o for o in bpy.context.scene.objects if o.parent is None
                                                     and o.type in ("MESH", "EMPTY", "ARMATURE") and o.visible_get()]
    path = bpy.path.abspath(a["path"])
    written = []
    groups = [[o] for o in targets] if a.get("per") == "object" else [targets]
    for grp in groups:
        sel = [x.name for o in grp for x in _tree(o)]
        op_select({"obj": sel})
        if a.get("per") == "object":
            os.makedirs(path, exist_ok=True)
            fp = os.path.join(path, f"{grp[0].name}.{ext}")
        else:
            fp = path if path.lower().endswith("." + ext) else os.path.splitext(path)[0] + "." + ext
            os.makedirs(os.path.dirname(fp) or ".", exist_ok=True)
        if ext == "fbx":
            bpy.ops.export_scene.fbx(filepath=fp, use_selection=True, **kw)
        else:
            bpy.ops.export_scene.gltf(filepath=fp, use_selection=True, **kw)
        written.append(os.path.basename(fp))
    return f"exported {eng}: " + ", ".join(written[:10]) + (f" +{len(written) - 10}" if len(written) > 10 else "")
