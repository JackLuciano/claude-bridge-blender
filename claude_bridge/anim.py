"""Animation ops: keyframes, motion presets and constraints."""

import math

import bpy

from .ops import _coerce, _get_rot, _set_attr, _set_rot, _wbox, obj, objs, op, op_add, op_cam, v3

_INTERP = {"linear": "LINEAR", "bezier": "BEZIER", "smooth": "BEZIER", "constant": "CONSTANT", "step": "CONSTANT",
           "elastic": "ELASTIC", "bounce": "BOUNCE", "back": "BACK", "sine": "SINE", "expo": "EXPO"}
_EASE = {"in": "EASE_IN", "out": "EASE_OUT", "in_out": "EASE_IN_OUT", "auto": "AUTO"}


def fcurves(idb):
    """F-curves of an ID's action, on both layered (4.4+) and legacy actions."""
    ad = idb.animation_data
    if not ad or not ad.action:
        return []
    act = ad.action
    try:
        from bpy_extras.anim_utils import action_get_channelbag_for_slot
        cb = action_get_channelbag_for_slot(act, ad.action_slot)
        return list(cb.fcurves) if cb else []
    except (ImportError, AttributeError):
        return list(act.fcurves)


def _style(idb, path, frame, interp, ease):
    if not interp and not ease:
        return
    for fc in fcurves(idb):
        if fc.data_path != path:
            continue
        for kp in fc.keyframe_points:
            if abs(kp.co.x - frame) < 0.5:
                if interp:
                    kp.interpolation = _INTERP.get(interp.lower(), interp.upper())
                if ease:
                    kp.easing = _EASE.get(ease.lower(), ease.upper())


def _insert(idb, path, frame, interp=None, ease=None):
    idb.keyframe_insert(data_path=path, frame=frame)
    _style(idb, path, frame, interp, ease)


def _rot_path(o):
    return {"QUATERNION": "rotation_quaternion", "AXIS_ANGLE": "rotation_axis_angle"}.get(o.rotation_mode, "rotation_euler")


@op("key")
def op_key(a):
    """Set values and keyframe them. a: obj, f (frame, default current), loc, rot (deg), scale, props{attr: val}
    (object or its data, e.g. energy, lens), interp (linear|bezier|constant|elastic|bounce|back|sine|expo),
    ease (in|out|in_out)."""
    f = a.get("f", bpy.context.scene.frame_current)
    interp, ease = a.get("interp"), a.get("ease")
    out = []
    for o in objs(a.get("obj")):
        keyed = []
        if "loc" in a:
            o.location = v3(a, "loc")
            _insert(o, "location", f, interp, ease)
            keyed.append("loc")
        if "rot" in a:
            _set_rot(o, v3(a, "rot"))
            _insert(o, _rot_path(o), f, interp, ease)
            keyed.append("rot")
        if "scale" in a:
            o.scale = v3(a, "scale")
            _insert(o, "scale", f, interp, ease)
            keyed.append("scale")
        for k, v in a.get("props", {}).items():
            _set_attr(o, k, v)
            holder = o.data if (o.data is not None and hasattr(o.data, k.split(".")[0])
                                and (o.type in ("LIGHT", "CAMERA") or not hasattr(o, k.split(".")[0]))) else o
            _insert(holder, k, f, interp, ease)
            keyed.append(k)
        if not keyed:
            # No values given: key the current transform.
            for p in ("location", _rot_path(o), "scale"):
                _insert(o, p, f, interp, ease)
            keyed.append("loc/rot/scale")
        out.append(f"key {o.name} f{f}: {','.join(keyed)}")
    return "\n".join(out)


def _frame_range(a):
    sc = bpy.context.scene
    if "start" in a:
        sc.frame_start = int(a["start"])
    if "end" in a:
        sc.frame_end = int(a["end"])
    if "fps" in a:
        sc.render.fps = int(a["fps"])
    return sc.frame_start, sc.frame_end


def _cycle(idb, path):
    for fc in fcurves(idb):
        if fc.data_path == path and not any(m.type == "CYCLES" for m in fc.modifiers):
            fc.modifiers.new("CYCLES")


@op("anim")
def op_anim(a):
    """Frame range and motion presets. a: start, end, fps, preset (turntable|spin|pop_in|bounce|float), obj,
    turns (default 1), stagger (frames between objects for pop_in), height (bounce/float, m)."""
    s, e = _frame_range(a)
    p = a.get("preset")
    if not p:
        return f"frames {s}-{e} @ {bpy.context.scene.render.fps}fps"
    turns = float(a.get("turns", 1))
    if p == "turntable":
        targets = objs(a["obj"]) if a.get("obj") else [o for o in bpy.context.scene.objects
                                                         if o.type == "MESH" and o.visible_get()]
        if not targets:
            raise ValueError("turntable needs objects to circle")
        lo, hi = _wbox(targets)
        c = (lo + hi) / 2
        op_add({"type": "empty", "name": "Turntable", "loc": list(c)})
        pivot = bpy.data.objects["Turntable"]
        sc = bpy.context.scene
        cam = sc.camera
        if cam is None:
            r = max((hi - lo).length, 0.5) * 1.4
            op_cam({"name": "Camera", "loc": [c.x, c.y - r, c.z + r * 0.45], "at": list(c), "lens": 50})
            cam = sc.camera
        bpy.context.view_layer.update()
        mw = cam.matrix_world.copy()
        cam.parent = pivot
        cam.matrix_world = mw
        cam.matrix_parent_inverse = pivot.matrix_world.inverted()
        pivot.animation_data_clear()
        _set_rot(pivot, [0, 0, 0])
        _insert(pivot, "rotation_euler", s, "linear")
        _set_rot(pivot, [0, 0, 360 * turns])
        _insert(pivot, "rotation_euler", e + 1, "linear")  # e+1 so the loop doesn't repeat a frame
        return f"turntable around {len(targets)} objects, camera {cam.name}, frames {s}-{e}"
    out = []
    stagger = int(a.get("stagger", 3))
    h = float(a.get("height", 0.5))
    for i, o in enumerate(objs(a.get("obj"))):
        if p == "spin":
            r0 = [math.degrees(x) for x in _get_rot(o)]
            o.rotation_mode = "XYZ" if o.rotation_mode in ("QUATERNION", "AXIS_ANGLE") else o.rotation_mode
            _set_rot(o, r0)
            _insert(o, "rotation_euler", s, "linear")
            _set_rot(o, [r0[0], r0[1], r0[2] + 360 * turns])
            _insert(o, "rotation_euler", e + 1, "linear")
            _cycle(o, "rotation_euler")
        elif p == "pop_in":
            f0 = s + i * stagger
            full = tuple(o.scale)
            o.scale = (0, 0, 0)
            _insert(o, "scale", f0, "bezier")
            o.scale = tuple(x * 1.12 for x in full)
            _insert(o, "scale", f0 + 8, "bezier", "out")
            o.scale = full
            _insert(o, "scale", f0 + 12, "bezier")
        elif p in ("bounce", "float"):
            z0 = o.location.z
            mid = (s + e) // 2
            _insert(o, "location", s, "bezier")
            o.location.z = z0 + h
            _insert(o, "location", mid, "sine" if p == "float" else "bezier", "out" if p == "bounce" else "in_out")
            o.location.z = z0
            _insert(o, "location", e, "bounce" if p == "bounce" else "sine", "out" if p == "bounce" else "in_out")
            _cycle(o, "location")
        else:
            raise ValueError("preset must be turntable|spin|pop_in|bounce|float")
        out.append(o.name)
    return f"{p} on {','.join(out)} frames {s}-{e}"


_CON = {"track": "TRACK_TO", "track_to": "TRACK_TO", "damped_track": "DAMPED_TRACK", "follow_path": "FOLLOW_PATH",
        "copy_loc": "COPY_LOCATION", "copy_rot": "COPY_ROTATION", "copy_scale": "COPY_SCALE",
        "copy_transforms": "COPY_TRANSFORMS", "child_of": "CHILD_OF", "limit_dist": "LIMIT_DISTANCE",
        "limit_loc": "LIMIT_LOCATION", "limit_rot": "LIMIT_ROTATION", "floor": "FLOOR", "clamp_to": "CLAMP_TO",
        "shrinkwrap": "SHRINKWRAP", "pivot": "PIVOT", "stretch_to": "STRETCH_TO", "look_at": "TRACK_TO"}


@op("con")
def op_con(a):
    """Add or edit a constraint. a: obj, type (track|damped_track|follow_path|copy_loc|copy_rot|child_of|limit_dist|
    floor|...), target (object name), name, props{}, animate (follow_path: move along the path over the frame range)."""
    out = []
    for o in objs(a.get("obj")):
        c = o.constraints.get(a["name"]) if a.get("name") else None
        if c is None:
            if not a.get("type"):
                raise ValueError(f"no constraint '{a.get('name')}' on {o.name}; give type to add one")
            t = _CON.get(a["type"].lower(), a["type"].upper())
            c = o.constraints.new(t)
            if a.get("name"):
                c.name = a["name"]
            if t == "TRACK_TO":
                c.track_axis, c.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"  # cameras and lights look down -Z
        if a.get("target"):
            c.target = obj(a["target"])
        for k, v in a.get("props", {}).items():
            if not hasattr(c, k):
                raise ValueError(f"{c.type.lower()} constraint has no '{k}'")
            setattr(c, k, _coerce(c, k, v))
        if c.type == "FOLLOW_PATH" and a.get("animate", True) and c.target and c.target.type == "CURVE":
            s, e = _frame_range(a)
            cu = c.target.data
            cu.use_path = True
            cu.path_duration = e - s + 1
            c.use_curve_follow = bool(a.get("follow", True))
            cu.eval_time = 0
            _insert(cu, "eval_time", s, "linear")
            cu.eval_time = cu.path_duration
            _insert(cu, "eval_time", e, "linear")
        out.append(f"con {c.name}[{c.type.lower()}] on {o.name}" + (f" -> {c.target.name}" if getattr(c, "target", None) else ""))
    return "\n".join(out)
