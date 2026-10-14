"""room op: walls with door and window openings from a floor plan, plus floor, ceiling, frames and glass."""

import math

import bmesh
import bpy
import mathutils

from .build import move_to_collection
from .ops import CREATED, _check_free, op, op_add, op_join, op_mat


def _box(name, center, size, angle, mat, col):
    op_add({"type": "cube", "name": name, "dims": list(size), "loc": list(center), "rot": [0, 0, math.degrees(angle)],
            "mat": mat, "col": col})
    return name


@op("room")
def op_room(a):
    """Room from a floor plan. a: pts [[x,y],...] (corners, in order; closed automatically), h (wall height, 2.6),
    t (wall thickness, 0.12), openings [{wall: i (segment pts[i]->pts[i+1]), at (m from that corner to the opening's
    near edge), w, h, sill (0 = door)}], name ('Room'), floor (true), ceiling (false), frames (true),
    wall_mat, floor_mat (names or #hex), walls (indices to build, e.g. [0,1] for an open corner; default all)."""
    pts = [mathutils.Vector((p[0], p[1], 0)) for p in a.get("pts") or []]
    if len(pts) < 3:
        raise ValueError("room needs pts with at least 3 corners [[x,y],...]")
    name = a.get("name", "Room")
    H, T = float(a.get("h", 2.6)), float(a.get("t", 0.12))
    col = name
    wall_mat = a.get("wall_mat", {"name": f"{name}_Wall", "color": "#e6e1d8", "rough": 0.9})
    floor_mat = a.get("floor_mat", {"name": f"{name}_Floor", "color": "#8a6a4a", "rough": 0.6})
    frame_mat = {"name": f"{name}_Frame", "color": "#f2f0ea", "rough": 0.5}
    glass_mat = {"name": f"{name}_Glass", "color": "#ffffff", "transmission": 1, "rough": 0.02, "ior": 1.45}
    for nm in [f"{name}_Wall_{i + 1}" for i in range(len(pts))] + [f"{name}_Floor"]:
        _check_free(nm)
    by_wall = {}
    for o in a.get("openings") or []:
        by_wall.setdefault(int(o["wall"]), []).append(o)
    made_mats = set()

    def mat(spec):
        # First use creates the material; later uses refer to it by name so it's shared.
        if isinstance(spec, dict) and spec.get("name") in made_mats:
            return spec["name"]
        if isinstance(spec, dict) and spec.get("name"):
            made_mats.add(spec["name"])
        return spec
    n_open = 0
    walls = a.get("walls")
    for i in range(len(pts)):
        if walls is not None and i not in walls:
            continue  # a cut-away: only the listed walls (e.g. [0, 1] for a corner you can see into)
        p0, p1 = pts[i], pts[(i + 1) % len(pts)]
        seg = p1 - p0
        L = seg.length
        if L < 1e-6:
            continue
        d = seg / L
        ang = math.atan2(d.y, d.x)
        # Extend each wall by half the thickness at both ends so corners close without gaps.
        start, end = -T / 2, L + T / 2
        holes = sorted(by_wall.get(i, []), key=lambda o: o["at"])
        pieces = []
        cur = start
        for o in holes:
            o0, w = float(o["at"]), float(o.get("w", 0.9))
            oh, sill = float(o.get("h", 2.1 if not o.get("sill") else 1.2)), float(o.get("sill", 0))
            if o0 < 0 or o0 + w > L:
                raise ValueError(f"opening on wall {i} doesn't fit (wall is {L:.2f} m)")
            if o0 - cur > 1e-4:
                pieces.append((cur, o0, 0, H))
            if sill > 1e-4:
                pieces.append((o0, o0 + w, 0, sill))
            if sill + oh < H - 1e-4:
                pieces.append((o0, o0 + w, sill + oh, H))
            cur = o0 + w
            n_open += 1
            mid = p0 + d * (o0 + w / 2)
            if a.get("frames", True):
                fw = 0.05
                fn = f"{name}_Frame_{i + 1}_{n_open}"
                parts = [_box(f"{fn}_L", mid - d * (w / 2 - fw / 2) + mathutils.Vector((0, 0, sill + oh / 2)), (fw, T + 0.02, oh), ang, mat(frame_mat), col),
                         _box(f"{fn}_R", mid + d * (w / 2 - fw / 2) + mathutils.Vector((0, 0, sill + oh / 2)), (fw, T + 0.02, oh), ang, mat(frame_mat), col),
                         _box(f"{fn}_T", mid + mathutils.Vector((0, 0, sill + oh - fw / 2)), (w, T + 0.02, fw), ang, mat(frame_mat), col)]
                if sill > 1e-4:
                    parts.append(_box(f"{fn}_B", mid + mathutils.Vector((0, 0, sill + fw / 2)), (w, T + 0.04, fw), ang, mat(frame_mat), col))
                op_join({"obj": parts})
                bpy.context.view_layer.objects.active.name = fn
            if sill > 1e-4:
                _box(f"{name}_Glass_{i + 1}_{n_open}", mid + mathutils.Vector((0, 0, sill + oh / 2)), (w - 0.1, 0.012, oh - 0.1),
                     ang, mat(glass_mat), col)
        if end - cur > 1e-4:
            pieces.append((cur, end, 0, H))
        names = []
        for k, (s0, s1, z0, z1) in enumerate(pieces):
            c = p0 + d * ((s0 + s1) / 2) + mathutils.Vector((0, 0, (z0 + z1) / 2))
            names.append(_box(f"{name}_W{i + 1}_{k}", c, (s1 - s0, T, z1 - z0), ang, mat(wall_mat), col))
        if len(names) > 1:
            op_join({"obj": names})
        bpy.data.objects[names[0]].name = f"{name}_Wall_{i + 1}"
    if a.get("floor", True) or a.get("ceiling"):
        for kind, z in (("Floor", 0.0), ("Ceiling", H)):
            if (kind == "Floor" and not a.get("floor", True)) or (kind == "Ceiling" and not a.get("ceiling")):
                continue
            me = bpy.data.meshes.new(f"{name}_{kind}")
            bm = bmesh.new()
            vs = [bm.verts.new((p.x, p.y, z)) for p in pts]
            f = bm.faces.new(vs if kind == "Floor" else list(reversed(vs)))
            bmesh.ops.recalc_face_normals(bm, faces=[f])
            if (f.normal.z < 0) == (kind == "Floor"):
                f.normal_flip()
            bm.to_mesh(me)
            bm.free()
            o = bpy.data.objects.new(f"{name}_{kind}", me)
            bpy.context.scene.collection.objects.link(o)
            move_to_collection(o, col)
            CREATED.add(o.name)
            spec = mat(floor_mat) if kind == "Floor" else {"name": f"{name}_Ceiling", "color": "#f4f2ee", "rough": 0.9}
            if isinstance(spec, dict):
                op_mat({"obj": o.name, **spec})
            elif str(spec).startswith("#"):
                op_mat({"obj": o.name, "color": spec})
            else:
                op_mat({"obj": o.name, "name": spec})
    xs, ys = [p.x for p in pts], [p.y for p in pts]
    return (f"room {name}: {len(pts)} walls h{H:g} t{T:g}, {n_open} openings, "
            f"footprint {max(xs) - min(xs):.2f}x{max(ys) - min(ys):.2f}m")
