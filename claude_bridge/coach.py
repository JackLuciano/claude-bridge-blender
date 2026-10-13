"""Coach mode lessons: steps with goals that are checked locally (zero tokens). Only when a check fails can the
user ask Claude for a hint.

Lesson file (bundled lessons/*.json or <config>/claude_bridge/lessons/*.json):
    {"title": "...", "steps": [{"text": "what to do", "checks": [{...}, ...]}]}
Checks: {"exists": "Name"} | {"count": {"type": "MESH", "min": 5}} | {"mod": {"obj": "Name", "type": "BEVEL"}} |
        {"dims": {"obj": "Name", "approx": [x, y, z], "tol": 0.1}} | {"mat": "Name"} (object has any material) |
        {"on_floor": "Name"} | {"bottom": {"obj": "Name", "z": 0.72, "tol": 0.03}} | {"parent": {"obj": "Name", "to": "Parent"}} | {"mode": "EDIT_MESH"} | {"camera": true}
"""

import json
import os

import bpy

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
progress = {"lesson": None, "step": 0, "last": ""}


def lesson_dirs():
    from .chat import chats_dir
    return [os.path.join(ADDON_DIR, "lessons"), os.path.join(os.path.dirname(chats_dir()), "lessons")]


def lessons():
    out = {}
    for d in lesson_dirs():
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(d, fn), encoding="utf-8") as f:
                        data = json.load(f)
                    if data.get("steps"):
                        out[fn[:-5]] = data
                except (OSError, ValueError):
                    continue
    return out


def _find(name):
    o = bpy.data.objects.get(name)
    if o is None:  # be forgiving about names: 'cube' finds 'Cube' or 'Cube.001'
        low = name.lower()
        o = next((x for x in bpy.context.scene.objects if x.name.lower().startswith(low)), None)
    return o


def run_check(c):
    """(ok, message) for one check."""
    (kind, arg), = c.items()
    if kind == "exists":
        o = _find(arg)
        return (o is not None, f"no object named '{arg}' yet")
    if kind == "count":
        n = sum(1 for o in bpy.context.scene.objects if not arg.get("type") or o.type == arg["type"])
        return (n >= arg.get("min", 1), f"need at least {arg.get('min', 1)} {arg.get('type', 'object').lower()}s, found {n}")
    if kind == "mod":
        o = _find(arg["obj"])
        ok = o is not None and any(m.type == arg["type"].upper() for m in o.modifiers)
        return (ok, f"{arg['obj']} needs a {arg['type'].lower()} modifier")
    if kind == "dims":
        o = _find(arg["obj"])
        if o is None:
            return (False, f"no object named '{arg['obj']}'")
        tol = arg.get("tol", 0.1)
        ok = all(abs(o.dimensions[i] - arg["approx"][i]) <= tol for i in range(3))
        size = ", ".join(f"{x:.2f}" for x in o.dimensions)
        return (ok, f"{o.name} is {size} m; aim for about {arg['approx']} (±{tol})")
    if kind == "mat":
        o = _find(arg)
        return (o is not None and bool(getattr(o.data, "materials", None)) and any(o.data.materials),
                f"{arg} has no material yet")
    if kind == "on_floor":
        o = _find(arg)
        if o is None:
            return (False, f"no object named '{arg}'")
        from .ops import _wbox
        bpy.context.view_layer.update()
        lo, _ = _wbox([o])
        return (abs(lo.z) < 0.01, f"{o.name} should sit on the floor (its bottom is at {lo.z:.2f} m)")
    if kind == "bottom":
        o = _find(arg["obj"])
        if o is None:
            return (False, f"no object named '{arg['obj']}'")
        from .ops import _wbox
        bpy.context.view_layer.update()
        lo, _ = _wbox([o])
        tol = arg.get("tol", 0.03)
        return (abs(lo.z - arg["z"]) <= tol, f"{o.name}'s bottom is at {lo.z:.2f} m; it should be about {arg['z']} m")
    if kind == "parent":
        o = _find(arg["obj"])
        return (o is not None and o.parent is not None and o.parent.name.lower().startswith(arg["to"].lower()),
                f"{arg['obj']} should be parented to {arg['to']} (select it, then the parent, Ctrl+P)")
    if kind == "mode":
        return (bpy.context.mode == arg, f"switch to {arg.replace('_', ' ').lower()} (Tab)")
    if kind == "camera":
        return (bpy.context.scene.camera is not None, "the scene needs a camera (Shift+A ▸ Camera)")
    return (False, f"unknown check {kind}")


def current():
    ls = lessons()
    les = ls.get(progress["lesson"])
    if not les:
        return None, None
    i = min(progress["step"], len(les["steps"]) - 1)
    return les, les["steps"][i]


def check_step():
    """Check the current step locally. Passing advances to the next step. Returns (ok, message)."""
    les, step = current()
    if les is None:
        return False, "pick a lesson first"
    fails = [msg for ok, msg in (run_check(c) for c in step.get("checks", [])) if not ok]
    if fails:
        progress["last"] = "; ".join(fails)
        return False, progress["last"]
    progress["step"] += 1
    progress["last"] = ""
    if progress["step"] >= len(les["steps"]):
        return True, f"Lesson complete: {les['title']} 🎉"
    return True, f"Step {progress['step']} done. Next: {les['steps'][progress['step']]['text']}"


def hint_prompt():
    les, step = current()
    return (f"I'm doing the lesson '{les['title']}', step {progress['step'] + 1}: \"{step['text']}\". "
            f"The automatic check says: {progress['last'] or 'not done yet'}. Look at my scene and give me a hint "
            f"(menu path and shortcut), without doing it for me.")
