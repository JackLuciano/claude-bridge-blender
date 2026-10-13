"""Claude Bridge: compact, token-cheap remote control of Blender for Claude Code.

A localhost TCP server receives JSON lines of the form
    {"ops": [{"op": "add", "type": "cube", "name": "Box"}, ...]}
queues them, executes them on Blender's main thread and answers with one
short text line per op. Every batch is a single undo step.
"""

bl_info = {
    "name": "Claude Bridge",
    "author": "JackLuciano",
    "version": (0, 2, 0),
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
    from . import doctor
    doctor.recent_ops.append(",".join(str(bridge_ops._normalize(a).get("op")) for a in batch)[:120])
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


APPROVAL_TIMEOUT = 300
pending = []  # safe mode: [(req, reply, lines, t0)] waiting for the user's Approve/Deny


def _needs_approval(req):
    p = prefs()
    if not (p and p.safe_mode) or "ops" not in req:
        return []
    return bridge_ops.describe_risky(req["ops"])


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
            lines = _needs_approval(req)
            if lines:
                pending.append((req, reply, lines, time.time()))
                continue
            reply.put(_handle_request(req))
        except Exception as e:  # never let the timer die
            reply.put({"ok": False, "text": f"ERR internal: {e}"})
    for item in [x for x in pending if time.time() - x[3] > APPROVAL_TIMEOUT]:
        resolve(item, False, "no answer within 5 minutes")
    return 0.02


def resolve(item, approve, why="the user declined"):
    """Answer a held batch: run it, or tell Claude it was refused (and not to retry)."""
    if item not in pending:
        return
    pending.remove(item)
    req, reply = item[0], item[1]
    if approve:
        try:
            reply.put(_handle_request(req))
        except Exception as e:
            reply.put({"ok": False, "text": f"ERR internal: {e}"})
    else:
        reply.put({"ok": False, "text": f"ERR {why}: {'; '.join(item[2])}. Don't retry it; ask the user or do it another way."})


class CLAUDE_OT_approve(bpy.types.Operator):
    bl_idname = "claude.bridge_approve"
    bl_label = "Approve"
    bl_description = "Run the held batch"
    approve: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        if pending:
            resolve(pending[0], self.approve)
        return {"FINISHED"}


def draw_pending(layout):
    if not pending:
        return
    box = layout.box()
    box.alert = True
    box.label(text="Claude wants to:", icon="ERROR")
    for ln in pending[0][2][:6]:
        box.label(text=ln[:90])
    row = box.row(align=True)
    row.operator("claude.bridge_approve", text="Approve", icon="CHECKMARK").approve = True
    row.operator("claude.bridge_approve", text="Deny", icon="CANCEL").approve = False
    if len(pending) > 1:
        box.label(text=f"+{len(pending) - 1} more waiting")


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
                if "ops" in req:
                    from .assets import prefetch
                    prefetch(req["ops"])  # network downloads here, off Blender's main thread
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


_cc = {"state": None, "busy": False}  # Claude Code registration status: None unknown, True/False


def _cc_status():
    """Is blender-bridge registered with Claude Code? (background thread; 'claude mcp get' is slow)"""
    import subprocess
    claude = chat.find_claude()
    if not claude:
        _cc["state"] = None
        return
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        r = subprocess.run([claude, "mcp", "get", "blender-bridge"], capture_output=True, text=True, timeout=30,
                           creationflags=flags, env=chat.child_env())
        _cc["state"] = r.returncode == 0
    except Exception:
        _cc["state"] = None


def _cc_connect():
    import shlex
    import subprocess
    claude = chat.find_claude()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    env = chat.child_env()
    try:
        subprocess.run([claude, "mcp", "remove", "-s", "user", "blender-bridge"], capture_output=True, timeout=30,
                       creationflags=flags, env=env)
        args = shlex.split(mcp_add_command(), posix=os.name != "nt")[1:]
        args = [x.strip('"') for x in args]
        subprocess.run([claude, *args], capture_output=True, timeout=30, creationflags=flags, env=env)
        chat.install_skill()
    finally:
        _cc_status()
        _cc["busy"] = False


class CLAUDE_OT_connect_cc(bpy.types.Operator):
    bl_idname = "claude.connect_claude_code"
    bl_label = "Connect Claude Code"
    bl_description = "Register this Blender's bridge with Claude Code and install the skill (restart Claude Code after)"

    def execute(self, context):
        if not chat.find_claude():
            self.report({"ERROR"}, "claude CLI not found")
            return {"CANCELLED"}
        _cc["busy"] = True
        threading.Thread(target=_cc_connect, daemon=True).start()
        return {"FINISHED"}


def skill_outdated(home=None):
    """True when Claude Code's installed copy of the skill differs from this add-on's version."""
    dst = os.path.join(home or os.path.expanduser("~"), ".claude", "skills", "blender-bridge", "SKILL.md")
    if not os.path.exists(dst):
        return None
    with open(dst, "rb") as a, open(chat.SKILL_FILE, "rb") as b:
        return a.read() != b.read()


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
        draw_pending(col)
        col.label(text=f"{'Running' if _server else 'Stopped'} on {HOST}:{PORT}")
        p = prefs()
        if p:
            col.prop(p, "safe_mode")
        if _server and PORT != getattr(start, "base", PORT):
            col.label(text="Another Blender has the default port;", icon="INFO")
            col.label(text="Claude Code talks to that one.", icon="BLANK1")
        col.operator("claude.toggle_bridge", text="Stop" if _server else "Start")
        col.separator()
        col.label(text="Claude Code (terminal / desktop):")
        if _cc["busy"]:
            col.label(text="Connecting…", icon="SORTTIME")
        elif _cc["state"] is True:
            col.label(text="Connected (blender-bridge)", icon="CHECKMARK")
        col.operator("claude.connect_claude_code", icon="LINKED",
                     text="Reconnect Claude Code" if _cc["state"] else "Connect Claude Code")
        col.operator("claude.copy_mcp_command", icon="COPYDOWN")
        outdated = skill_outdated()
        col.operator("claude.install_skill", icon="ERROR" if outdated else "BOOKMARKS",
                     text="Update Claude Code skill (outdated)" if outdated else "Install Claude Code skill")
        row = col.row(align=True)
        row.operator("claude.doctor_check", icon="CHECKMARK")
        row.operator("claude.copy_diagnostics", icon="URL")


_classes = (ClaudeBridgePrefs, CLAUDE_OT_approve, CLAUDE_OT_connect_cc, CLAUDE_OT_toggle, CLAUDE_OT_copy_mcp, CLAUDE_OT_install_skill, CLAUDE_PT_panel)


def _autostart():
    p = prefs()
    if p is None or p.autostart:
        start()
    chat.apply_prefs()
    if not bpy.app.background:
        from . import doctor
        doctor.run_async()
        threading.Thread(target=_cc_status, daemon=True).start()
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
