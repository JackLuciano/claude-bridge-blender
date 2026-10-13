"""Chat module tests (no network, no CLI calls). Run inside Blender:
    blender -b --factory-startup --python tests/test_chat.py
"""

import json
import os
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["CLAUDE_BRIDGE_CHATS"] = tempfile.mkdtemp(prefix="claude_chats_")

import bpy  # noqa: E402

import claude_bridge  # noqa: E402
from claude_bridge import chat  # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def t_register_roundtrip():
    claude_bridge.register()
    wm = bpy.context.window_manager
    assert hasattr(bpy.types.WindowManager, "claude_chat_input")
    assert "CLAUDE_PT_chat" in dir(bpy.types)
    assert wm.claude_chat_model == "opus"
    assert wm.claude_chat_list == "NEW"
    cmd = claude_bridge.mcp_add_command()
    assert cmd.startswith("claude mcp add -s user blender-bridge -- ") and "mcp_server.py" in cmd
    bpy.ops.claude.copy_mcp_command()
    assert wm.clipboard == cmd or bpy.app.background  # clipboard is unavailable headless
    claude_bridge.unregister()
    assert not hasattr(bpy.types.WindowManager, "claude_chat_input")


@test
def t_build_cmd():
    cmd = chat.build_cmd("claude", session="abc", model="sonnet")
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--input-format") + 1] == "stream-json"
    assert "--strict-mcp-config" in cmd and cmd[cmd.index("--resume") + 1] == "abc"
    assert "mcp__blender-bridge__bl" in cmd and "mcp__blender-bridge__look" in cmd
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert "--model" not in chat.build_cmd("claude", model="default")
    c = chat.build_cmd("claude")
    assert c[c.index("--model") + 1] == "opus", "default model must be Opus"
    cfg = json.load(open(cmd[cmd.index("--mcp-config") + 1]))
    srv = cfg["mcpServers"]["blender-bridge"]
    assert os.path.exists(srv["args"][0]) and srv["args"][0].endswith("mcp_server.py")


@test
def t_build_message_with_image():
    img = bpy.data.images.new("t", 8, 8)
    p = os.path.join(tempfile.gettempdir(), "claude_test_img.png")
    img.filepath_raw = p
    img.file_format = "PNG"
    img.save()
    msg = chat.build_message("what is this?", [p, "ignored.txt"])
    content = msg["message"]["content"]
    assert msg["type"] == "user" and len(content) == 2, content
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png"
    assert content[1] == {"type": "text", "text": "what is this?"}


@test
def t_handle_events():
    chat.new_chat()
    chat.handle_event({"type": "system", "subtype": "init", "session_id": "s1"})
    assert chat.state["session"] == "s1"
    chat.handle_event({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "On it."},
        {"type": "tool_use", "name": "mcp__blender-bridge__bl", "input": {"ops": [{"op": "add"}, {"op": "mat"}]}},
    ]}})
    chat.handle_event({"type": "user", "message": {"content": [
        {"type": "tool_result", "is_error": True, "content": [{"type": "text", "text": "0:set ERR no object"}]}]}})
    chat.handle_event({"type": "result", "session_id": "s2", "total_cost_usd": 0.01})
    h = chat.state["history"]
    assert h[0] == ("claude", "On it."), h
    assert h[1] == ("tool", "bl: add, mat"), h
    assert h[2][0] == "error" and "no object" in h[2][1], h
    assert chat.state["session"] == "s2"
    # Regression: string content crashed the worker (seen with Sonnet); errors show the ERR line.
    chat.handle_event({"type": "user", "message": {"role": "user", "content": "plain string"}})
    chat.handle_event({"type": "assistant", "message": {"content": ["stray", {"type": "text", "text": "ok"}]}})
    chat.handle_event({"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True,
                                                                "content": "mat Wood\n3:set ERR no object 'X'"}]}})
    assert chat.state["history"][-2] == ("claude", "ok"), chat.state["history"]
    assert chat.state["history"][-1] == ("error", "3:set ERR no object 'X'"), chat.state["history"]


@test
def t_persistence():
    chat.new_chat()
    for c in chat.list_chats():
        chat.delete(c["id"])
    chat._push("user", "make a table")
    chat._push("claude", "done")
    chat.state["session"] = "sess-A"
    chat.save()
    a = chat.state["chat_id"]
    chat.new_chat()
    chat._push("user", "make a chair")
    b = chat.state["chat_id"]
    chats = chat.list_chats()
    assert [c["id"] for c in chats] == [b, a], chats
    assert chats[1]["title"] == "make a table"
    assert chat.load(a)
    assert chat.state["session"] == "sess-A" and chat.state["history"][1] == ("claude", "done")
    chat.delete(a)
    assert chat.state["chat_id"] is None and [c["id"] for c in chat.list_chats()] == [b]
    chat.new_chat()
    assert len(chat.list_chats()) == 1, "empty chats must not be written"


@test
def t_chat_list_enum():
    claude_bridge.register()
    try:
        chat.new_chat()
        chat._push("user", "enum chat")
        cid = chat.state["chat_id"]
        wm = bpy.context.window_manager
        assert wm.claude_chat_list == cid
        wm.claude_chat_list = "NEW"
        assert chat.state["chat_id"] is None and chat.state["history"] == []
        wm.claude_chat_list = cid
        assert chat.state["chat_id"] == cid and chat.state["history"][0][1] == "enum chat"
    finally:
        claude_bridge.unregister()


@test
def t_plain_markdown():
    assert chat._plain("- **Lamp:** the `bevel` op\n## Done") == "• Lamp: the bevel op\nDone"


@test
def t_skill():
    body = chat.skill_body()
    assert body.startswith("# Working in Blender") and "name:" not in body.splitlines()[0]
    cmd = chat.build_cmd("claude")
    assert body in cmd[cmd.index("--append-system-prompt") + 1]
    home = tempfile.mkdtemp()
    path = chat.install_skill(home)
    text = open(path, encoding="utf-8").read()
    assert path.endswith(os.path.join(".claude", "skills", "blender-bridge", "SKILL.md"))
    assert text.startswith("---\nname: blender-bridge\ndescription:")


@test
def t_slim_context_flags():
    cmd = chat.build_cmd("claude")
    for f in ("--tools=", "--disable-slash-commands", "--setting-sources=", "--strict-mcp-config"):
        assert f in cmd, f


@test
def t_attach_limit():
    chat.new_chat()
    for _ in range(chat.MAX_ATTACH):
        chat.attach("x.png")
    try:
        chat.attach("x.png")
        raise AssertionError("limit not enforced")
    except ValueError:
        pass
    chat.new_chat()
    assert chat.state["attachments"] == []


@test
def t_missing_cli():
    chat.new_chat()
    orig = chat.find_claude
    chat.find_claude = lambda: None
    try:
        chat.send("hello")
    finally:
        chat.find_claude = orig
    assert chat.state["history"][-1][0] == "error" and not chat.state["busy"]


def main():
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    sys.stdout.flush()
    os._exit(1 if failed else 0)


main()
