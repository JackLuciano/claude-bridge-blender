"""Same build task as live_models.py, but through a normal Claude Code session
(user config, `blender-bridge` registered via install.py), one per model.
    python tests/live_models_cc.py [opus sonnet haiku]
Saves a camera capture per model to tests/_out/cc_<name>.jpg.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "_out")
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.makedirs(OUT, exist_ok=True)

TASK = ("Using the blender-bridge tools: clear the scene, then build a small cozy living room scene: a floor and "
        "two walls meeting in a corner, a sofa made of a few boxes, a low coffee table with four legs, a floor "
        "lamp with a warm point light, a rug, and a camera looking at the room. Use sensible real-world sizes in "
        "meters and distinct materials. Check your result with one look, fix anything wrong, then reply briefly.")

mcp = subprocess.Popen([sys.executable, os.path.join(ROOT, "mcp_server.py")], stdin=subprocess.PIPE,
                       stdout=subprocess.PIPE, text=True)
n = [0]


def call(name, args):
    n[0] += 1
    mcp.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n[0], "method": "tools/call",
                                "params": {"name": name, "arguments": args}}) + "\n")
    mcp.stdin.flush()
    return json.loads(mcp.stdout.readline())["result"]


claude = shutil.which("claude")
for model in sys.argv[1:] or ["opus", "sonnet", "haiku"]:
    call("bl", {"ops": [{"op": "new"}]})
    t0 = time.time()
    r = subprocess.run([claude, "-p", TASK, "--model", model, "--output-format", "stream-json", "--verbose",
                        "--allowedTools", "mcp__blender-bridge__bl", "mcp__blender-bridge__look"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=tempfile.gettempdir(), timeout=900)
    dt = time.time() - t0
    tools, errors, reply, ctx = [], 0, "", 0
    for line in r.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "assistant":
            for b in ev["message"]["content"]:
                if b.get("type") == "tool_use":
                    inp = b.get("input", {})
                    tools.append(b["name"].split("__")[-1] + (":" + ",".join(o.get("op", "?") for o in inp.get("ops", []))
                                                             if "ops" in inp else ""))
        elif ev.get("type") == "user":
            errors += sum(1 for b in ev["message"]["content"] if isinstance(b, dict) and b.get("is_error"))
        elif ev.get("type") == "result":
            reply = ev.get("result", "")
            u = ev.get("usage", {})
            ctx = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
            out_tok = u.get("output_tokens", 0)
    scene = call("bl", {"ops": [{"op": "scene"}]})["content"][0]["text"]
    img = next((c for c in call("look", {"cam": True, "max": 512, "shading": "material"})["content"]
                if c["type"] == "image"), None)
    if img:
        with open(os.path.join(OUT, f"cc_{model}.jpg"), "wb") as f:
            f.write(base64.b64decode(img["data"]))
    print(f"=== CC {model}: {dt:.0f}s, {len(tools)} tool calls, {errors} tool errors, "
          f"{scene.count(chr(10))} objects, input tokens (all turns) {ctx}, output {out_tok}")
    print("tools:", " | ".join(tools)[:500])
    print("reply:", reply[:300])
    print()

mcp.stdin.close()
