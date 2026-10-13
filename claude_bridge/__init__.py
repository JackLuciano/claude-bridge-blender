"""Claude Bridge: compact, token-cheap remote control of Blender for Claude Code.

A localhost TCP server receives JSON lines of the form
    {"ops": [{"op": "add", "type": "cube", "name": "Box"}, ...]}
queues them, executes them on Blender's main thread and answers with one
short text line per op. Every batch is a single undo step.
"""

bl_info = {
    "name": "Claude Bridge",
    "author": "JackLuciano",
    "version": (0, 1, 2),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Claude",
    "description": "Compact op-based control channel for Claude Code (MCP)",
    "category": "Interface",
}

import hmac
import json
import os
import queue
import secrets
import socket
import tempfile
import threading
import time

import bpy

from . import chat
from . import ops as bridge_ops

HOST = "127.0.0.1"
PORT = 9877

_server = None
_jobs: "queue.Queue[tuple[dict, queue.Queue]]" = queue.Queue()


def _handle_request(req: dict) -> dict:
    """Runs on the main thread. Executes a batch as one undo step."""
    global PUSHES
    if "look" in req:
        return bridge_ops.look(req["look"] or {})
    batch = req.get("ops", [])
    results, ok = bridge_ops.run_batch(batch)
    if bridge_ops.batch_kind(batch) == "write":  # read-only calls must not wipe the user's redo
        try:
            bpy.ops.ed.undo_push(message=bridge_ops.undo_label(batch))
        except Exception:
            pass
        PUSHES += 1
    resp = {"ok": ok, "text": "\n".join(results)}
    if bridge_ops.IMAGES:
        resp["images"] = list(bridge_ops.IMAGES)
    return resp


PUSHES = 0  # undo steps pushed by the bridge; the chat uses it to revert a whole turn


JOB_TTL = 600  # seconds a client waits; jobs older than this are dropped instead of run late


def _pump():
    """Timer callback: drain queued requests on the main thread."""
    while True:
        try:
            req, reply, deadline = _jobs.get_nowait()
        except queue.Empty:
            break
        if time.time() > deadline:
            reply.put({"ok": False, "text": "ERR expired"})
            continue
        try:
            reply.put(_handle_request(req))
        except Exception as e:  # never let the timer die
            reply.put({"ok": False, "text": f"ERR internal: {e}"})
    return 0.02


_HTTP = (b"GET ", b"POST", b"PUT ", b"HEAD", b"OPTI", b"DELE", b"PATC", b"CONN", b"TRAC")
TOKEN = None  # per-launch secret; mcp_server.py reads it from token_path()


def token_path(port=None):
    return os.path.join(tempfile.gettempdir(), f"claude_bridge_{port or PORT}.token")


def _write_token(port):
    global TOKEN
    TOKEN = secrets.token_urlsafe(16)
    path = token_path(port)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(TOKEN)


def _client_thread(conn: socket.socket):
    """One JSON request per line. Anything else (an HTTP request from a web page, bad JSON,
    a missing or wrong token) closes the connection without running anything."""
    buf = b""
    with conn:
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                return
            if not buf and chunk.lstrip()[:4].upper() in _HTTP:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    req = json.loads(line)
                    if not isinstance(req, dict):
                        raise ValueError("not an object")
                except ValueError:
                    return
                if not TOKEN or not hmac.compare_digest(str(req.pop("auth", "")), TOKEN):
                    conn.sendall((json.dumps({"ok": False, "text": "ERR bad token: restart Claude Code or the chat"}) + "\n").encode())
                    return
                reply: queue.Queue = queue.Queue()
                _jobs.put((req, reply, time.time() + JOB_TTL))
                try:
                    resp = reply.get(timeout=JOB_TTL)
                except queue.Empty:
                    resp = {"ok": False, "text": "ERR timeout"}
                conn.sendall((json.dumps(resp) + "\n").encode())


PORT_RANGE = 10  # a second Blender falls back to 9878, 9879, ...


class _Server(threading.Thread):
    def __init__(self, port):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            # Windows: SO_REUSEADDR would let two Blenders bind the same port and split traffic.
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind((HOST, port))
        except OSError:
            self.sock.close()
            raise
        self.sock.listen(4)
        self.port = port
        self.running = True

    def run(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=_client_thread, args=(conn,), daemon=True).start()

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass


def start():
    """Bind the first free port from PORT upward. The in-Blender chat follows the bound port;
    Claude Code (registered on the default port) talks to the first Blender."""
    global _server, PORT
    if _server:
        return
    p = prefs()
    base = getattr(start, "base", p.port if p else PORT)
    start.base = base
    for port in range(base, base + PORT_RANGE):
        try:
            _server = _Server(port)
            break
        except OSError:
            continue
    else:
        raise OSError(f"no free port in {base}-{base + PORT_RANGE - 1}")
    PORT = _server.port
    _write_token(PORT)
    _server.start()
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, persistent=True)


def stop():
    global _server
    if _server:
        _server.stop()
        try:
            os.remove(token_path(_server.port))
        except OSError:
            pass
        _server = None
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)


class ClaudeBridgePrefs(bpy.types.AddonPreferences):
    bl_idname = __package__

    port: bpy.props.IntProperty(name="Port", default=9877, min=1024, max=65000,
                                description="First port the bridge tries (a second Blender takes the next free one)")
    autostart: bpy.props.BoolProperty(name="Start bridge with Blender", default=True)
    default_model: bpy.props.EnumProperty(name="Default model", items=chat.MODEL_ITEMS_STATIC, default="opus")
    default_effort: bpy.props.EnumProperty(name="Default effort", items=chat.EFFORT_ITEMS, default="default")
    send_context: bpy.props.BoolProperty(
        name="Send scene context", default=True,
        description="Start every message with a short line about the selection, mode and 3D cursor (~30 tokens)")
    claude_path: bpy.props.StringProperty(name="claude CLI", subtype="FILE_PATH",
                                          description="Path to the claude executable; empty = find it automatically")
    chats_dir: bpy.props.StringProperty(name="Chats folder", subtype="DIR_PATH",
                                        description="Where chats are saved; empty = Blender's config folder")
    inbox_dir: bpy.props.StringProperty(name="Inbox folder", subtype="DIR_PATH",
                                        description="Model files dropped here are imported and fitted by the Inbox button; "
                                                    "empty = <chats folder>/inbox")
    custom_instructions: bpy.props.StringProperty(
        name="Standing instructions",
        description="Added to every chat's system prompt, e.g. 'Use imperial units' or 'Low-poly style'")
    safe_mode: bpy.props.BoolProperty(
        name="Safe mode", default=False,
        description="Hold risky ops (py, new, open, deleting objects Claude didn't make) until you approve them, "
                    "and keep file paths inside the .blend folder, temp and the Inbox")
    teach_mode: bpy.props.BoolProperty(
        name="Show how-to hints", default=False,
        description="Under each tool call, show the menu path or shortcut to do it by hand")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "port")
        col.prop(self, "autostart")
        row = col.row()
        row.prop(self, "default_model")
        row.prop(self, "default_effort")
        col.prop(self, "send_context")
        col.prop(self, "safe_mode")
        col.prop(self, "teach_mode")
        col.prop(self, "claude_path")
        col.prop(self, "chats_dir")
        col.prop(self, "inbox_dir")
        col.prop(self, "custom_instructions")
        n = len(self.custom_instructions) // 4
        if n:
            col.label(text=f"≈{n} tokens added to every message" + (" — keep it short" if n > 300 else ""),
                      icon="ERROR" if n > 300 else "INFO")


def prefs():
    """The add-on preferences, or None when the module isn't enabled as an add-on (tests, scripts)."""
    try:
        a = bpy.context.preferences.addons.get(__package__)
    except AttributeError:
        return None
    return a.preferences if a else None


class CLAUDE_OT_toggle(bpy.types.Operator):
    bl_idname = "claude.toggle_bridge"
    bl_label = "Toggle Claude Bridge"

    def execute(self, context):
        stop() if _server else start()
        return {"FINISHED"}


def mcp_add_command():
    """`claude mcp add` line for Claude Code, using Blender's bundled Python and this addon's server."""
    import os
    import sys
    server = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")
    p = prefs()
    env = f" -e CLAUDE_BRIDGE_PORT={p.port}" if p and p.port != 9877 else ""
    return f'claude mcp add -s user{env} blender-bridge -- "{sys.executable}" "{server}"'


class CLAUDE_OT_copy_mcp(bpy.types.Operator):
    bl_idname = "claude.copy_mcp_command"
    bl_label = "Copy Claude Code setup command"
    bl_description = "Copy the command that connects Claude Code to this Blender; run it once in a terminal"

    def execute(self, context):
        context.window_manager.clipboard = mcp_add_command()
        self.report({"INFO"}, "Copied. Run it once in a terminal, then restart Claude Code.")
        return {"FINISHED"}


class CLAUDE_OT_install_skill(bpy.types.Operator):
    bl_idname = "claude.install_skill"
    bl_label = "Install Claude Code skill"
    bl_description = "Install the blender-bridge skill into ~/.claude/skills so Claude Code knows how to use the tools"

    def execute(self, context):
        try:
            path = chat.install_skill()
        except OSError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Skill installed: {path}")
        return {"FINISHED"}


class CLAUDE_PT_panel(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Claude"
    bl_label = "Bridge"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        col = self.layout.column()
        col.label(text=f"{'Running' if _server else 'Stopped'} on {HOST}:{PORT}")
        if _server and PORT != getattr(start, "base", PORT):
            col.label(text="Another Blender has the default port;", icon="INFO")
            col.label(text="Claude Code talks to that one.", icon="BLANK1")
        col.operator("claude.toggle_bridge", text="Stop" if _server else "Start")
        col.operator("claude.copy_mcp_command", icon="COPYDOWN")
        col.operator("claude.install_skill", icon="BOOKMARKS")


_classes = (ClaudeBridgePrefs, CLAUDE_OT_toggle, CLAUDE_OT_copy_mcp, CLAUDE_OT_install_skill, CLAUDE_PT_panel)


def _autostart():
    p = prefs()
    if p is None or p.autostart:
        start()
    chat.apply_prefs()
    return None


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    chat.register()
    # Deferred so it also works when enabled at startup (preferences aren't readable during register).
    bpy.app.timers.register(_autostart, first_interval=0.5)


def unregister():
    stop()
    chat.unregister()
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
