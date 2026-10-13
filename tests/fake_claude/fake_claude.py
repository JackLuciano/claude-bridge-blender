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

msg = sys.stdin.readline()
with open(os.path.join(out_dir, "fake_claude_call.json"), "w", encoding="utf-8") as f:
    json.dump({"argv": argv, "stdin": json.loads(msg), "env_has_api_key": "ANTHROPIC_API_KEY" in os.environ}, f)


def emit(ev):
    sys.stdout.write(json.dumps(ev) + "\n")
    sys.stdout.flush()


emit({"type": "system", "subtype": "init", "session_id": "fake-session"})
# Call the bridge exactly like the real CLI would: start the configured MCP server and use tools/call.
cfg = json.load(open(argv[argv.index("--mcp-config") + 1]))["mcpServers"]["blender-bridge"]
srv = subprocess.Popen([cfg["command"], *cfg["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                       env={**os.environ, **cfg.get("env", {})})
ops = [{"op": "add", "type": "cube", "name": "FakeCube", "color": "#00ff00"}]
srv.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "bl", "arguments": {"ops": ops}}}) + "\n")
srv.stdin.flush()
res = json.loads(srv.stdout.readline())["result"]
srv.stdin.close()
emit({"type": "assistant", "message": {"content": [
    {"type": "text", "text": "Adding a **green** cube."},
    {"type": "tool_use", "id": "tu1", "name": "mcp__blender-bridge__bl", "input": {"ops": ops}}]}})
emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1", "is_error": res["isError"],
                                               "content": res["content"]}]}})
emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}})
emit({"type": "result", "subtype": "success", "session_id": "fake-session", "num_turns": 2, "duration_ms": 1500,
      "usage": {"input_tokens": 10, "cache_read_input_tokens": 6000, "output_tokens": 50}})
