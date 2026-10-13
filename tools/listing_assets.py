"""Generate screenshots and a render for the Blender Extensions listing.

    blender --enable-event-simulate --python tools/listing_assets.py

Drives the real in-Blender chat (Opus, 3 requests on your subscription), captures the
window after each turn and renders the camera view. Output: tools/_listing/raw/*.png.
Real input is ignored while event simulation is on; Blender quits when done.
"""

import os
import time

import bpy

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_listing", "raw")
os.makedirs(OUT, exist_ok=True)

TURNS = [
    ("1_build", "Build a detailed cozy living room: wooden floor and two walls in a corner, a sofa with seat and back "
                "cushions, a coffee table with four legs, a floor lamp with a warm light, a rug, a bookshelf with "
                "books, a plant, and a camera looking into the room. Real-world sizes. Make it look high quality: "
                "bevel every hard edge, subdivide the soft furniture, smooth round parts, and use procedural textures "
                "(wood grain, fabric, etc.) instead of flat colours. Check that every table leg touches the floor."),
    ("2_edit", "Make the sofa deep blue velvet, add two cushions on it, and put a mug on the coffee table."),
    ("3_image", "Here's the current view. What one change would make it feel cozier? Do it."),
]

S = {"step": 0, "turn": 0, "t": time.time()}


def chat():
    import importlib
    return importlib.import_module(next(m for m in bpy.context.preferences.addons.keys() if m.endswith("claude_bridge")) + ".chat")


def view3d():
    win = bpy.context.window_manager.windows[0]
    area = max((a for a in win.screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height)
    return win, area, next(r for r in area.regions if r.type == "UI")


def click(win, x, y):
    win.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x, y=y)
    win.event_simulate(type="LEFTMOUSE", value="PRESS", x=x, y=y)
    win.event_simulate(type="LEFTMOUSE", value="RELEASE", x=x, y=y)


def open_claude_tab():
    """The active sidebar tab is read-only from Python: click down the tab strip until it is 'Claude'."""
    win, area, ui = view3d()
    area.spaces.active.show_region_ui = True
    x = ui.x + ui.width - 8
    for y in range(ui.y + ui.height - 15, ui.y + 40, -12):
        if ui.active_panel_category == "Claude":
            return True
        click(win, x, y)
    return ui.active_panel_category == "Claude"


def screenshot(name):
    path = os.path.join(OUT, name + ".png")
    win, area, ui = view3d()
    with bpy.context.temp_override(window=win):
        bpy.ops.screen.screenshot(filepath=path)
    # 3D area bounds (bottom-left origin) so the composer can crop to viewport + chat.
    import json
    with open(os.path.join(OUT, "area.json"), "w") as f:
        json.dump({"x": area.x, "y": area.y, "w": area.width, "h": area.height, "win_h": win.height}, f)
    print("ASSET", path)


def tick():
    c = chat()
    s = S["step"]
    win, area, ui = view3d()
    if s == 0:  # dismiss splash, clean scene, bigger UI for legible previews (never saved to prefs)
        prefs = bpy.context.preferences
        prefs.use_preferences_save = False
        prefs.view.ui_scale = 1.25
        win.event_simulate(type="ESC", value="PRESS", x=10, y=10)
        win.event_simulate(type="ESC", value="RELEASE", x=10, y=10)
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        c.new_chat()
        S["step"] = 1
        return 1.0
    if s == 1:  # open the Claude tab
        print("TAB", open_claude_tab(), ui.active_panel_category)
        S["step"] = 2
        return 1.0
    if s == 2:  # next turn
        if S["turn"] >= len(TURNS):
            S["step"] = 4
            return 0.5
        name, prompt = TURNS[S["turn"]]
        if name == "3_image":
            c.new_chat()  # fresh chat so the preview shows the image turn itself
            c.capture_viewport()
        c.send(prompt, "opus")
        S["step"], S["t"] = 3, time.time()
        return 2.0
    if s == 3:  # wait for the reply, then show materials and capture the window
        if c.state["busy"] and time.time() - S["t"] < 600:
            return 2.0
        name = TURNS[S["turn"]][0]
        area.spaces.active.shading.type = "MATERIAL"
        area.tag_redraw()
        S["turn"] += 1
        S["step"] = 31
        S["pending"] = name
        return 1.5  # let the UI redraw first
    if s == 31:
        screenshot(S["pending"])
        S["step"] = 2
        return 0.5
    if s == 4:  # final camera render for the featured image
        sc = bpy.context.scene
        try:
            sc.render.engine = "BLENDER_EEVEE_NEXT"
        except TypeError:
            sc.render.engine = "BLENDER_EEVEE"
        sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = 1920, 1080, 100
        if hasattr(sc, "eevee"):
            sc.eevee.taa_render_samples = 128
            for attr in ("use_raytracing", "use_shadows"):
                if hasattr(sc.eevee, attr):
                    setattr(sc.eevee, attr, True)
        sc.render.filepath = os.path.join(OUT, "render.png")
        if sc.camera:
            bpy.ops.render.render(write_still=True)
            print("ASSET", sc.render.filepath)
        with open(os.path.join(OUT, "chat.txt"), "w", encoding="utf-8") as f:
            for role, text in c.state["history"]:
                f.write(f"{role}: {text}\n")
        bpy.ops.wm.quit_blender()
        return None
    return 1.0


bpy.app.timers.register(tick, first_interval=3.0)
