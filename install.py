"""One-shot installer.

    python install.py [--blender PATH] [--copy] [--no-mcp]

1. Links (or copies with --copy) claude_bridge into Blender's user addons folder.
2. Enables the addon and saves preferences (runs Blender headless once).
3. Registers the MCP server with Claude Code (`claude mcp add -s user blender-bridge ...`).
"""

import argparse
import glob
import os
import platform
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "claude_bridge")


def find_blender():
    if shutil.which("blender"):
        return shutil.which("blender")
    pats = {
        "Windows": [r"C:\Program Files\Blender Foundation\Blender *\blender.exe"],
        "Darwin": ["/Applications/Blender*.app/Contents/MacOS/Blender"],
    }.get(platform.system(), ["/usr/bin/blender", "/snap/bin/blender", "/opt/blender*/blender"])
    hits = sorted(h for p in pats for h in glob.glob(p))
    return hits[-1] if hits else None


def blender_query(blender, expr):
    out = subprocess.run([blender, "-b", "--factory-startup", "--python-expr", f"print('@@' + str({expr}))"],
                         capture_output=True, text=True).stdout
    return next(line[2:] for line in out.splitlines() if line.startswith("@@"))


def link(dst):
    if os.path.lexists(dst):
        if os.path.islink(dst) or (platform.system() == "Windows" and not os.path.exists(os.path.join(dst, "__init__.py"))):
            os.remove(dst) if os.path.islink(dst) else os.rmdir(dst)
        else:
            shutil.rmtree(dst)
    if platform.system() == "Windows":
        # Junctions need no admin rights.
        subprocess.run(["cmd", "/c", "mklink", "/J", dst, SRC], check=True, capture_output=True)
    else:
        os.symlink(SRC, dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blender")
    ap.add_argument("--copy", action="store_true", help="copy instead of link")
    ap.add_argument("--no-mcp", action="store_true")
    a = ap.parse_args()

    blender = a.blender or find_blender()
    if not blender:
        sys.exit("Blender not found; pass --blender PATH")
    addons = blender_query(blender, "__import__('bpy').utils.user_resource('SCRIPTS', path='addons', create=True)")
    dst = os.path.join(addons, "claude_bridge")
    if a.copy:
        if os.path.lexists(dst):
            shutil.rmtree(dst) if not os.path.islink(dst) else os.remove(dst)
        shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns("__pycache__"))
    else:
        link(dst)
    print(f"addon -> {dst}")

    r = subprocess.run([blender, "-b", "--python-expr",
                        "import bpy; bpy.ops.preferences.addon_enable(module='claude_bridge'); "
                        "bpy.context.preferences.use_preferences_save = True; bpy.ops.wm.save_userpref(); print('@@enabled')"],
                       capture_output=True, text=True)
    if "@@enabled" not in r.stdout:
        sys.exit("failed to enable addon:\n" + r.stdout[-2000:] + r.stderr[-2000:])
    print("addon enabled")

    if not a.no_mcp:
        claude = shutil.which("claude")
        if not claude:
            print("claude CLI not found; register manually:\n"
                  f"  claude mcp add -s user blender-bridge -- {sys.executable} {os.path.join(ROOT, 'mcp_server.py')}")
        else:
            subprocess.run([claude, "mcp", "remove", "-s", "user", "blender-bridge"], capture_output=True)
            subprocess.run([claude, "mcp", "add", "-s", "user", "blender-bridge", "--", sys.executable,
                            os.path.join(ROOT, "mcp_server.py")], check=True)
            print("MCP server registered as 'blender-bridge'")
    print("done. Start Blender; the bridge autostarts on 127.0.0.1:9877 (View3D sidebar > Claude).")


if __name__ == "__main__":
    main()
