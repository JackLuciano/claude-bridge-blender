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

        # One version everywhere (the 0.1.2 zip shipped with serverInfo 0.1.0)
        import re
        from claude_bridge import mcp_server as ms
        man = re.search(r'^version = "(.+)"', open(os.path.join(ROOT, "claude_bridge", "blender_manifest.toml")).read(), re.M)[1]
        pyp = re.search(r'^version = "(.+)"', open(os.path.join(ROOT, "pyproject.toml")).read(), re.M)[1]
        sj = json.load(open(os.path.join(ROOT, "server.json")))
        plug = json.load(open(os.path.join(ROOT, "plugin", ".claude-plugin", "plugin.json")))["version"]
        versions = {man, pyp, sj["version"], sj["packages"][0]["version"], plug, ms.__version__,
                    ".".join(map(str, claude_bridge.bl_info["version"])), r["result"]["serverInfo"]["version"]}
        check("one version everywhere", len(versions) == 1, versions)
        same = open(os.path.join(ROOT, "plugin", "skills", "blender-bridge", "SKILL.md"), "rb").read() == \
            open(os.path.join(ROOT, "claude_bridge", "skill", "SKILL.md"), "rb").read()
        check("plugin skill is a copy of the add-on skill", same)

        r = rpc("tools/list")
        names = [t["name"] for t in r["result"]["tools"]]
        check("tools/list", names == ["bl", "look"], names)
        from claude_bridge import ops
        enum = r["result"]["tools"][0]["inputSchema"]["properties"]["ops"]["items"]["properties"]["op"]["enum"]
        check("schema op enum matches ops.OPS", sorted(enum) == sorted(ops.OPS), set(enum) ^ set(ops.OPS))
        schema_chars = len(json.dumps(r["result"]["tools"]))
        # Sent with every request: ~4 chars/token, so 6000 chars is about 1.5k tokens for ~45 ops.
        check("schema is compact", schema_chars < 6000, schema_chars)

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

        # A web page can POST text/plain to localhost; the body must never run.
        import socket
        body = json.dumps({"ops": [{"op": "add", "type": "cube", "name": "Pwn"}]}) + "\n"
        for raw in (f"POST / HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: text/plain\r\n\r\n{body}",
                    "garbage line\n" + body,
                    body):  # valid JSON but no token
            with socket.create_connection(("127.0.0.1", PORT), timeout=5) as s:
                s.sendall(raw.encode())
                s.settimeout(3)
                try:
                    while s.recv(4096):
                        pass
                except OSError:
                    pass
        time.sleep(0.3)
        check("http/garbage/no-token rejected", "Pwn" not in bpy.data.objects, list(bpy.data.objects.keys()))

        # Safe mode: a risky batch waits for Approve/Deny; a denied one tells Claude not to retry.
        state["safe"] = True
        res = {}
        t = threading.Thread(target=lambda: res.update(r=rpc("tools/call", {"name": "bl", "arguments": {"ops": [
            {"op": "py", "code": "out = 1"}]}})))
        t.start()
        deadline = time.time() + 10
        while not claude_bridge.pending and time.time() < deadline:
            time.sleep(0.05)
        check("risky batch held", bool(claude_bridge.pending) and "run Python" in claude_bridge.pending[0][2][0],
              claude_bridge.pending)
        state["resolve"] = False
        t.join(10)
        txt = res.get("r", {}).get("result", {}).get("content", [{}])[-1].get("text", "")
        check("denied batch reported", "declined" in txt and "Don't retry" in txt, txt)
        state["safe"] = False
    except Exception:
        state["errors"].append(traceback.format_exc())
    finally:
        p.stdin.close()
        p.wait(timeout=10)
        state["done"] = True


def port_fallback():
    """A second server on a taken port must not share it, it must move to the next one."""
    import socket
    blocker = socket.socket()
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    blocker.bind(("127.0.0.1", PORT))
    blocker.listen(1)
    try:
        claude_bridge.start()
        moved = claude_bridge.PORT == PORT + 1
        claude_bridge.stop()
    finally:
        blocker.close()
    claude_bridge.PORT = PORT
    claude_bridge.start.base = PORT
    print(("PASS" if moved else "FAIL") + f" port fallback (got {claude_bridge.PORT if not moved else PORT + 1})")
    if moved:
        state["passed"] += 1
    else:
        state["errors"].append("port fallback")


def chat_with_fake_cli(P):
    """Full in-Blender chat round trip with a stand-in CLI: Blender -> fake claude -> MCP server -> bridge -> bpy."""
    import tempfile
    from claude_bridge import chat
    out = tempfile.mkdtemp()
    fake_dir = os.path.join(ROOT, "tests", "fake_claude")
    os.environ.update(FAKE_CLAUDE_OUT=out, FAKE_CLAUDE_PY=sys.executable, ANTHROPIC_API_KEY="sk-should-not-leak")
    os.environ.setdefault("CLAUDE_BRIDGE_CHATS", tempfile.mkdtemp())
    P.safe_mode = False
    P.send_context, P.custom_instructions, P.claude_path, P.chats_dir = True, "", "", ""
    orig = chat.find_claude
    chat.find_claude = lambda: os.path.join(fake_dir, "claude.cmd" if os.name == "nt" else "claude")
    try:
        chat.new_chat()
        chat.send("make a green cube", "opus")
        deadline = time.time() + 60
        while chat.state["busy"] and time.time() < deadline:
            claude_bridge._pump()
            time.sleep(0.01)
        chat._was_busy[0] = True  # the redraw timer saw the reply running (timers don't run headless)
        chat._redraw()
    finally:
        chat.find_claude = orig
        os.environ.pop("ANTHROPIC_API_KEY", None)
    call = json.load(open(os.path.join(out, "fake_claude_call.json")))
    hist = chat.state["history"]

    def check(name, cond, info=""):
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f": {info}"))
        if cond:
            state["passed"] += 1
        else:
            state["errors"].append(f"{name}: {info}")
    argv = call["argv"]
    check("fake cli: flags", "--system-prompt-file" in argv and argv[argv.index("--model") + 1] == "opus"
          and "--strict-mcp-config" in argv, argv)
    check("fake cli: no API key leaks", not call["env_has_api_key"])
    text = call["stdin"]["message"]["content"][-1]["text"]
    check("fake cli: context line", text.startswith("[ctx mode object") and text.endswith("make a green cube"), text)
    check("fake cli: cube built through MCP", "FakeCube" in bpy.data.objects, list(bpy.data.objects.keys()))
    check("fake cli: chat history", ("claude", "Done.") in hist and any(r == "diff" and "+1 FakeCube" in t for r, t in hist),
          hist)
    check("fake cli: usage", chat.usage_line().startswith("ctx 6.0k"), chat.usage_line())


def main():
    claude_bridge.start.base = PORT
    port_fallback()
    claude_bridge.start()
    threading.Thread(target=client, daemon=True).start()
    deadline = time.time() + 120
    class P:  # stand-in preferences: safe mode toggled by the client thread
        safe_mode = False
        port = PORT
    claude_bridge.prefs = lambda: P
    while not state["done"] and time.time() < deadline:
        P.safe_mode = state.get("safe", False)
        if "resolve" in state and claude_bridge.pending:  # main thread answers like the Deny button
            claude_bridge.resolve(claude_bridge.pending[0], state.pop("resolve"))
        claude_bridge._pump()
        time.sleep(0.01)
    if not state["done"]:
        state["errors"].append("timeout")
    try:
        chat_with_fake_cli(P)
    except Exception:
        state["errors"].append(traceback.format_exc())
    claude_bridge.stop()
    print(f"\n{state['passed']} passed, {len(state['errors'])} failed")
    for e in state["errors"]:
        print(e)
    sys.stdout.flush()
    os._exit(1 if state["errors"] else 0)


main()
