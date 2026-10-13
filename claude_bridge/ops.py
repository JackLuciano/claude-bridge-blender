"""Op implementations. Each op takes a dict and returns one short result line.

Design goal: minimal tokens in both directions. Object refs accept a name,
"sel" (all selected) or "active". Angles are degrees. Colors accept "#rrggbb"
(sRGB) or [r, g, b(, a)] linear floats.
"""

import math
import os
import tempfile
import traceback

import bpy
import mathutils

OPS = {}


def op(name):
    def deco(fn):
        OPS[name] = fn
        return fn
    return deco


# ---------------------------------------------------------------- helpers

def _r(v, n=3):
    return round(float(v), n)


def _vec(v):
    return "(" + ",".join(f"{_r(x, 2):g}" for x in v) + ")"


def objs(ref):
    """Resolve an object reference into a list of objects."""
    if ref in (None, "active"):
        o = bpy.context.view_layer.objects.active
        if not o:
            raise ValueError("no active object")
        return [o]
    if ref == "sel":
        sel = list(bpy.context.selected_objects)
        if not sel:
            raise ValueError("nothing selected")
        return sel
    names = ref if isinstance(ref, list) else [ref]
    out = []
    for n in names:
        o = bpy.data.objects.get(n)
        if not o:
            raise ValueError(f"no object '{n}'")
        out.append(o)
    return out


def obj(ref):
    return objs(ref)[0]


def color(c):
    if isinstance(c, str):
        h = c.lstrip("#")
        rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        lin = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in rgb]
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return (*lin, a)
    c = list(c)
    return tuple(c + [1.0] * (4 - len(c)))


def rad(v):
    return [math.radians(x) for x in v]


def view3d():
    """Return (window, area, region, space) of the largest 3D viewport."""
    best = None
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                size = area.width * area.height
                if not best or size > best[0]:
                    region = next(r for r in area.regions if r.type == "WINDOW")
                    best = (size, win, area, region, area.spaces.active)
    if not best:
        raise ValueError("no 3D viewport open")
    return best[1:]


def resolve_path(path):
    """Resolve a dotted data path like 'scene.render.fps' or 'objects.Cube.location'."""
    root, _, rest = path.partition(".")
    if root == "scene":
        cur = bpy.context.scene
    elif root in ("objects", "materials", "meshes", "cameras", "lights", "worlds", "collections", "node_groups", "images"):
        name, _, rest = rest.partition(".")
        cur = getattr(bpy.data, root)[name]
    elif root == "obj":
        cur = bpy.context.view_layer.objects.active
    else:
        cur = bpy.data.objects.get(root)
        if cur is None:
            raise ValueError(f"bad path root '{root}'")
    parts = rest.split(".") if rest else []
    return cur, parts


def _walk(cur, parts):
    for p in parts:
        if p.endswith("]") and "[" in p:
            attr, key = p[:-1].split("[", 1)
            cur = getattr(cur, attr) if attr else cur
            key = key.strip("'\"")
            cur = cur[int(key)] if key.lstrip("-").isdigit() else cur[key]
        else:
            cur = getattr(cur, p)
    return cur


def set_path(path, value):
    cur, parts = resolve_path(path)
    holder = _walk(cur, parts[:-1])
    setattr(holder, parts[-1], value)


def fmt(v):
    if isinstance(v, float):
        return f"{_r(v, 4):g}"
    if hasattr(v, "__len__") and not isinstance(v, str) and len(v) <= 16:
        try:
            return _vec(v)
        except TypeError:
            pass
    if hasattr(v, "name"):
        return v.name
    return str(v)


def summary(o):
    s = f"{o.name}[{o.type.lower()}] @{_vec(o.location)}"
    if any(abs(x) > 1e-4 for x in o.rotation_euler):
        s += f" rot{_vec(math.degrees(x) for x in o.rotation_euler)}"
    if o.type == "MESH":
        s += f" dim{_vec(o.dimensions)}"
    mats = [m.name for m in getattr(o.data, "materials", []) if m] if o.data else []
    if mats:
        s += " mat:" + ",".join(mats)
    if o.modifiers:
        s += " mod:" + ",".join(m.type.lower() for m in o.modifiers)
    if o.parent:
        s += f" ^{o.parent.name}"
    if o.hide_get():
        s += " hidden"
    return s


# ---------------------------------------------------------------- scene / info

@op("scene")
def op_scene(a):
    """Compact list of objects. a: filter (substring), type."""
    f, t = a.get("filter"), a.get("type")
    sc = bpy.context.scene
    act = bpy.context.view_layer.objects.active
    lines = [f"scene {sc.name} frame {sc.frame_current} engine {sc.render.engine} active {act.name if act else '-'}"]
    for o in sc.objects:
        if f and f.lower() not in o.name.lower():
            continue
        if t and o.type.lower() != t.lower():
            continue
        lines.append(("*" if o.select_get() else " ") + summary(o))
    return "\n".join(lines)


@op("info")
def op_info(a):
    o = obj(a.get("obj"))
    lines = [summary(o), f"scale{_vec(o.scale)}"]
    if o.type == "MESH":
        m = o.data
        lines.append(f"v{len(m.vertices)} e{len(m.edges)} f{len(m.polygons)}")
    for md in o.modifiers:
        props = [f"{p.identifier}={fmt(getattr(md, p.identifier))}" for p in md.bl_rna.properties
                 if not p.is_readonly and p.identifier not in ("name", "show_expanded") and p.type in ("INT", "FLOAT", "BOOLEAN", "ENUM")][:8]
        lines.append(f"mod {md.name}: " + " ".join(props))
    if o.type in ("LIGHT", "CAMERA"):
        d = o.data
        keys = ("type", "energy", "color", "size", "spot_size") if o.type == "LIGHT" else ("type", "lens", "clip_start", "clip_end", "dof.use_dof")
        lines.append(" ".join(f"{k}={fmt(_walk(d, k.split('.')))}" for k in keys if hasattr(d, k.split('.')[0])))
    return "\n".join(lines)


@op("get")
def op_get(a):
    """Read any data path: scene.render.fps, objects.Cube.location, Cube.data.energy."""
    cur, parts = resolve_path(a["path"])
    return f"{a['path']}={fmt(_walk(cur, parts))}"


@op("prop")
def op_prop(a):
    """Set any data path. a: path, value."""
    v = a["value"]
    if isinstance(v, str) and v.startswith("#") and len(v) in (7, 9):
        v = color(v)[:3] if "color" in a["path"] and "world" not in a["path"] else color(v)
    set_path(a["path"], v)
    return f"ok {a['path']}"


# ---------------------------------------------------------------- objects

_PRIMS = {
    "cube": ("mesh.primitive_cube_add", "size"),
    "sphere": ("mesh.primitive_uv_sphere_add", "radius"),
    "ico": ("mesh.primitive_ico_sphere_add", "radius"),
    "cylinder": ("mesh.primitive_cylinder_add", "radius"),
    "cone": ("mesh.primitive_cone_add", "radius1"),
    "plane": ("mesh.primitive_plane_add", "size"),
    "circle": ("mesh.primitive_circle_add", "radius"),
    "torus": ("mesh.primitive_torus_add", None),
    "monkey": ("mesh.primitive_monkey_add", "size"),
    "grid": ("mesh.primitive_grid_add", "size"),
    "empty": ("object.empty_add", "radius"),
    "text": ("object.text_add", "radius"),
    "curve": ("curve.primitive_bezier_curve_add", "radius"),
    "camera": ("object.camera_add", None),
}


def _apply_xform(o, a):
    if "loc" in a:
        o.location = a["loc"]
    if "rot" in a:
        o.rotation_euler = rad(a["rot"])
    if "scale" in a:
        s = a["scale"]
        o.scale = (s, s, s) if isinstance(s, (int, float)) else s
    if "dims" in a:
        o.dimensions = a["dims"]


@op("add")
def op_add(a):
    """a: type (cube|sphere|ico|cylinder|cone|plane|circle|torus|monkey|grid|empty|text|curve|camera|
    point|sun|spot|area), name, loc, rot, scale, dims, size, mat, plus 'args' for the raw operator."""
    t = a.get("type", "cube")
    extra = dict(a.get("args", {}))
    if t in ("point", "sun", "spot", "area"):
        bpy.ops.object.light_add(type=t.upper(), location=a.get("loc", (0, 0, 0)))
    else:
        if t not in _PRIMS:
            raise ValueError(f"unknown type '{t}'")
        idn, size_kw = _PRIMS[t]
        if size_kw and "size" in a:
            extra[size_kw] = a["size"]
        cat, fn = idn.split(".")
        getattr(getattr(bpy.ops, cat), fn)(location=a.get("loc", (0, 0, 0)), **extra)
    o = bpy.context.active_object
    if a.get("name"):
        o.name = a["name"]
        if o.data:
            o.data.name = a["name"]
    _apply_xform(o, a)
    if t == "text" and a.get("body"):
        o.data.body = a["body"]
    if o.type == "LIGHT":
        if "energy" in a:
            o.data.energy = a["energy"]
        if "color" in a:
            o.data.color = color(a["color"])[:3]
    if "mat" in a:
        m = a["mat"]
        if isinstance(m, (list, tuple)) or (isinstance(m, str) and m.startswith("#")):
            m = {"color": m}  # "mat": "#ff0000" means a color, not a material name
        elif isinstance(m, str):
            m = {"name": m}
        if "color" in a and "color" not in m:
            m = {**m, "color": a["color"]}
        op_mat({"obj": o.name, **m})
    elif "color" in a and o.type != "LIGHT" and hasattr(o.data, "materials"):
        # Shorthand: a color on a non-light becomes a material, instead of being silently ignored.
        op_mat({"obj": o.name, "color": a["color"]})
    if a.get("smooth") and o.type == "MESH":
        for p in o.data.polygons:
            p.use_smooth = True
    return "+ " + summary(o)


@op("set")
def op_set(a):
    """Absolute transform / attrs. a: obj, loc, rot, scale, dims, name, hide, parent, smooth, props{attr:val}."""
    out = []
    for o in objs(a.get("obj")):
        _apply_xform(o, a)
        if "hide" in a:
            o.hide_set(bool(a["hide"]))
            o.hide_render = bool(a["hide"])
        if "parent" in a:
            p = bpy.data.objects.get(a["parent"]) if a["parent"] else None
            mw = o.matrix_world.copy()
            o.parent = p
            o.matrix_world = mw
        if "smooth" in a and o.type == "MESH":
            for poly in o.data.polygons:
                poly.use_smooth = bool(a["smooth"])
        for k, v in a.get("props", {}).items():
            holder = _walk(o, k.split(".")[:-1])
            setattr(holder, k.split(".")[-1], color(v)[:len(getattr(holder, k.split(".")[-1]))] if isinstance(v, str) and v.startswith("#") else v)
        if "name" in a:
            o.name = a["name"]
        out.append(summary(o))
    return "\n".join(out)


@op("move")
def op_move(a):
    """Relative transform. a: obj, by (loc delta), rot (deg delta), scale (multiplier)."""
    out = []
    for o in objs(a.get("obj")):
        if "by" in a:
            o.location += mathutils.Vector(a["by"])
        if "rot" in a:
            r = rad(a["rot"])
            o.rotation_euler = [o.rotation_euler[i] + r[i] for i in range(3)]
        if "scale" in a:
            s = a["scale"]
            s = (s, s, s) if isinstance(s, (int, float)) else s
            o.scale = [o.scale[i] * s[i] for i in range(3)]
        out.append(summary(o))
    return "\n".join(out)


@op("del")
def op_del(a):
    names = []
    for o in objs(a.get("obj")):
        names.append(o.name)
        bpy.data.objects.remove(o, do_unlink=True)
    return "- " + ",".join(names)


@op("dup")
def op_dup(a):
    """a: obj, n (count), by (offset per copy), linked (share mesh), name."""
    src = obj(a.get("obj"))
    off = mathutils.Vector(a.get("by", (0, 0, 0)))
    out = []
    for i in range(int(a.get("n", 1))):
        c = src.copy()
        if src.data and not a.get("linked"):
            c.data = src.data.copy()
        for col in src.users_collection:
            col.objects.link(c)
        c.location = src.location + off * (i + 1)
        if a.get("name"):
            c.name = f"{a['name']}{'' if a.get('n', 1) == 1 else '.' + str(i + 1).zfill(3)}"
        out.append(c.name)
    return "+ " + ",".join(out)


@op("select")
def op_select(a):
    """a: obj (ref or list, or 'none'/'all'), active, add (bool)."""
    ref = a.get("obj")
    if not a.get("add"):
        for o in bpy.context.selected_objects:
            o.select_set(False)
    if ref == "all":
        for o in bpy.context.view_layer.objects:
            o.select_set(True)
    elif ref and ref != "none":
        for o in objs(ref):
            o.select_set(True)
            bpy.context.view_layer.objects.active = o
    if a.get("active"):
        bpy.context.view_layer.objects.active = obj(a["active"])
    return "sel " + ",".join(o.name for o in bpy.context.selected_objects)


@op("join")
def op_join(a):
    targets = objs(a.get("obj", "sel"))
    op_select({"obj": [o.name for o in targets]})
    bpy.context.view_layer.objects.active = targets[0]
    bpy.ops.object.join()
    return "= " + summary(bpy.context.active_object)


# ---------------------------------------------------------------- materials / modifiers

@op("mat")
def op_mat(a):
    """a: obj, name, color, metal, rough, emit (color), emit_strength, alpha, transmission, ior, slot.
    Reuses an existing material when 'name' exists and no other props are given."""
    if isinstance(a.get("name"), str) and a["name"].startswith("#") and "color" not in a:
        a = {**a, "color": a["name"], "name": None}
    name = a.get("name") or f"{obj(a.get('obj')).name}_mat"
    m = bpy.data.materials.get(name)
    if not m:
        m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = m.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    inp = bsdf.inputs
    mapping = {
        "color": ("Base Color", color), "metal": ("Metallic", float), "rough": ("Roughness", float),
        "alpha": ("Alpha", float), "transmission": ("Transmission Weight", float), "ior": ("IOR", float),
        "emit": ("Emission Color", color), "emit_strength": ("Emission Strength", float),
        "spec": ("Specular IOR Level", float), "coat": ("Coat Weight", float),
    }
    for k, (sock, conv) in mapping.items():
        if k in a:
            s = inp.get(sock)
            if s is not None:
                s.default_value = conv(a[k])
    if "color" in a:
        m.diffuse_color = color(a["color"])
    if "emit" in a and "emit_strength" not in a:
        inp["Emission Strength"].default_value = 1.0
    if a.get("alpha", 1) < 1:
        try:
            m.surface_render_method = "BLENDED"
        except (AttributeError, TypeError):
            pass
    if a.get("obj") is not None:
        for o in objs(a["obj"]):
            if not hasattr(o.data, "materials"):
                continue
            slot = a.get("slot", 0)
            if len(o.data.materials) > slot:
                o.data.materials[slot] = m
            else:
                o.data.materials.append(m)
    return f"mat {m.name}"


_MOD_ALIASES = {"bevel": "BEVEL", "subsurf": "SUBSURF", "subd": "SUBSURF", "array": "ARRAY", "mirror": "MIRROR",
                "solidify": "SOLIDIFY", "bool": "BOOLEAN", "boolean": "BOOLEAN", "decimate": "DECIMATE",
                "weld": "WELD", "wireframe": "WIREFRAME", "screw": "SCREW", "displace": "DISPLACE",
                "remesh": "REMESH", "triangulate": "TRIANGULATE", "smooth": "SMOOTH", "curve": "CURVE",
                "nodes": "NODES", "shrinkwrap": "SHRINKWRAP", "skin": "SKIN", "lattice": "LATTICE"}


@op("mod")
def op_mod(a):
    """Add or edit a modifier. a: obj, type, name, props{...} (object refs given by name), apply (bool)."""
    out = []
    for o in objs(a.get("obj")):
        md = o.modifiers.get(a["name"]) if a.get("name") else None
        if md is None:
            t = _MOD_ALIASES.get(a["type"].lower(), a["type"].upper())
            md = o.modifiers.new(a.get("name") or a["type"], t)
        for k, v in a.get("props", {}).items():
            cur = getattr(md, k)
            if isinstance(cur, bpy.types.ID) or (cur is None and isinstance(v, str) and bpy.data.objects.get(v)):
                v = bpy.data.objects.get(v)
            elif k.endswith("angle") or k == "angle_limit":
                v = math.radians(v)
            setattr(md, k, v)
        if a.get("apply"):
            with bpy.context.temp_override(object=o, active_object=o):
                bpy.ops.object.modifier_apply(modifier=md.name)
            out.append(f"applied {a['type']} on {o.name}")
        else:
            out.append(f"mod {md.name}[{md.type.lower()}] on {o.name}")
    return "\n".join(out)


@op("apply")
def op_apply(a):
    """Apply modifier(s) or transforms. a: obj, mod (name or 'all'), xform ('loc','rot','scale','all')."""
    out = []
    for o in objs(a.get("obj")):
        with bpy.context.temp_override(object=o, active_object=o, selected_editable_objects=[o], selected_objects=[o]):
            if a.get("mod"):
                names = [m.name for m in o.modifiers] if a["mod"] == "all" else [a["mod"]]
                for n in names:
                    bpy.ops.object.modifier_apply(modifier=n)
            if a.get("xform"):
                x = a["xform"]
                bpy.ops.object.transform_apply(location=x in ("loc", "all"), rotation=x in ("rot", "all"), scale=x in ("scale", "all"))
        out.append(summary(o))
    return "\n".join(out)


# ---------------------------------------------------------------- camera / view

@op("aim")
def op_aim(a):
    """Point an object (camera/light) at a target. a: obj, at (obj name or [x,y,z])."""
    o = obj(a.get("obj"))
    at = a["at"]
    target = bpy.data.objects[at].matrix_world.translation if isinstance(at, str) else mathutils.Vector(at)
    d = target - o.matrix_world.translation
    o.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()
    return summary(o)


@op("cam")
def op_cam(a):
    """Create/update the scene camera. a: name, loc, at, lens, active (default true)."""
    sc = bpy.context.scene
    name = a.get("name", "Camera")
    o = bpy.data.objects.get(name)
    if not o:
        data = bpy.data.cameras.new(name)
        o = bpy.data.objects.new(name, data)
        sc.collection.objects.link(o)
    if "loc" in a:
        o.location = a["loc"]
    if "lens" in a:
        o.data.lens = a["lens"]
    if "at" in a:
        bpy.context.view_layer.update()
        op_aim({"obj": o.name, "at": a["at"]})
    if a.get("active", True):
        sc.camera = o
    return summary(o) + f" lens{_r(o.data.lens, 1)}"


_VIEWS = {"front": "FRONT", "back": "BACK", "left": "LEFT", "right": "RIGHT", "top": "TOP", "bottom": "BOTTOM"}


@op("view")
def op_view(a):
    """Drive the user's viewport. a: preset (front|back|left|right|top|bottom|camera|persp|ortho),
    orbit [yaw,pitch] deg, zoom (factor, >1 = closer), frame ('sel'|'all'|obj name),
    shading (solid|material|rendered|wireframe), dist, at [x,y,z]."""
    win, area, region, space = view3d()
    r3d = space.region_3d
    with bpy.context.temp_override(window=win, area=area, region=region):
        p = a.get("preset")
        if p in _VIEWS:
            bpy.ops.view3d.view_axis(type=_VIEWS[p])
        elif p == "camera":
            if r3d.view_perspective != "CAMERA":
                bpy.ops.view3d.view_camera()
        elif p == "persp":
            r3d.view_perspective = "PERSP"
        elif p == "ortho":
            r3d.view_perspective = "ORTHO"
        f = a.get("frame")
        if f == "all":
            bpy.ops.view3d.view_all()
        elif f:
            if f != "sel":
                op_select({"obj": f})
            bpy.ops.view3d.view_selected()
    if "orbit" in a:
        if r3d.view_perspective == "CAMERA":
            r3d.view_perspective = "PERSP"
        yaw, pitch = (a["orbit"] + [0])[:2] if isinstance(a["orbit"], list) else (a["orbit"], 0)
        q = r3d.view_rotation
        q = mathutils.Quaternion((0, 0, 1), math.radians(yaw)) @ q
        right = q @ mathutils.Vector((1, 0, 0))
        r3d.view_rotation = mathutils.Quaternion(right, math.radians(pitch)) @ q
    if "at" in a:
        r3d.view_location = a["at"]
    if "dist" in a:
        r3d.view_distance = a["dist"]
    if "zoom" in a:
        r3d.view_distance /= a["zoom"]
    if "shading" in a:
        space.shading.type = a["shading"].upper()
    area.tag_redraw()
    e = r3d.view_rotation.to_euler()
    return f"view {r3d.view_perspective.lower()} rot{_vec(math.degrees(x) for x in e)} dist{_r(r3d.view_distance, 2)} at{_vec(r3d.view_location)}"


# ---------------------------------------------------------------- generic escape hatches

@op("bop")
def op_bop(a):
    """Call any bpy.ops operator. a: id ('mesh.subdivide'), args{}, ctx ('view3d' to run inside the viewport),
    obj (make active first), edit (bool: run in edit mode, select all first)."""
    if a.get("obj"):
        op_select({"obj": a["obj"]})
    cat, fn = a["id"].split(".")
    fnc = getattr(getattr(bpy.ops, cat), fn)
    kw = a.get("args", {})
    entered = False
    if a.get("edit"):
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        entered = True
    try:
        if a.get("ctx") == "view3d" or a.get("edit"):
            win, area, region, _ = view3d()
            with bpy.context.temp_override(window=win, area=area, region=region):
                res = fnc(**kw)
        else:
            res = fnc(**kw)
    finally:
        if entered:
            bpy.ops.object.mode_set(mode="OBJECT")
    return f"{a['id']} {','.join(res)}"


@op("py")
def op_py(a):
    """Last-resort Python. a: code. Assign to `out` to return a value."""
    ns = {"bpy": bpy, "mathutils": mathutils, "math": math, "out": None}
    exec(a["code"], ns)
    return "py " + (fmt(ns["out"]) if ns["out"] is not None else "ok")


# ---------------------------------------------------------------- files / render / history

@op("undo")
def op_undo(a):
    for _ in range(int(a.get("n", 1))):
        bpy.ops.ed.undo()
    return "undone"


@op("redo")
def op_redo(a):
    for _ in range(int(a.get("n", 1))):
        bpy.ops.ed.redo()
    return "redone"


@op("save")
def op_save(a):
    if a.get("path"):
        bpy.ops.wm.save_as_mainfile(filepath=a["path"])
    else:
        bpy.ops.wm.save_mainfile()
    return f"saved {bpy.data.filepath}"


@op("open")
def op_open(a):
    bpy.ops.wm.open_mainfile(filepath=a["path"])
    return f"opened {a['path']}"


@op("new")
def op_new(a):
    """Empty scene: deletes all objects (keeps file). a: keep (list of names)."""
    keep = set(a.get("keep", []))
    for o in list(bpy.context.scene.objects):
        if o.name not in keep:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.outliner.orphans_purge(do_recursive=True)
    return "scene cleared"


_IMPORT = {".obj": "wm.obj_import", ".fbx": "import_scene.fbx", ".glb": "import_scene.gltf", ".gltf": "import_scene.gltf",
           ".stl": "wm.stl_import", ".ply": "wm.ply_import", ".usd": "wm.usd_import", ".usdz": "wm.usd_import", ".abc": "wm.alembic_import"}
_EXPORT = {".obj": "wm.obj_export", ".fbx": "export_scene.fbx", ".glb": "export_scene.gltf", ".gltf": "export_scene.gltf",
           ".stl": "wm.stl_export", ".ply": "wm.ply_export", ".usd": "wm.usd_export", ".abc": "wm.alembic_export"}


def _file_op(table, a):
    ext = os.path.splitext(a["path"])[1].lower()
    if ext not in table:
        raise ValueError(f"unsupported ext {ext}")
    cat, fn = table[ext].split(".")
    getattr(getattr(bpy.ops, cat), fn)(filepath=a["path"], **a.get("args", {}))


@op("import")
def op_import(a):
    before = set(bpy.data.objects)
    _file_op(_IMPORT, a)
    new = [o.name for o in bpy.data.objects if o not in before]
    return f"imported {len(new)}: " + ",".join(new[:20])


@op("export")
def op_export(a):
    _file_op(_EXPORT, a)
    return f"exported {a['path']}"


@op("render")
def op_render(a):
    """a: path (default temp png), res [w,h], pct, samples, engine. Returns the file path."""
    sc = bpy.context.scene
    r = sc.render
    if "engine" in a:
        try:
            r.engine = a["engine"].upper()
        except TypeError as e:
            raise ValueError(str(e))
    if "res" in a:
        r.resolution_x, r.resolution_y = a["res"]
    if "pct" in a:
        r.resolution_percentage = a["pct"]
    if "samples" in a:
        if r.engine == "CYCLES":
            sc.cycles.samples = a["samples"]
        elif hasattr(sc, "eevee"):
            sc.eevee.taa_render_samples = a["samples"]
    path = a.get("path") or os.path.join(tempfile.gettempdir(), "claude_render.png")
    r.filepath = path
    bpy.ops.render.render(write_still=True)
    return f"rendered {path}"


# ---------------------------------------------------------------- look (image feedback)

def look(a):
    """Capture the viewport (or camera via 'cam': true) downscaled to max px. Returns a file path."""
    if any(k in a for k in ("preset", "orbit", "zoom", "frame", "shading", "dist", "at")):
        op_view(a)
    size = int(a.get("max", 512))
    tmp = tempfile.gettempdir()
    raw = os.path.join(tmp, "claude_look_raw.png")
    out = os.path.join(tmp, "claude_look.jpg")
    win, area, region, space = view3d()
    # OpenGL render of the viewport (or camera): no UI chrome, splash or overlays in the shot.
    sc = bpy.context.scene
    r = sc.render
    old = (r.resolution_x, r.resolution_y, r.resolution_percentage, r.filepath)
    ratio = r.resolution_x / r.resolution_y if a.get("cam") else region.width / max(1, region.height)
    r.resolution_x, r.resolution_y = (size, int(size / ratio)) if ratio >= 1 else (int(size * ratio), size)
    r.resolution_percentage = 100
    r.filepath = raw
    try:
        with bpy.context.temp_override(window=win, area=area, region=region):
            bpy.ops.render.opengl(write_still=True, view_context=not a.get("cam"))
    finally:
        r.resolution_x, r.resolution_y, r.resolution_percentage, r.filepath = old
    img = bpy.data.images.load(raw, check_existing=False)
    try:
        w, h = img.size
        k = min(1.0, size / max(w, h))
        if k < 1.0:
            img.scale(max(1, int(w * k)), max(1, int(h * k)))
        img.filepath_raw = out
        img.file_format = "JPEG"
        bpy.context.scene.render.image_settings.quality = 80
        img.save()
    finally:
        bpy.data.images.remove(img)
    return {"ok": True, "image": out, "text": op_view({})}


# ---------------------------------------------------------------- batch runner

def run_batch(batch):
    results, ok = [], True
    for i, a in enumerate(batch):
        if "op" not in a and len(a) == 1:
            # Accept the {"add": {...}} shorthand some models produce.
            (k, v), = a.items()
            if k in OPS and isinstance(v, dict):
                a = {"op": k, **v}
        name = a.get("op")
        fn = OPS.get(name)
        if not fn:
            results.append(f"{i}:ERR unknown op '{name}' (ops: {','.join(sorted(OPS))})")
            ok = False
            break
        try:
            results.append(fn(a))
        except Exception as e:
            tb = traceback.extract_tb(e.__traceback__)[-1]
            results.append(f"{i}:{name} ERR {type(e).__name__}: {e} (line {tb.lineno})")
            ok = False
            break  # stop on first error; earlier ops stay applied
    return results, ok
