"""End-to-end test: MCP JSON-RPC over stdio -> mcp_server.py -> TCP -> addon -> bpy.
Run inside Blender:
    blender -b --factory-startup --python tests/test_integration.py
Background mode has no event loop, so the main thread pumps the job queue itself.
"""

import json
import os
import subprocess
import sys
import threading
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bpy  # noqa: E402

import claude_bridge  # noqa: E402

PORT = 9879  # avoid clashing with a live Blender on the default port
claude_bridge.PORT = PORT

state = {"done": False, "errors": [], "passed": 0}


def client():
    py = os.environ.get("TEST_PYTHON", "python")
    env = dict(os.environ, CLAUDE_BRIDGE_PORT=str(PORT))
    p = subprocess.Popen([py, os.path.join(ROOT, "mcp_server.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         text=True, env=env)
    n = [0]

    def rpc(method, params=None):
        n[0] += 1
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n[0], "method": method, "params": params or {}}) + "\n")
        p.stdin.flush()
        return json.loads(p.stdout.readline())

    def check(name, cond, info=""):
        if cond:
            state["passed"] += 1
            print(f"PASS {name}")
        else:
            state["errors"].append(f"{name}: {info}")
            print(f"FAIL {name}: {info}")

    try:
        r = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
        check("initialize", r["result"]["serverInfo"]["name"] == "blender-bridge", r)
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        p.stdin.flush()

        r = rpc("tools/list")
        names = [t["name"] for t in r["result"]["tools"]]
        check("tools/list", names == ["bl", "look"], names)
        from claude_bridge import ops
        enum = r["result"]["tools"][0]["inputSchema"]["properties"]["ops"]["items"]["properties"]["op"]["enum"]
        check("schema op enum matches ops.OPS", sorted(enum) == sorted(ops.OPS), set(enum) ^ set(ops.OPS))
        schema_chars = len(json.dumps(r["result"]["tools"]))
        check("schema is compact", schema_chars < 3500, schema_chars)

        r = rpc("tools/call", {"name": "bl", "arguments": {"ops": [
            {"op": "new"},
            {"op": "add", "type": "cube", "name": "E2E", "loc": [0, 0, 1], "mat": {"name": "Blue", "color": "#0044ff"}},
            {"op": "mod", "obj": "E2E", "type": "bevel", "props": {"width": 0.05}},
            {"op": "scene"},
        ]}})
        text = r["result"]["content"][0]["text"]
        check("bl batch", not r["result"]["isError"] and "E2E[mesh]" in text and "mat:Blue" in text, text)
        check("response is compact", len(text) < 400, len(text))

        r = rpc("tools/call", {"name": "bl", "arguments": {"ops": [{"op": "set", "obj": "Missing"}]}})
        check("error surfaces", r["result"]["isError"] and "no object 'Missing'" in r["result"]["content"][0]["text"], r)

        r = rpc("tools/call", {"name": "nope", "arguments": {}})
        check("unknown tool", r["result"]["isError"], r)

        r = rpc("bogus/method")
        check("unknown method", r.get("error", {}).get("code") == -32601, r)
    except Exception:
        state["errors"].append(traceback.format_exc())
    finally:
        p.stdin.close()
        p.wait(timeout=10)
        state["done"] = True


def main():
    claude_bridge.start()
    threading.Thread(target=client, daemon=True).start()
    deadline = time.time() + 120
    while not state["done"] and time.time() < deadline:
        claude_bridge._pump()
        time.sleep(0.01)
    claude_bridge.stop()
    if not state["done"]:
        state["errors"].append("timeout")
    print(f"\n{state['passed']} passed, {len(state['errors'])} failed")
    for e in state["errors"]:
        print(e)
    sys.stdout.flush()
    os._exit(1 if state["errors"] else 0)


main()
