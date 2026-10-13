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

import json
import queue
import socket
import threading

import bpy

from . import chat
from . import ops as bridge_ops

HOST = "127.0.0.1"
PORT = 9877

_server = None
_jobs: "queue.Queue[tuple[dict, queue.Queue]]" = queue.Queue()


def _handle_request(req: dict) -> dict:
    """Runs on the main thread. Executes a batch as one undo step."""
    if "look" in req:
        return bridge_ops.look(req["look"] or {})
    results, ok = bridge_ops.run_batch(req.get("ops", []))
    try:
        bpy.ops.ed.undo_push(message="Claude batch")
    except Exception:
        pass
    return {"ok": ok, "text": "\n".join(results)}


def _pump():
    """Timer callback: drain queued requests on the main thread."""
    while True:
        try:
            req, reply = _jobs.get_nowait()
        except queue.Empty:
            break
        try:
            reply.put(_handle_request(req))
        except Exception as e:  # never let the timer die
            reply.put({"ok": False, "text": f"ERR internal: {e}"})
    return 0.02


def _client_thread(conn: socket.socket):
    buf = b""
    with conn:
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    req = json.loads(line)
                except json.JSONDecodeError as e:
                    resp = {"ok": False, "text": f"ERR bad json: {e}"}
                else:
                    reply: queue.Queue = queue.Queue()
                    _jobs.put((req, reply))
                    try:
                        resp = reply.get(timeout=600)
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
    base = getattr(start, "base", PORT)
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
    _server.start()
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, persistent=True)


def stop():
    global _server
    if _server:
        _server.stop()
        _server = None
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)


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
    return f'claude mcp add -s user blender-bridge -- "{sys.executable}" "{server}"'


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


_classes = (CLAUDE_OT_toggle, CLAUDE_OT_copy_mcp, CLAUDE_OT_install_skill, CLAUDE_PT_panel)


def register():
    chat.register()
    for c in _classes:
        bpy.utils.register_class(c)
    # Autostart; deferred so it also works when enabled at startup.
    bpy.app.timers.register(lambda: (start(), None)[1], first_interval=0.5)


def unregister():
    stop()
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
    chat.unregister()
