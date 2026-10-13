"""Teach mode: the manual way to do what Claude's tool call did, generated locally (zero tokens)."""

import bpy

_HOWTO = {
    "add": "Add: Shift+A in the 3D view",
    "add.cube": "Add ▸ Mesh ▸ Cube (Shift+A)",
    "add.sphere": "Add ▸ Mesh ▸ UV Sphere (Shift+A)",
    "add.cylinder": "Add ▸ Mesh ▸ Cylinder (Shift+A)",
    "add.plane": "Add ▸ Mesh ▸ Plane (Shift+A)",
    "add.camera": "Add ▸ Camera (Shift+A)",
    "add.point": "Add ▸ Light ▸ Point (Shift+A)",
    "add.sun": "Add ▸ Light ▸ Sun (Shift+A)",
    "add.area": "Add ▸ Light ▸ Area (Shift+A)",
    "add.spot": "Add ▸ Light ▸ Spot (Shift+A)",
    "add.text": "Add ▸ Text (Shift+A), Tab to edit",
    "move": "Move G, rotate R, scale S (then X/Y/Z to lock an axis)",
    "set": "Exact values: N sidebar ▸ Item ▸ Transform",
    "del": "Delete: X or Delete",
    "dup": "Duplicate: Shift+D (linked: Alt+D)",
    "join": "Join: Ctrl+J",
    "select": "Click to select, Shift+click to add, A selects all",
    "mat": "Properties ▸ Material tab ▸ Surface",
    "tex": "Shading workspace: Shift+A ▸ Texture node into Base Color",
    "mod": "Properties ▸ Modifiers (wrench) ▸ Add Modifier",
    "mod.bevel": "Modifiers ▸ Add ▸ Generate ▸ Bevel (or Ctrl+B in Edit Mode)",
    "mod.subsurf": "Ctrl+1/2/3 adds Subdivision Surface",
    "mod.array": "Modifiers ▸ Add ▸ Generate ▸ Array",
    "mod.mirror": "Modifiers ▸ Add ▸ Generate ▸ Mirror",
    "mod.bool": "Modifiers ▸ Add ▸ Generate ▸ Boolean",
    "mod.solidify": "Modifiers ▸ Add ▸ Generate ▸ Solidify",
    "apply": "Ctrl+A ▸ Apply (transforms), or the modifier's ⌄ ▸ Apply",
    "aim": "Object Constraint ▸ Track To, or N ▸ rotation by hand",
    "cam": "Set view to camera: Numpad 0; align camera to view: Ctrl+Alt+Numpad 0",
    "view": "Numpad 1/3/7 front/side/top, Home frames all, Numpad . frames the selection",
    "world": "Properties ▸ World tab ▸ Surface (Color or Environment Texture)",
    "render": "Render ▸ Render Image (F12)",
    "key": "Hover a value and press I to insert a keyframe",
    "anim": "Timeline: set Start/End; keyframes with I",
    "con": "Properties ▸ Object Constraints tab ▸ Add Object Constraint",
    "col": "Outliner ▸ right click ▸ New Collection; M moves objects to a collection",
    "rename": "F2 renames the active object",
    "curve": "Add ▸ Curve (Shift+A); Object Data ▸ Geometry ▸ Bevel for thickness; Screw modifier for lathes",
    "fit": "S to scale, then N ▸ Dimensions; Ctrl+A ▸ Scale to apply",
    "place": "Snapping (Shift+Tab) to Face, or move along Z with G Z",
    "edit": "Tab for Edit Mode: E extrude, I inset, Ctrl+B bevel, Ctrl+R loop cut",
    "nodes": "Shading or Geometry Nodes workspace: Shift+A adds nodes, drag sockets to link",
    "img": "Shading: Shift+A ▸ Texture ▸ Image Texture (Ctrl+Shift+T with Node Wrangler for a full PBR set)",
    "import": "File ▸ Import",
    "export": "File ▸ Export",
    "save": "Ctrl+S",
    "undo": "Ctrl+Z (redo Ctrl+Shift+Z)",
}


def _key_for(op_id):
    """The user's shortcut for an operator id from their keymap, if any."""
    try:
        for km in bpy.context.window_manager.keyconfigs.user.keymaps:
            for kmi in km.keymap_items:
                if kmi.idname == op_id and kmi.active:
                    return kmi.to_string()
    except Exception:
        pass
    return ""


def hints(block):
    """One line listing the manual equivalent of each distinct op in a bl call."""
    name = block.get("name", "").split("__")[-1]
    if name != "bl":
        return ""
    seen, out = set(), []
    for a in (block.get("input") or {}).get("ops", []):
        if not isinstance(a, dict):
            continue
        o = a.get("op")
        sub = a.get("type") if o in ("add", "mod") and isinstance(a.get("type"), str) else None
        key = f"{o}.{sub.lower()}" if sub and f"{o}.{sub.lower()}" in _HOWTO else o
        if key in seen:
            continue
        seen.add(key)
        if o == "bop" and a.get("id"):
            k = _key_for(a["id"])
            out.append(f"{a['id']}" + (f" ({k})" if k else " (F3 search)"))
        elif key in _HOWTO:
            out.append(_HOWTO[key])
    return " · ".join(out[:4])
