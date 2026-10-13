"""Run one larger build task through the in-Blender chat with each model.
    python tests/live_models.py [opus sonnet haiku]
Needs a running Blender GUI with the addon. Uses real Claude requests on your subscription.
Saves a camera capture per model to tests/_out/model_<name>.jpg.
"""

import base64
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "_out")
os.makedirs(OUT, exist_ok=True)

TASK = ("Build a small cozy living room scene: a floor and two walls meeting in a corner, a sofa made of a few "
        "boxes, a low coffee table with four legs, a floor lamp with a warm point light, a rug, and a camera "
        "looking at the room. Use sensible real-world sizes in meters and distinct materials. "
        "Check your result with one look, fix anything wrong, then reply briefly.")

p = subprocess.Popen([sys.executable, os.path.join(ROOT, "mcp_server.py")], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, text=True)
n = [0]


def call(name, args):
    n[0] += 1
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n[0], "method": "tools/call",
                              "params": {"name": name, "arguments": args}}) + "\n")
    p.stdin.flush()
    return json.loads(p.stdout.readline())["result"]


def py(code):
    return call("bl", {"ops": [{"op": "py", "code": code}]})["content"][0]["text"][3:]


for model in sys.argv[1:] or ["opus", "sonnet", "haiku"]:
    call("bl", {"ops": [{"op": "new"}]})
    t0 = time.time()
    py(f"import claude_bridge.chat as c; c.new_chat(); c.send({TASK!r}, {model!r})")
    while time.time() - t0 < 900:
        time.sleep(3)
        if py("import claude_bridge.chat as c; out = c.state['busy']") == "False":
            break
    dt = time.time() - t0
    hist = json.loads(py("import claude_bridge.chat as c, json; out = json.dumps(c.state['history'])"))
    tools = [t for r, t in hist if r == "tool"]
    errors = [t for r, t in hist if r == "error"]
    reply = next((t for r, t in reversed(hist) if r == "claude"), "")
    scene = call("bl", {"ops": [{"op": "scene"}]})["content"][0]["text"]
    objs = scene.count("\n")
    r = call("look", {"cam": True, "max": 512, "shading": "material"})
    img = next((c for c in r["content"] if c["type"] == "image"), None)
    if img:
        with open(os.path.join(OUT, f"model_{model}.jpg"), "wb") as f:
            f.write(base64.b64decode(img["data"]))
    print(f"=== {model}: {dt:.0f}s, {len(tools)} tool calls, {len(errors)} errors, {objs} objects")
    print("tools:", " | ".join(tools)[:400])
    if errors:
        print("errors:", " | ".join(errors)[:400])
    print("reply:", reply[:300])
    print(scene[:900])
    print()

p.stdin.close()
