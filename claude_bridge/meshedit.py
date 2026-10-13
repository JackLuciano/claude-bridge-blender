"""edit op: selection-aware mesh editing through bmesh (no edit-mode operators, no context/poll failures)."""

import math

import bmesh
import bpy
import mathutils

from .ops import obj, op

_AXES = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1), "-z": (0, 0, -1)}


def _select_faces(bm, o, sel):
    """Faces picked by: 'all' | 'keep' (the user's edit-mode selection) | '+z' style direction |
    {normal:[x,y,z], tol:deg} | {mat:'Name'} | {above:z} | {below:z} | [face indices]. World space."""
    mw = o.matrix_world
    nmat = mw.to_3x3().inverted_safe().transposed()
    faces = list(bm.faces)
    if sel in (None, "all"):
        return faces
    if sel == "keep":
        return [f for f in faces if f.select]
    if isinstance(sel, list):
        idx = set(int(i) for i in sel)
        return [f for f in faces if f.index in idx]
    if isinstance(sel, str):
        if sel not in _AXES:
            raise ValueError(f"sel must be all|keep|{'|'.join(_AXES)}|{{normal,tol}}|{{mat}}|{{above}}|{{below}}|[indices]")
        sel = {"normal": _AXES[sel]}
    if "normal" in sel:
        want = mathutils.Vector(sel["normal"]).normalized()
        cos = math.cos(math.radians(sel.get("tol", 10)))
        return [f for f in faces if (nmat @ f.normal).normalized().dot(want) >= cos]
    if "mat" in sel:
        names = [m.name if m else None for m in o.data.materials]
        return [f for f in faces if f.material_index < len(names) and names[f.material_index] == sel["mat"]]
    if "above" in sel:
        return [f for f in faces if (mw @ f.calc_center_median()).z > sel["above"]]
    if "below" in sel:
        return [f for f in faces if (mw @ f.calc_center_median()).z < sel["below"]]
    raise ValueError("unknown sel")


def _local(o, v):
    """A world-space offset as a local one."""
    return o.matrix_world.to_3x3().inverted_safe() @ mathutils.Vector(v)


def _avg_normal(faces):
    n = mathutils.Vector()
    for f in faces:
        n += f.normal * f.calc_area()
    return n.normalized() if n.length else mathutils.Vector((0, 0, 1))


def _step(bm, o, faces, action):
    """Apply one action; returns (new face selection, short description)."""
    (name, arg), = action.items()
    if name == "extrude":
        bm.normal_update()
        normal = _avg_normal(faces)
        r = bmesh.ops.extrude_face_region(bm, geom=faces)
        verts = [e for e in r["geom"] if isinstance(e, bmesh.types.BMVert)]
        moved = set(verts)
        # The moved copies only (not the new side walls), so the next step works on the new cap.
        new_faces = [e for e in r["geom"] if isinstance(e, bmesh.types.BMFace) and all(v in moved for v in e.verts)]
        if isinstance(arg, (int, float)):
            vec = normal * float(arg)  # along the faces' normal (local; sizes are baked metres)
        else:
            vec = _local(o, arg)
        bmesh.ops.translate(bm, vec=vec, verts=verts)
        bmesh.ops.delete(bm, geom=[f for f in faces if f.is_valid], context="FACES")
        return new_faces, f"extrude {len(faces)}f"
    if name == "inset":
        th = arg if isinstance(arg, (int, float)) else arg.get("w", 0.05)
        depth = 0 if isinstance(arg, (int, float)) else arg.get("depth", 0)
        bmesh.ops.inset_region(bm, faces=faces, thickness=float(th), depth=float(depth), use_even_offset=True)
        return faces, f"inset {len(faces)}f"
    if name == "bevel":
        a = arg if isinstance(arg, dict) else {"w": arg}
        edges = list({e for f in faces for e in f.edges}) if len(faces) < len(bm.faces) else list(bm.edges)
        if len(faces) < len(bm.faces):
            edges = [e for e in edges if any(lf not in faces for lf in e.link_faces) or len(e.link_faces) < 2] or edges
        bmesh.ops.bevel(bm, geom=edges, offset=float(a.get("w", 0.02)), segments=int(a.get("seg", 3)),
                        profile=0.5, affect="EDGES", clamp_overlap=True)
        return [f for f in faces if f.is_valid], f"bevel {len(edges)}e"
    if name == "cut":
        a = arg if isinstance(arg, dict) else {"axis": arg}
        ax = "xyz".index(a.get("axis", "z").lower()[-1])
        n = int(a.get("n", 1))
        co = [v.co[ax] for v in bm.verts]
        lo, hi = min(co), max(co)
        for i in range(1, n + 1):
            p = mathutils.Vector()
            p[ax] = lo + (hi - lo) * i / (n + 1)
            no = mathutils.Vector()
            no[ax] = 1
            bmesh.ops.bisect_plane(bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces), plane_co=p, plane_no=no)
        return list(bm.faces), f"cut {n}x{'xyz'[ax]}"
    if name == "bisect":
        a = arg
        p = o.matrix_world.inverted() @ mathutils.Vector(a.get("co", (0, 0, 0)))
        no = (o.matrix_world.to_3x3().transposed() @ mathutils.Vector(a.get("no", (0, 0, 1)))).normalized()
        clear = a.get("clear")
        bmesh.ops.bisect_plane(bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces), plane_co=p, plane_no=no,
                               clear_inner=clear == "inner", clear_outer=clear == "outer")
        return list(bm.faces), "bisect"
    if name == "delete":
        n = len(faces)
        bmesh.ops.delete(bm, geom=faces, context="FACES")
        return [], f"delete {n}f"
    if name == "move":
        verts = list({v for f in faces for v in f.verts})
        bmesh.ops.translate(bm, vec=_local(o, arg), verts=verts)
        return faces, f"move {len(verts)}v"
    if name == "scale":
        verts = list({v for f in faces for v in f.verts})
        c = sum((v.co for v in verts), mathutils.Vector()) / max(1, len(verts))
        s = arg if isinstance(arg, (list, tuple)) else (arg, arg, arg)
        bmesh.ops.scale(bm, vec=s, space=mathutils.Matrix.Translation(-c), verts=verts)
        return faces, f"scale {len(verts)}v"
    if name == "subdivide":
        edges = list({e for f in faces for e in f.edges})
        bmesh.ops.subdivide_edges(bm, edges=edges, cuts=int(arg), use_grid_fill=True)
        return list(bm.faces), f"subdivide x{int(arg)}"
    if name == "mat":
        names = [m.name if m else None for m in o.data.materials]
        if arg not in names:
            m = bpy.data.materials.get(arg)
            if m is None:
                raise ValueError(f"no material '{arg}'")
            o.data.materials.append(m)
            names.append(arg)
        for f in faces:
            f.material_index = names.index(arg)
        return faces, f"mat {arg} on {len(faces)}f"
    if name == "smooth":
        for f in faces:
            f.smooth = bool(arg)
        return faces, "smooth"
    raise ValueError(f"unknown edit action '{name}' (extrude|inset|bevel|cut|bisect|delete|move|scale|subdivide|mat|smooth)")


@op("edit")
def op_edit(a):
    """Mesh editing. a: obj, sel (all|keep|+x|-x|+y|-y|+z|-z|{normal,tol}|{mat}|{above}|{below}|[face ids]),
    do: [{extrude: dist | [x,y,z]}, {inset: w | {w,depth}}, {bevel: w | {w,seg}}, {cut: {axis,n}},
    {bisect: {co,no,clear}}, {delete: true}, {move: [x,y,z]}, {scale: f}, {subdivide: n}, {mat: name}, {smooth: bool}].
    Each step works on the faces the previous step produced (e.g. extrude then inset the new top)."""
    o = obj(a.get("obj"))
    if o.type != "MESH":
        raise ValueError(f"{o.name} is not a mesh")
    if o.data.users > 1:
        raise ValueError(f"{o.name} shares its mesh with other objects; dup it without linked first")
    steps = a.get("do") or []
    if isinstance(steps, dict):
        steps = [{k: v} for k, v in steps.items()]
    bm = bmesh.new()
    try:
        bm.from_mesh(o.data)
        bm.faces.ensure_lookup_table()
        faces = _select_faces(bm, o, a.get("sel", "all"))
        if not faces and steps:
            raise ValueError("sel matched no faces")
        log = [f"sel {len(faces)}f"]
        for st in steps:
            if not isinstance(st, dict) or len(st) != 1:
                raise ValueError("each step is one {action: value}")
            faces, msg = _step(bm, o, faces, st)
            faces = [f for f in faces if f.is_valid]
            bm.faces.ensure_lookup_table()
            log.append(msg)
        bm.normal_update()
        bm.to_mesh(o.data)
    finally:
        bm.free()
    o.data.update()
    return f"edit {o.name}: {', '.join(log)} -> v{len(o.data.vertices)} f{len(o.data.polygons)}"
