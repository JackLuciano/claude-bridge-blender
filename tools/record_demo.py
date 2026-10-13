"""Record the README demo: frames of the window while the in-Blender chat builds a scene.

    blender --enable-event-simulate --python tools/record_demo.py
    python tools/make_demo_gif.py

One real Opus request. Frames go to tools/_listing/demo/. Blender ignores real input
while recording and quits when done.
"""

import json
import os
import time

import bpy

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_listing", "demo")
os.makedirs(OUT, exist_ok=True)
PROMPT = ("Build a cozy reading nook: a wooden floor, a corner of two walls, a fabric armchair with a cushion, a small "
          "round side table with a mug, a floor lamp with a warm light, a rug and a short bookshelf with books. Bevel "
          "hard edges, use procedural textures, and finish with a camera view.")
S = {"step": 0, "frame": 0, "t": 0.0}


def chat():
    import importlib
    return importlib.import_module(next(m for m in bpy.context.preferences.addons.keys() if m.endswith("claude_bridge")) + ".chat")


def view3d():
    win = bpy.context.window_manager.windows[0]
    area = max((a for a in win.screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height)
    return win, area, next(r for r in area.regions if r.type == "UI")


def grab():
    win, area, _ = view3d()
    path = os.path.join(OUT, f"f{S['frame']:04d}.png")
    with bpy.context.temp_override(window=win):
        bpy.ops.screen.screenshot(filepath=path)
    # Per-frame crop box: the layout can change while recording.
    with open(path.replace(".png", ".json"), "w") as f:
        json.dump({"x": area.x, "y": area.y, "w": area.width, "h": area.height, "win_h": win.height,
                   "orbit": S["step"] == 4}, f)
    S["frame"] += 1


def tick():
    c = chat()
    win, area, ui = view3d()
    s = S["step"]
    if s == 0:
        prefs = bpy.context.preferences
        prefs.use_preferences_save = False
        prefs.view.ui_scale = 1.25
        win.event_simulate(type="ESC", value="PRESS", x=10, y=10)
        win.event_simulate(type="ESC", value="RELEASE", x=10, y=10)
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        c.new_chat()
        area.spaces.active.show_region_ui = True
        area.spaces.active.shading.type = "MATERIAL"
        S["step"] = 1
        return 1.0
    if s == 1:  # click down the tab strip until the Claude tab is active
        x = ui.x + ui.width - 8
        for y in range(ui.y + ui.height - 15, ui.y + 40, -12):
            if ui.active_panel_category == "Claude":
                break
            win.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x, y=y)
            win.event_simulate(type="LEFTMOUSE", value="PRESS", x=x, y=y)
            win.event_simulate(type="LEFTMOUSE", value="RELEASE", x=x, y=y)
        bpy.ops.view3d.view_all  # noqa: B018 (keep default framing)
        S["step"] = 2
        return 1.0
    if s == 2:  # show the typed prompt for a moment, then send
        wm = bpy.context.window_manager
        wm.claude_chat_input = ""
        for _ in range(3):
            grab()
        c.send(PROMPT, "opus")
        S["step"], S["t"] = 3, time.time()
        return 0.6
    if s == 3:  # record while Claude works; keep the viewport framed on the scene
        if bpy.context.scene.objects and S["frame"] % 6 == 0:
            with bpy.context.temp_override(window=win, area=area, region=next(r for r in area.regions if r.type == "WINDOW")):
                try:
                    bpy.ops.view3d.view_all(center=False)
                except RuntimeError:
                    pass
        grab()
        if not c.state["busy"] or time.time() - S["t"] > 480:
            S["step"], S["orbit"] = 4, 0
        return 0.6
    if s == 4:  # slow orbit to show the result
        r3d = area.spaces.active.region_3d
        if r3d.view_perspective == "CAMERA":
            r3d.view_perspective = "PERSP"
        import mathutils
        r3d.view_rotation = mathutils.Quaternion((0, 0, 1), 0.08) @ r3d.view_rotation
        area.tag_redraw()
        S["orbit"] += 1
        if S["orbit"] > 2:
            grab()
        if S["orbit"] > 26:
            bpy.ops.wm.quit_blender()
            return None
        return 0.25
    return 1.0


bpy.app.timers.register(tick, first_interval=3.0)
