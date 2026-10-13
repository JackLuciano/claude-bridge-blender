"""Stand-in for the claude CLI in tests: records its argv and stdin, then plays a canned stream-json reply
that makes one real bl tool call through the bridge's MCP server (so the whole chat pipeline is exercised
without a network or an account).
"""

import json
import os
import subprocess
import sys

out_dir = os.environ.get("FAKE_CLAUDE_OUT", ".")
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("9.9.9 (Claude Code)")
    sys.exit(0)
if argv[:1] == ["--help"]:
    print("--strict-mcp-config --disable-slash-commands --input-format --system-prompt")
    sys.exit(0)
if argv[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max", "email": "x@y"}))
    sys.exit(0)

def emit(ev):
    sys.stdout.write(json.dumps(ev) + "\n")
    sys.stdout.flush()


# Call the bridge exactly like the real CLI would: start the configured MCP server and use tools/call.
cfg = json.load(open(argv[argv.index("--mcp-config") + 1]))["mcpServers"]["blender-bridge"]
srv = subprocess.Popen([cfg["command"], *cfg["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                       env={**os.environ, **cfg.get("env", {})})
emit({"type": "system", "subtype": "init", "session_id": "fake-session"})
n = 0
# One reply per stdin line, like the real CLI in stream-json mode (one-shot callers close stdin after one line).
for msg in sys.stdin:
    if not msg.strip():
        continue
    m = json.loads(msg)
    if m.get("type") == "control_request":
        emit({"type": "control_response", "response": {"subtype": "success", "request_id": m["request_id"]}})
        continue
    n += 1
    with open(os.path.join(out_dir, "fake_claude_call.json" if n == 1 else f"fake_claude_call_{n}.json"), "w",
              encoding="utf-8") as f:
        json.dump({"argv": argv, "stdin": m, "env_has_api_key": "ANTHROPIC_API_KEY" in os.environ, "pid": os.getpid()}, f)
    name = "FakeCube" if n == 1 else f"FakeCube{n}"
    ops = [{"op": "add", "type": "cube", "name": name, "color": "#00ff00", "loc": [n * 3, 0, 0]}]
    srv.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n, "method": "tools/call",
                                "params": {"name": "bl", "arguments": {"ops": ops}}}) + "\n")
    srv.stdin.flush()
    res = json.loads(srv.stdout.readline())["result"]
    emit({"type": "stream_event", "event": {"type": "message_start"}})
    emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Adding a "}}})
    emit({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Adding a **green** cube."},
        {"type": "tool_use", "id": f"tu{n}", "name": "mcp__blender-bridge__bl", "input": {"ops": ops}}]}})
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"tu{n}",
                                                   "is_error": res["isError"], "content": res["content"]}]}})
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}})
    emit({"type": "result", "subtype": "success", "session_id": "fake-session", "num_turns": 2, "duration_ms": 1500,
          "usage": {"input_tokens": 10, "cache_read_input_tokens": 6000, "output_tokens": 50}})
srv.stdin.close()
