"""In-Blender chat: runs the local `claude` CLI headless (subscription auth, no API key)
with only the bridge's MCP tools, and streams the reply into a sidebar panel.

The CLI runs in a worker thread; its tool calls come back into Blender through the
bridge socket and are executed on the main thread by the bridge's timer, so the UI
stays responsive. The prompt (text + attached images) is sent as stream-json on stdin.
Chats are persisted as JSON files and can be reopened; reopening resumes the Claude session.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import uuid

import bpy

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER = "blender-bridge"
MAX_ATTACH = 5

SYSTEM_PROMPT = (
    "You are inside Blender, talking to the user through a small sidebar chat. "
    "Act on the scene with the `bl` tool (batch several ops per call) and verify visually with `look` "
    "(max 384 unless detail matters). Prefer ops over the `py` op. Images the user attaches are screenshots "
    "of their Blender or references. Keep replies to 1-3 short sentences: the user sees the result in their viewport."
)

_lock = threading.RLock()
state = {
    "chat_id": None,     # id of the persisted chat currently shown
    "history": [],       # list of (role, text); role in user|claude|tool|error|info
    "session": None,     # claude session id, for --resume
    "attachments": [],   # image paths queued for the next message
    "proc": None,
    "busy": False,
    "cost": 0.0,
}


# ---------------------------------------------------------------- persistence

def chats_dir():
    d = os.environ.get("CLAUDE_BRIDGE_CHATS") or bpy.utils.user_resource("CONFIG", path="claude_bridge/chats", create=True)
    os.makedirs(d, exist_ok=True)
    return d


def _chat_path(cid):
    return os.path.join(chats_dir(), f"{cid}.json")


def list_chats():
    """[{id, title, updated}] newest first."""
    out = []
    for fn in os.listdir(chats_dir()):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(chats_dir(), fn), encoding="utf-8") as f:
                c = json.load(f)
            out.append({"id": c["id"], "title": c.get("title") or "New chat", "updated": c.get("updated", 0)})
        except (OSError, ValueError, KeyError):
            continue
    return sorted(out, key=lambda c: c["updated"], reverse=True)


def save():
    """Persist the current chat. A chat without messages is not written."""
    with _lock:
        if not state["history"]:
            return
        if not state["chat_id"]:
            state["chat_id"] = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        first = next((t for r, t in state["history"] if r == "user"), "New chat")
        data = {"id": state["chat_id"], "title": first.splitlines()[0][:48], "updated": time.time(),
                "session": state["session"], "cost": state["cost"], "history": state["history"]}
        path = _chat_path(state["chat_id"])
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)


def load(cid):
    if state["busy"]:
        return False
    with open(_chat_path(cid), encoding="utf-8") as f:
        c = json.load(f)
    with _lock:
        state.update(chat_id=c["id"], session=c.get("session"), cost=c.get("cost", 0.0), attachments=[],
                     history=[tuple(x) for x in c.get("history", [])])
    return True


def delete(cid):
    try:
        os.remove(_chat_path(cid))
    except FileNotFoundError:
        pass
    if state["chat_id"] == cid:
        new_chat()


def stop():
    p = state["proc"]
    if p:
        p.kill()
        _push("info", "stopped")


def new_chat():
    stop()
    with _lock:
        state.update(chat_id=None, session=None, cost=0.0, history=[], attachments=[])


reset = new_chat


def _push(role, text):
    with _lock:
        state["history"].append((role, text))
        del state["history"][:-500]
    save()


# ---------------------------------------------------------------- attachments

def _attach_dir():
    d = os.path.join(chats_dir(), "attachments")
    os.makedirs(d, exist_ok=True)
    return d


def attach(path):
    if len(state["attachments"]) >= MAX_ATTACH:
        raise ValueError(f"max {MAX_ATTACH} images per message")
    state["attachments"].append(path)


def capture_viewport():
    """Clean OpenGL capture of the 3D viewport (no UI), up to 1024 px."""
    from . import ops
    r = ops.look({"max": 1024})
    dst = os.path.join(_attach_dir(), f"view-{uuid.uuid4().hex[:8]}.jpg")
    shutil.copyfile(r["image"], dst)
    attach(dst)
    return dst


def capture_window(context):
    """Screenshot of the whole Blender window (UI included), downscaled to 1568 px."""
    raw = os.path.join(tempfile.gettempdir(), "claude_window_raw.png")
    bpy.ops.screen.screenshot(filepath=raw)
    dst = os.path.join(_attach_dir(), f"window-{uuid.uuid4().hex[:8]}.jpg")
    img = bpy.data.images.load(raw, check_existing=False)
    try:
        w, h = img.size
        k = min(1.0, 1568 / max(w, h))
        if k < 1.0:
            img.scale(int(w * k), int(h * k))
        img.filepath_raw = dst
        img.file_format = "JPEG"
        img.save()
    finally:
        bpy.data.images.remove(img)
    attach(dst)
    return dst


def grab_clipboard_image(dst):
    """Save the OS clipboard image to dst (PNG). Returns True if there was an image."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    if os.name == "nt":
        ps = ("Add-Type -AssemblyName System.Windows.Forms; $i = [System.Windows.Forms.Clipboard]::GetImage(); "
              f"if ($i) {{ $i.Save('{dst}', [System.Drawing.Imaging.ImageFormat]::Png); 'ok' }}")
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True,
                           creationflags=flags, timeout=15)
        return "ok" in r.stdout and os.path.exists(dst)
    if sys.platform == "darwin":
        if shutil.which("pngpaste"):
            return subprocess.run(["pngpaste", dst], capture_output=True).returncode == 0
        script = (f'set f to open for access POSIX file "{dst}" with write permission\n'
                  "write (the clipboard as «class PNGf») to f\nclose access f")
        return subprocess.run(["osascript", "-e", script], capture_output=True).returncode == 0
    for cmd in (["wl-paste", "--type", "image/png"], ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"]):
        if shutil.which(cmd[0]):
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode == 0 and r.stdout.startswith(b"\x89PNG"):
                with open(dst, "wb") as f:
                    f.write(r.stdout)
                return True
    return False


class _WinPasteWatcher:
    """Windows: detect a real Ctrl+V keystroke while Blender is focused.

    Blender's text field and UI handlers consume Ctrl+V before any keymap runs, so a
    keymap can't attach clipboard images. Polling the physical key state works
    regardless of focus, including while typing in the chat field.
    """

    def __init__(self):
        import ctypes
        self.u = ctypes.windll.user32
        self.pid = os.getpid()
        self.was_down = False
        self.last_seq = None

    def _blender_focused(self):
        import ctypes
        pid = ctypes.c_ulong()
        self.u.GetWindowThreadProcessId(self.u.GetForegroundWindow(), ctypes.byref(pid))
        return pid.value == self.pid

    def poll(self):
        down = bool(self.u.GetAsyncKeyState(0x11) & 0x8000) and bool(self.u.GetAsyncKeyState(0x56) & 0x8000)
        fired = down and not self.was_down
        self.was_down = down
        if not fired or not self._blender_focused() or not _claude_tab_open():
            return False
        if not (self.u.IsClipboardFormatAvailable(8) or self.u.IsClipboardFormatAvailable(2)):  # CF_DIB / CF_BITMAP
            return False
        seq = self.u.GetClipboardSequenceNumber()
        if seq == self.last_seq:
            return False  # same image already attached
        self.last_seq = seq
        return True


def _claude_tab_open():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D" and area.spaces.active.show_region_ui:
                for r in area.regions:
                    if r.type == "UI" and r.active_panel_category == "Claude":
                        return True
    return False


_watcher = None


def _paste_watch():
    try:
        if _watcher and _watcher.poll():
            paste_image()
    except Exception as e:
        _push("error", f"paste failed: {e}")
    return 0.03


def paste_image():
    dst = os.path.join(_attach_dir(), f"paste-{uuid.uuid4().hex[:8]}.png")
    if not grab_clipboard_image(dst):
        raise ValueError("no image on the clipboard")
    attach(dst)
    return dst


_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}


def build_message(prompt, images):
    """stream-json user message with text + base64 images."""
    content = []
    for p in images:
        ext = os.path.splitext(p)[1].lower()
        if ext not in _MIME:
            continue
        with open(p, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        content.append({"type": "image", "source": {"type": "base64", "media_type": _MIME[ext], "data": data}})
    content.append({"type": "text", "text": prompt})
    return {"type": "user", "message": {"role": "user", "content": content}}


# ---------------------------------------------------------------- CLI process

def find_claude():
    exe = shutil.which("claude")
    if exe:
        return exe
    for p in (os.path.expanduser("~/.local/bin/claude.exe"), os.path.expanduser("~/.local/bin/claude"),
              os.path.expanduser("~/.claude/local/claude")):
        if os.path.exists(p):
            return p
    return None


def _mcp_config():
    # Blender's bundled Python runs the stdlib-only MCP server, so no system Python is needed.
    from . import PORT
    cfg = {"mcpServers": {SERVER: {"command": sys.executable,
                                   "args": [os.path.join(ADDON_DIR, "mcp_server.py")],
                                   "env": {"CLAUDE_BRIDGE_PORT": str(PORT)}}}}
    path = os.path.join(tempfile.gettempdir(), "claude_bridge_mcp.json")
    with open(path, "w") as f:
        json.dump(cfg, f)
    return path


def build_cmd(claude, session=None, model="opus"):
    # Slim context: no built-in tools, skills, other MCP servers or user settings/hooks.
    # Measured: ~62k -> ~6.7k context tokens per message, while keeping subscription (OAuth) auth.
    cmd = [claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--tools=", "--disable-slash-commands", "--setting-sources=",
           "--mcp-config", _mcp_config(), "--strict-mcp-config",
           "--allowedTools", f"mcp__{SERVER}__bl", f"mcp__{SERVER}__look",
           "--append-system-prompt", SYSTEM_PROMPT]
    if session:
        cmd += ["--resume", session]
    if model and model != "default":
        cmd += ["--model", model]
    return cmd


def _tool_summary(block):
    name = block.get("name", "").split("__")[-1]
    inp = block.get("input", {})
    if name == "bl":
        return "bl: " + ", ".join(o.get("op", "?") for o in inp.get("ops", []))
    return f"{name} {json.dumps(inp)[:60]}"


def handle_event(ev):
    """Turn one stream-json event into chat entries."""
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        state["session"] = ev.get("session_id", state["session"])
    elif t == "assistant":
        for b in ev.get("message", {}).get("content", []):
            if b.get("type") == "text" and b.get("text", "").strip():
                _push("claude", b["text"].strip())
            elif b.get("type") == "tool_use":
                _push("tool", _tool_summary(b))
    elif t == "user":
        for b in ev.get("message", {}).get("content", []):
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                c = b.get("content")
                txt = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict))
                _push("error", txt[:200])
    elif t == "result":
        state["session"] = ev.get("session_id", state["session"])
        state["cost"] += ev.get("total_cost_usd") or 0
        if ev.get("is_error"):
            _push("error", str(ev.get("result", "error"))[:300])
        else:
            save()  # persist the session id for --resume


def _worker(cmd, message):
    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", creationflags=flags,
                                cwd=chats_dir(),
                                # Load the two bridge tools up front instead of via a ToolSearch round trip.
                                env=dict(os.environ, ENABLE_TOOL_SEARCH="false"))
        state["proc"] = proc
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.close()
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            handle_event(ev)
            if ev.get("type") == "result":
                break
        proc.wait(timeout=30)
        if proc.returncode not in (0, None) and state["history"] and state["history"][-1][0] != "error":
            err = proc.stderr.read().strip()
            if err:
                _push("error", err[-300:])
    except Exception as e:
        _push("error", f"failed to run claude: {e}")
    finally:
        state["proc"] = None
        state["busy"] = False


def send(prompt, model="opus"):
    claude = find_claude()
    if not claude:
        _push("error", "claude CLI not found. Install Claude Code and run `claude` once to log in.")
        return
    from . import start
    start()  # the CLI's tool calls need the bridge running
    images = list(state["attachments"])
    state["attachments"] = []
    _push("user", prompt + (f"\n[{len(images)} image{'s' if len(images) > 1 else ''} attached]" if images else ""))
    state["busy"] = True
    threading.Thread(target=_worker, args=(build_cmd(claude, state["session"], model), build_message(prompt, images)),
                     daemon=True).start()


# ---------------------------------------------------------------- UI

_ICON = {"user": "USER", "claude": "LIGHT", "tool": "TOOL_SETTINGS", "error": "ERROR", "info": "INFO"}
_PREFIX = {"user": "You", "claude": "Claude", "tool": "tool", "error": "error", "info": "info"}


class CLAUDE_OT_chat_send(bpy.types.Operator):
    bl_idname = "claude.chat_send"
    bl_label = "Send"
    bl_description = "Send the message (and attached images) to Claude"

    def execute(self, context):
        wm = context.window_manager
        msg = wm.claude_chat_input.strip()
        if not msg and state["attachments"]:
            msg = "See the attached image."
        if not msg or state["busy"]:
            return {"CANCELLED"}
        wm.claude_chat_input = ""
        send(msg, wm.claude_chat_model)
        return {"FINISHED"}


class CLAUDE_OT_chat_stop(bpy.types.Operator):
    bl_idname = "claude.chat_stop"
    bl_label = "Stop"

    def execute(self, context):
        stop()
        return {"FINISHED"}


class CLAUDE_OT_chat_new(bpy.types.Operator):
    bl_idname = "claude.chat_new"
    bl_label = "New chat"

    def execute(self, context):
        new_chat()
        context.window_manager.claude_chat_list = "NEW"
        return {"FINISHED"}


class CLAUDE_OT_chat_delete(bpy.types.Operator):
    bl_idname = "claude.chat_delete"
    bl_label = "Delete chat"
    bl_description = "Delete the current chat from history"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        if state["chat_id"]:
            delete(state["chat_id"])
        context.window_manager.claude_chat_list = "NEW"
        return {"FINISHED"}


class CLAUDE_OT_chat_copy(bpy.types.Operator):
    bl_idname = "claude.chat_copy"
    bl_label = "Copy chat"

    def execute(self, context):
        context.window_manager.clipboard = "\n".join(f"{_PREFIX[r]}: {t}" for r, t in state["history"])
        return {"FINISHED"}


class CLAUDE_OT_attach(bpy.types.Operator):
    bl_idname = "claude.chat_attach"
    bl_label = "Attach screenshot"
    bl_description = "Attach a screenshot to the next message"

    kind: bpy.props.EnumProperty(items=[("VIEW", "Viewport", "Clean viewport capture"),
                                        ("WINDOW", "Window", "Whole Blender window, UI included")])

    def execute(self, context):
        try:
            capture_viewport() if self.kind == "VIEW" else capture_window(context)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


class CLAUDE_OT_attach_file(bpy.types.Operator):
    bl_idname = "claude.chat_attach_file"
    bl_label = "Attach image file"
    bl_description = "Attach an image file (png, jpg, webp, gif) to the next message"

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.png;*.jpg;*.jpeg;*.webp;*.gif", options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if os.path.splitext(self.filepath)[1].lower() not in _MIME:
            self.report({"ERROR"}, "unsupported image type")
            return {"CANCELLED"}
        try:
            attach(self.filepath)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


class CLAUDE_OT_paste(bpy.types.Operator):
    bl_idname = "claude.chat_paste"
    bl_label = "Paste image"
    bl_description = "Attach the image on the clipboard (Ctrl+V over the Claude sidebar)"

    def invoke(self, context, event):
        # Bound to Ctrl+V in the 3D view: only act over the sidebar, otherwise let Blender paste as usual.
        if event.type == "V" and not (context.area and context.area.type == "VIEW_3D"
                                      and context.region and context.region.type == "UI"):
            return {"PASS_THROUGH"}
        return self.execute(context)

    def execute(self, context):
        try:
            paste_image()
        except Exception as e:
            self.report({"WARNING"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, "image attached")
        return {"FINISHED"}


class CLAUDE_OT_attach_clear(bpy.types.Operator):
    bl_idname = "claude.chat_attach_clear"
    bl_label = "Remove attachments"

    def execute(self, context):
        state["attachments"] = []
        return {"FINISHED"}


class CLAUDE_PT_chat(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Claude"
    bl_label = "Chat"

    def draw(self, context):
        wm = context.window_manager
        layout = self.layout

        row = layout.row(align=True)
        row.prop(wm, "claude_chat_list", text="")
        row.operator("claude.chat_new", text="", icon="ADD")
        row.operator("claude.chat_delete", text="", icon="TRASH")

        width = max(20, int(context.region.width / (7.5 * context.preferences.view.ui_scale)))
        box = layout.box()
        with _lock:
            hist = list(state["history"][-30:])
        if not hist:
            box.label(text="Ask for anything, e.g. 'add a wooden table'", icon="INFO")
        col = box.column(align=True)
        for role, text in hist:
            first = True
            for para in text.splitlines() or [""]:
                for line in textwrap.wrap(para, width) or [""]:
                    col.label(text=line, icon=_ICON[role] if first else "BLANK1")
                    first = False
            col.separator(factor=0.5)
        if state["busy"]:
            box.label(text="Claude is working…", icon="SORTTIME")

        if state["attachments"]:
            row = layout.row(align=True)
            row.label(text=f"{len(state['attachments'])} image(s) attached", icon="IMAGE_DATA")
            row.operator("claude.chat_attach_clear", text="", icon="X")

        row = layout.row(align=True)
        row.prop(wm, "claude_chat_input", text="")
        if state["busy"]:
            row.operator("claude.chat_stop", text="", icon="CANCEL")
        else:
            row.operator("claude.chat_send", text="", icon="PLAY")

        row = layout.row(align=True)
        row.operator("claude.chat_attach", text="", icon="VIEW3D").kind = "VIEW"
        row.operator("claude.chat_attach", text="", icon="WINDOW").kind = "WINDOW"
        row.operator("claude.chat_attach_file", text="", icon="FILE_IMAGE")
        row.operator("claude.chat_paste", text="", icon="PASTEDOWN")
        row.operator("claude.chat_copy", text="", icon="COPYDOWN")
        row.prop(wm, "claude_chat_model", text="")


def _on_input_update(self, context):
    # Pressing Enter in the text field sends the message.
    if self.claude_chat_input.strip() and not state["busy"]:
        msg = self.claude_chat_input.strip()
        self.claude_chat_input = ""
        send(msg, self.claude_chat_model)


# Blender keeps raw pointers to the strings of dynamic enum items, so every list we hand
# out must stay alive while the UI may still draw it. Clearing it in place blanks the dropdown.
_enum_keep = []


def _chat_items(self, context):
    items = [("NEW", "New chat", "Start a new conversation", "ADD", 0)]
    cur = state["chat_id"]
    chats = list_chats()
    if cur and not any(c["id"] == cur for c in chats):
        chats.insert(0, {"id": cur, "title": "Current chat"})
    for i, c in enumerate(chats[:50], start=1):
        items.append((c["id"], c["title"], time.strftime("%Y-%m-%d %H:%M", time.localtime(c.get("updated", 0))),
                      "CHECKMARK" if c["id"] == cur else "NONE", i))
    if _enum_keep and _enum_keep[-1] == items:
        return _enum_keep[-1]
    _enum_keep.append(items)
    del _enum_keep[:-32]
    return items


def _chat_get(self):
    cur = state["chat_id"]
    for item in _chat_items(self, None):
        if item[0] == cur:
            return item[4]
    return 0


def _chat_set(self, value):
    for item in _enum_keep[-1] if _enum_keep else _chat_items(self, None):
        if item[4] == value:
            if item[0] == "NEW":
                new_chat()
            elif item[0] != state["chat_id"]:
                load(item[0])
            return


_last = [None]


def _redraw():
    """Redraw the sidebar while Claude streams, so new messages appear live."""
    sig = (len(state["history"]), state["busy"], len(state["attachments"]), state["chat_id"])
    if sig != _last[0]:
        _last[0] = sig
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    return 0.2


classes = (CLAUDE_OT_chat_send, CLAUDE_OT_chat_stop, CLAUDE_OT_chat_new, CLAUDE_OT_chat_delete, CLAUDE_OT_chat_copy,
           CLAUDE_OT_attach, CLAUDE_OT_attach_file, CLAUDE_OT_paste, CLAUDE_OT_attach_clear, CLAUDE_PT_chat)

_keymaps = []


def register():
    for c in classes:
        bpy.utils.register_class(c)
    global _watcher
    if os.name == "nt" and not bpy.app.background:
        _watcher = _WinPasteWatcher()
        bpy.app.timers.register(_paste_watch, persistent=True)
    else:
        # Elsewhere: Ctrl/Cmd+V over empty sidebar space (text fields still consume it; use the paste button).
        kc = bpy.context.window_manager.keyconfigs.addon
        if kc:  # None in background mode
            km = kc.keymaps.new(name="3D View Generic", space_type="VIEW_3D")
            key = {"oskey": True} if sys.platform == "darwin" else {"ctrl": True}
            _keymaps.append((km, km.keymap_items.new("claude.chat_paste", "V", "PRESS", **key)))
    wm = bpy.types.WindowManager
    wm.claude_chat_input = bpy.props.StringProperty(name="Message", options={"SKIP_SAVE"}, update=_on_input_update)
    wm.claude_chat_model = bpy.props.EnumProperty(
        name="Model", default="opus",
        items=[("opus", "Opus", "Most capable (default)"), ("sonnet", "Sonnet", "Faster"),
               ("haiku", "Haiku", "Fastest, cheapest"), ("default", "CLI default", "Your Claude Code default model")])
    wm.claude_chat_list = bpy.props.EnumProperty(name="Chats", description="Saved chats",
                                                 items=_chat_items, get=_chat_get, set=_chat_set)
    if not bpy.app.timers.is_registered(_redraw):
        bpy.app.timers.register(_redraw, persistent=True)


def unregister():
    stop()
    if bpy.app.timers.is_registered(_redraw):
        bpy.app.timers.unregister(_redraw)
    if bpy.app.timers.is_registered(_paste_watch):
        bpy.app.timers.unregister(_paste_watch)
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    for p in ("claude_chat_input", "claude_chat_model", "claude_chat_list"):
        delattr(bpy.types.WindowManager, p)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
