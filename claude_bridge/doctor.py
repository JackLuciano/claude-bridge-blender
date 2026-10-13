"""Setup check: is the claude CLI there, new enough, logged in with a subscription, and is the bridge up?

Runs in a background thread (the CLI calls take a second or two) and only keeps the fields it shows:
it never stores the account email or org.
"""

import collections
import json
import os
import platform
import subprocess
import sys
import threading
import webbrowser

import bpy

REQUIRED_FLAGS = ("--strict-mcp-config", "--disable-slash-commands", "--input-format", "--system-prompt")
INSTALL_URL = "https://docs.claude.com/en/docs/claude-code/setup"
ISSUES_URL = "https://github.com/JackLuciano/claude-bridge-blender/issues/new?template=bug_report.md"

result = {"state": "unknown", "items": [], "checked": False, "running": False}
recent_ops = collections.deque(maxlen=20)  # op names of the last batches, for diagnostics
last_stderr = [""]


def _run(args, timeout=20):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    from .chat import child_env
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=flags, env=child_env())


def check(claude=None):
    """[(ok, label, fix)] where fix is None or one of: install, login, update, start, api_env."""
    from . import _server, PORT
    from .chat import _NO_SUBSCRIPTION_ENV, find_claude
    items = []
    claude = claude or find_claude()
    if not claude:
        items.append((False, "claude CLI not found", "install"))
        return items
    try:
        ver = _run([claude, "--version"]).stdout.strip().split(" ")[0]
        help_text = _run([claude, "--help"]).stdout
        missing = [f for f in REQUIRED_FLAGS if f not in help_text]
        items.append((not missing, f"Claude Code {ver}" + (f" (missing {', '.join(missing)})" if missing else ""),
                      "update" if missing else None))
    except Exception as e:
        items.append((False, f"claude CLI doesn't run: {e}", "install"))
        return items
    try:
        st = json.loads(_run([claude, "auth", "status", "--json"]).stdout or "{}")
        if not st.get("loggedIn"):
            items.append((False, "Not logged in", "login"))
        else:
            sub = st.get("subscriptionType")
            method = st.get("authMethod", "")
            items.append((True, f"Logged in ({method}{', ' + sub if sub else ''})", None))
    except Exception:
        items.append((True, "Login status unknown (older CLI)", None))
    if any(os.environ.get(k) for k in _NO_SUBSCRIPTION_ENV):
        items.append((True, "API key env var set: ignored by the chat (subscription only)", None))
    items.append((bool(_server), f"Bridge {'running' if _server else 'stopped'} on port {PORT}",
                  None if _server else "start"))
    return items


def run_async():
    if result["running"]:
        return

    def work():
        try:
            items = check()
            result["items"] = items
            result["state"] = "ok" if all(ok for ok, _, _ in items) else "problem"
        except Exception as e:
            result["items"] = [(False, f"check failed: {e}", None)]
            result["state"] = "problem"
        finally:
            result["checked"] = True
            result["running"] = False
    result["running"] = True
    threading.Thread(target=work, daemon=True).start()


def _open_terminal(cmd):
    """Run an interactive CLI command (login/update) in a visible terminal window."""
    if os.name == "nt":
        subprocess.Popen(["cmd", "/c", "start", "Claude", "cmd", "/k", cmd])
    elif sys.platform == "darwin":
        subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{cmd}"'])
    else:
        for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"], ["konsole", "-e"], ["xterm", "-e"]):
            try:
                subprocess.Popen(term + ["sh", "-c", f"{cmd}; exec $SHELL"])
                return
            except OSError:
                continue


def fix(kind):
    from .chat import find_claude
    claude = find_claude() or "claude"
    if kind == "install":
        webbrowser.open(INSTALL_URL)
    elif kind == "login":
        _open_terminal(f'"{claude}"' if os.name == "nt" else claude)
    elif kind == "update":
        _open_terminal(f'"{claude}" update' if os.name == "nt" else f"{claude} update")
    elif kind == "start":
        from . import start
        start()


def diagnostics():
    """A redacted report for bug reports: versions, platform, recent op names, the last CLI error."""
    from . import PORT, _server, bl_info
    home = os.path.expanduser("~")
    lines = [
        f"Claude Bridge {'.'.join(map(str, bl_info['version']))}",
        f"Blender {bpy.app.version_string}",
        f"OS {platform.platform()}",
        f"GPU backend {getattr(bpy.context.preferences.system, 'gpu_backend', '?')}",
        f"Bridge {'running' if _server else 'stopped'} on {PORT}",
        "Setup: " + "; ".join(f"{'ok' if ok else 'FAIL'} {label}" for ok, label, _ in result["items"]) if result["items"]
        else "Setup: not checked",
        "Recent ops: " + " | ".join(recent_ops),
        "Last CLI error: " + last_stderr[0][-400:],
    ]
    return "\n".join(lines).replace(home, "~")


class CLAUDE_OT_doctor_fix(bpy.types.Operator):
    bl_idname = "claude.doctor_fix"
    bl_label = "Fix"
    kind: bpy.props.StringProperty()

    def execute(self, context):
        fix(self.kind)
        run_async()
        return {"FINISHED"}


class CLAUDE_OT_doctor_check(bpy.types.Operator):
    bl_idname = "claude.doctor_check"
    bl_label = "Check setup"
    bl_description = "Check the claude CLI, login and bridge"

    def execute(self, context):
        run_async()
        return {"FINISHED"}


class CLAUDE_OT_copy_diagnostics(bpy.types.Operator):
    bl_idname = "claude.copy_diagnostics"
    bl_label = "Copy diagnostics"
    bl_description = "Copy a short report (versions, recent ops, last error; no personal data) and open a GitHub issue"

    def execute(self, context):
        context.window_manager.clipboard = diagnostics()
        webbrowser.open(ISSUES_URL)
        self.report({"INFO"}, "Diagnostics copied: paste them into the issue")
        return {"FINISHED"}


_FIX_LABEL = {"install": "Install Claude Code", "login": "Log in", "update": "Update CLI", "start": "Start bridge"}


def draw(layout):
    """Collapsed 'ready' line when all is well, otherwise the failing checks with fix buttons."""
    if result["running"] and not result["checked"]:
        layout.label(text="Checking setup…", icon="SORTTIME")
        return
    if not result["checked"]:
        return
    if result["state"] == "ok":
        return
    box = layout.box()
    for ok, label, kind in result["items"]:
        row = box.row(align=True)
        row.label(text=label, icon="CHECKMARK" if ok else "ERROR")
        if not ok and kind:
            row.operator("claude.doctor_fix", text=_FIX_LABEL.get(kind, "Fix")).kind = kind
    row = box.row(align=True)
    row.operator("claude.doctor_check", text="Check again", icon="FILE_REFRESH")
    row.operator("claude.copy_diagnostics", icon="URL")


classes = (CLAUDE_OT_doctor_fix, CLAUDE_OT_doctor_check, CLAUDE_OT_copy_diagnostics)
