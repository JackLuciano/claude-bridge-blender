"""Live smoke test against a running Blender GUI with the addon enabled.
    python tests/live_smoke.py
Exercises viewport-only features (view, look) through the real MCP server and
saves the captured images next to this file for manual inspection.
"""

import base64
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "_out")
os.makedirs(OUT, exist_ok=True)

p = subprocess.Popen([sys.executable, os.path.join(ROOT, "mcp_server.py")], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, text=True)
i = 0
fails = 0


def call(name, args):
    global i
    i += 1
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                              "params": {"name": name, "arguments": args}}) + "\n")
    p.stdin.flush()
    return json.loads(p.stdout.readline())["result"]


def step(label, name, args, save=None):
    global fails
    r = call(name, args)
    texts = [c["text"] for c in r["content"] if c["type"] == "text"]
    imgs = [c for c in r["content"] if c["type"] == "image"]
    status = "FAIL" if r["isError"] else "PASS"
    fails += r["isError"]
    extra = ""
    if save and imgs:
        raw = base64.b64decode(imgs[0]["data"])
        with open(os.path.join(OUT, save), "wb") as f:
            f.write(raw)
        extra = f" [{save} {len(raw) // 1024}KB]"
    elif save:
        status, fails = "FAIL", fails + 1
    print(f"{status} {label}{extra}: {' | '.join(texts)[:300]}")


step("build scene", "bl", {"ops": [
    {"op": "new"},
    {"op": "add", "type": "plane", "name": "Floor", "size": 10, "mat": {"name": "Floor", "color": "#808080", "rough": 0.9}},
    {"op": "add", "type": "monkey", "name": "Suzanne", "loc": [0, 0, 1], "smooth": True,
     "mat": {"name": "Gold", "color": "#ffc040", "metal": 1, "rough": 0.25}},
    {"op": "mod", "obj": "Suzanne", "type": "subsurf", "props": {"levels": 2}},
    {"op": "add", "type": "area", "name": "Key", "loc": [3, -3, 4], "energy": 800},
    {"op": "aim", "obj": "Key", "at": "Suzanne"},
    {"op": "cam", "loc": [4, -5, 3], "at": [0, 0, 1], "lens": 50},
]})
step("view front + frame", "bl", {"ops": [{"op": "view", "preset": "front", "frame": "Suzanne", "shading": "material"}]})
step("look front", "look", {"max": 384}, save="1_front.jpg")
step("look orbit", "look", {"orbit": [45, -20], "zoom": 0.8, "max": 384}, save="2_orbit.jpg")
step("look camera", "look", {"cam": True, "max": 384}, save="3_camera.jpg")
step("edit-mode bop", "bl", {"ops": [{"op": "bop", "id": "mesh.subdivide", "obj": "Floor", "edit": True, "args": {"number_cuts": 2}},
                                      {"op": "info", "obj": "Floor"}]})
step("undo", "bl", {"ops": [{"op": "undo"}, {"op": "info", "obj": "Floor"}]})

if "--chat" in sys.argv:
    # Real round trip through the in-Blender chat: Blender -> claude CLI -> MCP -> Blender.
    import time
    call("bl", {"ops": [{"op": "new"}, {"op": "py", "code":
        "import claude_bridge.chat as c; c.reset(); "
        "c.send(\"Add a red cube named ChatCube at location 0,0,2 with a bevel modifier. Don't look, just confirm.\")"}]})
    deadline = time.time() + 240
    hist = ""
    while time.time() < deadline:
        r = call("bl", {"ops": [{"op": "py", "code":
            "import claude_bridge.chat as c; out = str(c.state['busy']) + '|' + ' || '.join(f'{a}:{b}' for a, b in c.state['history'])"}]})
        busy, _, hist = r["content"][0]["text"][3:].partition("|")
        if busy == "False":
            break
        time.sleep(2)
    print("chat history:", hist[:500])
    step("chat built ChatCube", "bl", {"ops": [{"op": "info", "obj": "ChatCube"}]})
    r = call("bl", {"ops": [{"op": "info", "obj": "ChatCube"}]})
    if "bevel" not in r["content"][0]["text"] or "mat:" not in r["content"][0]["text"]:
        fails += 1
        print("FAIL chat: bevel or material missing")
    if "ToolSearch" in hist:
        fails += 1
        print("FAIL chat: tools were deferred (extra round trip)")
    r = call("bl", {"ops": [{"op": "py", "code": "out = round(bpy.data.objects['ChatCube'].active_material.diffuse_color[0], 2)"}]})
    red = r["content"][0]["text"]
    print(("PASS" if red == "py 1" else "FAIL") + f" ChatCube is red ({red})")
    fails += red != "py 1"

    # Screenshot attachment: Claude must answer from the image alone (tools forbidden by the prompt).
    call("bl", {"ops": [{"op": "view", "frame": "ChatCube", "shading": "solid"}, {"op": "py", "code":
        "import claude_bridge.chat as c; c.capture_viewport(); "
        "c.send('Without using any tools: what color is the object in the attached image? Answer with one word.')"}]})
    while time.time() < deadline + 120:
        r = call("bl", {"ops": [{"op": "py", "code":
            "import claude_bridge.chat as c; out = str(c.state['busy']) + '|' + (c.state['history'][-1][1] if c.state['history'] else '')"}]})
        busy, _, last = r["content"][0]["text"][3:].partition("|")
        if busy == "False":
            break
        time.sleep(2)
    print(("PASS" if "red" in last.lower() else "FAIL") + f" screenshot understood: {last!r}")
    fails += "red" not in last.lower()
    r = call("bl", {"ops": [{"op": "py", "code": "import claude_bridge.chat as c; out = len(c.list_chats())"}]})
    print("chats saved:", r["content"][0]["text"])

if os.name == "nt":
    # Clipboard paste: put a PNG on the Windows clipboard, then attach it like Ctrl+V does.
    img = os.path.join(OUT, "3_camera.jpg")
    subprocess.run(["powershell", "-NoProfile", "-STA", "-Command",
                    "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
                    f"[System.Windows.Forms.Clipboard]::SetImage([System.Drawing.Image]::FromFile('{img}'))"], check=True)
    r = call("bl", {"ops": [{"op": "py", "code":
        "import claude_bridge.chat as c, os; c.state['attachments'] = []; p = c.paste_image(); "
        "out = f'{len(c.state[\"attachments\"])}:{os.path.getsize(p) > 1000}'; c.state['attachments'] = []"}]})
    ok = r["content"][0]["text"] == "py 1:True"
    fails += not ok
    print(("PASS" if ok else "FAIL") + f" clipboard paste: {r['content'][0]['text']}")

p.stdin.close()
print("\nFAILED" if fails else "\nALL OK")
sys.exit(1 if fails else 0)
