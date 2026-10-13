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
MAX_TURNS = 30  # tool round trips per message; stops runaway loops

MODEL_ITEMS_STATIC = [("opus", "Opus", "Most capable (default)"), ("sonnet", "Sonnet", "Faster"),
                      ("haiku", "Haiku", "Fastest, cheapest"), ("default", "CLI default", "Your Claude Code default model")]
EFFORT_ITEMS = [("default", "Effort: default", "The model's default reasoning effort"),
                ("low", "Effort: low", "Quick edits, fewest tokens"), ("medium", "Effort: medium", ""),
                ("high", "Effort: high", ""), ("max", "Effort: max", "Hardest builds, most tokens")]

SYSTEM_PROMPT = (
    "You are Claude, working inside Blender and talking to the user through a small sidebar chat. "
    "Act on the scene with the `mcp__blender-bridge__bl` tool (batch several ops per call) and verify visually with `mcp__blender-bridge__look` "
    "(max 384 unless detail matters). Prefer ops over the `py` op. Images the user attaches are screenshots "
    "of their Blender or references. Read every tool reply: on an ERR line, fix the cause and resend only the failed "
    "part. Messages may start with a [ctx ...] line written by the addon (not the user): 'this'/'it' means the active "
    "object, 'here' means the 3D cursor. Don't ask for permission for normal scene edits; do ask before deleting "
    "things the user made. Keep replies to 1-3 short plain sentences without markdown: the user sees the result in "
    "their viewport."
)

SKILL_FILE = os.path.join(ADDON_DIR, "skill", "SKILL.md")


def skill_body():
    """The blender-bridge skill without its frontmatter; appended to every chat session."""
    try:
        with open(SKILL_FILE, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    return text.strip()


def install_skill(home=None):
    """Install the skill for Claude Code at ~/.claude/skills/blender-bridge/SKILL.md. Returns the path."""
    dst_dir = os.path.join(home or os.path.expanduser("~"), ".claude", "skills", "blender-bridge")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, "SKILL.md")
    shutil.copyfile(SKILL_FILE, dst)
    return dst


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

def _prefs():
    from . import prefs
    return prefs()


def chats_dir():
    p = _prefs()
    d = (os.environ.get("CLAUDE_BRIDGE_CHATS") or (bpy.path.abspath(p.chats_dir) if p and p.chats_dir else "")
         or bpy.utils.user_resource("CONFIG", path="claude_bridge/chats", create=True))
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
        _kill_tree(p)
        _push("info", "stopped")


def new_chat():
    stop()
    with _lock:
        state.update(chat_id=None, session=None, cost=0.0, history=[], attachments=[])
    turn.update(mark=None, before=None, after=None, changed=[], pushes=0)


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
    _to_jpeg(raw, dst)
    attach(dst)
    return dst


MAX_IMAGE = 1568  # longest side Claude uses at full detail; bigger images only cost more tokens


def _to_jpeg(src, dst, size=MAX_IMAGE):
    """Downscale src to at most `size` px on the longest side and save it as a JPEG at dst (main thread)."""
    img = bpy.data.images.load(src, check_existing=False)
    try:
        w, h = img.size
        k = min(1.0, size / max(w, h, 1))
        if k < 1.0:
            img.scale(max(1, round(w * k)), max(1, round(h * k)))
        img.filepath_raw = dst
        img.file_format = "JPEG"
        img.save()
    finally:
        bpy.data.images.remove(img)
    return dst


def _shrink_attachments(paths):
    """Attachments larger than MAX_IMAGE (pasted 4K screenshots, photos) are downscaled before sending."""
    out = []
    for p in paths:
        try:
            img = bpy.data.images.load(p, check_existing=False)
            big = max(img.size) > MAX_IMAGE
            bpy.data.images.remove(img)
            if big:
                p = _to_jpeg(p, os.path.join(_attach_dir(), f"small-{uuid.uuid4().hex[:8]}.jpg"))
        except Exception:
            pass  # unreadable by Blender: send as is
        out.append(p)
    return out


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
            # Reading the clipboard spawns PowerShell (1-3 s): never block Blender's main thread on it.
            threading.Thread(target=_paste_bg, daemon=True).start()
    except Exception as e:
        _push("error", f"paste failed: {e}")
    return 0.03


def _paste_bg():
    try:
        paste_image()
    except Exception as e:
        _push("error", f"paste failed: {e}")


def paste_image():
    dst = os.path.join(_attach_dir(), f"paste-{uuid.uuid4().hex[:8]}.png")
    if not grab_clipboard_image(dst):
        raise ValueError("no image on the clipboard")
    attach(dst)
    return dst


_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}


def context_line():
    """~30 tokens of live context so 'make this red' / 'put a lamp here' work without a scene call (main thread)."""
    from .ops import _vec
    ctx = bpy.context
    sc = ctx.scene
    act = ctx.view_layer.objects.active
    sel = [o.name for o in ctx.selected_objects]
    parts = [f"mode {ctx.mode.lower()}", f"active {act.name if act else '-'}"]
    if sel:
        parts.append("sel " + ",".join(sel[:8]) + (f" +{len(sel) - 8}" if len(sel) > 8 else ""))
    parts += [f"cursor {_vec(sc.cursor.location)}", f"frame {sc.frame_current}", f"objects {len(sc.objects)}"]
    if bpy.data.filepath:
        parts.append("file " + bpy.path.basename(bpy.data.filepath))
    if ctx.mode == "EDIT_MESH" and act is not None and act.type == "MESH":
        act.update_from_editmode()
        parts.append(f"selected faces {sum(p.select for p in act.data.polygons)}")
    return "[ctx " + " | ".join(parts) + "]"


def build_message(prompt, images, ctx=None):
    """stream-json user message with text + base64 images (+ the context line, which isn't saved in history)."""
    if ctx:
        prompt = ctx + "\n" + prompt
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
    p = _prefs()
    if p and p.claude_path and os.path.exists(bpy.path.abspath(p.claude_path)):
        return bpy.path.abspath(p.claude_path)
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
    # One file per port: two Blenders must not overwrite each other's config.
    path = os.path.join(tempfile.gettempdir(), f"claude_bridge_mcp_{PORT}.json")
    with open(path, "w") as f:
        json.dump(cfg, f)
    return path


def system_prompt(extra=""):
    text = SYSTEM_PROMPT + "\n\n" + skill_body()
    if extra.strip():
        text += "\n\n# The user's standing instructions\n\n" + extra.strip()
    return text


def system_prompt_file(extra=""):
    """Write the chat's whole system prompt to a file (rewritten only when it changes, so it stays cache-friendly)."""
    text = system_prompt(extra)
    path = os.path.join(chats_dir(), "system_prompt.md")
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return path
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def build_cmd(claude, session=None, model="opus", effort=None, extra=""):
    # Slim context: no built-in tools, skills, other MCP servers or user settings/hooks, and our own system
    # prompt instead of Claude Code's coding-agent one (the chat never uses its tools or guidance).
    # Measured: ~62k -> ~6.7k context tokens per message with --append-system-prompt; replacing the prompt saves
    # a few thousand more on every call. A file also avoids a 5 KB multi-line argv through npm's claude.cmd shim.
    cmd = [claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--tools=", "--disable-slash-commands", "--setting-sources=",
           "--mcp-config", _mcp_config(), "--strict-mcp-config",
           "--allowedTools", f"mcp__{SERVER}__bl", f"mcp__{SERVER}__look",
           "--system-prompt-file", system_prompt_file(extra),
           "--max-turns", str(MAX_TURNS)]
    if effort and effort != "default":
        cmd += ["--effort", effort]
    if session:
        cmd += ["--resume", session]
    if model and model != "default":
        cmd += ["--model", model]
    return cmd


_ERRORS = (  # (substrings, friendly message); first match wins
    (("not logged in", "please run /login", "invalid api key", "oauth", "authentication"),
     "Claude Code is logged out. Run `claude` in a terminal once to log in."),
    (("usage limit", "rate limit", "limit reached", "resets"), "Usage limit reached: {raw}"),
    (("unknown option", "unrecognized", "error: option"), "Your Claude Code CLI is too old. Run `claude update`."),
    (("overloaded", "529"), "Claude is overloaded right now. Try again in a minute."),
    (("not reachable", "connection refused"), "The bridge isn't running. Start it in Claude ▸ Bridge."),
)


def classify_error(text):
    low = text.lower()
    for keys, msg in _ERRORS:
        if any(k in low for k in keys):
            return msg.format(raw=text.strip()[:200])
    return text


def _tool_summary(block):
    name = block.get("name", "").split("__")[-1]
    inp = block.get("input", {})
    if name == "bl":
        return "bl: " + ", ".join(o.get("op", "?") for o in inp.get("ops", []))
    return f"{name} {json.dumps(inp)[:60]}"


def _blocks(ev):
    """Content blocks of a message event; content may also be a plain string."""
    c = (ev.get("message") or {}).get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return [b for b in c or [] if isinstance(b, dict)]


def handle_event(ev):
    """Turn one stream-json event into chat entries."""
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        state["session"] = ev.get("session_id", state["session"])
    elif t == "assistant":
        for b in _blocks(ev):
            if b.get("type") == "text" and b.get("text", "").strip():
                _push("claude", b["text"].strip())
            elif b.get("type") == "tool_use":
                _push("tool", _tool_summary(b))
    elif t == "user":
        for b in _blocks(ev):
            if b.get("type") == "tool_result" and b.get("is_error"):
                c = b.get("content")
                txt = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict))
                # Batch output lists every op; show the failing line, not the successful ones before it.
                err = [ln for ln in txt.splitlines() if "ERR" in ln]
                _push("error", (err[0] if err else txt)[:300])
    elif t == "result":
        state["session"] = ev.get("session_id", state["session"])
        state["cost"] += ev.get("total_cost_usd") or 0
        if ev.get("is_error"):
            _push("error", classify_error(str(ev.get("result", "error"))[:300]))
        else:
            save()  # persist the session id for --resume


# Env vars that would make the CLI bill an API key or a cloud account instead of the user's subscription.
_NO_SUBSCRIPTION_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                        "CLAUDE_CODE_USE_FOUNDRY")


def child_env(base=None):
    env = dict(os.environ if base is None else base)
    for k in _NO_SUBSCRIPTION_ENV:
        env.pop(k, None)
    env["ENABLE_TOOL_SEARCH"] = "false"  # load the two bridge tools up front, not via a ToolSearch round trip
    return env


def _kill_tree(proc):
    """The CLI starts the MCP server as a child: stop both, not just the CLI."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except OSError:
        pass


def _worker(cmd, message):
    import collections
    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", creationflags=flags,
                                start_new_session=os.name != "nt", cwd=chats_dir(), env=child_env())
        state["proc"] = proc
        # Drain stderr continuously: a full pipe would block the CLI and hang the turn.
        tail = collections.deque(maxlen=40)
        threading.Thread(target=lambda: tail.extend(proc.stderr), daemon=True).start()
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.close()
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
                handle_event(ev)
            except Exception:
                continue  # one malformed event must not end the chat
            if ev.get("type") == "result":
                break
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
        if proc.returncode not in (0, None) and state["history"] and state["history"][-1][0] != "error":
            err = "".join(tail).strip()
            if err:
                _push("error", classify_error(err[-300:]))
    except Exception as e:
        _push("error", f"failed to run claude: {e}")
    finally:
        state["proc"] = None
        state["busy"] = False


# ---------------------------------------------------------------- turn ledger

def snapshot():
    """Cheap per-object fingerprint of the scene (main thread): enough to say what a turn changed."""
    snap = {}
    bpy.context.view_layer.update()  # matrix_world is stale right after a hand edit
    for o in bpy.context.scene.objects:
        mats = tuple(m.name for m in getattr(o.data, "materials", []) if m) if o.data else ()
        snap[o.name] = (o.type, tuple(round(x, 3) for x in o.matrix_world.translation),
                        tuple(round(x, 2) for x in o.matrix_world.to_euler()),
                        tuple(round(x, 3) for x in o.dimensions), mats,
                        tuple(m.type for m in o.modifiers), len(o.data.vertices) if o.type == "MESH" else 0,
                        o.hide_get())
    return snap


_WHAT = ("type", "moved", "rotated", "resized", "material", "modifiers", "geometry", "visibility")


def diff(before, after):
    """('+2 Lamp,Shade · ~1 Table moved · -1 Cube', [changed or added names]) or ('', [])."""
    added = [n for n in after if n not in before]
    removed = [n for n in before if n not in after]
    changed = []
    for n in after:
        if n in before and before[n] != after[n]:
            what = [_WHAT[i] for i in range(len(_WHAT)) if before[n][i] != after[n][i]]
            changed.append(f"{n} {'/'.join(what[:2])}")
    parts = []

    def names(lst):
        return ", ".join(lst[:4]) + (f" +{len(lst) - 4}" if len(lst) > 4 else "")
    if added:
        parts.append(f"+{len(added)} {names(added)}")
    if changed:
        parts.append(f"~{len(changed)} {names(changed)}")
    if removed:
        parts.append(f"-{len(removed)} {names(removed)}")
    return " · ".join(parts), added + [c.split(" ")[0] for c in changed]


turn = {"mark": None, "before": None, "after": None, "changed": [], "pushes": 0, "file": None}


def _bridge_pushes():
    from . import PUSHES
    return PUSHES


def _turn_start():
    turn.update(mark=_bridge_pushes(), before=snapshot(), file=bpy.data.filepath)


def _turn_end():
    """Main thread, once a reply is finished: show what changed and remember how to revert it."""
    if turn["before"] is None:
        return
    after = snapshot()
    text, names = diff(turn["before"], after)
    turn.update(after=after, changed=names, pushes=_bridge_pushes() - turn["mark"])
    if text:
        _push("diff", text)


def user_edits_since_turn():
    """Short note about edits the user made by hand since Claude's last reply (for the next message)."""
    if turn["after"] is None:
        return ""
    text, _ = diff(turn["after"], snapshot())
    return text


def can_revert():
    """(ok, reason)."""
    if state["busy"]:
        return False, "Claude is still working"
    if not turn["pushes"]:
        return False, "the last reply changed nothing"
    if turn["file"] != bpy.data.filepath:
        return False, "a different file is open"
    if turn["pushes"] > bpy.context.preferences.edit.undo_steps:
        return False, "more steps than Blender's undo history keeps"
    if turn["after"] is not None and snapshot() != turn["after"]:
        return False, "you edited the scene since; use Ctrl+Z"
    return True, ""


def revert_turn():
    ok, why = can_revert()
    if not ok:
        raise ValueError(why)
    for _ in range(turn["pushes"]):
        bpy.ops.ed.undo()
    n = turn["pushes"]
    turn.update(pushes=0, after=None, changed=[])
    _push("info", f"reverted the last reply ({n} step{'s' if n > 1 else ''})")


def send(prompt, model="opus", effort=None, ctx=True):
    claude = find_claude()
    if not claude:
        _push("error", "claude CLI not found. Install Claude Code and run `claude` once to log in.")
        return
    from . import start
    start()  # the CLI's tool calls need the bridge running
    images = _shrink_attachments(list(state["attachments"]))
    state["attachments"] = []
    _push("user", prompt + (f"\n[{len(images)} image{'s' if len(images) > 1 else ''} attached]" if images else ""))
    try:
        line = context_line() if ctx else None
        edits = user_edits_since_turn() if ctx else ""
        if edits:
            line = (line or "") + f"\n[user edits since your last reply: {edits}]"
        _turn_start()
    except Exception:
        line = None
    state["busy"] = True
    cmd = build_cmd(claude, state["session"], model, effort, custom_instructions())
    threading.Thread(target=_worker, args=(cmd, build_message(prompt, images, line)), daemon=True).start()


def custom_instructions():
    """Standing instructions from the add-on preferences (empty when unset or not installed as an add-on)."""
    p = _prefs()
    return p.custom_instructions if p else ""


# ---------------------------------------------------------------- UI

def _plain(text):
    """Blender labels can't render markdown: drop emphasis/code markers and turn bullets into •."""
    import re
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.M)
    return re.sub(r"^(\s*)[-*]\s+", r"\1• ", text, flags=re.M)


_ICON = {"user": "USER", "claude": "LIGHT", "tool": "TOOL_SETTINGS", "error": "ERROR", "info": "INFO",
         "diff": "FILE_REFRESH", "hint": "QUESTION"}
_PREFIX = {"user": "You", "claude": "Claude", "tool": "tool", "error": "error", "info": "info", "diff": "changed",
           "hint": "how-to"}


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
        prefill(wm, "")
        send_from_ui(msg, wm)
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


class CLAUDE_OT_chat_revert(bpy.types.Operator):
    bl_idname = "claude.chat_revert"
    bl_label = "Revert last reply"
    bl_description = "Undo everything Claude changed in its last reply, in one click"

    @classmethod
    def description(cls, context, props):
        ok, why = can_revert()
        return cls.bl_description if ok else f"Not available: {why}"

    def execute(self, context):
        try:
            revert_turn()
        except Exception as e:
            self.report({"WARNING"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


class CLAUDE_OT_select_changed(bpy.types.Operator):
    bl_idname = "claude.chat_select_changed"
    bl_label = "Select changed"
    bl_description = "Select and frame the objects Claude added or changed in its last reply"

    def execute(self, context):
        from . import ops
        names = [n for n in turn["changed"] if n in bpy.data.objects]
        if not names:
            return {"CANCELLED"}
        ops.op_select({"obj": names})
        try:
            ops.op_view({"frame": "sel"})
        except Exception:
            pass
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

        scale = context.preferences.view.ui_scale
        width = max(20, int(context.region.width / (7.5 * scale)))
        box = layout.box()
        with _lock:
            hist = list(state["history"][-60:])
        if not hist:
            box.label(text="Ask for anything, e.g. 'add a wooden table'", icon="INFO")
        # Panels don't auto-scroll: show only the newest lines that fit, so the latest reply is always visible.
        lines = []
        for role, text in hist:
            text = _plain(text)
            first = True
            for para in text.splitlines() or [""]:
                for line in textwrap.wrap(para, width) or [""]:
                    lines.append((line, _ICON[role] if first else "BLANK1"))
                    first = False
            lines.append(None)  # gap between messages
        fit = max(8, int((context.region.height - 300 * scale) / (20 * scale)))
        col = box.column(align=True)
        if len(lines) > fit:
            col.label(text=f"… {len(lines) - fit} earlier lines (Copy chat for all)", icon="TRIA_UP")
            lines = lines[-fit:]
        for item in lines:
            if item is None:
                col.separator(factor=0.5)
            else:
                col.label(text=item[0], icon=item[1])
        if state["busy"]:
            box.label(text="Claude is working…", icon="SORTTIME")
        elif turn["pushes"] or turn["changed"]:
            row = box.row(align=True)
            row.operator("claude.chat_select_changed", icon="RESTRICT_SELECT_OFF")
            sub = row.row(align=True)
            sub.enabled = can_revert()[0]
            sub.operator("claude.chat_revert", text="Revert reply", icon="LOOP_BACK")

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
        row.prop(wm, "claude_chat_effort", text="")


_suppress_send = [False]  # set while code (not the user) fills the input field


def _on_input_update(self, context):
    # Pressing Enter in the text field sends the message.
    if _suppress_send[0]:
        return
    if self.claude_chat_input.strip() and not state["busy"]:
        msg = self.claude_chat_input.strip()
        _suppress_send[0] = True
        try:
            self.claude_chat_input = ""
        finally:
            _suppress_send[0] = False
        send_from_ui(msg, self)


def prefill(wm, text):
    """Put text in the chat field without sending it."""
    _suppress_send[0] = True
    try:
        wm.claude_chat_input = text
    finally:
        _suppress_send[0] = False


def send_from_ui(msg, wm, model=None):
    p = _prefs()
    send(msg, model or wm.claude_chat_model, wm.claude_chat_effort, ctx=p.send_context if p else True)


def _on_model_update(self, context):
    """Remember the picked model as the default for next time."""
    p = _prefs()
    if p and p.default_model != self.claude_chat_model:
        p.default_model = self.claude_chat_model


def _on_effort_update(self, context):
    p = _prefs()
    if p and p.default_effort != self.claude_chat_effort:
        p.default_effort = self.claude_chat_effort


def apply_prefs():
    """Start the chat with the saved default model and effort (called once preferences are readable)."""
    p = _prefs()
    if p:
        wm = bpy.context.window_manager
        wm.claude_chat_model = p.default_model
        wm.claude_chat_effort = p.default_effort


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


_was_busy = [False]


def _redraw():
    """Redraw the sidebar while Claude streams, so new messages appear live."""
    if _was_busy[0] and not state["busy"]:
        try:
            _turn_end()
        except Exception as e:
            _push("error", f"change summary failed: {e}")
    _was_busy[0] = state["busy"]
    sig = (len(state["history"]), state["busy"], len(state["attachments"]), state["chat_id"])
    if sig != _last[0]:
        _last[0] = sig
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    return 0.2


classes = (CLAUDE_OT_chat_send, CLAUDE_OT_chat_stop, CLAUDE_OT_chat_new, CLAUDE_OT_chat_delete, CLAUDE_OT_chat_copy,
           CLAUDE_OT_attach, CLAUDE_OT_attach_file, CLAUDE_OT_paste, CLAUDE_OT_attach_clear, CLAUDE_OT_chat_revert,
           CLAUDE_OT_select_changed, CLAUDE_PT_chat)

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
    wm.claude_chat_model = bpy.props.EnumProperty(name="Model", default="opus", items=MODEL_ITEMS_STATIC,
                                                  update=_on_model_update)
    wm.claude_chat_effort = bpy.props.EnumProperty(name="Effort", default="default", items=EFFORT_ITEMS,
                                                   update=_on_effort_update)
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
    for p in ("claude_chat_input", "claude_chat_model", "claude_chat_effort", "claude_chat_list"):
        delattr(bpy.types.WindowManager, p)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
