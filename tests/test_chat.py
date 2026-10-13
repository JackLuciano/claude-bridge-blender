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
    cmd = chat.build_cmd("claude", extra="Always use metric units.")
    assert "--append-system-prompt" not in cmd
    sp = open(cmd[cmd.index("--system-prompt-file") + 1], encoding="utf-8").read()
    assert body in sp and chat.SYSTEM_PROMPT in sp and "Always use metric units." in sp
    assert cmd[cmd.index("--max-turns") + 1] == str(chat.MAX_TURNS)
    c = chat.build_cmd("claude", effort="low")
    assert "--effort" not in cmd and c[c.index("--effort") + 1] == "low"
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
def t_hardening():
    env = chat.child_env({"PATH": "x", "ANTHROPIC_API_KEY": "sk", "CLAUDE_CODE_USE_BEDROCK": "1"})
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_CODE_USE_BEDROCK" not in env, env
    assert env["PATH"] == "x" and env["ENABLE_TOOL_SEARCH"] == "false"
    cmd = chat.build_cmd("claude")
    assert cmd[cmd.index("--mcp-config") + 1].endswith(f"claude_bridge_mcp_{claude_bridge.PORT}.json")
    assert "logged out" in chat.classify_error("Invalid API key · Please run /login")
    assert chat.classify_error("something odd") == "something odd"
    # Big attachments are downscaled before sending
    img = bpy.data.images.new("big", 3000, 200)
    p = os.path.join(tempfile.gettempdir(), "claude_test_big.png")
    img.filepath_raw, img.file_format = p, "PNG"
    img.save()
    small = chat._shrink_attachments([p])[0]
    im = bpy.data.images.load(small)
    assert small != p and max(im.size) == chat.MAX_IMAGE, im.size


@test
def t_context_line():
    from claude_bridge import ops
    ops.op_new({})
    ops.run_batch([{"op": "add", "type": "cube", "name": "Chair"}, {"op": "select", "obj": "Chair"}])
    bpy.context.scene.cursor.location = (1, 2, 0)
    line = chat.context_line()
    assert line.startswith("[ctx mode object") and "active Chair" in line and "sel Chair" in line, line
    assert "cursor (1,2,0)" in line, line
    msg = chat.build_message("make this red", [], line)
    assert msg["message"]["content"][-1]["text"] == line + "\nmake this red"
    # Prefill must not send
    claude_bridge.register()
    try:
        sent = []
        orig = chat.send
        chat.send = lambda *a, **k: sent.append(a)
        try:
            chat.prefill(bpy.context.window_manager, "hello")
            assert not sent and bpy.context.window_manager.claude_chat_input == "hello"
        finally:
            chat.send = orig
            chat.prefill(bpy.context.window_manager, "")
    finally:
        claude_bridge.unregister()


@test
def t_preferences():
    import addon_utils
    mod = addon_utils.enable("claude_bridge", default_set=True)  # factory startup: prefs are never saved
    try:
        assert mod is not None
        p = claude_bridge.prefs()
        assert p is not None and p.port == 9877 and p.autostart and p.default_model == "opus"
        p.custom_instructions = "Use imperial units."
        assert chat.custom_instructions() == "Use imperial units."
        p.port = 9900
        assert "-e CLAUDE_BRIDGE_PORT=9900" in claude_bridge.mcp_add_command()
        p.default_model, p.default_effort = "sonnet", "low"
        chat.apply_prefs()
        wm = bpy.context.window_manager
        assert wm.claude_chat_model == "sonnet" and wm.claude_chat_effort == "low"
        wm.claude_chat_model = "haiku"
        assert p.default_model == "haiku"
    finally:
        addon_utils.disable("claude_bridge", default_set=True)


@test
def t_turn_ledger():
    from claude_bridge import ops
    chat.new_chat()
    ops.op_new({})
    ops.run_batch([{"op": "add", "type": "cube", "name": "Old"}, {"op": "add", "type": "cube", "name": "Gone"}])
    bpy.ops.ed.undo_push(message="setup")
    chat._turn_start()
    claude_bridge._handle_request({"ops": [{"op": "add", "type": "sphere", "name": "Lamp"},
                                           {"op": "move", "obj": "Old", "by": [1, 0, 0]}]})
    claude_bridge._handle_request({"ops": [{"op": "scene"}]})  # read-only: no undo step
    claude_bridge._handle_request({"ops": [{"op": "del", "obj": "Gone"}]})
    chat._turn_end()
    assert chat.turn["pushes"] == 2, chat.turn
    role, text = chat.state["history"][-1]
    assert role == "diff" and text == "+1 Lamp · ~1 Old moved · -1 Gone", text
    assert set(chat.turn["changed"]) == {"Lamp", "Old"}
    assert chat.can_revert() == (True, "")
    chat.revert_turn()
    names = set(bpy.context.scene.objects.keys())
    assert names == {"Old", "Gone"}, names
    assert round(bpy.data.objects["Old"].location.x, 3) == 0
    assert not chat.can_revert()[0]
    # A hand edit after the reply blocks revert and is reported with the next message
    chat._turn_start()
    claude_bridge._handle_request({"ops": [{"op": "add", "type": "cube", "name": "New2"}]})
    chat._turn_end()
    bpy.data.objects["Old"].location.z = 3
    ok, why = chat.can_revert()
    assert not ok and "edited" in why, why
    assert chat.user_edits_since_turn() == "~1 Old moved", chat.user_edits_since_turn()


@test
def t_usage_and_handoff():
    chat.new_chat()
    chat.handle_event({"type": "result", "session_id": "s", "num_turns": 3, "duration_ms": 9200,
                       "usage": {"input_tokens": 12, "cache_read_input_tokens": 13000, "cache_creation_input_tokens": 988,
                                 "output_tokens": 1100},
                       "modelUsage": {"claude-opus": {"contextWindow": 1000000}}})
    assert chat.usage_line() == "ctx 14k/1M · last reply: 3 steps, 1.1k out, 9s", chat.usage_line()
    chat.handle_event({"type": "result", "subtype": "error_max_turns", "is_error": True})
    assert "say 'continue'" in chat.state["history"][-1][1]
    chat._push("user", "build a desk")
    seed = chat.handoff_prompt()
    assert "The scene now:" in seed and "- build a desk" in seed
    # The seed rides along with the next message, invisibly
    chat.state["handoff"] = seed
    sent = {}
    orig_find, orig_thread = chat.find_claude, chat.threading.Thread

    class T:
        def __init__(self, target, args, daemon):
            sent["msg"] = args[1]

        def start(self):
            pass
    chat.find_claude, chat.threading.Thread = (lambda: "claude"), T
    try:
        chat.send("add a lamp", ctx=False)
    finally:
        chat.find_claude, chat.threading.Thread = orig_find, orig_thread
        chat.state["busy"] = False
    text = sent["msg"]["message"]["content"][-1]["text"]
    assert text.startswith("Continuing an earlier chat") and text.endswith("add a lamp"), text
    assert chat.state["history"][-1] == ("user", "add a lamp") and "handoff" not in chat.state


@test
def t_inbox():
    from claude_bridge import ops
    ops.op_new({})
    chat.new_chat()
    d = chat.inbox_dir()  # import_inbox runs batches in-process: no bridge needed
    ops.run_batch([{"op": "add", "type": "cube", "name": "Huge", "dims": [100, 100, 100], "loc": [0, 0, 500]},
                   {"op": "export", "path": os.path.join(d, "huge.obj")}])
    ops.op_new({})
    lines = chat.import_inbox()
    assert len(lines) == 1 and "scaled to 1 m" in lines[0], lines
    o = bpy.data.collections["Inbox"].objects[0]
    assert abs(max(o.dimensions) - 1) < 1e-3, tuple(o.dimensions)
    assert chat.import_inbox() == [], "already imported files are skipped"


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
