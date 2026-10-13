"""Windows: real-keystroke test for Ctrl+V image paste into the chat.
    blender --python tests/live_paste.py
Puts an image on the clipboard, brings Blender to the foreground, injects a real
Ctrl+V (only if Blender really is the foreground window), and checks the image got
attached. Writes the result to <temp>/claude_paste_result.txt and quits.
"""

import ctypes
import os
import subprocess
import tempfile

import bpy

OUT = os.path.join(tempfile.gettempdir(), "claude_paste_result.txt")
u = ctypes.windll.user32
result = {"msg": "not run"}


def blender_hwnd():
    pid = os.getpid()
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        p = ctypes.c_ulong()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and u.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    u.EnumWindows(cb, 0)
    return found[0] if found else None


def foreground_is_blender():
    p = ctypes.c_ulong()
    u.GetWindowThreadProcessId(u.GetForegroundWindow(), ctypes.byref(p))
    return p.value == os.getpid()


def setup():
    from claude_bridge import chat
    chat._claude_tab_open = lambda: True  # the sidebar tab can't be switched from Python
    img = os.path.join(tempfile.gettempdir(), "claude_paste_src.png")
    i = bpy.data.images.new("src", 64, 64)
    i.filepath_raw, i.file_format = img, "PNG"
    i.save()
    subprocess.run(["powershell", "-NoProfile", "-STA", "-Command",
                    "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
                    f"[System.Windows.Forms.Clipboard]::SetImage([System.Drawing.Image]::FromFile('{img}'))"], check=True)
    chat.state["attachments"] = []
    hwnd = blender_hwnd()
    # Alt tap lets a background process take the foreground.
    u.keybd_event(0x12, 0, 0, 0)
    u.keybd_event(0x12, 0, 2, 0)
    u.SetForegroundWindow(hwnd)
    return None


def press():
    if not foreground_is_blender():
        result["msg"] = "SKIP: Blender is not the foreground window; refusing to inject keys"
        return None
    import threading
    import time

    def keys():
        # Off the main thread: the poller is a main-thread timer and must run while V is held.
        for vk, up in ((0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2)):  # Ctrl down, V down, V up, Ctrl up
            u.keybd_event(vk, 0, up, 0)
            time.sleep(0.12)

    threading.Thread(target=keys, daemon=True).start()
    return None


def check():
    from claude_bridge import chat
    if result["msg"] == "not run":
        n = len(chat.state["attachments"])
        result["msg"] = ("PASS" if n == 1 else "FAIL") + f" attachments={n}"
    with open(OUT, "w") as f:
        f.write(result["msg"])
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(setup, first_interval=2.0)
bpy.app.timers.register(press, first_interval=3.5)
bpy.app.timers.register(check, first_interval=5.0)
