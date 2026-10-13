"""Image-based PBR materials (img) and free CC0 assets from Poly Haven (asset).

Downloads happen on the bridge's connection thread before a batch is queued (prefetch), so Blender's UI
never waits on the network; the op itself then only loads cached files on the main thread.
Respects Blender's "Allow Online Access" preference.
"""

import hashlib
import json
import os
import re
import time
import urllib.request

import bpy

from .ops import _normalize, color, obj, op

API = "https://api.polyhaven.com"
UA = "claude-bridge-blender (+https://github.com/JackLuciano/claude-bridge-blender)"
KINDS = ("textures", "hdris", "models")
_cache_dir = [None]


def cache_dir():
    if _cache_dir[0] is None:
        d = os.environ.get("CLAUDE_BRIDGE_ASSETS") or bpy.utils.user_resource("CONFIG", path="claude_bridge/assets", create=True)
        os.makedirs(d, exist_ok=True)
        _cache_dir[0] = d
    return _cache_dir[0]


def _online():
    allowed = getattr(bpy.app, "online_access", True)
    if not allowed:
        raise ValueError("online access is off: Preferences ▸ System ▸ Network ▸ Allow Online Access")


def _fetch(url, timeout=60):
    if not url.startswith(("https://api.polyhaven.com/", "https://dl.polyhaven.org/")):
        raise ValueError("refusing to download from outside Poly Haven")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _json(url, name, max_age=86400):
    path = os.path.join(cache_dir(), name)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    _online()
    data = json.loads(_fetch(url))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def index(kind):
    if kind not in KINDS:
        raise ValueError(f"type must be one of {','.join(KINDS)}")
    return _json(f"{API}/assets?t={kind}", f"index_{kind}.json")


def kind_of(aid):
    for k in KINDS:
        if aid in index(k):
            return k
    raise ValueError(f"no Poly Haven asset '{aid}' (asset{{search}} finds ids)")


def search(q, kind, n=8):
    words = [w for w in re.split(r"\W+", q.lower()) if w]
    scored = []
    for aid, a in index(kind).items():
        hay = " ".join([aid, a.get("name", ""), " ".join(a.get("tags", [])), " ".join(a.get("categories", []))]).lower()
        s = sum(3 if w in a.get("name", "").lower() else 1 for w in words if w in hay)
        if s:
            scored.append((s, a.get("download_count", 0), aid, a))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    lines = []
    for _, _, aid, a in scored[:n]:
        dim = a.get("dimensions")
        size = f" {dim[0] / 1000:g}x{dim[1] / 1000:g}m" if dim else ""
        lines.append(f"{aid} ({a.get('name')}){size}")
    return lines


def _safe_join(base, rel):
    p = os.path.normpath(os.path.join(base, rel))
    if not p.startswith(os.path.normpath(base) + os.sep):
        raise ValueError("unsafe path in asset listing")
    return p


def _download(url, dst, md5=None):
    if os.path.exists(dst) and (not md5 or hashlib.md5(open(dst, "rb").read()).hexdigest() == md5):
        return dst
    _online()
    data = _fetch(url, timeout=300)
    if md5 and hashlib.md5(data).hexdigest() != md5:
        raise ValueError(f"download corrupted: {os.path.basename(dst)}")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst + ".part", "wb") as f:
        f.write(data)
    os.replace(dst + ".part", dst)
    return dst


_TEX_MAPS = {"Diffuse": "albedo", "Rough": "rough", "nor_gl": "normal", "Displacement": "disp", "AO": "ao", "Metal": "metal"}


def fetch(aid, res="1k", kind=None):
    """Download an asset at a resolution into the cache. Returns {kind, files: {role: path} | path}."""
    kind = kind or kind_of(aid)
    files = _json(f"{API}/files/{aid}", f"files_{aid}.json", max_age=7 * 86400)
    base = os.path.join(cache_dir(), kind, aid, res)

    def pick(entry, fmts):
        if res not in entry:
            avail = sorted(entry, key=lambda r: int(r.rstrip("k")) if r.rstrip("k").isdigit() else 99)
            raise ValueError(f"res must be one of {','.join(avail)}")
        for f in fmts:
            if f in entry[res]:
                return entry[res][f]
        raise ValueError(f"no {'/'.join(fmts)} file")
    if kind == "textures":
        out = {}
        for key, role in _TEX_MAPS.items():
            if key in files:
                e = pick(files[key], ("jpg", "png"))
                out[role] = _download(e["url"], _safe_join(base, os.path.basename(e["url"])), e.get("md5"))
        return {"kind": kind, "files": out}
    if kind == "hdris":
        e = pick(files["hdri"], ("hdr", "exr"))
        return {"kind": kind, "files": _download(e["url"], _safe_join(base, os.path.basename(e["url"])), e.get("md5"))}
    e = pick(files["gltf"], ("gltf",))
    main = _download(e["url"], _safe_join(base, os.path.basename(e["url"])), e.get("md5"))
    for rel, inc in (e.get("include") or {}).items():
        _download(inc["url"], _safe_join(base, rel), inc.get("md5"))
    return {"kind": kind, "files": main}


def prefetch(batch):
    """Connection thread: download what asset ops in this batch will need, so the main thread doesn't wait."""
    for a in batch if isinstance(batch, list) else []:
        a = _normalize(a)
        if a.get("op") != "asset":
            continue
        try:
            if a.get("search"):
                index(a.get("type", "textures"))
            elif a.get("id"):
                fetch(a["id"], a.get("res", "1k"), a.get("type"))
        except Exception:
            pass  # the op reports the error with context on the main thread


def _credit(aid, kind):
    """Keep a CC0 credit list in the file (not required by the license, but good manners)."""
    a = index(kind).get(aid, {})
    t = bpy.data.texts.get("Asset Credits") or bpy.data.texts.new("Asset Credits")
    line = f"{a.get('name', aid)} by {', '.join(a.get('authors', {}))} — CC0, polyhaven.com/a/{aid}"
    if line not in t.as_string():
        t.write(line + "\n")


@op("asset")
def op_asset(a):
    """Free CC0 assets from Poly Haven. a: search (words) + type (textures|hdris|models) -> ids;
    id + res (1k default, 2k, 4k) and: textures -> obj|mat (+ size_m: real tile size, default from the asset);
    hdris -> becomes the world (rot, strength); models -> imported, fitted on the floor (at, height)."""
    if a.get("search"):
        kind = a.get("type", "textures")
        hits = search(a["search"], kind, int(a.get("n", 8)))
        return f"{len(hits)} {kind}: " + " | ".join(hits) if hits else f"no {kind} match '{a['search']}'"
    aid = a.get("id")
    if not aid:
        raise ValueError("asset needs search or id")
    got = fetch(aid, a.get("res", "1k"), a.get("type"))
    kind = got["kind"]
    _credit(aid, kind)
    if kind == "textures":
        dim = index("textures").get(aid, {}).get("dimensions")
        size = a.get("size_m") or (dim[0] / 1000 if dim else 1.0)
        return op_img({**{k: a[k] for k in ("obj", "mat") if k in a}, "maps": got["files"], "size_m": size,
                       "name": a.get("name") or aid})
    if kind == "hdris":
        from .ops import op_world
        return op_world({"hdri": got["files"], "rot": a.get("rot", 0), "strength": a.get("strength", 1.0)})
    from .ops import op_import
    fit = {"floor": True}
    if "height" in a:
        fit["height"] = a["height"]
    if "at" in a:
        fit["at"] = a["at"]
    return op_import({"path": got["files"], "fit": fit, "col": a.get("col", aid)})


# ---------------------------------------------------------------- img: PBR texture sets

_ROLE_PATTERNS = {
    "albedo": ("albedo", "basecolor", "base_color", "diffuse", "_diff", "_col", "color"),
    "rough": ("rough",), "normal": ("normal", "_nor", "_nrm"), "metal": ("metal",),
    "disp": ("disp", "height", "bump"), "ao": ("_ao", "ambientocclusion", "occlusion"),
}


def detect_maps(folder):
    """{role: path} from a texture folder, by common file-name suffixes."""
    maps = {}
    for fn in sorted(os.listdir(folder)):
        if os.path.splitext(fn)[1].lower() not in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".exr", ".webp"):
            continue
        low = fn.lower()
        if "_dx" in low and "normal" not in maps:  # prefer OpenGL normals; DirectX only as a fallback
            maps.setdefault("normal_dx", os.path.join(folder, fn))
            continue
        for role, pats in _ROLE_PATTERNS.items():
            if role not in maps and any(p in low for p in pats):
                maps[role] = os.path.join(folder, fn)
                break
    if "normal" not in maps and "normal_dx" in maps:
        maps["normal"] = maps["normal_dx"]
    maps.pop("normal_dx", None)
    if "albedo" not in maps:
        raise ValueError(f"no color/albedo/diffuse image in {folder}")
    return maps


def _bsdf_of(m):
    from .ops import _bsdf
    return _bsdf(m)


@op("img")
def op_img(a):
    """Image texture set onto a material at real-world scale (no UVs needed: box projection).
    a: obj | mat, path (one color image) | dir (folder with albedo/rough/normal/metal/disp/ao maps) | maps {role: path},
    size_m (meters one tile covers, default 1), proj (box|uv, default box), name (material name)."""
    if a.get("maps"):
        maps = dict(a["maps"])
    elif a.get("dir"):
        maps = detect_maps(bpy.path.abspath(a["dir"]))
    elif a.get("path"):
        maps = {"albedo": bpy.path.abspath(a["path"])}
    else:
        raise ValueError("img needs path, dir or maps")
    for role, p in maps.items():
        if not os.path.exists(p):
            raise ValueError(f"missing {role} image: {p}")
    target = None
    if a.get("mat"):
        m = bpy.data.materials.get(a["mat"]) or bpy.data.materials.new(a["mat"])
    else:
        target = obj(a.get("obj"))
        m = target.active_material
        if m is None or (a.get("name") and m.name != a["name"]):
            m = bpy.data.materials.get(a.get("name") or f"{target.name}_mat") or bpy.data.materials.new(
                a.get("name") or f"{target.name}_mat")
            if target.data.materials:
                target.data.materials[0] = m
            else:
                target.data.materials.append(m)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = _bsdf_of(m)
    for n in [n for n in nt.nodes if n.get("claude_tex")]:
        nt.nodes.remove(n)

    def node(t, x, y):
        n = nt.nodes.new(t)
        n["claude_tex"] = True
        n.location = (bsdf.location.x - x, bsdf.location.y + y)
        return n
    size = float(a.get("size_m", 1.0))
    box = a.get("proj", "box") == "box"
    coord, mapping = node("ShaderNodeTexCoord", 1000, 0), node("ShaderNodeMapping", 820, 0)
    nt.links.new(coord.outputs["Object" if box else "UV"], mapping.inputs["Vector"])
    mapping.inputs["Scale"].default_value = (1 / size,) * 3
    if box and target is not None and m.users <= 1:
        mapping.inputs["Scale"].default_value = tuple(abs(s) / size or 1 / size for s in target.scale)
    y = 300

    def tex(role, noncolor):
        nonlocal y
        t = node("ShaderNodeTexImage", 560, y)
        y -= 280
        t.image = bpy.data.images.load(maps[role], check_existing=True)
        if noncolor:
            t.image.colorspace_settings.is_data = True
        if box:
            t.projection, t.projection_blend = "BOX", 0.2
        nt.links.new(mapping.outputs["Vector"], t.inputs["Vector"])
        return t
    alb = tex("albedo", False)
    if "ao" in maps:
        ao = tex("ao", True)
        mul = node("ShaderNodeMix", 280, 300)
        mul.data_type, mul.blend_type = "RGBA", "MULTIPLY"
        mul.inputs["Factor"].default_value = 0.8
        nt.links.new(alb.outputs["Color"], mul.inputs[6])
        nt.links.new(ao.outputs["Color"], mul.inputs[7])
        nt.links.new(mul.outputs[2], bsdf.inputs["Base Color"])
    else:
        nt.links.new(alb.outputs["Color"], bsdf.inputs["Base Color"])
    if "rough" in maps:
        nt.links.new(tex("rough", True).outputs["Color"], bsdf.inputs["Roughness"])
    if "metal" in maps:
        nt.links.new(tex("metal", True).outputs["Color"], bsdf.inputs["Metallic"])
    normal_out = None
    if "normal" in maps:
        nm = node("ShaderNodeNormalMap", 280, -300)
        nt.links.new(tex("normal", True).outputs["Color"], nm.inputs["Color"])
        normal_out = nm.outputs["Normal"]
    if "disp" in maps:
        bump = node("ShaderNodeBump", 140, -420)
        bump.inputs["Strength"].default_value = 0.3
        bump.inputs["Distance"].default_value = size * 0.01
        nt.links.new(tex("disp", True).outputs["Color"], bump.inputs["Height"])
        if normal_out is not None:
            nt.links.new(normal_out, bump.inputs["Normal"])
        normal_out = bump.outputs["Normal"]
    if normal_out is not None:
        nt.links.new(normal_out, bsdf.inputs["Normal"])
    m.diffuse_color = color("#888888")
    return f"img {m.name}: {','.join(sorted(maps))} tile {size:g}m {'box' if box else 'uv'}"
