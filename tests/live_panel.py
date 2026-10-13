"""Screenshot the chat panel in its main states, to check the UI draws (no Claude calls):
    blender --factory-startup --enable-event-simulate --python tests/live_panel.py
Writes tests/_out/panel_empty.png and panel_full.png, then quits. Blender ignores real input while it runs.
"""

import os
import sys

import addon_utils
import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "tests", "_out")
os.makedirs(OUT, exist_ok=True)
bpy.context.preferences.use_preferences_save = False
addon_utils.enable("claude_bridge", default_set=True)

import claude_bridge  # noqa: E402
from claude_bridge import chat, ops  # noqa: E402

claude_bridge.prefs().port = 9891  # keep clear of a Blender you're using
claude_bridge.start.base = 9891
S = {"step": 0}


def tick():
    win = bpy.context.window_manager.windows[0]
    area = max((a for a in win.screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height)
    ui = next(r for r in area.regions if r.type == "UI")
    s = S["step"]
    if s == 0:  # dismiss the splash, open the sidebar
        win.event_simulate(type="ESC", value="PRESS", x=10, y=10)
        win.event_simulate(type="ESC", value="RELEASE", x=10, y=10)
        area.spaces.active.show_region_ui = True
    elif s == 1:  # click down the tab strip until the Claude tab is active
        x = ui.x + ui.width - 8
        for y in range(ui.y + ui.height - 15, ui.y + 40, -12):
            if ui.active_panel_category == "Claude":
                break
            win.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x, y=y)
            win.event_simulate(type="LEFTMOUSE", value="PRESS", x=x, y=y)
            win.event_simulate(type="LEFTMOUSE", value="RELEASE", x=x, y=y)
    elif s == 2:
        bpy.ops.screen.screenshot(filepath=os.path.join(OUT, "panel_empty.png"))
        ops.run_batch([{"op": "add", "type": "cube", "name": "Box"}])
        chat._push("user", "make a desk")
        chat._push("claude", "Here's a **plan** for the desk.")
        chat._push("diff", "+2 Desk, Lamp · ~1 Box moved")
        chat.state["usage"] = {"ctx": 14000, "window": 1000000, "out": 1100, "calls": 3, "secs": 9}
        chat.state["plan"] = {"summary": "A simple desk", "steps": [
            {"title": "Top", "ops": [{"op": "add"}]}, {"title": "Run code", "ops": [{"op": "py", "code": "x"}]}]}
        chat.state["plan_ticks"] = [True, False]
        chat.turn.update(pushes=1, changed=["Box"])
        wm = bpy.context.window_manager
        wm.claude_chat_mode = "coach"
        wm.claude_lesson = "first_table"
    elif s == 3:
        bpy.ops.screen.screenshot(filepath=os.path.join(OUT, "panel_full.png"))
        bpy.ops.wm.quit_blender()
        return None
    S["step"] += 1
    return 1.0


bpy.app.timers.register(tick, first_interval=3.0)
