"""Minimal stdio MCP server bridging Claude Code to the Claude Bridge Blender addon.

No third-party dependencies. Two tools only, to keep the per-turn schema cost tiny:
  bl   - run a batch of compact ops (one undo step)
  look - get a small JPEG of the viewport or camera
"""

import base64
import json
import os
import socket
import sys

HOST, PORT = "127.0.0.1", int(os.environ.get("CLAUDE_BRIDGE_PORT", "9877"))

BL_DESC = """Control the live Blender. ops = list of {"op":..., ...}; runs in order as ONE undo step, stops at first error.
Refs: obj = name | [names] | "sel" | "active". Angles deg. Colors "#rrggbb" or [r,g,b].
scene{filter,type} info{obj} get{path} prop{path,value}  (path e.g. "scene.render.fps", "Cube.data.energy", "objects.Cube.location")
add{type:cube|sphere|ico|cylinder|cone|plane|circle|torus|monkey|grid|empty|text|curve|camera|point|sun|spot|area, name,loc,rot,scale,dims,size,mat,smooth,body,energy,color,args{}}
set{obj,loc,rot,scale,dims,name,hide,parent,smooth,props{dotted.attr:val}}  move{obj,by,rot,scale}
del{obj} dup{obj,n,by,linked,name} select{obj|"all"|"none",active,add} join{obj}
mat{obj,name,color,metal,rough,alpha,transmission,ior,emit,emit_strength,coat,spec,slot}
mod{obj,type:bevel|subsurf|array|mirror|solidify|bool|decimate|weld|wireframe|screw|displace|remesh|nodes|...,name,props{},apply}
apply{obj,mod:name|"all",xform:loc|rot|scale|all}
aim{obj,at} cam{name,loc,at,lens,active}
view{preset:front|back|left|right|top|bottom|camera|persp|ortho, orbit[yaw,pitch], zoom, dist, at, frame:"sel"|"all"|name, shading:solid|material|rendered|wireframe}
bop{id:"mesh.subdivide",args{},obj,edit,ctx:"view3d"}  (any bpy.ops operator)
render{path,res[w,h],pct,samples,engine} import{path} export{path} save{path} open{path} new{keep} undo{n} redo{n}
py{code}  (last resort; set `out` to return)"""

# Mirrors ops.OPS (this process has no bpy); tests/test_integration.py checks they match.
OP_NAMES = ["scene", "info", "get", "prop", "add", "set", "move", "del", "dup", "select", "join", "mat", "mod",
            "apply", "aim", "cam", "view", "bop", "py", "undo", "redo", "save", "open", "new", "import", "export",
            "render"]

TOOLS = [
    {
        "name": "bl",
        "description": BL_DESC,
        "inputSchema": {
            "type": "object",
            "properties": {"ops": {"type": "array", "items": {
                "type": "object", "required": ["op"],
                "properties": {"op": {"type": "string", "enum": OP_NAMES}},
            }}},
            "required": ["ops"],
        },
    },
    {
        "name": "look",
        "description": "Small JPEG of the user's viewport (or camera view with cam:true). Optional view args "
                       "(preset, orbit, zoom, frame, shading, dist, at) are applied first. max = longest side px (default 512; use 256-384 when possible).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cam": {"type": "boolean"}, "max": {"type": "integer"}, "preset": {"type": "string"},
                "orbit": {"type": "array", "items": {"type": "number"}}, "zoom": {"type": "number"},
                "frame": {"type": "string"}, "shading": {"type": "string"},
            },
        },
    },
]


def blender(req: dict) -> dict:
    try:
        with socket.create_connection((HOST, PORT), timeout=5) as s:
            s.settimeout(600)
            s.sendall((json.dumps(req) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(1 << 20)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf)
    except OSError as e:
        return {"ok": False, "text": f"ERR Blender not reachable on {HOST}:{PORT} ({e}). Is the Claude Bridge addon enabled?"}


def call_tool(name: str, args: dict) -> dict:
    if name == "bl":
        r = blender({"ops": args.get("ops", [])})
        return {"content": [{"type": "text", "text": r.get("text", "")}], "isError": not r.get("ok", False)}
    if name == "look":
        r = blender({"look": args})
        content = []
        if r.get("image") and os.path.exists(r["image"]):
            with open(r["image"], "rb") as f:
                content.append({"type": "image", "data": base64.b64encode(f.read()).decode(), "mimeType": "image/jpeg"})
        content.append({"type": "text", "text": r.get("text", "")})
        return {"content": content, "isError": not r.get("ok", False)}
    return {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}


def send(msg: dict):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:
            continue  # notification
        if method == "initialize":
            result = {
                "protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "blender-bridge", "version": "0.1.0"},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = msg.get("params", {})
            try:
                result = call_tool(p.get("name"), p.get("arguments") or {})
            except Exception as e:
                result = {"content": [{"type": "text", "text": f"ERR {e}"}], "isError": True}
        elif method == "ping":
            result = {}
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}})
            continue
        send({"jsonrpc": "2.0", "id": mid, "result": result})


if __name__ == "__main__":
    main()
