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
del{obj} dup{obj,n,by,linked,name,tree} select{obj|"all"|"none",active,add} join{obj}   (add/dup/cam take col:"Name")
col{name,obj,parent,hide,exclude,color} rename{obj,seq:"Leg_{n}",sort:x|y|z|name,prefix,suffix,find,replace}
curve{name,pts[[x,y,z]..],type:poly|smooth,cyclic,bevel,fill,lathe[[r,z]..],steps,mesh,mat}  (pipes, cables, vases, turned legs)
fit{obj,height|longest|dims,at,floor,apply,faces}  (normalize imports; import{path,fit:true|{height}})
mat{obj,name,color,metal,rough,alpha,transmission,ior,emit,emit_strength,coat,spec,slot}
tex{obj|mat,kind:wood|fabric|marble|noise|brick|checker|tiles|leather|concrete,color1,color2,scale,bump,rough[min,max]}  (procedural texture)
mod{obj,type:bevel|subsurf|array|mirror|solidify|bool|decimate|weld|wireframe|screw|displace|remesh|nodes|...,name,props{},inputs{GN input:val},apply}
nodes{mat|obj|world|gn|comp, add{id:type|alias}, set{"id.Socket":v,"id.prop":v,"ramp.0":"#hex"}, link["a.Out>b.In"], del[ids], show, preset:"scatter"+instance,density,scale}  (ids: bsdf,out,bg,in,rl)
apply{obj,mod:name|"all",xform:loc|rot|scale|all}
edit{obj,sel:all|keep|+z|-y..|{normal,tol}|{mat}|{above}|[ids],do:[{extrude:d|[x,y,z]},{inset:w|{w,depth}},{bevel:{w,seg}},{cut:{axis,n}},{bisect},{delete},{move},{scale},{subdivide},{mat},{smooth}]}  (steps chain on new faces)
place{obj,on:"floor"|name,at:[x,y]|"center",against,side:+x|-x|+y|-y,gap}  (exact contact by real bounds)
check{obj,only:[floating,overlap,frame,scale,images,light,cam]}  (measured problems + fix hints; run before the final look)
aim{obj,at} cam{name,loc,at,lens,active}
key{obj,f,loc,rot,scale,props{},interp:linear|bezier|constant|bounce|...,ease:in|out|in_out}
anim{start,end,fps,preset:turntable|spin|pop_in|bounce|float,obj,turns,stagger,height}
con{obj,type:track|damped_track|follow_path|copy_loc|copy_rot|child_of|limit_dist|floor|...,target,name,props{},animate}
view{preset:front|back|left|right|top|bottom|camera|persp|ortho, orbit[yaw,pitch], zoom, dist, at, frame:"sel"|"all"|name, shading:solid|material|rendered|wireframe}
bop{id:"mesh.subdivide",args{},obj,edit,ctx:"view3d"}  (any bpy.ops operator)
api{id|path|node|search}  (look up operator args, writable props, node sockets in this Blender; use before guessing)
world{color,strength | hdri:studio|interior|forest|city|courtyard|night|sunrise|sunset|path,rot | sky:true,sun_elev,sun_rot,sun | bg}
render{path,res[w,h],pct,samples,engine,preview,look,max}  (look:true returns the image; settings restored) import{path} export{path} save{path} open{path} new{keep} undo{n} redo{n}
py{code}  (last resort; set `out` to return)"""

# Mirrors ops.OPS (this process has no bpy); tests/test_integration.py checks they match.
OP_NAMES = ["scene", "info", "get", "prop", "add", "set", "move", "del", "dup", "select", "join", "mat", "tex", "mod",
            "apply", "aim", "cam", "view", "bop", "py", "undo", "redo", "save", "open", "new", "import", "export",
            "render", "world", "place", "check", "api", "key", "anim", "con", "fit", "col", "rename", "curve", "edit", "nodes"]

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
                       "(preset, orbit, zoom, frame, shading, dist, at) are applied first. max = longest side px (default 512; use 256-384 when possible). "
                       "views:[\"cam\",\"front\",\"right\",\"top\"] = one grid image of several angles for the price of one (leaves the user's view alone).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cam": {"type": "boolean"}, "max": {"type": "integer"}, "preset": {"type": "string"},
                "views": {"type": "array", "items": {"type": "string"}}, "keep": {"type": "boolean"},
                "orbit": {"type": "array", "items": {"type": "number"}}, "zoom": {"type": "number"},
                "frame": {"type": "string"}, "shading": {"type": "string"},
            },
        },
    },
]


def token():
    """The per-launch secret the addon writes when its bridge starts (re-read: Blender may restart)."""
    try:
        import tempfile
        with open(os.path.join(tempfile.gettempdir(), f"claude_bridge_{PORT}.token")) as f:
            return f.read().strip()
    except OSError:
        return ""


def blender(req: dict) -> dict:
    req = {**req, "auth": token()}
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


def _images(r):
    out = []
    for p in ([r["image"]] if r.get("image") else []) + list(r.get("images") or []):
        if os.path.exists(p):
            with open(p, "rb") as f:
                out.append({"type": "image", "data": base64.b64encode(f.read()).decode(), "mimeType": "image/jpeg"})
    return out


def call_tool(name: str, args: dict) -> dict:
    if name == "bl":
        r = blender({"ops": args.get("ops", [])})
        content = _images(r) + [{"type": "text", "text": r.get("text", "")}]
        return {"content": content, "isError": not r.get("ok", False)}
    if name == "look":
        r = blender({"look": args})
        content = _images(r) + [{"type": "text", "text": r.get("text", "")}]
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
                "serverInfo": {"name": "blender-bridge", "version": "0.1.2"},
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
